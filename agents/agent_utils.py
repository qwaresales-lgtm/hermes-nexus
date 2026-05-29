"""
Shared utilities for all Hermes Nexus agents.

All agents must follow this protocol:
  1. Fetch only issues with the target label AND status = Todo
  2. Acquire a PID-aware lock before processing
  3. Set status to In Progress immediately after acquiring the lock
  4. Reset status to Todo in a finally block, then release the lock
"""

import json
import logging
import os
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
