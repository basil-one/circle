import { expect } from '@playwright/test';

const viewportTolerance = 1;

export async function assertResponsivePage(page, route, width) {
  const measurements = await page.evaluate(() => {
    const root = document.documentElement;
    const main = document.querySelector('main');
    const overflowingElements = [...document.body.querySelectorAll('*')].filter((element) => {
      const bounds = element.getBoundingClientRect();
      return bounds.left < -1 || bounds.right > root.clientWidth + 1;
    });
    const topLevelOverflows = overflowingElements.filter((element) =>
      !overflowingElements.some((candidate) => candidate !== element && candidate.contains(element)),
    );
    return {
      viewportWidth: root.clientWidth,
      documentWidth: Math.max(root.scrollWidth, document.body.scrollWidth),
      mainWidth: main?.clientWidth ?? 0,
      mainScrollWidth: main?.scrollWidth ?? 0,
      overflowingElements: topLevelOverflows.slice(0, 3).map((element) => {
        const bounds = element.getBoundingClientRect();
        const selector = element.id
          ? `${element.tagName.toLowerCase()}#${element.id}`
          : `${element.tagName.toLowerCase()}${[...element.classList].map((name) => `.${name}`).join('')}`;
        return `${selector} (${bounds.left.toFixed(1)}px to ${bounds.right.toFixed(1)}px)`;
      })
    };
  });

  if (
    measurements.documentWidth > measurements.viewportWidth + viewportTolerance ||
    measurements.mainScrollWidth > measurements.mainWidth + viewportTolerance
  ) {
    throw new Error(
      `Responsive usability failure on ${route} at ${width}px: whole-page horizontal overflow ` +
      `(document ${measurements.documentWidth}px, viewport ${measurements.viewportWidth}px; ` +
      `main ${measurements.mainScrollWidth}px, available ${measurements.mainWidth}px). ` +
      `Affected elements: ${measurements.overflowingElements.join(', ') || 'not identified'}. ` +
      'Next step: inspect the reported route at this viewport for content extending beyond the reading area.'
    );
  }
}

export async function assertResponsiveTarget(page, locator, label, route, width, testInfo, region) {
  await expect(
    locator,
    `Responsive usability failure on ${route} at ${width}px: ${label} is not visible. ` +
    'Next step: inspect its responsive layout and confirm the visitor action remains available.'
  ).toBeVisible();
  await locator.scrollIntoViewIfNeeded();

  const box = await locator.boundingBox();
  expect(
    box,
    `Responsive usability failure on ${route} at ${width}px: ${label} has no visible hit area. ` +
    'Next step: inspect whether it is clipped, hidden, or covered.'
  ).not.toBeNull();
  expect(
    box.x,
    `Responsive usability failure on ${route} at ${width}px: ${label} is clipped off the left edge. ` +
    'Next step: inspect the element and its containing layout.'
  ).toBeGreaterThanOrEqual(-viewportTolerance);
  expect(
    box.x + box.width,
    `Responsive usability failure on ${route} at ${width}px: ${label} is clipped off the right edge. ` +
    'Next step: inspect the element and its containing layout.'
  ).toBeLessThanOrEqual(width + viewportTolerance);

  if (testInfo && region) {
    testInfo.annotations.push({
      type: 'responsive-hit-area',
      description: `${region}:${label}:${box.width.toFixed(1)}x${box.height.toFixed(1)}`
    });
  }

  await locator.click({ trial: true });
}
