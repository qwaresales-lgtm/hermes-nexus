#!/usr/bin/env python3
"""
Hermes Nexus — Development Agent
Monitors Linear issues labeled 'agent-dev' and executes development tasks.
"""

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Allow running directly from agents/development_agent/ or from project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()

from agents.agent_utils import acquire_lock, fetch_pending_issues, set_in_progress, set_todo
from core.config import get_settings
from linear.client import LinearClient

logger = logging.getLogger(__name__)

FLOW_LABEL_PREFIXES = ("agent-", "human-")

REQUIRED_VERBS = [
    "建立", "修改", "修正", "產生", "整理", "檢查", "串接", "設計", "新增", "刪除",
    "重構", "優化", "實作", "部署", "遷移", "補充", "更新", "修復",
    "create", "update", "fix", "generate", "implement", "build", "add",
    "refactor", "optimize", "migrate", "deploy",
]
REQUIRED_TARGETS = [
    "檔案", "功能", "頁面", "API", "報表", "文件", "流程", "畫面", "資料", "程式",
    "模組", "元件", "介面", "資料庫", "腳本", "設定", "測試",
    "file", "feature", "page", "api", "report", "document", "data", "module",
    "component", "interface", "database", "script", "config", "test",
]
VAGUE_PHRASES = ["幫我做一下", "處理一下", "優化一下", "看一下", "弄一下", "改一下"]
DANGEROUS_KEYWORDS = [
    "正式部署", "刪除資料", "改 DB", "金鑰", "密碼", "token",
    "deploy to production", "delete data", "drop table", "rm -rf",
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class AgentExecutionResult:
    success: bool
    stdout: str
    stderr: str
    return_code: int
    duration_seconds: float


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
            logging.FileHandler("logs/development_agent.log", encoding="utf-8"),
        ],
    )
    logger.info("Development Agent starting up")


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

def load_memory_files(memory_dir: Path) -> dict:
    memory = {}
    for name in ["development_agent_rules", "linear_label_rules", "project_context"]:
        path = memory_dir / f"{name}.md"
        if path.exists():
            memory[name] = path.read_text(encoding="utf-8")
            logger.info(f"Loaded memory: {path.name}")
        else:
            memory[name] = ""
            logger.warning(f"Memory file not found (skipping): {path}")
    return memory


# ---------------------------------------------------------------------------
# PROJECT_PATH override
# ---------------------------------------------------------------------------

