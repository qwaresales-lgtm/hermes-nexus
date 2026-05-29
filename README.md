# Hermes Nexus

AI 多代理人任務調動系統，以 Linear label 驅動工作流程。

## 系統概覽

人工在 Linear 貼上 `agent-ready` 後，Hermes Master 分析任務並產生**完整執行計劃**，後續 Agent 依計劃順序執行。

```
人工貼上 agent-ready（status = Todo）
        ↓
  Hermes Master          ← 分析任務，產生執行計劃（步驟序列）
        ↓ 計劃寫入 Linear 留言，設第一步 label
  Development Agent      ← 執行開發，完成後讀計劃決定下一步
        ↓
  Reviewer Agent         ← 審核產出，通過後讀計劃決定下一步
        ↓
  human-confirm          ← 人工執行 commit + merge
```

異常時 Agent 會設成 `agent-escalate`，由 Hermes Master 重新判斷或轉 `human-failed`。

---

## Agent 說明

| Agent | 監聽 Label | 說明 | Prompt |
|---|---|---|---|
| [Hermes Master](#hermes-master) | `agent-ready` `agent-escalate` | 產生執行計劃並派工；處理異常重判 | [派工](agents/hermes_master/dispatch_prompt.md) · [升級處理](agents/hermes_master/escalate_prompt.md) |
| [Development Agent](#development-agent) | `agent-dev` | 執行開發，支援 Claude Code CLI；依計劃決定下一步 | [Prompt](agents/development_agent/development_prompt.md) |
| [Reviewer Agent](#reviewer-agent) | `agent-review` | 審核開發產出；approve 時附 merge 步驟；依計劃決定下一步 | [Prompt](agents/reviewer_agent/system_prompt.md) |

---

## Hermes Master

監聽兩種 label：

- **`agent-ready`**：分析 issue，產生完整執行計劃（步驟序列），寫入 Linear 留言，設第一步 label
- **`agent-escalate`**：讀取失敗留言，重新路由或轉 `human-failed`

**計劃格式（嵌入 Linear 留言中）：**

```
## 執行計劃

| 步驟 | Label          | 說明                    |
|------|----------------|-------------------------|
| 1    | agent-dev      | 實作功能，包含單元測試   |
| 2    | agent-review   | 審核程式碼品質           |
| 3    | human-confirm  | 確認後 merge             |

**HERMES_PLAN**
```json
{"version": 1, "steps": [...]}
```
```

後續 Agent 完成時會讀取 `HERMES_PLAN` 決定下一步，無計劃時 fallback 到 config 預設值。

→ [查看派工 Prompt](agents/hermes_master/dispatch_prompt.md)  
→ [查看升級處理 Prompt](agents/hermes_master/escalate_prompt.md)

```bash
python agents/hermes_master/hermes_master.py
python agents/hermes_master/hermes_master.py --daemon --interval 30
python agents/hermes_master/hermes_master.py --identifier HER-5 --ready-only
python agents/hermes_master/hermes_master.py --identifier HER-5 --escalate-only
```

---

## Development Agent

監聽 `agent-dev` label，根據 Linear issue 描述執行開發。

- 使用 Claude Code CLI backend（可切換 Codex）
- 每個任務建立獨立 git worktree（branch: `agent/HER-xx-task-slug`），支援並行執行
- 需求不足時自動轉 `human-clarify` 並列出缺少資訊
- 完成後讀取 Hermes Master 的執行計劃決定下一步
- 執行紀錄儲存於 `task_runs/`

**支援 per-issue PROJECT_PATH 覆寫：**

在 issue description 加入以下內容，可讓 Dev Agent 在指定目錄工作（不需改 `.env`）：

```
PROJECT_PATH: /home/alan_tseng/hermes-nexus/hermes-nexus-api
```

適合用來讓 Dev Agent 開發 Hermes Nexus 自身的新 Agent。

→ [查看 Prompt](agents/development_agent/development_prompt.md)

```bash
python agents/development_agent/development_agent.py
python agents/development_agent/development_agent.py --daemon --interval 30
python agents/development_agent/development_agent.py --identifier HER-5
```

---

## Reviewer Agent

監聽 `agent-review` label，使用 Claude Sonnet API 審核 Development Agent 的產出。

審核依據：原始需求 + git diff + dev_result.md + issue 留言（含 Hermes Master 計劃）

| 決策 | 條件 | 下一步 |
|---|---|---|
| **approve** | 符合需求、無明顯問題 | 依計劃下一步（預設 `human-confirm`） |
| **reject** | 有具體問題需修正 | `agent-dev`（附具體修改建議） |
| **escalate** | 無法判斷 | `agent-escalate` |

**approve 時，Linear 留言會附上 merge 步驟：**

```bash
# 1. 進入 worktree
cd /tmp/hermes-worktrees/HER-17

# 2. Commit 變更
git add .
git commit -m "her-17-task-slug"

# 3. 回到專案目錄並 merge
cd /path/to/project
git merge agent/her-17-task-slug

# 4. 清除 worktree
git worktree remove --force /tmp/hermes-worktrees/HER-17
```

→ [查看 Prompt](agents/reviewer_agent/system_prompt.md)

```bash
python agents/reviewer_agent/reviewer_agent.py
python agents/reviewer_agent/reviewer_agent.py --daemon --interval 30
python agents/reviewer_agent/reviewer_agent.py --identifier HER-5
```

---

## Label 流程

| Label | 處理者 | 人工需要做什麼 |
|---|---|---|
| `agent-ready` | Hermes Master | — |
| `agent-dev` | Development Agent | — |
| `agent-review` | Reviewer Agent | — |
| `agent-escalate` | Hermes Master | — |
| `human-clarify` | 人工 | 補充需求描述，改回 `agent-ready` |
| `human-confirm` | 人工 | 執行 Reviewer 留言的 commit + merge 步驟 |
| `human-failed` | 人工 | 手動處理，Agent 無法執行 |

**Status 說明：**
- `Todo` = 等待 Agent 接手
- `In Progress` = Agent 正在處理（防止重複執行）
- `Done` = 人工完成後手動設定

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
  master_HER-17_20260529_095000/     ← Hermes Master（含執行計劃）
    master_prompt.md
    master_result.json

  HER-17_20260529_100000/            ← Development Agent
    issue_context.json
    branch_name.txt
    dev_prompt.md
    dev_stdout.log
    git_diff.patch
    dev_result.json

  review_HER-17_20260529_110000/     ← Reviewer Agent
    review_prompt.md
    review_raw.json
    review_result.json
```

---

## 專案結構

```
hermes-nexus-api/
├── agents/
│   ├── agent_utils.py                  # 共用：lock / state / fetch / workflow plan
│   ├── development_agent/
│   │   ├── development_agent.py
│   │   └── development_prompt.md       # Dev Agent prompt
│   ├── reviewer_agent/
│   │   ├── reviewer_agent.py
│   │   └── system_prompt.md            # Reviewer prompt
│   └── hermes_master/
│       ├── hermes_master.py
│       ├── dispatch_prompt.md          # 派工與計劃設計 prompt
│       └── escalate_prompt.md          # 升級處理 prompt
├── core/
│   ├── config.py                       # 所有環境變數設定
│   └── logger.py
├── linear/
│   ├── client.py                       # Linear GraphQL client
│   └── router.py                       # FastAPI endpoints
├── memory/                             # 系統規範文件（人工維護）
│   ├── project_context.md
│   ├── linear_label_rules.md
│   ├── linear_states.md
│   ├── development_agent_rules.md
│   └── memory_policy.md
├── main.py                             # FastAPI app + MCP
├── requirements.txt
└── .env.example
```
