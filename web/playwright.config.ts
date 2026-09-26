import { defineConfig } from '@playwright/test';

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
      reuseExistingServer: !process.env.CI,
    },
    {
      command: 'pnpm dev',
      cwd: '.',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: !process.env.CI,
    },
  ],
});
