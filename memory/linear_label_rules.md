# Linear Label 定義與使用規則

## Label 前綴規則

| 前綴 | 意義 |
|---|---|
| `agent-` | 目前在 Agent 流程中，機器在處理 |
| `human-` | 需要人工介入，機器停手 |

## 完整 Label 一覽

| Label | 監聽者 | 意義 |
|---|---|---|
| `agent-ready` | Hermes Master | 人工確認，等待初次派工 |
| `agent-dev` | Development Agent | 開發任務進行中 |
| `agent-test` | Test Agent | 測試任務進行中 |
| `agent-review` | Reviewer Agent | 審核進行中 |
| `agent-escalate` | Hermes Master | 非預期情況，需重新判斷 |
| `human-clarify` | 人工 | 需求不足，請補充描述後改回 `agent-ready` |
| `human-confirm` | 人工 | Reviewer 通過，請執行 commit + merge |
| `human-failed` | 人工 | Agent 無法處理，需人工介入 |

## 標準流程

```
人工貼上 agent-ready
  ↓ Hermes Master 判斷
agent-dev
  ↓ Development Agent 完成
agent-review
  ↓ Reviewer Agent 通過
human-confirm（人工 commit + merge）
  ↓ 人工完成
Done
```

## 例外流程

```
需求描述不足
  ↓ Development Agent
human-clarify（人工補充需求，改回 agent-ready）

任何 Agent 遇到非預期情況
  ↓
agent-escalate（Hermes Master 重新判斷）
  ↓ 確認無法處理
human-failed（人工介入）
```

## 使用規則

1. 每個 issue 同時只應有一個 `agent-*` 或 `human-*` 流程 label
2. `Feature` / `Bug` / `Improvement` 等類型 label 可與流程 label 同時存在
3. Label ID 必須透過 API 動態取得，不可寫死
4. 只有人工可以貼 `agent-ready`，Agent 不得自行貼上
