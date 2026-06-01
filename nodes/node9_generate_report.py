"""
nodes/node9_generate_report.py
------------------------------
Node 9｜generate_report_node

職責：
  1. 讀取 JobBranchState 的所有分析結果
  2. 用 gpt-4o-mini 組合四個 section 的 Markdown 報告
  3. 透過 FileSystem MCP write_file 寫入磁碟
  4. 回傳 markdown_report（供後續彙整 mail 使用）

四個 Section：
  1. 面試流程（interview_process）
  2. 面試準備方向（interview_prep + salary_info）
  3. 技能落差分析（gap_analysis）
  4. 預測面試考題（interview_questions）

檔名格式：{job_id}_{company_name}_{job_title}.md
輸出目錄：環境變數 REPORT_OUTPUT_DIR，預設 ./reports

注意事項：
  - 不回寫 OverallState，只操作磁碟
  - insufficient_sections 不為空時，對應 section 標注「⚠️ 無相關資料」
  - action_items 格式為 list[dict]（topic / subtopics / source），非 list[str]
  - MCP 不可用時 fallback 為直接 open() 寫檔

gpt-4o-mini：支援 temperature 與 max_tokens。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# 懶初始化
# ---------------------------------------------------------------------------

def _get_openai_client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


# ---------------------------------------------------------------------------
# Section 產生工具
# ---------------------------------------------------------------------------

def _section_interview_process(job_details: dict, insufficient: list[str]) -> str:
    """產生面試流程 section。"""
    if "interview_process" in insufficient:
        return "## 一、面試流程\n\n⚠️ 無相關搜尋資料，建議直接詢問 HR 確認流程。\n"

    results = job_details.get("interview_process", [])
    if not results:
        return "## 一、面試流程\n\n⚠️ 無相關搜尋資料，建議直接詢問 HR 確認流程。\n"

    lines = ["## 一、面試流程\n"]
    for r in results[:5]:
        title   = r.get("title", "")
        content = r.get("content", "")
        url     = r.get("url", "")
        lines.append(f"### {title}")
        lines.append(content)
        if url:
            lines.append(f"\n來源：{url}")
        lines.append("")
    return "\n".join(lines)


def _section_interview_prep(job_details: dict, insufficient: list[str]) -> str:
    """產生面試準備方向 + 薪資資訊 section。"""
    lines = ["## 二、面試準備方向與薪資資訊\n"]

    # 面試準備
    if "interview_prep" in insufficient:
        lines.append("### 面試準備方向\n\n⚠️ 無相關搜尋資料。\n")
    else:
        prep_results = job_details.get("interview_prep", [])
        if not prep_results:
            lines.append("### 面試準備方向\n\n⚠️ 無相關搜尋資料。\n")
        else:
            lines.append("### 面試準備方向\n")
            for r in prep_results[:5]:
                title   = r.get("title", "")
                content = r.get("content", "")
                url     = r.get("url", "")
                lines.append(f"**{title}**")
                lines.append(content)
                if url:
                    lines.append(f"\n來源：{url}")
                lines.append("")

    # 薪資資訊
    if "salary_info" in insufficient:
        lines.append("### 薪資資訊\n\n⚠️ 無相關搜尋資料。\n")
    else:
        salary_results = job_details.get("salary_info", [])
        if not salary_results:
            lines.append("### 薪資資訊\n\n⚠️ 無相關搜尋資料。\n")
        else:
            lines.append("### 薪資資訊\n")
            for r in salary_results[:3]:
                title   = r.get("title", "")
                content = r.get("content", "")
                url     = r.get("url", "")
                lines.append(f"**{title}**")
                lines.append(content)
                if url:
                    lines.append(f"\n來源：{url}")
                lines.append("")

    return "\n".join(lines)


def _section_gap_analysis(gap_analysis: dict) -> str:
    """產生技能落差分析 section。"""
    if not gap_analysis:
        return "## 三、技能落差分析\n\n⚠️ 無法取得分析結果。\n"

    lines = ["## 三、技能落差分析\n"]

    # 強項
    strengths = gap_analysis.get("strengths", [])
    lines.append("### ✅ 強項\n")
    if strengths:
        for s in strengths:
            lines.append(f"- {s}")
    else:
        lines.append("- （無資料）")
    lines.append("")

    # 待補強
    gaps = gap_analysis.get("gaps", [])
    lines.append("### ⚠️ 待補強項目\n")
    if gaps:
        for g in gaps:
            lines.append(f"- {g}")
    else:
        lines.append("- （無資料）")
    lines.append("")

    # 建議行動
    action_items = gap_analysis.get("action_items", [])
    lines.append("### 📋 建議行動\n")
    if action_items:
        for i, item in enumerate(action_items, 1):
            # action_items 為 list[dict]：topic / subtopics / source
            if isinstance(item, dict):
                topic     = item.get("topic", "")
                subtopics = item.get("subtopics", [])
                source    = item.get("source", "")
                lines.append(f"**{i}. {topic}**")
                for sub in subtopics:
                    lines.append(f"  - {sub}")
                if source:
                    lines.append(f"  > 來源：{source}")
                lines.append("")
            else:
                # fallback：舊版 list[str] 格式
                lines.append(f"- {item}")
    else:
        lines.append("- （無資料）")
    lines.append("")

    return "\n".join(lines)


def _section_interview_questions(interview_questions: list) -> str:
    """產生預測面試考題 section。"""
    lines = ["## 四、預測面試考題\n"]

    if not interview_questions:
        lines.append("⚠️ 無法取得面試題預測。\n")
        return "\n".join(lines)

    for i, q in enumerate(interview_questions, 1):
        if isinstance(q, dict):
            question = q.get("question", "")
            hint     = q.get("hint", "")
            lines.append(f"**Q{i}. {question}**")
            if hint:
                lines.append(f"> 💡 答題提示：{hint}")
            lines.append("")
        else:
            lines.append(f"**Q{i}. {q}**\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown 組裝
# ---------------------------------------------------------------------------

def _build_markdown(state: dict) -> str:
    company_name  = state.get("company_name", "未知公司")
    job_title     = state.get("job_title", "未知職位")
    job_id        = state.get("job_id", "")
    job_details   = state.get("job_details", {})
    gap_analysis  = state.get("gap_analysis", {})
    interview_questions = state.get("interview_questions", [])
    insufficient  = state.get("insufficient_sections") or []
    generated_at  = datetime.now().strftime("%Y-%m-%d %H:%M")

    header = (
        f"# 面試準備報告\n\n"
        f"**公司**：{company_name}  \n"
        f"**職位**：{job_title}  \n"
        f"**Job ID**：{job_id}  \n"
        f"**產生時間**：{generated_at}  \n"
    )

    if insufficient:
        section_labels = {
            "interview_process": "面試流程",
            "interview_prep":    "面試準備方向",
            "salary_info":       "薪資資訊",
        }
        missing = [section_labels.get(s, s) for s in insufficient]
        header += f"\n> ⚠️ 以下面向資料不足：{', '.join(missing)}\n"

    header += "\n---\n"

    s1 = _section_interview_process(job_details, insufficient)
    s2 = _section_interview_prep(job_details, insufficient)
    s3 = _section_gap_analysis(gap_analysis)
    s4 = _section_interview_questions(interview_questions)

    return "\n".join([header, s1, s2, s3, s4])


# ---------------------------------------------------------------------------
# 檔案寫入（MCP → fallback 直接寫）
# ---------------------------------------------------------------------------

def _sanitize_filename(s: str) -> str:
    """移除檔名不合法字元。"""
    return re.sub(r'[\\/*?:"<>|]', "_", s).strip()


def _write_report(job_id: str, company_name: str, job_title: str, content: str) -> str:
    """
    寫入 Markdown 報告。
    優先嘗試 FileSystem MCP；失敗時直接用 open() 寫入。
    回傳實際寫入的完整路徑。
    """
    output_dir = Path(os.environ.get("REPORT_OUTPUT_DIR", "./reports"))
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_company = _sanitize_filename(company_name)
    safe_title   = _sanitize_filename(job_title)
    filename     = f"{job_id}_{safe_company}_{safe_title}.md"
    filepath     = output_dir / filename

    # ── 嘗試 FileSystem MCP ──────────────────────────────────────────────
    mcp_success = False
    try:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client, StdioServerParameters

        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", str(output_dir)],
        )

        import asyncio

        async def _mcp_write():
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await session.call_tool(
                        "write_file",
                        {"path": str(filepath), "content": content},
                    )

        asyncio.run(_mcp_write())
        mcp_success = True
        print(f"[Node 9] MCP 寫入成功：{filepath}")

    except Exception as e:
        print(f"[Node 9] MCP 不可用（{e}），改用直接寫入。")

    # ── Fallback：直接寫入 ────────────────────────────────────────────────
    if not mcp_success:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[Node 9] 直接寫入成功：{filepath}")

    return str(filepath)


# ---------------------------------------------------------------------------
# 主節點
# ---------------------------------------------------------------------------

def generate_report_node(state: dict) -> dict:
    """
    Node 9｜報告生成節點。

    輸入（JobBranchState）：
        job_id, company_name, job_title
        job_details, gap_analysis, interview_questions
        insufficient_sections

    輸出：
        markdown_report: str（完整 Markdown 內容，供彙整 mail 使用）
    """
    job_id       = state.get("job_id", "unknown")
    company_name = state.get("company_name", "未知公司")
    job_title    = state.get("job_title", "未知職位")
    insufficient = state.get("insufficient_sections") or []

    print(f"[Node 9] 開始產生報告：job_id={job_id}")
    if insufficient:
        print(f"[Node 9] 不足面向：{insufficient}")

    # ── 組合 Markdown ────────────────────────────────────────────────────
    markdown = _build_markdown(state)

    # ── 寫入磁碟 ─────────────────────────────────────────────────────────
    filepath = _write_report(job_id, company_name, job_title, markdown)

    print(
        f"[Node 9] 完成：{filepath}，"
        f"字數={len(markdown)}，"
        f"不足面向={insufficient or '無'}"
    )

    # 不回寫 OverallState，只更新 JobBranchState.markdown_report
    return {"markdown_report": markdown}