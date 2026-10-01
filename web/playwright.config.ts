import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const smokeRoot = mkdtempSync(join(tmpdir(), 'stream-analysis-smoke-'));

export default defineConfig({
  testDir: './e2e',
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: 'http://127.0.0.1:5173',
    browserName: 'chromium',
    channel: 'chrome',
  },
  webServer: [
    {
      command: '.venv/bin/python -m uvicorn market_analysis.api.app:app --host 127.0.0.1 --port 8000',
      cwd: '..',
      url: 'http://127.0.0.1:8000/health',
      reuseExistingServer: false,
      env: {
        STREAM_ANALYSIS_DATABASE_URL: `sqlite+pysqlite:///${join(smokeRoot, 'smoke.sqlite')}`,
        STREAM_ANALYSIS_DATA_ROOT: join(smokeRoot, 'data'),
        STREAM_ANALYSIS_SMOKE_INIT_DB: '1',
      },
    },
    {
      command: 'pnpm dev',
      cwd: '.',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: !process.env.CI,
    },
  ],
});
