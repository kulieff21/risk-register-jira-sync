import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from rrsync.annex import control_ids, load_controls, theme_counts
from rrsync.register import REQUIRED_COLUMNS, RegisterError, read_register
from rrsync.validate import errors, validate, warnings
from tests.conftest import EXAMPLES


def test_annex_counts():
    assert len(load_controls()) == 93
    assert len(control_ids()) == 93
    assert theme_counts() == {"organizational": 37, "people": 8, "physical": 14, "technological": 34}


def make_reg(tmp_path: Path, rows: list[dict], columns=REQUIRED_COLUMNS):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Risks"
    ws.append(list(columns))
    base = dict(risk_id="R-001", title="t", asset="a", threat="th", vulnerability="v", owner="o",
                likelihood=3, impact=3, controls="5.15", treatment="mitigate", treatment_plan="p",
                due_date=dt.date(2026, 12, 1), residual_likelihood=2, residual_impact=2, approved_by="")
    for r in rows:
        row = {**base, **r}
        ws.append([row.get(c) for c in columns])
    p = tmp_path / "r.xlsx"
    wb.save(p)
    return read_register(p)


def msgs(issues, level="error"):
    return [i.message for i in issues if i.level == level]


def has(issues, text, level="error"):
    return any(text in m for m in msgs(issues, level))


def test_example_register_valid():
    issues = validate(read_register(EXAMPLES / "register.xlsx"))
    assert errors(issues) == []
    assert len(warnings(issues)) == 1 and "high inherent" in warnings(issues)[0].message


def test_example_has_required_treatments_and_size():
    reg = read_register(EXAMPLES / "register.xlsx")
    assert len(reg.risks) == 24
    kinds = {r.treatment for r in reg.risks}
    assert kinds == {"mitigate", "accept", "transfer", "avoid"}
    assert sum(1 for r in reg.risks if r.treatment == "accept" and r.approved_by) >= 2
    assert all(r.residual <= r.inherent for r in reg.risks)
    today = dt.date(2026, 10, 8)
    assert any(r.due_date and r.due_date < today for r in reg.risks)
    assert any(r.due_date and r.due_date >= today for r in reg.risks)


def test_errors_file_triggers_each_row_rule_and_both_warnings():
    issues = validate(read_register(EXAMPLES / "register-with-errors.xlsx"))
    for text in ("does not match R-NNN", "duplicate risk_id", "title is empty", "owner is empty",
                 "asset is empty", "threat is empty", "must be an integer 1-5", "greater than inherent",
                 "controls is empty", "2013 numbering", "not an ISO/IEC 27001:2022", "treatment 'ignore'",
                 "needs a treatment_plan", "needs a due_date", "needs approved_by", "is not a date"):
        assert has(issues, text), text
    assert has(issues, "high inherent", "warning")
    assert has(issues, "changes nothing", "warning")
    assert all(i.row >= 2 for i in issues)


def test_rule1_missing_columns(tmp_path):
    cols = [c for c in REQUIRED_COLUMNS if c not in ("owner", "approved_by")]
    issues = validate(make_reg(tmp_path, [], cols))
    assert len(issues) == 1 and "owner" in issues[0].message and "approved_by" in issues[0].message


def test_extra_columns_allowed(tmp_path):
    reg = make_reg(tmp_path, [{}], list(REQUIRED_COLUMNS) + ["notes"])
    assert errors(validate(reg)) == []


def test_valid_row_has_no_issues(tmp_path):
    assert validate(make_reg(tmp_path, [{}])) == []


@pytest.mark.parametrize("rid", ["R-1", "r-001", "R-0001", "", "RISK-001"])
def test_rule2_bad_id(tmp_path, rid):
    assert has(validate(make_reg(tmp_path, [{"risk_id": rid}])), "R-NNN")


def test_rule2_duplicate_reports_first_row(tmp_path):
    issues = validate(make_reg(tmp_path, [{}, {}]))
    assert has(issues, "first seen in row 2") and issues[0].row == 3


@pytest.mark.parametrize("field", ["title", "owner", "asset", "threat"])
def test_rule3_empty_text(tmp_path, field):
    assert has(validate(make_reg(tmp_path, [{field: None}])), f"{field} is empty")


