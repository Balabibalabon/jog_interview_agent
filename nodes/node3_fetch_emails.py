"""
nodes/node3_fetch_emails.py
---------------------------
Node 3｜fetch_emails_node

職責：
  1. 呼叫 Gmail API 撈取職缺相關信件
  2. 將原始信件清單寫入 raw_emails
  3. 為每封信產生 Send("classify_job_node", JobBranchState{...})

LangGraph Send() 回傳方式：
  節點回傳 list 時，LangGraph 會將 dict 項目作為 state update，
  Send() 項目作為 fan-out 任務。
  正確寫法：return [{"raw_emails": emails}, Send(...), Send(...), ...]

模式切換：
  環境變數 USE_MOCK_EMAILS=true  → 使用內建 mock 資料（測試用）
  環境變數 USE_MOCK_EMAILS=false → 使用真實 Gmail API（預設）

Gmail 查詢條件：
  - 寄件人含 104.com.tw、linkedin.com、yourator.co、cakeresume.com
  - 或主旨含「詢問意願」、「面試邀請」、「職缺邀請」
  - 最多抓 20 封
"""

from __future__ import annotations

import os

from langgraph.types import Send
from state import OverallState
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command


# ---------------------------------------------------------------------------
# Mock Gmail 資料（測試用）
# ---------------------------------------------------------------------------

MOCK_EMAILS = [
    {
        "message_id": "msg_001",
        "gmail_id":   "msg_001",
        "subject":    "【104人力銀行】新職缺通知：台積電 - 資深後端工程師",
        "from_":      "notification@104.com.tw",
        "date":       "2025-06-01T09:00:00+08:00",
        "body_text":  "您好，根據您的求職條件，為您推薦台積電資深後端工程師職缺。",
        "email_content": (
            "MESSAGE_ID: msg_001\n"
            "SUBJECT: 【104人力銀行】新職缺通知：台積電 - 資深後端工程師\n"
            "DATE: 2025-06-01T09:00:00+08:00\n\n"
            "您好，根據您的求職條件，為您推薦台積電資深後端工程師職缺。"
        ),
    },
    {
        "message_id": "msg_002",
        "gmail_id":   "msg_002",
        "subject":    "【LinkedIn】Job Alert: Frontend Engineer at Shopee Taiwan",
        "from_":      "jobs-noreply@linkedin.com",
        "date":       "2025-06-01T10:30:00+08:00",
        "body_text":  "A new job matching your preferences: Frontend Engineer at Shopee Taiwan.",
        "email_content": (
            "MESSAGE_ID: msg_002\n"
            "SUBJECT: 【LinkedIn】Job Alert: Frontend Engineer at Shopee Taiwan\n"
            "DATE: 2025-06-01T10:30:00+08:00\n\n"
            "A new job matching your preferences: Frontend Engineer at Shopee Taiwan."
        ),
    },
    {
        "message_id": "msg_003",
        "gmail_id":   "msg_003",
        "subject":    "【電子報】2025 年 6 月軟體開發趨勢報告",
        "from_":      "newsletter@techweekly.com.tw",
        "date":       "2025-06-01T08:00:00+08:00",
        "body_text":  "本週科技週報：AI Coding 工具市場分析、Rust 崛起...",
        "email_content": (
            "MESSAGE_ID: msg_003\n"
            "SUBJECT: 【電子報】2025 年 6 月軟體開發趨勢報告\n"
            "DATE: 2025-06-01T08:00:00+08:00\n\n"
            "本週科技週報：AI Coding 工具市場分析、Rust 崛起..."
        ),
    },
    {
        "message_id": "msg_004",
        "gmail_id":   "msg_004",
        "subject":    "詢問意願 - Garena 後端工程師職缺",
        "from_":      "recruiter@garena.com",
        "date":       "2025-06-01T11:00:00+08:00",
        "body_text":  "您好，我是 Garena 的招募專員，想詢問您對後端工程師職缺的意願，薪資範圍 80-120k，歡迎與我們進一步討論。",
        "email_content": (
            "MESSAGE_ID: msg_004\n"
            "SUBJECT: 詢問意願 - Garena 後端工程師職缺\n"
            "DATE: 2025-06-01T11:00:00+08:00\n\n"
            "您好，我是 Garena 的招募專員，想詢問您對後端工程師職缺的意願，"
            "薪資範圍 80-120k，歡迎與我們進一步討論。"
        ),
    },
]

# Gmail 搜尋條件
# GMAIL_QUERY = (
#     "from:104.com.tw OR from:linkedin.com OR from:yourator.co OR from:cakeresume.com "
#     "OR subject:詢問意願 OR subject:面試邀請 OR subject:職缺邀請 "
#     "-subject:[JobAgent]"
# )
GMAIL_QUERY = (
    "("
    "from:104.com.tw OR from:linkedin.com OR from:yourator.co OR from:cakeresume.com "
    "OR subject:詢問意願 OR subject:面試邀請 OR subject:職缺邀請"
    ") "
    "-subject:[JobAgent]"
)
GMAIL_MAX_RESULTS = 20
GMAIL_DAYS_BACK = int(os.getenv("GMAIL_DAYS_BACK", "1"))


# ---------------------------------------------------------------------------
# 節點函式
# ---------------------------------------------------------------------------

def fetch_emails_node(state: OverallState, config: RunnableConfig = None) -> list:
    """
    Node 3｜讀取 Gmail 信件，產生 Send() 清單。

    回傳 list：
      - 第一個元素為 dict，寫入 raw_emails 到 OverallState
      - 後續每個 Send() 對應一個 classify_job_node fan-out 任務
    """
    use_mock = os.getenv("USE_MOCK_EMAILS", "false").lower() == "true"

    if use_mock:
        emails = MOCK_EMAILS
        print(f"\n[Node 3] mock 模式：{len(emails)} 封信件")
    else:
        emails = _fetch_real_emails()
        print(f"\n[Node 3] Gmail API：取得 {len(emails)} 封信件")

    # raw_emails 統一使用 email_content 欄位存入 state
    raw = [e["email_content"] for e in emails]
    sends = []
    for email in emails:
        branch_init = {
            "job_id":              "",
            "company_name":        "",
            "job_title":           "",
            "email_content":       email["email_content"],
            "job_details":         {},
            "gap_analysis":        {},
            "interview_questions": [],
            "markdown_report":     None,
        }
        sends.append(Send("classify_job_node", branch_init))
        print(f"[Node 3] Send() → classify_job_node | {email['subject'][:50]}...")

    return Command(
        update={"raw_emails": raw},
        goto=sends,
    )


# ---------------------------------------------------------------------------
# 內部：真實 Gmail API
# ---------------------------------------------------------------------------

def _fetch_real_emails() -> list[dict]:
    """
    透過 gmail_client 抓取信件。
    失敗時印出錯誤並回傳空清單，不中斷主流程。
    """
    try:
        from gmail_client import get_gmail_service, fetch_recent_emails
        service = get_gmail_service()
        emails  = fetch_recent_emails(
            service,
            max_results=GMAIL_MAX_RESULTS,
            query=GMAIL_QUERY,
            days_back=GMAIL_DAYS_BACK,
        )
        return emails
    except FileNotFoundError as e:
        print(f"[Node 3] Gmail 授權檔案不存在：{e}")
        print("[Node 3] 請先執行 python gmail_auth.py 完成授權。")
        return []
    except Exception as e:
        print(f"[Node 3] Gmail API 錯誤：{e}")
        return []