"""
node7b_think_node.py
--------------------
Node 7b｜think_node

流程：
1. 讀取 job_details（Node 7 剛寫入的三個面向）
2. GPT-5 nano 判斷每個面向的資料是否「足夠有用」
3. 決策：
   - 全部足夠 → research_sufficient=True → 進入 Node 8
   - 有不足 + retry_count < 2 → research_sufficient=False → 回 Node 7 重試
   - 有不足 + retry_count >= 2（已是第 3 輪）→ research_sufficient=False，
     標記為「無資料」，進入 Node 8（Node 9 該 section 會說明無資料）

判斷標準（交給 GPT-5 nano）：
  - 結果是否與該公司 + 職位直接相關（不是泛泛介紹）
  - 結果是否有實質內容（非空、非廣告、非無關頁面）
  - 薪資需有具體數字或範圍，面試流程需有具體步驟或輪數

gpt-4o-mini：支援 temperature 與 max_tokens。
"""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import json
import os
import re
from typing import Any

MAX_RETRY = 2  # 最多執行 3 次（retry_count: 0, 1, 2）


# ---------------------------------------------------------------------------
# 懶初始化
# ---------------------------------------------------------------------------

def _get_openai_client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


# ---------------------------------------------------------------------------
# 判斷 Prompt
# ---------------------------------------------------------------------------

_THINK_PROMPT = """\
你是一個資料品質審查員。以下是針對「{company}」的「{title}」職位，
從搜尋引擎取得的三個面向資料。

請判斷每個面向的資料是否「足夠有用」，標準如下：
- 與該公司和職位直接相關（不是泛泛介紹或無關頁面）
- 有實質內容（非空、非廣告）
- 面試流程：需有具體步驟或輪數描述
- 面試準備：需有具體考題方向或準備建議
- 薪資資訊：需有具體數字、範圍或相對水準描述

【面試流程資料】（{process_count} 筆）：
{process_summary}

【面試準備資料】（{prep_count} 筆）：
{prep_summary}

【薪資資訊資料】（{salary_count} 筆）：
{salary_summary}

請只回傳以下 JSON，不要任何前言：
{{
  "interview_process_ok": true 或 false,
  "interview_prep_ok": true 或 false,
  "salary_info_ok": true 或 false,
  "reason": "簡短說明哪個面向不足（若全部足夠則填 ok）"
}}
"""


def _summarize_results(results: list[dict], max_chars: int = 300) -> str:
    """將搜尋結果摘要成簡短文字，避免 token 過多。"""
    if not results:
        return "（無結果）"
    lines = []
    for r in results[:3]:  # 最多看前 3 筆
        title   = r.get("title", "")
        content = r.get("content", "")[:max_chars]
        lines.append(f"- {title}：{content}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主節點
# ---------------------------------------------------------------------------

def think_node(state: dict, config: RunnableConfig) -> dict:
    """
    Node 7b｜資料品質判斷節點。

    輸入（JobBranchState）：
        job_details, company_name, job_title, research_retry_count

    輸出：
        research_sufficient: bool
        research_retry_count: int（若重試則 +1）
        insufficient_sections: list[str]（不足的面向名稱，供 Node 9 說明）
    """
    company = state.get("company_name", "")
    title   = state.get("job_title", "")
    job_id  = state.get("job_id", "unknown")
    retry   = state.get("research_retry_count", 0)
    details = state.get("job_details", {})

    process_results = details.get("interview_process", [])
    prep_results    = details.get("interview_prep", [])
    salary_results  = details.get("salary_info", [])

    print(f"[Think Node] 判斷資料品質：job_id={job_id}，第 {retry + 1} 輪結果")

    # ── 呼叫 GPT-5 nano 判斷 ────────────────────────────────────────────
    prompt = _THINK_PROMPT.format(
        company         = company,
        title           = title,
        process_count   = len(process_results),
        prep_count      = len(prep_results),
        salary_count    = len(salary_results),
        process_summary = _summarize_results(process_results),
        prep_summary    = _summarize_results(prep_results),
        salary_summary  = _summarize_results(salary_results),
    )

    judgment = {}
    try:
        client   = _get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()
        print(f"[Think Node] LLM 判斷原始回應：{raw[:200]}")

        # 清除 markdown 包裝
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            raw = raw.strip()

        judgment = json.loads(raw)

    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                judgment = json.loads(match.group())
            except Exception:
                pass
        if not judgment:
            print("[Think Node] 無法解析判斷結果，視為資料足夠，繼續流程。")
            judgment = {
                "interview_process_ok": True,
                "interview_prep_ok":    True,
                "salary_info_ok":       True,
                "reason":               "parse failed, defaulting to ok",
            }
    except Exception as e:
        print(f"[Think Node] LLM 呼叫失敗：{e}，視為資料足夠，繼續流程。")
        judgment = {
            "interview_process_ok": True,
            "interview_prep_ok":    True,
            "salary_info_ok":       True,
            "reason":               "llm failed, defaulting to ok",
        }

    # ── 分析哪些面向不足 ────────────────────────────────────────────────
    insufficient: list[str] = []
    if not judgment.get("interview_process_ok", True):
        insufficient.append("interview_process")
    if not judgment.get("interview_prep_ok", True):
        insufficient.append("interview_prep")
    if not judgment.get("salary_info_ok", True):
        insufficient.append("salary_info")

    reason = judgment.get("reason", "")
    print(f"[Think Node] 不足面向：{insufficient}，原因：{reason}")

    # ── 決策 ────────────────────────────────────────────────────────────
    all_sufficient = len(insufficient) == 0

    # 改後
    if all_sufficient:
        print("[Think Node] 資料充足，進入 Node 8。")
        return {
            "research_sufficient":   True,
            "research_retry_count":  retry,
            "insufficient_sections": [],
        }

    next_retry = retry + 1
    if next_retry <= MAX_RETRY:
        print(f"[Think Node] 資料不足，進行第 {next_retry + 1} 輪搜尋（retry={next_retry}）。")
        return {
            "research_sufficient":   False,
            "research_retry_count":  next_retry,
            "insufficient_sections": insufficient,
        }
    else:
        print(f"[Think Node] 已達最大重試次數（retry={retry}），強制進入 Node 8。")
        return {
            "research_sufficient":   True,
            "research_retry_count":  retry,
            "insufficient_sections": insufficient,
        }