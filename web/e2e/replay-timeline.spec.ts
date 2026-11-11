import { expect, test } from './fixtures';
import { selectShortDemo } from './short-demo';

test('event timeline selection focuses chart without advancing replay', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from' })).toBeEnabled({ timeout: 60_000 });
  await selectShortDemo(page);
  await page.getByRole('button', { name: 'Start replay from' }).click();
  const timeline = page.getByRole('region', { name: 'Detected patterns' });
  await expect(timeline.getByTestId('timeline-event')).toHaveCount(1);
  await page.getByRole('button', { name: 'Next detector event' }).click();
  const event = timeline.getByTestId('timeline-event');
  await expect(event).toHaveCount(1);
  await expect(event).toContainText('persistence_confirmed');
  const cursor = await page.getByText(/Replay paused ·/).textContent();
  const viewport = () => page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!).tabs[0].viewport);
  await page.getByRole('button', { name: 'Zoom out', exact: true }).click();
  await expect.poll(async () => {
    const range = await viewport();
    return range ? range.to - range.from : 0;
  }).toBeGreaterThan(94);
  const before = await viewport();
  await event.click();
  await expect(event).toHaveAttribute('aria-current', 'true');
  await expect(page.locator('.focus-notice')).toBeVisible();
  expect(await page.getByText(/Replay paused ·/).textContent()).toBe(cursor);
  await expect.poll(async () => {
    const range = await viewport();
    return range.to - range.from;
  }).toBeCloseTo(before.to - before.from, 2);
  await page.getByRole('button', { name: 'Return to replay cursor' }).click();
  await expect(page.locator('.focus-notice')).toHaveCount(0);
  await page.getByRole('button', { name: 'Stop replay' }).click();
});
