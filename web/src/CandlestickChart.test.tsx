import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { buildChartModel } from './chartModel';
import { CandlestickChart, type OverlayVisibility } from './CandlestickChart';
import type { ReplayBar, ReplayDetectorEvent, ReplayObservation } from './replay';

const first = '2026-01-05T12:11:00Z';
const second = '2026-01-05T12:12:00Z';
const third = '2026-01-05T12:13:00Z';
const bars: ReplayBar[] = [
  { timestamp: first, open: '100', high: '110', low: '90', close: '105' },
  { timestamp: second, open: '105', high: '108', low: '95', close: '98' },
  { timestamp: third, open: '98', high: '102', low: '94', close: '99' },
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
  }, market_events: [{
    ordinal: 0, event_type: 'SWING_POINT_CONFIRMED', event_time: first,
    detection_time: second, source_instance_id: 'swing_point',
    evidence: { swing_type: 'SWING_HIGH', event_price: '110' },
  }],
};
const transition: ReplayDetectorEvent = {
  event_id: 'event-0', runtime_instance_id: 'runtime-occurrence-1',
  run_id: 'run', dataset_revision_id: 'dataset', instrument_id: 'US30', timeframe: '1m',
  detection_config_hash: 'hash', pattern_id: 'PATTERN', pattern_version: '1',
  pattern_name: 'Fixture pattern', definition_fingerprint: 'fingerprint',
  build_id: 'build', code_revision: 'revision', code_dirty: false, code_capture_status: 'captured',
  instance_id: 'occurrence-1',
  sequence: 0, from_state: 'CANDIDATE', to_state: 'CONFIRMED', trigger_id: 'confirmed',
  event_time: first, detection_time: second, rationale: { source: 'fixture' }, emission_order: 0,
};

describe('TradingView chart model', () => {
  it('passes only cursor-visible canonical candles and detector markers to the chart', () => {
    const before = buildChartModel(bars, [initial, confirmed], [transition], first, visibility, null, null);
    expect(before.candles).toHaveLength(1);
    expect(before.markers.some((marker) => marker.id === 'event:0')).toBe(false);
    expect(before.markers.some((marker) => String(marker.id).startsWith('swing:'))).toBe(false);
    const after = buildChartModel(bars, [initial, confirmed], [transition], second, visibility, 0, null);
    expect(after.candles).toHaveLength(2);
    expect(after.ema.get('ema')?.points).toHaveLength(2);
    expect(after.markers.find((marker) => marker.id === 'event:0')?.time)
      .toBe(Date.parse(second) / 1000);
    expect(after.markers.find((marker) => marker.id === 'event:0')?.size).toBe(2);
    expect(after.markers.some((marker) => marker.id === 'trend:trend_leg:2')).toBe(true);
    expect(after.markers.some((marker) => String(marker.id).startsWith('swing:'))).toBe(true);
    expect(after.range[0].value).toBe(82);
  });

  it('hides only selected presentation layers and keeps review selection', () => {
    const model = buildChartModel(bars, [initial, confirmed], [], second,
      { ...visibility, ema: false, swing: false, range: false, session: false }, null,
      { start: first, end: second });
    expect(model.candles).toHaveLength(2);
    expect(model.ema.size).toBe(0);
    expect(model.range).toHaveLength(0);
    expect(model.markers.some((marker) => String(marker.id).startsWith('swing:'))).toBe(false);
    expect(model.markers.some((marker) => String(marker.id).startsWith('session:'))).toBe(false);
    expect(model.markers.some((marker) => marker.id === `review:${first}`)).toBe(true);
  });

  it('rejects invalid chart input rather than drawing a misleading price', () => {
    expect(() => buildChartModel([{ ...bars[0], high: '91' }], [], [], first,
      visibility, null, null)).toThrow('Canonical OHLC is inconsistent');
    expect(() => buildChartModel([bars[1], bars[0]], [], [], second,
      visibility, null, null)).toThrow('strictly ordered');
  });

  it('keeps an accessible fallback and TradingView attribution around the canvas', () => {
    const markup = renderToStaticMarkup(<CandlestickChart bars={bars} observations={[]}
      cursorTime={second} overlays={visibility} />);
    expect(markup).toContain('Interactive UTC candlestick chart with price and time scales');
    expect(markup).toContain('Charting by');
    expect(markup).toContain('www.tradingview.com');
  });
});
