"""
state.py
--------
定義 Job Agent 的兩個 State TypedDict 與三個 Reducer 函式。

OverallState  : 主流程全局狀態（Node 1–6 共用）
JobBranchState: 第二次 Fan-out 子狀態（Node 7–9 每個職缺獨立一份）

Reducer 函式：
  _append_list  → OverallState.interview_invites
  _merge_dict   → JobBranchState.job_details / gap_analysis
  _keep_latest  → JobBranchState.interview_questions / research_sufficient /
                  insufficient_sections / pending_job_ids

Phase 5 更新：
  job_details key 改名：
    company_overview  → interview_process
    recent_news       → interview_prep
    tech_and_products → salary_info
  新增欄位：
    research_retry_count   : Node 7 重試計數（0–2）
    research_sufficient    : Think Node 判斷資料是否足夠
    insufficient_sections  : 不足的面向名稱清單（供 Node 9 / mail 說明）

清理二更新（串行處理）：
  新增欄位：
    pending_job_ids : 尚未處理的職缺 ID 清單，每次取第一個串行處理
"""

from __future__ import annotations

from typing import Annotated, Any, Optional
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Reducer 函式
# ---------------------------------------------------------------------------

def _append_list(existing: list | None, new: list | None) -> list:
    """
    將 new 累加到 existing 後面。
    - 任一方為 None 或空 list 時安全處理，不報錯。
    - 用於 OverallState.interview_invites。
    """
    base     = existing if existing is not None else []
    addition = new      if new      is not None else []
    return base + addition


def _merge_dict(existing: dict | None, new: dict | None) -> dict:
    """
    將 new 合併進 existing，重複 key 以 new 的值為準（後寫覆蓋）。
    - 任一方為 None 或空 dict 時安全處理。
    - 用於 JobBranchState.job_details / gap_analysis：
      Node 7 與 Node 8 並行寫入不同 key，合併後互不覆蓋。
    """
    base     = existing if existing is not None else {}
    addition = new      if new      is not None else {}
    return {**base, **addition}


def _keep_latest(existing: Any, new: Any) -> Any:
    """
    直接取最新值；若 new 為 None 則保留 existing，不覆蓋。
    - 用於 JobBranchState.interview_questions / research_sufficient /
      insufficient_sections / pending_job_ids。
    """
    if new is None:
        return existing
    return new


# ---------------------------------------------------------------------------
# OverallState — 主流程全局狀態
# ---------------------------------------------------------------------------

class OverallState(TypedDict):
    """
    主流程（Node 1–6）共享的狀態。
    thread_id 固定為 "job_agent_main"，整個主流程只有一個 checkpoint 點。
    """

    # Node 3 fetch_emails_node 寫入，原始郵件清單
    raw_emails: list[dict]

    # Node 4 classify_job_node 寫入，累積所有相關職缺
    # _append_list：每個 Fan-out branch 各自 append，不互相覆蓋
    interview_invites: Annotated[list[dict], _append_list]

    # Node 5 send_summary_email_node 寫入，ISO8601 字串
    summary_notified_at: Optional[str]

    # 外部監聽器（Email listener）寫入用戶回信原文
    user_reply_text: Optional[str]

    # Node 6 parse_user_reply_node 解析後寫入，核准的職缺 ID 清單
    approved_job_ids: Optional[list[str]]

    # 串行處理佇列：尚未處理的職缺 ID 清單
    # fan_out_to_research 初始化為 approved_job_ids[1:]，每輪處理完後
    # 由 Node 7 return 帶回（pop 第一個後的剩餘清單）
    # _keep_latest：每輪更新整個 list，不累加
    pending_job_ids: Annotated[Optional[list[str]], _keep_latest]

    # --- 第二次 Fan-out branch 欄位（Node 7–9 使用）---
    # Send() input 不會自動流入下游節點，必須由 research_company_node 在 return 裡帶回
    job_id:                Optional[str]
    company_name:          Optional[str]
    job_title:             Optional[str]
    email_content:         Optional[str]
    research_retry_count:  Annotated[int, _keep_latest]
    research_sufficient:   Annotated[Optional[bool], _keep_latest]
    insufficient_sections: Annotated[Optional[list[str]], _keep_latest]
    job_details:           Annotated[dict, _merge_dict]
    gap_analysis:          Annotated[dict, _merge_dict]
    interview_questions:   Annotated[Optional[list[dict]], _keep_latest]
    markdown_report:       Optional[str]


# ---------------------------------------------------------------------------
# JobBranchState — 第二次 Fan-out 子狀態（每個核准職缺獨立一份）
# ---------------------------------------------------------------------------

class JobBranchState(TypedDict):
    """
    第二次 Fan-out（Node 7–9）每個職缺的獨立子狀態。
    由 Node 6 透過 Send() 初始化，攜帶識別欄位。

    Node 9 產出的報告透過 MCP write_file 直接寫磁碟，不回寫 OverallState。

    job_details 三個 key（Phase 5 更新）：
      interview_process : 面試流程（Dcard / PTT / 各來源，含出處 url）
      interview_prep    : 面試準備方向（考什麼、如何準備、面試官問題）
      salary_info       : 薪資資訊（含具體數字或範圍，含出處 url）
    """

    # --- 識別欄位（由 Send() 帶入，Node 7–9 唯讀） ---
    job_id:        str
    company_name:  str
    job_title:     str
    email_content: str

    # --- 串行處理佇列（由 Node 7 return 帶回 OverallState）---
    # 每輪 Send() 時帶入剩餘清單，Node 7 return 時寫回 OverallState
    pending_job_ids: Annotated[Optional[list[str]], _keep_latest]

    # --- Node 7 / Think Node 調研流程控制 ---
    research_retry_count: Annotated[int, _keep_latest]
    research_sufficient:   Annotated[Optional[bool], _keep_latest]   # Think Node 寫入
    insufficient_sections: Annotated[Optional[list[str]], _keep_latest]  # 不足面向名稱

    # --- Node 7 research_company_node 寫入 ---
    # _merge_dict：Node 7 與 Node 8 並行，各寫各的 key，合併時不互相覆蓋
    # key：interview_process / interview_prep / salary_info
    job_details: Annotated[dict, _merge_dict]

    # --- Node 8 personal_gap_analysis_node 寫入 ---
    gap_analysis: Annotated[dict, _merge_dict]

    # --- Node 8 personal_gap_analysis_node 寫入 ---
    interview_questions: Annotated[list[dict], _keep_latest]

    # --- Node 9 generate_report_node 產出 ---
    markdown_report: Optional[str]