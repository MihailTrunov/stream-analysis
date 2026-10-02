import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CandlestickChart, type OverlayVisibility } from './CandlestickChart';
import type { ReplayBar, ReplayDetectorEvent, ReplayObservation } from './replay';

const first = '2026-01-05T12:11:00Z';
const second = '2026-01-05T12:12:00Z';
const bars: ReplayBar[] = [
  { timestamp: first, open: '100', high: '110', low: '90', close: '105' },
  { timestamp: second, open: '105', high: '108', low: '95', close: '98' },
];
const visibility: OverlayVisibility = { ema: true, trend: true, swing: true, range: true, session: true };
const initial: ReplayObservation = {
  timestamp: first, availability: { ema: 'AVAILABLE' },
  components: {
    ema: { component_id: 'ema', period: 45, value_ready: true, ema: '102' },
    session: { trading_date: '2026-01-05', session_name: 'regular', local_timestamp: first },
  }, market_events: [],
};
const confirmed: ReplayObservation = {
  timestamp: second, availability: { trend_leg: 'ACTIVE' },
  components: {
    ema: { component_id: 'ema', period: 45, value_ready: true, ema: '101' },
    trend_leg: { component_id: 'trend_leg', active_leg: { leg_index: 2, direction: 'UP' } },
    range_state: { component_id: 'range_state', compression_score: '82', compression_regime: 'COMPRESSED' },
    session: { trading_date: '2026-01-05', session_name: 'regular', local_timestamp: second },
  },
  market_events: [{
    ordinal: 0, event_type: 'SWING_POINT_CONFIRMED', event_time: first,
    detection_time: second, source_instance_id: 'swing_point',
    evidence: { swing_type: 'SWING_HIGH', event_price: '110' },
  }],
};
const transition: ReplayDetectorEvent = {
  run_id: 'run', dataset_revision_id: 'dataset', instrument_id: 'US30', timeframe: '1m',
  detection_config_hash: 'hash', pattern_id: 'PATTERN', pattern_version: '1',
  pattern_name: 'Fixture pattern', definition_fingerprint: 'fingerprint',
  build_id: 'build', code_revision: 'revision', code_dirty: false, code_capture_status: 'captured',
  instance_id: 'occurrence-1',
  sequence: 0, from_state: 'CANDIDATE', to_state: 'CONFIRMED', trigger_id: 'confirmed',
  event_time: first, detection_time: second, rationale: { source: 'fixture' }, emission_order: 0,
};

describe('canonical market-state overlays', () => {
  it('does not show a backdated swing before its detection bar', () => {
    const before = renderToStaticMarkup(<CandlestickChart bars={bars.slice(0, 1)} observations={[initial]}
      cursorTime={first} overlays={visibility} />);
    expect(before).toContain('data-testid="ema-overlay"');
    expect(before).toContain('data-period="45"');
    expect(before).not.toContain('data-testid="swing-marker"');
    const after = renderToStaticMarkup(<CandlestickChart bars={bars} observations={[initial, confirmed]}
      cursorTime={second} overlays={visibility} />);
    expect(after).toContain('data-testid="swing-marker"');
    expect(after).toContain(`data-detection-time="${second}"`);
    expect(after).toContain('data-testid="trend-leg-overlay"');
    expect(after).toContain('data-testid="range-band"');
    expect(after).toContain('data-testid="session-marker"');
  });

  it('hides only selected presentation layers, preserving candles', () => {
    const html = renderToStaticMarkup(<CandlestickChart bars={bars} observations={[initial, confirmed]}
      cursorTime={second} overlays={{ ...visibility, swing: false, ema: false }} />);
    expect(html).not.toContain('data-testid="swing-marker"');
    expect(html).not.toContain('data-testid="ema-overlay"');
    expect(html.match(/data-testid="candle"/g)).toHaveLength(2);
  });

  it('renders each emitted lifecycle transition once at detection time, including same-bar invalidation', () => {
    const invalidated: ReplayDetectorEvent = { ...transition, sequence: 1,
      from_state: 'CONFIRMED', to_state: 'INVALIDATED', trigger_id: 'invalidated', emission_order: 1 };
    const before = renderToStaticMarkup(<CandlestickChart bars={bars.slice(0, 1)} observations={[initial]}
      events={[transition, invalidated]} cursorTime={first} overlays={visibility} />);
    expect(before).not.toContain('data-testid="pattern-annotation"');
    const after = renderToStaticMarkup(<CandlestickChart bars={bars} observations={[initial, confirmed]}
      events={[transition, invalidated]} selectedEventOrder={1} cursorTime={second} overlays={visibility} />);
    expect(after.match(/data-testid="pattern-annotation"/g)).toHaveLength(2);
    expect(after).toContain('data-instance="occurrence-1" data-sequence="0"');
    expect(after).toContain('data-instance="occurrence-1" data-sequence="1"');
    expect(after).toContain('data-emission-order="1"');
    expect(after).toContain('data-selected="true"');
  });
});
