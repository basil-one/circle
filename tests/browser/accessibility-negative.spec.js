import { test } from '@playwright/test';
import { assertNoAccessibilityViolations } from './accessibility.js';

test('a missing image alternative produces actionable diagnostics', async ({ page }) => {
  await page.setContent(`<!doctype html>
    <html lang="en">
      <head><title>Accessibility failure fixture</title></head>
      <body><main><h1>Fixture</h1><img></main></body>
    </html>`);

  await assertNoAccessibilityViolations(page, 'in-memory accessibility failure fixture');
});
