"""
nodes/node1_check_store.py
--------------------------
Node 1｜check_store_node

職責：
  1. 從 Store 讀取使用者技能記憶 ("user_profile", "skills")
  2. 詢問使用者是否更新履歷
     - terminal mode：使用 input()
     - email mode：使用 interrupt()
  3. 透過 Command(goto=...) 直接指定下一個節點（不寫入 State）

路由出口：
  Command(goto="update_store_node") → 使用者輸入 y
  Command(goto="fetch_emails_node") → 使用者輸入 n 或直接 Enter
"""

from __future__ import annotations

import os
from langgraph.types import Command, interrupt
from langchain_core.runnables import RunnableConfig

INTERACTION_MODE = os.environ.get("INTERACTION_MODE", "terminal")


def make_check_store_node(store=None):
    def check_store_node(state: dict, config: RunnableConfig) -> Command:
        _store = (
            config.get("configurable", {}).get("store")
            or store
        )

        if _store is None:
            raise ValueError(
                "Store 未提供。"
                "測試環境請透過 make_check_store_node(store=...) 傳入；"
                "上線環境請在 config['configurable']['store'] 帶入。"
            )

        existing = _store.get(("user_profile",), "skills")
        has_profile = existing is not None

        if INTERACTION_MODE == "terminal":
            if has_profile:
                value = existing.value if hasattr(existing, "value") else existing
                print("\n[check_store] 目前已有技能記憶：")
                print(f"  {value}")
                answer = input(
                    "[check_store] 是否更新履歷？(y/n，直接 Enter 視為 n)："
                ).strip().lower()
            else:
                print("\n[check_store] 尚無技能記憶，建議先建立履歷。")
                answer = input(
                    "[check_store] 是否現在輸入履歷？(y/n，直接 Enter 視為 n)："
                ).strip().lower()

        else:
            # email mode：用 interrupt() 等待使用者回應
            if has_profile:
                value = existing.value if hasattr(existing, "value") else existing
                answer = interrupt(
                    f"[check_store] 目前已有技能記憶：{value}\n是否更新履歷？(y/n)"
                )
            else:
                answer = interrupt(
                    "[check_store] 尚無技能記憶，是否現在輸入履歷？(y/n)"
                )
            answer = answer.strip().lower()

        goto = "update_store_node" if answer == "y" else "fetch_emails_node"
        return Command(goto=goto)

    return check_store_node