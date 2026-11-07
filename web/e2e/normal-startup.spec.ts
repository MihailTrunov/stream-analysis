import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';

const normalKey = 'stream-analysis.workspace.live.v1';
const demoKey = 'stream-analysis.workspace.v1';

async function normalStartup(page: Page) {
  await page.route('**/api/diagnostics', async (route) => {
    const response = await route.fetch();
    await route.fulfill({ json: { ...await response.json(), synthetic_demo_enabled: false } });
  });
  const prepares: string[] = [];
  // The isolated smoke API contains demo data. Never contact a real provider.
  await page.route('**/api/live/**', async (route) => {
    if (route.request().url().includes('/prepare/')) prepares.push(new URL(route.request().url()).pathname.split('/').at(-1)!);
    await route.fulfill({ status: 503, json: { detail: 'Provider disabled for startup test' } });
  });
  return prepares;
}

test('normal startup replaces demo-only history with live and preserves the demo workspace', async ({ page }) => {
  const prepares = await normalStartup(page);
  await page.addInitScript(({ demoKey }) => {
    localStorage.setItem(demoKey, JSON.stringify({ version: 3, activeId: 'old-demo', tabs: [
      { id: 'old-demo', instrument: 'US30', sourceId: 'offline-replay-us30-v3', timezone: 'Europe/London' },
    ] }));
  }, { demoKey });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Live monitoring' })).toBeVisible();
  await expect.poll(() => prepares).toEqual(['US30']);
  await expect(page.getByText(/Synthetic demo only/)).toHaveCount(0);
  await expect.poll(() => page.evaluate((key) => JSON.parse(localStorage.getItem(key)!).tabs[0].mode, normalKey)).toBe('live');
  expect(await page.evaluate((key) => JSON.parse(localStorage.getItem(key)!).tabs[0].id, demoKey)).toBe('old-demo');
});

test('restored live tabs prepare only when focused', async ({ page }) => {
  const prepares = await normalStartup(page);
  await page.addInitScript(({ normalKey }) => {
    localStorage.setItem(normalKey, JSON.stringify({ version: 3, activeId: 'first', tabs: [
      { id: 'first', instrument: 'US30', sourceId: '', timezone: 'Europe/London', mode: 'live', liveConnect: true },
      { id: 'second', instrument: 'DAX', sourceId: '', timezone: 'Europe/London', mode: 'live', liveConnect: true },
    ] }));
  }, { normalKey });
  await page.goto('/');
  await expect.poll(() => prepares).toEqual(['US30']);
  await page.getByRole('tab', { name: 'DAX Live', exact: true }).click();
  await expect.poll(() => prepares).toEqual(['US30', 'DAX']);
});

test('normal startup preserves a real saved historical tab', async ({ page }) => {
  await normalStartup(page);
  await page.route('**/api/replay/sources', async (route) => {
    const response = await route.fetch();
    const catalog = await response.json();
    catalog.sources = catalog.sources.filter((source: { dataset_revision_id: string }) => source.dataset_revision_id === 'offline-replay-us30-v3')
      .map((source: object) => ({ ...source, non_research_grade: false }));
    await route.fulfill({ json: catalog });
  });
  await page.addInitScript(({ normalKey }) => {
    localStorage.setItem(normalKey, JSON.stringify({ version: 3, activeId: 'real-history', tabs: [
      { id: 'real-history', instrument: 'US30', sourceId: 'offline-replay-us30-v3', timezone: 'Europe/London', mode: 'history' },
    ] }));
  }, { normalKey });
  await page.goto('/');
  await expect(page.getByRole('tab', { name: 'US30 History', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start replay from' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Live monitoring' })).toHaveCount(0);
});
