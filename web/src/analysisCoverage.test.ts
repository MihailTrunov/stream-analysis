import { expect, it } from 'vitest';
import { containsEnd, hasInitializedResult, coversWindow, type StreamCoverage } from './analysisCoverage';
const partial: StreamCoverage = { run_id: 'run', pattern_id: 'pattern', timeframe: '1m', status: 'partial',
  valid_from: '2026-01-05T09:11:00Z', available_from: '2026-01-05T09:12:00Z', valid_to: '2026-01-05T14:00:00Z', processed: 300, required: 11 };
it('never advertises an uninitialized window or backward reuse as complete', () => {
  expect(coversWindow(partial, '2026-01-05T09:00:00Z', '2026-01-05T14:00:00Z')).toBe(false);
  expect(coversWindow(partial, '2026-01-05T10:00:00Z', '2026-01-05T14:00:00Z')).toBe(true);
  expect(hasInitializedResult(partial, '2026-01-05T09:10:00Z')).toBe(false);
  expect(hasInitializedResult({ ...partial, valid_from: null, available_from: null }, partial.valid_to)).toBe(false);
  expect(containsEnd(partial, '2026-01-05T15:00:00Z')).toBe(false);
});
