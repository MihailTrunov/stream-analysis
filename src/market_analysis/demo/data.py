from __future__ import annotations

import json
from importlib.resources import files

from market_analysis.domain import Bar


def load_demo_bars() -> tuple[Bar, ...]:
    path = files("market_analysis.demo").joinpath("bars.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(Bar.from_canonical_dict(item) for item in payload)