@pytest.mark.parametrize("field", ["likelihood", "impact", "residual_likelihood", "residual_impact"])
@pytest.mark.parametrize("bad", [0, 6, "x", None, 2.5])
def test_rule4_bad_score(tmp_path, field, bad):
    assert has(validate(make_reg(tmp_path, [{field: bad}])), f"{field} must be an integer 1-5")


def test_rule4_accepts_text_digits_and_whole_floats(tmp_path):
    assert errors(validate(make_reg(tmp_path, [{"likelihood": "3", "impact": 3.0}]))) == []


def test_rule5_residual_greater(tmp_path):
    assert has(validate(make_reg(tmp_path, [{"residual_likelihood": 4, "residual_impact": 4}])), "greater than inherent")
    assert not has(validate(make_reg(tmp_path, [{"residual_likelihood": 3, "residual_impact": 3}])), "greater than")


def test_rule6_controls(tmp_path):
    assert has(validate(make_reg(tmp_path, [{"controls": None}])), "controls is empty")
    assert has(validate(make_reg(tmp_path, [{"controls": "5.15; A.9.2.3"}])), "2013 numbering")
    assert has(validate(make_reg(tmp_path, [{"controls": "9.9"}])), "not an ISO/IEC 27001:2022")
    assert errors(validate(make_reg(tmp_path, [{"controls": "5.15, 8.2;5.18"}]))) == []


def test_rule7_treatment(tmp_path):
    assert has(validate(make_reg(tmp_path, [{"treatment": "ignore"}])), "treatment 'ignore'")
    for t in ("mitigate", "accept", "transfer", "avoid"):
        extra = {"approved_by": "x"} if t == "accept" else {}
        assert errors(validate(make_reg(tmp_path, [{"treatment": t, **extra}]))) == []


@pytest.mark.parametrize("t", ["mitigate", "transfer", "avoid"])
def test_rule8_plan_and_due(tmp_path, t):
    issues = validate(make_reg(tmp_path, [{"treatment": t, "treatment_plan": None, "due_date": None}]))
    assert has(issues, "needs a treatment_plan") and has(issues, "needs a due_date")
    assert not has(validate(make_reg(tmp_path, [{"treatment": t}])), "needs a")


def test_rule8_not_required_for_accept(tmp_path):
    issues = validate(make_reg(tmp_path, [{"treatment": "accept", "treatment_plan": None,
                                           "due_date": None, "approved_by": "CISO"}]))
    assert errors(issues) == []


def test_rule9_accept_needs_approver(tmp_path):
    assert has(validate(make_reg(tmp_path, [{"treatment": "accept"}])), "needs approved_by")
    assert not has(validate(make_reg(tmp_path, [{"treatment": "accept", "approved_by": "CISO"}])), "approved_by")


def test_rule10_due_date(tmp_path):
    assert has(validate(make_reg(tmp_path, [{"due_date": "next week"}])), "is not a date")
    assert has(validate(make_reg(tmp_path, [{"due_date": "2026-13-45"}])), "is not a date")
    assert errors(validate(make_reg(tmp_path, [{"due_date": "2026-12-01"}]))) == []


def test_warnings(tmp_path):
    hi = make_reg(tmp_path, [{"treatment": "accept", "approved_by": "x", "likelihood": 3, "impact": 5,
                              "residual_likelihood": 3, "residual_impact": 5}])
    assert has(validate(hi), "high inherent", "warning")
    lo = make_reg(tmp_path, [{"treatment": "accept", "approved_by": "x", "likelihood": 3, "impact": 4,
                              "residual_likelihood": 3, "residual_impact": 4}])
    assert warnings(validate(lo)) == []
    same = make_reg(tmp_path, [{"residual_likelihood": 3, "residual_impact": 3}])
    assert has(validate(same), "changes nothing", "warning")
    assert warnings(validate(make_reg(tmp_path, [{}]))) == []


def test_all_errors_collected_with_rows(tmp_path):
    issues = validate(make_reg(tmp_path, [{"title": None}, {"risk_id": "R-002", "owner": None}]))
    assert [(i.row, i.message) for i in errors(issues)] == [(2, "title is empty"), (3, "owner is empty")]


def test_missing_sheet_and_file(tmp_path):
    wb = openpyxl.Workbook()
    p = tmp_path / "x.xlsx"
    wb.save(p)
    with pytest.raises(RegisterError):
        read_register(p)
    with pytest.raises(RegisterError):
        read_register(tmp_path / "none.xlsx")
