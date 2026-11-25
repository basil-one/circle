import { expect, test } from '@playwright/test';

test('a missing route produces an actionable diagnostic', async ({ page }) => {
  const missingRoute = '/__browser-test-missing-route__/';
  const response = await page.goto(missingRoute);

  expect(
    response?.status(),
    `Expected ${missingRoute} to return HTTP 200; received HTTP ${response?.status() ?? 'no response'}`
  ).toBe(200);
});
