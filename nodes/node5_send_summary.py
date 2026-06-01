"""
node5_send_summary.py
---------------------
Node 5a｜send_summary_email_node

職責：
  寄出彙整信、記錄寄信時間、在 email mode 下啟動背景監聽器。
  不呼叫 interrupt()，直接 return，靜態 edge 接到 wait_for_reply_node。

設計說明：
  將原本的 Node 5（寄信 + interrupt）拆成兩個節點：
    5a. send_summary_email_node：負責 side effect（寄信）
    5b. wait_for_reply_node：負責 interrupt()
  原因：LangGraph 1.2.2 interrupt() 後節點從頭重新執行，
  若寄信與 interrupt() 同在一個節點，resume 時會重複寄信。
  拆開後，wait_for_reply_node re-execute 時只有 interrupt()，無 side effect。

工廠函式模式：make_send_summary_email_node()
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from email_listener import start_listener_thread


def make_send_summary_email_node():
    def send_summary_email_node(state, config):
        invites = state.get("interview_invites") or []

        # ── 無邀請 → 直接結束 ──────────────────────────────────────────
        if not invites:
            print("[Node 5] 無面試邀請，流程結束。")
            return {}

        # ── 依 job_id 去重（保留第一次出現的順序）─────────────────────
        seen = set()
        unique_invites = []
        for job in invites:
            jid = job.get("job_id")
            if jid not in seen:
                seen.add(jid)
                unique_invites.append(job)

        if len(unique_invites) < len(invites):
            print(f"[Node 5] 去重：{len(invites)} → {len(unique_invites)} 筆。")

        # ── 組合彙整信 ─────────────────────────────────────────────────
        lines = ["您好，以下是本次掃描到的面試邀請，請回信告知您想跟進的編號：\n"]
        for i, job in enumerate(unique_invites, 1):
            company = job.get("company_name", "（未知公司）")
            title   = job.get("job_title",    "（未知職稱）")
            job_id  = job.get("job_id",       "")
            lines.append(f"{i}. [{job_id}] {company} — {title}")

        lines.append("\n回信範例：「我想跟進 1、3」或「全部都要」或「都不用」")
        lines.append("（若 8 小時內未回覆，系統將自動略過所有邀請）")
        body = "\n".join(lines)

        # ── 寄信 ───────────────────────────────────────────────────────
        _send_email(subject="[JobAgent] 面試邀請彙整，請確認", body=body)
        print("[Node 5] 彙整信已寄出。")

        # ── 記錄寄出時間 ───────────────────────────────────────────────
        notified_at = datetime.now(timezone.utc).isoformat()

        # ── 只在 email mode 啟動監聽器 ─────────────────────────────────
        # terminal mode 由使用者手動透過 Command(resume=...) 提供回覆
        if os.environ.get("INTERACTION_MODE", "terminal") == "email":
            _graph      = config.get("configurable", {}).get("graph")
            _done_event = config.get("configurable", {}).get("done_event")
            start_listener_thread(
                graph=_graph,
                config=config,
                notified_at=notified_at,
                done_event=_done_event,
            )
            print("[Node 5] 監聽器已在背景啟動，等待使用者回信。")
        else:
            print("[Node 5] terminal mode，跳過監聽器。")

        return {"summary_notified_at": notified_at}

    return send_summary_email_node


# ---------------------------------------------------------------------------
# 內部：寄信
# ---------------------------------------------------------------------------

def _send_email(subject: str, body: str) -> None:
    """
    透過 Gmail API 寄信給自己（寄件人 = 收件人 = 授權帳號）。
    """
    import base64
    from email.mime.text import MIMEText
    from gmail_client import get_gmail_service

    service = get_gmail_service()

    profile    = service.users().getProfile(userId="me").execute()
    user_email = profile["emailAddress"]

    msg = MIMEText(body, "plain", "utf-8")
    msg["to"]      = user_email
    msg["from"]    = user_email
    msg["subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()