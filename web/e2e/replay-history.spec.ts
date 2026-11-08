import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

async function smallRecordedViewport(page: Page, delayedEnd?: string, wait?: Promise<void>) {
  let reads = 0;
  await page.route('**/api/analysis/*/view?*', async (route) => {
    ++reads;
    const response = await route.fetch();
    const data = await response.json();
    const params = new URL(route.request().url()).searchParams;
    if (Date.parse(params.get('end')!) === Date.parse('2026-01-05T14:00:00Z') && params.get('timeframe') === '1m') {
      data.bars = data.bars.slice(-10);
      const times = new Set(data.bars.map((bar: { timestamp: string }) => bar.timestamp));
      data.observations = data.observations.filter((frame: { timestamp: string }) => times.has(frame.timestamp));
    }
    if (delayedEnd && Date.parse(params.get('end')!) === Date.parse(delayedEnd)) await wait;
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Start replay from', exact: true }).click();
  await expect(page.getByRole('img', { name: /10 visible bars/ })).toBeVisible();
  return { reads: () => reads };
}
async function current(page: Page) {
  return (await (await page.request.get('/api/analysis/active')).json()).sessions[0];
}
async function browseOld(page: Page) {
  await page.getByLabel('Chart date and time').fill('2026-01-05T09:10');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.getByRole('img', { name: /36 visible bars/ })).toBeVisible();
  await expect(page.locator('.focus-notice')).toContainText('09:10');
}

test('active replay restores manually zoomed geometry without refocusing its saved target', async ({ page }) => {
  await smallRecordedViewport(page);
  await browseOld(page);
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  const savedViewport = () => page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].viewport);
  await expect.poll(async () => (await savedViewport())?.from).toBeGreaterThan(1);
  const before = await savedViewport();
  const run = await current(page);
  await page.reload();
  await expect(page.getByRole('img', { name: /36 visible bars/ })).toBeVisible();
  await expect(page.locator('.focus-notice')).toContainText('09:10');
  await page.waitForTimeout(300);
  const after = await savedViewport();
  expect(after.origin).toBe(before.origin);
  expect(after.from).toBeCloseTo(before.from, 1);
  expect(after.to).toBeCloseTo(before.to, 1);
  expect(await current(page)).toEqual(run);
  // An explicit new navigation must still override the restored viewport.
  await page.getByLabel('Chart date and time').fill('2026-01-05T09:05');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.locator('.focus-notice')).toContainText('09:05');
  await expect.poll(async () => (await savedViewport()).from).toBeLessThan(1);
});

test('latest loaded Move to target survives timeframe changes and reload without another price read', async ({ page }) => {
  const fixture = await smallRecordedViewport(page);
  await browseOld(page);
  const reads = fixture.reads();
  const run = await current(page);
  await page.getByLabel('Chart date and time').fill('2026-01-05T09:30');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.locator('.focus-notice')).toContainText('09:30');
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].replayWindow?.target)).toBe('2026-01-05T09:30:00.000Z');
  expect(fixture.reads()).toBe(reads);
  await page.getByLabel('Chart timeframe').selectOption('5m');
  await expect(page.getByRole('img', { name: /12 visible bars/ })).toBeVisible();
  await expect(page.locator('.focus-notice')).toContainText('09:30');
  await page.reload();
  await expect(page.getByRole('img', { name: /12 visible bars/ })).toBeVisible();
  await expect(page.locator('.focus-notice')).toContainText('09:30');
  expect(await current(page)).toEqual(run);
});

test('older revealed context loads without advancing the run and restores with its tab', async ({ page }) => {
  await smallRecordedViewport(page);
  let acquisitions = 0;
  page.on('request', (request) => { if (request.url().includes('/api/history/')) ++acquisitions; });
  const before = await current(page);
  await browseOld(page);
  expect(await current(page)).toEqual(before);
  expect(acquisitions).toBe(0);
  await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].replayWindow?.target)).toBe('2026-01-05T09:10:00.000Z');
  await page.reload();
  await expect(page.getByRole('img', { name: /36 visible bars/ })).toBeVisible();
  await expect(page.locator('.focus-notice')).toContainText('09:10');
  expect((await current(page)).run_id).toBe(before.run_id);
  expect((await current(page)).cursor_index).toBe(before.cursor_index);
});

