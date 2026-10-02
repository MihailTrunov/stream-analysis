import { expect, test } from '@playwright/test';
import { selectShortDemo } from './short-demo';

test('pinned offline replay reveals only current and past candles', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Detector walkthrough' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Launch walkthrough' })).toBeEnabled();
  await selectShortDemo(page);
  await page.getByRole('button', { name: 'Launch walkthrough' }).click();
  await expect(page.getByText(/Replay created/)).toBeVisible();
  await expect(page.getByRole('img', { name: /0 visible bars/ })).toBeVisible();

  await page.getByRole('button', { name: 'Step one visible bar' }).click();
  await expect(page.getByRole('img', { name: /1 visible bars/ })).toBeVisible();
  expect(await page.locator('.trading-chart canvas').count()).toBeGreaterThan(1);
  const runId = await page.getByRole('region', { name: 'Detector walkthrough' }).locator('dd code').first().textContent();
  const response = await page.request.get(`/api/replay/${runId}/view`, { params: {
    start: '2026-01-05T12:11:00Z', end: '2026-01-05T12:14:00Z',
  } });
  expect(response.ok()).toBe(true);
  const first = await response.json() as { bars: Array<{ timestamp: string }> };
  expect(first.bars).toHaveLength(1);
  expect(first.bars[0].timestamp).toBe('2026-01-05T12:11:00Z');
  await expect(page.getByText(/Current market state:/)).toBeVisible();
  await page.getByRole('checkbox', { name: 'Sessions' }).uncheck();
  await expect(page.getByRole('img', { name: /1 visible bars/ })).toBeVisible();
  await page.getByRole('checkbox', { name: 'Sessions' }).check();
  await expect(page.getByRole('img', { name: /1 visible bars/ })).toBeVisible();

  await page.getByRole('button', { name: 'Step one visible bar' }).click();
  await expect(page.getByRole('img', { name: /2 visible bars/ })).toBeVisible();
  const second = await page.request.get(`/api/replay/${runId}/view`, { params: {
    start: '2026-01-05T12:11:00Z', end: '2026-01-05T12:14:00Z',
  } });
  const secondView = await second.json() as { bars: Array<{ timestamp: string }> };
  expect(secondView.bars).toHaveLength(2);
  expect(secondView.bars.some((bar) => bar.timestamp === '2026-01-05T12:13:00Z')).toBe(false);
  await page.getByRole('button', { name: 'Stop walkthrough' }).click();
});
