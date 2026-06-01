"""
email_listener.py
-----------------
背景監聽器，由 Node 5 在 interrupt() 前啟動。

功能：
- 每 60 秒輪詢一次信箱，尋找主旨含 [JobAgent] 的回信
- 找到後透過 Command(resume=reply_text) 喚醒 Graph
- 超過 8 小時未回覆，以空字串喚醒（全部視為不跟進）
- resume 完成後透過 done_event 通知主 process 可以退出

設計說明：
  不再使用 graph.update_state() 寫入 user_reply_text。
  LangGraph 1.2.2 的 interrupt() 機制：
    - update_state() 會建立新 checkpoint，破壞 pending interrupt 狀態
    - 後續 Command(resume=...) 找不到待 resume 的 task，graph 直接結束
  正確做法：直接 Command(resume=reply_text)，
    Node 5 的 interrupt() 回傳值即為 reply_text，
    Node 5 再把它寫入 state["user_reply_text"]，Node 6 正常讀取。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone, timedelta

from langgraph.types import Command

from gmail_client import get_gmail_service, fetch_recent_emails

# 超時門檻（小時）
TIMEOUT_HOURS = 8

# 輪詢間隔（秒）
POLL_INTERVAL_SECONDS = 60


def start_listener_thread(
    graph, config: dict, notified_at: str, done_event: threading.Event = None
) -> None:
    """
    在背景 daemon thread 啟動監聽器。
    由 Node 5 呼叫，傳入 graph config 與寄信時間。
    done_event：resume 完成後 set()，通知主 process 可以退出。
    """
    t = threading.Thread(
        target=_listen_loop,
        args=(graph, config, notified_at, done_event),
        daemon=True,
    )
    t.start()


def _listen_loop(
    graph, config: dict, notified_at: str, done_event: threading.Event = None
) -> None:
    deadline = datetime.fromisoformat(notified_at) + timedelta(hours=TIMEOUT_HOURS)
    service  = get_gmail_service()

    print(f"[Listener] 開始監聽，截止時間：{deadline.isoformat()}")

    while True:
        now = datetime.now(timezone.utc)

        if now >= deadline:
            print("[Listener] 已超時 8 小時，以空回覆喚醒 Graph。")
            _resume_graph(graph, config, user_reply_text="", done_event=done_event)
            return

        try:
            emails = fetch_recent_emails(
                service,
                max_results=5,
                query="subject:[JobAgent] in:inbox",
            )
        except Exception as e:
            print(f"[Listener] 抓信失敗：{e}，60 秒後重試。")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        for email in emails:
            email_time  = _parse_email_date(email.get("date", ""))
            in_reply_to = email.get("in_reply_to", "")

            # 只接受有 in_reply_to 的信（代表是回信，不是原始寄出的信）
            if not in_reply_to:
                continue

            if email_time and email_time > datetime.fromisoformat(notified_at):
                reply_text = email.get("body_text", "")
                print(f"[Listener] 收到回信：{email.get('subject')}")
                _resume_graph(graph, config, user_reply_text=reply_text, done_event=done_event)
                return

        print(f"[Listener] 尚未收到回信，{POLL_INTERVAL_SECONDS} 秒後再查。")
        time.sleep(POLL_INTERVAL_SECONDS)


def _resume_graph(
    graph, config: dict, user_reply_text: str, done_event: threading.Event = None
) -> None:
    # 只傳 thread_id，移除 graph/done_event 等非標準欄位
    clean_config = {
        "configurable": {
            "thread_id": config["configurable"]["thread_id"],
        }
    }
    graph.invoke(
        Command(resume=user_reply_text),
        config=clean_config,
    )
    print("[Listener] Graph 已喚醒。")
    if done_event is not None:
        done_event.set()


def _parse_email_date(date_str: str):
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None
