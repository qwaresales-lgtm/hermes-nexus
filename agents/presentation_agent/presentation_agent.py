#!/usr/bin/env python3
"""
Hermes Nexus — Presentation Agent
Monitors agent-ppt issues and generates PPTX presentations via Google NotebookLM.
Source material: Document Agent output for the same issue (fallback: issue description).

Setup (one-time):
    pip install "notebooklm-py[browser]"
    playwright install chromium
    notebooklm login
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

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
            logging.FileHandler("logs/presentation_agent.log", encoding="utf-8"),
        ],
    )
    logger.info("Presentation Agent starting up")


# ---------------------------------------------------------------------------
# Find Document Agent output for the same issue
# ---------------------------------------------------------------------------

def find_doc_output(identifier: str) -> dict | None:
    """Find the latest successful Document Agent output for this issue identifier."""
    task_runs = Path("task_runs")
    if not task_runs.exists():
        return None

    candidates = sorted(
        [d for d in task_runs.iterdir() if d.is_dir() and d.name.startswith(f"doc_{identifier}_")],
        reverse=True,
    )
    for candidate in candidates:
        result_json = candidate / "doc_result.json"
        if not result_json.exists():
            continue
        try:
            data = json.loads(result_json.read_text(encoding="utf-8"))
            if data.get("status") == "completed" and data.get("saved_path"):
                return data
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# NotebookLM workflow (async)
# ---------------------------------------------------------------------------

async def _generate_notebooklm(
    notebook_title: str,
    source_title: str,
    source_content: str,
    output_path: Path,
    config,
) -> str:
    """Create a NotebookLM notebook, add source, generate and download PPTX.
    Returns the saved PPTX path.
    """
    try:
        from notebooklm import NotebookLMClient, SlideDeckFormat, SlideDeckLength
    except ImportError:
        raise RuntimeError(
            "notebooklm-py is not installed. Run: pip install 'notebooklm-py[browser]' "
            "&& playwright install chromium && notebooklm login"
        )

    fmt_map = {
        "DETAILED_DECK": SlideDeckFormat.DETAILED_DECK,
        "PRESENTER_SLIDES": SlideDeckFormat.PRESENTER_SLIDES,
    }
    len_map = {
        "DEFAULT": SlideDeckLength.DEFAULT,
        "SHORT": SlideDeckLength.SHORT,
    }
    slide_format = fmt_map.get(config.notebooklm_format, SlideDeckFormat.DETAILED_DECK)
    slide_length = len_map.get(config.notebooklm_length, SlideDeckLength.DEFAULT)

    async with await NotebookLMClient.from_storage() as client:
        logger.info(f"Creating NotebookLM notebook: '{notebook_title}'")
        nb = await client.notebooks.create(notebook_title)
        logger.info(f"Notebook created: {nb.id}")

        logger.info(f"Adding source: '{source_title}' ({len(source_content)} chars)")
        await client.sources.add_text(
            nb.id,
            title=source_title,
            content=source_content,
            wait=True,
        )
        logger.info("Source added and ready")

        logger.info(f"Generating slide deck (format={config.notebooklm_format}, length={config.notebooklm_length})...")
        status = await client.artifacts.generate_slide_deck(
            nb.id,
            slide_deck_format=slide_format,
            slide_deck_length=slide_length,
        )
        logger.info(f"Generation started: task_id={status.task_id}")

        logger.info(f"Waiting for completion (timeout={config.notebooklm_timeout}s)...")
        await client.artifacts.wait_for_completion(
            nb.id,
            status.task_id,
            timeout=config.notebooklm_timeout,
        )
        logger.info("Slide deck generation complete")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        pptx_path = await client.artifacts.download_slide_deck(
            nb.id,
            str(output_path),
            output_format="pptx",
        )
        logger.info(f"Downloaded PPTX: {pptx_path}")

        if config.notebooklm_delete_notebook:
            await client.notebooks.delete(nb.id)
            logger.info(f"Notebook {nb.id} deleted")
        else:
            logger.info(f"Notebook {nb.id} kept in NotebookLM")

        return pptx_path


def generate_notebooklm(notebook_title, source_title, source_content, output_path, config) -> str:
    return asyncio.run(
        _generate_notebooklm(notebook_title, source_title, source_content, output_path, config)
    )


# ---------------------------------------------------------------------------
# Comment builders
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _footer() -> str:
    return f"\n\n---\n_由 **Presentation Agent** 寫入 · {_ts()}_"


def comment_success(identifier: str, pptx_path: str, source_desc: str, next_label: str,
                    keep_notebook: bool) -> str:
    notebook_note = (
        "NotebookLM notebook 已保留，可至 https://notebooklm.google.com/ 查看。"
        if keep_notebook
        else "NotebookLM notebook 已自動刪除。"
    )
    return (
        f"# Presentation Agent：簡報產生完成\n\n"
        f"## 來源\n\n{source_desc}\n\n"
        f"## 產出檔案\n\n`{pptx_path}`\n\n"
        f"## 備註\n\n{notebook_note}\n\n"
        f"## 下一步\n任務已轉移至 `{next_label}`。"
        f"{_footer()}"
    )


def comment_error(error: Exception) -> str:
    msg = str(error)
    hint = ""
    if "from_storage" in msg or "login" in msg.lower() or "auth" in msg.lower():
        hint = (
            "\n\n**可能原因：NotebookLM 尚未登入。**請在伺服器執行：\n"
            "```bash\nnotebooklm login\n```"
        )
    return (
        f"# Presentation Agent：執行失敗，已升級給 Hermes Master\n\n"
        f"## 錯誤摘要\n\n```\n{msg[:500]}\n```"
        f"{hint}\n\n"
        f"## 系統處理\n任務已改為 `agent-escalate`。"
        f"{_footer()}"
    )


# ---------------------------------------------------------------------------
# Issue processor
# ---------------------------------------------------------------------------

def process_issue(issue: dict, client: LinearClient, config) -> None:
    identifier = issue["identifier"]
    issue_id = issue["id"]

    logger.info(f"=== Presentation Agent {identifier}: {issue.get('title')} ===")

    lock_path = acquire_lock(identifier, prefix="ppt_")
    if lock_path is None:
        return

    set_in_progress(client, issue_id, identifier, config)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path("task_runs") / f"ppt_{identifier}_{ts}"
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

        # Find Document Agent output for same issue
        doc_output = find_doc_output(identifier)
        if doc_output:
            doc_path = Path(doc_output["saved_path"])
            if doc_path.exists():
                source_content = doc_path.read_text(encoding="utf-8")
                source_title = doc_path.name
                source_desc = f"Document Agent 產出：`{doc_output['saved_path']}`"
                logger.info(f"[{identifier}] Using doc output: {doc_path} ({len(source_content)} chars)")
            else:
                logger.warning(f"[{identifier}] Doc output path not found: {doc_path}, falling back to issue description")
                doc_output = None

        if not doc_output:
            source_content = f"# {issue.get('title')}\n\n{issue.get('description') or ''}"
            source_title = f"{identifier} - {issue.get('title')}"
            source_desc = "Issue description（找不到 Document Agent 產出，使用 issue 描述作為來源）"
            logger.warning(f"[{identifier}] No doc output found, using issue description as source")

        # Output path
        output_filename = f"{identifier.lower()}-slides.pptx"
        output_path = project_path / output_filename

        # Generate via NotebookLM
        notebook_title = f"{identifier} - {issue.get('title', '')}"
        logger.info(f"[{identifier}] Starting NotebookLM generation...")
        pptx_path = generate_notebooklm(
            notebook_title=notebook_title,
            source_title=source_title,
            source_content=source_content,
            output_path=output_path,
            config=config,
        )

        (run_dir / "ppt_result.json").write_text(
            json.dumps({
                "agent": "presentation_agent",
                "status": "completed",
                "source": source_desc,
                "pptx_path": pptx_path,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Determine next label from workflow plan
        fresh_issue = client.get_issue(issue_id)
        comments = (fresh_issue.get("comments") or {}).get("nodes", [])
        plan = read_workflow_plan(comments)
        next_label = get_next_label_from_plan(plan, config.flow_label_ppt, config.presentation_next_label)

        client.add_comment(issue_id, comment_success(
            identifier, pptx_path, source_desc, next_label,
            keep_notebook=not config.notebooklm_delete_notebook,
        ))
        client.replace_flow_label(issue_id, next_label, config.linear_team_id)
        logger.info(f"[{identifier}] SUCCESS → {next_label}")

    except Exception as e:
        logger.error(f"[{identifier}] Unexpected error: {e}", exc_info=True)
        (run_dir / "ppt_error.log").write_text(str(e), encoding="utf-8")
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
        candidates = linear_client.get_issues(label_name=config.flow_label_ppt, limit=50)
        issues = [i for i in candidates if i["identifier"] == args.identifier]
        if not issues:
            logger.error(f"Issue {args.identifier} with label '{config.flow_label_ppt}' not found")
            return
    else:
        issues = fetch_pending_issues(linear_client, config.flow_label_ppt, config)

    if not issues:
        logger.info(f"No issues with label '{config.flow_label_ppt}' found.")
        return

    logger.info(f"Found {len(issues)} issue(s): {[i['identifier'] for i in issues]}")
    for issue in issues:
        process_issue(issue, linear_client, config)


def main() -> None:
    setup_logging()
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Hermes Nexus Presentation Agent — generates PPTX via NotebookLM from agent-ppt issues"
    )
    parser.add_argument("--issue-id", help="Process specific Linear issue ID (UUID)")
    parser.add_argument("--identifier", help="Process by identifier, e.g. HER-5")
    parser.add_argument("--daemon", action="store_true", help="Poll continuously")
    parser.add_argument("--interval", type=int, default=60,
                        help="Poll interval in seconds (default: 60, NotebookLM takes ~5-10 min per task)")
    args = parser.parse_args()

    config = get_settings()

    errors = []
    if not config.linear_api_key:
        errors.append("LINEAR_API_KEY is not set")
    if not config.linear_team_id:
        errors.append("LINEAR_TEAM_ID is not set")
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
        f"watch_label={config.flow_label_ppt}, "
        f"notebooklm_format={config.notebooklm_format}, "
        f"notebooklm_timeout={config.notebooklm_timeout}s, "
        f"project_path={config.project_path}"
    )

    linear_client = LinearClient()

    if args.daemon:
        logger.info(f"=== Presentation Agent daemon started (poll interval: {args.interval}s) ===")
        cycle = 0
        try:
            while True:
                cycle += 1
                logger.info(f"--- Poll cycle #{cycle} ---")
                _run_one_poll_cycle(linear_client, config, args)
                logger.info(f"--- Cycle #{cycle} complete, sleeping {args.interval}s ---")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("Presentation Agent daemon stopped (KeyboardInterrupt)")
    else:
        try:
            _run_one_poll_cycle(linear_client, config, args)
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
