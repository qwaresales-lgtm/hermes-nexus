# Linear 狀態定義與使用規則

## 狀態一覽

| 狀態 | 意義 | 觸發條件 |
|---|---|---|
| **Backlog** | Issue 建立，尚未規劃 | 初始建立時預設 |
| **Todo** | 等待 Agent 接手 | 人工確認後手動設定；或 Agent 完成後自動重設 |
| **In Progress** | Agent 正在處理此任務 | Agent 接手時透過 API 自動設定 |
| **Done** | 任務完成 | 人工確認 merge 後手動設定 |
| **Canceled** | 任務取消 | 人工取消時設定 |

## Agent 如何使用 Status

Status 是系統防止重複執行的核心機制：

```
人工設 Todo + agent-ready
  ↓ Agent 接手
自動設 In Progress（其他排程不會再抓）
  ↓ Agent 完成
自動設回 Todo + 換 label（讓下一個 Agent 接手）
  ↓ 最後進入 human-confirm
人工 merge 後手動設 Done
```

## 各角色的操作範圍

| 角色 | Status 操作 |
|---|---|
| 人工 | 全部 |
| Hermes Master | In Progress → Todo |
| Development Agent | In Progress → Todo |
| Reviewer Agent | In Progress → Todo |

> Agent 只會在任務開始時設 **In Progress**，結束時設回 **Todo**。
> Done 永遠由人工設定。

## 注意事項

- Agent 只抓 `label = 目標 label` **且** `status = Todo` 的 issue
- Status `In Progress` 代表正在處理中，其他排程看到會跳過，防止重複執行
- 若 Agent 崩潰，issue 會卡在 `In Progress`，需人工改回 `Todo` 才能繼續
- Status ID 請透過 `GET /linear/states?team_id=` 動態取得，不可寫死
