"""
node7_research_company.py
-------------------------
Node 7｜research_company_node

流程：
1. 從 JobBranchState 取得 company_name、job_title、research_retry_count
2. 依 retry_count 選擇對應的關鍵字策略（共三輪，精確度高→低）
3. 用 Tavily 搜尋三個面向：面試流程、面試準備方向、薪資資訊
4. 將結果結構化後寫入 job_details（透過 _merge_dict reducer）
5. 回傳結果供 think_node 判斷是否足夠

三輪關鍵字策略（精確度高→低）：
  第 1 輪：公司名 + 職位 + 平台限定（Dcard / PTT）
  第 2 輪：公司名 + 職位，不限平台
  第 3 輪：公司名 + 職位 + 英文關鍵字（Glassdoor / Blind）

懶初始化：_get_tavily_client() 在函式內建立。
並行安全：只寫 job_details，不碰 gap_analysis / interview_questions。
"""
from __future__ import annotations
from langchain_core.runnables import RunnableConfig

import os
from typing import Any

# ---------------------------------------------------------------------------
# 三輪備援關鍵字策略
# ---------------------------------------------------------------------------

def _build_queries(company: str, title: str, retry: int) -> dict[str, str]:
    """
    依 retry_count 回傳三個面向的搜尋關鍵字。

    retry=0：第 1 輪，平台限定（Dcard / PTT）
    retry=1：第 2 輪，不限平台
    retry=2：第 3 輪，英文關鍵字（Glassdoor / Blind）
    """
    if retry == 0:
        return {
            "interview_process": f"{company} {title} 面試流程 Dcard OR PTT",
            "interview_prep":    f"{company} {title} 面試準備 考什麼 Dcard OR PTT",
            "salary_info":       f"{company} {title} 薪資 薪水 Dcard OR PTT",
        }
    elif retry == 1:
        return {
            "interview_process": f"{company} {title} 面試流程 面試經驗",
            "interview_prep":    f"{company} {title} 面試準備 面試題目 面試官",
            "salary_info":       f"{company} {title} 薪資 薪水 待遇",
        }
    else:
        return {
            "interview_process": f"{company} {title} interview process experience",
            "interview_prep":    f"{company} {title} interview questions preparation",
            "salary_info":       f"{company} {title} salary compensation Glassdoor",
        }


# ---------------------------------------------------------------------------
# 懶初始化
# ---------------------------------------------------------------------------

def _get_tavily_client():
    from tavily import TavilyClient
    return TavilyClient(api_key=os.environ["TAVILY_API_KEY"])


# ---------------------------------------------------------------------------
# 搜尋輔助函式
# ---------------------------------------------------------------------------

def _search(client, query: str, max_results: int = 5) -> list[dict]:
    """
    執行單次 Tavily 搜尋，回傳結果清單（含出處 url）。
    失敗時回傳空 list，不中斷主流程。
    """
    try:
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=max_results,
        )
        results = response.get("results", [])
        return [
            {
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "content": r.get("content", "")[:500],  # 截斷避免 token 爆炸
            }
            for r in results
        ]
    except Exception as e:
        print(f"[Node 7] Tavily 搜尋失敗（query={query!r}）：{e}")
        return []


# ---------------------------------------------------------------------------
# 主節點
# ---------------------------------------------------------------------------

def research_company_node(state: dict, config: RunnableConfig) -> dict:
    """
    Node 7｜面試資訊調研節點。

    輸入（JobBranchState）：
        company_name, job_title, research_retry_count

    輸出（寫入 job_details）：
        {
          "interview_process": [...],   # 面試流程（含出處）
          "interview_prep":    [...],   # 面試準備方向（被問什麼、考什麼）
          "salary_info":       [...],   # 薪資資訊（含出處）
        }
    """
    company = state.get("company_name", "")
    title   = state.get("job_title", "")
    job_id  = state.get("job_id", "unknown")
    retry   = state.get("research_retry_count", 0)

    print(f"[Node 7] 開始調研：job_id={job_id}，公司={company}，職位={title}，第 {retry + 1} 輪")

    if not company or not title:
        print("[Node 7] company_name 或 job_title 為空，略過調研。")
        return {
            "job_id":        job_id,
            "company_name":  company,
            "job_title":     title,
            "email_content": state.get("email_content", ""),
            "job_details":   {},
            "pending_job_ids":  state.get("pending_job_ids")
        }

    client  = _get_tavily_client()
    queries = _build_queries(company, title, retry)

    # ── 三個面向搜尋 ────────────────────────────────────────────────────
    process_results = _search(client, queries["interview_process"])
    prep_results    = _search(client, queries["interview_prep"])
    salary_results  = _search(client, queries["salary_info"])

    job_details: dict[str, Any] = {
        "interview_process": process_results,
        "interview_prep":    prep_results,
        "salary_info":       salary_results,
    }

    print(
        f"[Node 7] 調研完成（第 {retry + 1} 輪）："
        f"面試流程={len(process_results)} 筆，"
        f"面試準備={len(prep_results)} 筆，"
        f"薪資={len(salary_results)} 筆"
    )

    return {
            "job_id":        job_id,
            "company_name":  company,
            "job_title":     title,
            "email_content": state.get("email_content", ""),
            "job_details":   job_details,
            "pending_job_ids":  state.get("pending_job_ids")
        }