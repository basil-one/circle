import assert from 'node:assert/strict';
import test from 'node:test';
import { renderPerformanceSummary } from '../scripts/render_site_performance_summary.mjs';

const routes = ['/', '/moves/', '/lenses/', '/method/'];
const metrics = [
  'first-contentful-paint',
  'largest-contentful-paint',
  'speed-index',
  'total-blocking-time',
  'cumulative-layout-shift'
];

function makeSummary() {
  return {
    complete: true,
    capturedAt: '2026-11-16T17:00:00.000Z',
    source: { sha: 'abc123', runUrl: 'https://github.com/basil-one/circle/actions/runs/99' },
    coverage: { routeCount: 4, runCount: 3, measurementCount: 24 },
    timing: { measurementSeconds: 264.1 },
    environment: {
      lighthouse: '13.6.0',
      chrome: 'Google Chrome for Testing 159.0.8154.6',
      node: 'v22.23.4',
      ruby: 'ruby 3.3.13 (2026-10-23 revision af48f08f21) [x86_64-linux]',
      jekyll: 'jekyll 3.10.0',
      runner: 'ubuntu-24.04'
    },
    conditions: {
      site: 'Jekyll production build served locally',
      mobile: 'Lighthouse default mobile configuration',
      desktop: 'Lighthouse desktop preset',
      analyticsCollectionBlocked: true,
      labOnly: true
    },
    firstPartyAssets: [{ path: 'assets/site.css', bytes: 2048 }],
    externalScripts: [{
      source: 'https://www.googletagmanager.com/gtag/js',
      samples: 8,
      successful: 8,
      medianTransferBytes: 180_000
    }],
    results: routes.flatMap((route) =>
      ['mobile', 'desktop'].map((formFactor) => ({
        route,
        formFactor,
        values: Object.fromEntries(metrics.map((metric) => [
          metric,
          metric === 'cumulative-layout-shift'
            ? 0.125
            : metric === 'largest-contentful-paint'
              ? formFactor === 'mobile' ? 1_234 : 567
              : metric === 'total-blocking-time'
                ? 89
                : 456
        ]))
      }))
    )
  };
}

function makeComparison({ available = false, historyStatus = 'collecting_history' } = {}) {
  const summary = makeSummary();
  return {
    deltasAvailable: available,
    fixedReference: available
      ? { available: true, runUrl: 'https://github.com/basil-one/circle/actions/runs/88' }
      : { available: false, reason: 'No versioned successful main reference has been established.' },
    history: {
      status: historyStatus,
      availableDailySamples: historyStatus === 'available' ? 28 : 0,
      requiredDailySamples: 28,
      reason: historyStatus === 'unavailable' ? 'GitHub Actions API returned HTTP 403.' : undefined,
      skipped: historyStatus === 'unavailable' ? ['history permission denied'] : [],
      provenance: []
    },
    comparisons: summary.results.map((result) => ({
      ...result,
      fixedReference: available ? Object.fromEntries(metrics.map((metric) => [metric, result.values[metric] - 100])) : null,
      rollingBaseline: available ? Object.fromEntries(metrics.map((metric) => [metric, result.values[metric] - 200])) : null
    }))
  };
}

function makeInputs(overrides = {}) {
  return {
    summary: makeSummary(),
    comparison: makeComparison(),
    jobStatus: 'success',
    eventName: 'pull_request',
    ref: 'copilot/change',
    runUrl: 'https://github.com/basil-one/circle/actions/runs/99',
    skip: { measurement: false, paths: [] },
    reports: { outcome: 'success', url: 'https://github.com/basil-one/circle/actions/runs/99/artifacts/1' },
    machineArtifact: { outcome: 'success', url: 'https://github.com/basil-one/circle/actions/runs/99/artifacts/2' },
    steps: { measure: 'success', diagnostic: 'success', reports: 'success', summary: 'success' },
    ...overrides
  };
}

