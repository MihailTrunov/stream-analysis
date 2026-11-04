export interface StreamCoverage {
  run_id: string; pattern_id: string; timeframe: string;
  status: 'calculated' | 'partial' | 'initialization_unavailable';
  valid_from: string | null; available_from: string | null; valid_to: string;
  processed: number; required: number;
}
export function containsEnd(coverage: StreamCoverage, end: string): boolean {
  return Date.parse(end) <= Date.parse(coverage.valid_to);
}
export function hasInitializedResult(coverage: StreamCoverage, end: string): boolean {
  return coverage.available_from !== null && Date.parse(end) >= Date.parse(coverage.available_from) && containsEnd(coverage, end);
}
export function coversWindow(coverage: StreamCoverage, start: string, end: string): boolean {
  return coverage.valid_from !== null && Date.parse(start) >= Date.parse(coverage.valid_from) && hasInitializedResult(coverage, end);
}
