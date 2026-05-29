#!/usr/bin/env python3
"""
Hermes Nexus — Reviewer Agent
Monitors Linear issues labeled 'agent-review' and reviews Development Agent output.
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

from agents.agent_utils import acquire_lock, fetch_pending_issues, set_in_progress, set_todo
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

REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Submit the structured code review decision",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["approve", "reject", "escalate"],
                "description": "approve=通過, reject=退回修改, escalate=無法判斷",
            },
            "summary": {"type": "string", "description": "審核摘要（1-3 句）"},
            "issues_found": {
                "type": "array",
                "items": {"type": "string"},
                "description": "發現的問題清單",
            },
            "suggestions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "具體修改建議（reject 時必填）",
            },
            "reasoning": {"type": "string", "description": "決策理由"},
        },
        "required": ["decision", "summary", "reasoning"],
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
            logging.FileHandler("logs/reviewer_agent.log", encoding="utf-8"),
        ],
    )
    logger.info("Reviewer Agent starting up")


def load_memory_files(memory_dir: Path) -> dict:
    memory = {}
    for name in ["development_agent_rules", "linear_label_rules", "project_context"]:
        path = memory_dir / f"{name}.md"
        if path.exists():
            memory[name] = path.read_text(encoding="utf-8")
        else:
            memory[name] = ""
            logger.warning(f"Memory file not found (skipping): {path}")
    return memory


# ---------------------------------------------------------------------------
# Find latest dev run
# ---------------------------------------------------------------------------

def find_latest_dev_run(identifier: str) -> Path | None:
    """Return the most recent completed Development Agent task_run for this identifier."""
    task_runs = Path("task_runs")
    if not task_runs.exists():
        return None

    candidates = sorted(
        [
            d for d in task_runs.iterdir()
            if d.is_dir()
            and d.name.startswith(f"{identifier}_")
            and not d.name.startswith(f"review_{identifier}_")
        ],
        reverse=True,
    )

    for candidate in candidates:
        result_json = candidate / "dev_result.json"
        if not result_json.exists():
            continue
        try:
            data = json.loads(result_json.read_text(encoding="utf-8"))
            if data.get("agent") == "development_agent" and data.get("status") == "completed":
                return candidate
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------

def call_claude_review(user_prompt: str, config) -> dict:
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = client.messages.create(
        model=config.reviewer_model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[REVIEW_TOOL],
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": user_prompt}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_review":
            return block.input
    raise ValueError("Claude did not call submit_review tool — unexpected response format")


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_user_prompt(issue: dict, dev_run_dir: Path | None) -> str:
    git_diff = ""
    dev_result_md = ""
    dev_stdout = ""

    if dev_run_dir:
        diff_path = dev_run_dir / "git_diff.patch"
        if diff_path.exists():
            git_diff = diff_path.read_text(encoding="utf-8")

        result_path = dev_run_dir / "dev_result.md"
        if result_path.exists():
            dev_result_md = result_path.read_text(encoding="utf-8")

        stdout_path = dev_run_dir / "dev_stdout.log"
        if stdout_path.exists():
            dev_stdout = stdout_path.read_text(encoding="utf-8")[:3000]

    comments = (issue.get("comments") or {}).get("nodes", [])
    comments_text = "\n\n".join(
        f"**{c.get('user', {}).get('name', 'unknown')}** ({c.get('createdAt', '')}):\n{c.get('body', '')}"
        for c in comments
    ) or "(無留言)"

    diff_display = (
        git_diff[:5000] + "\n...(截斷，僅顯示前 5000 字)"
        if len(git_diff) > 5000
        else git_diff or "(無 git diff — 可能為文件或設定檔交付)"
    )

    return f"""## 原始需求

- **Issue**: {issue.get('identifier')} — {issue.get('title')}
- **URL**: {issue.get('url')}
- **Description**:
{issue.get('description') or '(無描述)'}

## Issue 留言記錄

{comments_text}

## Development Agent 自述（dev_result.md）

{dev_result_md or '(找不到 dev_result.md)'}

## Development Agent 執行輸出（前 3000 字）

{dev_stdout or '(找不到 dev_stdout.log)'}

## Git Diff

```diff
{diff_display}
```

