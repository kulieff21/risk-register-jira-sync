"""Compare the register (desired) with Jira (current) and decide what to do per risk."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .mapping import SCALAR_FIELDS, desired_fields, digest, is_owned_label
from .register import Risk

RISK_LABEL = re.compile(r"^risk-(R-\d{3})$")


def issue_labels(issue: dict) -> list[str]:
    return list(issue.get("fields", {}).get("labels") or [])


def risk_ids_of(issue: dict) -> list[str]:
    return [m.group(1) for l in issue_labels(issue) if (m := RISK_LABEL.match(l))]


def group_by_risk(issues: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for issue in issues:
        for rid in risk_ids_of(issue):
            groups.setdefault(rid, []).append(issue)
    return groups


def current_view(issue: dict) -> dict:
    f = issue.get("fields", {})
    labels = issue_labels(issue)
    return {
        "summary": f.get("summary"),
        "priority": (f.get("priority") or {}).get("name"),
        "duedate": f.get("duedate"),
        "description": f.get("description"),
        "owned_labels": sorted(l for l in labels if is_owned_label(l)),
        "other_labels": sorted(l for l in labels if not is_owned_label(l)),
    }


class State:
    """Last-synced state: what this tool last pushed, per project and risk ID."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {"version": 1, "projects": {}}
        if self.path.is_file():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def project(self, key: str) -> dict:
        return self.data.setdefault("projects", {}).setdefault(key, {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass
class Action:
    kind: str  # create | update | drift | noop | conflict | orphan
    risk_id: str | None
    key: str | None = None
    keys: list[str] = field(default_factory=list)
    changes: dict[str, tuple[Any, Any]] = field(default_factory=dict)  # field -> (before, after)
    drift_fields: list[str] = field(default_factory=list)
    keep_labels: list[str] = field(default_factory=list)
    desired: dict | None = None
    pushed_before: dict | None = None  # state entry used for the decision

    @property
    def is_write(self) -> bool:
        return self.kind in ("create", "update", "drift")


@dataclass
class Plan:
    project: str
    actions: list[Action]

    def counts(self) -> dict[str, int]:
        out = {k: 0 for k in ("create", "update", "drift", "noop", "conflict", "orphan")}
        for a in self.actions:
            out[a.kind] += 1
        return out

    def of(self, kind: str) -> list[Action]:
        return [a for a in self.actions if a.kind == kind]


def pushed_snapshot(desired: dict) -> dict:
    return {"summary": desired["summary"], "priority": desired["priority"],
            "duedate": desired["duedate"], "labels": sorted(desired["labels"])}


def _plan_existing(risk: Risk, desired: dict, issue: dict, st: dict | None) -> Action:
    cur = current_view(issue)
    changes: dict[str, tuple[Any, Any]] = {}
    drift: list[str] = []
    for name in SCALAR_FIELDS:
        if cur[name] != desired[name]:
            changes[name] = (cur[name], desired[name])
            if st is not None and cur[name] != st["fields"][name]:
                drift.append(name)
    want_labels = sorted(desired["labels"])
    if cur["owned_labels"] != want_labels:
        changes["labels"] = (cur["owned_labels"], want_labels)
        if st is not None and cur["owned_labels"] != st["fields"]["labels"]:
            drift.append("labels")
    if st is None:
        # No record of what we pushed: cannot verify the description, so write it once.
        changes["description"] = (cur["description"], desired["description"])
    else:
        generated_changed = digest(desired["description"]) != st["desc_hash"]
        hand_edited = digest(cur["description"]) != st["jira_desc_hash"]
        if generated_changed or hand_edited:
            changes["description"] = (cur["description"], desired["description"])
        if hand_edited:
            drift.append("description")
    if not changes:
        return Action("noop", risk.risk_id, key=issue["key"], desired=desired)
    return Action("drift" if drift else "update", risk.risk_id, key=issue["key"], changes=changes,
                  drift_fields=drift, keep_labels=cur["other_labels"], desired=desired, pushed_before=st)


def build_plan(risks: list[Risk], issues: list[dict], state_project: dict, project: str) -> Plan:
    groups = group_by_risk(issues)
    register_ids = {r.risk_id for r in risks}
    actions: list[Action] = []
    for risk in risks:
        desired = desired_fields(risk)
        found = groups.get(risk.risk_id, [])
        if not found:
            actions.append(Action("create", risk.risk_id, changes={k: (None, v) for k, v in desired.items()},
                                  desired=desired))
        elif len(found) > 1:
            actions.append(Action("conflict", risk.risk_id, keys=sorted(i["key"] for i in found),
                                  desired=desired))
        else:
            actions.append(_plan_existing(risk, desired, found[0], state_project.get(risk.risk_id)))
    seen: set[str] = set()
    for issue in sorted(issues, key=lambda i: i["key"]):
        rids = risk_ids_of(issue)
        is_orphan = (rids and not any(r in register_ids for r in rids)) or \
                    (not rids and "grc-risk" in issue_labels(issue))
        if is_orphan and issue["key"] not in seen:
            seen.add(issue["key"])
            actions.append(Action("orphan", rids[0] if rids else None, key=issue["key"]))
    return Plan(project=project, actions=actions)


def describe(action: Action) -> str:
    rid = action.risk_id or "-"
    if action.kind == "create":
        return f"CREATE   {rid}  {action.desired['summary']}"
    if action.kind in ("update", "drift"):
        names = ", ".join(action.changes)
        line = f"{action.kind.upper():<8} {rid}  {action.key}  fields: {names}"
        for f in action.drift_fields:
            before = action.changes[f][0]
            shown = "<edited ADF>" if f == "description" else repr(before)
            line += f"\n           drift in {f}: Jira has {shown}, register wins"
        return line
    if action.kind == "conflict":
        return f"CONFLICT {rid}  several issues carry the label risk-{rid}: {', '.join(action.keys)} (skipped)"
    if action.kind == "orphan":
        return f"ORPHAN   {rid}  {action.key}  not in the register (left untouched)"
    return f"NOOP     {rid}  {action.key}"
