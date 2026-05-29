# Hermes Nexus

AI 多代理人任務調動系統，以 Linear label 驅動工作流程。

## 系統概覽

人工在 Linear 貼上 `agent-ready` 後，系統自動派工、開發、審核，直到進入 `human-confirm` 等待人工確認。

```
人工貼上 agent-ready
        ↓
  Hermes Master          ← 判斷派給哪個 Agent
        ↓
  Development Agent      ← 執行開發任務
        ↓
  Reviewer Agent         ← 審核產出
        ↓
  human-confirm          ← 人工確認並 merge
```

異常時 Agent 會設成 `agent-escalate`，由 Hermes Master 重新判斷或轉 `human-failed`。

---

## Agent 說明

| Agent | 監聽 Label | 說明 | Prompt |
|---|---|---|---|
| [Hermes Master](#hermes-master) | `agent-ready` `agent-escalate` | 初次派工與異常重判 | [派工](agents/hermes_master/dispatch_prompt.md) · [升級處理](agents/hermes_master/escalate_prompt.md) |
| [Development Agent](#development-agent) | `agent-dev` | 執行開發任務，支援 Claude Code CLI | [Prompt](agents/development_agent/development_prompt.md) |
| [Reviewer Agent](#reviewer-agent) | `agent-review` | 審核開發產出，決定 approve / reject / escalate | [Prompt](agents/reviewer_agent/system_prompt.md) |

---

## Hermes Master

監聽兩種 label：

- **`agent-ready`**：分析 issue 內容，決定路由到哪個 Agent
- **`agent-escalate`**：讀取失敗留言，重新路由或轉 `human-failed`

→ [查看派工 Prompt](agents/hermes_master/dispatch_prompt.md)  
→ [查看升級處理 Prompt](agents/hermes_master/escalate_prompt.md)

```bash
python agents/hermes_master/hermes_master.py
python agents/hermes_master/hermes_master.py --daemon --interval 30
python agents/hermes_master/hermes_master.py --identifier HER-5 --ready-only
```

---

## Development Agent

監聽 `agent-dev` label，根據 Linear issue 描述執行開發。

- 支援 Claude Code CLI backend（可切換 Codex）
- 需求不足時自動轉 `human-confirm` 並列出缺少資訊
- 每個任務建立獨立 git worktree（branch: `agent/HER-xx-task-slug`）
- 執行紀錄儲存於 `task_runs/`

→ [查看 Prompt](agents/development_agent/development_prompt.md)

```bash
python agents/development_agent/development_agent.py
python agents/development_agent/development_agent.py --daemon --interval 30
python agents/development_agent/development_agent.py --identifier HER-5
```

---

## Reviewer Agent

監聽 `agent-review` label，使用 Claude API 審核 Development Agent 的產出。

審核依據：原始需求 + git diff + dev_result.md + issue 留言

| 決策 | 下一步 |
|---|---|
| approve | `human-confirm` |
| reject | `agent-dev`（附具體修改建議） |
| escalate | `agent-escalate` |

→ [查看 Prompt](agents/reviewer_agent/system_prompt.md)

```bash
python agents/reviewer_agent/reviewer_agent.py
python agents/reviewer_agent/reviewer_agent.py --daemon --interval 30
python agents/reviewer_agent/reviewer_agent.py --identifier HER-5
```

---

## Label 流程

| Label | 處理者 | 意義 |
|---|---|---|
| `agent-ready` | Hermes Master | 人工確認，等待初次派工 |
| `agent-dev` | Development Agent | 開發進行中 |
| `agent-review` | Reviewer Agent | 審核進行中 |
| `agent-escalate` | Hermes Master | 異常，需重新判斷 |
| `human-confirm` | 人工 | Agent 流程完成，等待人工確認 merge |
| `human-failed` | 人工 | 無法由 Agent 處理，需人工介入 |

Status `In Progress` = 某個 Agent 正在處理中（防止重複執行）。

---

## 快速開始

### 1. 安裝依賴

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 設定環境變數

```bash
cp .env.example .env
# 編輯 .env，填入以下必填項目：
# LINEAR_API_KEY
# ANTHROPIC_API_KEY
# LINEAR_TEAM_ID
# PROJECT_PATH
```

### 3. 啟動 API

```bash
uvicorn main:app --reload
# API 文件：http://localhost:8000/docs
# MCP：http://localhost:8000/mcp
```

### 4. 啟動 Agents（各自獨立執行）

```bash
python agents/hermes_master/hermes_master.py --daemon
python agents/development_agent/development_agent.py --daemon
python agents/reviewer_agent/reviewer_agent.py --daemon
```

---

## 執行紀錄

每次任務執行都會在 `task_runs/` 建立子目錄：

```
task_runs/
  HER-17_20260529_095000/          ← Development Agent
    issue_context.json
    dev_prompt.md
    dev_stdout.log
    git_diff.patch
    dev_result.json

  review_HER-17_20260529_100000/   ← Reviewer Agent
    review_prompt.md
    review_raw.json
    review_result.json

  master_HER-17_20260529_101000/   ← Hermes Master
    master_prompt.md
    master_result.json
```

---

## 專案結構

```
hermes-nexus-api/
├── agents/
│   ├── agent_utils.py                  # 共用：lock / state / fetch
│   ├── development_agent/
│   │   ├── development_agent.py
│   │   └── development_prompt.md       # Dev Agent prompt
│   ├── reviewer_agent/
│   │   ├── reviewer_agent.py
│   │   └── system_prompt.md            # Reviewer prompt
│   └── hermes_master/
│       ├── hermes_master.py
│       ├── dispatch_prompt.md          # 派工判斷 prompt
│       └── escalate_prompt.md          # 升級處理 prompt
├── core/
│   ├── config.py                       # 所有環境變數設定
│   └── logger.py
├── linear/
│   ├── client.py                       # Linear GraphQL client
│   └── router.py                       # FastAPI endpoints
├── memory/                             # Agent 長期規則（人工維護）
├── main.py                             # FastAPI app + MCP
├── requirements.txt
└── .env.example
```
