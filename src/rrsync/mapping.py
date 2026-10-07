"""Risk -> the Jira fields the register owns."""

from __future__ import annotations

import hashlib
import json

from .annex import control_by_id
from .register import Risk

SCALAR_FIELDS = ("summary", "priority", "duedate")
OWNED_FIELDS = ("summary", "description", "priority", "duedate", "labels")


def priority_for(score: int) -> str:
    if score >= 20:
        return "Highest"
    if score >= 15:
        return "High"
    if score >= 8:
        return "Medium"
    if score >= 4:
        return "Low"
    return "Lowest"


def is_owned_label(label: str) -> bool:
    return label == "grc-risk" or label.startswith(("risk-", "treatment-", "ctrl-"))


def labels_for(risk: Risk) -> list[str]:
    labels = ["grc-risk", f"risk-{risk.risk_id}", f"treatment-{risk.treatment}"]
    labels += [f"ctrl-{c}" for c in risk.controls]
    return sorted(set(labels))


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def build_adf(risk: Risk) -> dict:
    names = control_by_id()
    content = [
        {"type": "heading", "attrs": {"level": 3},
         "content": [{"type": "text", "text": f"{risk.risk_id}: {risk.title}"}]},
        _para(f"Asset: {risk.asset}"),
        _para(f"Threat: {risk.threat}"),
        _para(f"Vulnerability: {risk.vulnerability}"),
        _para(f"Owner: {risk.owner}"),
        _para(f"Inherent risk: likelihood {risk.likelihood} x impact {risk.impact} = {risk.inherent}"),
        _para(f"Residual risk: likelihood {risk.residual_likelihood} x impact "
              f"{risk.residual_impact} = {risk.residual}"),
        _para(f"Treatment: {risk.treatment}"),
    ]
    if risk.approved_by:
        content.append(_para(f"Approved by: {risk.approved_by}"))
    content.append(_para("Controls (ISO/IEC 27001:2022 Annex A):"))
    content.append({
        "type": "bulletList",
        "content": [{"type": "listItem", "content": [_para(
            f"{c} {names[c].name}" if c in names else c)]} for c in risk.controls],
    })
    content.append(_para(f"Treatment plan: {risk.treatment_plan or '(none)'}"))
    return {"version": 1, "type": "doc", "content": content}


def desired_fields(risk: Risk) -> dict:
    return {
        "summary": f"[{risk.risk_id}] {risk.title}",
        "description": build_adf(risk),
        "priority": priority_for(risk.inherent),
        "duedate": risk.due_date.isoformat() if risk.due_date else None,
        "labels": labels_for(risk),
    }


def digest(value) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
