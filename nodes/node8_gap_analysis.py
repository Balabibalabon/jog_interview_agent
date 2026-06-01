"""
node8_gap_analysis.py
---------------------
Node 8｜personal_gap_analysis_node

流程：
1. 從 Store 取出使用者技能（user_profile / skills）
2. 從 email_content 萃取 JD 要求的技能關鍵字
3. 用 text-embedding-3-small 將使用者技能逐筆 embed，存入 pgvector
4. 將 JD 關鍵字 embed 後，對 pgvector 做 cosine similarity 搜尋（k=10）
5. 從 state 讀取 job_details（Node 7 已寫入的面試流程 / 準備方向 / 薪資）
6. gpt-4o-mini 根據搜尋結果 + job_details 做 Gap 比對，產出：
   - gap_analysis：強項 / 待補強 / 具體建議行動（根據面試搜尋資料）
   - interview_questions：10 道預測面試題（含答題提示）
7. 寫入 gap_analysis 與 interview_questions

Prompt 設計原則：
  - 有搜尋資料時：根據 interview_prep 裡具體提到的技術點給出深度建議
  - 無搜尋資料時：誠實標注「無搜尋資料支撐，以下為通用建議」，不亂扯

Store 傳遞策略：config 優先，closure fallback（與其他節點一致）。
懶初始化：OpenAI client 與 pgvector 連線皆在函式內建立。
gpt-4o-mini：支援 temperature 與 max_tokens。
"""

from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import json
import os
import re
import uuid
from typing import Any


# ---------------------------------------------------------------------------
# 懶初始化
# ---------------------------------------------------------------------------

def _get_openai_client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _get_pg_conn():
    import psycopg2
    return psycopg2.connect(os.environ["POSTGRES_URI"])


# ---------------------------------------------------------------------------
# Embedding 工具
# ---------------------------------------------------------------------------

def _embed(client, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    return [item.embedding for item in response.data]


# ---------------------------------------------------------------------------
# pgvector 操作
# ---------------------------------------------------------------------------

VECTOR_DIM = 1536


def _ensure_table(conn, table_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id      TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                embedding vector({VECTOR_DIM}) NOT NULL
            );
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS {table_name}_embedding_idx
            ON {table_name} USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 10);
        """)
    conn.commit()


def _upsert_vectors(conn, table_name: str, items: list[dict]) -> None:
    if not items:
        return
    with conn.cursor() as cur:
        for item in items:
            vec_str = "[" + ",".join(str(v) for v in item["embedding"]) + "]"
            cur.execute(f"""
                INSERT INTO {table_name} (id, content, embedding)
                VALUES (%s, %s, %s::vector)
                ON CONFLICT (id) DO UPDATE
                    SET content   = EXCLUDED.content,
                        embedding = EXCLUDED.embedding;
            """, (item["id"], item["content"], vec_str))
    conn.commit()


def _similarity_search(
    conn, table_name: str, query_embedding: list[float], k: int = 10
) -> list[str]:
    vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT content
            FROM {table_name}
            ORDER BY embedding <=> %s::vector
            LIMIT %s;
        """, (vec_str, k))
        rows = cur.fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# JD 技能關鍵字萃取
# ---------------------------------------------------------------------------

_SKILL_PATTERNS = [
    r'\bPython\b', r'\bJava\b', r'\bGo\b', r'\bRust\b', r'\bC\+\+\b',
    r'\bTypeScript\b', r'\bJavaScript\b', r'\bSQL\b', r'\bNoSQL\b',
    r'\bDocker\b', r'\bKubernetes\b', r'\bAWS\b', r'\bGCP\b', r'\bAzure\b',
    r'\bLangGraph\b', r'\bLangChain\b', r'\bRAG\b', r'\bLLM\b',
    r'\bReact\b', r'\bNode\.js\b', r'\bFastAPI\b', r'\bDjango\b',
    r'\bPostgreSQL\b', r'\bRedis\b', r'\bKafka\b', r'\bSpark\b',
    r'\bMLflow\b', r'\bPyTorch\b', r'\bTensorFlow\b',
    r'機器學習', r'深度學習', r'自然語言處理', r'資料工程', r'後端開發',
    r'前端開發', r'全端', r'微服務', r'系統設計', r'敏捷開發',
]


