from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN = {"fastapi", "sqlalchemy", "psycopg", "oandapyV20"}
CORE_DIRS = {
    "domain",
    "structure",
    "patterns",
    "indicators",
    "detection",
    "evaluation",
    "config",
}


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


def test_engine_level_write_boundaries_use_atomic_transaction() -> None:
    """Plain engine.begin() leaks nested-savepoint writes on legacy SQLite.

    Repository calls open ``connection.begin_nested()`` savepoints; under
    SQLite's default transaction control a savepoint issued before any DML
    becomes the real transaction, so releasing it commits early and a later
    failure in the outer block leaves a partial commit. The shared
    ``atomic_transaction`` helper establishes the real transaction first.
    Only the helper itself may call ``engine.begin()``.
    """
    root = Path("src/market_analysis")
    helper = root / "persistence" / "transactions.py"
    violations = [
        str(path)
        for path in root.rglob("*.py")
        if path != helper and "engine.begin()" in path.read_text()
    ]
    assert violations == []
