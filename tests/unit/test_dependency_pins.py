from __future__ import annotations

import re
import tomllib
from pathlib import Path


def _pin_name(requirement: str) -> str:
    return re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].strip().lower()


def test_python_direct_dependencies_are_present_in_lock() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text())
    declared = {
        _pin_name(item)
        for item in config["project"]["dependencies"] + config["dependency-groups"]["dev"]
    }
    locked = {
        _pin_name(line)
        for line in Path("requirements.lock").read_text().splitlines()
        if line and not line.startswith("#")
    }
    assert declared <= locked
