"""Independent causal reference and explicit adapters for existing chart drivers."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime

from parity_support import Trace
from sqlalchemy import create_engine

from market_analysis.application.autonomous_evaluation import execute_evaluation
from market_analysis.application.context_capture import load_detection_context
from market_analysis.application.evaluation_runs import create_evaluation_run
from market_analysis.application.replay_catalog import PATTERN_DEFINITIONS, detector_bindings
from market_analysis.config import (
    DetectionAnalysisConfig,
    EvaluationPlan,
    detection_config_hash,
    resolve_detection_config,
)
from market_analysis.detection import DetectorEvent, DetectorRuntime, json_value
from market_analysis.detection.context_snapshots import CONTEXT_SCHEMA_VERSION, capture_values
from market_analysis.domain import Timeframe
from market_analysis.domain.aggregation import AGGREGATION_VERSION, MINUTES, CandleAggregator
from market_analysis.domain.dataset_lineage import canonical_bar_checksum_ordered
from market_analysis.domain.evaluation import EvaluationRequest, RequestedEvaluationStream
from market_analysis.patterns.instance_context import instance_semantic_key
from market_analysis.persistence.pattern_instances import load_pattern_instance


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def without_run_lineage(value, *, lineage=False):
    if isinstance(value, dict):
        return {
            key: without_run_lineage(item, lineage=key == "lineage")
            for key, item in value.items()
            if not lineage or key != "run_id"
        }
    if isinstance(value, list):
        return [without_run_lineage(item) for item in value]
    return value


def occurrence_keys(events, runtime_key="instance_id"):
    keys, counts = {}, defaultdict(int)
    for event in events:
        identity = event[runtime_key]
        if identity not in keys:
            assert event["sequence"] == 0, "parity trace is missing an opening transition"
            pattern = event["pattern_id"], event["pattern_version"]
            keys[identity] = [
                *pattern,
                event["instrument_id"],
                event["timeframe"],
                event["event_time"],
                event["detection_time"],
                counts[pattern],
            ]
            counts[pattern] += 1
    return keys


def detector_events(events, keys, runtime_key="instance_id", persisted_ordinals=None):
    result = []
    for ordinal, event in enumerate(events):
        value = {
            key: event[key]
            for key in DetectorEvent.__dataclass_fields__
            if key not in {"run_id", "instance_id"}
        }
        result.append(
            {
                **value,
                "occurrence": keys[event[runtime_key]],
                "within_bar_ordinal": (
                    ordinal if persisted_ordinals is None else persisted_ordinals[event["event_id"]]
                ),
            }
        )
    return result


def invariants(bars, calendar, configs, start, end):
    return {
        "source_checksum": canonical_bar_checksum_ordered(bars),
        "initialization_origin": stamp(bars[0].timestamp),
        "selected_start": stamp(start),
        "available_at": stamp(end),
        "calendar_version": calendar.version,
        "calendar_id": calendar.calendar_id,
        "aggregation_version": AGGREGATION_VERSION,
        "streams": sorted(
            [
                {
                    "timeframe": config.timeframe.value,
                    "pattern": config.patterns[0].pattern_id,
                    "config_hash": detection_config_hash(
                        config, pattern_definitions=PATTERN_DEFINITIONS
                    ),
                    "definition": PATTERN_DEFINITIONS[
                        (config.patterns[0].pattern_id, config.patterns[0].pattern_version)
                    ].semantic_fingerprint(),
                    "components": config.canonical_dict()["components"],
                }
                for config in configs
            ],
            key=lambda item: (MINUTES[Timeframe(item["timeframe"])], item["pattern"]),
        ),
    }


def ordered(points):
    return sorted(
        points,
        key=lambda item: (
            item["available_at"],
            MINUTES[Timeframe(item["timeframe"])],
            item["pattern"],
        ),
    )


def reference_trace(bars, calendar, configs, start, end, input_id):
    points, occurrences = [], []
    for config in configs:
        runtime = DetectorRuntime(
            config,
            detector_bindings(config),
            run_id="reference-run",
            dataset_revision_id=input_id,
            calendar=calendar,
            pinned_calendar_version=calendar.version,
            pinned_config_hash=detection_config_hash(
                config, pattern_definitions=PATTERN_DEFINITIONS
            ),
        )
        aggregator = CandleAggregator(config.timeframe, calendar)
        results = []
        for bar in bars:
            for candle in (*aggregator.advance(bar.timestamp), *aggregator.push(bar)):
                results.append((candle, runtime.process_bar(candle.bar)))
        for candle in aggregator.advance(end):
            results.append((candle, runtime.process_bar(candle.bar)))
        events = [event.to_canonical_dict() for _, result in results for event in result.events]
        keys = occurrence_keys(events)
        for candle, result in results:
            frame = json.loads(result.frame.debug_json())
            points.append(
                {
                    "available_at": stamp(candle.available_at),
                    "timeframe": config.timeframe.value,
                    "pattern": config.patterns[0].pattern_id,
                    "bar": dict(candle.bar.to_canonical_dict()),
                    "frame": without_run_lineage(
                        {
                            key: frame[key]
                            for key in ("availability", "components", "market_events_this_bar")
                        }
                    ),
                    "events": detector_events(
                        [event.to_canonical_dict() for event in result.events], keys
                    ),
                    "contexts": [
                        {
                            "context_schema_version": CONTEXT_SCHEMA_VERSION,
                            "available_at": stamp(candle.available_at),
                            "values": json_value(
                                capture_values(
                                    result.frame,
                                    event,
                                    instance,
                                    PATTERN_DEFINITIONS[(event.pattern_id, event.pattern_version)],
                                )
                            ),
                        }
                        for event, instance in zip(
                            result.events, result.event_instances, strict=True
                        )
                    ],
                }
            )
        for instance in runtime.occurrences:
            occurrences.append(
                {
                    "occurrence": keys[instance.instance_id],
                    "state": instance.state,
                    "context": json_value(instance.context),
                    "instance_semantic_key": instance_semantic_key(
                        dataset_revision_id=input_id,
                        detection_config_hash=runtime.detection_config_hash,
                        instrument_id=config.instrument_id,
                        timeframe=config.timeframe.value,
                        definition=PATTERN_DEFINITIONS[
                            (instance.pattern_id, instance.pattern_version)
                        ],
                        occurrence_event_time=datetime.fromisoformat(keys[instance.instance_id][4]),
                        occurrence_detection_time=datetime.fromisoformat(
                            keys[instance.instance_id][5]
                        ),
                        occurrence_ordinal=keys[instance.instance_id][6],
                    ),
                }
            )
    return Trace(
        invariants(bars, calendar, configs, start, end),
        ordered(points),
        occurrences,
        execution={"run_id": "reference-run", "input_id": input_id},
    )


def session_trace(session, configs, start):
    points, occurrences = [], []
    engine = create_engine(os.environ["STREAM_ANALYSIS_DATABASE_URL"])
    try:
        with engine.connect() as connection:
            snapshots = {
                event["event_id"]: load_detection_context(connection, event["event_id"])
                for stream in session.streams
                for event in stream.events
            }
    finally:
        engine.dispose()
    for stream in session.streams:
        tf = stream.runtime.config.timeframe
        keys = occurrence_keys(stream.events, "runtime_instance_id")
        persisted_ordinals = {
            event.event_id: event.within_bar_ordinal
            for record in stream.store._instances.values()
            for event in record.detector_events
        }
        assert len(session.candles[tf]) == len(stream.frames)
        for candle, frame in zip(session.candles[tf], stream.frames, strict=True):
            events = [
                event for event in stream.events if event["available_at"] == frame["available_at"]
            ]
            points.append(
                {
                    "available_at": frame["available_at"],
                    "timeframe": tf.value,
                    "pattern": stream.runtime.config.patterns[0].pattern_id,
                    "bar": dict(candle.bar.to_canonical_dict()),
                    "frame": without_run_lineage(
                        {
                            "availability": frame["availability"],
                            "components": frame["components"],
                            "market_events_this_bar": frame["market_events"],
                        }
                    ),
                    "events": detector_events(
                        events, keys, "runtime_instance_id", persisted_ordinals
                    ),
                    "contexts": [
                        {
                            "context_schema_version": (
                                snapshots[event["event_id"]].context_schema_version
                            ),
                            "available_at": stamp(snapshots[event["event_id"]].available_at),
                            "values": json_value(snapshots[event["event_id"]].values),
                        }
                        for event in events
                    ],
                }
            )
        for (_, _, runtime_id), record in stream.store._instances.items():
            occurrences.append(
                {
                    "occurrence": keys[runtime_id],
                    "state": record.state,
                    "context": json_value(record.context),
                    "instance_semantic_key": record.instance_semantic_key,
                }
            )
    # Config invariants come from actual pinned snapshots, not the expected settings.
    actual_configs = [stream.runtime.config for stream in session.streams]
    result = Trace(
        invariants(tuple(session.bars), session.calendar, actual_configs, start, session.watermark),
        ordered(points),
        occurrences,
        execution={"run_id": session.session_id, "input_id": session.request.dataset_revision_id},
    )
    assert len(actual_configs) == len(configs)
    return result


def single_configs(settings):
    result = []
    for item in settings:
        resolved = resolve_detection_config(item.config, pattern_definitions=PATTERN_DEFINITIONS)
        result.extend(
            resolved.model_copy(update={"patterns": (pattern,)})
            for pattern in resolved.patterns
            if pattern.enabled
        )
    return result


def autonomous_trace(
    engine,
    store,
    revision,
    bars,
    calendar,
    settings,
    start,
    end,
    *,
    outcomes=(),
    definitions=None,
    chunk_size=128,
):
    """Execute the production worker driver; compare SQL records, not simulated UI output."""
    with engine.begin() as connection:
        record = create_evaluation_run(
            connection,
            EvaluationRequest(
                dataset_revision_id=revision,
                instrument_id=bars[0].instrument_id,
                selected_start=start,
                selected_end=end,
                streams=tuple(
                    RequestedEvaluationStream(
                        config=item.config,
                        plan=EvaluationPlan(
                            detection_config_hash=detection_config_hash(
                                item.config,
                                pattern_definitions=PATTERN_DEFINITIONS,
                            ),
                            context_schema_version=CONTEXT_SCHEMA_VERSION,
                            outcomes=outcomes,
                        ),
                    )
                    for item in settings
                ),
            ),
            store,
            build_id=os.environ["STREAM_ANALYSIS_BUILD_ID"],
            created_at=end,
            calendar=calendar,
            outcome_definitions=definitions or {},
        )
    observed = defaultdict(list)

    def frame(stream, candle, result, persisted):
        observed[stream.run_id].append((candle, result, persisted))

    final = execute_evaluation(
        engine,
        store,
        str(record.inputs.evaluation_id),
        chunk_size=chunk_size,
        on_frame=frame,
    )
    points, occurrences = [], []
    with engine.connect() as connection:
        for stream in final.inputs.streams:
            results = observed[stream.run_id]
            events = [
                event.to_canonical_dict() for _, result, _ in results for event in result.events
            ]
            keys = occurrence_keys(events)
            instances = {}
            for candle, result, persisted in results:
                raw = json.loads(result.frame.debug_json())
                snapshots = [
                    load_detection_context(connection, event.event_id) for event in persisted
                ]
                points.append(
                    {
                        "available_at": stamp(candle.available_at),
                        "timeframe": stream.timeframe.value,
                        "pattern": stream.pattern_id,
                        "bar": dict(candle.bar.to_canonical_dict()),
                        "frame": without_run_lineage(
                            {
                                key: raw[key]
                                for key in ("availability", "components", "market_events_this_bar")
                            }
                        ),
                        "events": [
                            {**normalized, "within_bar_ordinal": stored.within_bar_ordinal}
                            for normalized, stored in zip(
                                detector_events(
                                    [event.to_canonical_dict() for event in result.events],
                                    keys,
                                ),
                                persisted,
                                strict=True,
                            )
                        ],
                        "contexts": [
                            {
                                "context_schema_version": snapshot.context_schema_version,
                                "available_at": stamp(snapshot.available_at),
                                "values": json_value(snapshot.values),
                            }
                            for snapshot in snapshots
                        ],
                    }
                )
                for event, stored in zip(result.events, persisted, strict=True):
                    instances[event.instance_id] = stored.instance_id
            definition = PATTERN_DEFINITIONS[(stream.pattern_id, stream.pattern_version)]
            for runtime_id, instance_id in instances.items():
                stored = load_pattern_instance(connection, instance_id, definition)
                assert stored is not None
                occurrences.append(
                    {
                        "occurrence": keys[runtime_id],
                        "state": stored.state,
                        "context": json_value(stored.context),
                        "instance_semantic_key": stored.instance_semantic_key,
                    }
                )
    return Trace(
        invariants(
            bars,
            calendar,
            [
                DetectionAnalysisConfig.from_canonical_json(stream.config_json)
                for stream in final.inputs.streams
            ],
            start,
            end,
        ),
        ordered(points),
        occurrences,
        execution={
            "run_id": str(final.inputs.evaluation_id),
            "input_id": revision,
            "status": final.status.value,
            "plan_hashes": [stream.evaluation_plan_hash for stream in final.inputs.streams],
        },
    )
