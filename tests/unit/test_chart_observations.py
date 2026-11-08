"""Chart transport must not serialize accumulated analytical histories per candle."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from market_analysis.application.chart_observations import RecordedFrame
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators.market_state import MarketEvent, MarketEventType, MarketStateFrame
from market_analysis.indicators.swing_point import SwingPoint, SwingType


def frame(history_size=1):
    timestamp = datetime(2026, 11, 6, 20, tzinfo=UTC)
    state = MarketStateFrame(
        Bar('US30', Timeframe.M1, timestamp, '100', '103', '99', '101'),
        1, 'run', 'source', 'hash', None, {}, {'ema': 'AVAILABLE'}, {
            'ema': {'component_id': 'ema', 'period': 45, 'value_ready': True, 'ema': '101'},
            'structure': {'classifications': tuple({'label': 'HH'} for _ in range(history_size))},
            'leg': {'component_id': 'trend_leg', 'active_leg': {
                'leg_index': 2, 'direction': 'UP', 'unneeded_history': ('large',) * history_size}},
        }, (),
    )
    return RecordedFrame(state, timestamp + timedelta(minutes=1))


def test_chart_size_is_independent_of_accumulated_component_history():
    small, large = frame(1), frame(10000)
    assert small.chart_payload() == large.chart_payload()
    assert len(json.dumps(large.chart_payload())) < 1000
    assert large.chart_payload()['components']['leg']['active_leg']['direction'] == 'UP'
    assert large.chart_payload()['components']['ema']['ema'] == '101'


def test_complete_canonical_frame_remains_available_lazily_for_audit():
    recorded = frame(1000)
    assert 'canonical' not in recorded.__dict__
    recorded.chart_payload()
    assert 'canonical' not in recorded.__dict__
    assert len(recorded['components']['structure']['classifications']) == 1000
    assert len(recorded['components']['leg']['active_leg']['unneeded_history']) == 1000
    assert 'canonical' in recorded.__dict__


def test_chart_projection_preserves_canonical_swing_marker_identity_and_times():
    recorded = frame()
    timestamp = recorded.frame.bar.timestamp
    point = SwingPoint(1, SwingType.SWING_HIGH, 'swing-point-v1', timestamp, Decimal('103'),
                       timestamp, Decimal('101'), 0, 1, 2, Decimal('1'), Decimal('1.5'),
                       Decimal('1.5'), 1, None)
    event = MarketEvent(MarketEventType.SWING_POINT_CONFIRMED, timestamp, timestamp,
                        0, 'swing_point', point)
    recorded = RecordedFrame(replace(recorded.frame, market_events_this_bar=(event,)),
                             recorded.available_at)
    complete = recorded['market_events'][0]
    marker = recorded.chart_payload()['market_events'][0]
    assert all(marker[key] == complete[key] for key in marker if key != 'evidence')
    assert marker['evidence']['swing_type'] == complete['evidence']['swing_type']
