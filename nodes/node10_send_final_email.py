"""
nodes/node10_send_final_email.py
--------------------------------
Node 10｜send_final_email_node

職責：
  所有職缺的 Node 9 都完成後，彙整所有報告，寄出一封通知 mail。

  mail 內容：
    - 有報告的職缺：列出公司 + 職稱，附上對應 .md 檔
    - 資料不足的職缺：說明哪個面向查無資料
    - 所有附件同一封 mail 寄出

  輸入：OverallState
    - interview_invites：所有職缺的基本資料
    - approved_job_ids：核准的職缺 ID 清單

  設計：
    - 從磁碟讀取已產生的 .md 檔（依 job_id + company + title 組出路徑）
    - 找不到檔案時在 mail 中說明，不中斷流程
    - 不寫入 OverallState
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from gmail_client import send_email_with_attachments


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _sanitize_filename(s: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", s).strip()


def _section_label(section_key: str) -> str:
    return {
        "interview_process": "面試流程",
        "interview_prep":    "面試準備方向",
        "salary_info":       "薪資資訊",
    }.get(section_key, section_key)


def _find_report_file(job_id: str, company_name: str, job_title: str) -> Path | None:
    """依命名規則找到對應的 .md 檔，找不到回傳 None。"""
    output_dir   = Path(os.environ.get("REPORT_OUTPUT_DIR", "./reports"))
    safe_company = _sanitize_filename(company_name)
    safe_title   = _sanitize_filename(job_title)
    filepath     = output_dir / f"{job_id}_{safe_company}_{safe_title}.md"
    return filepath if filepath.exists() else None


# ---------------------------------------------------------------------------
# 主節點
# ---------------------------------------------------------------------------

def send_final_email_node(state: dict) -> dict:
    """
    Node 10｜彙整通知 mail。

    讀取 OverallState 的 interview_invites 與 approved_job_ids，
    找出每個核准職缺的報告檔，組合一封彙整 mail 寄出。
    """
    approved_ids = state.get("approved_job_ids") or []
    invites      = state.get("interview_invites") or []

    if not approved_ids:
        print("[Node 10] 無核准職缺，略過彙整 mail。")
        return {}

    invite_map = {job.get("job_id"): job for job in invites}

    # ── 整理每個職缺的報告狀態 ───────────────────────────────────────────
    report_items  = []   # 有報告的職缺
    missing_items = []   # 找不到報告檔的職缺

    for job_id in approved_ids:
        invite = invite_map.get(job_id, {})
        company_name = invite.get("company_name", "未知公司")
        job_title    = invite.get("job_title",    "未知職稱")

        filepath = _find_report_file(job_id, company_name, job_title)

        if filepath:
            content = filepath.read_text(encoding="utf-8")
            report_items.append({
                "job_id":       job_id,
                "company_name": company_name,
                "job_title":    job_title,
                "filepath":     filepath,
                "filename":     filepath.name,
                "content":      content,
            })
            print(f"[Node 10] 找到報告：{filepath.name}")
        else:
            missing_items.append({
                "job_id":       job_id,
                "company_name": company_name,
                "job_title":    job_title,
            })
            print(f"[Node 10] 找不到報告：{job_id}_{company_name}_{job_title}")

    # ── 組合 mail 內文 ───────────────────────────────────────────────────
    body_lines = [
        "您好，Job Agent 已完成本次面試邀請的調研與分析。\n",
        f"共處理 {len(approved_ids)} 個核准職缺，以下為結果摘要：\n",
    ]

    if report_items:
        body_lines.append("【已產生報告】")
        for item in report_items:
            body_lines.append(
                f"  ✅ {item['company_name']} — {item['job_title']}"
                f"（{item['filename']}，已附於附件）"
            )
        body_lines.append("")

    if missing_items:
        body_lines.append("【報告未產生】")
        for item in missing_items:
            body_lines.append(
                f"  ⚠️ {item['company_name']} — {item['job_title']}"
                f"（查無報告檔，請確認 Node 9 是否正常執行）"
            )
        body_lines.append("")

    body_lines.append("如有任何問題，請查看 Agent 執行 log。")
    body = "\n".join(body_lines)

    # ── 準備附件 ─────────────────────────────────────────────────────────
    attachments = [
        {"filename": item["filename"], "content": item["content"]}
        for item in report_items
    ]

    # ── 寄出 ─────────────────────────────────────────────────────────────
    subject = f"[JobAgent] 面試調研報告（{len(report_items)} 份）"

    try:
        send_email_with_attachments(
            subject     = subject,
            body        = body,
            attachments = attachments if attachments else None,
        )
        print(f"[Node 10] 彙整 mail 已寄出，附件 {len(attachments)} 份。")
    except Exception as e:
        print(f"[Node 10] 寄信失敗：{e}")

    return {}