def extract_project_path_override(description: str) -> str | None:
    """Parse `PROJECT_PATH: /some/path` from issue description (any line, case-insensitive)."""
    if not description:
        return None
    match = re.search(r"^PROJECT_PATH:\s*(.+)$", description, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------------------
# Requirement completeness check
# ---------------------------------------------------------------------------

def check_requirement_completeness(issue: dict) -> dict:
    title = (issue.get("title") or "").strip()
    description = (issue.get("description") or "").strip()
    combined = f"{title} {description}".lower()

    missing_items = []
    suggested_questions = []

    if not description:
        missing_items.append("缺少任務描述（description 為空）")
        suggested_questions.append("請補充詳細的任務描述，說明要做什麼")

    if len(title) < 10 and not description:
        missing_items.append("標題過短且無描述")
        suggested_questions.append("請補充完整的任務說明")

    if description and not any(v in combined for v in REQUIRED_VERBS):
        missing_items.append("缺少明確動作動詞（如：建立、修改、修正、產生、串接）")
        suggested_questions.append("請說明要對系統做什麼操作？")

    if description and not any(t in combined for t in REQUIRED_TARGETS):
        missing_items.append("缺少明確目標（如：檔案、功能、API、文件）")
        suggested_questions.append("請說明要操作的具體目標是什麼？")

    if any(p in combined for p in VAGUE_PHRASES) and len(description) < 50:
        missing_items.append("描述過於模糊，缺少足夠細節")
        suggested_questions.append("請補充具體的需求細節與驗收標準")

    if any(k in combined for k in DANGEROUS_KEYWORDS):
        missing_items.append("任務涉及高風險操作（部署/刪除資料/金鑰），需明確授權說明")
        suggested_questions.append("請確認已獲得授權，並明確說明操作範圍與邊界")

    is_complete = len(missing_items) == 0
    return {
        "is_complete": is_complete,
        "missing_items": missing_items,
        "reason": "需求足夠執行" if is_complete else "任務描述不足，無法安全執行開發",
        "suggested_questions": suggested_questions,
    }


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_dev_prompt(issue: dict, memory: dict, config, work_dir: Path | None = None) -> str:
    template_path = Path(__file__).parent / "development_prompt.md"
    base_prompt = template_path.read_text(encoding="utf-8") if template_path.exists() else ""

    labels = [l["name"] for l in (issue.get("labels") or {}).get("nodes", [])]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    parts = [
        base_prompt,
        "",
        "---",
        "",
        "## Memory: Development Agent Rules",
        memory.get("development_agent_rules") or "(empty)",
        "",
        "## Memory: Linear Label Rules",
        memory.get("linear_label_rules") or "(empty)",
        "",
        "## Memory: Project Context",
        memory.get("project_context") or "(empty)",
        "",
        "---",
        "",
        "## 本次任務",
        "",
        f"- **Issue**: {issue.get('identifier')}",
        f"- **URL**: {issue.get('url')}",
        f"- **Title**: {issue.get('title')}",
        f"- **Labels**: {', '.join(labels)}",
        f"- **Priority**: {issue.get('priority')}",
        f"- **State**: {(issue.get('state') or {}).get('name')}",
        f"- **Timestamp**: {ts}",
        "",
        "**Description**:",
        issue.get("description") or "(無描述)",
        "",
        "---",
        "",
        f"**PROJECT_PATH**: {work_dir or config.project_path}",
        "",
        "## 執行限制（必須遵守）",
        "- 不要自動 git commit",
        "- 不要自動 git push",
        "- 不要自動 deploy",
        "- 不要修改 .env、金鑰、token、password",
        "- 不要刪除資料",
        "- 不要操作正式資料庫",
        "- 如果需求不足，請停止並說明原因，不要自行猜測",
        "- 修改後請執行基本語法或啟動檢查",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def run_claude_backend(prompt_text: str, work_dir: Path, config) -> AgentExecutionResult:
    command = config.claude_code_command
    allowed_tools = config.claude_allowed_tools
    timeout = config.claude_timeout_seconds

    logger.info(f"Starting Claude backend  command={command}  work_dir={work_dir}")
    start = time.time()

    try:
        result = subprocess.run(
            [command, "-p", prompt_text, "--allowedTools", allowed_tools],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(work_dir),
        )
        return AgentExecutionResult(
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.returncode,
            duration_seconds=time.time() - start,
        )
    except subprocess.TimeoutExpired:
        return AgentExecutionResult(
            success=False,
            stdout="",
            stderr=f"Claude execution timed out after {timeout}s",
            return_code=-1,
            duration_seconds=time.time() - start,
        )
    except FileNotFoundError:
        return AgentExecutionResult(
            success=False,
            stdout="",
            stderr=f"Command not found: '{command}'. Make sure Claude Code CLI is installed and in PATH.",
            return_code=-2,
            duration_seconds=0,
        )


def run_codex_backend(prompt_text: str, work_dir: Path, config) -> AgentExecutionResult:
    # TODO: implement Codex backend
    raise NotImplementedError(
        "Codex backend is not implemented yet. Set DEV_AGENT_BACKEND=claude."
    )


def run_backend(prompt_text: str, work_dir: Path, config) -> AgentExecutionResult:
    backend = config.dev_agent_backend
    if backend == "claude":
        return run_claude_backend(prompt_text, work_dir, config)
    if backend == "codex":
        return run_codex_backend(prompt_text, work_dir, config)
    raise ValueError(f"Unknown DEV_AGENT_BACKEND: '{backend}'")


# ---------------------------------------------------------------------------
# Git diff
# ---------------------------------------------------------------------------

def get_git_diff(project_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "diff"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(project_path),
        )
        if result.returncode == 0:
            return result.stdout
        logger.warning(f"git diff returned {result.returncode} (workspace may not be a git repo)")
        return ""
    except Exception as e:
        logger.warning(f"git diff failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Git worktree
# ---------------------------------------------------------------------------

def is_git_repo(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, cwd=str(path), timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _slugify(text: str, max_len: int = 40) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower().strip())
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")[:max_len].rstrip("-")


def create_worktree(project_path: Path, identifier: str, title: str, config) -> tuple[Path, str]:
    slug = _slugify(title) or "task"
    branch_name = f"agent/{identifier.lower()}-{slug}"
    worktree_path = Path(config.worktree_base_dir) / identifier

    # Remove stale worktree if present
    if worktree_path.exists():
        logger.warning(f"Stale worktree at {worktree_path}, removing...")
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            capture_output=True, cwd=str(project_path),
        )

    # Remove stale branch if present
    subprocess.run(
        ["git", "branch", "-D", branch_name],
        capture_output=True, cwd=str(project_path),
    )

    # Fetch latest base branch (best-effort, don't fail if no remote)
    fetch = subprocess.run(
        ["git", "fetch", "origin", config.git_base_branch],
        capture_output=True, text=True, cwd=str(project_path), timeout=60,
    )
    base = f"origin/{config.git_base_branch}" if fetch.returncode == 0 else config.git_base_branch

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "-b", branch_name, base],
        capture_output=True, text=True, cwd=str(project_path), timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")

    logger.info(f"Worktree created: {worktree_path} → branch {branch_name}")
    return worktree_path, branch_name


def remove_worktree(project_path: Path, worktree_path: Path) -> None:
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            capture_output=True, cwd=str(project_path), timeout=30,
        )
        logger.info(f"Worktree removed: {worktree_path}")
    except Exception as e:
        logger.warning(f"Failed to remove worktree {worktree_path}: {e}")


