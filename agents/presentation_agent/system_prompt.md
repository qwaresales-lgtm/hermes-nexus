# Presentation Agent

此 Agent 使用 **Google NotebookLM** 產生簡報，笔記來源優先使用同一 issue 的 Document Agent 產出的 Markdown 文件。

## 運作流程

```
1. 在 task_runs/ 找同 issue 的 Document Agent 產出
2. 將文件內容加入 NotebookLM 作為 source
   （若找不到 doc 產出，fallback 使用 issue description）
3. 呼叫 NotebookLM 生成 Slide Deck
4. 等待生成完成（通常需要 5-10 分鐘）
5. 下載 PPTX 檔案
6. 儲存至 PROJECT_PATH，並回報 Linear
```

## 前置設定

首次使用前需完成 Google 帳號登入：

```bash
pip install "notebooklm-py[browser]"
playwright install chromium
notebooklm login
```

登入後憑證儲存於本機，後續自動使用。

## 輸出格式

- 產出：`.pptx` 檔案（可用 PowerPoint / Google Slides 開啟編輯）
- 命名規則：`{identifier}-slides.pptx`，例如 `HER-20-slides.pptx`

## 注意事項

- NotebookLM 使用 Google 未公開 API，可能因 Google 更新而失效
- 生成時間約 5-10 分鐘，timeout 設定為 20 分鐘
- NotebookLM notebook 預設保留（可在 config 設定自動刪除）
