import { expect, test } from './fixtures';
import { selectShortDemo } from './short-demo';

test.afterEach(async ({ page }) => {
  const stop = page.getByRole('button', { name: 'Stop replay' });
  if (await stop.isEnabled()) await stop.click();
});

test('play, pause, speed and terminal controls follow replay lifecycle', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from' })).toBeEnabled();
  await selectShortDemo(page);
  await page.getByRole('button', { name: 'Start replay from' }).click();
  await page.getByRole('combobox', { name: 'Playback speed' }).selectOption('0.5');
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(page.getByText(/Replay running/)).toBeVisible();
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  await expect(page.getByText(/Replay paused/)).toBeVisible();
  await expect(page.getByRole('img', { name: /11 visible bars/ })).toBeVisible();
  await page.getByRole('combobox', { name: 'Playback speed' }).selectOption('4');
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(page.getByText(/Replay completed/)).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole('img', { name: /14 visible bars/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Next detector event' })).toBeDisabled();
});

test('next event, viewport navigation and reset preserve causal run identity', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from' })).toBeEnabled();
  await selectShortDemo(page);
  await page.getByRole('button', { name: 'Start replay from' }).click();
  await expect(page.getByText(/Replay paused/)).toBeVisible();
  const region = page.getByRole('tabpanel', { name: 'US30 Replay', exact: true });
  const firstRun = await region.locator('dd code').first().textContent();
  await page.getByRole('button', { name: 'Next detector event' }).click();
  await expect(page.getByText(/Stopped on persistence_confirmed/)).toBeVisible();
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  expect(await region.locator('dd code').first().textContent()).toBe(firstRun);
  await page.getByLabel('Chart date and time').fill('2026-01-05T12:11');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  expect(await region.locator('dd code').first().textContent()).toBe(firstRun);
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  await page.getByRole('button', { name: 'Reset replay' }).click();
  await expect(page.getByRole('img', { name: /11 visible bars/ })).toBeVisible();
  const resetRun = await region.locator('dd code').first().textContent();
  expect(resetRun).not.toBe(firstRun);
  await page.getByRole('button', { name: 'Next detector event' }).click();
  await expect(page.getByRole('img', { name: /13 visible bars/ })).toBeVisible();
  expect(await region.locator('dd code').first().textContent()).toBe(resetRun);
});
