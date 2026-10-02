import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { EventInspector } from './EventInspector';
import type { ReplayDetectorEvent } from './replay';

const event: ReplayDetectorEvent = {
  event_id: 'event-7', runtime_instance_id: 'runtime-instance-2',
  run_id: 'run-123', dataset_revision_id: 'revision-456', instrument_id: 'US30', timeframe: '1m',
  detection_config_hash: 'config-hash', pattern_id: 'REVERSAL_V1', pattern_version: '1',
  pattern_name: 'Trend reversal', definition_fingerprint: 'definition-hash',
  build_id: 'build-789', code_revision: 'git-revision', code_dirty: false,
  code_capture_status: 'captured', instance_id: 'instance-2', sequence: 3,
  from_state: 'CANDIDATE', to_state: 'ACTIVE', trigger_id: 'protected_swing_broken',
  event_time: '2026-01-05T12:10:00Z', detection_time: '2026-01-05T12:13:00Z',
  emission_order: 7,
  rationale: {
    schema: 'detector-evidence-v1', condition: 'protected_swing_broken',
    source_market_event_refs: ['source-1'], items: [{
      condition_id: 'protected_swing_broken', status: 'PASS',
      value: { type: 'decimal', value: '89.1250' }, operator: '<',
      threshold: { type: 'decimal', value: '90.0000' }, units: 'points',
      source_refs: ['source-1'],
      features: {
        source_duration_bars: { type: 'integer', value: 6 },
        source_ema_efficiency_pct: { type: 'decimal', value: '38.75' },
        optional_level_points: { type: 'decimal', value: null },
      },
    }],
  },
};

describe('event explanation inspector', () => {
  it('shows exact recorded threshold, feature units, timing and lineage', () => {
    const html = renderToStaticMarkup(<EventInspector event={event} />);
    for (const text of [
      'Trend reversal', 'REVERSAL_V1', 'definition-hash', 'instance-2', 'event-7', 'run-123',
      'revision-456', 'config-hash', 'build-789', 'git-revision',
      '2026-01-05T12:10:00Z', '2026-01-05T12:13:00Z',
      '89.1250', '90.0000', '38.75', 'source_duration_bars', 'source-1',
    ]) expect(html).toContain(text);
    expect(html).toContain('38.75</code><span> %</span>');
    expect(html).toContain('Not provided');
    expect(html).toContain('Detection time (first observable)');
  });

  it('handles invalidation, null threshold and unknown optional evidence explicitly', () => {
    const invalidated: ReplayDetectorEvent = { ...event,
      to_state: 'INVALIDATED', trigger_id: 'candidate_interrupted', pattern_name: null,
      code_revision: null, code_dirty: null, rationale: {
        schema: 'detector-evidence-v1', condition: 'candidate_interrupted', items: [{
          condition_id: 'candidate_interrupted', status: 'PASS',
          value: { type: 'string', value: 'gap' }, operator: '==', threshold: null,
          units: null, features: {},
        }],
      },
    };
    const html = renderToStaticMarkup(<EventInspector event={invalidated} />);
    expect(html).toContain('INVALIDATED');
    expect(html).toContain('Name unavailable');
    expect(html).toContain('Not provided');
    expect(html).toContain('Not specified');
    expect(html).toContain('None recorded');
    expect(html).toContain('Unknown');
  });

  it('falls back to unchanged raw evidence for an unknown schema', () => {
    const unknown = { ...event, rationale: { schema: 'future-v2', payload: 'exact' } };
    const html = renderToStaticMarkup(<EventInspector event={unknown} />);
    expect(html).toContain('not supported');
    expect(html).toContain('future-v2');
    expect(html).toContain('exact');
  });
});
