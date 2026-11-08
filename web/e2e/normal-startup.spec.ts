import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

const normalKey = 'stream-analysis.workspace.live.v1';
const demoKey = 'stream-analysis.workspace.v1';

async function normalStartup(page: Page) {
  await page.route('**/api/diagnostics', async (route) => {
    const response = await route.fetch();
    await route.fulfill({ json: { ...await response.json(), synthetic_demo_enabled: false } });
  });
  const prepares: string[] = [];
  // The isolated smoke API contains demo data. Never contact a real provider.
  await page.route('**/api/live/**', async (route) => {
    if (route.request().url().includes('/prepare/')) prepares.push(new URL(route.request().url()).pathname.split('/').at(-1)!);
    await route.fulfill({ status: 503, json: { detail: 'Provider disabled for startup test' } });
  });
  return prepares;
}

test('normal startup replaces demo-only history with live and preserves the demo workspace', async ({ page }) => {
  const prepares = await normalStartup(page);
  await page.addInitScript(({ demoKey }) => {
    localStorage.setItem(demoKey, JSON.stringify({ version: 3, activeId: 'old-demo', tabs: [
      { id: 'old-demo', instrument: 'US30', sourceId: 'offline-replay-us30-v3', timezone: 'Europe/London' },
    ] }));
  }, { demoKey });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Live monitoring' })).toBeVisible();
  await expect.poll(() => prepares).toEqual(['US30']);
  await expect(page.getByText(/Synthetic demo only/)).toHaveCount(0);
  await expect.poll(() => page.evaluate((key) => JSON.parse(localStorage.getItem(key)!).tabs[0].mode, normalKey)).toBe('live');
  expect(await page.evaluate((key) => JSON.parse(localStorage.getItem(key)!).tabs[0].id, demoKey)).toBe('old-demo');
});

test('restored live tabs prepare only when focused', async ({ page }) => {
  const prepares = await normalStartup(page);
  await page.addInitScript(({ normalKey }) => {
    localStorage.setItem(normalKey, JSON.stringify({ version: 3, activeId: 'first', tabs: [
      { id: 'first', instrument: 'US30', sourceId: '', timezone: 'Europe/London', mode: 'live', liveConnect: true },
      { id: 'second', instrument: 'DAX', sourceId: '', timezone: 'Europe/London', mode: 'live', liveConnect: true },
    ] }));
  }, { normalKey });
  await page.goto('/');
  await expect.poll(() => prepares).toEqual(['US30']);
  await page.getByRole('tab', { name: 'DAX Live', exact: true }).click();
  await expect.poll(() => prepares).toEqual(['US30', 'DAX']);
});

test('normal startup preserves a real saved historical tab', async ({ page }) => {
  await normalStartup(page);
  await page.route('**/api/replay/sources', async (route) => {
    const response = await route.fetch();
    const catalog = await response.json();
    catalog.sources = catalog.sources.filter((source: { dataset_revision_id: string }) => source.dataset_revision_id === 'offline-replay-us30-v3')
      .map((source: object) => ({ ...source, non_research_grade: false }));
    await route.fulfill({ json: catalog });
  });
  await page.addInitScript(({ normalKey }) => {
    localStorage.setItem(normalKey, JSON.stringify({ version: 3, activeId: 'real-history', tabs: [
      { id: 'real-history', instrument: 'US30', sourceId: 'offline-replay-us30-v3', timezone: 'Europe/London', mode: 'history' },
    ] }));
  }, { normalKey });
  await page.goto('/');
  await expect(page.getByRole('tab', { name: 'US30 History', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start replay from' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Live monitoring' })).toHaveCount(0);
});

for (const instrument of ['US30', 'DAX']) {
  test(`startup shows latest completed ${instrument} prices when today's calendar is unavailable`, async ({ page }) => {
    await normalStartup(page);
    const end = instrument === 'US30' ? '2026-11-06T22:00:00Z' : '2026-11-06T21:00:00Z';
    // Transport fixture; trading-calendar selection is verified in Python tests.
    const prices = Array.from({ length: 1440 }, (_, index) => {
      const time = Date.parse(end) - (1440 - index) * 60_000;
      return { timestamp: new Date(time).toISOString(), available_at: new Date(time + 60_000).toISOString(),
        open: '100', high: '102', low: '99', close: '101' };
    });
    const source = { dataset_revision_id: `latest-${instrument}`, instrument_id: instrument, timeframe: '1m',
      source_dataset_id: `latest-${instrument}`, range_start: prices[0].timestamp, range_end: end,
      non_research_grade: false, bar_count: 1440, calendar_version: 'test', canonical_checksum: 'f'.repeat(64) };
    let preparations = 0;
    let connections = 0;
    page.on('request', request => { if (request.method() === 'POST' && request.url().includes('/api/live/analysis/')) ++connections; });
    await page.route('**/api/replay/sources', route => route.fulfill({ json: { sources: [] } }));
    await page.route(`**/api/live/prepare/${instrument}`, async route => {
      ++preparations;
      await route.fulfill({ json: { plan: { request_ids: ['latest-range'], instrument_id: instrument,
        target: prices.at(-1)!.timestamp, start: prices[0].timestamp, end, timeframe: '1m', intent: 'replay',
        initialization: 'available', live_status: 'calendar_unverified',
        live_detail: 'Showing the latest verified completed history. The trading calendar does not cover the current date.' } } });
    });
    await page.route('**/api/history/requests/latest-range', route => route.fulfill({ json: {
      request_id: 'latest-range', status: 'available', source_manifest_hash: 'f'.repeat(64),
    } }));
    await page.route('**/api/history/inputs', route => route.fulfill({ json: source }));
    await page.route(`**/api/replay/sources/latest-${instrument}/bars?*`, route => {
      const params = new URL(route.request().url()).searchParams;
      return route.fulfill({ json: { bars: prices.filter(bar => Date.parse(bar.timestamp) < Date.parse(params.get('end')!))
        .slice(-Number(params.get('limit'))) } });
    });
    await page.addInitScript(({ normalKey, instrument }) => {
      localStorage.setItem(normalKey, JSON.stringify({ version: 3, activeId: 'selected', tabs: [
        { id: 'selected', instrument, sourceId: '', mode: 'live', liveConnect: true, timezone: 'Europe/London' },
      ] }));
    }, { normalKey, instrument });
    await page.goto('/');
    await expect(page.getByRole('tab', { name: `${instrument} Live`, exact: true })).toBeVisible();
    await expect(page.getByRole('img', { name: /1440 visible bars/ })).toBeVisible();
    await expect(page.getByText('Latest available history', { exact: true })).toBeVisible();
    await expect(page.getByText(/calendar does not cover the current date/)).toBeVisible();
    expect(connections).toBe(0);
    await page.waitForTimeout(600);
    expect(preparations).toBe(1);
  });
}

test('retained prices remain visible when live preparation fails', async ({ page }) => {
  await normalStartup(page);
  await page.route('**/api/replay/sources', async route => {
    const response = await route.fetch(); const catalog = await response.json();
    catalog.sources = catalog.sources.filter((source: { dataset_revision_id: string }) => source.dataset_revision_id === 'offline-replay-us30-v3')
      .map((source: object) => ({ ...source, non_research_grade: false }));
    await route.fulfill({ json: catalog });
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Live monitoring' })).toBeVisible();
  await expect(page.getByRole('img', { name: /300 visible bars/ })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Provider disabled for startup test');
});
