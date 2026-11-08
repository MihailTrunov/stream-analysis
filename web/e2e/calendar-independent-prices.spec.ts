import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

const key = 'stream-analysis.workspace.live.v1';
async function priceOnly(page: Page, mode: 'live' | 'history' = 'live') {
  const reads: string[] = [];
  const connections: string[] = [];
  await page.route('**/api/diagnostics', async route => {
    const response = await route.fetch();
    await route.fulfill({ json: { ...await response.json(), synthetic_demo_enabled: false } });
  });
  await page.route('**/api/replay/sources', async route => {
    if (mode === 'live') { await route.fulfill({ json: { sources: [] } }); return; }
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({ json: { sources: data.sources.filter((s: { dataset_revision_id: string }) => s.dataset_revision_id === 'offline-replay-us30-v3')
      .map((s: object) => ({ ...s, non_research_grade: false })) } });
  });
  await page.route('**/api/history/navigation', route => route.fulfill({ status: 422,
    json: { detail: 'trading date is outside verified calendar coverage' } }));
  await page.route('**/api/live/**', async route => {
    const request = route.request(); const url = new URL(request.url());
    if (url.pathname.includes('/price-subscriptions')) {
      if (request.method() === 'POST') connections.push(request.postDataJSON().instrument_id);
      await route.fulfill({ json: { status: 'ok' } }); return;
    }
    if (url.pathname.endsWith('/source')) {
      const instrument = url.pathname.split('/').at(-2);
      const end = request.postDataJSON().end ?? '2026-11-06T22:00:00Z';
      await route.fulfill({ json: { dataset_revision_id: 'provider-observed-fixture',
        source_dataset_id: 'provider-fixture', instrument_id: instrument, timeframe: '1m',
        range_start: new Date(Date.parse(end) - 1440 * 60_000).toISOString(), range_end: end,
        bar_count: 1440, calendar_version: 'provider-observed-v1:fixture',
        suggested_start: new Date(Date.parse(end) - 30 * 60_000).toISOString(), suggested_end: end,
        non_research_grade: false } }); return;
    }
    if (url.pathname.includes('/prices/')) {
      reads.push(url.href);
      const instrument = url.pathname.split('/').at(-1);
      const end = url.searchParams.get('end') ?? '2026-11-06T22:00:00Z';
      const timeframe = url.searchParams.get('timeframe') ?? '1m';
      const width = timeframe === '5m' ? 5 : 1;
      const bars = Array.from({ length: 1440 / width }, (_, i) => {
        const time = Date.parse(end) - (1440 - i * width) * 60_000;
        return { timestamp: new Date(time).toISOString(), available_at: new Date(time + width * 60_000).toISOString(),
          open: '100', high: '103', low: '99', close: '101', is_complete: width === 1 };
      });
      await route.fulfill({ json: { instrument_id: instrument, timeframe, end, bars,
        calendar_status: 'unverified', analysis_ready: false, aggregation: 'display_only', detail: null } }); return;
    }
    await route.fulfill({ status: 422, json: { detail: 'trading date is outside verified calendar coverage' } });
  });
  await page.addInitScript(({ key, mode }) => {
    if (localStorage.getItem(key)) return;
    localStorage.setItem(key, JSON.stringify({ version: 3, activeId: 'one', tabs: [
      { id: 'one', instrument: 'US30', sourceId: mode === 'history' ? 'offline-replay-us30-v3' : '',
        timezone: 'Europe/London', mode, liveConnect: true },
      { id: 'two', instrument: 'DAX', sourceId: '', timezone: 'Europe/London', mode: 'live', liveConnect: true },
    ] }));
  }, { key, mode });
  return { reads, connections };
}

test('live chart loads 1440 provider prices despite rejected calendar preparation, and reconnects lazily', async ({ page }) => {
  const { reads, connections } = await priceOnly(page);
  await page.goto('/');
  await expect(page.getByRole('img', { name: /1440 visible bars/ })).toBeVisible();
  await expect(page.getByText(/Provider prices loaded independently/)).toBeVisible();
  expect([...new Set(connections)]).toEqual(['US30']);
  expect(reads.every(url => url.includes('/US30?'))).toBe(true);
  await page.getByRole('tab', { name: 'DAX Live', exact: true }).click();
  await expect.poll(() => [...new Set(connections)]).toEqual(['US30', 'DAX']);
  await expect(page.getByRole('img', { name: /1440 visible bars/ })).toBeVisible();
});

