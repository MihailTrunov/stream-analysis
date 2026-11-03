import { expect, test } from './fixtures';

test.afterEach(async ({ request }) => {
  const response = await request.get('/api/replay/active');
  const result = await response.json() as { active: { run_id: string } | null };
  if (result.active) await request.post(`/api/replay/${result.active.run_id}/stop`);
});

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
  const snapshot = await (await page.request.get('/api/replay/active')).json() as {
    active: { run_id: string; selected_start: string; selected_end: string; cursor_time: null };
  };
  expect(snapshot.active.cursor_time).toBeNull();
  const data = await (await page.request.get(`/api/replay/${snapshot.active.run_id}/view`, { params: {
    start: snapshot.active.selected_start, end: snapshot.active.selected_end,
  } })).json() as { bars: unknown[]; events: unknown[] };
  expect(data.bars).toHaveLength(0);
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
    const snapshot = await (await page.request.get('/api/replay/active')).json() as { active: { status: string } };
    return snapshot.active.status;
  }).toBe('paused');
  await page.getByLabel('Instrument', { exact: true }).selectOption('DAX');
  await expect(page.getByRole('img', { name: /300 visible bars/ })).toBeVisible();
  await expect(page.locator('.trading-chart')).toHaveCount(1);
  await expect(page.getByRole('tab', { name: /DAX.*History/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeDisabled();
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
  const before = await (await page.request.get('/api/replay/active')).json();
  await page.getByRole('button', { name: 'Move to', exact: true }).click();
  await expect(page.locator('.focus-notice')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  const after = await (await page.request.get('/api/replay/active')).json();
  expect(after.active.cursor_index).toBe(before.active.cursor_index);
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
    return saved.tabs[0].config.patterns[0].enabled;
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
  await page.route('**/api/replay/*/view?*', async (route) => {
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
  await page.route('**/api/replay/*/view?*', async (route) => {
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
  await page.route('**/api/replay/*/view?*', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (body.events.length) {
      const time = Date.parse(body.events[0].detection_time);
      body.bars = body.bars.filter((bar: { timestamp: string }) => Date.parse(bar.timestamp) !== time);
      body.observations = body.observations.filter((bar: { timestamp: string }) => Date.parse(bar.timestamp) !== time);
    }
    await route.fulfill({ response, json: body });
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start replay from', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Start replay from', exact: true }).click();
  await page.getByRole('button', { name: 'Next detector event', exact: true }).click();
  const event = page.getByTestId('timeline-event').first();
  await expect(event).toBeVisible();
  await event.click();
  await expect(page.getByRole('alert')).toContainText('outside the loaded candle window');
  await expect(page.locator('.focus-notice')).toHaveCount(0);
});
