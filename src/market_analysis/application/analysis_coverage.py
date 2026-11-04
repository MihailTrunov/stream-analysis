"""Explicit valid-analysis bounds, independent of requested viewport bounds."""

from datetime import datetime
from typing import Literal

CoverageStatus = Literal["calculated", "partial", "initialization_unavailable"]


def coverage_status(
    start: datetime,
    end: datetime,
    valid_from: datetime | None,
    available_from: datetime | None,
    valid_to: datetime,
) -> CoverageStatus:
    if valid_from is None or available_from is None or end < available_from:
        return "initialization_unavailable"
    return "calculated" if start >= valid_from and end <= valid_to else "partial"
