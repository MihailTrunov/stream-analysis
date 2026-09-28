"""Causal component contract for one completed canonical bar per update.

Implementations keep only the history needed for their calculation. They expose
observable values through ``_state_values``; the base copies and recursively
freezes those values. Reset and replay from the same config and bars must produce
the same state and debug JSON. There is deliberately no snapshot/restore API:
resuming from partial, lossy state is not a supported operation.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import final

from market_analysis.config import DetectionAnalysisConfig
from market_analysis.domain import Bar


class MarketStateError(ValueError):
    """An input bar or component declaration violates the incremental contract."""


@dataclass(frozen=True, slots=True)
class MarketState:
    completed_bars: int
    last_timestamp: datetime | None
    is_warm: bool
    values: Mapping[str, object]


class IncrementalMarketState(ABC):
    """Base for deterministic components driven by completed bars only.

    Subclasses implement the three hooks. ``_update_completed_bar`` receives
    exactly the current bar, with no future-bar or full-history argument.
    ``_state_values`` may return nested mappings and sequences of immutable
    scalar values; the public state is a detached, read-only copy.
    """

    def __init__(
        self,
        run_config: DetectionAnalysisConfig,
        *,
        warmup_completed_bars: int,
    ) -> None:
        if not isinstance(run_config, DetectionAnalysisConfig):
            raise MarketStateError("run_config must be a frozen DetectionAnalysisConfig")
        if (
            isinstance(warmup_completed_bars, bool)
            or not isinstance(warmup_completed_bars, int)
            or warmup_completed_bars < 0
        ):
            raise MarketStateError("warmup_completed_bars must be a non-negative integer")
        self._run_config = run_config
        self._warmup_completed_bars = warmup_completed_bars
        self.reset()

    @property
    def run_config(self) -> DetectionAnalysisConfig:
        return self._run_config

    @property
    def warmup_completed_bars(self) -> int:
        return self._warmup_completed_bars

    @property
    def reset_generation(self) -> int:
        """Monotonic reset identity for consumers enforcing stream continuity."""
        return self._reset_generation

    @property
    def last_completed_bar(self) -> Bar | None:
        """The immutable current input, for exact dependency lockstep checks."""
        return self._last_completed_bar

    @final
    def reset(self) -> None:
        """Return to the initial state under the same immutable run config."""
        self._reset_state()
        self._reset_generation = getattr(self, "_reset_generation", 0) + 1
        self._completed_bars = 0
        self._last_timestamp: datetime | None = None
        self._last_completed_bar: Bar | None = None

    @final
    def update(self, bar: Bar) -> None:
        """Apply one completed bar, rejecting invalid input before any hook runs."""
        if not isinstance(bar, Bar):
            raise MarketStateError("update requires a canonical Bar")
        if not bar.is_complete:
            raise MarketStateError("bar must be completed")
        if (
            bar.instrument_id != self._run_config.instrument_id
            or bar.timeframe != self._run_config.timeframe
        ):
            raise MarketStateError("bar instrument/timeframe does not match run config")
        if self._last_timestamp is not None and bar.timestamp <= self._last_timestamp:
            raise MarketStateError("bar timestamps must be strictly increasing")
        self._update_completed_bar(bar)
        self._completed_bars += 1
        self._last_timestamp = bar.timestamp
        self._last_completed_bar = bar

    @property
    @final
    def state(self) -> MarketState:
        """Return a detached, recursively read-only observable state."""
        values = _freeze(self._state_values())
        if not isinstance(values, Mapping):
            raise MarketStateError("state values must be a mapping")
        return MarketState(
            completed_bars=self._completed_bars,
            last_timestamp=self._last_timestamp,
            is_warm=self._completed_bars >= self._warmup_completed_bars,
            values=values,
        )

    @final
    def debug_json(self) -> str:
        """Serialize the current observable state with stable key order and values."""
        state = self.state
        payload = {
            "run_config": json.loads(self._run_config.canonical_json()),
            "warmup_completed_bars": self._warmup_completed_bars,
            "completed_bars": state.completed_bars,
            "last_timestamp": _json_value(state.last_timestamp),
            "is_warm": state.is_warm,
            "values": _json_value(state.values),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @abstractmethod
    def _reset_state(self) -> None:
        """Discard all calculation history."""

    @abstractmethod
    def _update_completed_bar(self, bar: Bar) -> None:
        """Consume the current bar without accessing later bars."""

    @abstractmethod
    def _state_values(self) -> Mapping[str, object]:
        """Return observable values; do not expose mutable internal objects."""


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise MarketStateError("state mapping keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, str | bool | int | datetime):
        return value
    if isinstance(value, Decimal) and value.is_finite():
        return value
    raise MarketStateError(f"unsupported observable state value: {type(value).__name__}")


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise MarketStateError("state datetime must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value, "f")
    return value
