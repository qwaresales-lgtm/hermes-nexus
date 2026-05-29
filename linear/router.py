import logging
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from core.config import get_settings
from linear.client import LinearClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/linear", tags=["Linear"])

CALLER_HEADER = "X-Agent-Name"


class CreateIssueRequest(BaseModel):
    title: str
    team_id: str
    description: str = ""
    priority: int = 0
    parent_id: str | None = None


class UpdateIssueRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: int | None = None
    state_id: str | None = Field(None, description="狀態 ID，從 GET /linear/states 取得")
    assignee_id: str | None = None
    label_ids: list[str] | None = Field(None, description="完整 label ID 列表（覆蓋現有 labels），從 GET /linear/labels 取得")


class AddCommentRequest(BaseModel):
    issue_id: str
    body: str = Field(..., description="Comment 內容（Markdown）")


def _client() -> LinearClient:
    return LinearClient()


def _caller(x_agent_name: str | None) -> str:
    return x_agent_name or "unknown"


def _log(caller: str, action: str, detail: str = "") -> None:
    msg = f"[{caller}] {action}"
    if detail:
        msg += f" | {detail}"
    logger.info(msg)


def _comment_footer(caller: str) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"\n\n---\n_由 **{caller}** 寫入 · {ts}_"


@router.get("/states", summary="列出指定團隊的所有工作流程狀態")
def list_states(
    team_id: str = Query(..., description="Team ID"),
    x_agent_name: str | None = Header(None),
):
    caller = _caller(x_agent_name)
    try:
        states = _client().get_workflow_states(team_id)
        _log(caller, "GET /linear/states", f"team_id={team_id} count={len(states)}")
        return {"states": states, "count": len(states)}
    except Exception as e:
        logger.error(f"[{caller}] list_states failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/labels", summary="建立新 Label")
def create_label(
    name: str = Query(..., description="Label 名稱"),
    color: str = Query(..., description="十六進位色碼，例如 #FF0000"),
    team_id: str = Query(..., description="Team ID"),
    x_agent_name: str | None = Header(None),
):
    caller = _caller(x_agent_name)
    try:
        result = _client().create_label(name=name, color=color, team_id=team_id)
        _log(caller, "POST /linear/labels", f"name={name} color={color}")
        return result
    except Exception as e:
        logger.error(f"[{caller}] create_label failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/labels", summary="列出指定團隊的所有 labels")
def list_labels(
    team_id: str = Query(..., description="Team ID"),
    x_agent_name: str | None = Header(None),
):
    caller = _caller(x_agent_name)
    try:
        labels = _client().get_labels(team_id)
        _log(caller, "GET /linear/labels", f"team_id={team_id} count={len(labels)}")
        return {"labels": labels, "count": len(labels)}
    except Exception as e:
        logger.error(f"[{caller}] list_labels failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/teams", summary="列出所有 Linear 團隊")
def list_teams(x_agent_name: str | None = Header(None)):
    caller = _caller(x_agent_name)
    try:
        teams = _client().get_teams()
        _log(caller, "GET /linear/teams", f"count={len(teams)}")
        return {"teams": teams, "count": len(teams)}
    except Exception as e:
        logger.error(f"[{caller}] list_teams failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/issues", summary="列出 Linear issues（可依 label 篩選）")
def list_issues(
    label: str | None = Query(None, description="依 label name 篩選"),
    limit: int = Query(10, ge=1, le=50),
    x_agent_name: str | None = Header(None),
):
    caller = _caller(x_agent_name)
    try:
        issues = _client().get_issues(label_name=label, limit=limit)
        _log(caller, "GET /linear/issues", f"label={label} count={len(issues)}")
        return {"issues": issues, "count": len(issues)}
    except Exception as e:
        logger.error(f"[{caller}] list_issues failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/issues/agent-ready", summary="列出所有 agent-ready issues")
def list_agent_ready_issues(x_agent_name: str | None = Header(None)):
    caller = _caller(x_agent_name)
    settings = get_settings()
    try:
        issues = _client().get_issues(
            label_name=settings.linear_ready_label,
            limit=settings.linear_max_issues,
        )
        _log(caller, "GET /linear/issues/agent-ready", f"count={len(issues)}")
        return {"issues": issues, "count": len(issues)}
    except Exception as e:
        logger.error(f"[{caller}] list_agent_ready_issues failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/issues/{issue_id}", summary="取得單筆 Linear issue")
def get_issue(issue_id: str, x_agent_name: str | None = Header(None)):
    caller = _caller(x_agent_name)
    try:
        issue = _client().get_issue(issue_id)
        _log(caller, f"GET /linear/issues/{issue_id}", f"identifier={issue.get('identifier')}")
        return issue
    except Exception as e:
        logger.error(f"[{caller}] get_issue({issue_id}) failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/issues", summary="建立 Linear issue")
def create_issue(req: CreateIssueRequest, x_agent_name: str | None = Header(None)):
    caller = _caller(x_agent_name)
    try:
        result = _client().create_issue(
            title=req.title,
            team_id=req.team_id,
            description=req.description,
            priority=req.priority,
            parent_id=req.parent_id,
        )
        _log(caller, "POST /linear/issues", f"title={req.title!r} identifier={result.get('issue', {}).get('identifier')}")
        return result
    except Exception as e:
        logger.error(f"[{caller}] create_issue failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.patch("/issues/{issue_id}", summary="更新 Linear issue 狀態或欄位")
def update_issue(
    issue_id: str,
    req: UpdateIssueRequest,
    x_agent_name: str | None = Header(None),
):
    caller = _caller(x_agent_name)
    input_data: dict = {}
    if req.title is not None:
        input_data["title"] = req.title
    if req.description is not None:
        input_data["description"] = req.description
    if req.priority is not None:
        input_data["priority"] = req.priority
    if req.state_id is not None:
        input_data["stateId"] = req.state_id
    if req.assignee_id is not None:
        input_data["assigneeId"] = req.assignee_id
    if req.label_ids is not None:
        input_data["labelIds"] = req.label_ids

    if not input_data:
        raise HTTPException(status_code=400, detail="No fields to update.")

    try:
        result = _client().update_issue(issue_id, input_data)
        _log(caller, f"PATCH /linear/issues/{issue_id}", f"fields={list(input_data.keys())}")
        return result
    except Exception as e:
        logger.error(f"[{caller}] update_issue({issue_id}) failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/comments", summary="在 Linear issue 寫入 comment（含 caller 資訊）")
def add_comment(req: AddCommentRequest, x_agent_name: str | None = Header(None)):
    caller = _caller(x_agent_name)
    body_with_footer = req.body + _comment_footer(caller)
    try:
        result = _client().add_comment(req.issue_id, body_with_footer)
        _log(caller, "POST /linear/comments", f"issue_id={req.issue_id}")
        return result
    except Exception as e:
        logger.error(f"[{caller}] add_comment({req.issue_id}) failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
