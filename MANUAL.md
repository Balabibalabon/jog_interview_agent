# Job Agent 使用手冊

最後更新：2026-05-31

---

## 系統概述

Job Agent 是基於 LangGraph 的自動化職缺調研系統，每日自動掃描 Gmail 中的面試邀請，寄出彙整信讓你確認要跟進的職缺，並自動產生面試準備報告寄回給你。

完整流程：
1. 啟動系統，回答是否更新履歷
2. 系統抓取昨日的面試邀請信件並分類
3. 系統寄出彙整信，列出所有面試邀請
4. 你回覆 mail 確認要跟進的職缺
5. 系統自動調研公司、進行 Gap 分析、產生報告
6. 系統寄出包含報告附件的通知 mail

---

## 每次啟動前的前置步驟

```bash
# 1. 啟動 PostgreSQL
sudo service postgresql start

# 2. 進入專案目錄並啟動虛擬環境
cd /home/balabibalabon/job_interview_agent
source job_interview/bin/activate

# 3. 載入環境變數
set -a && source .env && set +a
```

---

## 確認前置條件（建議第一次或出問題時執行）

```bash
# 確認 DB 存在
sudo -u postgres psql -c "\l"

# 確認 Gmail token 存在
ls $GMAIL_TOKEN_PATH

# 確認 INTERACTION_MODE 設定
grep INTERACTION_MODE .env
```

---

## 正式啟動

每天執行時自動使用當天日期作為 thread_id，不需手動清除 checkpoint。系統會在 terminal 互動詢問履歷，並在彙整信寄出後自動等待你的 mail 回覆：

```bash
INTERACTION_MODE=email python -c "
from graph import build_graph
from langgraph.types import Command
import datetime
import threading

THREAD_ID = 'job_agent_' + datetime.date.today().strftime('%Y%m%d')
graph = build_graph(use_postgres=True)

done_event = threading.Event()

config = {
    'configurable': {
        'thread_id': THREAD_ID,
        'graph':     graph,
        'done_event': done_event,
    }
}

input_data = {}

while True:
    stream_input = Command(resume=input_data['reply']) if 'reply' in input_data else {}
    interrupted  = False

    for chunk in graph.stream(stream_input, config, stream_mode='updates'):
        print(chunk)
        if '__interrupt__' in chunk:
            interrupt_value = chunk['__interrupt__'][0].value

            if '等待使用者回覆' in interrupt_value:
                print('=== 彙整信已寄出，監聽器在背景等待使用者回信 ===')
                print('=== 使用者回信後系統將自動繼續，Ctrl+C 可退出 ===')
                try:
                    done_event.wait()
                    print('=== 所有流程完成 ===')
                except KeyboardInterrupt:
                    print('=== 使用者中斷 ===')
                exit(0)

            print(f'\n>>> {interrupt_value}')
            user_input = input('>>> ').strip()
            input_data['reply'] = user_input
            interrupted = True
            break

    if not interrupted:
        break
"
```

啟動後互動流程：

```
>>> [check_store] 尚無技能記憶，是否現在輸入履歷？(y/n)
>>> y

>>> [update_store] 請輸入履歷 PDF 路徑，或直接貼上履歷文字：
>>> ./resume.pdf

=== 彙整信已寄出，監聽器在背景等待使用者回信 ===
=== 使用者回信後系統將自動繼續，Ctrl+C 可退出 ===
```

收到彙整信後，直接回覆 mail 告知要跟進的職缺編號，例如：「跟進 1」或「全部都要」。

---

## 抓取信件的日期範圍設定

預設抓取昨天一天的信件（`GMAIL_DAYS_BACK=1`）。如需調整：

```bash
# 抓昨天的信（預設）
GMAIL_DAYS_BACK=1 INTERACTION_MODE=email python -c "..."

# 抓最近兩天的信
GMAIL_DAYS_BACK=2 INTERACTION_MODE=email python -c "..."
```

也可以直接在 `.env` 裡設定：

```bash
GMAIL_DAYS_BACK= <往前幾天>
```

---

## 同一天內中斷後 resume

如果當天流程中斷（例如 Ctrl+C 或重啟），直接重新執行啟動指令即可，系統會自動從中斷點接續，不會重複寄信。

---

### 跨 process 手動 resume（非常規，備用）

```bash
INTERACTION_MODE=email python -c "
from graph import build_graph
from langgraph.types import Command
import datetime

THREAD_ID = 'job_agent_' + datetime.date.today().strftime('%Y%m%d')
graph = build_graph(use_postgres=True)
config = {
    'configurable': {
        'thread_id': THREAD_ID,
        'graph': graph,
    }
}

for chunk in graph.stream(Command(resume='跟進 1'), config, stream_mode='updates'):
    print(chunk)
"
```

---

## 履歷管理

### 首次輸入或更新履歷

啟動時回答 `y` 進入履歷輸入，支援 PDF 路徑或直接貼上文字。輸入完成後系統會印出解析結果：

