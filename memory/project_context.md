# Project Context

## 系統名稱

Hermes Nexus — AI 多代理人任務調動系統

## 架構概述

```
Linear（任務來源）
  ↓ agent-ready label
Hermes Master（派工判斷）
  ↓ 換 label
子 Agent（Development / Test / Reviewer）
  ↓ 換 label
human-confirm / human-failed（人工確認）
```

## API

Hermes Nexus API 運行於 `hermes-nexus-api/`，提供 Linear CRUD 操作。

## 注意事項

此檔案由人工維護，記錄長期有效的專案背景資訊。
