# Linear Label Rules

## 前綴規則

| 前綴 | 意義 |
|---|---|
| `agent-` | 目前在 Agent 流程中，機器在處理 |
| `human-` | 需要人工介入，機器停手 |

## 流程 Label

| Label | 監聽者 | 意義 |
|---|---|---|
| `agent-ready` | Hermes Master | 人工確認，等待初次派工 |
| `agent-dev` | Development Agent | 開發任務進行中 |
| `agent-test` | Test Agent | 測試任務進行中 |
| `agent-review` | Reviewer Agent | 審核進行中 |
| `agent-escalate` | Hermes Master | 非預期情況，需重新判斷 |
| `human-confirm` | 人工 | Agent 完成，等待人工確認 |
| `human-failed` | 人工 | 確認失敗，需人工介入 |

## 使用規則

- 每個 issue 同時只應有一個流程 label
- label ID 必須透過 API 動態取得，不要寫死
- 只有人工可以貼 `agent-ready`
