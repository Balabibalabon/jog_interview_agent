# Job Interview Agent

基於 LangGraph 的自動化職缺調研系統。每日掃描 Gmail 中的面試邀請，寄出彙整信讓你確認要跟進的職缺，並自動產生面試準備報告寄回給你。

---

## 系統流程

```
啟動
  ↓
[Node 1] 確認履歷是否存在，詢問是否更新
  ↓ (需要) → [Node 2] 上傳履歷（PDF 或文字），解析技能寫入 Store
  ↓
[Node 3] 抓取昨日 Gmail 面試邀請（最多 20 封）
  ↓
[Node 4] LLM 分類：是否為個人面試邀請（並行）
  ↓
[Node 5a] 寄出彙整信給自己，啟動背景監聽器
  ↓
[Node 5b] interrupt() 暫停，等待使用者回信
  ↓ 監聽器偵測到回信，自動 resume
[Node 6] LLM 解析回信，取得 approved_job_ids
  ↓
[Node 7] Tavily 調研（面試流程、準備方向、薪資）
  ↓
[Node 7b] Think Node 判斷資料品質，不足則重試（最多 3 輪）
  ↓
[Node 8] pgvector + LLM Gap 分析，比對個人技能與職缺要求
  ↓
[Node 9] 產生 Markdown 報告，透過 MCP 寫入 ./reports/
  ↓ 多個職缺時串行繼續，直到全部完成
[Node 10] 寄出彙整通知 mail，附上所有報告
  ↓
END
```

![Job Agent System Flow](./job_agent_flow_v4.svg)

---

## 技術選型

| 項目 | 選擇 |
|---|---|
| Framework | LangGraph 1.2.2 |
| LLM | OpenAI gpt-4o-mini |
| Python | 3.11.15 |
| Checkpointer | PostgresSaver（持久化，支援跨 process resume） |
| Store | PostgresStore（長期記憶） |
| 資料庫 | PostgreSQL 14（WSL Ubuntu 22.04） |
| 向量資料庫 | pgvector（技能 embedding 比對） |
| Embedding | text-embedding-3-small（OpenAI，1536 維） |
| 網路調研 | Tavily |
| 檔案寫入 | FileSystem MCP |
| PDF 解析 | pymupdf（fitz） |
| Email | Gmail API（OAuth 2.0） |

### 五大核心能力

- **長期記憶**：履歷與技能存於 PostgresStore，跨日執行不需重複輸入
- **串行處理**：`pending_job_ids` 佇列確保多職缺依序處理，避免 OverallState 欄位互蓋
- **遠端 HIL（Human-in-the-Loop）**：透過 Email + LangGraph `interrupt()` 實現跨 process 的使用者確認
- **RAG 檢索**：pgvector cosine similarity 搜尋，比對個人技能與職缺要求
- **本地檔案寫入**：FileSystem MCP 將報告寫入 `./reports/`

---

## 前置需求

- Python 3.11
- PostgreSQL 14+（含 pgvector extension）
- Gmail 帳號（需完成 OAuth 2.0 授權）
- OpenAI API key
- Tavily API key

---

## 安裝步驟

### 1. 建立虛擬環境並安裝套件

```bash
cd job_interview_agent
python -m venv job_interview
source job_interview/bin/activate
pip install -r requirements.txt
```

### 2. 設定環境變數

複製並編輯 `.env`：

```bash
OPENAI_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
POSTGRES_URI=postgresql://job_agent:你的密碼@localhost:5432/job_agent
GMAIL_CREDENTIALS_PATH=/home/balabibalabon/job_interview_agent/credentials.json
GMAIL_TOKEN_PATH=/home/balabibalabon/job_interview_agent/token.json
INTERACTION_MODE=email
GMAIL_DAYS_BACK=1
REPORT_OUTPUT_DIR=./reports
```

### 3. 設定 PostgreSQL

```bash
sudo service postgresql start
sudo -u postgres psql -c "CREATE DATABASE job_agent;"
sudo -u postgres psql -c "CREATE USER job_agent WITH PASSWORD '你的密碼';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE job_agent TO job_agent;"
sudo -u postgres psql job_agent -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 4. 完成 Gmail OAuth 授權

前往 Google Cloud Console 建立 OAuth 2.0 憑證，下載 `credentials.json` 放入專案根目錄，然後執行：

```bash
python gmail_auth.py
```

瀏覽器會開啟授權頁面，完成後 `token.json` 自動產生。

---

## 使用方式

### 啟動前

```bash
sudo service postgresql start
cd /home/balabibalabon/job_interview_agent
source job_interview/bin/activate
set -a && source .env && set +a
```

### 啟動系統

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

啟動後，系統會在 terminal 互動詢問：

```
>>> [check_store] 目前已有技能記憶：...是否更新履歷？(y/n)
>>> n

（系統抓信、分類、寄出彙整信）

