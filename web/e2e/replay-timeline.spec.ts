import { expect, test } from '@playwright/test';

test('event timeline selection focuses chart without advancing replay', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Launch walkthrough' })).toBeEnabled({ timeout: 60_000 });
  await page.getByRole('button', { name: 'Launch walkthrough' }).click();
  const timeline = page.getByRole('region', { name: 'Detector event timeline' });
  await expect(timeline.getByTestId('timeline-event')).toHaveCount(0);
  await page.getByRole('button', { name: 'Next detector event' }).click();
  const cursor = await page.getByText(/Replay paused ·/).textContent();
  const event = timeline.getByTestId('timeline-event');
  await expect(event).toHaveCount(1);
  await expect(event).toContainText('persistence_confirmed');
  await event.click();
  await expect(event).toHaveAttribute('aria-current', 'true');
  await expect(page.getByTestId('pattern-annotation')).toHaveAttribute('data-selected', 'true');
  await expect(page.getByText(/Chart focused on event detected at/)).toBeVisible();
  expect(await page.getByText(/Replay paused ·/).textContent()).toBe(cursor);
  await page.getByRole('button', { name: 'Return to live cursor' }).click();
  await expect(page.getByText(/Chart focused on event detected at/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Stop walkthrough' }).click();
});
