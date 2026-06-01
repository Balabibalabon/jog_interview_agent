"""
node6_parse_reply.py
--------------------
Node 6｜parse_user_reply_node

流程：
1. 讀取 user_reply_text（外部監聽器寫入的回信內文）
2. 空字串或 None → approved_job_ids = []，流程結束
3. 非空 → 交給 GPT-5 nano 解析使用者想跟進的職缺
4. 回傳 approved_job_ids（job_id 字串清單）

懶初始化：_get_client() 在函式內部建立，避免 module 載入時需要 OPENAI_API_KEY。
gpt-4o-mini：支援 temperature，本節點不傳入以使用預設值。
"""

from __future__ import annotations

import json
import re
import os


def _get_client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def parse_user_reply_node(state, config):
    invites    = state.get("interview_invites") or []
    user_reply = state.get("user_reply_text")

    # ── 空回覆 → 全部不跟進 ────────────────────────────────────────────
    if not user_reply or not user_reply.strip():
        print("[Node 6] 無回覆或空回覆，approved_job_ids = []。")
        return {"approved_job_ids": []}

    # ── 無邀請清單（理論上不應發生）───────────────────────────────────
    if not invites:
        print("[Node 6] interview_invites 為空，approved_job_ids = []。")
        return {"approved_job_ids": []}

    # ── 組合 prompt ────────────────────────────────────────────────────
    invite_lines = []
    for i, job in enumerate(invites, 1):
        job_id  = job.get("job_id", "")
        company = job.get("company_name", "（未知）")
        title   = job.get("job_title", "（未知）")
        invite_lines.append(f"{i}. [{job_id}] {company} — {title}")

    invite_list_str = "\n".join(invite_lines)

    prompt = f"""你是一個職缺管理助理。以下是使用者收到的面試邀請清單：

{invite_list_str}

使用者的回覆如下：
---
{user_reply.strip()}
---

請根據使用者的回覆，判斷他想跟進哪些職缺，回傳對應的 job_id 清單。

規則：
- 若使用者說「全部」、「都要」、「all」等，回傳所有 job_id
- 若使用者說「都不用」、「不跟進」、「none」等，回傳空清單
- 若使用者指定編號（如「1、3」），回傳對應的 job_id
- 若使用者指定公司或職稱名稱，回傳對應的 job_id
- 無法判斷時，回傳空清單
- job_id 必須完整複製清單中方括號內的字串，例如 "誠億企業有限公司_法拍撤回業務專員_202605291353"，不可只回傳部分

只回傳 JSON，格式如下，不要有任何其他文字：
{{"approved_job_ids": ["job_id_1", "job_id_2"]}}"""

    # ── 呼叫 LLM ───────────────────────────────────────────────────────
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        raw = response.choices[0].message.content.strip()
        print(f"[Node 6] LLM 原始回應：{raw}")

    except Exception as e:
        print(f"[Node 6] LLM 呼叫失敗：{e}，回傳空清單。")
        return {"approved_job_ids": []}

    # ── 解析 JSON ──────────────────────────────────────────────────────
    approved_ids = _parse_approved_ids(raw)
    print(f"[Node 6] approved_job_ids：{approved_ids}")
    return {"approved_job_ids": approved_ids}


def _parse_approved_ids(raw: str) -> list[str]:
    """
    從 LLM 回應中解析 approved_job_ids。
    先嘗試直接 JSON parse，失敗則用 regex 萃取，完全失敗回傳空清單。
    """
    # 嘗試直接解析
    try:
        data = json.loads(raw)
        ids = data.get("approved_job_ids", [])
        if isinstance(ids, list):
            return [str(i) for i in ids]
    except json.JSONDecodeError:
        pass

    # regex 萃取：找 ["xxx", "yyy"] 格式
    match = re.search(r'\[([^\]]*)\]', raw)
    if match:
        inner = match.group(1)
        ids = re.findall(r'"([^"]+)"', inner)
        if ids:
            return ids

    return []