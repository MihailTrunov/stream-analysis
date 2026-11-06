"""Real replay/history/captured-live paths against an independent causal driver.

Detection-time context is compared at every emitted event. EvaluationRun is
still a separate execution dependency and is not mocked by these chart modes.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from cross_mode_support import reference_trace, session_trace, single_configs
from parity_support import assert_parity
from sqlalchemy import create_engine

import market_analysis.api.app  # noqa: F401 -- register the actual persistence schema
from market_analysis.application.analysis_sessions import (
    AnalysisRequest,
    AnalysisSessionManager,
    StreamSettings,
)
from market_analysis.application.history_ranges import HistoryFeed
from market_analysis.application.live_analysis import LiveAnalysisManager, LiveAnalysisRequest
from market_analysis.application.live_ingress import LiveIngress
from market_analysis.application.live_inputs import read_live_input
from market_analysis.application.live_retention import feed_key
from market_analysis.application.replay_catalog import demo_detection_config
from market_analysis.config import (
    ComponentSelection,
    ConfigParameter,
    DetectionAnalysisConfig,
    PatternSelection,
)
from market_analysis.config.oanda_uk_calendars import (
    build_oanda_uk_calendar,
    build_oanda_uk_instrument,
)
from market_analysis.domain import (
    Bar,
    DatasetLineage,
    HistoricalDataPage,
    HistoricalSource,
    Timeframe,
    ValidationStatus,
)
from market_analysis.domain.aggregation import MINUTE, MINUTES
from market_analysis.domain.dataset_lineage import (
    BAR_CHECKSUM_VERSION,
    canonical_bar_checksum_ordered,
)
from market_analysis.domain.live_data import LiveCandleUpdate
from market_analysis.persistence.dataset_store import DATASET_FORMAT_VERSION, DatasetStore
from market_analysis.persistence.market_data import (
    DatasetMembership,
    DatasetRevision,
    register_instrument,
)
from market_analysis.persistence.runs import metadata


@pytest.fixture
def storage(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'parity.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(tmp_path / "data"))
    engine = create_engine(url)
    metadata.create_all(engine)
    yield engine, DatasetStore(tmp_path / "data")
    engine.dispose()


def publish(storage, bars, feed, end):
    engine, store = storage
    identity = str(uuid4())
    provenance = {
        key: value
        for key, value in feed.model_dump().items()
        if key
        in {
            "provider",
            "environment",
            "source_id",
            "price_component",
            "smooth",
            "account_fingerprint",
        }
    }
    provenance["symbol"] = feed.provider_symbol
    checksum = canonical_bar_checksum_ordered(bars)
    with engine.begin() as conn:
        register_instrument(conn, build_oanda_uk_instrument(feed.provider_symbol))
        store.publish(
            conn,
            DatasetRevision(
                identity,
                "parity-fixture",
                "parity-fixture",
                "oanda",
                end,
                end,
                feed.normalization_version,
                feed.calendar_version,
                DATASET_FORMAT_VERSION,
                (
                    DatasetMembership(
                        feed.instrument_id, Timeframe.M1, bars[0].timestamp, end, len(bars)
                    ),
                ),
                f"datasets/{identity}/manifest.json",
            ),
            DatasetLineage(
                identity,
                "parity-fixture",
                feed.instrument_id,
                Timeframe.M1,
                bars[0].timestamp,
                end,
                bars[0].timestamp,
                bars[-1].timestamp + MINUTE,
                len(bars),
                end,
                ValidationStatus.PASS,
                json.dumps(provenance, sort_keys=True, separators=(",", ":")),
                sha256(b"authored parity fixture").hexdigest(),
                checksum,
                BAR_CHECKSUM_VERSION,
                DATASET_FORMAT_VERSION,
            ),
            bars,
        )
    return identity


def exercise_modes(storage, bars, config, end, display=Timeframe.M1):
    engine, store = storage
    configurations = (config,) if isinstance(config, DetectionAnalysisConfig) else tuple(config)
    instrument = configurations[0].instrument_id
    symbol = "US30_USD" if instrument == "US30" else "DE30_EUR"
    calendar = build_oanda_uk_calendar(
        provider="oanda",
        region="UK",
        environment="live",
        account="parity-fixture",
        provider_symbol=symbol,
    )
    feed = HistoryFeed(
        instrument_id=instrument,
        provider_symbol=symbol,
        calendar_version=calendar.version,
        account_fingerprint="f" * 64,
    )
    split = len(bars) - 6
    cut = bars[split].timestamp
    start = cut - MINUTE
    revision = publish(storage, bars, feed, end)
    seed = publish(storage, bars[:split], feed, cut)
    settings = tuple(StreamSettings(config=item) for item in configurations)
    configs = single_configs(settings)
    traces = []
    for mode in ("replay", "history"):
        manager = AnalysisSessionManager()
        state = manager.launch(
            AnalysisRequest(
                dataset_revision_id=revision,
                instrument_id=instrument,
                selected_start=start,
                selected_end=end,
                streams=settings,
                mode=mode,
                chart_timeframe=display,
            )
        )
        while state["has_next"]:
            state = manager.command(state["run_id"], "step", display)
        session = manager.current(state["run_id"])
        actual = session_trace(session, configs, start)
        expected = reference_trace(bars, calendar, configs, start, end, revision)
        assert_parity(expected, actual)
        traces.append(actual)
    assert_parity(traces[0], traces[1])

    class Source:
        def poll_live(self, request, *, at):
            forming = (
                replace(bars[-1], timestamp=end, is_complete=False)
                if calendar.derive(end).is_open
                else None
            )
            return LiveCandleUpdate(
                HistoricalDataPage(
                    request,
                    tuple(bar for bar in bars if request.start <= bar.timestamp < request.end),
                    HistoricalSource("oanda", symbol, "oanda-midpoint", "live"),
                ),
                at,
                forming,
            )

        def close(self):
            pass

    hub = LiveIngress(
        engine=lambda: engine,
        source=lambda _: Source(),
        clock=lambda: end + timedelta(seconds=20),
        threaded=False,
    )
    manager = LiveAnalysisManager(hub)
    try:
        state = manager.connect(
            LiveAnalysisRequest(
                subscription_id=uuid4(), bootstrap_revision_id=seed, streams=settings
            ),
            feed,
        )
        for _ in range(8):
            hub.poll(feed_key(feed))
            state = manager.state(state["subscription_id"])
            if datetime.fromisoformat(state["available_at"]) == end:
                break
        assert state["status"] == "connected", state
        assert datetime.fromisoformat(state["available_at"]) == end
        session = manager._analysis.current(state["run_id"])
        actual = session_trace(session, configs, start)
        expected = reference_trace(bars, calendar, configs, start, end, state["input_id"])
        assert_parity(expected, actual)
        hub.poll(feed_key(feed))
        assert_parity(actual, session_trace(session, configs, start))
        manager.disconnect(state["subscription_id"])
        with engine.connect() as conn:
            verified = read_live_input(conn, store, state["input_id"])
            assert verified["source_minutes"] == len(bars) - split
        traces.append(actual)
    finally:
        manager.close()
        hub.close()
    return traces


def component_config(pattern):
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/market_state/integrated_chain_v1.json").read_text()
    )
    components = tuple(
        ComponentSelection(
            component_id=item["id"],
            component_version=item["version"],
            parameters=tuple(
                ConfigParameter(
                    name=name,
                    value=(Decimal(value["decimal"]) if isinstance(value, dict) else value),
                )
                for name, value in item["parameters"].items()
            ),
        )
        for item in fixture["components"]
    )
    components = tuple(
        item.model_copy(
            update={
                "parameters": (
                    ConfigParameter(name="chop_period", value=3),
                    ConfigParameter(name="bandwidth_period", value=4),
                    ConfigParameter(name="compression_reference_bars", value=6),
                )
            }
        )
        if item.component_id == "range_state"
        else item
        for item in components
    )
    return DetectionAnalysisConfig(
        instrument_id="US30",
        calendar_id="oanda-uk-live-US30_USD",
        components=components,
        patterns=(PatternSelection(pattern_id=pattern, pattern_version="1"),),
    )


@pytest.mark.parametrize(
    "pattern,finish",
    [
        ("EMA_STRUCTURE_REVERSAL_V1", ("70", "110", "69")),
        ("EMA_RECLAIM_STRUCTURE_CONTINUATION_V1", ("150", "160", "119")),
    ],
)
def test_curated_detector_and_complete_component_chain(storage, pattern, finish):
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/market_state/integrated_chain_v1.json").read_text()
    )
    triples = (
        [("100", "101", "99")] * 30
        + [tuple(item) for item in fixture["bars_close_high_low"][:15]]
        + [finish]
    )
    beginning = datetime(2026, 11, 5, 14, tzinfo=UTC)
    bars = tuple(
        Bar(
            "US30",
            Timeframe.M1,
            beginning + index * MINUTE,
            close,
            high,
            low,
            close,
            source_id="oanda-midpoint",
        )
        for index, (close, high, low) in enumerate(triples)
    )
    traces = exercise_modes(storage, bars, component_config(pattern), bars[-1].timestamp + MINUTE)
    for trace in traces:
        events = [event for point in trace.checkpoints for event in point["events"]]
        assert any(event["to_state"] == "CANDIDATE" for event in events)
        assert any(event["to_state"] == "CONFIRMED" for event in events)
        assert set(trace.checkpoints[0]["frame"]["components"]) == {
            "session",
            "ema",
            "atr",
            "swing_point",
            "swing_structure",
            "trend_leg",
            "trend_leg_qualification",
            "range_state",
        }
        assert trace.occurrences


@pytest.mark.parametrize(
    "instrument,timeframe",
    [
        *(("US30", timeframe) for timeframe in Timeframe),
        ("DAX", Timeframe.M1),
        ("DAX", Timeframe.H4),
        ("DAX", Timeframe.D1),
    ],
)
def test_compression_analytical_timeframes_and_long_dst_source(storage, instrument, timeframe):
    config = demo_detection_config(instrument).model_copy(update={"timeframe": timeframe})
    calendar = build_oanda_uk_calendar(
        provider="oanda",
        region="UK",
        environment="live",
        account="parity-fixture",
        provider_symbol="US30_USD" if instrument == "US30" else "DE30_EUR",
    )
    end = datetime(2026, 11, 5, 23, tzinfo=UTC)
    cursor = datetime(2026, 10, 18, 22, tzinfo=UTC)
    all_bars = []
    while cursor < end:
        if calendar.derive(cursor).is_open:
            close = Decimal(10 + len(all_bars) % 2)
            all_bars.append(
                Bar(
                    instrument,
                    Timeframe.M1,
                    cursor,
                    close,
                    Decimal(12),
                    Decimal(9),
                    close,
                    source_id="oanda-midpoint",
                )
            )
        cursor += MINUTE
    bars = tuple(all_bars[-(12 * MINUTES[timeframe] + 60) :])
    traces = exercise_modes(storage, bars, config, end, display=timeframe)
    for trace in traces:
        assert any(point["events"] for point in trace.checkpoints)
        assert trace.occurrences
        if timeframe is Timeframe.D1:
            assert len(bars) > 10_000
            assert trace.checkpoints[-1]["available_at"] == "2026-11-05T23:00:00Z"


def test_concurrent_m1_and_m5_streams_share_source_but_keep_independent_state(storage):
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
    config = demo_detection_config("US30")
    traces = exercise_modes(
        storage,
        bars,
        (config, config.model_copy(update={"timeframe": Timeframe.M5})),
        bars[-1].timestamp + MINUTE,
        display=Timeframe.M5,
    )
    for trace in traces:
        assert {point["timeframe"] for point in trace.checkpoints} == {"1m", "5m"}
        assert len(trace.invariants["streams"]) == 2
