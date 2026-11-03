import { expect, test } from './fixtures';

test('chart dominates the initial workspace, zooms, and discards cached future candles on replay entry', async ({ page }) => {
  await page.goto('/');
  const start = page.getByRole('button', { name: 'Start replay from', exact: true });
  await expect(start).toBeEnabled();
  const chart = page.getByRole('img', { name: /candlestick chart/ });
  await expect(chart).toHaveAccessibleName(/300 visible bars/);
  const size = await chart.boundingBox();
  expect(size!.width).toBeGreaterThan(page.viewportSize()!.width * 0.65);
  expect(size!.height).toBeGreaterThan(550);
  const chartBox = await page.locator('.chart-stage').boundingBox();
  const instrumentBox = await page.getByLabel('Instrument', { exact: true }).boundingBox();
  expect(instrumentBox!.x).toBeGreaterThan(chartBox!.x);
  expect(instrumentBox!.x).toBeLessThan(chartBox!.x + 100);
  const before = await chart.screenshot();
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  expect((await chart.screenshot()).equals(before)).toBe(false);
  await expect(page.locator('.app-header')).toBeInViewport({ ratio: 1 });
  await expect.poll(async () => page.evaluate(() => {
    const workspace = JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!);
    return Boolean(workspace.tabs[0].viewport?.origin);
  })).toBe(true);
  await page.screenshot({ path: '/tmp/stream-analysis-workspace.png', fullPage: true });
  await start.click();
  await expect(chart).toHaveAccessibleName(/60 visible bars/);
  await expect(page.getByText('0 replay bars revealed', { exact: false })).toBeVisible();
  const snapshot = await (await page.request.get('/api/analysis/active')).json() as {
    sessions: Array<{ run_id: string; selected_start: string; selected_end: string; cursor_time: null }>;
  };
  expect(snapshot.sessions[0].cursor_time).toBeNull();
  const data = await (await page.request.get(`/api/analysis/${snapshot.sessions[0].run_id}/view`, { params: {
    start: snapshot.sessions[0].selected_start, end: snapshot.sessions[0].selected_end,
  } })).json() as { bars: unknown[]; events: unknown[] };
  expect(data.bars).toHaveLength(60);
  expect(data.events).toHaveLength(0);
  await page.getByRole('button', { name: 'Step one visible bar' }).click();
  await expect(chart).toHaveAccessibleName(/61 visible bars/);
  await expect(page.getByText('1 replay bars revealed', { exact: false })).toBeVisible();
  await page.getByLabel('Chart date and time').fill('2026-01-05T13:00');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('already revealed');
  await expect(chart).toHaveAccessibleName(/61 visible bars/);
  await page.getByRole('button', { name: 'Stop replay', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Historical exploration' })).toBeVisible();
  await expect(chart).toHaveAccessibleName(/300 visible bars/);
});

test('tab switching pauses replay, renders only the focused chart, and restores instruments after reload', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Start replay from', exact: true }).click();
  await page.getByLabel('Playback speed').selectOption('0.5');
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(page.getByText(/Replay running/)).toBeVisible();
  await page.getByRole('button', { name: 'New analysis tab' }).click();
  await expect.poll(async () => {
    const snapshot = await (await page.request.get('/api/analysis/active')).json() as { sessions: Array<{ status: string }> };
    return snapshot.sessions[0].status;
  }).toBe('paused');
  await page.getByLabel('Instrument', { exact: true }).selectOption('DAX');
  await expect(page.getByRole('img', { name: /300 visible bars/ })).toBeVisible();
  await expect(page.locator('.trading-chart')).toHaveCount(1);
  await expect(page.getByRole('tab', { name: /DAX.*History/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.reload();
  await expect(page.getByLabel('Instrument', { exact: true })).toHaveValue('DAX');
  await expect(page.getByRole('tab', { name: /DAX.*History/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('.trading-chart')).toHaveCount(1);
  await page.getByRole('tab', { name: /US30.*Replay/ }).click();
  await expect(page.getByText(/Replay paused/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeEnabled();
  await page.getByRole('tab', { name: 'Detector settings' }).click();
  await expect(page.getByText('Stop replay to change its detector settings.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Apply settings' })).toBeDisabled();
});

test('ambiguous UK picker times can be explicitly resolved by choosing UTC', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByLabel('Chart date and time').fill('2026-10-25T01:30');
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('occurs twice');
  await page.locator('details.source-settings > summary').click();
  await page.getByLabel('Display timezone').selectOption('UTC');
  await expect(page.getByLabel('Display timezone')).toHaveValue('UTC');
  await expect(page.getByLabel('Chart date and time')).toHaveValue('2026-10-25T01:30');
  await expect(page.getByRole('alert')).toContainText('Check the date/time');
});

 test('Move to accepts loaded candles without changing the replay cursor', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.locator('.focus-notice')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page.getByRole('button', { name: 'Start replay from', exact: true }).click();
  await page.getByRole('button', { name: 'Step one visible bar' }).click();
  await expect(page.getByText('1 replay bars revealed', { exact: false })).toBeVisible();
  const before = await (await page.request.get('/api/analysis/active')).json();
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.locator('.focus-notice')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  const after = await (await page.request.get('/api/analysis/active')).json();
  expect(after.sessions[0].cursor_index).toBe(before.sessions[0].cursor_index);
 });

test('unapplied detector edits remain drafts after reload', async ({ page }) => {
  await page.goto('/');
  const launch = page.getByRole('button', { name: 'Start replay from', exact: true });
  await expect(launch).toBeEnabled();
  await page.getByRole('tab', { name: 'Detector settings' }).click();
  const enabled = page.locator('.detector-configuration input[type=checkbox]').first();
  await enabled.uncheck();
  await expect(launch).toBeDisabled();
  await expect.poll(async () => page.evaluate(() => {
    const saved = JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!);
    return saved.tabs[0].streams[0].config.patterns[0].enabled;
  })).toBe(false);
  await page.reload();
  await page.getByRole('tab', { name: 'Detector settings' }).click();
  await expect(page.locator('.detector-configuration input[type=checkbox]').first()).not.toBeChecked();
  await expect(launch).toBeDisabled();
  await page.getByRole('button', { name: 'Apply settings', exact: true }).click();
  await expect(launch).toBeEnabled();
  await page.reload();
  await expect(launch).toBeEnabled();
});

test('slow chart reads finish before the next automatic candle advance', async ({ page }) => {
  let views = 0;
  await page.route('**/api/analysis/*/view?*', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    views += 1;
    await route.continue();
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Start replay from', exact: true }).click();
  await page.getByLabel('Playback speed').selectOption('4');
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(page.getByText('3 replay bars revealed', { exact: false })).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole('img', { name: /63 visible bars/ })).toBeVisible();
  expect(views).toBeGreaterThanOrEqual(3);
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
});

test('failed chart updates block playback until an explicit retry succeeds', async ({ page }) => {
  let fail = true;
  await page.route('**/api/analysis/*/view?*', async (route) => {
    if (fail) await route.fulfill({ status: 503, json: { detail: 'Simulated unavailable chart' } });
    else await route.continue();
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Start replay from', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Simulated unavailable chart');
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeDisabled();
  fail = false;
  await page.getByRole('button', { name: 'Retry chart update' }).click();
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeEnabled();
});

test('events outside the loaded candle window show an explicit limitation', async ({ page }) => {
  let clippedOrder = -1;
  await page.route('**/api/analysis/*/view?*', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (body.events.length) {
      const occurrence = body.events[0].instance_id;
      const latest = body.events.filter((event: { instance_id: string }) => event.instance_id === occurrence).at(-1);
      clippedOrder = latest.emission_order;
      const time = Date.parse(latest.detection_time);
      body.bars = body.bars.filter((bar: { timestamp: string }) => Date.parse(bar.timestamp) !== time);
      body.observations = body.observations.filter((bar: { timestamp: string }) => Date.parse(bar.timestamp) !== time);
    }
    await route.fulfill({ response, json: body });
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Start replay from', exact: true }).click();
  await page.getByRole('button', { name: 'Next detector event', exact: true }).click();
  for (let i = 0; i < 5; i += 1) await page.getByRole('button', { name: 'Step one visible bar' }).click();
  await expect(page.getByRole('button', { name: 'Next detector event', exact: true })).toBeEnabled();
  const event = page.locator(`[data-testid=timeline-event][data-emission-order="${clippedOrder}"]`);
  await expect(event).toBeVisible();
  await event.click();
  await expect(page.getByRole('alert')).toContainText('outside the loaded candle window');
  await expect(page.locator('.focus-notice')).toHaveCount(0);
});

test('analysis panel tabs support arrow keys and link to their panel', async ({ page }) => {
  await page.goto('/');
  const patterns = page.getByRole('tab', { name: /^Patterns/ });
  await patterns.focus();
  await page.keyboard.press('ArrowRight');
  const settings = page.getByRole('tab', { name: 'Detector settings' });
  await expect(settings).toBeFocused();
  await expect(settings).toHaveAttribute('aria-selected', 'true');
  await expect(settings).toHaveAttribute('aria-controls', /panel-/);
  await page.keyboard.press('Home');
  await expect(patterns).toBeFocused();
  await expect(patterns).toHaveAttribute('aria-selected', 'true');
});

test('a failed session render leaves other tabs usable', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('stream-analysis.workspace.v1', JSON.stringify({ version: 2, activeId: 'one', tabs: [
      { id: 'one', instrument: 'US30', sourceId: '', timezone: 'Europe/London' },
      { id: 'two', instrument: 'DAX', sourceId: '', timezone: 'Europe/London' },
    ] }));
  });
  await page.route('**/api/replay/sources', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.sources = body.sources.map((source: { instrument_id: string }) => source.instrument_id === 'US30'
      ? { ...source, suggested_start: 'invalid date' } : source);
    await route.fulfill({ response, json: body });
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Reset tab settings' })).toBeVisible();
  await page.getByRole('tab', { name: /DAX/ }).click();
  await expect(page.getByRole('img', { name: /candlestick chart/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
});

test('chart and panel controls remain reachable in short and narrow windows', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 600 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Zoom in', exact: true })).toBeInViewport();
  await page.getByRole('tab', { name: 'Detector settings' }).click();
  await page.getByRole('button', { name: 'Apply settings', exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('button', { name: 'Apply settings', exact: true })).toBeInViewport();
  await page.screenshot({ path: '/tmp/stream-analysis-review-short.png' });
  await page.setViewportSize({ width: 600, height: 800 });
  await expect(page.getByRole('button', { name: 'Zoom in', exact: true })).toBeInViewport();
  await page.getByRole('button', { name: 'Apply settings', exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('button', { name: 'Apply settings', exact: true })).toBeInViewport();
});

test('independent tabs replay concurrently and five-minute Step preserves every source close', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('tab', { name: 'Detector settings' }).click();
  await page.getByLabel('Add analytical timeframe').selectOption('5m');
  await expect(page.getByRole('group', { name: '5m analytical settings' })).toBeVisible();
  await page.getByRole('button', { name: 'Apply settings', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByLabel('Chart timeframe', { exact: true }).selectOption('5m');
  await page.getByRole('button', { name: 'Start replay from', exact: true }).click();
  await page.getByRole('button', { name: 'Step one visible bar' }).click();
  await expect(page.getByText('5 replay bars revealed', { exact: false })).toBeVisible();
  const first = (await (await page.request.get('/api/analysis/active')).json()).sessions[0];
  expect(first.initialization.map((item: { processed: number }) => item.processed)).toEqual([65, 13]);
  await page.getByRole('button', { name: 'New analysis tab' }).click();
  await page.getByLabel('Instrument', { exact: true }).selectOption('DAX');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Start replay from', exact: true }).click();
  await page.getByRole('button', { name: 'Step one visible bar' }).click();
  await expect(page.getByText('1 replay bars revealed', { exact: false })).toBeVisible();
  expect((await (await page.request.get('/api/analysis/active')).json()).sessions).toHaveLength(2);
  expect((await (await page.request.get(`/api/analysis/${first.run_id}`)).json()).cursor_index).toBe(first.cursor_index);
  await page.reload();
  await expect(page.getByLabel('Instrument', { exact: true })).toHaveValue('DAX');
  await page.getByRole('tab', { name: /US30.*Replay/ }).click();
  await expect(page.getByLabel('Chart timeframe', { exact: true })).toHaveValue('5m');
  await expect(page.getByText('5 replay bars revealed', { exact: false })).toBeVisible();
});

test('manual historical calculation shows occurrence sections without starting replay', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Fit candles', exact: true }).click();
  await page.getByRole('tab', { name: 'Detector settings' }).click();
  await page.getByRole('button', { name: 'Calculate RANGE_COMPRESSION_V1 for current view · 1m', exact: true }).click();
  await page.getByRole('tab', { name: /^Patterns/ }).click();
  await expect(page.getByText('Historical detections loaded for this view.')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Developing patterns' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Active patterns' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Completed patterns' })).toBeVisible();
  expect((await (await page.request.get('/api/analysis/active')).json()).sessions).toHaveLength(0);
  await page.screenshot({ path: '/tmp/stream-analysis-pattern-groups.png', fullPage: true });
});

test('Always on calculates historical detections and display changes reuse compatible results', async ({ page }) => {
  let calculations = 0;
  page.on('request', (request) => { if (request.method() === 'POST' && request.url().endsWith('/api/analysis')) calculations += 1; });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('tab', { name: 'Detector settings' }).click();
  await page.getByLabel('1m RANGE_COMPRESSION_V1 operation mode').selectOption('always');
  await page.getByRole('button', { name: 'Apply settings', exact: true }).click();
  await page.getByRole('tab', { name: /^Patterns/ }).click();
  await expect(page.getByText('Historical detections loaded for this view.')).toBeVisible({ timeout: 15000 });
  const before = calculations;
  await page.getByLabel('Chart timeframe', { exact: true }).selectOption('5m');
  await expect(page.getByRole('img', { name: /60 visible bars/ })).toBeVisible();
  await expect(page.getByText('Historical detections loaded for this view.')).toBeVisible();
  await expect.poll(() => calculations).toBe(before);
});

test('a migrated configuration can add a registered detector without discarding existing parameters', async ({ page, request }) => {
  const legacy = (await (await request.get('/api/replay/config-default?instrument_id=US30')).json()).detection_config;
  await page.addInitScript((config) => {
    localStorage.setItem('stream-analysis.workspace.v1', JSON.stringify({ version: 2, activeId: 'one', tabs: [
      { id: 'one', instrument: 'US30', sourceId: 'offline-replay-us30-v3', timezone: 'Europe/London', config, appliedConfig: config },
    ] }));
  }, legacy);
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('tab', { name: 'Detector settings' }).click();
  await page.getByLabel('Add detector 1m', { exact: true }).selectOption('EMA_STRUCTURE_REVERSAL_V1');
  await expect(page.getByLabel('1m EMA_STRUCTURE_REVERSAL_V1 enabled', { exact: true })).toBeChecked();
  await page.getByRole('button', { name: 'Apply settings', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('stream-analysis.workspace.v1')!));
  expect(saved.tabs[0].streams[0].config.components.find((item: { component_id: string }) => item.component_id === 'range_state').parameters)
    .toEqual(legacy.components.find((item: { component_id: string }) => item.component_id === 'range_state').parameters);
});