test('puts completed informational outcome first and summarizes representative LCP/TBT by page', () => {
  const report = renderPerformanceSummary(makeInputs());

  assert.match(report, /^# Site: Performance Integrity\n\n\*\*Measurement completed — 24\/24 Lighthouse samples\*\*/);
  assert.match(
    report,
    /Measurements were collected using controlled Lighthouse tests, not actual visitors\. Comparisons are informational; no automatic regression thresholds are enforced\./
  );
  assert.match(report, /\| Home \| 1\.23 s \| 567 ms \| 89 ms \|/);
  assert.match(report, /\| Moves \|/);
  assert.match(report, /\| Lenses \|/);
  assert.match(report, /\| Method \| 1\.23 s \| 567 ms \| 89 ms \|/);
  assert.match(report, /Technical details/);
  assert.match(report, /\| Page \| Profile \| FCP \| LCP \| Speed Index \| TBT \| CLS \|/);
  assert.match(
    report,
    /Lighthouse 13\.6\.0; Google Chrome for Testing 159\.0\.8154\.6; Node v22\.23\.4; ruby 3\.3\.13 .*; jekyll 3\.10\.0; runner ubuntu-24\.04\./
  );
  assert.doesNotMatch(report, /Chrome Google Chrome|Ruby ruby|Jekyll jekyll/);
  assert.match(report, /\| Method \| mobile \|/);
  assert.match(report, /264\.1 seconds/);
});

test('reports pending fixed anchor and collecting rolling history without an empty comparison table', () => {
  const report = renderPerformanceSummary(makeInputs());

  assert.match(report, /Fixed reference:\*\* pending reviewed successful `main` anchor/);
  assert.match(report, /Rolling daily-main baseline:\*\* collecting \(0\/28\)/);
  assert.match(report, /review and version it as the fixed anchor/);
  assert.doesNotMatch(report, /^\| Page \| Profile \| Fixed LCP/m);
});

test('renders route/profile-specific deltas and matching baseline provenance when available', () => {
  const report = renderPerformanceSummary(makeInputs({
    comparison: makeComparison({ available: true, historyStatus: 'available' })
  }));

  assert.match(report, /Fixed reference:\*\* \[available\]/);
  assert.match(report, /28\/28 compatible successful daily-main runs available/);
  assert.match(report, /^\| Home \| mobile \| \+100 ms \(\+8\.8%\) \| \+200 ms \(\+19\.3%\)/m);
  assert.match(report, /^\| Method \| mobile \|/m);
  assert.doesNotMatch(report, /Method \(\/method\/\)/);
  assert.match(report, /Fixed TBT Δ/);
});

test('preserves a history lookup error and does not mislabel it as collecting', () => {
  const report = renderPerformanceSummary(makeInputs({
    comparison: makeComparison({ historyStatus: 'unavailable' })
  }));

  assert.match(report, /Rolling daily-main baseline:\*\* unavailable \(GitHub Actions API returned HTTP 403\)/);
  assert.match(report, /history permission denied/);
  assert.doesNotMatch(report, /collecting \(0\/28\)/);
});

test('does not report complete sample counts from partial measurement data', () => {
  const summary = makeSummary();
  summary.complete = false;
  summary.coverage.measurementCount = 12;
  summary.results = summary.results.slice(0, 4);
  const report = renderPerformanceSummary(makeInputs({
    summary,
    comparison: undefined,
    steps: { measure: 'success', diagnostic: 'skipped' }
  }));

  assert.match(report, /\*\*Measurement failed or incomplete/);
  assert.match(report, /No complete measurement is reported/);
  assert.doesNotMatch(report, /Measurements were collected/);
  assert.match(report, /Complete sample count: \*\*unavailable\*\*/);
  assert.doesNotMatch(report, /24\/24 Lighthouse samples|12\/24/);
  assert.match(report, /Partial measurement data only/);
});

test('links only successful uploads and identifies missing artifacts for investigation', () => {
  const report = renderPerformanceSummary(makeInputs({
    reports: { outcome: 'failure' },
    machineArtifact: { outcome: 'skipped' }
  }));

  assert.doesNotMatch(report, /Raw Lighthouse reports\]\(/);
  assert.doesNotMatch(report, /Machine-readable summary and comparison\]\(/);
  assert.match(report, /Raw Lighthouse reports unavailable/);
  assert.match(report, /Machine-readable summary\/comparison artifact unavailable/);
  assert.match(report, /\[the workflow run\]\(https:\/\/github.com\/basil-one\/circle\/actions\/runs\/99\)/);
});

test('does not imply a comparison file exists when only the summary was produced', () => {
  const report = renderPerformanceSummary(makeInputs({
    comparison: undefined
  }));

  assert.match(report, /Machine-readable performance summary \(comparison unavailable\)/);
});

test('uses the same hierarchy for intentional collection-text skips without measurement claims', () => {
  const report = renderPerformanceSummary(makeInputs({
    summary: undefined,
    comparison: undefined,
    skip: {
      measurement: true,
      reason: 'all changes are editorial move/lens text',
      paths: ['_moves/new.md']
    },
    reports: { outcome: 'skipped' },
    machineArtifact: { outcome: 'skipped' },
    steps: { measure: 'skipped' }
  }));

  assert.match(report, /^# Site: Performance Integrity\n\n\*\*Intentionally skipped — existing collection text only\*\*/);
  assert.match(report, /Changed paths \(1\): `_moves\/new\.md`/);
  assert.match(report, /Lighthouse samples collected: \*\*0\*\*/);
  assert.match(report, /No metrics, baseline comparison, or artifacts were produced/);
  assert.doesNotMatch(report, /24\/24|Mobile LCP|Fixed LCP Δ/);
});

test('reports measurement failure and incomplete setup without fabricated counts or artifact links', () => {
  const report = renderPerformanceSummary(makeInputs({
    summary: undefined,
    comparison: undefined,
    jobStatus: 'failure',
    runUrl: 'https://github.com/basil-one/circle/actions/runs/99',
    reports: { outcome: 'skipped' },
    machineArtifact: { outcome: 'skipped' },
    steps: { tests: 'failure', measure: 'skipped', diagnostic: 'skipped' }
  }));

  assert.match(report, /\*\*Measurement failed or incomplete/);
  assert.match(report, /No complete measurement is reported/);
  assert.doesNotMatch(report, /Measurements were collected/);
  assert.match(report, /Complete sample count: \*\*unavailable\*\*/);
  assert.match(report, /performance tests failed/);
  assert.doesNotMatch(report, /24\/24 Lighthouse samples/);
  assert.doesNotMatch(report, /artifacts\/[12]/);
});
