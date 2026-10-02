import { expect, test } from '@playwright/test';

test('default four-hour offline chart has usable scales and stays cursor-bounded', async ({ page }) => {
  test.setTimeout(150_000);
  await page.goto('/');
  await expect(page.getByRole('combobox', { name: 'Dataset revision' }))
    .toHaveValue('offline-replay-us30-v3');
  await expect(page.getByText(/Synthetic demo only — not research-grade/)).toBeVisible();
  await page.getByRole('button', { name: 'Launch walkthrough' }).click();
  const region = page.getByRole('region', { name: 'Detector walkthrough' });
  const runId = await region.locator('dd code').first().textContent();
  await page.getByRole('button', { name: 'Next detector event' }).click();
  const chart = page.getByRole('img', { name: /Interactive UTC candlestick chart with price and time scales/ });
  await expect(chart).toBeVisible();
  const box = await chart.boundingBox();
  expect(box?.width).toBeGreaterThan(500);
  expect(box?.height).toBeGreaterThan(400);
  expect(await chart.locator('canvas').count()).toBeGreaterThan(1);
  const cursor = await page.getByText(/Replay paused ·/).textContent();
  await chart.hover();
  await page.mouse.wheel(0, -300);
  await page.mouse.down();
  await page.mouse.move((box?.x ?? 0) + 100, (box?.y ?? 0) + 150);
  await page.mouse.up();
  expect(await page.getByText(/Replay paused ·/).textContent()).toBe(cursor);
  const state = await page.request.get(`/api/replay/${runId}`);
  const snapshot = await state.json() as { cursor_time: string };
  const view = await page.request.get(`/api/replay/${runId}/view`, { params: {
    start: '2026-01-05T10:00:00Z', end: '2026-01-05T14:00:00Z', limit: 500,
  } });
  const payload = await view.json() as { bars: Array<{ timestamp: string }>; events: Array<{ detection_time: string }> };
  expect(payload.bars.length).toBeGreaterThan(0);
  expect(payload.bars.every((bar) => bar.timestamp <= snapshot.cursor_time)).toBe(true);
  expect(payload.events.every((event) => event.detection_time <= snapshot.cursor_time)).toBe(true);
  await page.getByRole('textbox', { name: 'Seek time UTC' }).fill('2026-01-05T13:59');
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  await expect(page.getByRole('img', { name: /240 visible bars/ })).toBeVisible({ timeout: 30_000 });
  await chart.hover();
  const beforeZoom = await chart.screenshot();
  await page.mouse.wheel(0, -400);
  expect((await chart.screenshot()).equals(beforeZoom)).toBe(false);
  await page.getByRole('button', { name: 'Stop walkthrough' }).click();
});
