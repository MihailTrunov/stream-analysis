import { expect, type Page } from '@playwright/test';

export async function selectShortDemo(page: Page): Promise<void> {
  const revision = page.getByRole('combobox', { name: 'Dataset revision' });
  await revision.selectOption('offline-replay-us30-v1');
  await expect(revision).toHaveValue('offline-replay-us30-v1');
  await expect(page.getByLabel('Start UTC')).toHaveValue('2026-01-05T12:11');
  await expect(page.getByLabel('End UTC')).toHaveValue('2026-01-05T12:14');
  await expect(page.getByRole('button', { name: 'Launch walkthrough' })).toBeEnabled();
}
