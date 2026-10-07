"""ISO/IEC 27001:2022 Annex A control list (93 controls, 4 themes)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources


@dataclass(frozen=True)
class Control:
    id: str
    theme: str
    name: str


@lru_cache(maxsize=1)
def load_controls() -> tuple[Control, ...]:
    text = resources.files("rrsync").joinpath("annex_a_2022.tsv").read_text(encoding="utf-8")
    controls = []
    for line in text.splitlines()[1:]:
        if not line.strip():
            continue
        cid, theme, name = line.split("\t")
        controls.append(Control(cid.strip(), theme.strip(), name.strip()))
    return tuple(controls)


def control_ids() -> frozenset[str]:
    return frozenset(c.id for c in load_controls())


def control_by_id() -> dict[str, Control]:
    return {c.id: c for c in load_controls()}


def theme_counts() -> dict[str, int]:
    counts = Counter(c.theme for c in load_controls())
    return dict(counts)
