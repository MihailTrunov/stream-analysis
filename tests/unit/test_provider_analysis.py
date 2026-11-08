"""Provider candles drive the same detectors/replay without a confirmed calendar."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from test_provider_prices import NOW, feed
from test_provider_prices import prices as price_fixture

from market_analysis.application.analysis_sessions import AnalysisRequest, AnalysisSessionManager
from market_analysis.application.live_analysis import LiveAnalysisManager, LiveAnalysisRequest
from market_analysis.application.live_ingress import LiveIngress
from market_analysis.application.live_inputs import read_live_input
from market_analysis.application.live_retention import feed_key
from market_analysis.application.provider_analysis import observed_feed, publish_provider_source
from market_analysis.application.replay_catalog import demo_detection_config
from market_analysis.domain import Bar, HistoricalDataPage, HistoricalSource, Timeframe
from market_analysis.domain.live_data import LiveCandleUpdate
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.runs import metadata

prices = price_fixture


@pytest.fixture
def inputs(request, monkeypatch):
    manager, sources, clock, root = request.getfixturevalue('prices')
    engine = create_engine(f'sqlite:///{root / "analysis.sqlite"}')
    metadata.create_all(engine)
    monkeypatch.setenv('STREAM_ANALYSIS_DATABASE_URL', str(engine.url))
    monkeypatch.setenv('STREAM_ANALYSIS_DATA_ROOT', str(root))
    yield engine, manager, sources, clock, root
    engine.dispose()


@pytest.mark.parametrize('instrument', ['US30', 'DAX'])
@pytest.mark.parametrize('mode', ['history', 'replay'])
def test_history_and_replay_detectors_run_after_verified_calendar_expires(inputs, instrument, mode):
    engine, prices, _, clock, _ = inputs
    source = publish_provider_source(engine, prices, feed(instrument))
    session = AnalysisSessionManager()
    end = clock[0].replace(second=0, microsecond=0)
    request = AnalysisRequest.model_validate({
        'dataset_revision_id': source['dataset_revision_id'], 'instrument_id': instrument,
        'selected_start': end - timedelta(minutes=30), 'selected_end': end,
        'mode': mode, 'streams': [{'config': demo_detection_config(instrument).model_copy(
            update={'timeframe': tf}).model_dump(mode='json')}
            for tf in (Timeframe.M1, Timeframe.M5)],
    })
    state = session.launch(request)
    assert state['calendar_status'] == 'unverified'
    assert source['calendar_version'].startswith('provider-observed-v1:')
    if mode == 'replay':
        state = session.command(state['run_id'], 'step')
    current = session.current(state['run_id'])
    assert all(stream.processed > 6 for stream in current.streams)
    for stream in current.streams:
        assert stream.frames[-1]['components']['session']['calendar_verified'] is False
    view = session.view(state['run_id'], Timeframe.M1, request.selected_start, end)
    assert view['observations'] and view['calendar_status'] == 'unverified'
    assert any('calendar_unverified' in c.bar.quality_flags
               for c in current.candles[Timeframe.M1])


def test_live_detectors_advance_on_completed_provider_candles_without_calendar(inputs):
    engine, prices, _, clock, root = inputs
    base = feed()
    source = publish_provider_source(engine, prices, base, live=True)
    selected = observed_feed(base)

    class LiveSource:
        def poll_live(self, request, *, at):
            completed = (Bar('US30', Timeframe.M1, request.end - timedelta(minutes=1),
                             '100', '103', '99', '101', source_id='oanda-midpoint'),)
            bars = tuple(b for b in completed if request.start <= b.timestamp < request.end)
            return LiveCandleUpdate(HistoricalDataPage(request, bars,
                HistoricalSource('oanda', 'US30_USD', 'oanda-midpoint', 'live')), at)

        def close(self):
            pass

    ingress = LiveIngress(engine=lambda: engine, source=lambda _: LiveSource(),
                          clock=lambda: clock[0], threaded=False)
    analysis = LiveAnalysisManager(ingress)
    id = str(uuid4())
    try:
        state = analysis.connect(LiveAnalysisRequest.model_validate({
            'subscription_id': id, 'bootstrap_revision_id': source['dataset_revision_id'],
            'calendar_mode': 'provider_observed',
            'streams': [{'config': demo_detection_config('US30').model_dump(mode='json')}],
        }), selected)
        before = analysis._analysis.current(state['run_id']).streams[0].processed
        clock[0] += timedelta(minutes=1)
        ingress.poll(feed_key(selected))
        assert analysis.state(id)['status'] == 'connected'
        assert analysis._analysis.current(state['run_id']).streams[0].processed == before + 1
        # An absent timestamp is unclassified, not a fabricated candle or calendar block.
        clock[0] += timedelta(minutes=2)
        ingress.poll(feed_key(selected))
        assert analysis.state(id)['status'] == 'connected'
        assert analysis._analysis.current(state['run_id']).streams[0].processed == before + 2
        with engine.connect() as connection:
            manifest = read_live_input(connection, DatasetStore(root), state['input_id'])
        assert manifest['source_minutes'] == 2
    finally:
        analysis.close()
        ingress.close()


def test_observed_source_is_reused_without_mutating_verified_calendar(inputs):
    engine, prices, _, clock, _ = inputs
    selected = feed()
    with pytest.raises(ValueError, match='outside verified'):
        selected.calendar().derive(NOW)
    first = publish_provider_source(engine, prices, selected)
    second = publish_provider_source(engine, prices, selected)
    assert first['dataset_revision_id'] == second['dataset_revision_id']
    assert not selected.calendar().observed_only
