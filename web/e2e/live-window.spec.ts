import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

const stamp = (minute: number) => new Date(Date.UTC(2026, 0, 5, 10, minute)).toISOString().replace('.000Z', 'Z');
async function fakeLive(page: Page, options = { prepare: false }) {
  let count = 600;
  let historyReady = true;
  let retainedSource: Record<string, unknown>;
  let publishedSource: Record<string, unknown> | undefined;
  const historyReads: string[] = [];
  const connections = new Map<string, Record<string, unknown>>();
  const prepares: string[] = [];
  const reads = new Map<string, number>();
  const disconnected: string[] = [];
  await page.route('**/api/replay/sources', async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    retainedSource = data.sources.find((source: Record<string, unknown>) => source.dataset_revision_id === 'offline-replay-us30-v3');
    // Give this fake feed one deterministic bootstrap, regardless of datasets
    // created by earlier real API tests in the shared smoke server.
    data.sources = data.sources.filter((source: Record<string, unknown>) => source.dataset_revision_id === 'offline-replay-us30-v3')
      .map((source: Record<string, unknown>) => ({ ...source, non_research_grade: false, range_start: stamp(0), range_end: stamp(count) }));
    if (publishedSource) data.sources.push(publishedSource);
    await route.fulfill({ json: data });
  });
  await page.route('**/api/history/requests/live-history-one', async (route) => {
    historyReads.push(route.request().url());
    await route.fulfill({ json: { request_id: 'live-history-one', status: historyReady ? 'available' : 'pending', source_manifest_hash: 'f'.repeat(64) } });
  });
  if (options.prepare) await page.route('**/api/history/inputs', async (route) => {
    publishedSource = { ...retainedSource, dataset_revision_id: 'published-live-init',
      range_start: stamp(0), range_end: stamp(12), bar_count: 12, non_research_grade: false };
    await route.fulfill({ json: publishedSource });
  });
  await page.route('**/api/history/navigation', route => route.fulfill({ status: 422, json: { detail: 'Reached the beginning of this fixture feed.' } }));
  function state(id: string) {
    const request = connections.get(id)!;
    return { subscription_id: id, run_id: `session-${id}`, input_id: `live-input-${id}`,
      bootstrap_revision_id: request.bootstrap_revision_id, instrument_id: 'US30', streams: request.streams,
      status: 'connected', detail: null, available_at: stamp(count), history_start: stamp(0),
      forming: { timestamp: stamp(count), open: '100', high: '104', low: '99', close: '102', is_complete: false } };
  }
  await page.route('**/api/live/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.includes('/prices/') || url.pathname.includes('/price-subscriptions')) {
      await route.fulfill({ status: 503, json: { detail: 'Price fallback disabled in this analytical-feed fixture.' } }); return;
    }
    if (url.pathname.includes('/prepare/')) {
      const body = request.postDataJSON();
      prepares.push(body.subscription_id);
      if (options.prepare && !connections.has(body.subscription_id)) {
        await route.fulfill({ json: { plan: { request_ids: ['live-history-one'], instrument_id: 'US30',
          target: stamp(11), start: stamp(0), end: stamp(12), timeframe: '1m', intent: 'replay', initialization: 'available' } } }); return;
      }
      connections.set(body.subscription_id, body);
      await route.fulfill({ json: { state: state(body.subscription_id) } }); return;
    }
    const id = url.pathname.split('/')[4];
    if (request.method() === 'DELETE') {
      disconnected.push(id); connections.delete(id);
      await route.fulfill({ json: { status: 'disconnected' } }); return;
    }
    if (request.method() === 'POST') {
      if (url.pathname.endsWith('/history')) {
        await route.fulfill({ json: { ...retainedSource, non_research_grade: false } }); return;
      }
      const body = request.postDataJSON(); connections.set(body.subscription_id, body);
      await route.fulfill({ json: state(body.subscription_id) }); return;
    }
    reads.set(id, (reads.get(id) ?? 0) + 1);
    const live = state(id);
    const timeframe = url.searchParams.get('timeframe') ?? '1m';
    const width = timeframe === '5m' ? 5 : 1;
    const end = Math.min(Date.parse(live.available_at), Date.parse(url.searchParams.get('end') ?? live.available_at));
    const bars = Array.from({ length: Math.floor(count / width) }, (_, index) => ({
      timestamp: stamp(index * width), available_at: stamp((index + 1) * width),
      open: '100', high: '103', low: '99', close: '101',
    })).filter((bar) => Date.parse(bar.available_at) <= end).slice(-500);
    const event = { event_id: 'live-compression', run_id: `detector-${id}`, dataset_revision_id: live.input_id,
      instrument_id: 'US30', timeframe: '5m', detection_config_hash: 'hash', pattern_id: 'RANGE_COMPRESSION_V1',
      pattern_version: '1', pattern_name: 'Range compression', definition_fingerprint: 'fingerprint',
      build_id: 'test', code_revision: null, code_dirty: null, code_capture_status: 'unavailable',
      instance_id: 'occurrence', runtime_instance_id: 'runtime', sequence: 0, from_state: 'NONE', to_state: 'CANDIDATE',
      trigger_id: 'candidate', event_time: stamp(count - 1), detection_time: stamp(count - 1), available_at: stamp(count), rationale: {}, emission_order: 0 };
    const manual = (connections.get(id)?.streams as Array<{ policies: Record<string, string> }>)[0].policies.RANGE_COMPRESSION_V1 === 'manual';
    await route.fulfill({ json: { run_id: live.run_id, cursor_index: count, cursor_time: stamp(count - 1),
      available_at: live.available_at, history_start: stamp(0), timeframe, bars, observations: [],
      events: !manual && end >= Date.parse(event.available_at) ? [event] : [], view_start: stamp(0), view_end: new Date(end).toISOString(),
      limit: 500, coverage: 'calculated', live: { ...live, forming: url.searchParams.has('end') ? null : {
        ...live.forming, timestamp: stamp(Math.floor(count / width) * width),
      } } } });
  });
  return { prepares, reads, disconnected, historyReads, advance: () => { count += 1; },
    setHistoryReady: (value: boolean) => { historyReady = value; } };
}


