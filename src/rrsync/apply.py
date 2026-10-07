"""Execute a plan and write the audit log."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .jira import JiraError
from .mapping import digest
from .plan import Plan, State, pushed_snapshot


@dataclass
class ApplyResult:
    writes: int = 0
    errors: list[str] = field(default_factory=list)
    audit_path: Path | None = None


def _api_fields(changes: dict, keep_labels: list[str]) -> dict:
    out = {}
    for name, (_, after) in changes.items():
        if name == "priority":
            out[name] = {"name": after}
        elif name == "labels":
            out[name] = sorted(set(after) | set(keep_labels))
        else:
            out[name] = after
    return out


def _audit_path(audit_dir: Path, now: dt.datetime) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    path = audit_dir / f"{stamp}.jsonl"
    n = 1
    while path.exists():
        path = audit_dir / f"{stamp}_{n}.jsonl"
        n += 1
    return path


def apply_plan(client, plan: Plan, state: State, out_dir: Path, register_sha256: str,
               now: dt.datetime | None = None) -> ApplyResult:
    now = now or dt.datetime.now(dt.timezone.utc)
    result = ApplyResult(audit_path=_audit_path(Path(out_dir) / "audit", now))
    proj_state = state.project(plan.project)
    with result.audit_path.open("w", encoding="utf-8") as audit:
        def log(obj: dict) -> None:
            audit.write(json.dumps(obj, sort_keys=True, ensure_ascii=False) + "\n")
            audit.flush()

        log({"type": "run", "time": now.isoformat(), "register_sha256": register_sha256,
             "project": plan.project, "tool_version": __version__, "plan_counts": plan.counts()})
        for a in plan.actions:
            if not a.is_write:
                continue
            line = {"type": "write", "risk_id": a.risk_id, "action": "create" if a.kind == "create" else "update",
                    "key": a.key, "drift": bool(a.drift_fields), "drift_fields": a.drift_fields,
                    "changes": {k: {"before": b, "after": af} for k, (b, af) in a.changes.items()}}
            try:
                if a.kind == "create":
                    status, body = client.create_issue(plan.project, _api_fields(a.changes, []))
                    a.key = line["key"] = body["key"]
                else:
                    status = client.update_issue(a.key, _api_fields(a.changes, a.keep_labels))
                result.writes += 1
                line["http_status"] = status
                line["ok"] = True
            except JiraError as exc:
                line.update(ok=False, http_status=exc.status, error=str(exc))
                result.errors.append(f"{a.risk_id}: {exc}")
                log(line)
                continue
            log(line)
            try:
                after = client.get_issue(a.key)
                proj_state[a.risk_id] = {
                    "key": a.key,
                    "fields": pushed_snapshot(a.desired),
                    "desc_hash": digest(a.desired["description"]),
                    "jira_desc_hash": digest(after.get("fields", {}).get("description")),
                }
                state.save()
            except JiraError as exc:
                result.errors.append(f"{a.risk_id}: written, but could not read back: {exc}")
    return result
