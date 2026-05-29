import logging

import requests

from core.config import get_settings

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"


class LinearClient:
    def __init__(self):
        self.api_key = get_settings().linear_api_key
        self.headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        payload = {"query": query, "variables": variables or {}}
        response = requests.post(LINEAR_API_URL, json=payload, headers=self.headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            raise ValueError(f"Linear API error: {data['errors']}")
        return data["data"]

    def get_issues(self, label_name: str | None = None, limit: int = 10) -> list[dict]:
        filter_clause = ""
        variables: dict = {"first": limit}
        if label_name:
            filter_clause = ", filter: { labels: { name: { eq: $labelName } } }"
            variables["labelName"] = label_name

        query = f"""
        query GetIssues($first: Int{",$labelName:String" if label_name else ""}) {{
          issues(first: $first{filter_clause}) {{
            nodes {{
              id identifier title description url priority createdAt updatedAt
              labels {{ nodes {{ id name }} }}
              state {{ id name }}
              assignee {{ id name email }}
              team {{ id name }}
              project {{ id name }}
            }}
          }}
        }}
        """
        data = self.graphql(query, variables)
        return data["issues"]["nodes"]

    def get_issue(self, issue_id: str) -> dict:
        query = """
        query GetIssue($id: String!) {
          issue(id: $id) {
            id identifier title description url priority createdAt updatedAt
            labels { nodes { id name } }
            state { id name }
            assignee { id name email }
            team { id name }
            project { id name }
            comments(first: 20) { nodes { id body createdAt user { name } } }
          }
        }
        """
        data = self.graphql(query, {"id": issue_id})
        return data["issue"]

    def get_workflow_states(self, team_id: str) -> list[dict]:
        query = """
        query GetWorkflowStates($teamId: String!) {
          team(id: $teamId) {
            states { nodes { id name type } }
          }
        }
        """
        data = self.graphql(query, {"teamId": team_id})
        return data["team"]["states"]["nodes"]

    def create_label(self, name: str, color: str, team_id: str) -> dict:
        mutation = """
        mutation CreateLabel($name: String!, $color: String!, $teamId: String!) {
          issueLabelCreate(input: { name: $name, color: $color, teamId: $teamId }) {
            success
            issueLabel { id name color }
          }
        }
        """
        data = self.graphql(mutation, {"name": name, "color": color, "teamId": team_id})
        return data["issueLabelCreate"]

    def get_labels(self, team_id: str) -> list[dict]:
        query = """
        query GetLabels($teamId: String!) {
          team(id: $teamId) {
            labels { nodes { id name color } }
          }
        }
        """
        data = self.graphql(query, {"teamId": team_id})
        return data["team"]["labels"]["nodes"]

    def get_teams(self) -> list[dict]:
        query = """
        query GetTeams {
          teams { nodes { id name key } }
        }
        """
        data = self.graphql(query)
        return data["teams"]["nodes"]

    def create_issue(
        self,
        title: str,
        team_id: str,
        description: str = "",
        priority: int = 0,
        parent_id: str | None = None,
        label_names: list[str] | None = None,
    ) -> dict:
        mutation = """
        mutation CreateIssue(
          $title: String!, $teamId: String!, $description: String,
          $priority: Int, $parentId: String
        ) {
          issueCreate(input: {
            title: $title, teamId: $teamId, description: $description,
            priority: $priority, parentId: $parentId
          }) {
            success
            issue { id identifier url title }
          }
        }
        """
        data = self.graphql(mutation, {
            "title": title,
            "teamId": team_id,
            "description": description,
            "priority": priority,
            "parentId": parent_id,
        })
        return data["issueCreate"]

    def update_issue(self, issue_id: str, input_data: dict) -> dict:
        mutation = """
        mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue { id identifier }
          }
        }
        """
        data = self.graphql(mutation, {"id": issue_id, "input": input_data})
        return data["issueUpdate"]

    def replace_flow_label(self, issue_id: str, new_label_name: str, team_id: str) -> dict:
        """Remove all agent-*/human-* flow labels and add new_label_name."""
        all_labels = self.get_labels(team_id)
        label_map = {l["name"]: l["id"] for l in all_labels}

        if new_label_name not in label_map:
            raise ValueError(f"Label '{new_label_name}' not found in team {team_id}")

        issue = self.get_issue(issue_id)
        non_flow_ids = [
            l["id"] for l in issue["labels"]["nodes"]
            if not (l["name"].startswith("agent-") or l["name"].startswith("human-"))
        ]
        new_label_ids = non_flow_ids + [label_map[new_label_name]]
        return self.update_issue(issue_id, {"labelIds": new_label_ids})

    def archive_issue(self, issue_id: str) -> dict:
        mutation = """
        mutation ArchiveIssue($id: String!) {
          issueArchive(id: $id) { success }
        }
        """
        data = self.graphql(mutation, {"id": issue_id})
        return data["issueArchive"]

    def set_issue_state_by_name(self, issue_id: str, state_name: str, team_id: str) -> dict:
        states = self.get_workflow_states(team_id)
        state = next((s for s in states if s["name"].lower() == state_name.lower()), None)
        if not state:
            available = [s["name"] for s in states]
            raise ValueError(f"State '{state_name}' not found. Available: {available}")
        return self.update_issue(issue_id, {"stateId": state["id"]})

    def add_comment(self, issue_id: str, body: str) -> dict:
        mutation = """
        mutation AddComment($issueId: String!, $body: String!) {
          commentCreate(input: { issueId: $issueId, body: $body }) {
            success
            comment { id createdAt }
          }
        }
        """
        data = self.graphql(mutation, {"issueId": issue_id, "body": body})
        return data["commentCreate"]
