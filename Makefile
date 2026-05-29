PYTHON := venv/bin/python

.PHONY: help install api master dev doc ppt reviewer start stop status restart

help:
	@echo "Hermes Nexus — 常用指令"
	@echo ""
	@echo "排程管理："
	@echo "  make start       啟動所有 Agent（背景執行）"
	@echo "  make stop        停止所有 Agent"
	@echo "  make status      查看各 Agent 狀態"
	@echo "  make restart     重啟所有 Agent"
	@echo ""
	@echo "單獨啟動（daemon）："
	@echo "  make master / dev / doc / ppt / reviewer"
	@echo ""
	@echo "單次執行（指定 issue）："
	@echo "  make master ID=HER-5"
	@echo "  make dev    ID=HER-5"
	@echo ""
	@echo "其他："
	@echo "  make install              安裝依賴到 venv"
	@echo "  make api                  啟動 FastAPI"
	@echo "  make install-notebooklm   安裝 NotebookLM 套件"
	@echo "  make notebooklm-login     NotebookLM Google 登入"
	@echo "  make notebooklm-check     確認 NotebookLM 登入狀態"

start:
	./scheduler.sh start

stop:
	./scheduler.sh stop

status:
	./scheduler.sh status

restart:
	./scheduler.sh restart

install:
	python3 -m venv venv
	venv/bin/pip install -r requirements.txt

install-notebooklm:
	venv/bin/pip install "notebooklm-py[browser]"
	venv/bin/playwright install chromium
	@echo "Done. Run: make notebooklm-login"

notebooklm-login:
	venv/bin/notebooklm login

notebooklm-check:
	venv/bin/notebooklm auth check --test --json

api:
	$(PYTHON) -m uvicorn main:app --reload

master:
ifdef ID
	$(PYTHON) agents/hermes_master/hermes_master.py --identifier $(ID)
else
	$(PYTHON) agents/hermes_master/hermes_master.py --daemon --interval 30
endif

dev:
ifdef ID
	$(PYTHON) agents/development_agent/development_agent.py --identifier $(ID)
else
	$(PYTHON) agents/development_agent/development_agent.py --daemon --interval 30
endif

doc:
ifdef ID
	$(PYTHON) agents/document_agent/document_agent.py --identifier $(ID)
else
	$(PYTHON) agents/document_agent/document_agent.py --daemon --interval 30
endif

ppt:
ifdef ID
	$(PYTHON) agents/presentation_agent/presentation_agent.py --identifier $(ID)
else
	$(PYTHON) agents/presentation_agent/presentation_agent.py --daemon --interval 60
endif

reviewer:
ifdef ID
	$(PYTHON) agents/reviewer_agent/reviewer_agent.py --identifier $(ID)
else
	$(PYTHON) agents/reviewer_agent/reviewer_agent.py --daemon --interval 30
endif
