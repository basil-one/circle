import { defineConfig } from '@playwright/test';

const viewportWidth = Number(process.env.BROWSER_VIEWPORT_WIDTH ?? 1280);
const viewportHeight = Number(process.env.BROWSER_VIEWPORT_HEIGHT ?? 800);

export default defineConfig({
  testDir: './tests/browser',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? 'line' : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    browserName: 'chromium',
    headless: true,
    viewport: { width: viewportWidth, height: viewportHeight }
  },
  webServer: {
    command: 'bundle exec ruby -run -e httpd _site -p 4173 -b 127.0.0.1',
    url: 'http://127.0.0.1:4173/',
    reuseExistingServer: !process.env.CI,
    timeout: 15_000
  }
});
