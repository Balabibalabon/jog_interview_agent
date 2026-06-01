# gmail_client.py
# Node 3 與 Phase 4 共用的 Gmail 工具函式

import os
import base64
import re
from pathlib import Path
from typing import List, Dict, Optional
from dotenv import load_dotenv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.json")


def get_gmail_service():
    if not Path(TOKEN_PATH).exists():
        raise FileNotFoundError(
            f"找不到 {TOKEN_PATH}，請先執行 python gmail_auth.py 完成授權。"
        )

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError("Token 無效，請重新執行 python gmail_auth.py。")

    service = build("gmail", "v1", credentials=creds)
    return service


def _decode_base64(data: str) -> str:
    padded = data + "=" * (4 - len(data) % 4)
    decoded_bytes = base64.urlsafe_b64decode(padded)
    return decoded_bytes.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    html = html.replace("\r\n", "\n").replace("\r", "\n")
    html = re.sub(r"<!--\[if[^\]]*\]>.*?<!\[endif\]-->", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<!\[if[^\]]*\]>.*?<!\[endif\]>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<(br|/p|/div|/li|/tr|/td)[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&")
    html = html.replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&quot;", '"').replace("&#39;", "'")
    html = html.replace("&zwnj;", "").replace("&zwsp;", "")
    html = re.sub(r"&#\d+;", "", html)
    html = re.sub(r"&[a-zA-Z]+;", "", html)
    html = re.sub(r"<https?://[^>]+>", "", html)
    html = re.sub(r"https?://\S+", "", html)
    html = re.sub(r"[ \t]{2,}", " ", html)
    html = re.sub(r"\n[　\t \xa0]*\n", "\n\n", html)
    html = re.sub(r"(\n\n)+", "\n\n", html)
    return html.strip()


def _clean_plain_text(text: str) -> str:
    text = re.sub(r"<https?://[^>]+>", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_body(payload: dict) -> str:
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if not data:
            return ""
        return _clean_plain_text(_decode_base64(data))

    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        if not data:
            return ""
        return _strip_html(_decode_base64(data))

    if mime_type.startswith("multipart/"):
        parts = payload.get("parts", [])

        if mime_type == "multipart/alternative":
            for part in parts:
                if part.get("mimeType") == "text/plain":
                    result = _extract_body(part)
                    if result:
                        return result
            for part in parts:
                if part.get("mimeType") == "text/html":
                    result = _extract_body(part)
                    if result:
                        return result
            return ""

        else:
            texts = []
            for part in parts:
                part_mime = part.get("mimeType", "")
                filename = part.get("filename", "")
                if filename:
                    continue
                if part_mime.startswith("text/") or part_mime.startswith("multipart/"):
                    text = _extract_body(part)
                    if text:
                        texts.append(text)
            return "\n".join(texts)

    return ""


def fetch_recent_emails(
    service,
    max_results: int = 10,
    query: str = "",
    days_back: int = 1,
) -> List[Dict]:
    try:
        from datetime import date, timedelta

        list_params = {
            "userId": "me",
            "maxResults": max_results,
            "labelIds": ["INBOX"],
        }

        if days_back > 0:
            today = date.today()
            since = today - timedelta(days=days_back)
            date_query = f"after:{since.strftime('%Y/%m/%d')} before:{today.strftime('%Y/%m/%d')}"
            combined_query = f"{date_query} {query}".strip()
        else:
            combined_query = query

        if combined_query:
            list_params["q"] = combined_query

        result = service.users().messages().list(**list_params).execute()
        messages = result.get("messages", [])

        if not messages:
            return []

        emails = []
        for msg_ref in messages:
            gmail_id = msg_ref["id"]

            msg = service.users().messages().get(
                userId="me",
                id=gmail_id,
                format="full",
            ).execute()

            payload = msg.get("payload", {})
            headers = payload.get("headers", [])

            header_map = {h["name"].lower(): h["value"] for h in headers}

            message_id  = header_map.get("message-id", "")
            subject     = header_map.get("subject", "(無主旨)")
            date        = header_map.get("date", "")
            from_       = header_map.get("from", "")
            in_reply_to = header_map.get("in-reply-to", "")

            body_text = _extract_body(payload)
            body_text_truncated = body_text[:2000] if len(body_text) > 2000 else body_text

            email_content = (
                f"MESSAGE_ID: {message_id}\n"
                f"SUBJECT: {subject}\n"
                f"DATE: {date}\n"
                f"FROM: {from_}\n"
                f"\n"
                f"{body_text_truncated}"
            )

            emails.append({
                "message_id":    message_id,
                "gmail_id":      gmail_id,
                "subject":       subject,
                "date":          date,
                "from_":         from_,
                "in_reply_to":   in_reply_to,
                "body_text":     body_text_truncated,
                "email_content": email_content,
            })

        return emails

    except HttpError as e:
        print(f"Gmail API 錯誤：{e}")
        raise


def send_email_with_attachments(
    subject: str,
    body: str,
    attachments: list[dict] | None = None,
) -> None:
    import base64
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    service = get_gmail_service()
    profile = service.users().getProfile(userId="me").execute()
    user_email = profile["emailAddress"]

    if attachments:
        msg = MIMEMultipart()
        msg["to"]      = user_email
        msg["from"]    = user_email
        msg["subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        for att in attachments:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(att["content"].encode("utf-8"))
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{att["filename"]}"',
            )
            msg.attach(part)
    else:
        msg = MIMEMultipart()
        msg["to"]      = user_email
        msg["from"]    = user_email
        msg["subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()
