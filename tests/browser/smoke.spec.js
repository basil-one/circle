import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { assertNoAccessibilityViolations } from './accessibility.js';
import { discoverHtmlRoutes, generatedFileForRoute, selectSmokeRoutes } from './site-routes.js';

const siteDirectory = resolve(process.cwd(), '_site');
const localOrigin = new URL('http://127.0.0.1:4173').origin;
const htmlRoutes = discoverHtmlRoutes(siteDirectory);
const smokeRoutes = selectSmokeRoutes(siteDirectory);
const moveRoutes = htmlRoutes.filter((route) => route.startsWith('/moves/') && route !== '/moves/');
const lensRoutes = htmlRoutes.filter((route) => route.startsWith('/lenses/') && route !== '/lenses/');

if (htmlRoutes.length === 0) {
  throw new Error(`No generated HTML pages were discovered in ${siteDirectory}.`);
}

test.beforeEach(async ({ page }) => {
  await page.route('**/*', (route) => {
    const requestOrigin = new URL(route.request().url()).origin;
    return requestOrigin === localOrigin ? route.continue() : route.abort();
  });
});

for (const route of smokeRoutes) {
  test(`${route} is present in generated output and loads`, async ({ page }) => {
    const outputFile = generatedFileForRoute(siteDirectory, route);
    expect(existsSync(outputFile), `Jekyll did not generate ${route} at ${outputFile}`).toBe(true);

    const response = await page.goto(route);
    expect(response?.status(), `Expected ${route} to return HTTP 200`).toBe(200);
    await expect(page.locator('main')).toBeVisible();
    await expect(page.locator('main h1').first()).toHaveText(/\S/);
    await expect(page.locator('footer')).toBeVisible();
    await expect(page.locator('footer').getByRole('link', { name: 'Home', exact: true })).toHaveAttribute('href', '/');
  });
}

for (const route of htmlRoutes) {
  test(`${route} has no detectable accessibility violations`, async ({ page }) => {
    await page.goto(route);
    await assertNoAccessibilityViolations(page, route, test.info());
  });
}

test('the Moves index links to every generated move collection page', async ({ page }) => {
  expect(moveRoutes).toEqual(['/moves/establish/', '/moves/integrate/', '/moves/reconcile/']);
  await page.goto('/moves/');

  for (const route of moveRoutes) {
    const link = page.locator(`main a[href="${route}"]`);
    await expect(link, `The Moves index must link to ${route}`).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(`${localOrigin}${route}`);
    await expect(page.locator('main h1')).toHaveText(/\S/);
    await page.goto('/moves/');
  }
});

test('the Lenses index links to every generated lens collection page', async ({ page }) => {
  expect(lensRoutes).toEqual(['/lenses/energy/', '/lenses/paradigm/', '/lenses/sense/']);
  await page.goto('/lenses/');

  for (const route of lensRoutes) {
    const link = page.locator(`main a[href="${route}"]`);
    await expect(link, `The Lenses index must link to ${route}`).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(`${localOrigin}${route}`);
    await expect(page.locator('main h1')).toHaveText(/\S/);
    await page.goto('/lenses/');
  }
});

test('footer navigation reaches the Moves and Lenses collection indexes', async ({ page }) => {
  await page.goto('/');
  const footer = page.locator('footer');

  for (const [label, route] of [['Moves', '/moves/'], ['Lenses', '/lenses/']]) {
    const link = footer.getByRole('link', { name: label, exact: true });
    await expect(link).toHaveAttribute('href', route);
    await link.click();
    await expect(page).toHaveURL(`${localOrigin}${route}`);
    await expect(page.locator('main h1')).toBeVisible();
    await page.goto('/');
  }
});

test('the published-paper page links to the current release artifact', async ({ page }) => {
  await page.goto('/paper/');
  const download = page.getByRole('link', { name: /download/i });
  await expect(download).toBeVisible();
  expect(await download.getAttribute('href')).toContain('circle3-paper.pdf');
});

test('site routes are generated as rendered pages', async () => {
  for (const route of htmlRoutes) {
    const outputFile = generatedFileForRoute(siteDirectory, route);
    expect(existsSync(outputFile), `${route} should be generated at ${outputFile}`).toBe(true);
    expect(readFileSync(outputFile, 'utf8')).toContain('<main');
  }
});
