# Memory Policy

## 第一版原則

1. Agent 不可自行修改 memory 檔案。
2. Agent 可以在 task_runs 中產出 memory_suggestion.md。
3. 只有人工確認後，才可把建議加入 memory。
4. 任務執行紀錄放在 task_runs。
5. memory 只保存長期規則，不保存單次任務細節。

## 可以記住

- 長期有效的流程規則
- 專案固定架構
- 常見錯誤處理方式
- 常見需求不足模式
- Agent 的安全限制

## 不應記住

- 單次任務的臨時資訊
- 沒確認過的推測
- 錯誤結果
- API key / token / password
- 個人敏感資料
