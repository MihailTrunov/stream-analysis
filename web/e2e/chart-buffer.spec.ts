import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

const stamp = (minute: number) => new Date(Date.UTC(2026, 0, 5, 10, minute)).toISOString().replace('.000Z', 'Z');
async function fakeLive(page: Page, options = { prepare: false }, initialCount = 1800) {
  let count = initialCount;
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
    })).filter((bar) => Date.parse(bar.available_at) <= end).slice(-1440);
    const event = { event_id: 'live-compression', run_id: `detector-${id}`, dataset_revision_id: live.input_id,
      instrument_id: 'US30', timeframe: '1m', detection_config_hash: 'hash', pattern_id: 'RANGE_COMPRESSION_V1',
      pattern_version: '1', pattern_name: 'Range compression', definition_fingerprint: 'fingerprint',
      build_id: 'test', code_revision: null, code_dirty: null, code_capture_status: 'unavailable',
      instance_id: 'occurrence', runtime_instance_id: 'runtime', sequence: 0, from_state: 'NONE', to_state: 'CANDIDATE',
      trigger_id: 'candidate', event_time: stamp(count - 1), detection_time: stamp(count - 1), available_at: stamp(count), rationale: {}, emission_order: 0 };
    const manual = (connections.get(id)?.streams as Array<{ policies: Record<string, string> }>)[0].policies.RANGE_COMPRESSION_V1 === 'manual';
    await route.fulfill({ json: { run_id: live.run_id, cursor_index: count, cursor_time: stamp(count - 1),
      available_at: live.available_at, history_start: stamp(0), timeframe, bars, observations: bars.filter((bar) => bar.timestamp === stamp(count - 1)).map((bar) => ({ timestamp: bar.timestamp, availability: {},
        components: { ema: { component_id: 'ema', period: 5, value_ready: true, ema: '100' } },
        market_events: [{ ordinal: 0, event_type: 'SWING_POINT_CONFIRMED', event_time: bar.timestamp, detection_time: bar.timestamp,
          source_instance_id: 'swing_point', evidence: { swing_type: 'SWING_HIGH' } }],
      })),
      events: !manual && end >= Date.parse(event.available_at) ? [event] : [], view_start: stamp(0), view_end: new Date(end).toISOString(),
      limit: 500, coverage: 'calculated', live: { ...live, forming: url.searchParams.has('end') ? null : {
        ...live.forming, timestamp: stamp(Math.floor(count / width) * width),
      } } } });
  });
  return { prepares, reads, disconnected, historyReads, advance: () => { count += 1; },
    setHistoryReady: (value: boolean) => { historyReady = value; } };
}



interface Fiber { type?: { name?: string }; return?: Fiber; memoizedState?: Hook }
interface Hook { memoizedState?: { current?: { timeScale?: () => Scale } }; next?: Hook }
interface Scale { getVisibleLogicalRange: () => { from: number; to: number }; options: () => { barSpacing: number } }
async function geometry(page: Page) {
  return page.locator('.trading-chart').evaluate((element) => {
    const host = element as unknown as Record<string, Fiber>;
    let fiber: Fiber | undefined = host[Object.keys(host).find(key => key.startsWith('__reactFiber'))!];
    while (fiber && fiber.type?.name !== 'CandlestickChart') fiber = fiber.return;
    let hook = fiber?.memoizedState;
    while (hook) {
      const current = hook.memoizedState?.current;
      if (current?.timeScale) {
        const scale = current.timeScale();
        return { range: scale.getVisibleLogicalRange(), spacing: scale.options().barSpacing };
      }
      hook = hook.next;
    }
    throw new Error('Chart instance not found');
  });
}