---

請審核以上內容，並呼叫 submit_review 回傳你的決策。"""


# ---------------------------------------------------------------------------
# Comment builders
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _footer() -> str:
    return f"\n\n---\n_由 **Reviewer Agent** 寫入 · {_ts()}_"


def comment_approved(review: dict, next_label: str) -> str:
    notes = "\n".join(f"- {i}" for i in review.get("issues_found", [])) or "- 無"
    return (
        f"# Reviewer Agent：審核通過\n\n"
        f"## 審核摘要\n\n{review['summary']}\n\n"
        f"## 備註\n\n{notes}\n\n"
        f"## 下一步\n任務已轉移至 `{next_label}`，請人工確認並合併。"
        f"{_footer()}"
    )


def comment_rejected(review: dict) -> str:
    issues_list = "\n".join(f"- {i}" for i in review.get("issues_found", [])) or "- (未列出)"
    suggestions_list = (
        "\n".join(f"{i + 1}. {s}" for i, s in enumerate(review.get("suggestions", [])))
        or "請參考審核摘要"
    )
    return (
        f"# Reviewer Agent：需要修改\n\n"
        f"## 審核摘要\n\n{review['summary']}\n\n"
        f"## 發現問題\n\n{issues_list}\n\n"
        f"## 修改建議\n\n{suggestions_list}\n\n"
        f"## 下一步\n任務已退回至 `agent-dev`，請 Development Agent 依照建議重新處理。"
        f"{_footer()}"
    )


def comment_escalated(review: dict) -> str:
    return (
        f"# Reviewer Agent：無法判斷，升級給 Hermes Master\n\n"
        f"## 說明\n\n{review['summary']}\n\n"
        f"## 理由\n\n{review['reasoning']}\n\n"
        f"## 系統處理\n任務已改為 `agent-escalate`，等待 Hermes Master 重新判斷。"
        f"{_footer()}"
    )


def comment_error(error: Exception) -> str:
    return (
        f"# Reviewer Agent：執行失敗，已升級給 Hermes Master\n\n"
        f"## 錯誤摘要\n\n```\n{str(error)[:500]}\n```\n\n"
        f"## 系統處理\n任務已改為 `agent-escalate`，等待 Hermes Master 重新判斷。"
        f"{_footer()}"
    )


# ---------------------------------------------------------------------------
# Result JSON
# ---------------------------------------------------------------------------

def _save_result_json(
    run_dir: Path,
    status: str,
    decision: str,
    summary: str,
    next_label: str,
    review_data: dict,
    requires_human: bool = False,
) -> None:
    data = {
        "agent": "reviewer_agent",
        "status": status,
        "decision": decision,
        "summary": summary,
        "next_label": next_label,
        "review": review_data,
        "requires_human_confirmation": requires_human,
    }
    (run_dir / "review_result.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Issue processor
# ---------------------------------------------------------------------------

def process_issue(issue: dict, client: LinearClient, config, memory: dict) -> None:
    identifier = issue["identifier"]
    issue_id = issue["id"]

    logger.info(f"=== Reviewing {identifier}: {issue.get('title')} ===")

    lock_path = acquire_lock(identifier, prefix="review_")
    if lock_path is None:
        return

    set_in_progress(client, issue_id, identifier, config)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path("task_runs") / f"review_{identifier}_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"[{identifier}] Review run directory: {run_dir}")

    try:
        (run_dir / "issue_context.json").write_text(
            json.dumps(issue, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

        dev_run_dir = find_latest_dev_run(identifier)
        if dev_run_dir:
            logger.info(f"[{identifier}] Found dev run: {dev_run_dir.name}")
        else:
            logger.warning(f"[{identifier}] No completed dev run found — reviewing from issue only")

        # Fetch fresh issue data (includes comments)
        fresh_issue = client.get_issue(issue_id)

        user_prompt = build_user_prompt(fresh_issue, dev_run_dir)
        (run_dir / "review_prompt.md").write_text(
            f"## SYSTEM\n\n{SYSTEM_PROMPT}\n\n---\n\n## USER\n\n{user_prompt}",
            encoding="utf-8",
        )
        logger.info(f"[{identifier}] Saved review_prompt.md")

        logger.info(f"[{identifier}] Calling {config.reviewer_model} for review...")
        review = call_claude_review(user_prompt, config)
        logger.info(f"[{identifier}] Decision: {review['decision']}")

        (run_dir / "review_raw.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        decision = review["decision"]

        if decision == "approve":
            next_label = config.reviewer_approved_label
            _save_result_json(run_dir, "approved", decision, review["summary"], next_label, review, requires_human=True)
            client.add_comment(issue_id, comment_approved(review, next_label))
            client.replace_flow_label(issue_id, next_label, config.linear_team_id)
            logger.info(f"[{identifier}] APPROVED → {next_label}")

        elif decision == "reject":
            next_label = config.reviewer_rejected_label
            _save_result_json(run_dir, "rejected", decision, review["summary"], next_label, review)
            client.add_comment(issue_id, comment_rejected(review))
            client.replace_flow_label(issue_id, next_label, config.linear_team_id)
            logger.info(f"[{identifier}] REJECTED → {next_label}")

        else:  # escalate
            next_label = config.flow_label_escalate
            _save_result_json(run_dir, "escalated", decision, review["summary"], next_label, review)
            client.add_comment(issue_id, comment_escalated(review))
            client.replace_flow_label(issue_id, next_label, config.linear_team_id)
            logger.info(f"[{identifier}] ESCALATED → {next_label}")

    except Exception as e:
        logger.error(f"[{identifier}] Unexpected error: {e}", exc_info=True)
        (run_dir / "review_error.log").write_text(str(e), encoding="utf-8")
        try:
            client.add_comment(issue_id, comment_error(e))
            client.replace_flow_label(issue_id, config.flow_label_escalate, config.linear_team_id)
            logger.info(f"[{identifier}] Error escalated → {config.flow_label_escalate}")
        except Exception as e2:
            logger.error(f"[{identifier}] Failed to update Linear after error: {e2}")
    finally:
        set_todo(client, issue_id, identifier, config)
        lock_path.unlink(missing_ok=True)
        logger.info(f"[{identifier}] Review lock released")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _run_one_poll_cycle(linear_client: LinearClient, config, memory: dict, args) -> None:
    if args.issue_id:
        issues = [linear_client.get_issue(args.issue_id)]
    elif args.identifier:
        candidates = linear_client.get_issues(label_name=config.flow_label_review, limit=50)
        issues = [i for i in candidates if i["identifier"] == args.identifier]
        if not issues:
            logger.error(f"Issue {args.identifier} with label '{config.flow_label_review}' not found")
            return
    else:
        issues = fetch_pending_issues(linear_client, config.flow_label_review, config)

    if not issues:
        logger.info(f"No issues with label '{config.flow_label_review}' found.")
        return

    logger.info(f"Found {len(issues)} issue(s) to review: {[i['identifier'] for i in issues]}")
    for issue in issues:
        process_issue(issue, linear_client, config, memory)


def main() -> None:
    setup_logging()
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Hermes Nexus Reviewer Agent — reviews agent-review Linear issues"
    )
    parser.add_argument("--issue-id", help="Review specific Linear issue ID (UUID)")
    parser.add_argument("--identifier", help="Review by identifier, e.g. HER-5")
    parser.add_argument("--daemon", action="store_true", help="Poll continuously")
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Poll interval in seconds when in daemon mode (default: 30)",
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
        f"model={config.reviewer_model}, "
        f"watch_label={config.flow_label_review}, "
        f"approved_label={config.reviewer_approved_label}, "
        f"rejected_label={config.reviewer_rejected_label}"
    )

    linear_client = LinearClient()
    memory = load_memory_files(Path("memory"))

    if args.daemon:
        logger.info(f"=== Reviewer daemon started (poll interval: {args.interval}s) ===")
        cycle = 0
        try:
            while True:
                cycle += 1
                logger.info(f"--- Poll cycle #{cycle} ---")
                _run_one_poll_cycle(linear_client, config, memory, args)
                logger.info(f"--- Cycle #{cycle} complete, sleeping {args.interval}s ---")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("Reviewer daemon stopped (KeyboardInterrupt)")
    else:
        try:
            _run_one_poll_cycle(linear_client, config, memory, args)
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
