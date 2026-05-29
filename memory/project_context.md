# Project Context

## 系統名稱

Hermes Nexus — AI 多代理人任務調動系統

## 架構概述

```
人工貼上 agent-ready（Linear issue status = Todo）
  ↓
Hermes Master（派工判斷 / 升級重判）
  ↓
Development Agent（監聽 agent-dev）
  → 建立 git worktree，在獨立 branch 開發
  → 不 commit，執行完成後存 git_diff.patch
  ↓
Reviewer Agent（監聽 agent-review）
  → 讀取 git_diff.patch + dev_result.md 審核
  → approve：留言含 merge 步驟 → human-confirm
  → reject：退回 agent-dev
  ↓
人工執行 commit + merge → 關閉 issue
```

## Label 定義

| Label | 處理者 | 人工需要做什麼 |
|---|---|---|
| `agent-ready` | Hermes Master | — |
| `agent-dev` | Development Agent | — |
| `agent-review` | Reviewer Agent | — |
| `agent-escalate` | Hermes Master | — |
| `human-clarify` | 人工 | 補充需求描述，改回 `agent-ready` |
| `human-confirm` | 人工 | 執行 Reviewer 留言的 merge 步驟 |
| `human-failed` | 人工 | 手動處理，Agent 無法執行 |

## Git Worktree 機制

- 每個任務在 `/tmp/hermes-worktrees/{identifier}/` 建立獨立工作目錄
- Branch 命名：`agent/{identifier-lower}-{task-slug}`
- Dev Agent 不 commit，由人工在 `human-confirm` 階段手動 commit + merge
- Reviewer approve 留言會附上完整的 commit + merge 指令
- 多個任務可以同時並行，互不干擾

## API

Hermes Nexus API 運行於 `hermes-nexus-api/`，提供 Linear CRUD 操作，並透過 fastapi-mcp 自動對外提供 MCP tools（連線位置：/mcp）。

## 注意事項

此檔案由人工維護，記錄長期有效的專案背景資訊。
