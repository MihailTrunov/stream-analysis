import { expect, test } from '@playwright/test';

test('detector annotation appears only when emitted and inspects its exact event', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Launch walkthrough' }).click();
  await expect(page.getByTestId('pattern-annotation')).toHaveCount(0);
  await page.getByRole('button', { name: 'Step one visible bar' }).click();
  await expect(page.getByTestId('pattern-annotation')).toHaveCount(0);
  await page.getByRole('button', { name: 'Step one visible bar' }).click();
  const annotation = page.getByTestId('pattern-annotation');
  await expect(annotation).toHaveCount(1);
  await expect(annotation).toHaveAttribute('data-detection-time', '2026-01-05T12:12:00Z');
  const instanceId = await annotation.getAttribute('data-instance');
  const sequence = await annotation.getAttribute('data-sequence');
  await annotation.click();
  const inspector = page.getByRole('complementary', { name: 'Selected pattern event' });
  await expect(inspector).toContainText(instanceId ?? 'missing instance');
  await expect(inspector).toContainText(sequence ?? 'missing sequence');
  await expect(inspector).toContainText('persistence_confirmed');
  await page.getByRole('button', { name: 'Reset replay' }).click();
  await expect(page.getByTestId('pattern-annotation')).toHaveCount(0);
  await expect(inspector).toHaveCount(0);
  await page.getByRole('button', { name: 'Stop walkthrough' }).click();
});
