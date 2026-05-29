"""
Shared utilities for all Hermes Nexus agents.

All agents must follow this protocol:
  1. Fetch only issues with the target label AND status = Todo
  2. Acquire a PID-aware lock before processing
  3. Set status to In Progress immediately after acquiring the lock
  4. Reset status to Todo in a finally block, then release the lock

Workflow plan (set by Hermes Master, read by all agents):
  - Hermes Master embeds a JSON plan in its dispatch comment
  - Each agent calls get_next_label_from_plan() to determine next step
  - Falls back to config default if no plan found
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from linear.client import LinearClient

logger = logging.getLogger(__name__)


def fetch_pending_issues(
    client: LinearClient, label_name: str, config, limit: int | None = None
) -> list[dict]:
    """Return issues with label_name that are in Todo state.

    Skips issues in any other state (In Progress, Backlog, Done, etc.) so
    that multiple agent instances never process the same issue simultaneously.
    """
    issues = client.get_issues(label_name=label_name, limit=limit or config.linear_max_issues)
    pending = [i for i in issues if (i.get("state") or {}).get("name") == config.linear_state_todo]
    skipped = len(issues) - len(pending)
    if skipped:
        logger.info(
            f"Fetched {len(issues)} '{label_name}' issue(s), "
            f"skipped {skipped} not in '{config.linear_state_todo}' state"
        )
    return pending


def acquire_lock(identifier: str, prefix: str = "") -> Path | None:
    """Try to acquire a PID-aware lock file for an issue.

    Returns the lock Path if acquired, or None if another live process holds it.
    Stale locks from dead processes are automatically cleared.
    """
    fname = f"{prefix}{identifier}.lock" if prefix else f"{identifier}.lock"
    lock_path = Path("locks") / fname

    if lock_path.exists():
        try:
            data = json.loads(lock_path.read_text())
            pid = data.get("pid")
            started_at = data.get("started_at", "unknown")
            alive = False
            if pid:
                try:
                    os.kill(pid, 0)  # signal 0 = check existence only, does not kill
                    alive = True
                except OSError:
                    pass
            if alive:
                logger.warning(
                    f"[{identifier}] Lock held by PID {pid} (started {started_at}), skipping."
                )
                return None
            logger.warning(
                f"[{identifier}] Stale lock from PID {pid} (started {started_at}, process gone) — clearing."
            )
            lock_path.unlink(missing_ok=True)
        except Exception:
            logger.warning(f"[{identifier}] Lock unreadable, skipping to be safe.")
            return None

    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": datetime.now().isoformat(), "identifier": identifier})
    )
    logger.info(f"[{identifier}] Lock acquired (PID {os.getpid()})")
    return lock_path


def set_in_progress(client: LinearClient, issue_id: str, identifier: str, config) -> None:
    """Set issue status to In Progress. Non-fatal on failure."""
    try:
        client.set_issue_state_by_name(issue_id, config.linear_state_in_progress, config.linear_team_id)
        logger.info(f"[{identifier}] State → '{config.linear_state_in_progress}'")
    except Exception as e:
        logger.warning(f"[{identifier}] Could not set In Progress state: {e}")


def set_todo(client: LinearClient, issue_id: str, identifier: str, config) -> None:
    """Reset issue status to Todo so the next agent can pick it up. Non-fatal on failure."""
    try:
        client.set_issue_state_by_name(issue_id, config.linear_state_todo, config.linear_team_id)
        logger.info(f"[{identifier}] State → '{config.linear_state_todo}'")
    except Exception as e:
        logger.warning(f"[{identifier}] Could not reset state to Todo: {e}")


# ---------------------------------------------------------------------------
# Workflow plan (set by Hermes Master, consumed by all agents)
# ---------------------------------------------------------------------------

_PLAN_PATTERN = re.compile(
    r"\*\*HERMES_PLAN\*\*\s*```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL,
)


def extract_project_path_override(description: str) -> str | None:
    """Parse `PROJECT_PATH: /some/path` from issue description (any line)."""
    if not description:
        return None
    match = re.search(r"^PROJECT_PATH:\s*(.+)$", description, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else None


def read_workflow_plan(comments: list[dict]) -> dict | None:
    """Find and parse the Hermes Master workflow plan embedded in issue comments.

    Returns the plan dict, or None if no plan is found.
    """
    for comment in comments:
        body = comment.get("body", "")
        match = _PLAN_PATTERN.search(body)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    return None


def get_next_label_from_plan(
    plan: dict | None, current_label: str, fallback: str
) -> str:
    """Return the next label in the workflow plan after current_label.

    Falls back to `fallback` when:
    - No plan exists
    - current_label is not in the plan
    - current_label is the last step
    """
    if not plan:
        return fallback
    steps = plan.get("steps", [])
    for i, step in enumerate(steps):
        if step.get("label") == current_label and i + 1 < len(steps):
            next_label = steps[i + 1]["label"]
            logger.info(f"Workflow plan: {current_label} → {next_label}")
            return next_label
    return fallback