test('live history extends the 24-hour buffer without changing scale or visible timestamps', async ({ page }) => {
  await fakeLive(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await expect(page.getByRole('img', { name: /1441 visible bars/ })).toBeVisible();
  const before = await geometry(page);
  await expect.poll(() => markerLabels(page)).toEqual(expect.arrayContaining(['SH', 'C']));
  await page.getByRole('button', { name: 'Load earlier candles', exact: true }).click();
  await expect(page.getByRole('img', { name: /1800 visible bars/ })).toBeVisible();
  const after = await geometry(page);
  await expect.poll(() => markerLabels(page)).toEqual(expect.arrayContaining(['SH', 'C']));
  expect(after.spacing).toBeCloseTo(before.spacing, 5);
  // Price-axis/scrollbar width can shift the fractional range by less than one candle.
  expect(Math.abs(after.range.from - before.range.from - 360)).toBeLessThan(1);
  await page.getByRole('button', { name: 'Return to latest', exact: true }).click();
  await expect(page.getByRole('img', { name: new RegExp(`through ${stamp(1800)}`) })).toBeVisible();
  await expect(page.getByText('Forming candle: display only')).toBeVisible();
  expect((await geometry(page)).spacing).toBeCloseTo(before.spacing, 5);
});

test('zooming out automatically fills older retained prices without resetting zoom', async ({ page }) => {
  await fakeLive(page, { prepare: false }, 1800);
  await page.goto('/');
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await expect(page.getByRole('img', { name: /1441 visible bars/ })).toBeVisible();
  for (let i = 0; i < 12; i++) await page.getByRole('button', { name: 'Zoom out', exact: true }).click();
  await expect(page.getByRole('img', { name: /1800 visible bars/ })).toBeVisible();
  expect((await geometry(page)).spacing).toBeLessThan(1);
  await expect(page.getByText('Reached the beginning of this fixture feed.', { exact: false })).toBeVisible();
  const after = await geometry(page);
  await page.waitForTimeout(500);
  expect((await geometry(page)).spacing).toBeCloseTo(after.spacing, 5);
});

test('fetched history retries failure and fills the exposed area without changing zoom or focus', async ({ page }) => {
  await fakeLive(page, { prepare: false }, 95);
  let ready = false;
  let failed = false;
  let preparations = 0;
  const source = { dataset_revision_id: 'buffer-older', source_dataset_id: 'buffer-older', instrument_id: 'US30',
    range_start: stamp(-500), range_end: stamp(0), bar_count: 500, non_research_grade: false,
    canonical_checksum: 'f'.repeat(64), calendar_version: 'test' };
  await page.route('**/api/history/navigation', async route => {
    ++preparations;
    await route.fulfill({ json: { request_ids: ['buffer-request'], instrument_id: 'US30',
      target: stamp(-1), start: stamp(-500), end: stamp(0), timeframe: '1m', intent: 'move', initialization: 'available' } });
  });
  await page.route('**/api/history/requests/buffer-request', route => route.fulfill({ json: {
    request_id: 'buffer-request', status: failed ? 'failed' : ready ? 'available' : 'pending', source_manifest_hash: 'f'.repeat(64),
  } }));
  await page.route('**/api/history/inputs', route => route.fulfill({ json: source }));
  await page.route('**/api/replay/sources/buffer-older/bars?*', route => route.fulfill({ json: {
    bars: Array.from({ length: 500 }, (_, i) => ({ timestamp: stamp(i - 500), available_at: stamp(i - 499),
      open: '100', high: '103', low: '99', close: '101' })),
  } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await expect(page.getByRole('img', { name: /96 visible bars/ })).toBeVisible();
  for (let i = 0; i < 4; i++) await page.getByRole('button', { name: 'Zoom out', exact: true }).click();
  await expect(page.getByText(/Waiting for historical data/)).toBeVisible();
  const before = await geometry(page);
  failed = true;
  await expect(page.getByRole('alert')).toContainText('Historical data loading failed');
  failed = false; ready = true;
  await page.getByRole('button', { name: 'Retry history loading' }).click();
  await expect(page.getByRole('img', { name: /595 visible bars/ })).toBeVisible();
  const after = await geometry(page);
  await expect.poll(() => markerLabels(page)).toEqual(expect.arrayContaining(['SH', 'C']));
  expect(after.spacing).toBeCloseTo(before.spacing, 5);
  expect(after.range.from).toBeGreaterThan(0);
  expect(preparations).toBe(1);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.locator('.focus-notice')).toHaveCount(0);
});

async function markerLabels(page: Page): Promise<string[]> {
  return page.locator('.trading-chart').evaluate((element) => {
    interface Frame { type?: { name?: string }; return?: Frame; memoizedState?: State }
    interface State { memoizedState?: unknown; next?: State }
    const host = element as unknown as Record<string, Frame>;
    let fiber: Frame | undefined = host[Object.keys(host).find(key => key.startsWith('__reactFiber'))!];
    while (fiber && fiber.type?.name !== 'CandlestickChart') fiber = fiber.return;
    let hook = fiber?.memoizedState;
    while (hook) {
      const memo = hook.memoizedState;
      if (Array.isArray(memo) && memo[0]?.model?.markers) return memo[0].model.markers.map((marker: { text: string }) => marker.text);
      hook = hook.next;
    }
    return [];
  });
}
