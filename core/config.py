from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Core
    linear_api_key: str
    anthropic_api_key: str | None = None
    linear_ready_label: str = "agent-ready"
    linear_max_issues: int = 5
    log_level: str = "INFO"

    # Development Agent
    linear_team_id: str | None = None
    project_path: str | None = None
    dev_agent_backend: str = "claude"

    # Claude Code CLI
    claude_code_command: str = "claude"
    claude_allowed_tools: str = "Read,Edit,Bash"
    claude_timeout_seconds: int = 3600

    # Codex (reserved)
    codex_command: str = "codex"
    codex_timeout_seconds: int = 3600
    codex_args: str = ""

    # Linear workflow states
    linear_state_in_progress: str = "In Progress"
    linear_state_todo: str = "Todo"

    # Git worktree
    worktree_base_dir: str = "/tmp/hermes-worktrees"
    git_base_branch: str = "main"

    # Hermes Master
    hermes_master_model: str = "claude-sonnet-4-6"

    # Document Agent
    flow_label_doc: str = "agent-doc"
    document_model: str = "claude-sonnet-4-6"
    document_next_label: str = "human-confirm"

    # Presentation Agent
    flow_label_ppt: str = "agent-ppt"
    presentation_model: str = "claude-sonnet-4-6"
    presentation_next_label: str = "human-confirm"

    # Reviewer Agent
    reviewer_model: str = "claude-sonnet-4-6"
    reviewer_approved_label: str = "human-confirm"
    reviewer_rejected_label: str = "agent-dev"

    # Flow labels
    flow_label_dev: str = "agent-dev"
    flow_label_test: str = "agent-test"
    flow_label_review: str = "agent-review"
    flow_label_escalate: str = "agent-escalate"
    flow_label_human_confirm: str = "human-confirm"
    flow_label_human_clarify: str = "human-clarify"
    flow_label_human_failed: str = "human-failed"
    development_next_label: str = "agent-review"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
