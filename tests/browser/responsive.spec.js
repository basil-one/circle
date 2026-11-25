import { expect, test } from '@playwright/test';
import { resolve } from 'node:path';
import { discoverHtmlRoutes } from './site-routes.js';
import { assertResponsivePage, assertResponsiveTarget } from './responsive.js';

test.use({ hasTouch: true });

const widths = [320, 768, 1280];
const localOrigin = new URL('http://127.0.0.1:4173').origin;
const routes = discoverHtmlRoutes(resolve(process.cwd(), '_site'));

test.beforeEach(async ({ page }) => {
  await page.route('**/*', (route) => {
    const requestOrigin = new URL(route.request().url()).origin;
    return requestOrigin === localOrigin ? route.continue() : route.abort();
  });
});

for (const route of routes) {
  for (const width of widths) {
    test(`Responsive usability: ${route} at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      await page.goto(route);
      await assertResponsivePage(page, route, width);
      await expect(page.locator('main h1').first()).toBeVisible();

      const footer = page.locator('footer');
      for (const link of await footer.getByRole('link').all()) {
        const label = (await link.innerText()).trim().replace(/\s+/g, ' ');
        await assertResponsiveTarget(
          page,
          link,
          `footer ${label} link`,
          route,
          width,
          test.info(),
          'footer'
        );
      }

      const collectionLinks = route === '/moves/'
        ? [['move entry', page.locator('main a[href^="/moves/"]')]]
        : route === '/lenses/'
          ? [['lens entry', page.locator('main a[href^="/lenses/"]')]]
          : route === '/method/'
            ? [['collection index', page.locator('main a[href="/moves/"], main a[href="/lenses/"]')]]
            : route === '/'
              ? [['method page', page.locator('main a[href="/method/"]')]]
              : [];
      for (const [label, locator] of collectionLinks) {
        for (const link of await locator.all()) {
          await assertResponsiveTarget(
            page,
            link,
            label,
            route,
            width,
            test.info(),
            'navigation'
          );
        }
      }

      const hitAreaAnnotations = test.info().annotations.filter(
        ({ type }) => type === 'responsive-hit-area'
      );
      expect(hitAreaAnnotations.length).toBeGreaterThan(0);
      expect(
        hitAreaAnnotations.every(({ description }) =>
          description.startsWith('navigation:') || description.startsWith('footer:footer ')
        )
      ).toBe(true);
    });
  }
}

test('intentional local horizontal scrolling is not page overflow', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.setContent(`<!doctype html>
    <html lang="en">
      <head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Local scroll fixture</title></head>
      <body><main><h1>Fixture</h1><div style="width: 100%; overflow-x: auto"><div style="width: 640px">Local scrolling content</div></div></main></body></html>`);

  await assertResponsivePage(page, 'in-memory local scroll fixture', 320);
});

test('mobile footer and collection links remain operable by touch', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto('/');

  const movesLink = page.locator('footer').getByRole('link', { name: 'Moves', exact: true });
  await movesLink.scrollIntoViewIfNeeded();
  const movesBox = await movesLink.boundingBox();
  expect(movesBox, 'Moves footer link must have a touch target.').not.toBeNull();
  await page.touchscreen.tap(movesBox.x + movesBox.width / 2, movesBox.y + movesBox.height / 2);
  await expect(page).toHaveURL(/\/moves\/$/);

  const establishLink = page.locator('main a[href="/moves/establish/"]');
  await establishLink.scrollIntoViewIfNeeded();
  const establishBox = await establishLink.boundingBox();
  expect(establishBox, 'Establish move link must have a touch target.').not.toBeNull();
  await page.touchscreen.tap(establishBox.x + establishBox.width / 2, establishBox.y + establishBox.height / 2);
  await expect(page).toHaveURL(/\/moves\/establish\/$/);

  await page.goto('/');
  const lensesLink = page.locator('footer').getByRole('link', { name: 'Lenses', exact: true });
  await lensesLink.scrollIntoViewIfNeeded();
  const lensesBox = await lensesLink.boundingBox();
  expect(lensesBox, 'Lenses footer link must have a touch target.').not.toBeNull();
  await page.touchscreen.tap(lensesBox.x + lensesBox.width / 2, lensesBox.y + lensesBox.height / 2);
  await expect(page).toHaveURL(/\/lenses\/$/);

  const senseLink = page.locator('main a[href="/lenses/sense/"]');
  await senseLink.scrollIntoViewIfNeeded();
  const senseBox = await senseLink.boundingBox();
  expect(senseBox, 'Sense Making lens link must have a touch target.').not.toBeNull();
  await page.touchscreen.tap(senseBox.x + senseBox.width / 2, senseBox.y + senseBox.height / 2);
  await expect(page).toHaveURL(/\/lenses\/sense\/$/);
});