def _extract_jd_keywords(email_content: str) -> list[str]:
    found = []
    for pattern in _SKILL_PATTERNS:
        if re.search(pattern, email_content, re.IGNORECASE):
            keyword = re.sub(r'\\b|\\.|\\+', lambda m: {'\\b': '', '\\.': '.', '\\+': '+'}[m.group()], pattern)
            found.append(keyword)
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# job_details 摘要工具
# ---------------------------------------------------------------------------

def _summarize_job_details(job_details: dict) -> str:
    """
    將 job_details 的三個面向整理成 prompt 可用的文字。
    每個面向最多取前 3 筆，content 截至 300 字元。
    若整個面向為空，標注「無資料」。
    """
    sections = {
        "interview_process": "【面試流程】",
        "interview_prep":    "【面試準備方向 / 考題重點】",
        "salary_info":       "【薪資資訊】",
    }
    lines = []
    for key, label in sections.items():
        results = job_details.get(key, [])
        if not results:
            lines.append(f"{label}\n  （無搜尋資料）")
            continue
        lines.append(label)
        for r in results[:3]:
            title   = r.get("title", "")
            content = r.get("content", "")[:300]
            url     = r.get("url", "")
            lines.append(f"  - {title}（{url}）\n    {content}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Gap 比對 Prompt
# ---------------------------------------------------------------------------

_GAP_PROMPT = """\
你是一個職涯顧問，正在協助求職者準備「{company_name}」的「{job_title}」面試。

以下是從網路搜尋到的面試相關資訊：
{job_details_summary}

求職者已具備的相關技能（依與職缺相似度排序，最多 10 筆）：
{matched_skills}

職缺 JD 要求的技能關鍵字：
{jd_keywords}

---

請依照以下規則產出分析，只回傳 JSON，不要任何前言：

規則：
1. strengths：求職者技能中與職缺高度吻合的項目，需對應上方搜尋資料中提到的重點
2. gaps：職缺要求但求職者較弱或未提及的項目
3. action_items：**根據上方搜尋資料中具體提到的技術點**給出建議。
   - 若搜尋資料有提到「面試官會問 FastAPI routing」，就要寫「準備 FastAPI routing 的基本操作：路由定義、dependency injection、response model」
   - 若搜尋資料顯示「考系統設計，重點是 DB schema 設計」，就要寫「練習 DB schema 設計：正規化、index 策略、foreign key 設計」
   - 若某項目完全沒有搜尋資料支撐，請標注「（無搜尋資料，通用建議）」，不要捏造具體內容
   - 最多 5 條，每條需指出具體要準備的子項目
4. interview_questions：根據搜尋資料中出現的題目或方向預測 10 道面試題
   - 技術題需具體（例：「請說明 FastAPI 的 dependency injection 如何實作」而非「請介紹 FastAPI」）
   - 行為題需結合職位特性
   - hint 需給出答題的具體切入點或關鍵詞

回傳格式：
{{
  "strengths": ["..."],
  "gaps": ["..."],
  "action_items": [
    {{
      "topic": "準備主題（例：FastAPI 基本操作）",
      "subtopics": ["具體子項目1", "具體子項目2", "具體子項目3"],
      "source": "根據搜尋資料（來源網址）" 或 "（無搜尋資料，通用建議）"
    }}
  ],
  "interview_questions": [
    {{
      "question": "面試題目",
      "hint": "答題切入點或關鍵詞"
    }}
  ]
}}
"""


def _run_gap_analysis(
    client,
    matched_skills: list[str],
    jd_keywords: list[str],
    company_name: str,
    job_title: str,
    job_details: dict,
) -> dict[str, Any]:
    """呼叫 gpt-4o-mini 進行 Gap 分析，回傳解析後的 dict。"""

    job_details_summary = _summarize_job_details(job_details)

    prompt = _GAP_PROMPT.format(
        company_name        = company_name,
        job_title           = job_title,
        job_details_summary = job_details_summary,
        matched_skills      = "\n".join(f"- {s}" for s in matched_skills) or "（無資料）",
        jd_keywords         = "\n".join(f"- {k}" for k in jd_keywords) or "（無資料）",
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()
        print(f"[Node 8] LLM 原始回應（前 200 字）：{raw[:200]}")
    except Exception as e:
        print(f"[Node 8] LLM 呼叫失敗：{e}")
        return {}

    # ── JSON 解析 ──────────────────────────────────────────────────────
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        print("[Node 8] 無法解析 LLM 回應，回傳空 dict。")
        return {}


# ---------------------------------------------------------------------------
# 主節點（工廠模式，支援 closure store）
# ---------------------------------------------------------------------------

def make_gap_analysis_node(store=None):

    def personal_gap_analysis_node(state: dict, config: RunnableConfig) -> dict:
        _store = (
            config.get("configurable", {}).get("store")
            or store
        )

        job_id        = state.get("job_id", "unknown")
        company_name  = state.get("company_name", "")
        job_title     = state.get("job_title", "")
        email_content = state.get("email_content", "")
        job_details   = state.get("job_details", {})   # Node 7 已寫入

        print(f"[Node 8] 開始 Gap 分析：job_id={job_id}")
        print(f"[Node 8] job_details 面向：{list(job_details.keys())}")

        # ── 從 Store 取出使用者技能 ─────────────────────────────────────
        user_skills: list[str] = []
        if _store is not None:
            try:
                item = _store.get(("user_profile",), "skills")
                if item is not None:
                    value = item.value if hasattr(item, "value") else item
                    if isinstance(value, dict):
                        user_skills = value.get("skills", [])
                    print(f"[Node 8] 從 Store 取得 {len(user_skills)} 項技能")
            except Exception as e:
                print(f"[Node 8] Store 讀取失敗：{e}")
        else:
            print("[Node 8] Store 未提供，略過個人技能比對。")

        # ── 萃取 JD 關鍵字 ─────────────────────────────────────────────
        jd_keywords = _extract_jd_keywords(email_content)
        print(f"[Node 8] JD 關鍵字：{jd_keywords}")

        # ── pgvector：embed 使用者技能並搜尋相關項目 ────────────────────
        matched_skills: list[str] = []

        if user_skills:
            try:
                openai_client = _get_openai_client()
                pg_conn       = _get_pg_conn()

                table_name = "user_skill_vectors"
                _ensure_table(pg_conn, table_name)

                skill_texts = [str(s) for s in user_skills]
                embeddings  = _embed(openai_client, skill_texts)
                items = [
                    {
                        "id":        f"skill_{uuid.uuid5(uuid.NAMESPACE_DNS, s)}",
                        "content":   s,
                        "embedding": emb,
                    }
                    for s, emb in zip(skill_texts, embeddings)
                ]
                _upsert_vectors(pg_conn, table_name, items)

                query_text    = " ".join(jd_keywords) if jd_keywords else f"{job_title} {company_name}"
                query_emb     = _embed(openai_client, [query_text])[0]
                matched_skills = _similarity_search(pg_conn, table_name, query_emb, k=10)
                print(f"[Node 8] 向量搜尋結果（k=10）：{matched_skills}")

                pg_conn.close()

            except Exception as e:
                print(f"[Node 8] pgvector 操作失敗：{e}，改用全量技能清單。")
                matched_skills = user_skills[:10]
        else:
            matched_skills = []

        # ── gpt-4o-mini Gap 比對 ────────────────────────────────────────
        openai_client = _get_openai_client()
        result = _run_gap_analysis(
            openai_client,
            matched_skills = matched_skills,
            jd_keywords    = jd_keywords,
            company_name   = company_name,
            job_title      = job_title,
            job_details    = job_details,
        )

        if not result:
            return {
                "gap_analysis":        {},
                "interview_questions": [],
            }

        gap_analysis = {
            "strengths":    result.get("strengths", []),
            "gaps":         result.get("gaps", []),
            "action_items": result.get("action_items", []),
        }
        interview_questions = result.get("interview_questions", [])

        print(
            f"[Node 8] 完成：強項={len(gap_analysis['strengths'])} 項，"
            f"落差={len(gap_analysis['gaps'])} 項，"
            f"面試題={len(interview_questions)} 道"
        )

        return {
            "gap_analysis":        gap_analysis,
            "interview_questions": interview_questions,
        }

    return personal_gap_analysis_node