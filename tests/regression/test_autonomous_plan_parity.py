"""Actual execution invariance under outcome-only plan changes and fresh run identities."""

from datetime import UTC, datetime
from decimal import Decimal

import test_cross_mode_parity as chart_modes
from cross_mode_support import autonomous_trace, reference_trace, single_configs
from parity_support import assert_parity

from market_analysis.application.analysis_sessions import StreamSettings
from market_analysis.application.history_ranges import HistoryFeed
from market_analysis.application.replay_catalog import PATTERN_DEFINITIONS, demo_detection_config
from market_analysis.config import (
    ConfigParameter,
    DetectionAnalysisConfig,
    OutcomeSelection,
    resolve_detection_config,
)
from market_analysis.config.oanda_uk_calendars import build_oanda_uk_calendar
from market_analysis.domain import Bar, Timeframe
from market_analysis.domain.aggregation import MINUTE
from market_analysis.domain.outcomes import OutcomeDefinition, OutputType
from market_analysis.patterns import ParameterSpec, ParameterType

storage = chart_modes.storage


def test_outcome_plan_changes_and_chunking_do_not_change_executed_x(storage):
    engine, store = storage
    beginning = datetime(2026, 11, 5, 14, tzinfo=UTC)
    bars = tuple(
        Bar(
            "US30",
            Timeframe.M1,
            beginning + index * MINUTE,
            Decimal(10 + index % 2),
            Decimal(12),
            Decimal(9),
            Decimal(10 + index % 2),
            source_id="oanda-midpoint",
        )
        for index in range(120)
    )
    start, end = bars[60].timestamp, bars[-1].timestamp + MINUTE
    calendar = build_oanda_uk_calendar(
        provider="oanda",
        region="UK",
        environment="live",
        account="parity-fixture",
        provider_symbol="US30_USD",
    )
    feed = HistoryFeed(
        instrument_id="US30",
        provider_symbol="US30_USD",
        calendar_version=calendar.version,
        account_fingerprint="f" * 64,
    )
    revision = chart_modes.publish(storage, bars, feed, end)
    settings = tuple(
        StreamSettings(
            config=DetectionAnalysisConfig.from_canonical_json(
                resolve_detection_config(
                    demo_detection_config("US30").model_copy(update={"timeframe": timeframe}),
                    pattern_definitions=PATTERN_DEFINITIONS,
                ).canonical_json(),
            )
        )
        for timeframe in (Timeframe.M1, Timeframe.M5)
    )
    definition = OutcomeDefinition(
        outcome_id="parity-fixture",
        outcome_version="1",
        output_type=OutputType.NUMERIC,
        units="points",
        parameters=(
            ParameterSpec(
                "horizon",
                ParameterType.INTEGER,
                2,
                minimum=Decimal(1),
            ),
        ),
    )
    expected = reference_trace(bars, calendar, single_configs(settings), start, end, revision)
    traces = []
    for horizon, chunk in ((2, 1), (3, 17), (2, 128)):
        actual = autonomous_trace(
            engine,
            store,
            revision,
            bars,
            calendar,
            settings,
            start,
            end,
            outcomes=(
                OutcomeSelection(
                    outcome_id=definition.outcome_id,
                    outcome_version=definition.outcome_version,
                    parameters=(ConfigParameter(name="horizon", value=horizon),),
                ),
            ),
            definitions={definition.identity: definition},
            chunk_size=chunk,
        )
        assert_parity(expected, actual)
        assert actual.execution["status"] == "DETECTION_COMPLETED"
        assert any(point["contexts"] for point in actual.checkpoints)
        traces.append(actual)
    assert len({trace.execution["run_id"] for trace in traces}) == 3
    assert traces[0].execution["plan_hashes"] != traces[1].execution["plan_hashes"]
    assert traces[0].execution["plan_hashes"] == traces[2].execution["plan_hashes"]
    assert_parity(traces[0], traces[1])
    assert_parity(traces[0], traces[2])
