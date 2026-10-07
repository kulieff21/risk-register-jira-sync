import datetime as dt

import pytest

from rrsync.mapping import desired_fields, is_owned_label, labels_for, priority_for
from rrsync.register import Risk


def risk(**kw):
    base = dict(row=2, risk_id="R-001", title="Leaver accounts", asset="a", threat="t", vulnerability="v",
                owner="o", likelihood=4, impact=4, controls=["5.18", "6.5"], treatment="mitigate",
                treatment_plan="plan", due_date=dt.date(2026, 12, 1), raw_due_date="2026-12-01",
                residual_likelihood=2, residual_impact=2, approved_by="")
    base.update(kw)
    return Risk(**base)


@pytest.mark.parametrize("score,expected", [
    (25, "Highest"), (20, "Highest"), (19, "High"), (15, "High"), (14, "Medium"), (8, "Medium"),
    (7, "Low"), (4, "Low"), (3, "Lowest"), (1, "Lowest")])
def test_priority_boundaries(score, expected):
    assert priority_for(score) == expected


def test_labels():
    assert labels_for(risk()) == sorted(["grc-risk", "risk-R-001", "treatment-mitigate", "ctrl-5.18", "ctrl-6.5"])


def test_owned_label_rule():
    for l in ("grc-risk", "risk-R-001", "treatment-accept", "ctrl-5.1"):
        assert is_owned_label(l)
    for l in ("urgent", "team-red", "grc"):
        assert not is_owned_label(l)


def test_desired_fields():
    d = desired_fields(risk())
    assert d["summary"] == "[R-001] Leaver accounts"
    assert d["priority"] == "High"
    assert d["duedate"] == "2026-12-01"
    assert d["description"]["type"] == "doc" and d["description"]["version"] == 1
    assert "5.18 Access rights" in str(d["description"])
    assert desired_fields(risk(due_date=None))["duedate"] is None
