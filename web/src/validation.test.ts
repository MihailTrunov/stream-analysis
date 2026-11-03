import { expect, it } from 'vitest';
import { isDetectionConfig, validateReplayState, validateReplayView } from './validation';

it('rejects incomplete configuration and non-finite detector parameters', () => {
  expect(isDetectionConfig({ instrument_id: 'US30' })).toBe(false);
  const config = { schema_version: 'v1', instrument_id: 'US30', timeframe: '1m', calendar_id: 'uk',
    patterns: [], components: [{ component_id: 'ema', parameters: [{ name: 'period', value: 10 }] }] };
  expect(isDetectionConfig(config)).toBe(true);
  config.components[0].parameters[0].value = Infinity;
  expect(isDetectionConfig(config)).toBe(false);
});

it('rejects unknown lifecycle states and malformed snapshot envelopes', () => {
  expect(() => validateReplayState({ status: 'unexpected' })).toThrow('invalid replay state');
  expect(() => validateReplayView({ run_id: 'run', cursor_index: 0, cursor_time: null, bars: {}, observations: [], events: [] }))
    .toThrow('invalid chart snapshot');
  const snapshot = { run_id: 'run', cursor_index: -1, cursor_time: null, bars: [], observations: [], events: [], limit: 500 };
  expect(validateReplayView(snapshot)).toEqual(snapshot);
  expect(() => validateReplayView({ ...snapshot, bars: [{ timestamp: 'broken', open: '100', high: '110', low: '90', close: '105' }] }))
    .toThrow('invalid chart snapshot');
});
