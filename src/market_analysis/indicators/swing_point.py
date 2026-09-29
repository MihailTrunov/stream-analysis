"""Provisional ATR directional-change SwingPoint v1 over completed canonical bars."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from types import MappingProxyType

from market_analysis.config import DetectionAnalysisConfig, detection_config_hash
from market_analysis.domain import Bar
from market_analysis.patterns import PatternDefinition

from .atr import AtrState
from .incremental import IncrementalMarketState, MarketStateError

SWING_POINT_DEFINITION_ID = "ATR_DIRECTIONAL_CHANGE_SWING_V1"
SWING_POINT_PRECISION = 34

_SWING_POINT_PARAMETERS = frozenset(
    {
        "atr_period",
        "reversal_atr_multiplier",
        "extreme_source",
        "confirmation_source",
        "freeze_atr_at_extreme",
        "allow_same_bar_confirmation",
        "equal_extreme_policy",
        "require_alternation",
    }
)


class SwingType(StrEnum):
    """Confirmed swing direction of a SwingPoint."""

    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"


class ExtremeSource(StrEnum):
    """Bar price used for candidate extremes; v1 supports OHLC high/low only."""

    HIGH_LOW = "HIGH_LOW"


class ConfirmationSource(StrEnum):
    """Bar price used for threshold confirmation; v1 supports closes only."""

    CLOSE = "CLOSE"


class EqualExtremePolicy(StrEnum):
    """Equal-extreme handling; v1 keeps the earliest candidate event_time."""

    KEEP_EARLIEST = "KEEP_EARLIEST"


@dataclass(frozen=True, slots=True)
class SwingPointReference:
    """Scalar semantic reference to the previously confirmed SwingPoint."""

    swing_index: int
    swing_type: SwingType
    event_time: datetime
    event_price: Decimal


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """Confirmed swing evidence; an unconfirmed candidate never produces one.

    ``event_time``/``event_price`` identify the extreme bar while
    ``detection_time``/``confirmation_close`` identify the later bar whose
    completed close first crossed the frozen threshold. Downstream consumers
    may only use the point from ``detection_time`` onward.
    """

    swing_index: int
    swing_type: SwingType
    definition_id: str
    event_time: datetime
    event_price: Decimal
    detection_time: datetime
    confirmation_close: Decimal
    candidate_bar_index: int
    confirmation_bar_index: int
    atr_period: int
    atr_at_extreme: Decimal
    reversal_atr_multiplier: Decimal
    threshold_points: Decimal
    bars_to_confirmation: int
    previous_swing: SwingPointReference | None
    detection_config_hash: str | None = None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """Provisional extreme; debug evidence only, never a confirmed swing."""

    price: Decimal
    event_time: datetime
    atr_at_extreme: Decimal
    threshold_points: Decimal
    bar_index: int
    equal_extreme_count: int = 0
    last_equal_extreme_time: datetime | None = None


class SwingPointState(IncrementalMarketState):
    """Causal ATR directional-change swings; visible only from detection_time.

    The searched-direction candidate and the opposite running extreme follow
    directional-change carry semantics. Until the first confirmation both
    directions are searched independently (dual mode); afterwards confirmed
    swings strictly alternate and only the next direction is searched. On
    every completed bar, in order:

    1. Replacement. A strictly more extreme bar replaces the searched
       candidate of that direction, freezing the ATR threshold at that bar.
       In dual mode the two searched candidates are independent, so a new low
       never disturbs a pending high confirmation and vice versa. Replacing a
       candidate also re-anchors the opposite running extreme that tracks the
       window starting at that candidate's anchor bar.
    2. Opposite tracking. The opposite running extreme of a candidate is the
       most extreme opposite price observed since that candidate's anchor bar
       (including the current bar); it moves only on strictly more extreme
       values with its own frozen ATR/threshold, and equal values keep the
       earliest ``event_time`` while counting evidence.
    3. Confirmation. A candidate confirms only when its anchor bar is strictly
       before the current bar; a candidate replaced on the current bar is
       structurally unable to confirm on it, for every multiplier. In dual
       mode the high is checked before the low, fixing the alternation on the
       first confirmation. Confirming a direction at bar ``j`` emits the
       candidate's anchor evidence with bar ``j``'s detection evidence; the
       next searched candidate is the carried opposite running extreme with
       its own earlier anchor, its own ATR/threshold frozen at that anchor
       and its own equal-evidence, and a fresh opposite extreme is seeded
       from bar ``j``. The carried candidate cannot confirm on the emission
       bar itself.

    Boundary initialization is a documented v1 deviation: a first
    post-warm-up bar anchors both dual candidates, and because v1 has no
    pre-window observation, a strictly monotone window can later confirm one
    swing anchored at that boundary bar once the threshold reversal is
    observable. Material changes to this initialization require a new
    definition version.

    An equal extreme never replaces a candidate and keeps the earliest
    ``event_time``. ATR is composed by injecting the existing
    :class:`AtrState`, which the driver must update with each bar before this
    component sees it; the declared warm-up equals the ATR component's.

    ``detection_config_hash`` is the canonical hash of the resolved run
    configuration, recomputed here so every confirmed point carries verified
    config lineage. An optional ``pinned_config_hash`` must equal the
    recomputed hash and is rejected otherwise.
    """

    def __init__(
        self,
        run_config: DetectionAnalysisConfig,
        atr: AtrState,
        *,
        instance_id: str = "swing_point",
        pinned_config_hash: str | None = None,
        pattern_definitions: Mapping[tuple[str, str], PatternDefinition] | None = None,
    ) -> None:
        selection = next(
            (
                item for item in run_config.components
                if item.component_id == "swing_point" and item.effective_instance_id == instance_id
            ),
            None,
        )
        if selection is None or not selection.enabled or selection.component_version != "1":
            raise MarketStateError("enabled SwingPoint v1 selection is required")
        parameters = {item.name: item.value for item in selection.parameters}
        if set(parameters) != _SWING_POINT_PARAMETERS:
            raise MarketStateError("SwingPoint v1 requires exactly its registered parameters")
        if not isinstance(atr, AtrState):
            raise MarketStateError("SwingPoint requires an injected AtrState component")
        if atr.run_config != run_config:
            raise MarketStateError("injected ATR run config differs from the SwingPoint run config")
        atr_period = parameters["atr_period"]
        if isinstance(atr_period, bool) or not isinstance(atr_period, int) or atr_period < 1:
            raise MarketStateError("SwingPoint atr_period must be a positive integer")
        if atr.period != atr_period:
            raise MarketStateError("SwingPoint atr_period must match the injected ATR period")
        multiplier_value = parameters["reversal_atr_multiplier"]
        if isinstance(multiplier_value, bool) or not isinstance(multiplier_value, Decimal | int):
            raise MarketStateError("SwingPoint reversal_atr_multiplier must be a decimal")
        multiplier = Decimal(multiplier_value)
        if not multiplier.is_finite() or multiplier <= 0:
            raise MarketStateError("SwingPoint reversal_atr_multiplier must be finite and > 0")
        self.instance_id = instance_id
        self.atr = atr
        self.atr_period = atr_period
        self.reversal_atr_multiplier = multiplier
        self.extreme_source = _parse_option(
            "extreme_source", parameters["extreme_source"], ExtremeSource
        )
        self.confirmation_source = _parse_option(
            "confirmation_source", parameters["confirmation_source"], ConfirmationSource
        )
        self.equal_extreme_policy = _parse_option(
            "equal_extreme_policy", parameters["equal_extreme_policy"], EqualExtremePolicy
        )
        self.freeze_atr_at_extreme = _fixed_flag(parameters, "freeze_atr_at_extreme", True)
        self.allow_same_bar_confirmation = _fixed_flag(
            parameters, "allow_same_bar_confirmation", False
        )
        self.require_alternation = _fixed_flag(parameters, "require_alternation", True)
        resolved_config_hash = detection_config_hash(
            run_config, pattern_definitions=pattern_definitions
        )
        if pinned_config_hash is not None and pinned_config_hash != resolved_config_hash:
            raise MarketStateError(
                f"pinned_config_hash {pinned_config_hash!r} does not match the resolved "
                f"detection config hash {resolved_config_hash!r}"
            )
        self.pinned_config_hash = pinned_config_hash
        self.detection_config_hash = resolved_config_hash
        super().__init__(run_config, warmup_completed_bars=atr.warmup_completed_bars)

    @property
    def confirmed_swing_points(self) -> tuple[SwingPoint, ...]:
        return tuple(self._confirmed)

    @property
    def current_bar_swing_points(self) -> tuple[SwingPoint, ...]:
        """New confirmations only, without copying the accumulated history."""
        if self._confirmed and (
            self._confirmed[-1].confirmation_bar_index == self._completed_bars - 1
        ):
            return (self._confirmed[-1],)
        return ()

    def _reset_state(self) -> None:
        self._confirmed: list[SwingPoint] = []
        self._candidate_high: _Candidate | None = None
        self._candidate_low: _Candidate | None = None
        self._opposite_high: _Candidate | None = None
        self._opposite_low: _Candidate | None = None
        self._first_index: int | None = None
        self._search_high = True
        self._search_low = True

    def _update_completed_bar(self, bar: Bar) -> None:
        # The ATR component shares the base completed-bars counter; lockstep
        # means it is always exactly one bar ahead when this hook runs.
        if self.atr._completed_bars != self._completed_bars + 1:
            raise MarketStateError("the injected ATR component must be updated with each bar first")
        if self._completed_bars + 1 < self._warmup_completed_bars:
            return
        with localcontext() as context:
            context.prec = SWING_POINT_PRECISION
            atr_value = self.atr.volatility_state.atr
            if atr_value is None:
                raise MarketStateError("ATR must be available once the declared warm-up completes")
            self._apply_bar(bar, self._completed_bars, atr_value)

    def _apply_bar(self, bar: Bar, index: int, atr_value: Decimal) -> None:
        threshold = self.reversal_atr_multiplier * atr_value
        if self._first_index is None:
            self._first_index = index
        high_replaced = self._search_high and (
            self._candidate_high is None or bar.high > self._candidate_high.price
        )
        low_replaced = self._search_low and (
            self._candidate_low is None or bar.low < self._candidate_low.price
        )
        if high_replaced:
            self._candidate_high = _Candidate(
                bar.high, bar.timestamp, atr_value, threshold, index
            )
            self._opposite_low = _Candidate(bar.low, bar.timestamp, atr_value, threshold, index)
        elif (
            self._search_high
            and self._candidate_high is not None
            and bar.high == self._candidate_high.price
        ):
            self._candidate_high = _equal_evidence(self._candidate_high, bar)
        if low_replaced:
            self._candidate_low = _Candidate(bar.low, bar.timestamp, atr_value, threshold, index)
            self._opposite_high = _Candidate(bar.high, bar.timestamp, atr_value, threshold, index)
        elif (
            self._search_low
            and self._candidate_low is not None
            and bar.low == self._candidate_low.price
        ):
            self._candidate_low = _equal_evidence(self._candidate_low, bar)
        if not high_replaced and self._opposite_low is not None:
            if bar.low < self._opposite_low.price:
                self._opposite_low = _Candidate(
                    bar.low, bar.timestamp, atr_value, threshold, index
                )
            elif bar.low == self._opposite_low.price:
                self._opposite_low = _equal_evidence(self._opposite_low, bar)
        if not low_replaced and self._opposite_high is not None:
            if bar.high > self._opposite_high.price:
                self._opposite_high = _Candidate(
                    bar.high, bar.timestamp, atr_value, threshold, index
                )
            elif bar.high == self._opposite_high.price:
                self._opposite_high = _equal_evidence(self._opposite_high, bar)
        candidate_high = self._candidate_high
        if (
            self._search_high
            and candidate_high is not None
            and candidate_high.bar_index < index
            and bar.close <= candidate_high.price - candidate_high.threshold_points
        ):
            self._confirm(SwingType.SWING_HIGH, candidate_high, bar, index, atr_value, threshold)
            return
        candidate_low = self._candidate_low
        if (
            self._search_low
            and candidate_low is not None
            and candidate_low.bar_index < index
            and bar.close >= candidate_low.price + candidate_low.threshold_points
        ):
            self._confirm(SwingType.SWING_LOW, candidate_low, bar, index, atr_value, threshold)

    def _confirm(
        self,
        swing_type: SwingType,
        candidate: _Candidate,
        bar: Bar,
        index: int,
        atr_value: Decimal,
        threshold: Decimal,
    ) -> None:
        previous = self._confirmed[-1] if self._confirmed else None
        point = SwingPoint(
            swing_index=len(self._confirmed) + 1,
            swing_type=swing_type,
            definition_id=SWING_POINT_DEFINITION_ID,
            event_time=candidate.event_time,
            event_price=candidate.price,
            detection_time=bar.timestamp,
            confirmation_close=bar.close,
            candidate_bar_index=candidate.bar_index,
            confirmation_bar_index=index,
            atr_period=self.atr_period,
            atr_at_extreme=candidate.atr_at_extreme,
            reversal_atr_multiplier=self.reversal_atr_multiplier,
            threshold_points=candidate.threshold_points,
            bars_to_confirmation=index - candidate.bar_index,
            previous_swing=(
                None
                if previous is None
                else SwingPointReference(
                    previous.swing_index,
                    previous.swing_type,
                    previous.event_time,
                    previous.event_price,
                )
            ),
            detection_config_hash=self.detection_config_hash,
        )
        self._confirmed.append(point)
        if swing_type is SwingType.SWING_HIGH:
            carried = self._opposite_low if self._opposite_low is not None else _Candidate(
                bar.low, bar.timestamp, atr_value, threshold, index
            )
            self._search_high = False
            self._search_low = True
            self._candidate_high = None
            self._candidate_low = carried
            self._opposite_low = None
            self._opposite_high = _Candidate(bar.high, bar.timestamp, atr_value, threshold, index)
        else:
            carried = self._opposite_high if self._opposite_high is not None else _Candidate(
                bar.high, bar.timestamp, atr_value, threshold, index
            )
            self._search_high = True
            self._search_low = False
            self._candidate_low = None
            self._candidate_high = carried
            self._opposite_high = None
            self._opposite_low = _Candidate(bar.low, bar.timestamp, atr_value, threshold, index)

    def _state_values(self) -> Mapping[str, object]:
        return {
            "component_id": "swing_point",
            "instance_id": self.instance_id,
            "definition_id": SWING_POINT_DEFINITION_ID,
            "atr_instance_id": self.atr.instance_id,
            "atr_period": self.atr_period,
            "reversal_atr_multiplier": self.reversal_atr_multiplier,
            "extreme_source": self.extreme_source.value,
            "confirmation_source": self.confirmation_source.value,
            "freeze_atr_at_extreme": self.freeze_atr_at_extreme,
            "allow_same_bar_confirmation": self.allow_same_bar_confirmation,
            "equal_extreme_policy": self.equal_extreme_policy.value,
            "require_alternation": self.require_alternation,
            "pinned_config_hash": self.pinned_config_hash,
            "detection_config_hash": self.detection_config_hash,
            "search_active": self._completed_bars >= self._warmup_completed_bars,
            "search_direction": self._search_direction(),
            "provisional_candidate_high": _candidate_values(
                SwingType.SWING_HIGH,
                self._candidate_high if self._search_high else self._opposite_high,
            ),
            "provisional_candidate_low": _candidate_values(
                SwingType.SWING_LOW,
                self._candidate_low if self._search_low else self._opposite_low,
            ),
            "opposite_running_high": _candidate_values(
                SwingType.SWING_HIGH, self._opposite_high if self._search_low else None
            ),
            "opposite_running_low": _candidate_values(
                SwingType.SWING_LOW, self._opposite_low if self._search_high else None
            ),
            "confirmed": tuple(_point_values(point) for point in self._confirmed),
            "confirmed_count": len(self._confirmed),
        }

    def _search_direction(self) -> str:
        if self._search_high and self._search_low:
            return "both"
        if self._search_high:
            return "high"
        return "low" if self._search_low else "none"


def _parse_option[OptionT: StrEnum](
    name: str,
    value: object,
    options: type[OptionT],
) -> OptionT:
    if isinstance(value, str):
        try:
            return options(value)
        except ValueError:
            pass
    raise MarketStateError(f"unsupported SwingPoint {name}: {value!r}")


def _fixed_flag(parameters: Mapping[str, object], name: str, supported: bool) -> bool:
    value = parameters[name]
    if value is not supported:
        raise MarketStateError(f"SwingPoint v1 supports only {name}={supported}")
    return supported


def _equal_evidence(candidate: _Candidate, bar: Bar) -> _Candidate:
    return replace(
        candidate,
        equal_extreme_count=candidate.equal_extreme_count + 1,
        last_equal_extreme_time=bar.timestamp,
    )


def _candidate_values(
    swing_type: SwingType,
    candidate: _Candidate | None,
) -> Mapping[str, object] | None:
    if candidate is None:
        return None
    return MappingProxyType({
        "status": "unconfirmed",
        "swing_type": swing_type.value,
        "event_time": candidate.event_time,
        "event_price": candidate.price,
        "candidate_bar_index": candidate.bar_index,
        "atr_at_extreme": candidate.atr_at_extreme,
        "threshold_points": candidate.threshold_points,
        "equal_extreme_count": candidate.equal_extreme_count,
        "last_equal_extreme_time": candidate.last_equal_extreme_time,
    })


def _point_values(point: SwingPoint) -> Mapping[str, object]:
    previous = point.previous_swing
    return MappingProxyType({
        "status": "confirmed",
        "swing_index": point.swing_index,
        "swing_type": point.swing_type.value,
        "definition_id": point.definition_id,
        "event_time": point.event_time,
        "event_price": point.event_price,
        "detection_time": point.detection_time,
        "confirmation_close": point.confirmation_close,
        "candidate_bar_index": point.candidate_bar_index,
        "confirmation_bar_index": point.confirmation_bar_index,
        "bars_to_confirmation": point.bars_to_confirmation,
        "atr_period": point.atr_period,
        "atr_at_extreme": point.atr_at_extreme,
        "reversal_atr_multiplier": point.reversal_atr_multiplier,
        "threshold_points": point.threshold_points,
        "previous_swing": None if previous is None else MappingProxyType({
            "swing_index": previous.swing_index,
            "swing_type": previous.swing_type.value,
            "event_time": previous.event_time,
            "event_price": previous.event_price,
        }),
        "detection_config_hash": point.detection_config_hash,
    })