=== 彙整信已寄出，監聽器在背景等待使用者回信 ===
```

收到彙整信後，直接**回覆該封信**（不要新開信件），內容例如：

```
跟進 1
```

或

```
全部都要
```

監聽器偵測到回信後，系統自動繼續調研並寄出報告。

---

## 專案結構

```
job_interview_agent/
├── .env                         # 環境變數
├── requirements.txt
├── gmail_auth.py                # Gmail OAuth 授權腳本
├── gmail_client.py              # Gmail API 工具函式（收發信、日期過濾）
├── email_listener.py            # 背景監聽器（偵測回信，自動 resume graph）
├── state.py                     # OverallState + JobBranchState + Reducer 定義
├── graph.py                     # Graph 組裝、Conditional Edge、build_graph()
├── nodes/
│   ├── node1_check_store.py     # 確認履歷是否存在，路由到 Node 2 或 Node 3
│   ├── node2_update_store.py    # 上傳履歷（PDF/文字），解析技能寫入 Store
│   ├── node3_fetch_emails.py    # 抓取 Gmail 信件，Fan-out 到 Node 4
│   ├── node4_classify_job.py    # LLM 分類：是否為個人面試邀請
│   ├── node5_send_summary.py    # 寄出彙整信，啟動背景監聽器
│   ├── node5b_wait_for_reply.py # interrupt()，等待使用者回信
│   ├── node6_parse_reply.py     # LLM 解析回信，取得 approved_job_ids
│   ├── node7_research_company.py# Tavily 三面向調研
│   ├── node7b_think_node.py     # 判斷調研資料品質，決定是否重試
│   ├── node8_gap_analysis.py    # pgvector + LLM Gap 分析
│   ├── node9_generate_report.py # 產生 Markdown 報告，MCP 寫入
│   └── node10_send_final_email.py # 寄出彙整通知 mail（含附件）
├── reports/                     # 產生的 .md 報告存放位置
└── tests/
    ├── test_reducers.py
    ├── test_phase2.py
    ├── test_phase3.py
    ├── test_phase4.py
    ├── test_phase5.py
    └── test_phase6.py
```

---

## 環境變數說明

| 變數 | 說明 | 預設值 |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key | 必填 |
| `TAVILY_API_KEY` | Tavily 調研 API key | 必填 |
| `POSTGRES_URI` | PostgreSQL 連線字串 | 必填 |
| `GMAIL_CREDENTIALS_PATH` | Gmail OAuth credentials.json 路徑 | 必填 |
| `GMAIL_TOKEN_PATH` | Gmail token.json 路徑 | 必填 |
| `INTERACTION_MODE` | `email`（上線）或 `terminal`（開發） | `terminal` |
| `GMAIL_DAYS_BACK` | 抓取幾天前到今天的信件 | `1` |
| `REPORT_OUTPUT_DIR` | .md 報告輸出目錄 | `./reports` |
| `USE_MOCK_EMAILS` | `true` 使用 mock 信件（測試用） | `false` |

---

## 開發與測試

### 執行單元測試

```bash
pytest tests/ -v -k "not LLMAsJudge"
```

### Terminal mode 開發驗證

```bash
INTERACTION_MODE=terminal python -c "
from graph import build_graph, THREAD_ID
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

store = InMemoryStore()
graph = build_graph(store=store)
config = {
    'configurable': {
        'thread_id': THREAD_ID,
        'store':     store,
        'graph':     graph,
    }
}

for chunk in graph.stream({}, config, stream_mode='updates'):
    print(chunk)
    if '__interrupt__' in chunk:
        break

for chunk in graph.stream(Command(resume='跟進 1'), config, stream_mode='updates'):
    print(chunk)
"
```

---

## 重要設計決策

### LangGraph 1.2.2 interrupt() 三條鐵律

**鐵律一**：resume 後節點從頭重執行，有 side effect 的程式碼必須獨立成節點。這是 Node 5 拆成 5a（寄信）和 5b（interrupt）的原因。

**鐵律二**：resume 前絕對不可呼叫 `update_state()`，否則 resume 靜默失敗（零 chunk）。

**鐵律三**：resume value 透過 `Command(resume=value)` 傳入，由 `interrupt()` 回傳值取得。

### 串行處理架構

多個 approved_job 時，所有 branch 共用同一個 OverallState，並行執行會互蓋欄位。改用 `pending_job_ids` 佇列，每次只取第一個 job 處理，Node 9 完成後繼續取下一個。

### 監聽器跨 process 設計

監聽器透過 `threading.Event`（`done_event`）與主 process 通訊，resume 完成後 `done_event.set()` 通知主 process 退出。監聽器使用 `In-Reply-To` header 區分系統彙整信與使用者回信，避免誤觸發。

---

## 已知限制

| 項目 | 說明 |
|---|---|
| 串行處理 | 多個 approved_job 依序處理，無法並行 |
| 監聽器識別回信 | 依賴 `In-Reply-To` header，需直接回覆彙整信，不可新開信件 |
| 調研資料品質 | 業務類、小公司職缺的面試資料稀少，最多重試 3 輪後強制繼續 |
| 薪資調研 | 偶爾抓到不相關網頁，屬 Tavily 搜尋品質問題 |

---

## 更多說明

詳細的日常操作、履歷管理、Checkpoint 管理、常見問題排除，請參閱 [MANUAL.md](./MANUAL.md)。
