import { expect, test } from '@playwright/test';

test.afterEach(async ({ page }) => {
  const stop = page.getByRole('button', { name: 'Stop walkthrough' });
  if (await stop.isVisible()) await stop.click();
});

test('play, pause, speed and terminal controls follow replay lifecycle', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Launch walkthrough' }).click();
  await page.getByRole('combobox', { name: 'Playback speed' }).selectOption('0.5');
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(page.getByText(/Replay running/)).toBeVisible();
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  await expect(page.getByText(/Replay paused/)).toBeVisible();
  await expect(page.getByTestId('candle')).toHaveCount(0);
  await page.getByRole('combobox', { name: 'Playback speed' }).selectOption('4');
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(page.getByText(/Replay completed/)).toBeVisible({ timeout: 10000 });
  await expect(page.getByTestId('candle')).toHaveCount(3);
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Next detector event' })).toBeDisabled();
});

test('next event, seek and reset create causal fresh run views', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Launch walkthrough' }).click();
  const region = page.getByRole('region', { name: 'Detector walkthrough' });
  const firstRun = await region.locator('dd code').first().textContent();
  await page.getByRole('button', { name: 'Next detector event' }).click();
  await expect(page.getByText(/Stopped on persistence_confirmed/)).toBeVisible();
  await expect(page.getByTestId('candle')).toHaveCount(2);
  await page.reload();
  await expect(page.getByTestId('candle')).toHaveCount(2);
  expect(await region.locator('dd code').first().textContent()).toBe(firstRun);
  await page.getByRole('button', { name: 'Reset replay' }).click();
  await expect(page.getByTestId('candle')).toHaveCount(0);
  const resetRun = await region.locator('dd code').first().textContent();
  expect(resetRun).not.toBe(firstRun);
  await page.getByRole('textbox', { name: 'Seek time UTC' }).fill('2026-01-05T12:12');
  await page.getByRole('button', { name: 'Seek', exact: true }).click();
  await expect(page.getByTestId('candle')).toHaveCount(2);
  const soughtRun = await region.locator('dd code').first().textContent();
  expect(soughtRun).not.toBe(resetRun);
  await expect(page.getByText(/persistence_confirmed/).first()).toBeVisible();
});
