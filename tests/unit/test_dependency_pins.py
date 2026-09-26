from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


def _pin(requirement: str) -> tuple[str, str]:
    match = re.match(r"^([\w.-]+)(?:\[[^]]+\])?==([^;\s]+)", requirement)
    assert match is not None, f"requirement is not pinned: {requirement}"
    return match.group(1).lower(), match.group(2)


def _exported_pins() -> dict[str, str]:
    pins = {}
    for line in Path("requirements.lock").read_text().splitlines():
        if re.match(r"^[\w.-]+==", line):
            name, version = _pin(line)
            pins[name] = version
    return pins


def test_python_direct_dependencies_are_present_in_lock() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text())
    declared = dict(
        _pin(item)
        for item in config["project"]["dependencies"] + config["dependency-groups"]["dev"]
    )
    assert declared.items() <= _exported_pins().items()


def test_exported_requirements_cover_resolved_python_graph() -> None:
    uv_lock = tomllib.loads(Path("uv.lock").read_text())
    resolved = {
        package["name"]: package["version"]
        for package in uv_lock["package"]
        if package["name"] != "stream-analysis"
    }
    assert resolved.items() <= _exported_pins().items()


def test_nx_is_installed_from_the_workspace_lock() -> None:
    package = json.loads(Path("package.json").read_text())
    assert package["devDependencies"]["nx"] == "21.5.3"
    assert all("pnpm dlx" not in command for command in package["scripts"].values())