# ---------------------------------------------------------------------------
# Comment builders
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _footer() -> str:
    return f"\n\n---\n_由 **Development Agent** 寫入 · {_ts()}_"


def comment_incomplete(req_check: dict) -> str:
    missing = "\n".join(f"- {m}" for m in req_check["missing_items"])
    questions = "\n".join(
        f"{i + 1}. {q}" for i, q in enumerate(req_check["suggested_questions"])
    )
    return (
        f"# Development Agent：需求資訊不足，需人工補充\n\n"
        f"此任務目前無法安全進行開發，原因如下：\n\n"
        f"## 缺少資訊\n\n{missing}\n\n"
        f"## 請補充\n\n{questions}\n\n"
        f"## 系統處理\n\n"
        f"- Development Agent 未進行開發\n"
        f"- 任務已改為 `human-clarify`，請補充需求後將 label 改回 `agent-ready`"
        f"{_footer()}"
    )


def comment_success(issue: dict, exec_result: AgentExecutionResult, next_label: str,
                    branch_name: str | None = None) -> str:
    snippet = (exec_result.stdout[:1000] + "...") if len(exec_result.stdout) > 1000 else exec_result.stdout
    branch_line = f"- Branch: `{branch_name}`\n" if branch_name else ""
    return (
        f"# Development Agent：開發完成\n\n"
        f"## 任務\n"
        f"- Issue: {issue['identifier']}\n"
        f"- Duration: {exec_result.duration_seconds:.1f}s\n"
        f"{branch_line}\n"
        f"## 開發結果摘要\n\n{snippet}\n\n"
        f"## Git Diff\n已產生 `git_diff.patch`。\n\n"
        f"## 下一步\n任務已轉移至 `{next_label}`，等待下一位 Agent 接手。"
        f"{_footer()}"
    )


def comment_failure(issue: dict, exec_result: AgentExecutionResult) -> str:
    snippet = (exec_result.stderr[:500] + "...") if len(exec_result.stderr) > 500 else exec_result.stderr
    return (
        f"# Development Agent：執行失敗，已升級給 Hermes Master\n\n"
        f"Development Agent 執行時發生錯誤。\n\n"
        f"## 錯誤摘要\n\n```\n{snippet}\n```\n\n"
        f"## 系統處理\n任務已改為 `agent-escalate`，等待 Hermes Master 重新判斷。"
        f"{_footer()}"
    )