test('Step progresses analysis while the older window stays fixed and Return reloads the cursor', async ({ page }) => {
  await smallRecordedViewport(page);
  await browseOld(page);
  const before = await current(page);
  await page.getByRole('button', { name: 'Step one visible bar', exact: true }).click();
  await expect(page.getByText(/1 replay bars revealed/)).toBeVisible();
  await expect(page.getByRole('img', { name: /36 visible bars/ })).toBeVisible();
  expect((await current(page)).cursor_index).toBe(before.cursor_index + 1);
  await page.getByRole('button', { name: 'Return to replay cursor', exact: true }).click();
  await expect(page.getByRole('img', { name: /visible bars through 2026-01-05T10:00:00Z/ })).toBeVisible();
  expect((await current(page)).cursor_index).toBe(before.cursor_index + 1);
});

test('timeframe browsing stays causal and a future point never requests another window', async ({ page }) => {
  const fixture = await smallRecordedViewport(page);
  await browseOld(page);
  const before = await current(page);
  await page.getByLabel('Chart timeframe').selectOption('5m');
  await expect(page.getByRole('img', { name: /12 visible bars through 2026-01-05T09:55:00Z/ })).toBeVisible();
  await expect(page.locator('.focus-notice')).toContainText('09:10');
  const reads = fixture.reads();
  await page.getByLabel('Chart date and time').fill('2026-01-05T10:01');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('already revealed');
  expect(fixture.reads()).toBe(reads);
  expect(await current(page)).toEqual(before);
});

test('a delayed old window cannot replace a returned cursor view', async ({ page }) => {
  let release = () => {};
  const waiting = new Promise<void>((resolve) => { release = resolve; });
  try {
    await smallRecordedViewport(page, '2026-01-05T09:36:00Z', waiting);
    const before = await current(page);
    await page.getByLabel('Chart date and time').fill('2026-01-05T09:10');
    await page.getByRole('button', { name: 'Move to', exact: true }).click();
    await expect(page.getByText('Loading recorded replay history…')).toBeVisible();
    await page.getByRole('button', { name: 'Return to replay cursor', exact: true }).click();
    await expect(page.getByRole('img', { name: /10 visible bars/ })).toBeVisible();
    release();
    await page.waitForTimeout(250);
    await expect(page.getByRole('img', { name: /10 visible bars/ })).toBeVisible();
    expect(await current(page)).toEqual(before);
  } finally { release(); }
});

test('replay left-edge dragging reads earlier recorded candles without fetching provider history', async ({ page }) => {
  await smallRecordedViewport(page);
  const before = await current(page);
  const box = (await page.locator('.trading-chart').boundingBox())!;
  await page.mouse.move(box.x + 150, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 750, box.y + box.height / 2, { steps: 12 });
  await page.mouse.up();
  await expect(page.getByRole('img', { name: /60 visible bars/ })).toBeVisible();
  expect(await current(page)).toEqual(before);
});

test('Stop retains browsed candles and zoom through exploration and reload instead of loading the future tail', async ({ page }) => {
  await smallRecordedViewport(page);
  await browseOld(page);
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].viewport?.origin)).toBe('2026-01-05T09:00:00Z');
  await page.waitForTimeout(250);
  const before = await page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].viewport);
  await page.getByRole('button', { name: 'Stop replay', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Historical exploration' })).toBeVisible();
  await expect(page.getByRole('img', { name: /36 visible bars through 2026-01-05T09:35:00Z/ })).toBeVisible();
  await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].historyEnd)).toBe('2026-01-05T09:36:00Z');
  await page.waitForTimeout(300);
  const stopped = await page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0]);
  expect(stopped.viewport.from).toBeCloseTo(before.from, 1);
  expect(stopped.viewport.to).toBeCloseTo(before.to, 1);
  expect(stopped.replayWindow).toBeUndefined();
  expect((await (await page.request.get('/api/analysis/active')).json()).sessions).toHaveLength(0);
  await page.reload();
  await expect(page.getByRole('img', { name: /36 visible bars/ })).toBeVisible();
  await page.waitForTimeout(300);
  const restored = await page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].viewport);
  expect(restored.from).toBeCloseTo(before.from, 1);
  expect(restored.to).toBeCloseTo(before.to, 1);
  await page.getByRole('button', { name: 'Return to latest', exact: true }).click();
  await expect(page.getByRole('img', { name: /300 visible bars/ })).toBeVisible();
});
