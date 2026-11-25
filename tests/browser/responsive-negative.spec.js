import { test } from '@playwright/test';
import { assertResponsivePage } from './responsive.js';

test('a forced page overflow produces an actionable responsive diagnostic', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.setContent(`<!doctype html>
    <html lang="en">
      <head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Responsive failure fixture</title></head>
      <body><main><h1>Fixture</h1><div style="width: 640px">Forced horizontal overflow</div></main></body>
    </html>`);

  await assertResponsivePage(page, 'in-memory responsive failure fixture', 320);
});
