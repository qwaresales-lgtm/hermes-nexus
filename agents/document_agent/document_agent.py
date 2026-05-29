#!/usr/bin/env python3
"""
Hermes Nexus — Document Agent
Monitors agent-doc issues and generates Markdown documents using Claude API.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

import anthropic

from agents.agent_utils import (
    acquire_lock,
    extract_project_path_override,
    fetch_pending_issues,
    get_next_label_from_plan,
    read_workflow_plan,
    set_in_progress,
    set_todo,
)
from core.config import get_settings
from linear.client import LinearClient

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent


def _load_prompt(filename: str) -> str:
    path = _PROMPT_DIR / filename
    if not path.exists():
        logger.warning(f"Prompt file not found: {path}")
        return ""
    return path.read_text(encoding="utf-8")


SYSTEM_PROMPT = _load_prompt("system_prompt.md")

DOCUMENT_TOOL = {
    "name": "create_document",
    "description": "產生完整的 Markdown 文件並指定儲存路徑",
    "input_schema": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "相對於 PROJECT_PATH 的儲存路徑，例如 api_reference.md 或 docs/guide.md",
            },
            "content": {
                "type": "string",
                "description": "完整的 Markdown 文件內容",
            },
            "summary": {
                "type": "string",
                "description": "文件摘要（1-3 句），用於 Linear 留言",
            },
        },
        "required": ["filename", "content", "summary"],
    },
}


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    Path("logs").mkdir(exist_ok=True)
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/document_agent.log", encoding="utf-8"),
        ],
    )
    logger.info("Document Agent starting up")


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------

def call_claude(user_prompt: str, config) -> dict:
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = client.messages.create(
        model=config.document_model,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        tools=[DOCUMENT_TOOL],
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": user_prompt}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "create_document":
            return block.input
    raise ValueError("Claude did not call create_document tool")


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_user_prompt(issue: dict) -> str:
    comments = (issue.get("comments") or {}).get("nodes", [])
    comments_text = "\n\n".join(
        f"**{c.get('user', {}).get('name', 'unknown')}** ({c.get('createdAt', '')}):\n{c.get('body', '')}"
        for c in comments
    ) or "(無留言)"
    return (
        f"## 任務\n\n"
        f"- **Issue**: {issue.get('identifier')} — {issue.get('title')}\n"
        f"- **URL**: {issue.get('url')}\n\n"
        f"## 需求描述\n\n{issue.get('description') or '(無描述)'}\n\n"
        f"## 留言記錄\n\n{comments_text}"
    )


# ---------------------------------------------------------------------------
# Comment builders
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _footer() -> str:
    return f"\n\n---\n_由 **Document Agent** 寫入 · {_ts()}_"


def comment_success(result: dict, saved_path: str, next_label: str) -> str:
    return (
        f"# Document Agent：文件產生完成\n\n"
        f"## 文件摘要\n\n{result['summary']}\n\n"
        f"## 產出檔案\n\n`{saved_path}`\n\n"
        f"## 下一步\n任務已轉移至 `{next_label}`。"
        f"{_footer()}"
    )


def comment_error(error: Exception) -> str:
    return (
        f"# Document Agent：執行失敗，已升級給 Hermes Master\n\n"
        f"## 錯誤摘要\n\n```\n{str(error)[:500]}\n```\n\n"
        f"## 系統處理\n任務已改為 `agent-escalate`。"
        f"{_footer()}"
    )


# ---------------------------------------------------------------------------
# Issue processor
# ---------------------------------------------------------------------------

def process_issue(issue: dict, client: LinearClient, config) -> None:
    identifier = issue["identifier"]
    issue_id = issue["id"]

    logger.info(f"=== Document Agent {identifier}: {issue.get('title')} ===")

    lock_path = acquire_lock(identifier, prefix="doc_")
    if lock_path is None:
        return

    set_in_progress(client, issue_id, identifier, config)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path("task_runs") / f"doc_{identifier}_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        (run_dir / "issue_context.json").write_text(
            json.dumps(issue, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

        # Resolve project path
        path_override = extract_project_path_override(issue.get("description") or "")
        project_path = Path(path_override) if path_override else Path(config.project_path)
        if not project_path.exists():
            raise FileNotFoundError(f"PROJECT_PATH does not exist: {project_path}")
        if path_override:
            logger.info(f"[{identifier}] PROJECT_PATH override: {project_path}")

        # Fetch fresh issue with comments
        fresh_issue = client.get_issue(issue_id)
        user_prompt = build_user_prompt(fresh_issue)
        (run_dir / "doc_prompt.md").write_text(user_prompt, encoding="utf-8")

        logger.info(f"[{identifier}] Calling {config.document_model} to generate document...")
        result = call_claude(user_prompt, config)
        logger.info(f"[{identifier}] Document generated: {result['filename']} ({len(result['content'])} chars)")

        # Save document to project path
        output_path = project_path / result["filename"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result["content"], encoding="utf-8")
        logger.info(f"[{identifier}] Saved to {output_path}")

        (run_dir / "doc_result.json").write_text(
            json.dumps({
                "agent": "document_agent",
                "status": "completed",
                "filename": result["filename"],
                "saved_path": str(output_path),
                "summary": result["summary"],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Determine next label from workflow plan
        comments = (fresh_issue.get("comments") or {}).get("nodes", [])
        plan = read_workflow_plan(comments)
        next_label = get_next_label_from_plan(plan, config.flow_label_doc, config.document_next_label)

        client.add_comment(issue_id, comment_success(result, str(output_path), next_label))
        client.replace_flow_label(issue_id, next_label, config.linear_team_id)
        logger.info(f"[{identifier}] SUCCESS → {next_label}")

    except Exception as e:
        logger.error(f"[{identifier}] Unexpected error: {e}", exc_info=True)
        (run_dir / "doc_error.log").write_text(str(e), encoding="utf-8")
        try:
            client.add_comment(issue_id, comment_error(e))
            client.replace_flow_label(issue_id, config.flow_label_escalate, config.linear_team_id)
        except Exception as e2:
            logger.error(f"[{identifier}] Failed to update Linear after error: {e2}")
    finally:
        set_todo(client, issue_id, identifier, config)
        lock_path.unlink(missing_ok=True)
        logger.info(f"[{identifier}] Lock released")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _run_one_poll_cycle(linear_client: LinearClient, config, args) -> None:
    if args.issue_id:
        issues = [linear_client.get_issue(args.issue_id)]
    elif args.identifier:
        candidates = linear_client.get_issues(label_name=config.flow_label_doc, limit=50)
        issues = [i for i in candidates if i["identifier"] == args.identifier]
        if not issues:
            logger.error(f"Issue {args.identifier} with label '{config.flow_label_doc}' not found")
            return
    else:
        issues = fetch_pending_issues(linear_client, config.flow_label_doc, config)

    if not issues:
        logger.info(f"No issues with label '{config.flow_label_doc}' found.")
        return

    logger.info(f"Found {len(issues)} issue(s): {[i['identifier'] for i in issues]}")
    for issue in issues:
        process_issue(issue, linear_client, config)


def main() -> None:
    setup_logging()
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Hermes Nexus Document Agent — generates Markdown documents from agent-doc issues"
    )
    parser.add_argument("--issue-id", help="Process specific Linear issue ID (UUID)")
    parser.add_argument("--identifier", help="Process by identifier, e.g. HER-5")
    parser.add_argument("--daemon", action="store_true", help="Poll continuously")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default: 30)")
    args = parser.parse_args()

    config = get_settings()

    errors = []
    if not config.linear_api_key:
        errors.append("LINEAR_API_KEY is not set")
    if not config.linear_team_id:
        errors.append("LINEAR_TEAM_ID is not set")
    if not config.anthropic_api_key:
        errors.append("ANTHROPIC_API_KEY is not set")
    if not config.project_path:
        errors.append("PROJECT_PATH is not set")
    if errors:
        for e in errors:
            logger.error(e)
        sys.exit(1)

    for d in ["task_runs", "locks", "logs"]:
        Path(d).mkdir(exist_ok=True)

    logger.info(
        f"Config: team_id={config.linear_team_id}, "
        f"model={config.document_model}, "
        f"watch_label={config.flow_label_doc}, "
        f"project_path={config.project_path}"
    )

    linear_client = LinearClient()

    if args.daemon:
        logger.info(f"=== Document Agent daemon started (poll interval: {args.interval}s) ===")
        cycle = 0
        try:
            while True:
                cycle += 1
                logger.info(f"--- Poll cycle #{cycle} ---")
                _run_one_poll_cycle(linear_client, config, args)
                logger.info(f"--- Cycle #{cycle} complete, sleeping {args.interval}s ---")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("Document Agent daemon stopped (KeyboardInterrupt)")
    else:
        try:
            _run_one_poll_cycle(linear_client, config, args)
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
