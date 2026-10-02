import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const smokeRoot = mkdtempSync(join(tmpdir(), 'stream-analysis-smoke-'));
const apiPort = Number(process.env.STREAM_ANALYSIS_SMOKE_API_PORT ?? '8000');
const webPort = Number(process.env.STREAM_ANALYSIS_SMOKE_WEB_PORT ?? '5173');

export default defineConfig({
  testDir: './e2e',
  workers: 1, // The MVP API deliberately permits one active walkthrough.
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    browserName: 'chromium',
    channel: 'chrome',
  },
  webServer: [
    {
      command: `.venv/bin/python -m uvicorn market_analysis.api.app:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: '..',
      url: `http://127.0.0.1:${apiPort}/health`,
      reuseExistingServer: false,
      env: {
        STREAM_ANALYSIS_DATABASE_URL: `sqlite+pysqlite:///${join(smokeRoot, 'smoke.sqlite')}`,
        STREAM_ANALYSIS_DATA_ROOT: join(smokeRoot, 'data'),
        STREAM_ANALYSIS_SMOKE_INIT_DB: '1',
      },
    },
    {
      command: `pnpm exec vite --host 127.0.0.1 --port ${webPort}`,
      cwd: '.',
      url: `http://127.0.0.1:${webPort}`,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