```
[update_store] 技能記憶已更新：軟體工程師 | AI 工程師 3年
[update_store] 抓取到的技能點（共 16 項）：
  - Python
  - LangGraph
  - ...
```

請確認技能清單是否符合你的實際技能，如有遺漏可重新輸入。

### 更新履歷前必須清除舊的向量資料

Node 8 Gap 分析使用 pgvector 做技能比對，每次上傳新履歷前需先清除舊資料，否則舊技能會混入搜尋結果：

```bash
python -c "
import os, psycopg
conn = psycopg.connect(os.environ['POSTGRES_URI'])
cur = conn.cursor()
cur.execute('DELETE FROM user_skill_vectors')
conn.commit()
print('user_skill_vectors 清除完成')
conn.close()
"
```

### 清除履歷重新輸入

```python
from langgraph.store.postgres import PostgresStore
import os, psycopg

conn = psycopg.connect(os.environ["POSTGRES_URI"], autocommit=True)
store = PostgresStore(conn)
store.setup()
store.delete(("user_profile",), "skills")
print("履歷清除完成")
```

或是在 terminal 中執行以下指令:
```bash
python DeleteDBSkills.py
```

---

## Checkpoint 管理

系統使用每日不同的 thread_id（`job_agent_YYYYMMDD`），不需手動清除 checkpoint。

### 同一天想重頭重跑

```bash
python -c "
import os, psycopg
conn = psycopg.connect(os.environ['POSTGRES_URI'])
cur = conn.cursor()
for table in ['checkpoints', 'checkpoint_writes', 'checkpoint_blobs']:
    cur.execute(f\"DELETE FROM {table} WHERE thread_id LIKE 'job_agent_%'\")
conn.commit()
print('清除完成')
conn.close()
"
```

### 查詢目前有哪些 thread_id

```bash
python -c "
import os, psycopg
conn = psycopg.connect(os.environ['POSTGRES_URI'])
cur = conn.cursor()
cur.execute(\"SELECT DISTINCT thread_id FROM checkpoints\")
print(cur.fetchall())
conn.close()
"
```

---

## 資料庫 Table 說明

| Table | 用途 | 重置時清除 |
|---|---|---|
| `checkpoints` | Graph 狀態快照 | ✅ 需要時清除 |
| `checkpoint_writes` | 節點 write 紀錄 | ✅ 需要時清除 |
| `checkpoint_blobs` | 大型 state 資料 | ✅ 需要時清除 |
| `checkpoint_migrations` | Schema 版本紀錄 | ❌ 不可清除 |
| `user_skill_vectors` | 履歷向量資料（pgvector） | ❌ 更新履歷前手動清除 |

---

## 常見問題排除

### 問題：執行後只輸出 `{'check_store_node': None}` 就結束

原因：PostgreSQL 中存有舊的 checkpoint，graph 認為流程已完成。

解法：執行上方的 checkpoint 清除指令後重新啟動。

### 問題：Gmail token 不存在或過期

解法：

```bash
python gmail_auth.py
# 會開啟瀏覽器，完成 OAuth 授權後 token.json 自動產生
```

### 問題：監聽器一直沒有觸發

可能原因：
- 你的回信沒有 `In-Reply-To` header（不是直接回覆彙整信，而是新開一封信）
- 回信時間早於彙整信寄出時間（時區問題）

解法：確認是直接在 Gmail 點「回覆」彙整信，不要新開信件。

### 問題：Gap 分析的技能出現不認識的項目

原因：`user_skill_vectors` 有舊資料殘留。

解法：執行上方的 `user_skill_vectors` 清除指令，重新上傳履歷。

---

## 環境變數清單

| 變數 | 說明 | 預設值 |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key | 必填 |
| `TAVILY_API_KEY` | Tavily 調研 API key | 必填 |
| `POSTGRES_URI` | PostgreSQL 連線字串 | 必填 |
| `GMAIL_CREDENTIALS_PATH` | Gmail OAuth credentials.json 路徑 | 必填 |
| `GMAIL_TOKEN_PATH` | Gmail token.json 路徑 | 必填 |
| `INTERACTION_MODE` | `email` 或 `terminal` | `terminal` |
| `GMAIL_DAYS_BACK` | 抓取幾天前到今天的信件 | `1` |
| `REPORT_OUTPUT_DIR` | .md 報告輸出目錄 | `./reports` |

---

## 已知限制

| 項目 | 現況 |
|---|---|
| 串行處理 | 多個 approved_job 依序處理，無法並行 |
| 監聽器識別回信 | 依 `In-Reply-To` header 判斷，需直接回覆彙整信 |
| 調研資料品質 | 部分職缺（如業務類）的面試流程/準備方向資料稀少，最多重試 3 輪後強制繼續 |
| 薪資調研 | 偶爾抓到不相關的網頁，屬 Tavily 搜尋品質問題 |

## Reference:
[如何設定 google project 啟用 gmail api :](https://vocus.cc/article/68c66bc9fd89780001f5b8c9)