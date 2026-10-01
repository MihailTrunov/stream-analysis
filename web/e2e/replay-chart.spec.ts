import { expect, test } from '@playwright/test';

test('pinned offline replay reveals only current and past candles', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Detector walkthrough' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Launch walkthrough' })).toBeEnabled();
  await page.getByRole('button', { name: 'Launch walkthrough' }).click();
  await expect(page.getByText(/Replay created/)).toBeVisible();
  await expect(page.getByTestId('candle')).toHaveCount(0);

  const firstPayload = page.waitForResponse((response) =>
    response.url().includes('/api/replay/') && response.url().includes('/bars?') && response.status() === 200);
  await page.getByRole('button', { name: 'Step one visible bar' }).click();
  const first = await (await firstPayload).json() as { bars: Array<{ timestamp: string }> };
  expect(first.bars).toHaveLength(1);
  expect(first.bars[0].timestamp).toBe('2026-01-05T12:11:00Z');
  await expect(page.getByTestId('candle')).toHaveCount(1);
  await expect(page.locator('[data-testid="candle"][data-time="2026-01-05T12:12:00Z"]')).toHaveCount(0);

  await page.getByRole('button', { name: 'Step one visible bar' }).click();
  await expect(page.getByTestId('candle')).toHaveCount(2);
  await expect(page.locator('[data-testid="candle"][data-time="2026-01-05T12:13:00Z"]')).toHaveCount(0);
});
