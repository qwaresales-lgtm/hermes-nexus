PYTHON := venv/bin/python

.PHONY: help install api master dev doc ppt reviewer

help:
	@echo "Hermes Nexus — 常用指令"
	@echo ""
	@echo "  make install     安裝依賴到 venv"
	@echo "  make api         啟動 FastAPI"
	@echo "  make master      啟動 Hermes Master (daemon)"
	@echo "  make dev         啟動 Development Agent (daemon)"
	@echo "  make doc         啟動 Document Agent (daemon)"
	@echo "  make ppt         啟動 Presentation Agent (daemon)"
	@echo "  make reviewer    啟動 Reviewer Agent (daemon)"
	@echo ""
	@echo "單次執行（指定 issue）："
	@echo "  make master ID=HER-5"
	@echo "  make dev    ID=HER-5"

install:
	python3 -m venv venv
	venv/bin/pip install -r requirements.txt

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
