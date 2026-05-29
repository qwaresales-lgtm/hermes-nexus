#!/usr/bin/env python3
"""
Hermes Nexus — Hermes Master
Monitors agent-ready (initial dispatch) and agent-escalate (re-routing) issues.
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

from agents.agent_utils import acquire_lock, fetch_pending_issues, set_in_progress, set_todo, read_workflow_plan, get_next_label_from_plan
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


# ---------------------------------------------------------------------------
# Claude tools
# ---------------------------------------------------------------------------

DISPATCH_TOOL = {
    "name": "handle_issue",
    "description": "Decide how to handle this Linear issue: respond directly, dispatch to agents, or ask for clarification",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["respond", "dispatch", "clarify"],
                "description": (
                    "respond: 問題或說明請求，由 Hermes Master 直接回覆，不派工；"
                    "dispatch: 明確的工作任務，規劃執行步驟並派工；"
                    "clarify: 無法判斷是問題還是任務，需要補充資訊"
                ),
            },
            "direct_response": {
                "type": "string",
                "description": "action=respond 時的回覆內容（完整的 Markdown 回覆）",
            },
            "clarification_question": {
                "type": "string",
                "description": "action=clarify 時要問的問題",
            },
            "workflow_steps": {
                "type": "array",
                "description": "action=dispatch 時的完整執行計劃，按順序列出每個步驟",
                "items": {
                    "type": "object",
                    "properties": {
                        "order": {"type": "integer"},
                        "label": {
                            "type": "string",
                            "enum": [
                                "agent-dev", "agent-doc", "agent-ppt",
                                "agent-test", "agent-review",
                                "human-confirm", "human-failed",
                            ],
                        },
                        "description": {"type": "string"},
                    },
                    "required": ["order", "label", "description"],
                },
            },
            "summary": {"type": "string", "description": "決策摘要（1-2 句）"},
        },
        "required": ["action", "summary"],
    },
}

ESCALATION_TOOL = {
    "name": "handle_escalation",
    "description": "Decide how to re-route an escalated issue",
    "input_schema": {
        "type": "object",
        "properties": {
            "next_label": {
                "type": "string",
                "enum": ["agent-dev", "agent-test", "agent-review", "human-failed"],
                "description": (
                    "agent-dev: 退回開發重新處理；"
                    "agent-test: 退回測試；"
                    "agent-review: 退回審核；"
                    "human-failed: 無法由 Agent 解決，需人工介入"
                ),
            },
            "summary": {"type": "string", "description": "處理決策摘要（1-2 句）"},
            "reasoning": {"type": "string", "description": "決策理由"},
        },
        "required": ["next_label", "summary", "reasoning"],
    },
}

DISPATCH_SYSTEM_PROMPT = _load_prompt("dispatch_prompt.md")
ESCALATION_SYSTEM_PROMPT = _load_prompt("escalate_prompt.md")


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
            logging.FileHandler("logs/hermes_master.log", encoding="utf-8"),
        ],
    )
    logger.info("Hermes Master starting up")


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------

def call_claude(system_prompt: str, user_prompt: str, tool: dict, config) -> dict:
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = client.messages.create(
        model=config.hermes_master_model,
        max_tokens=1024,
        system=system_prompt,
        tools=[tool],
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": user_prompt}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise ValueError("Claude did not call the expected tool — unexpected response format")


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_issue_prompt(issue: dict) -> str:
    comments = (issue.get("comments") or {}).get("nodes", [])
    comments_text = "\n\n".join(
        f"**{c.get('user', {}).get('name', 'unknown')}** ({c.get('createdAt', '')}):\n{c.get('body', '')}"
        for c in comments
    ) or "(無留言)"
    labels = [l["name"] for l in (issue.get("labels") or {}).get("nodes", [])]
    state = (issue.get("state") or {}).get("name", "unknown")

    return (
        f"## Issue\n\n"
        f"- **Identifier**: {issue.get('identifier')}\n"
        f"- **Title**: {issue.get('title')}\n"
        f"- **Labels**: {', '.join(labels)}\n"
        f"- **State**: {state}\n"
        f"- **Priority**: {issue.get('priority')}\n"
        f"- **URL**: {issue.get('url')}\n\n"
        f"## Description\n\n{issue.get('description') or '(無描述)'}\n\n"
        f"## 留言記錄（時間排序）\n\n{comments_text}"
    )


# ---------------------------------------------------------------------------
# Comment builders
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _footer() -> str:
    return f"\n\n---\n_由 **Hermes Master** 寫入 · {_ts()}_"


def comment_responded(decision: dict) -> str:
    return (
        f"# Hermes Master：回覆\n\n"
        f"{decision['direct_response']}"
        f"{_footer()}"
    )


def comment_clarified(decision: dict) -> str:
    return (
        f"# Hermes Master：需要補充資訊\n\n"
        f"{decision['clarification_question']}\n\n"
        f"補充後請將 label 改回 `agent-ready`，Hermes Master 會重新判斷。"
        f"{_footer()}"
    )


def comment_dispatched(decision: dict) -> str:
    steps = decision.get("workflow_steps", [])
    table_rows = "\n".join(
        f"| {s['order']} | `{s['label']}` | {s['description']} |"
        for s in steps
    )
    steps_table = f"| 步驟 | Label | 說明 |\n|---|---|---|\n{table_rows}"

    plan_json = json.dumps(
        {"version": 1, "steps": [{"order": s["order"], "label": s["label"], "description": s["description"]} for s in steps]},
        ensure_ascii=False,
    )
    first_label = steps[0]["label"] if steps else "agent-dev"

    return (
        f"# Hermes Master：任務派工\n\n"
        f"## 任務分析\n\n{decision['summary']}\n\n"
        f"## 執行計劃\n\n{steps_table}\n\n"
        f"## 理由\n\n{decision['reasoning']}\n\n"
        f"**HERMES_PLAN**\n```json\n{plan_json}\n```\n\n"
        f"## 下一步\n任務已分配至 `{first_label}`，開始執行第一步。"
        f"{_footer()}"
    )


def comment_escalation_handled(decision: dict) -> str:
    return (
        f"# Hermes Master：升級處理\n\n"
        f"## 處理決策\n\n{decision['summary']}\n\n"
        f"## 理由\n\n{decision['reasoning']}\n\n"
        f"## 下一步\n任務已重新分配至 `{decision['next_label']}`。"
        f"{_footer()}"
    )


def comment_error(error: Exception) -> str:
    return (
        f"# Hermes Master：執行失敗\n\n"
        f"## 錯誤摘要\n\n```\n{str(error)[:500]}\n```\n\n"
        f"## 系統處理\n任務已改為 `human-failed`，請人工介入。"
        f"{_footer()}"
    )


# ---------------------------------------------------------------------------
# Issue processor
# ---------------------------------------------------------------------------

def process_issue(issue: dict, client: LinearClient, config, mode: str) -> None:
    """
    mode: "dispatch" for agent-ready / agent-retry, "escalate" for agent-escalate
    """
    identifier = issue["identifier"]
    issue_id = issue["id"]

    logger.info(f"=== Hermes Master [{mode}] {identifier}: {issue.get('title')} ===")

    lock_path = acquire_lock(identifier, prefix="master_")
    if lock_path is None:
        return

    set_in_progress(client, issue_id, identifier, config)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path("task_runs") / f"master_{identifier}_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        (run_dir / "issue_context.json").write_text(
            json.dumps(issue, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

        # Fetch fresh issue with comments for full context
        fresh_issue = client.get_issue(issue_id)
        user_prompt = build_issue_prompt(fresh_issue)
        (run_dir / "master_prompt.md").write_text(user_prompt, encoding="utf-8")
        logger.info(f"[{identifier}] Saved master_prompt.md")

        system_prompt = DISPATCH_SYSTEM_PROMPT if mode == "dispatch" else ESCALATION_SYSTEM_PROMPT
        tool = DISPATCH_TOOL if mode == "dispatch" else ESCALATION_TOOL

        logger.info(f"[{identifier}] Calling {config.hermes_master_model} ({mode})...")
        decision = call_claude(system_prompt, user_prompt, tool, config)

        if mode == "dispatch":
            action = decision.get("action", "dispatch")
            logger.info(f"[{identifier}] Action: {action} — {decision.get('summary', '')}")

            if action == "respond":
                if not decision.get("direct_response"):
                    raise ValueError(
                        "Claude returned action=respond but omitted direct_response. "
                        "Check dispatch_prompt.md to ensure direct_response is required for respond action."
                    )
                client.add_comment(issue_id, comment_responded(decision))
                next_label = config.flow_label_human_confirm
                client.replace_flow_label(issue_id, next_label, config.linear_team_id)
                logger.info(f"[{identifier}] Responded directly → {next_label}")

            elif action == "clarify":
                if not decision.get("clarification_question"):
                    decision["clarification_question"] = decision.get("summary", "請補充更多資訊")
                client.add_comment(issue_id, comment_clarified(decision))
                next_label = config.flow_label_human_clarify
                client.replace_flow_label(issue_id, next_label, config.linear_team_id)
                logger.info(f"[{identifier}] Clarification requested → {next_label}")

            else:  # dispatch
                steps = decision.get("workflow_steps", [])
                next_label = steps[0]["label"] if steps else config.flow_label_dev
                logger.info(f"[{identifier}] Plan: {[s['label'] for s in steps]} — first: {next_label}")
                client.add_comment(issue_id, comment_dispatched(decision))
                client.replace_flow_label(issue_id, next_label, config.linear_team_id)
                logger.info(f"[{identifier}] Dispatched → {next_label}")

        else:  # escalate
            next_label = decision["next_label"]
            logger.info(f"[{identifier}] Re-route → {next_label}")
            client.add_comment(issue_id, comment_escalation_handled(decision))
            client.replace_flow_label(issue_id, next_label, config.linear_team_id)
            logger.info(f"[{identifier}] Label → {next_label}")

        (run_dir / "master_result.json").write_text(
            json.dumps({"agent": "hermes_master", "mode": mode, **decision},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    except Exception as e:
        logger.error(f"[{identifier}] Unexpected error: {e}", exc_info=True)
        (run_dir / "master_error.log").write_text(str(e), encoding="utf-8")
        try:
            client.add_comment(issue_id, comment_error(e))
            client.replace_flow_label(issue_id, config.flow_label_human_failed, config.linear_team_id)
            logger.info(f"[{identifier}] Fallback → {config.flow_label_human_failed}")
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
    dispatched = 0
    escalated = 0

    # --- agent-ready + agent-retry: dispatch / re-dispatch ---
    if not args.escalate_only:
        if args.issue_id:
            ready_issues = [linear_client.get_issue(args.issue_id)]
        elif args.identifier:
            candidates = linear_client.get_issues(label_name=config.linear_ready_label, limit=50)
            ready_issues = [i for i in candidates if i["identifier"] == args.identifier]
            if not ready_issues:
                logger.info(f"Issue {args.identifier} not found in '{config.linear_ready_label}'")
        else:
            ready_issues = fetch_pending_issues(linear_client, config.linear_ready_label, config)
            retry_issues = fetch_pending_issues(linear_client, config.flow_label_agent_retry, config)
            ready_issues = ready_issues + retry_issues

        if ready_issues:
            logger.info(f"dispatch: {len(ready_issues)} issue(s) — {[i['identifier'] for i in ready_issues]}")
            for issue in ready_issues:
                process_issue(issue, linear_client, config, mode="dispatch")
            dispatched = len(ready_issues)
        else:
            logger.info("dispatch: nothing to do.")

    # --- agent-escalate: re-routing ---
    if not args.ready_only:
        if args.issue_id and args.escalate_only:
            escalate_issues = [linear_client.get_issue(args.issue_id)]
        elif args.identifier and args.escalate_only:
            candidates = linear_client.get_issues(label_name=config.flow_label_escalate, limit=50)
            escalate_issues = [i for i in candidates if i["identifier"] == args.identifier]
            if not escalate_issues:
                logger.info(f"Issue {args.identifier} not found in '{config.flow_label_escalate}'")
        else:
            escalate_issues = fetch_pending_issues(linear_client, config.flow_label_escalate, config)

        if escalate_issues:
            logger.info(f"agent-escalate: {len(escalate_issues)} issue(s) — {[i['identifier'] for i in escalate_issues]}")
            for issue in escalate_issues:
                process_issue(issue, linear_client, config, mode="escalate")
            escalated = len(escalate_issues)
        else:
            logger.info("agent-escalate: nothing to handle.")

    if not dispatched and not escalated:
        logger.info("Nothing to do this cycle.")


def main() -> None:
    setup_logging()
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Hermes Nexus Master — dispatches agent-ready and handles agent-escalate issues"
    )
    parser.add_argument("--issue-id", help="Process specific Linear issue ID (UUID)")
    parser.add_argument("--identifier", help="Process by identifier, e.g. HER-5")
    parser.add_argument("--ready-only", action="store_true", help="Only process agent-ready issues")
    parser.add_argument("--escalate-only", action="store_true", help="Only process agent-escalate issues")
    parser.add_argument("--daemon", action="store_true", help="Poll continuously")
    parser.add_argument(
        "--interval", type=int, default=30,
        help="Poll interval in seconds in daemon mode (default: 30)",
    )
    args = parser.parse_args()

    config = get_settings()

    errors = []
    if not config.linear_api_key:
        errors.append("LINEAR_API_KEY is not set")
    if not config.linear_team_id:
        errors.append("LINEAR_TEAM_ID is not set")
    if not config.anthropic_api_key:
        errors.append("ANTHROPIC_API_KEY is not set")
    if errors:
        for e in errors:
            logger.error(e)
        sys.exit(1)

    for d in ["task_runs", "locks", "logs"]:
        Path(d).mkdir(exist_ok=True)

    logger.info(
        f"Config: team_id={config.linear_team_id}, "
        f"model={config.hermes_master_model}, "
        f"ready_label={config.linear_ready_label}, "
        f"escalate_label={config.flow_label_escalate}"
    )

    linear_client = LinearClient()

    if args.daemon:
        logger.info(f"=== Hermes Master daemon started (poll interval: {args.interval}s) ===")
        cycle = 0
        try:
            while True:
                cycle += 1
                logger.info(f"--- Poll cycle #{cycle} ---")
                _run_one_poll_cycle(linear_client, config, args)
                logger.info(f"--- Cycle #{cycle} complete, sleeping {args.interval}s ---")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("Hermes Master daemon stopped (KeyboardInterrupt)")
    else:
        try:
            _run_one_poll_cycle(linear_client, config, args)
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
