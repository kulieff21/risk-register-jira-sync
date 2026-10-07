"""In-memory fake of the Jira client interface used by plan/apply/report."""

from __future__ import annotations

import copy
import re

from rrsync.jira import JiraError


class FakeJira:
    def __init__(self, projects=("RISK",)):
        self.projects = set(projects)
        self.issues: dict[str, dict] = {}
        self.counter = 0
        self.writes: list[tuple] = []  # every write call: (op, key_or_project, fields)
        self.reads = 0
        self.lose_next_create_response = False
        self.fail_next_update: JiraError | None = None

    # -- test helpers (not counted as writes) ------------------------------------
    def add_issue(self, project="RISK", **fields) -> str:
        self.counter += 1
        key = f"{project}-{self.counter}"
        base = {"summary": "", "description": None, "priority": {"name": "Medium"}, "duedate": None,
                "labels": [], "status": {"name": "To Do", "statusCategory": {"key": "new"}},
                "resolutiondate": None}
        base.update(copy.deepcopy(fields))
        self.issues[key] = {"key": key, "id": str(10000 + self.counter), "project": project, "fields": base}
        return key

    def by_label(self, label: str) -> list[str]:
        return [k for k, i in self.issues.items() if label in i["fields"]["labels"]]

    def hand_edit(self, key: str, **fields) -> None:
        if "priority" in fields:
            fields["priority"] = {"name": fields["priority"]}
        self.issues[key]["fields"].update(fields)

    def set_status(self, key: str, name: str, category: str, resolved: str | None = None) -> None:
        f = self.issues[key]["fields"]
        f["status"] = {"name": name, "statusCategory": {"key": category}}
        f["resolutiondate"] = resolved

    def add_duplicate_of(self, key: str) -> str:
        return self.add_issue(**copy.deepcopy(self.issues[key]["fields"]))

    def add_orphan(self, risk_id: str = "R-999") -> str:
        return self.add_issue(summary=f"[{risk_id}] removed from register", labels=["grc-risk", f"risk-{risk_id}"])

    # -- client interface --------------------------------------------------------
    def myself(self) -> dict:
        return {"accountId": "acc-1", "displayName": "Fake User"}

    def get_project(self, key: str):
        return {"key": key} if key in self.projects else None

    def create_project(self, key, name, lead_account_id):
        self.writes.append(("create_project", key, name))
        self.projects.add(key)
        return {"key": key}

    @staticmethod
    def _project_of(jql: str) -> str:
        m = re.search(r'project\s*=\s*"?([A-Z0-9]+)"?', jql)
        if not m:
            raise JiraError("fake: unsupported JQL")
        return m.group(1)

    def search_issues(self, project: str) -> list[dict]:
        self.reads += 1
        return [copy.deepcopy(i) for i in self.issues.values() if i["project"] == project]

    def get_issue(self, key: str) -> dict:
        self.reads += 1
        if key not in self.issues:
            raise JiraError(f"issue {key} not found", 404)
        return copy.deepcopy(self.issues[key])

    def create_issue(self, project: str, fields: dict):
        self.writes.append(("create", project, copy.deepcopy(fields)))
        f = copy.deepcopy(fields)
        key = self.add_issue(project, **f)
        if self.lose_next_create_response:
            self.lose_next_create_response = False
            raise JiraError("connection lost while waiting for the response")
        return 201, {"key": key, "id": self.issues[key]["id"]}

    def update_issue(self, key: str, fields: dict) -> int:
        self.writes.append(("update", key, copy.deepcopy(fields)))
        if self.fail_next_update:
            exc, self.fail_next_update = self.fail_next_update, None
            raise exc
        self.issues[key]["fields"].update(copy.deepcopy(fields))
        return 204
