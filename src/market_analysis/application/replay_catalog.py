"""Registered MVP replay profiles, detector bindings, calendars and warm-up policy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    DetectionAnalysisConfig,
    PatternSelection,
)
from market_analysis.config.oanda_uk_calendars import (
    OANDA_UK_LIVE_PROFILES,
    build_oanda_uk_calendar,
)
from market_analysis.detection import (
    COMPRESSION_V1,
    CONTINUATION_V1,
    REVERSAL_V1,
    CompressionDetector,
    ContinuationDetector,
    DetectorBinding,
    ReversalDetector,
)
from market_analysis.detection.runtime import PatternDetector
from market_analysis.domain import TradingCalendar
from market_analysis.patterns import PatternDefinition


class ReplayCatalogError(ValueError):
    """A replay uses an unsupported version or ambiguous registered binding."""


PATTERN_DEFINITIONS: dict[tuple[str, str], PatternDefinition] = {
    item.identity: item for item in (COMPRESSION_V1, CONTINUATION_V1, REVERSAL_V1)
}
DEMO_CONFIG_ID = "offline-compression-v1"
DEMO_CONFIG_VERSION = "1"


def demo_detection_config(instrument_id: str) -> DetectionAnalysisConfig:
    """Small offline fixture profile, not a recommended research configuration."""
    profile = next(
        (item for item in OANDA_UK_LIVE_PROFILES.values()
         if item.instrument_id == instrument_id),
        None,
    )
    if profile is None:
        raise ReplayCatalogError("only US30 and DAX M1 profiles are registered for MVP replay")
    return DetectionAnalysisConfig(
        instrument_id=instrument_id,
        calendar_id=profile.calendar_id,
        components=(
            ComponentSelection(
                component_id="atr", component_version="1",
                parameters=(ConfigParameter(name="period", value=2),),
            ),
            ComponentSelection(
                component_id="range_state", component_version="1",
                parameters=(
                    ConfigParameter(name="chop_period", value=3),
                    ConfigParameter(name="bandwidth_period", value=4),
                    ConfigParameter(name="compression_reference_bars", value=6),
                ),
            ),
        ),
        patterns=(PatternSelection(
            pattern_id=COMPRESSION_V1.pattern_id,
            pattern_version=COMPRESSION_V1.pattern_version,
        ),),
    )


def _one_component(config: DetectionAnalysisConfig, component_id: str) -> str:
    matches = [
        item.effective_instance_id for item in config.components
        if item.enabled and item.component_id == component_id
    ]
    if len(matches) != 1:
        raise ReplayCatalogError(
            f"registered detector requires exactly one enabled {component_id} instance"
        )
    return matches[0]


def detector_bindings(config: DetectionAnalysisConfig) -> tuple[DetectorBinding, ...]:
    """Create fresh version-matched runners for this one replay run."""
    result: list[DetectorBinding] = []
    for selected in config.patterns:
        if not selected.enabled:
            continue
        identity = selected.pattern_id, selected.pattern_version
        definition = PATTERN_DEFINITIONS.get(identity)
        if definition is None:
            raise ReplayCatalogError(f"unregistered detector version {identity}")
        detector: PatternDetector
        if definition is COMPRESSION_V1:
            detector = CompressionDetector(
                range_instance_id=_one_component(config, "range_state")
            )
        elif definition is REVERSAL_V1:
            detector = ReversalDetector(
                trend_leg_instance_id=_one_component(config, "trend_leg"),
                qualification_instance_id=_one_component(config, "trend_leg_qualification"),
                structure_instance_id=_one_component(config, "swing_structure"),
            )
        elif definition is CONTINUATION_V1:
            detector = ContinuationDetector(
                trend_leg_instance_id=_one_component(config, "trend_leg"),
                qualification_instance_id=_one_component(config, "trend_leg_qualification"),
                structure_instance_id=_one_component(config, "swing_structure"),
            )
        else:
            raise ReplayCatalogError(f"unregistered detector version {identity}")
        result.append(DetectorBinding(definition, detector, reentrant=True))
    return tuple(result)


def resolve_replay_calendar(
    instrument_id: str, calendar_id: str, version: str,
) -> TradingCalendar:
    """Resolve only the pinned UK/live profile, offline and without credentials."""
    profile = next(
        (item for item in OANDA_UK_LIVE_PROFILES.values()
         if item.instrument_id == instrument_id),
        None,
    )
    if profile is None or (profile.calendar_id, profile.version) != (calendar_id, version):
        raise ReplayCatalogError("dataset has no matching verified replay calendar version")
    return build_oanda_uk_calendar(
        provider="oanda", region="UK", environment="live",
        account="offline-replay", provider_symbol=profile.provider_symbol,
    )


def calendar_resolver(instrument_id: str) -> Callable[[str, str], TradingCalendar]:
    return lambda calendar_id, version: resolve_replay_calendar(
        instrument_id, calendar_id, version
    )


def _parameters(items: tuple[ConfigParameter, ...]) -> dict[str, Any]:
    return {item.name: item.value for item in items}


def required_warmup_bars(config: DetectionAnalysisConfig) -> int:
    """Versioned provisional MVP history minimum for resolved registered selections."""
    minimum = 0
    for selection in config.components:
        if not selection.enabled:
            continue
        key = selection.component_id, selection.component_version
        values = _parameters(selection.parameters)
        if key == ("ema", "1"):
            minimum = max(minimum, 5 * int(values["period"]))
        elif key == ("atr", "1"):
            minimum = max(minimum, 5 * int(values["period"]) + 1)
        elif key == ("range_state", "1"):
            minimum = max(
                minimum,
                int(values["chop_period"]),
                int(values["bandwidth_period"]) + int(values["compression_reference_bars"]) - 1,
            )
        elif key == ("trend_leg_qualification", "1"):
            minimum = max(minimum, int(values["min_duration_bars"]))
        elif key in {
            ("swing_point", "1"), ("swing_structure", "1"), ("trend_leg", "1")
        }:
            # Directional-change SwingPoint v1 has no left/right window width.
            minimum = max(minimum, 1)
        else:
            raise ReplayCatalogError(f"no warm-up rule for component {key}")
    for pattern in config.patterns:
        if pattern.enabled and (
            pattern.pattern_id, pattern.pattern_version
        ) == COMPRESSION_V1.identity:
            values = _parameters(pattern.parameters)
            minimum = max(minimum, int(values["confirmation_bars"]))
    return minimum
