import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import test from 'node:test';
import {
  discoverHtmlRoutes,
  generatedFileForRoute,
  selectPerformanceRoutes,
  selectSmokeRoutes,
  performanceRunCount,
  responsiveViewports
} from '../../scripts/site/routes.js';

function writePage(root, route) {
  const file = route === '/'
    ? resolve(root, 'index.html')
    : resolve(root, `.${route}`, 'index.html');
  mkdirSync(resolve(file, '..'), { recursive: true });
  writeFileSync(file, '<html><body><main><h1>Circle</h1></main></body></html>');
}

test('discovers Circle collection entries and smoke-tests every generated route', () => {
  assert.deepEqual(responsiveViewports, [320, 768, 1280]);
  assert.equal(performanceRunCount, 3);
  const root = mkdtempSync(resolve(tmpdir(), 'circle-site-routes-'));
  try {
    const routes = [
      '/',
      '/moves/',
      '/moves/establish/',
      '/lenses/',
      '/lenses/sense/',
      '/method/',
      '/paper/'
    ];
    routes.forEach((route) => writePage(root, route));

    assert.deepEqual(discoverHtmlRoutes(root), [...routes].sort());
    assert.deepEqual(selectSmokeRoutes(root), [...routes].sort());
    assert.deepEqual(selectPerformanceRoutes(root), ['/', '/moves/', '/lenses/', '/method/']);
    assert.equal(generatedFileForRoute(root, '/moves/establish/'), resolve(root, 'moves/establish/index.html'));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('performance route selection fails when an expected Circle route is absent', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'circle-site-routes-'));
  try {
    writePage(root, '/');
    assert.throws(selectPerformanceRoutes.bind(null, root), /Circle performance routes are missing/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
