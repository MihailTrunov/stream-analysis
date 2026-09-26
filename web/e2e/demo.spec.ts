import { expect, test } from '@playwright/test';

test('seeded walkthrough reveals one bar at a time and shows diagnostics', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Stream Analysis' })).toBeVisible();
  await expect(page.getByText('Bar 1 of 2')).toBeVisible();
  await expect(page.getByText('2026-01-02T14:30:00Z')).toBeVisible();
  await expect(page.getByText('2026-01-02T14:31:00Z')).toHaveCount(0);
  await page.getByRole('button', { name: 'Step one bar' }).click();
  await expect(page.getByText('Bar 2 of 2')).toBeVisible();
  await expect(page.getByText('2026-01-02T14:31:00Z')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Step one bar' })).toBeDisabled();
  await page.getByRole('button', { name: 'Reset' }).click();
  await expect(page.getByText('Bar 1 of 2')).toBeVisible();
  await expect(page.getByText('2026-01-02T14:31:00Z')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Local diagnostics' })).toBeVisible();
  await expect(page.getByText('Not implemented', { exact: true })).toBeVisible();
});
