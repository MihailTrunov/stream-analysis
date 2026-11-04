import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

async function providerFixture(page: Page, options: { terminal?: string; pending?: boolean; replay?: boolean } = {}) {
  let base: Record<string, unknown>;
  let published = false;
  let ready = !options.pending;
  let plans = 0;
  let publications = 0;
  let polls = 0;
  let requested: Record<string, unknown> = {};
  await page.route('**/api/replay/sources', async (route) => {
    const response = await route.fetch();
    const catalog = await response.json();
    base = catalog.sources.find((item: { dataset_revision_id: string }) => item.dataset_revision_id === 'offline-replay-us30-v3');
    catalog.sources = catalog.sources.map((item: { dataset_revision_id: string }) => item.dataset_revision_id === base.dataset_revision_id
      ? { ...base, non_research_grade: false, range_start: published ? base.range_start : '2026-01-05T10:00:00Z' } : item);
    await route.fulfill({ json: catalog });
  });
  await page.route('**/api/history/navigation', async (route) => {
    ++plans; requested = route.request().postDataJSON();
    await route.fulfill({ json: { request_ids: ['request-one'], instrument_id: 'US30', target: requested.target,
      start: '2026-01-05T09:00:00Z', end: options.replay ? requested.end ?? '2026-01-05T14:00:00Z' : '2026-01-05T10:00:00Z',
      timeframe: requested.timeframe, intent: requested.intent, initialization: 'available' } });
  });
  await page.route('**/api/history/requests/request-one', async (route) => {
    ++polls;
    await route.fulfill({ json: { request_id: 'request-one', status: options.terminal ?? (ready ? 'available' : 'pending'), source_manifest_hash: 'reviewed-hash' } });
  });
  await page.route('**/api/history/inputs', async (route) => {
    ++publications; published = true;
    expect(route.request().postDataJSON()).toEqual({ requests: { 'request-one': 'reviewed-hash' } });
    // The provider transport is faked; retained prices and replay are served by
    // the real seeded API so the UI cannot invent new/future source candles.
    await route.fulfill({ json: { ...base, source_dataset_id: base.source_dataset_id } });
  });
  return { allow: () => { ready = true; }, plans: () => plans, publications: () => publications,
    polls: () => polls, requested: () => requested };
}

test('Move to loads an unloaded retained window and restores it without creating a replay', async ({ page }) => {
  await page.route('**/api/replay/sources/offline-replay-us30-v3/bars?*', async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    if (Date.parse(new URL(route.request().url()).searchParams.get('end')!) === Date.parse('2026-01-05T14:00:00Z')) data.bars = data.bars.slice(-100);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  const chart = page.getByRole('img', { name: /candlestick chart/ });
  await expect(chart).toHaveAccessibleName(/100 visible bars/);
  await page.getByLabel('Chart date and time').fill('2026-01-05T10:00');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(chart).toHaveAccessibleName(/86 visible bars/);
  await expect(page.locator('.focus-notice')).toContainText('10:00');
  await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].historyEnd)).toBe('2026-01-05T10:26:00.000Z');
  expect((await (await page.request.get('/api/analysis/active')).json()).sessions).toHaveLength(0);
  await page.reload();
  await expect(chart).toHaveAccessibleName(/86 visible bars/);
});

