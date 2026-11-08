import { expect, test } from './fixtures';

test('automatic historical analysis keeps one request in flight while the viewport changes', async ({ page }) => {
  const config = (await (await page.request.get('/api/analysis-defaults?instrument_id=US30')).json()).detection_config;
  const streams = [{ config, policies: Object.fromEntries(config.patterns.map((p: { pattern_id: string }) => [p.pattern_id, 'always'])) }];
  await page.addInitScript(streams => {
    localStorage.setItem('stream-analysis.workspace.v1', JSON.stringify({ version: 3, activeId: 'history', tabs: [{
      id: 'history', instrument: 'US30', sourceId: 'offline-replay-us30-v3', timezone: 'Europe/London',
      streams, appliedStreams: streams,
    }] }));
  }, streams);
  let calls = 0;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/analysis', async route => {
    calls++;
    await pending;
    await route.fulfill({ json: { run_id: 'historical-in-flight' } });
  });
  await page.route('**/api/analysis/historical-in-flight/view?*', route => route.fulfill({ json: {
    events: [], observations: [], stream_coverage: config.patterns.map((p: { pattern_id: string }) => ({
      run_id: 'historical-in-flight', pattern_id: p.pattern_id, timeframe: '1m', status: 'calculated',
      valid_from: '2026-01-05T09:00:00Z', available_from: '2026-01-05T09:00:00Z',
      valid_to: '2026-01-05T14:00:00Z', processed: 300, required: 6,
    })),
  } }));
  await page.route('**/api/analysis/historical-in-flight/stop', route => route.fulfill({ json: {} }));
  try {
    await page.goto('/');
    await expect.poll(() => calls).toBe(1);
    for (let i = 0; i < 3; i++) await page.getByRole('button', { name: 'Zoom out', exact: true }).click();
    const chart = await page.locator('.trading-chart').boundingBox();
    await page.mouse.move(chart!.x + chart!.width * 0.35, chart!.y + chart!.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(chart!.x + chart!.width * 0.65, chart!.y + chart!.height * 0.5, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(700); // Exceed the normal automatic calculation debounce.
    expect(calls).toBe(1);
    release();
    await expect(page.getByText('Calculating historical detectors…')).toHaveCount(0);
    await page.waitForTimeout(700);
    expect(calls).toBe(1);
  } finally { release(); }
});
