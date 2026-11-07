"""Canonical bounded event inventory from the exact pinned market-state producer."""

import json
from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import datetime

from market_analysis.application.analysis_clock import verify_closed_interval
from market_analysis.application.replay_catalog import PATTERN_DEFINITIONS
from market_analysis.application.structural_outcomes import ALGORITHMS, TYPES
from market_analysis.config import DetectionAnalysisConfig, EvaluationPlan
from market_analysis.detection.records import json_value
from market_analysis.domain import BarSequence, TradingCalendar
from market_analysis.domain.aggregation import MINUTE, AnalyticalCandle, CandleAggregator
from market_analysis.domain.outcomes import CanonicalMarketEvent, OutcomeEventReference
from market_analysis.indicators.market_state import (
    MarketEvent,
    MarketStateAggregator,
    MarketStateFrame,
    market_event_semantic_ref,
)
from market_analysis.indicators.swing_point import SwingPoint
from market_analysis.indicators.trend_leg import TrendLegTransition
from market_analysis.indicators.trend_leg_qualification import (
    EndedQualification,
    QualificationEarned,
)
from market_analysis.persistence.evaluations import EvaluationRunRecord

SUPPORTED = frozenset(kind for kinds in TYPES.values() for kind in kinds)


def canonical_reference(
    frame: MarketStateFrame, event: MarketEvent, available_at: datetime
) -> OutcomeEventReference:
    semantic_ref = market_event_semantic_ref(frame, event)  # Authenticates actual emitted object.
    kind = event.event_type.value
    evidence = event.evidence
    if isinstance(evidence, SwingPoint):
        category = evidence.swing_type.value.removeprefix("SWING_")
    elif kind in TYPES["BREAK"]:
        category = "UP" if kind == "SWING_HIGH_CLOSE_BREAK" else "DOWN"
    elif isinstance(evidence, TrendLegTransition):
        category = f"{kind}_{evidence.leg.direction.value}"
    elif isinstance(evidence, QualificationEarned):
        category = f"{kind}_{evidence.source_leg.direction.value}"
    elif isinstance(evidence, EndedQualification):
        category = f"{kind}_{evidence.evidence.source_leg.direction.value}"
    else:
        raise ValueError("unsupported canonical structural event")
    return OutcomeEventReference(
        semantic_ref=semantic_ref,
        kind="market",
        dataset_revision_id=frame.dataset_revision_id,
        instrument_id=frame.bar.instrument_id,
        timeframe=frame.bar.timeframe,
        detection_config_hash=frame.detection_config_hash,
        event_time=event.event_time,
        detection_time=event.detection_time,
        available_at=available_at,
        within_bar_ordinal=event.ordinal,
        canonical_market=CanonicalMarketEvent(
            event_type=kind,
            source_instance_id=event.source_instance_id,
            category=category,
            evidence_json=json.dumps(
                json_value(asdict(evidence)), sort_keys=True, separators=(",", ":")
            ),
        ),
    )


def structural_event_inventory(
    record: EvaluationRunRecord,
    sequence: BarSequence,
    calendar: TradingCalendar,
    *,
    check_stop: Callable[[], None],
) -> dict[str, tuple[OutcomeEventReference, ...]]:
    result: dict[str, tuple[OutcomeEventReference, ...]] = {}
    selected_streams = [
        stream
        for stream in record.inputs.streams
        if any(
            (item.outcome_id, item.outcome_version) in ALGORITHMS
            for item in EvaluationPlan.from_canonical_json(stream.plan_json).outcomes
        )
    ]
    if not selected_streams:
        return result
    # All source events precede selected_end; their windows cannot outlive the last source day.
    _, limit = calendar.trading_day_bounds(
        sequence.bars[record.inputs.execution_bar_count - 1].timestamp
    )
    for stream in selected_streams:
        config = DetectionAnalysisConfig.from_canonical_json(stream.config_json)
        producer = MarketStateAggregator(
            config,
            run_id=str(stream.run_id),
            dataset_revision_id=record.inputs.dataset_revision_id,
            calendar=calendar,
            pinned_calendar_version=calendar.version,
            pinned_config_hash=stream.detection_config_hash,
            pattern_definitions=PATTERN_DEFINITIONS,
        )
        aggregator = CandleAggregator(stream.timeframe, calendar)
        events: list[OutcomeEventReference] = []

        def consume(
            candles: Iterable[AnalyticalCandle],
            producer: MarketStateAggregator = producer,
            events: list[OutcomeEventReference] = events,
        ) -> None:
            for candle in candles:
                check_stop()
                frame = producer.update(candle.bar)
                if candle.available_at >= record.inputs.selected_start:
                    events.extend(
                        canonical_reference(frame, event, candle.available_at)
                        for event in frame.market_events_this_bar
                        if event.event_type.value in SUPPORTED
                    )

        watermark = sequence.bars[0].timestamp
        for bar in sequence.bars:
            check_stop()
            if bar.timestamp >= limit:
                break
            try:
                verify_closed_interval(calendar, watermark, bar.timestamp)
            except ValueError as error:
                if not str(error).startswith("missing expected-open source minute"):
                    raise
                # Do not continue state across an unknown open minute. Every affected source
                # outcome's full-window gate reports the missing minute before using events.
                break
            consume(aggregator.advance(bar.timestamp))
            consume(aggregator.push(bar))
            watermark = bar.timestamp + MINUTE
        consume(aggregator.advance(min(watermark, limit)))
        result[str(stream.run_id)] = tuple(events)
    return result
