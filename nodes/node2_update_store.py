"""
nodes/node2_update_store.py
---------------------------
Node 2｜update_store_node

職責：
  1. 取得使用者輸入的履歷文字
     - terminal mode：input() 輸入 PDF 路徑或貼上文字
     - email mode：interrupt() 等待使用者傳入 PDF 路徑或履歷文字
  2. 呼叫 gpt-4o-mini 解析為結構化技能 JSON
  3. 將結果寫入 Store：store.put(("user_profile",), "skills", parsed)
     （覆蓋寫入，舊資料會被取代）

注意事項：
  - 此節點不寫入 OverallState，只操作 Store
  - store.put() 為覆蓋寫入，不累加
  - user_skill_vectors（pgvector）由 Node 8 負責，此節點不處理
"""

from __future__ import annotations

import json
import os

from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

INTERACTION_MODE = os.environ.get("INTERACTION_MODE", "terminal")

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

PARSE_RESUME_PROMPT = """\
你是一個履歷解析助手。請將以下履歷文字解析為 JSON 格式，只回傳 JSON，不要有任何前言或說明。

JSON 格式如下：
{{
  "summary": "一句話描述（職稱 + 年資）",
  "skills": ["技能1", "技能2", ...],
  "years_of_experience": <整數>,
  "languages": ["語言1", ...],
  "education": "最高學歷"
}}

履歷文字：
{resume_text}
"""


def make_update_store_node(store=None):
    def update_store_node(state: dict, config: RunnableConfig) -> dict:
        _store = (
            config.get("configurable", {}).get("store")
            or store
        )

        if _store is None:
            raise ValueError(
                "Store 未提供。"
                "測試環境請透過 make_update_store_node(store=...) 傳入；"
                "上線環境請在 config['configurable']['store'] 帶入。"
            )

        if INTERACTION_MODE == "terminal":
            pdf_path = input(
                "\n[update_store] 請輸入履歷 PDF 路徑（直接 Enter 改為貼文字）："
            ).strip()

            if pdf_path:
                try:
                    import fitz
                    with fitz.open(pdf_path) as pdf:
                        resume_text = "\n".join(page.get_text() for page in pdf)
                    print(f"[update_store] PDF 解析完成，{len(resume_text)} 字元。")
                except FileNotFoundError:
                    print(f"[update_store] 找不到檔案：{pdf_path}，略過更新。")
                    return {}
                except Exception as e:
                    print(f"[update_store] PDF 解析失敗：{e}，略過更新。")
                    return {}
            else:
                print("[update_store] 請貼上履歷文字（輸入完成後按 Enter 兩次）：")
                lines = []
                while True:
                    line = input()
                    if line == "":
                        if lines:
                            break
                    else:
                        lines.append(line)
                resume_text = "\n".join(lines)

        else:
            # email mode：用 interrupt() 等待使用者傳入履歷內容
            resume_input = interrupt(
                "[update_store] 請輸入履歷 PDF 路徑，或直接貼上履歷文字："
            )
            resume_input = resume_input.strip()

            if resume_input.endswith(".pdf"):
                try:
                    import fitz
                    with fitz.open(resume_input) as pdf:
                        resume_text = "\n".join(page.get_text() for page in pdf)
                    print(f"[update_store] PDF 解析完成，{len(resume_text)} 字元。")
                except FileNotFoundError:
                    print(f"[update_store] 找不到檔案：{resume_input}，略過更新。")
                    return {}
                except Exception as e:
                    print(f"[update_store] PDF 解析失敗：{e}，略過更新。")
                    return {}
            else:
                resume_text = resume_input

        if not resume_text.strip():
            print("[update_store] 未收到履歷文字，略過更新。")
            return {}

        prompt = PARSE_RESUME_PROMPT.format(resume_text=resume_text)
        response = llm.invoke(prompt)
        raw = response.content.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[update_store] LLM 回傳格式錯誤，略過更新。錯誤：{e}")
            print(f"[update_store] 原始回傳：{raw}")
            return {}

        _store.put(("user_profile",), "skills", parsed)
        print(f"[update_store] 技能記憶已更新：{parsed.get('summary', '')}")
        print(f"[update_store] 抓取到的技能點（共 {len(parsed.get('skills', []))} 項）：")
        for skill in parsed.get('skills', []):
            print(f"  - {skill}")
        print(f"[update_store] 年資：{parsed.get('years_of_experience', '未知')} 年")
        print(f"[update_store] 語言：{parsed.get('languages', [])}")
        print(f"[update_store] 學歷：{parsed.get('education', '未知')}")

        return {}

    return update_store_node