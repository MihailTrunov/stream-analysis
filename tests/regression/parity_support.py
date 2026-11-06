"""Strict semantic parity assertions with fixture-declared numeric tolerances."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class Trace:
    invariants: dict[str, Any]
    checkpoints: list[dict[str, Any]]
    occurrences: list[dict[str, Any]]
    execution: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Tolerance:
    absolute: Decimal = Decimal(0)
    relative: Decimal = Decimal(0)

    def __post_init__(self):
        if any(not value.is_finite() or value < 0 for value in (self.absolute, self.relative)):
            raise ValueError("parity tolerances must be finite and nonnegative")


@dataclass(frozen=True)
class Divergence:
    checkpoint: str
    path: tuple[str, ...]
    expected: Any
    actual: Any

    def __str__(self):
        return (
            f"first divergence at {self.checkpoint}: {'.'.join(self.path)}; "
            f"expected={json.dumps(self.expected, sort_keys=True, default=str)}; "
            f"actual={json.dumps(self.actual, sort_keys=True, default=str)}"
        )


MISSING = "<missing parity value>"


def _difference(expected, actual, path, tolerances):
    if any(
        isinstance(value, float)
        and not math.isfinite(value)
        or isinstance(value, Decimal)
        and not value.is_finite()
        for value in (expected, actual)
    ):
        return path, expected, actual
    tolerance = next(
        (
            value
            for pattern, value in tolerances.items()
            if len(pattern) == len(path)
            and all(key == "*" or key == item for key, item in zip(pattern, path, strict=False))
        ),
        None,
    )
    if tolerance is not None:
        if expected is None or actual is None:
            return None if expected is actual else (path, expected, actual)
        if isinstance(expected, bool) or isinstance(actual, bool):
            return (
                None
                if type(expected) is type(actual) and expected == actual
                else (path, expected, actual)
            )
        try:
            left, right = Decimal(str(expected)), Decimal(str(actual))
            if (
                left.is_finite()
                and right.is_finite()
                and abs(left - right)
                <= max(
                    tolerance.absolute,
                    tolerance.relative * abs(left),
                )
            ):
                return None
        except (InvalidOperation, ValueError):
            pass
        return path, expected, actual
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected or key not in actual:
                return (*path, str(key)), expected.get(key, MISSING), actual.get(key, MISSING)
            difference = _difference(expected[key], actual[key], (*path, str(key)), tolerances)
            if difference:
                return difference
        return None
    if isinstance(expected, list | tuple) and isinstance(actual, list | tuple):
        for index in range(max(len(expected), len(actual))):
            if index >= len(expected) or index >= len(actual):
                return (
                    (*path, str(index)),
                    (expected[index] if index < len(expected) else MISSING),
                    (actual[index] if index < len(actual) else MISSING),
                )
            difference = _difference(
                expected[index], actual[index], (*path, str(index)), tolerances
            )
            if difference:
                return difference
        return None
    if type(expected) is type(actual) and expected == actual:
        return None
    return path, expected, actual


def first_divergence(expected: Trace, actual: Trace, *, tolerances=None) -> Divergence | None:
    tolerances = tolerances or {}
    difference = _difference(expected.invariants, actual.invariants, ("invariants",), tolerances)
    if difference:
        return Divergence("input lineage", *difference)
    for index in range(max(len(expected.checkpoints), len(actual.checkpoints))):
        left = expected.checkpoints[index] if index < len(expected.checkpoints) else MISSING
        right = actual.checkpoints[index] if index < len(actual.checkpoints) else MISSING
        difference = _difference(left, right, ("checkpoints", str(index)), tolerances)
        if difference:
            checkpoint = (left if isinstance(left, dict) else right).get("available_at", str(index))
            return Divergence(checkpoint, *difference)
    difference = _difference(expected.occurrences, actual.occurrences, ("occurrences",), tolerances)
    if difference:
        return Divergence("final occurrence context", *difference)
    return None


def assert_parity(expected: Trace, actual: Trace, **kwargs):
    difference = first_divergence(expected, actual, **kwargs)
    assert difference is None, str(difference)
