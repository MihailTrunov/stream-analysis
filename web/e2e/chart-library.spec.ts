import { expect, test } from './fixtures';

test('default four-hour offline chart has usable scales and stays cursor-bounded', async ({ page }) => {
  test.setTimeout(150_000);
  await page.goto('/');
  await page.locator('details.source-settings > summary').click();
  await expect(page.getByRole('combobox', { name: 'Dataset revision' }))
    .toHaveValue('offline-replay-us30-v3');
  await expect(page.getByText(/Synthetic demo only — not research-grade/)).toBeVisible();
  await page.getByRole('button', { name: 'Start replay from' }).click();
  await expect(page.getByText(/Replay created/)).toBeVisible();
  const region = page.getByRole('tabpanel', { name: 'US30 Replay', exact: true });
  const runId = await region.locator('dd code').first().textContent();
  await page.getByRole('button', { name: 'Next detector event' }).click();
  const chart = page.getByRole('img', { name: /Interactive UK time candlestick chart with price and time scales/ });
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
  expect(payload.bars.every((bar) => Date.parse(bar.timestamp) <= Date.parse(snapshot.cursor_time))).toBe(true);
  expect(payload.events.every((event) => Date.parse(event.detection_time) <= Date.parse(snapshot.cursor_time))).toBe(true);
  await chart.hover();
  const beforeZoom = await chart.screenshot();
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  expect((await chart.screenshot()).equals(beforeZoom)).toBe(false);
  await page.getByRole('button', { name: 'Stop replay' }).click();
});