test('earlier-history control replaces a bounded window and stops at the synthetic limit', async ({ page }) => {
  await page.route('**/api/replay/sources/offline-replay-us30-v3/bars?*', async (route) => {
    const response = await route.fetch(); const data = await response.json();
    if (Date.parse(new URL(route.request().url()).searchParams.get('end')!) === Date.parse('2026-01-05T14:00:00Z')) data.bars = data.bars.slice(-100);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await expect(page.getByRole('img', { name: /100 visible bars/ })).toBeVisible();
  await page.getByRole('button', { name: 'Load earlier candles', exact: true }).click();
  await expect(page.getByRole('img', { name: /225 visible bars/ })).toBeVisible();
  await page.getByRole('button', { name: 'Load earlier candles', exact: true }).click();
  await expect(page.getByText('Reached the beginning of this synthetic dataset.')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('pending provider navigation survives reload, polls only the focused tab and publishes reviewed history', async ({ page }) => {
  const provider = await providerFixture(page, { pending: true });
  await page.goto('/');
  await expect(page.getByRole('img', { name: /240 visible bars/ })).toBeVisible();
  await page.getByLabel('Chart date and time').fill('2026-01-05T09:30');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.getByText(/Waiting for historical data/)).toBeVisible();
  await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].navigationPlan?.request_ids)).toEqual(['request-one']);
  await page.reload();
  await expect(page.getByText(/Waiting for historical data/)).toBeVisible();
  expect(provider.plans()).toBe(1);
  await page.getByRole('button', { name: 'New analysis tab' }).click();
  await expect(page.locator('.trading-chart')).toHaveCount(1);
  const before = provider.polls();
  await page.waitForTimeout(1500);
  expect(provider.polls()).toBe(before);
  provider.allow();
  await page.getByRole('tab', { name: /US30.*History/ }).first().click();
  await expect(page.locator('.focus-notice')).toContainText('09:30');
  await expect(page.getByRole('img', { name: /60 visible bars/ })).toBeVisible();
  expect(provider.publications()).toBe(1);
  expect((await (await page.request.get('/api/analysis/active')).json()).sessions).toHaveLength(0);
});

for (const [status, message] of [['unavailable', 'unavailable'], ['failed', 'loading failed'], ['unexpected_gap', 'unexpected gap'], ['conflict', 'conflicting prices']]) {
  test(`navigation exposes ${status} without publishing partial history`, async ({ page }) => {
    const provider = await providerFixture(page, { terminal: status });
    await page.goto('/');
    await expect(page.getByRole('img', { name: /240 visible bars/ })).toBeVisible();
    await page.getByLabel('Chart date and time').fill('2026-01-05T09:30');
    await page.getByRole('button', { name: 'Move to', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText(message);
    expect(provider.publications()).toBe(0);
    await expect(page.getByRole('img', { name: /240 visible bars/ })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Retry navigation' })).toBeEnabled();
    await page.getByRole('button', { name: 'Cancel navigation' }).count().then((count) => expect(count).toBe(0));
  });
}

test('cancelling pending navigation leaves the old chart and ignores later ready coverage', async ({ page }) => {
  const provider = await providerFixture(page, { pending: true });
  await page.goto('/');
  await expect(page.getByRole('img', { name: /240 visible bars/ })).toBeVisible();
  await page.getByLabel('Chart date and time').fill('2026-01-05T09:30');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.getByText(/Waiting for historical data/)).toBeVisible();
  await page.getByRole('button', { name: 'Cancel navigation', exact: true }).click();
  provider.allow();
  await page.waitForTimeout(1200);
  expect(provider.publications()).toBe(0);
  await expect(page.getByRole('img', { name: /240 visible bars/ })).toBeVisible();
  await expect(page.locator('.focus-notice')).toHaveCount(0);
});

test('provider replay prepares initialization, fixes an explicit end and blocks navigation into future data', async ({ page }) => {
  const provider = await providerFixture(page, { replay: true });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.locator('details.source-settings > summary').click();
  await page.getByLabel('Replay end', { exact: true }).fill('2026-01-05T12:30');
  await page.getByRole('button', { name: 'Start replay from', exact: true }).click();
  await expect(page.getByText(/Replay paused/)).toBeVisible();
  const original = (await (await page.request.get('/api/analysis/active')).json()).sessions[0];
  expect(original.visible_bars).toBe(0);
  expect(Date.parse(original.selected_end)).toBe(Date.parse('2026-01-05T12:30:00Z'));
  expect(Date.parse(String(provider.requested().end))).toBe(Date.parse(original.selected_end));
  await page.getByLabel('Chart date and time').fill('2026-01-05T13:00');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('already revealed');
  const after = (await (await page.request.get('/api/analysis/active')).json()).sessions[0];
  expect(after.cursor_index).toBe(original.cursor_index);
  expect(provider.plans()).toBe(1);
  await expect(page.getByRole('img', { name: /60 visible bars/ })).toBeVisible();
});

test('a blank provider replay end requests and displays the fixed default endpoint', async ({ page }) => {
  const provider = await providerFixture(page, { replay: true });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.locator('details.source-settings > summary').click();
  await expect(page.getByLabel('Replay end', { exact: true })).toHaveValue('');
  await page.getByRole('button', { name: 'Start replay from', exact: true }).click();
  await expect(page.getByText(/Replay paused/)).toBeVisible();
  expect(provider.requested().end).toBeUndefined();
  await expect(page.getByLabel('Replay end', { exact: true })).toHaveValue('2026-01-05T14:00');
});

test('panning to the left edge automatically requests an earlier bounded window', async ({ page }) => {
  await page.route('**/api/replay/sources/offline-replay-us30-v3/bars?*', async (route) => {
    const response = await route.fetch(); const data = await response.json();
    if (Date.parse(new URL(route.request().url()).searchParams.get('end')!) === Date.parse('2026-01-05T14:00:00Z')) data.bars = data.bars.slice(-100);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await expect(page.getByRole('img', { name: /100 visible bars/ })).toBeVisible();
  const box = (await page.locator('.trading-chart').boundingBox())!;
  await page.mouse.move(box.x + 180, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 800, box.y + box.height / 2, { steps: 12 });
  await page.mouse.up();
  await expect(page.getByRole('img', { name: /225 visible bars/ })).toBeVisible();
  expect((await (await page.request.get('/api/analysis/active')).json()).sessions).toHaveLength(0);
});

test('an obsolete local history response cannot overwrite a changed chart timeframe', async ({ page }) => {
  let release = () => {};
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/replay/sources/offline-replay-us30-v3/bars?*', async (route) => {
    const response = await route.fetch(); const data = await response.json();
    const end = Date.parse(new URL(route.request().url()).searchParams.get('end')!);
    if (end === Date.parse('2026-01-05T14:00:00Z')) data.bars = data.bars.slice(-100);
    else await delayed;
    await route.fulfill({ json: data });
  });
  try {
    await page.goto('/');
    await expect(page.getByRole('img', { name: /100 visible bars/ })).toBeVisible();
    await page.getByLabel('Chart date and time').fill('2026-01-05T10:00');
    await page.getByRole('button', { name: 'Move to', exact: true }).click();
    await expect(page.getByText('Loading earlier chart candles…')).toBeVisible();
    await page.getByLabel('Chart timeframe').selectOption('5m');
    await expect(page.getByRole('img', { name: /60 visible bars/ })).toBeVisible();
    release();
    await page.waitForTimeout(300);
    await expect(page.getByRole('img', { name: /60 visible bars/ })).toBeVisible();
    await expect(page.getByLabel('Chart timeframe')).toHaveValue('5m');
    await expect(page.locator('.focus-notice')).toHaveCount(0);
  } finally { release(); }
});

test('a recovered server replay restores its selected dates instead of the source defaults', async ({ page }) => {
  const defaults = await (await page.request.get('/api/analysis-defaults?instrument_id=US30')).json();
  const response = await page.request.post('/api/analysis', { data: {
    dataset_revision_id: 'offline-replay-us30-v3', instrument_id: 'US30',
    selected_start: '2026-01-05T11:00:00Z', selected_end: '2026-01-05T12:30:00Z',
    streams: [{ config: defaults.detection_config }],
  } });
  expect(response.status()).toBe(201);
  await page.goto('/');
  await page.getByRole('tab', { name: /US30.*Replay/ }).click();
  await expect(page.getByText(/Replay paused/)).toBeVisible();
  await expect(page.getByLabel('Chart date and time')).toHaveValue('2026-01-05T11:00');
  await page.locator('details.source-settings > summary').click();
  await expect(page.getByLabel('Replay end', { exact: true })).toHaveValue('2026-01-05T12:30');
});
