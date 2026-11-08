import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

const stamp = (minute: number) => new Date(Date.UTC(2026, 0, 5, 10, minute)).toISOString().replace('.000Z', 'Z');
async function fakeLive(page: Page, options = { prepare: false }) {
  let count = 12;
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
    })).filter((bar) => Date.parse(bar.available_at) <= end);
    const event = { event_id: 'live-compression', run_id: `detector-${id}`, dataset_revision_id: live.input_id,
      instrument_id: 'US30', timeframe: '5m', detection_config_hash: 'hash', pattern_id: 'RANGE_COMPRESSION_V1',
      pattern_version: '1', pattern_name: 'Range compression', definition_fingerprint: 'fingerprint',
      build_id: 'test', code_revision: null, code_dirty: null, code_capture_status: 'unavailable',
      instance_id: 'occurrence', runtime_instance_id: 'runtime', sequence: 0, from_state: 'NONE', to_state: 'CANDIDATE',
      trigger_id: 'candidate', event_time: stamp(3), detection_time: stamp(5), available_at: stamp(10), rationale: {}, emission_order: 0 };
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

test('live mode shows forming data separately and pattern selection keeps the display timeframe', async ({ page }) => {
  const live = await fakeLive(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Live monitoring' })).toBeVisible();
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  await expect(page.getByText('Forming candle: display only')).toBeVisible();
  await expect(page.getByTestId('timeline-event').locator('.timeframe-label')).toHaveText('5m');
  await page.getByTestId('timeline-event').click();
  await expect(page.getByLabel('Chart timeframe')).toHaveValue('1m');
  await expect(page.getByRole('img', { name: /12 visible bars/ })).toBeVisible();
  await page.getByRole('button', { name: 'Pattern details', exact: true }).hover();
  await expect(page.getByRole('dialog', { name: 'Pattern evidence preview' })).toBeVisible();
  expect(live.prepares).toHaveLength(1);
});

test('restored live tabs reconnect only on focus and hidden tabs stop DOM/view polling', async ({ page }) => {
  const live = await fakeLive(page);
  await page.addInitScript(() => {
    if (localStorage.getItem('stream-analysis.workspace.v1')) return;
    const first = crypto.randomUUID(), second = crypto.randomUUID();
    localStorage.setItem('stream-analysis.workspace.v1', JSON.stringify({ version: 3, activeId: first,
      tabs: [first, second].map((id) => ({ id, instrument: 'US30', sourceId: 'offline-replay-us30-v3',
        timezone: 'Europe/London', mode: 'live', liveConnect: true, liveConnectionId: crypto.randomUUID() })) }));
  });
  await page.goto('/');
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  expect(live.prepares).toHaveLength(1);
  const first = live.prepares[0];
  await page.locator('.workspace-tabs [role=tab]').nth(1).click();
  await expect.poll(() => live.prepares.length).toBe(2);
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  const before = live.reads.get(first);
  await page.waitForTimeout(1200);
  expect(live.reads.get(first)).toBe(before);
  await expect(page.locator('.analysis-session')).toHaveCount(1);
  expect(live.disconnected).toHaveLength(0);
  await page.locator('.workspace-tabs [role=tab]').nth(0).click();
  await expect.poll(() => live.reads.get(first) ?? 0).toBeGreaterThan(before!);
  expect(live.prepares).toHaveLength(2); // Already connected tabs retain their live sessions.
  await page.reload();
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  expect(live.prepares).toHaveLength(3);
  expect(live.prepares.at(-1)).toBe(first);
});

test('live initialization waits for shared history and resumes only when focused', async ({ page }) => {
  const live = await fakeLive(page, { prepare: true });
  live.setHistoryReady(false);
  await page.goto('/');
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await expect(page.getByText(/Waiting for live initialization history/)).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean(JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].livePreparation))).toBe(true);
  await page.getByRole('button', { name: 'New analysis tab' }).click();
  const reads = live.historyReads.length;
  await page.waitForTimeout(1100);
  expect(live.historyReads).toHaveLength(reads);
  live.setHistoryReady(true);
  await page.locator('.workspace-tabs [role=tab]').nth(0).click();
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  expect(live.prepares).toHaveLength(1);
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].sourceId)).toBe('published-live-init');
});

test('live Apply creates a new connection and manual history uses an independent calculation', async ({ page }) => {
  const live = await fakeLive(page);
  const calculations: Array<{ mode: string; automatic: boolean }> = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/analysis' && request.method() === 'POST') calculations.push(request.postDataJSON());
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  await page.getByRole('tab', { name: 'Detector settings', exact: true }).click();
  await page.getByLabel('1m RANGE_COMPRESSION_V1 operation mode').selectOption('manual');
  await page.getByRole('button', { name: 'Apply settings', exact: true }).click();
  await expect.poll(() => live.prepares.length).toBe(2);
  expect(live.prepares[0]).not.toBe(live.prepares[1]);
  expect(live.disconnected).toHaveLength(1);
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  await page.getByRole('button', { name: 'Calculate RANGE_COMPRESSION_V1 for current view · 1m' }).click();
  await expect.poll(() => calculations.length, { timeout: 15000 }).toBeGreaterThan(0);
  expect(calculations[0].mode).toBe('history');
  expect(calculations[0].automatic).toBe(false);
  expect(live.prepares).toHaveLength(2);
});

test('live navigation suspends following while confirmed data keeps updating', async ({ page }) => {
  const live = await fakeLive(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  const box = (await page.locator('.trading-chart').boundingBox())!;
  await page.mouse.move(box.x + 150, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 450, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(250);
  const viewport = () => page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].viewport);
  const before = await viewport();
  live.advance();
  await expect(page.getByText(/Confirmed through:.*10:13/)).toBeVisible();
  const after = await viewport();
  expect(after.from).toBeCloseTo(before.from, 1);
  expect(after.to).toBeCloseTo(before.to, 1);
  await page.getByLabel('Chart navigation').getByRole('button', { name: 'Return to latest', exact: true }).click();
  await expect(page.getByText('US30 · 1m · 13 completed candles')).toBeVisible();
  await expect.poll(async () => (await viewport())?.to ?? Infinity).toBeLessThan(25);
  const latest = await viewport();
  expect(latest.to).toBeGreaterThan(12);
  live.advance();
  await expect(page.getByText('US30 · 1m · 14 completed candles')).toBeVisible();
  await expect.poll(async () => (await viewport())?.to ?? -Infinity).toBeGreaterThan(latest.to);
});

test('disconnect prevents automatic reconnect and closing a live tab disconnects its consumer', async ({ page }) => {
  const live = await fakeLive(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  await page.getByRole('button', { name: 'Disconnect live', exact: true }).click();
  await expect(page.getByText('disconnected', { exact: true })).toBeVisible();
  expect(live.disconnected).toHaveLength(1);
  await page.waitForTimeout(1100);
  expect(live.prepares).toHaveLength(1);
  await page.getByRole('button', { name: 'Reconnect live', exact: true }).click();
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  expect(live.prepares).toHaveLength(2);
  await page.getByRole('button', { name: 'New analysis tab', exact: true }).click();
  await page.locator('.workspace-tabs [role=tab]').nth(0).click();
  await page.locator('.workspace-tab').nth(0).getByRole('button', { name: 'Close US30 tab' }).click();
  await expect.poll(() => live.disconnected.length).toBe(3);
});