test('price-only live chart can move to unverified history, load older candles and return latest', async ({ page }) => {
  await priceOnly(page);
  await page.goto('/');
  await expect(page.getByRole('img', { name: /1440 visible bars/ })).toBeVisible();
  await page.getByLabel('Chart date and time').fill('2022-01-03T15:00');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.getByText('Provider history', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Load earlier candles', exact: true }).click();
  await expect(page.getByRole('img', { name: /2880 visible bars/ })).toBeVisible();
  await page.getByRole('button', { name: 'Return to latest', exact: true }).click();
  await expect(page.getByText('Live provider', { exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: /1440 visible bars/ })).toBeVisible();
  await page.getByLabel('Chart timeframe').selectOption('5m');
  await expect(page.getByRole('img', { name: /288 visible bars/ })).toBeVisible();
  await expect(page.getByText(/calendar\/session coverage remains unverified/)).toBeVisible();
});

test('historical browsing loads prices outside verified coverage without starting detector replay', async ({ page }) => {
  const { reads, connections } = await priceOnly(page, 'history');
  let runs = 0;
  page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/analysis') ++runs; });
  await page.goto('/');
  await expect(page.getByRole('img', { name: /300 visible bars/ })).toBeVisible();
  await page.getByLabel('Chart date and time').fill('2022-01-03T15:00');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect.poll(() => reads.some(url => url.includes('end=2022-'))).toBe(true);
  await expect(page.getByText('Provider history', { exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: /1440 visible bars/ })).toBeVisible();
  await page.getByRole('button', { name: 'Load earlier candles', exact: true }).click();
  await expect(page.getByRole('img', { name: /2880 visible bars/ })).toBeVisible();
  expect(runs).toBe(0);
  expect(connections).toEqual([]);
  await page.reload();
  await expect(page.getByText('Provider history', { exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: /1440 visible bars.*2022-/ })).toBeVisible();
});

test('an empty older-price response preserves the loaded chart', async ({ page }) => {
  await priceOnly(page);
  await page.route('**/api/live/prices/US30?*', async route => {
    const url = new URL(route.request().url());
    const end = url.searchParams.get('end');
    if (!end) { await route.fallback(); return; }
    await route.fulfill({ json: { instrument_id: 'US30', timeframe: '1m', end, bars: [],
      calendar_status: 'unverified', analysis_ready: false, aggregation: 'display_only', detail: null } });
  });
  await page.goto('/');
  await expect(page.getByRole('img', { name: /1440 visible bars/ })).toBeVisible();
  await page.getByRole('button', { name: 'Load earlier candles', exact: true }).click();
  await expect(page.getByText('No earlier provider prices were returned.', { exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: /1440 visible bars/ })).toBeVisible();
});


test('manual detector calculation uses provider candles despite unverified calendar coverage', async ({ page }) => {
  await priceOnly(page);
  const calculations: Record<string, unknown>[] = [];
  await page.route('**/api/analysis', async route => {
    calculations.push(route.request().postDataJSON());
    expect(route.request().headers().prefer).toBe('respond-async');
    await route.fulfill({ status: 202, json: { job_id: 'provider-job', status: 'pending' } });
  });
  await page.route('**/api/analysis/tasks/provider-job', route => route.fulfill({ json: {
    job_id: 'provider-job', status: 'ready', result: { run_id: 'provider-calculation' },
  } }));
  await page.route('**/api/analysis/provider-calculation/view?*', route => route.fulfill({ json: {
    events: [], observations: [], stream_coverage: [{ pattern_id: 'RANGE_COMPRESSION_V1', timeframe: '1m',
      status: 'calculated', valid_from: '2026-11-05T22:00:00Z', available_from: '2026-11-05T22:00:00Z',
      valid_to: '2026-11-06T22:00:00Z', processed: 1440, required: 6 }],
  } }));
  await page.route('**/api/analysis/provider-calculation/stop', route => route.fulfill({ json: {} }));
  await page.goto('/');
  await expect(page.getByRole('img', { name: /1440 visible bars/ })).toBeVisible();
  await page.getByRole('tab', { name: 'Detector settings', exact: true }).click();
  const calculate = page.getByRole('button', { name: 'Calculate RANGE_COMPRESSION_V1 for current view · 1m', exact: true });
  await expect(calculate).toBeEnabled();
  await calculate.click();
  await expect.poll(() => calculations.length).toBe(1);
  expect(calculations[0].dataset_revision_id).toBe('provider-observed-fixture');
  expect(calculations[0].mode).toBe('history');
  await expect(page.getByText('Calculating historical detectors…')).toHaveCount(0);
  await expect(page.getByText(/require validated calendar/)).toHaveCount(0);
});
