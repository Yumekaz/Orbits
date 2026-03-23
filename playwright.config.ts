import { defineConfig } from '@playwright/test';

const webServerCommand = process.platform === 'win32'
  ? '.\\.venv\\Scripts\\python.exe analyzer.py . --serve --port 8766'
  : './.venv/bin/python analyzer.py . --serve --port 8766';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: 'http://127.0.0.1:8766',
    headless: true,
    viewport: { width: 1280, height: 720 },
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      testIgnore: '**/visual.spec.ts',
      use: {
        browserName: 'chromium',
        channel: process.platform === 'win32' ? 'msedge' : undefined,
      },
    },
  ],
  webServer: {
    command: webServerCommand,
    url: 'http://127.0.0.1:8766/visualizer.html',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