test('rolling a full live window exposes the newest detections', async ({ page }) => {
  const live = await fakeLive(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await expect(page.getByRole('img', { name: /501 visible bars/ })).toBeVisible();
  await page.waitForTimeout(400);
  const viewport = () => page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].viewport);
  await expect.poll(async () => Boolean((await viewport())?.to)).toBe(true);
  const before = await viewport();
  for (let i = 0; i < 5; i++) live.advance();
  await expect(page.getByText(/Confirmed through:.*20:05/)).toBeVisible();
  await page.waitForTimeout(400);
  const after = await viewport();
  expect(after.to - after.from).toBeCloseTo(before.to - before.from, 1);
  expect(after.to).toBeGreaterThan(before.to);
  await expect(page.getByTestId('timeline-event')).toContainText('20:05');
});
test('panning a full live window requests earlier retained candles', async ({ page }) => {
  await fakeLive(page);
  const endpoints: string[] = [];
  page.on('request', (request) => { if (request.url().includes('/live/analysis/') && request.url().includes('/view')) endpoints.push(new URL(request.url()).searchParams.get('end') ?? 'latest'); });
  await page.goto('/');
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await expect(page.getByRole('img', { name: /501 visible bars/ })).toBeVisible();
  await page.getByRole('button', { name: 'Fit candles', exact: true }).click();
  await expect.poll(() => page.evaluate(() => { const v = JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].viewport; return v ? v.to - v.from : 0; })).toBeGreaterThan(450);
  const box = (await page.locator('.trading-chart').boundingBox())!;
  await page.mouse.move(box.x + 100, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 700, box.y + box.height / 2, {steps: 10});
  await page.mouse.up();
  await page.waitForTimeout(1500);
  expect(endpoints.some(value => value !== 'latest' && Date.parse(value) < Date.parse(stamp(600)))).toBe(true);
});
