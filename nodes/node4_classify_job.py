# nodes/node4_classify_job.py

import os
import json
import re
from datetime import datetime
from openai import OpenAI


def _get_client():
    """懶初始化 OpenAI client，只在實際呼叫時建立"""
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s-]+", "_", text)
    return text


def classify_job_node(state, config):
    email_content = state.get("email_content", "")

    prompt = f"""你是一個職缺篩選助理。請分析以下郵件內容，判斷這封郵件是否為「針對收件人個人發出的職缺邀請」。

判斷標準：
- is_interview=true：獵頭直接邀請、104 詢問意願（對方公司主動對你個人發出），且能識別出公司名稱與職位名稱。判斷關鍵字：郵件 SUBJECT 或內文含「詢問意願」即為 true。
- is_interview=false：104 關注公司新職缺通知（系統群發）、LinkedIn 職缺推薦（演算法群發）、系統通知、電子報、無法識別公司或職位

職位類型不影響判斷，任何職位只要是個人邀請都算 true。

若 is_interview=true，請提取：
- company_name：公司名稱（只填核心公司名，不含括號說明或其他附註）
- job_title：職位名稱（只填核心職稱，不含地點、薪資、英文說明或括號內容）

若 is_interview=false 但郵件中仍有職缺資訊，company_name 與 job_title 仍請填入；
若完全無法識別（如純系統通知），則填空字串 ""。

請只回傳 JSON，格式如下，不要有任何其他文字：
{{
  "is_interview": true 或 false,
  "company_name": "公司名稱",
  "job_title": "職位名稱"
}}

郵件內容：
{email_content}
"""

    client = _get_client()
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.choices[0].message.content.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                return {}
        else:
            return {}

    is_interview = result.get("is_interview", False)

    if not is_interview:
        return {}

    company_name = result.get("company_name", "Unknown")
    job_title = result.get("job_title", "Unknown")

    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    slug = f"{_slugify(company_name)}_{_slugify(job_title)}"
    job_id = f"{slug}_{timestamp}"

    job_dict = {
        "job_id": job_id,
        "company_name": company_name,
        "job_title": job_title,
        "email_content": email_content,
        "job_details": {},
        "gap_analysis": {},
        "interview_questions": None,
        "markdown_report": None,
    }

    return {"interview_invites": [job_dict]}
