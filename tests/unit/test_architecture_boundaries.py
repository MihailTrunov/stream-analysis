from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN = {"fastapi", "sqlalchemy", "psycopg", "oandapyV20"}
CORE_DIRS = {"domain", "structure", "patterns", "indicators", "evaluation", "config"}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_core_has_no_infrastructure_imports() -> None:
    root = Path("src/market_analysis")
    violations: list[str] = []
    for dirname in CORE_DIRS:
        for path in (root / dirname).rglob("*.py"):
            bad = imported_roots(path) & FORBIDDEN
            if bad:
                violations.append(f"{path}: {sorted(bad)}")
    assert violations == []
