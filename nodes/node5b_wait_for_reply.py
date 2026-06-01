"""
node5b_wait_for_reply.py
------------------------
Node 5b｜wait_for_reply_node

職責：
  呼叫 interrupt() 等待使用者回覆，把 reply 寫入 user_reply_text。
  無其他 side effect，resume 重新執行時安全。

設計說明：
  LangGraph 1.2.2 interrupt() 後節點從頭重新執行（re-executing all logic）。
  本節點只有 interrupt()，re-execute 時不會觸發任何 side effect，
  因此可以安全地與 send_summary_email_node 拆開。

  若 interview_invites 為空（send_summary_email_node 已判斷並 return {}），
  本節點直接 return {}，不觸發 interrupt。
"""

from __future__ import annotations

from langgraph.types import interrupt


def wait_for_reply_node(state, config):
    """
    Node 5b｜等待使用者回覆。

    interview_invites 非空時才 interrupt；
    為空時直接 return {}，讓 parse_user_reply_node 處理空 invites 邏輯。
    """
    invites = state.get("interview_invites") or []
    if not invites:
        return {}

    # interrupt()：第一次執行時暫停，resume 後重頭執行並直接回傳 reply
    reply = interrupt("等待使用者回覆面試邀請彙整信")
    print(f"[Node 5b] resume 完成，reply={reply}")

    return {"user_reply_text": reply}