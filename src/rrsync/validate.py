"""Register validation: collect every problem with its sheet row number."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .annex import control_ids
from .register import REQUIRED_COLUMNS, Register, Risk

TREATMENTS = ("mitigate", "accept", "transfer", "avoid")


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning"
    row: int
    risk_id: str
    message: str

    def format(self) -> str:
        who = f" [{self.risk_id}]" if self.risk_id else ""
        return f"row {self.row}{who} {self.level}: {self.message}"


def _in_range(v: int | None) -> bool:
    return v is not None and 1 <= v <= 5


def validate(reg: Register) -> list[Issue]:
    issues: list[Issue] = []
    if reg.missing_columns:
        issues.append(Issue("error", 1, "", "required columns missing: " + ", ".join(reg.missing_columns)))
        return issues
    valid_controls = control_ids()
    first_row: dict[str, int] = {}
    for r in reg.risks:
        def err(msg: str, r: Risk = r) -> None:
            issues.append(Issue("error", r.row, r.risk_id, msg))

        def warn(msg: str, r: Risk = r) -> None:
            issues.append(Issue("warning", r.row, r.risk_id, msg))

        # 2: id format and uniqueness
        if not re.fullmatch(r"R-\d{3}", r.risk_id):
            err(f"risk_id {r.risk_id!r} does not match R-NNN (e.g. R-001)")
        elif r.risk_id in first_row:
            err(f"duplicate risk_id {r.risk_id} (first seen in row {first_row[r.risk_id]})")
        else:
            first_row[r.risk_id] = r.row
        # 3: required text
        for name in ("title", "owner", "asset", "threat"):
            if not getattr(r, name):
                err(f"{name} is empty")
        # 4: scores
        for name in ("likelihood", "impact", "residual_likelihood", "residual_impact"):
            if not _in_range(getattr(r, name)):
                err(f"{name} must be an integer 1-5")
        # 5: residual <= inherent
        scores_ok = all(_in_range(getattr(r, n)) for n in
                        ("likelihood", "impact", "residual_likelihood", "residual_impact"))
        if scores_ok and r.residual > r.inherent:
            err(f"residual score {r.residual} is greater than inherent score {r.inherent}")
        # 6: controls
        if not r.controls:
            err("controls is empty; list at least one Annex A 2022 control")
        for c in r.controls:
            if c in valid_controls:
                continue
            if re.fullmatch(r"A\.\d+(\.\d+)*", c):
                err(f"control {c!r} uses the ISO 27001:2013 numbering, which is not accepted; "
                    "use the 2022 Annex A ID (for example 5.15)")
            else:
                err(f"control {c!r} is not an ISO/IEC 27001:2022 Annex A control ID")
        # 7: treatment
        if r.treatment not in TREATMENTS:
            err(f"treatment {r.treatment!r} must be one of: {', '.join(TREATMENTS)}")
        # 8: plan and due date
        if r.treatment in ("mitigate", "transfer", "avoid"):
            if not r.treatment_plan:
                err(f"treatment '{r.treatment}' needs a treatment_plan")
            if not r.raw_due_date:
                err(f"treatment '{r.treatment}' needs a due_date")
        # 9: acceptance approver
        if r.treatment == "accept" and not r.approved_by:
            err("treatment 'accept' needs approved_by (a named approver)")
        # 10: due date format
        if r.raw_due_date and r.due_date is None:
            err(f"due_date {r.raw_due_date!r} is not a date (use YYYY-MM-DD)")
        # warnings
        if r.treatment == "accept" and r.inherent is not None and r.inherent >= 15:
            warn(f"high inherent score {r.inherent} (>= 15) is being accepted; check the approval")
        if (r.treatment == "mitigate" and scores_ok and r.residual == r.inherent):
            warn(f"mitigate but residual score {r.residual} equals inherent; the plan changes nothing")
    return issues


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]


def warnings(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "warning"]


__all__ = ["Issue", "validate", "errors", "warnings", "REQUIRED_COLUMNS", "TREATMENTS"]
