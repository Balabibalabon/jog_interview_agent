"""
graph.py
--------
Job Agent 的 LangGraph 主流程骨架。

Phase 進度：
  Phase 1 ✅ → State + Reducer + stub 節點
  Phase 2 ✅ → Node 1–3 實作
  Phase 3 ✅ → Node 4（classify_job_node）
  Phase 4 ✅ → Node 5–6 + 外部監聽器 + PostgresSaver
  Phase 5 ✅ → Node 7–8（面試調研 + pgvector Gap 分析）+ Think Node 重試機制
  Phase 6 ✅ → Node 9–10 報告生成 + 彙整 mail
  清理二  ✅ → 改為串行處理，避免多 branch 並行時 OverallState 欄位互蓋
  清理一  ✅ → Node 5 拆成 5a（寄信）+ 5b（interrupt），解決 resume 重複寄信問題
  清理三  ✅ → 彙整信去重
  清理四  ✅ → terminal mode 不啟動監聽器，避免第二次 resume

Node 5a → Node 5b → Node 6 路由說明：
  5a (send_summary_email_node)：寄信、記錄時間，直接 return（無 interrupt）
  5b (wait_for_reply_node)：只做 interrupt()，re-execute 時無 side effect
  re-execute 時 5b 不再重複寄信，只需等待 resume value

第二次串行處理架構：
  parse_user_reply_node
      ↓ fan_out_to_research（每次只送第一個 pending job）
      research_company_node (Node 7) → think_node (Node 7b) → personal_gap_analysis_node (Node 8)
          ↓ route_after_report
          ├─ pending_job_ids 非空 → parse_user_reply_node（處理下一個）
          └─ pending_job_ids 為空 → send_final_email_node
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langgraph.types import Send
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

from state import OverallState, JobBranchState
from nodes.node1_check_store import make_check_store_node
from nodes.node2_update_store import make_update_store_node
from nodes.node3_fetch_emails import fetch_emails_node
from nodes.node4_classify_job import classify_job_node
from nodes.node5_send_summary import make_send_summary_email_node
from nodes.node5b_wait_for_reply import wait_for_reply_node
from nodes.node6_parse_reply import parse_user_reply_node
from nodes.node7_research_company import research_company_node
from nodes.node7b_think_node import think_node
from nodes.node8_gap_analysis import make_gap_analysis_node
from nodes.node9_generate_report import generate_report_node
from nodes.node10_send_final_email import send_final_email_node

load_dotenv()

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

THREAD_ID = "job_agent_main"

DEFAULT_CONFIG = {
    "configurable": {
        "thread_id": THREAD_ID,
    }
}


# ---------------------------------------------------------------------------
# Conditional Edge 路由函式
# ---------------------------------------------------------------------------

def fan_out_to_research(state: OverallState):
    """
    串行處理：每次只取 pending_job_ids 的第一個送出 Send()。
    """
    approved_ids = state.get("approved_job_ids") or []
    if not approved_ids:
        print("[graph] approved_job_ids 為空，流程結束。")
        return END

    pending = state.get("pending_job_ids")
    if pending is None:
        pending = approved_ids.copy()

    if not pending:
        print("[graph] pending_job_ids 已清空，流程結束。")
        return END

    job_id    = pending[0]
    remaining = pending[1:]

    invites    = state.get("interview_invites") or []
    invite_map = {job.get("job_id"): job for job in invites}
    invite     = invite_map.get(job_id)

    if invite is None:
        print(f"[graph] 找不到 job_id={job_id} 的 invite 資料，略過。")
        return END

    branch_state: JobBranchState = {
        "job_id":                job_id,
        "company_name":          invite.get("company_name", ""),
        "job_title":             invite.get("job_title", ""),
        "email_content":         invite.get("email_content", ""),
        "pending_job_ids":       remaining,
        "research_retry_count":  0,
        "research_sufficient":   None,
        "insufficient_sections": [],
        "job_details":           {},
        "gap_analysis":          {},
        "interview_questions":   [],
        "markdown_report":       None,
    }

    print(f"[graph] 開始處理 job_id={job_id}，剩餘 {len(remaining)} 個待處理。")
    return [Send("research_company_node", branch_state)]


def route_after_think(state: OverallState):
    if not state.get("research_sufficient", True):
        print(f"[graph] Think Node 判斷不足，回 Node 7 重試（retry={state.get('research_retry_count')}）")
        return "research_company_node"
    print("[graph] Think Node 判斷足夠，進入 Node 8。")
    return "personal_gap_analysis_node"


def route_after_report(state: OverallState):
    pending = state.get("pending_job_ids") or []
    if pending:
        print(f"[graph] 還有 {len(pending)} 個職缺待處理，繼續串行。")
        return "parse_user_reply_node"
    print("[graph] 所有職缺處理完畢，進入 Node 10 寄出彙整信。")
    return "send_final_email_node"


# ---------------------------------------------------------------------------
# Checkpointer 工廠
# ---------------------------------------------------------------------------

def _make_checkpointer(use_postgres: bool = False):
    if use_postgres:
        from langgraph.checkpoint.postgres import PostgresSaver
        import psycopg
        postgres_uri = os.environ["POSTGRES_URI"]
        conn         = psycopg.connect(postgres_uri, autocommit=True)
        checkpointer = PostgresSaver(conn)
        checkpointer.setup()
        return checkpointer
    return MemorySaver()

# ---------------------------------------------------------------------------
# Store 工廠
# ---------------------------------------------------------------------------
def _make_store(store=None, use_postgres: bool = False):
    if store is not None:
        return store
    if use_postgres:
        from langgraph.store.postgres import PostgresStore
        import psycopg
        conn = psycopg.connect(os.environ["POSTGRES_URI"], autocommit=True)
        pg_store = PostgresStore(conn)
        pg_store.setup()          # 建立 store 用的 table（首次執行需要）
        return pg_store
    return InMemoryStore()


# ---------------------------------------------------------------------------
# Graph 組裝
# ---------------------------------------------------------------------------

def build_graph(store=None, use_postgres: bool = False):
    """
    組裝並編譯 Job Agent 主流程 Graph。

    Args:
        store       : 測試環境傳入 InMemoryStore()（closure 模式）。
        use_postgres: True → PostgresSaver；False → MemorySaver。
    """

    # _store       = store or InMemoryStore()
    _store       = _make_store(store=store, use_postgres=use_postgres)
    checkpointer = _make_checkpointer(use_postgres=use_postgres)

    # ── 節點實例化 ──────────────────────────────────────────────────────
    check_store        = make_check_store_node(store=_store)
    update_store       = make_update_store_node(store=_store)
    send_summary_email = make_send_summary_email_node()
    gap_analysis       = make_gap_analysis_node(store=_store)

    # ── Graph 建構 ──────────────────────────────────────────────────────
    builder = StateGraph(OverallState)

    builder.add_node("check_store_node",           check_store)
    builder.add_node("update_store_node",          update_store)
    builder.add_node("fetch_emails_node",          fetch_emails_node)
    builder.add_node("classify_job_node",          classify_job_node)

    # Node 5a：寄信（無 interrupt）
    builder.add_node("send_summary_email_node",    send_summary_email)
    # Node 5b：等待回覆（只有 interrupt，re-execute 安全）
    builder.add_node("wait_for_reply_node",        wait_for_reply_node)

    builder.add_node("parse_user_reply_node",      parse_user_reply_node)
    builder.add_node("research_company_node",      research_company_node)
    builder.add_node("think_node",                 think_node)
    builder.add_node("personal_gap_analysis_node", gap_analysis)
    builder.add_node("generate_report_node",       generate_report_node)
    builder.add_node("send_final_email_node",      send_final_email_node)

    # ── Entry Point ─────────────────────────────────────────────────────
    builder.set_entry_point("check_store_node")

    # ── Edges ───────────────────────────────────────────────────────────
    builder.add_edge("update_store_node",       "fetch_emails_node")
    builder.add_edge("classify_job_node",       "send_summary_email_node")

    # 5a → 5b（靜態）：寄完信直接進等待節點
    builder.add_edge("send_summary_email_node", "wait_for_reply_node")
    # 5b → 6（靜態）：拿到 reply 後進解析節點
    builder.add_edge("wait_for_reply_node",     "parse_user_reply_node")

    builder.add_conditional_edges(
        "parse_user_reply_node",
        fan_out_to_research,
        ["research_company_node", END],
    )

    builder.add_edge("research_company_node", "think_node")

    builder.add_conditional_edges(
        "think_node",
        route_after_think,
        {
            "research_company_node":      "research_company_node",
            "personal_gap_analysis_node": "personal_gap_analysis_node",
        },
    )

    builder.add_edge("personal_gap_analysis_node", "generate_report_node")

    builder.add_conditional_edges(
        "generate_report_node",
        route_after_report,
        {
            "parse_user_reply_node": "parse_user_reply_node",
            "send_final_email_node": "send_final_email_node",
        },
    )

    builder.add_edge("send_final_email_node", END)

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    graph = build_graph(use_postgres=True)
    print("Graph compiled successfully.")
    print(f"Thread ID: {THREAD_ID}")