def comment_unexpected_error(issue: dict, error: Exception) -> str:
    return (
        f"# Development Agent：執行失敗，已升級給 Hermes Master\n\n"
        f"Development Agent 執行時發生非預期錯誤。\n\n"
        f"## 錯誤摘要\n\n```\n{str(error)[:500]}\n```\n\n"
        f"## 系統處理\n任務已改為 `agent-escalate`，等待 Hermes Master 重新判斷。"
        f"{_footer()}"
    )


# ---------------------------------------------------------------------------
# Issue processor
# ---------------------------------------------------------------------------

def process_issue(issue: dict, client: LinearClient, config, memory: dict) -> None:
    identifier = issue["identifier"]
    issue_id = issue["id"]

    logger.info(f"=== Processing {identifier}: {issue.get('title')} ===")

    lock_path = acquire_lock(identifier)
    if lock_path is None:
        return

    set_in_progress(client, issue_id, identifier, config)

    # Resolve PROJECT_PATH: config default, overridable per-issue via description
    _path_override = extract_project_path_override(issue.get("description") or "")
    if _path_override:
        project_path = Path(_path_override)
        if not project_path.exists():
            logger.error(f"[{identifier}] PROJECT_PATH override '{_path_override}' does not exist")
            lock_path.unlink(missing_ok=True)
            return
        logger.info(f"[{identifier}] PROJECT_PATH override from issue: {project_path}")
    else:
        project_path = Path(config.project_path)
    work_dir = project_path
    branch_name: str | None = None
    worktree_path: Path | None = None

    # Task run directory
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path("task_runs") / f"{identifier}_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"[{identifier}] Run directory created: {run_dir}")

    try:
        (run_dir / "issue_context.json").write_text(
            json.dumps(issue, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info(f"[{identifier}] Saved issue_context.json")

        # --- Requirement check ---
        req_check = check_requirement_completeness(issue)
        logger.info(f"[{identifier}] Requirement check: is_complete={req_check['is_complete']}, reason={req_check['reason']}")
        if not req_check["is_complete"]:
            for item in req_check["missing_items"]:
                logger.info(f"[{identifier}]   missing: {item}")

        if not req_check["is_complete"]:
            logger.info(f"[{identifier}] Blocking: incomplete requirements → human-confirm")
            (run_dir / "missing_requirements.md").write_text(
                "# 需求不足報告\n\n## 缺少資訊\n\n"
                + "\n".join(f"- {m}" for m in req_check["missing_items"]),
                encoding="utf-8",
            )
            logger.info(f"[{identifier}] Saved missing_requirements.md")
            _save_result_json(run_dir, "blocked", config.dev_agent_backend,
                              "needs_human_clarification",
                              "需求資訊不足，未進行開發，已要求人工補充。",
                              config.flow_label_human_clarify,
                              {"missing_requirements": "missing_requirements.md"},
                              requires_human=True)
            logger.info(f"[{identifier}] Saved dev_result.json (status=blocked)")
            logger.info(f"[{identifier}] Linear: adding comment (incomplete requirements)")
            client.add_comment(issue_id, comment_incomplete(req_check))
            logger.info(f"[{identifier}] Linear: replacing label → {config.flow_label_human_clarify}")
            client.replace_flow_label(issue_id, config.flow_label_human_clarify, config.linear_team_id)
            logger.info(f"[{identifier}] Blocked flow complete: label set to human-confirm")
            return

        # --- Build prompt ---
        dev_prompt = build_dev_prompt(issue, memory, config, work_dir=project_path)
        (run_dir / "dev_prompt.md").write_text(dev_prompt, encoding="utf-8")
        logger.info(f"[{identifier}] Saved dev_prompt.md ({len(dev_prompt)} chars)")

        # --- Setup worktree (if project is a git repo) ---
        if is_git_repo(project_path):
            try:
                worktree_path, branch_name = create_worktree(
                    project_path, identifier, issue.get("title", ""), config
                )
                work_dir = worktree_path
                (run_dir / "branch_name.txt").write_text(branch_name, encoding="utf-8")
                logger.info(f"[{identifier}] Worktree ready: {worktree_path}  branch: {branch_name}")
            except Exception as wt_err:
                logger.warning(f"[{identifier}] Worktree creation failed ({wt_err}), falling back to project_path")
        else:
            logger.info(f"[{identifier}] project_path is not a git repo, skipping worktree")

        # --- Run backend ---
        logger.info(f"[{identifier}] Dispatching to backend '{config.dev_agent_backend}', work_dir={work_dir}")
        exec_result = run_backend(dev_prompt, work_dir, config)
        logger.info(
            f"[{identifier}] Backend finished: success={exec_result.success}, "
            f"rc={exec_result.return_code}, duration={exec_result.duration_seconds:.1f}s, "
            f"stdout_len={len(exec_result.stdout)}, stderr_len={len(exec_result.stderr)}"
        )

        (run_dir / "dev_stdout.log").write_text(exec_result.stdout, encoding="utf-8")
        (run_dir / "dev_stderr.log").write_text(exec_result.stderr, encoding="utf-8")
        logger.info(f"[{identifier}] Saved dev_stdout.log and dev_stderr.log")

        if not exec_result.success:
            logger.error(f"[{identifier}] Backend failed (rc={exec_result.return_code}) → escalating")
            (run_dir / "dev_error.log").write_text(
                f"rc={exec_result.return_code}\n\n{exec_result.stderr}", encoding="utf-8"
            )
            logger.info(f"[{identifier}] Saved dev_error.log")
            if worktree_path:
                remove_worktree(project_path, worktree_path)
            _save_result_json(run_dir, "failed", config.dev_agent_backend,
                              "escalate",
                              "Development Agent 執行失敗，已升級給 Hermes Master。",
                              config.flow_label_escalate,
                              {"error_log": "dev_error.log"})
            logger.info(f"[{identifier}] Saved dev_result.json (status=failed)")
            logger.info(f"[{identifier}] Linear: adding comment (backend failure)")
            client.add_comment(issue_id, comment_failure(issue, exec_result))
            logger.info(f"[{identifier}] Linear: replacing label → {config.flow_label_escalate}")
            client.replace_flow_label(issue_id, config.flow_label_escalate, config.linear_team_id)
            logger.info(f"[{identifier}] Failure flow complete: label set to {config.flow_label_escalate}")
            return

        # --- Git diff (from worktree if available) ---
        git_diff = get_git_diff(work_dir)
        (run_dir / "git_diff.patch").write_text(git_diff, encoding="utf-8")
        logger.info(f"[{identifier}] Saved git_diff.patch ({len(git_diff)} bytes)")

        # --- Result files ---
        dev_result_md = (
            f"# Development Agent 執行結果\n\n"
            f"## 任務\n- Issue: {identifier}\n"
            f"- Backend: {config.dev_agent_backend}\n"
            f"- Duration: {exec_result.duration_seconds:.1f}s\n\n"
            f"## 執行輸出\n\n{exec_result.stdout[:3000]}\n\n"
            f"## 下一步\n任務將轉移至 `{config.development_next_label}`。\n"
        )
        (run_dir / "dev_result.md").write_text(dev_result_md, encoding="utf-8")
        logger.info(f"[{identifier}] Saved dev_result.md")

        next_label = config.development_next_label
        _save_result_json(run_dir, "completed", config.dev_agent_backend,
                          "next_agent",
                          "Development Agent 已完成開發，建議交給下一位 Agent 審核。",
                          next_label,
                          {"dev_prompt": "dev_prompt.md",
                           "stdout": "dev_stdout.log",
                           "stderr": "dev_stderr.log",
                           "dev_result": "dev_result.md",
                           "git_diff": "git_diff.patch"},
                          branch=branch_name)
        logger.info(f"[{identifier}] Saved dev_result.json (status=completed)")

        logger.info(f"[{identifier}] Linear: adding comment (success)")
        client.add_comment(issue_id, comment_success(issue, exec_result, next_label, branch_name))
        logger.info(f"[{identifier}] Linear: replacing label → {next_label}")
        client.replace_flow_label(issue_id, next_label, config.linear_team_id)
        logger.info(f"[{identifier}] SUCCESS: label set to {next_label}")

    except Exception as e:
        logger.error(f"[{identifier}] Unexpected error: {e}", exc_info=True)
        (run_dir / "dev_error.log").write_text(str(e), encoding="utf-8")
        if worktree_path:
            remove_worktree(project_path, worktree_path)
        try:
            logger.info(f"[{identifier}] Linear: adding error comment and setting to {config.flow_label_escalate}")
            client.add_comment(issue_id, comment_unexpected_error(issue, e))
            client.replace_flow_label(issue_id, config.flow_label_escalate, config.linear_team_id)
            logger.info(f"[{identifier}] Escalation complete after unexpected error")
        except Exception as e2:
            logger.error(f"[{identifier}] Failed to update Linear after error: {e2}")
    finally:
        set_todo(client, issue_id, identifier, config)
        lock_path.unlink(missing_ok=True)
        logger.info(f"[{identifier}] Lock released")


def _save_result_json(run_dir: Path, status: str, backend, decision: str,
                      summary: str, next_label: str, artifacts: dict,
                      requires_human: bool = False, branch: str | None = None) -> None:
    data = {
        "agent": "development_agent",
        "status": status,
        "backend": backend,
        "decision": decision,
        "summary": summary,
        "next_label": next_label,
        "branch": branch,
        "artifacts": artifacts,
        "requires_human_confirmation": requires_human,
    }
    (run_dir / "dev_result.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _run_one_poll_cycle(client: LinearClient, config, memory: dict, args) -> None:
    """Run a single poll-and-process cycle."""
    if args.issue_id:
        issues = [client.get_issue(args.issue_id)]
    elif args.identifier:
        candidates = client.get_issues(label_name=config.flow_label_dev, limit=50)
        issues = [i for i in candidates if i["identifier"] == args.identifier]
        if not issues:
            logger.error(f"Issue {args.identifier} with label '{config.flow_label_dev}' not found")
            return
    else:
        issues = fetch_pending_issues(client, config.flow_label_dev, config)

    if not issues:
        logger.info(f"No issues with label '{config.flow_label_dev}' found.")
        return

    logger.info(f"Found {len(issues)} issue(s) to process: {[i['identifier'] for i in issues]}")
    for issue in issues:
        process_issue(issue, client, config, memory)


def main() -> None:
    setup_logging()
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Hermes Nexus Development Agent — processes agent-dev Linear issues"
    )
    parser.add_argument("--issue-id", help="Process specific Linear issue ID (UUID)")
    parser.add_argument("--identifier", help="Process by identifier, e.g. HER-5")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Simulated scheduler: poll continuously until no more issues",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Poll interval in seconds when running in --daemon mode (default: 30)",
    )
    args = parser.parse_args()

    config = get_settings()

    # Validate required fields
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

    project_path = Path(config.project_path)
    if not project_path.exists():
        logger.error(f"PROJECT_PATH does not exist: {project_path}")
        sys.exit(1)

    for d in ["task_runs", "locks", "logs"]:
        Path(d).mkdir(exist_ok=True)

    logger.info(
        f"Config: team_id={config.linear_team_id}, "
        f"project_path={config.project_path}, "
        f"backend={config.dev_agent_backend}, "
        f"watch_label={config.flow_label_dev}, "
        f"next_label={config.development_next_label}, "
        f"max_issues={config.linear_max_issues}"
    )

    client = LinearClient()
    memory = load_memory_files(Path("memory"))

    if args.daemon:
        logger.info(f"=== Scheduler simulation started (poll interval: {args.interval}s) ===")
        cycle = 0
        try:
            while True:
                cycle += 1
                logger.info(f"--- Poll cycle #{cycle} ---")
                _run_one_poll_cycle(client, config, memory, args)
                logger.info(f"--- Cycle #{cycle} complete, sleeping {args.interval}s ---")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("Scheduler simulation stopped by user (KeyboardInterrupt)")
    else:
        try:
            _run_one_poll_cycle(client, config, memory, args)
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
