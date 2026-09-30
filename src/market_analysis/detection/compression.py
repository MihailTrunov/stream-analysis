"""SCRUM-84 direction-neutral Range Compression v1 detector.

Only canonical RangeState observations enter the rule; later breakout and
outcome information never participates. Bind with ``reentrant=True`` so
invalidated or completed episodes remain inspectable as separate occurrences.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from hashlib import sha256
from typing import cast

from market_analysis.patterns import (
    ConditionGroup,
    ContextFieldSpec,
    ParameterSpec,
    ParameterType,
    PatternDefinition,
    TransitionSpec,
)

from .inputs import DetectorInput
from .records import DetectorOutput, TransitionIntent

_CONTEXT = Context(prec=34)

COMPRESSION_V1 = PatternDefinition(
    pattern_id="RANGE_COMPRESSION_V1",
    pattern_version="1",
    name="Range Compression v1",
    description="Direction-neutral RangeState persistence and release with hysteresis.",
    required_components=("range_state",),
    required_market_events=(),
    parameters=(
        ParameterSpec(
            "compression_entry_score",
            ParameterType.DECIMAL,
            Decimal(80),
            minimum=Decimal(0),
            maximum=Decimal(100),
        ),
        ParameterSpec(
            "choppiness_entry_score",
            ParameterType.DECIMAL,
            Decimal("61.8"),
            minimum=Decimal(0),
            maximum=Decimal(100),
        ),
        ParameterSpec(
            "confirmation_bars",
            ParameterType.INTEGER,
            5,
            minimum=Decimal(2),
        ),
        ParameterSpec(
            "compression_release_score",
            ParameterType.DECIMAL,
            Decimal(60),
            minimum=Decimal(0),
            maximum=Decimal(100),
        ),
        ParameterSpec(
            "choppiness_release_score",
            ParameterType.DECIMAL,
            Decimal(50),
            minimum=Decimal(0),
            maximum=Decimal(100),
        ),
        ParameterSpec(
            "require_all_confirmation_bars",
            ParameterType.BOOLEAN,
            True,
            supported_values=(True,),
        ),
        ParameterSpec(
            "require_bounded_range",
            ParameterType.BOOLEAN,
            False,
            supported_values=(False,),
        ),
        ParameterSpec(
            "predict_breakout_direction",
            ParameterType.BOOLEAN,
            False,
            supported_values=(False,),
        ),
    ),
    lifecycle_states=("INACTIVE", "CANDIDATE", "ACTIVE", "INVALIDATED", "COMPLETED"),
    transitions=(
        TransitionSpec("INACTIVE", "CANDIDATE", "compression_entry"),
        TransitionSpec("CANDIDATE", "ACTIVE", "persistence_confirmed"),
        TransitionSpec("CANDIDATE", "INVALIDATED", "candidate_interrupted"),
        TransitionSpec("ACTIVE", "COMPLETED", "compression_released"),
    ),
    condition_groups=(
        ConditionGroup("entry", ("compression_entry",)),
        ConditionGroup("persistence", ("persistence_confirmed", "candidate_interrupted")),
        ConditionGroup("release", ("compression_released",)),
    ),
    simultaneous_precedence=("persistence_confirmed", "candidate_interrupted"),
    context_schema=(
        ContextFieldSpec("candidate_started_at", "datetime"),
        ContextFieldSpec("candidate_bar_index", "int"),
        ContextFieldSpec("qualifying_count", "int"),
        ContextFieldSpec("source_range_ref", "event_ref"),
        ContextFieldSpec("source_range_version", "string"),
        ContextFieldSpec("compression_sum", "decimal"),
        ContextFieldSpec("compression_peak", "decimal"),
        ContextFieldSpec("choppiness_sum", "decimal"),
        ContextFieldSpec("choppiness_peak", "decimal"),
        ContextFieldSpec("minimum_bandwidth", "decimal"),
        ContextFieldSpec("last_compression_score", "decimal"),
        ContextFieldSpec("last_choppiness_score", "decimal"),
        ContextFieldSpec("confirmation_at", "datetime"),
        ContextFieldSpec("confirmation_bar_index", "int"),
        ContextFieldSpec("active_observation_count", "int"),
        ContextFieldSpec("invalidated_cause", "string"),
        ContextFieldSpec("release_cause", "string"),
    ),
    rationale_condition_ids=(
        "compression_entry",
        "persistence_confirmed",
        "candidate_interrupted",
        "compression_released",
    ),
    terminal_states=("INVALIDATED", "COMPLETED"),
    rationale_schema_version="detector-evidence-v1",
)


class CompressionDetector:
    """Add candidate persistence and release hysteresis to canonical RangeState."""

    def __init__(self, *, range_instance_id: str = "range_state") -> None:
        if not isinstance(range_instance_id, str) or not range_instance_id.strip():
            raise ValueError("range_instance_id must be non-empty")
        self.range_instance_id = range_instance_id

    def reset(self) -> None:
        """All episode state belongs to the runtime-owned PatternInstance."""

    def process_bar(self, bar_input: DetectorInput) -> DetectorOutput:
        if bar_input.definition is not COMPRESSION_V1 and (
            bar_input.definition.semantic_fingerprint() != COMPRESSION_V1.semantic_fingerprint()
        ):
            raise ValueError("CompressionDetector requires the Range Compression v1 definition")
        parameters = bar_input.parameters
        if not (
            cast(Decimal, parameters["compression_release_score"])
            < cast(Decimal, parameters["compression_entry_score"])
            and cast(Decimal, parameters["choppiness_release_score"])
            < cast(Decimal, parameters["choppiness_entry_score"])
        ):
            raise ValueError("release thresholds must be below entry thresholds")
        state = bar_input.frame.components.get(self.range_instance_id)
        if state is None or state.get("component_id") != "range_state":
            raise ValueError("bound RangeState component is absent or has the wrong family")
        if state.get("definition_version") != "1":
            raise ValueError("Range Compression v1 requires RangeState v1")
        available = bar_input.frame.availability.get(self.range_instance_id) == "AVAILABLE"
        compression = state.get("compression_score")
        chop = state.get("choppiness_score")
        bandwidth = state.get("bandwidth")
        percentile = state.get("bandwidth_percentile")
        if available and (not isinstance(compression, Decimal) or not isinstance(chop, Decimal)):
            raise ValueError("available RangeState lacks numeric compression/CHOP scores")
        observation = _Observation(
            available=available,
            availability_status=bar_input.frame.availability.get(self.range_instance_id, "ABSENT"),
            compression=compression if isinstance(compression, Decimal) else None,
            chop=chop if isinstance(chop, Decimal) else None,
            bandwidth=bandwidth if isinstance(bandwidth, Decimal) else None,
            bandwidth_percentile=percentile if isinstance(percentile, Decimal) else None,
            compression_regime=str(state.get("compression_regime", "UNAVAILABLE")),
            choppiness_regime=str(state.get("choppiness_regime", "UNAVAILABLE")),
        )
        if bar_input.instance.state == "INACTIVE":
            return self._entry(bar_input, observation)
        if bar_input.instance.state == "CANDIDATE":
            return self._candidate(bar_input, observation)
        if bar_input.instance.state == "ACTIVE":
            return self._active(bar_input, observation)
        return DetectorOutput(bar_input.instance)

    def _entry(self, bar_input: DetectorInput, observed: _Observation) -> DetectorOutput:
        if not _qualifies(bar_input, observed):
            return DetectorOutput(bar_input.instance)
        assert observed.compression is not None and observed.chop is not None
        context: dict[str, object] = {
            "candidate_started_at": bar_input.detection_time,
            "candidate_bar_index": bar_input.completed_bars,
            "qualifying_count": 1,
            "source_range_ref": _range_ref(bar_input, self.range_instance_id),
            "source_range_version": "1",
            "compression_sum": observed.compression,
            "compression_peak": observed.compression,
            "choppiness_sum": observed.chop,
            "choppiness_peak": observed.chop,
            "last_compression_score": observed.compression,
            "last_choppiness_score": observed.chop,
        }
        if observed.bandwidth is not None:
            context["minimum_bandwidth"] = observed.bandwidth
        intent = _intent(
            bar_input,
            "INACTIVE",
            "CANDIDATE",
            "compression_entry",
            bar_input.detection_time,
            _features(bar_input, observed, context),
            _typed("integer", 1),
            _typed("integer", bar_input.parameters["confirmation_bars"]),
            "<",
        )
        return DetectorOutput(
            replace(bar_input.instance, state="CANDIDATE", context=context), (intent,)
        )

    def _candidate(self, bar_input: DetectorInput, observed: _Observation) -> DetectorOutput:
        context = dict(bar_input.instance.context)
        if not _qualifies(bar_input, observed):
            cause = _interruption_cause(bar_input, observed)
            context["invalidated_cause"] = cause
            if observed.compression is not None:
                context["last_compression_score"] = observed.compression
            if observed.chop is not None:
                context["last_choppiness_score"] = observed.chop
            intent = _intent(
                bar_input,
                "CANDIDATE",
                "INVALIDATED",
                "candidate_interrupted",
                bar_input.detection_time,
                _features(bar_input, observed, context),
                _typed("string", cause),
                None,
                "PRESENT",
            )
            return DetectorOutput(
                replace(bar_input.instance, state="INVALIDATED", context=context), (intent,)
            )
        _accumulate(context, observed)
        count = cast(int, context["qualifying_count"])
        if count < cast(int, bar_input.parameters["confirmation_bars"]):
            return DetectorOutput(replace(bar_input.instance, context=context))
        context["confirmation_at"] = bar_input.detection_time
        context["confirmation_bar_index"] = bar_input.completed_bars
        context["active_observation_count"] = 1
        intent = _intent(
            bar_input,
            "CANDIDATE",
            "ACTIVE",
            "persistence_confirmed",
            cast(datetime, context["candidate_started_at"]),
            _features(bar_input, observed, context),
            _typed("integer", count),
            _typed("integer", bar_input.parameters["confirmation_bars"]),
            "=",
        )
        return DetectorOutput(
            replace(bar_input.instance, state="ACTIVE", context=context), (intent,)
        )

    def _active(self, bar_input: DetectorInput, observed: _Observation) -> DetectorOutput:
        if not observed.available or observed.compression is None or observed.chop is None:
            return DetectorOutput(bar_input.instance)
        parameters = bar_input.parameters
        release_compression = observed.compression < cast(
            Decimal, parameters["compression_release_score"]
        )
        release_chop = observed.chop < cast(Decimal, parameters["choppiness_release_score"])
        context = dict(bar_input.instance.context)
        if release_compression or release_chop:
            cause = (
                "BOTH"
                if release_compression and release_chop
                else "COMPRESSION_RELEASE"
                if release_compression
                else "CHOPPINESS_RELEASE"
            )
            context["release_cause"] = cause
            context["last_compression_score"] = observed.compression
            context["last_choppiness_score"] = observed.chop
            intent = _intent(
                bar_input,
                "ACTIVE",
                "COMPLETED",
                "compression_released",
                bar_input.detection_time,
                _features(bar_input, observed, context),
                _typed("string", cause),
                None,
                "PRESENT",
            )
            return DetectorOutput(
                replace(bar_input.instance, state="COMPLETED", context=context), (intent,)
            )
        _accumulate(context, observed, candidate=False)
        return DetectorOutput(replace(bar_input.instance, context=context))


class _Observation:
    __slots__ = (
        "available",
        "availability_status",
        "compression",
        "chop",
        "bandwidth",
        "bandwidth_percentile",
        "compression_regime",
        "choppiness_regime",
    )

    def __init__(
        self,
        *,
        available: bool,
        availability_status: str,
        compression: Decimal | None,
        chop: Decimal | None,
        bandwidth: Decimal | None,
        bandwidth_percentile: Decimal | None,
        compression_regime: str,
        choppiness_regime: str,
    ) -> None:
        self.available = available
        self.availability_status = availability_status
        self.compression = compression
        self.chop = chop
        self.bandwidth = bandwidth
        self.bandwidth_percentile = bandwidth_percentile
        self.compression_regime = compression_regime
        self.choppiness_regime = choppiness_regime


def _qualifies(bar_input: DetectorInput, observed: _Observation) -> bool:
    return (
        observed.available
        and observed.compression is not None
        and observed.chop is not None
        and observed.compression >= cast(Decimal, bar_input.parameters["compression_entry_score"])
        and observed.chop > cast(Decimal, bar_input.parameters["choppiness_entry_score"])
    )


def _interruption_cause(bar_input: DetectorInput, observed: _Observation) -> str:
    if not observed.available:
        return "RANGE_STATE_UNAVAILABLE"
    assert observed.compression is not None and observed.chop is not None
    compression_failed = observed.compression < cast(
        Decimal, bar_input.parameters["compression_entry_score"]
    )
    chop_failed = observed.chop <= cast(Decimal, bar_input.parameters["choppiness_entry_score"])
    if compression_failed and chop_failed:
        return "BOTH_ENTRY_AXES_FAILED"
    return "COMPRESSION_ENTRY_FAILED" if compression_failed else "CHOPPINESS_ENTRY_FAILED"


def _accumulate(
    context: dict[str, object], observed: _Observation, *, candidate: bool = True
) -> None:
    assert observed.compression is not None and observed.chop is not None
    counter = "qualifying_count" if candidate else "active_observation_count"
    context[counter] = cast(int, context[counter]) + 1
    with localcontext(_CONTEXT):
        context["compression_sum"] = (
            cast(Decimal, context["compression_sum"]) + observed.compression
        )
        context["choppiness_sum"] = cast(Decimal, context["choppiness_sum"]) + observed.chop
    context["compression_peak"] = max(
        cast(Decimal, context["compression_peak"]), observed.compression
    )
    context["choppiness_peak"] = max(cast(Decimal, context["choppiness_peak"]), observed.chop)
    context["last_compression_score"] = observed.compression
    context["last_choppiness_score"] = observed.chop
    if observed.bandwidth is not None:
        previous = context.get("minimum_bandwidth")
        context["minimum_bandwidth"] = (
            min(previous, observed.bandwidth)
            if isinstance(previous, Decimal)
            else observed.bandwidth
        )


def _range_ref(bar_input: DetectorInput, instance_id: str) -> str:
    payload = {
        "version": "range-state-source-v1",
        "dataset_revision_id": bar_input.dataset_revision_id,
        "detection_config_hash": bar_input.detection_config_hash,
        "instrument_id": bar_input.instrument_id,
        "timeframe": bar_input.timeframe.value,
        "range_instance_id": instance_id,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _typed(kind: str, value: object) -> Mapping[str, object]:
    if kind == "decimal":
        value = format(cast(Decimal, value), "f")
    elif kind == "datetime":
        value = cast(datetime, value).astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {"type": kind, "value": value}


def _features(
    bar_input: DetectorInput, observed: _Observation, context: Mapping[str, object]
) -> dict[str, Mapping[str, object]]:
    features: dict[str, Mapping[str, object]] = {
        "source_range_ref": _typed("event_ref", context["source_range_ref"]),
        "source_range_version": _typed("string", context["source_range_version"]),
        "range_available": _typed("boolean", observed.available),
        "range_availability_status": _typed("string", observed.availability_status),
        "candidate_started_at": _typed("datetime", context["candidate_started_at"]),
        "qualifying_count": _typed("integer", context["qualifying_count"]),
        "compression_entry_score": _typed(
            "decimal", bar_input.parameters["compression_entry_score"]
        ),
        "choppiness_entry_score": _typed("decimal", bar_input.parameters["choppiness_entry_score"]),
        "confirmation_bars": _typed("integer", bar_input.parameters["confirmation_bars"]),
        "compression_release_score": _typed(
            "decimal", bar_input.parameters["compression_release_score"]
        ),
        "choppiness_release_score": _typed(
            "decimal", bar_input.parameters["choppiness_release_score"]
        ),
        "compression_regime": _typed("string", observed.compression_regime),
        "choppiness_regime": _typed("string", observed.choppiness_regime),
        "compression_peak": _typed("decimal", context["compression_peak"]),
        "choppiness_peak": _typed("decimal", context["choppiness_peak"]),
        "compression_entry_pass": _typed(
            "boolean",
            observed.available
            and observed.compression is not None
            and observed.compression
            >= cast(Decimal, bar_input.parameters["compression_entry_score"]),
        ),
        "choppiness_entry_pass": _typed(
            "boolean",
            observed.available
            and observed.chop is not None
            and observed.chop > cast(Decimal, bar_input.parameters["choppiness_entry_score"]),
        ),
    }
    for name, value in (
        ("compression_score", observed.compression),
        ("choppiness_score", observed.chop),
        ("bandwidth", observed.bandwidth),
        ("bandwidth_percentile", observed.bandwidth_percentile),
        ("minimum_bandwidth", context.get("minimum_bandwidth")),
    ):
        if isinstance(value, Decimal):
            features[name] = _typed("decimal", value)
    count = cast(int, context["qualifying_count"])
    if "active_observation_count" in context:
        # The confirmation bar is present in both counts; count it once.
        count += cast(int, context["active_observation_count"]) - 1
    if count:
        features["observed_bar_count"] = _typed("integer", count)
        with localcontext(_CONTEXT):
            features["mean_compression_score"] = _typed(
                "decimal", cast(Decimal, context["compression_sum"]) / count
            )
            features["mean_choppiness_score"] = _typed(
                "decimal", cast(Decimal, context["choppiness_sum"]) / count
            )
    if "invalidated_cause" in context:
        features["invalidated_cause"] = _typed("string", context["invalidated_cause"])
    if "confirmation_at" in context:
        features["confirmation_at"] = _typed("datetime", context["confirmation_at"])
        features["bars_to_confirmation"] = _typed(
            "integer",
            cast(int, context["confirmation_bar_index"])
            - cast(int, context["candidate_bar_index"])
            + 1,
        )
        features["active_duration_bars"] = _typed(
            "integer", bar_input.completed_bars - cast(int, context["confirmation_bar_index"])
        )
        features["active_duration_seconds"] = _typed(
            "integer",
            int(
                (
                    bar_input.detection_time - cast(datetime, context["confirmation_at"])
                ).total_seconds()
            ),
        )
    if "release_cause" in context:
        features["release_cause"] = _typed("string", context["release_cause"])
        features["release_at"] = _typed("datetime", bar_input.detection_time)
    session = bar_input.frame.components.get("session")
    if session is not None and bar_input.frame.availability.get("session") == "AVAILABLE":
        for name in ("session_name", "trading_date"):
            value = session.get(name)
            if isinstance(value, str):
                features[name] = _typed("string", value)
        local_timestamp = session.get("local_timestamp")
        if isinstance(local_timestamp, datetime):
            features["session_local_timestamp"] = _typed("string", local_timestamp.isoformat())
    for instance_id, component in bar_input.frame.components.items():
        if component.get("component_id") != "atr":
            continue
        atr = component.get("atr")
        if bar_input.frame.availability.get(instance_id) == "AVAILABLE" and isinstance(
            atr, Decimal
        ):
            features[f"atr_{instance_id}"] = _typed("decimal", atr)
    return features


def _intent(
    bar_input: DetectorInput,
    from_state: str,
    to_state: str,
    trigger: str,
    event_time: datetime,
    features: Mapping[str, Mapping[str, object]],
    value: Mapping[str, object],
    threshold: Mapping[str, object] | None,
    operator: str,
) -> TransitionIntent:
    return TransitionIntent(
        pattern_id=bar_input.pattern_id,
        pattern_version=bar_input.pattern_version,
        instance_id=bar_input.instance.instance_id,
        from_state=from_state,
        to_state=to_state,
        trigger_id=trigger,
        event_time=event_time,
        detection_time=bar_input.detection_time,
        rationale={
            "schema": "detector-evidence-v1",
            "condition": trigger,
            "source_market_event_refs": (),
            "items": (
                {
                    "condition_id": trigger,
                    "status": "PASS",
                    "value": value,
                    "operator": operator,
                    "threshold": threshold,
                    "units": None,
                    "source_refs": (),
                    "features": features,
                },
            ),
        },
    )
