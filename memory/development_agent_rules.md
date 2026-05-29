# Development Agent Rules

## 核心限制

- 不自動 git commit / push / deploy
- 不修改 .env、金鑰、token、password
- 不刪除資料
- 不操作正式資料庫
- 需求不足時停止，不猜測

## 需求完整性標準

需求足夠的條件：
- description 不為空
- 有明確動作動詞（建立、修改、產生、串接…）
- 有明確目標（檔案、功能、API、文件…）
- 無高風險操作，或高風險操作有明確授權

## 交付原則

- 根據任務性質選擇最合適的交付形式
- 優先最小必要修改
- 修改後執行基本語法或啟動確認

## Label 流程

| 情境 | 下一個 Label | 人工要做什麼 |
|---|---|---|
| 開發完成 | `agent-review` | 等 Reviewer 審核 |
| 需求不足 | `human-clarify` | 補充需求後改回 `agent-ready` |
| 執行失敗 | `agent-escalate` | 等 Hermes Master 重新判斷 |

## 注意

`human-clarify` 和 `human-confirm` 是不同的 label：
- `human-clarify`：需要人工補充需求說明
- `human-confirm`：Reviewer 通過，需要人工 commit + merge
