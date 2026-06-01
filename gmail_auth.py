import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

load_dotenv()

# 關閉 oauthlib 的 scope 變更警告（避免被當 exception 拋出）
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.json")


def authorize():
    creds = None

    if Path(TOKEN_PATH).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Token 已過期，嘗試自動 refresh...")
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
            auth_url, _ = flow.authorization_url(
                access_type="offline",
                prompt="consent",
            )

            print("\n請複製以下 URL，在你的本機瀏覽器開啟：")
            print("-" * 60)
            print(auth_url)
            print("-" * 60)
            print("\n登入並授權後，Google 會顯示一串授權碼。")
            auth_code = input("請將授權碼貼在這裡，然後按 Enter：").strip()

            flow.fetch_token(code=auth_code)
            creds = flow.credentials

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        print(f"\n授權成功！Token 已儲存至 {TOKEN_PATH}")
    else:
        print("Token 有效，無需重新授權。")

    return creds


if __name__ == "__main__":
    creds = authorize()
    print(f"Scopes：{creds.scopes}")
    print(f"有效：{creds.valid}")