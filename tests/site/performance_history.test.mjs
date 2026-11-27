import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildComparison,
  isCompleteSummary,
  isDailyPerformanceEvidence,
  loadDailyHistory,
  referenceIsReady
} from '../../scripts/site/compare_performance.mjs';

const metricKeys = [
  'first-contentful-paint',
  'largest-contentful-paint',
  'speed-index',
  'total-blocking-time',
  'cumulative-layout-shift'
];

function makeSummary({
  eventName = 'schedule',
  branch = 'main',
  lcp = 1_000,
  capturedAt = '2026-11-16T00:00:00.000Z',
  reviewAssessment
} = {}) {
  const routes = ['/', '/moves/', '/lenses/', '/method/'];
  return {
    schemaVersion: 1,
    complete: true,
    capturedAt,
    source: {
      eventName,
      branch,
      sha: 'abc123',
      runId: '123',
      runUrl: 'https://github.com/basil-one/circle/actions/runs/123'
    },
    coverage: {
      routeCount: 4,
      runCount: 3,
      measurementCount: 24
    },
    environment: {
      runner: 'ubuntu-24.04',
      lighthouse: '13.6.0',
      chrome: '159.0.8154.6',
      ruby: '3.3.13',
      jekyll: '3.10.0',
      node: 'v22.23.4'
    },
    externalScripts: [{
      source: 'https://www.googletagmanager.com/gtag/js',
      samples: 8,
      successful: 8,
      medianTransferBytes: 180_000
    }],
    reviewAssessment,
    results: routes.flatMap((route) =>
      ['mobile', 'desktop'].map((formFactor) => ({
        route,
        formFactor,
        values: Object.fromEntries(metricKeys.map((key) => [
          key,
          key === 'largest-contentful-paint'
            ? lcp
            : key === 'cumulative-layout-shift'
              ? 0
              : key === 'total-blocking-time'
                ? 50
                : 500
        ]))
      }))
    )
  };
}

function makeHistory(summaries, status) {
  return {
    status: status ?? (summaries.length === 28 ? 'available' : 'collecting_history'),
    summaries,
    skipped: []
  };
}

function makeRun(id, created_at) {
  return {
    id,
    created_at,
    event: 'schedule',
    head_branch: 'main',
    status: 'completed'
  };
}

test('requires a complete four-route, three-run summary', () => {
  const summary = makeSummary();
  assert.equal(isCompleteSummary(summary), true);
  assert.equal(referenceIsReady(summary), true);
  assert.equal(isCompleteSummary({ ...summary, coverage: { ...summary.coverage, measurementCount: 8 } }), false);
  assert.equal(referenceIsReady(makeSummary({ eventName: 'pull_request', branch: 'copilot/change' })), false);
});

test('reports unavailable fixed and rolling baselines without fabricating deltas', () => {
  const current = makeSummary({ eventName: 'pull_request', branch: 'copilot/change', lcp: 1_200 });
  const { lines, comparison } = buildComparison(
    current,
    { schemaVersion: 1, status: 'pending' },
    makeHistory([])
  );
  const report = lines.join('\n');

  assert.match(report, /\*\*Fixed reference:\*\* not comparable; tool or runner versions differ from the reviewed reference|Fixed reference:\*\* unavailable; no complete reviewed successful main reference is available/);
  assert.match(report, /\*\*Rolling daily-main baseline:\*\* collecting \(0\/28 compatible successful daily-main runs\)/);
  assert.match(report, /No comparison\/deltas available yet/);
  assert.doesNotMatch(report, /^\| `\//m);
  assert.equal(comparison.fixedReference.available, false);
  assert.equal(comparison.deltasAvailable, false);
  assert.equal(comparison.comparisons[0].rollingBaseline, null);
});

test('reports unavailable history without disguising an API error as collecting', () => {
  const history = {
    ...makeHistory([], 'unavailable'),
    reason: 'GitHub Actions API returned HTTP 403.',
    skipped: ['history permission denied']
  };
  const { lines, comparison } = buildComparison(
    makeSummary({ eventName: 'pull_request', branch: 'copilot/change' }),
    undefined,
    history
  );
  const report = lines.join('\n');

  assert.match(report, /\*\*Rolling daily-main baseline:\*\* unavailable \(GitHub Actions API returned HTTP 403\)\./);
  assert.match(report, /\*\*History lookup details:\*\* history permission denied/);
  assert.doesNotMatch(report, /collecting \(0\/28/);
  assert.equal(comparison.history.status, 'unavailable');
  assert.equal(comparison.deltasAvailable, false);
});

test('renders a comparison table when the fixed reference is available', () => {
  const fixed = makeSummary({ lcp: 1_000 });
  const { lines, comparison } = buildComparison(
    makeSummary({ eventName: 'pull_request', branch: 'copilot/change', lcp: 1_200 }),
    fixed,
    makeHistory([])
  );

  assert.match(lines.join('\n'), /^\| Route \| Profile \| Fixed Δ/m);
  assert.match(lines.join('\n'), /^\| `\/` \| mobile \|/m);
  assert.equal(comparison.fixedReference.available, true);
  assert.equal(comparison.deltasAvailable, true);
});

test('reports rolling summaries without using them as enforcement thresholds', () => {
  const prior = Array.from({ length: 28 }, (_, index) =>
    makeSummary({
      lcp: 1_000 + index,
      capturedAt: new Date(Date.UTC(2026, 9, index + 1)).toISOString()
    })
  );
  const current = makeSummary({ eventName: 'schedule', branch: 'main', lcp: 1_600 });
  const fixed = makeSummary({ lcp: 1_000 });
  const { lines, comparison } = buildComparison(current, fixed, makeHistory(prior));
  const report = lines.join('\n');

  assert.equal(comparison.history.provenance.length, 28);
  assert.equal(comparison.history.requiredDailySamples, 28);
  assert.match(report, /28\/28 compatible successful daily-main runs; available/);
  assert.match(report, /\+587 ms \(\+57\.9%\)/);
  assert.match(report, /Rolling comparisons are descriptive only and never establish an enforcement threshold/);
  assert.equal(comparison.deltasAvailable, true);
  assert.equal(comparison.policy.confirmationProfiles.length, 8);
});

test('accepts observed Ruby and Node patch drift while preserving exact recorded versions', () => {
  const current = makeSummary();
  current.environment.ruby = 'ruby 3.3.14 (2026-11-19 revision f4c37f481b) [x86_64-linux]';
  current.environment.node = 'v22.23.4';
  const previousNov18 = makeSummary();
  previousNov18.environment.ruby = 'ruby 3.3.13 (2026-10-23 revision af48f08f21) [x86_64-linux]';
  previousNov18.environment.node = 'v22.23.4';
  const previousNov19 = makeSummary();
  previousNov19.environment.ruby = 'ruby 3.3.14 (2026-11-19 revision f4c37f481b) [x86_64-linux]';
  previousNov19.environment.node = 'v22.23.5';
  const history = makeHistory(Array.from({ length: 28 }, (_, index) =>
    structuredClone(index % 2 ? previousNov18 : previousNov19)
  ));

  const { lines, comparison } = buildComparison(current, previousNov18, history);

  assert.equal(comparison.history.status, 'available');
  assert.equal(comparison.history.availableDailySamples, 28);
  assert.equal(comparison.fixedReference.available, true);
  assert.deepEqual(comparison.conditions, []);
  assert.match(lines.join('\n'), /Ruby and Node must match by major\.minor/);
  assert.equal(current.environment.ruby, 'ruby 3.3.14 (2026-11-19 revision f4c37f481b) [x86_64-linux]');
  assert.equal(current.environment.node, 'v22.23.4');
  const pendingReference = buildComparison(
    current,
    { schemaVersion: 1, status: 'pending' },
    history
  ).comparison;
  assert.equal(pendingReference.history.availableDailySamples, 28);
  assert.equal(pendingReference.fixedReference.available, false);
  const collecting = buildComparison(
    current,
    previousNov18,
    makeHistory(Array.from({ length: 27 }, (_, index) =>
      structuredClone(index % 2 ? previousNov18 : previousNov19)
    ))
  ).comparison;
  assert.match(collecting.history.reason, /Ruby and Node major\.minor; other versions exact/);
});

test('rejects different runtime major.minor and exact-measured tool or platform versions', () => {
  const incompatibleEnvironments = [
    ['ruby', 'ruby 3.2.14 (2026-11-19 revision f4c37f481b) [x86_64-linux]', 'Ruby major.minor version differs'],
    ['node', 'v21.23.4', 'Node major.minor version differs'],
    ['lighthouse', '13.6.1', 'Lighthouse version/environment differs'],
    ['chrome', '159.0.8154.7', 'Chrome version/environment differs'],
    ['runner', 'ubuntu-24.04.1', 'runner version/environment differs'],
    ['jekyll', 'jekyll 3.10.1', 'Jekyll version/environment differs'],
    ['chrome', undefined, 'Chrome version/environment is missing']
  ];

  for (const [key, value, diagnostic] of incompatibleEnvironments) {
    const current = makeSummary();
    const baseline = makeSummary();
    baseline.environment[key] = value;
    const { comparison } = buildComparison(current, baseline, makeHistory([baseline]));
    assert.equal(comparison.history.availableDailySamples, 0, `${key} mismatch excludes rolling sample`);
    assert.equal(comparison.fixedReference.available, false, `${key} mismatch excludes fixed reference`);
    assert.ok(comparison.conditions.includes(diagnostic), `${key} mismatch is reported`);
  }
});

test('fails closed on missing or malformed Ruby and Node versions', () => {
  const invalidVersions = [
    ['ruby', undefined],
    ['ruby', ''],
    ['ruby', 'ruby 3.3'],
    ['ruby', '3.3.14-not-a-runtime-output'],
    ['node', undefined],
    ['node', ''],
    ['node', 'v22.23'],
    ['node', 'node v22.23.4']
  ];

  for (const target of ['current', 'baseline']) {
    for (const [key, value] of invalidVersions) {
      const current = makeSummary();
      const baseline = makeSummary();
      const invalid = target === 'current' ? current : baseline;
      invalid.environment[key] = value;
      const { comparison } = buildComparison(current, baseline, makeHistory([baseline]));
      assert.equal(comparison.history.availableDailySamples, 0, `${target} ${key} ${value} excludes rolling sample`);
      assert.equal(comparison.fixedReference.available, false, `${target} ${key} ${value} excludes fixed reference`);
      assert.ok(
        comparison.conditions.includes(`${key === 'ruby' ? 'Ruby' : 'Node'} version is missing or unparseable`),
        `${target} ${key} ${value} is reported as unknown`
      );
    }
  }
});

test('preserves legacy scheduled and new orchestrated Lighthouse history provenance', () => {
  const summary = makeSummary();
  const legacyRun = {
    id: '123',
    event: 'schedule',
    head_branch: 'main',
    status: 'completed',
    conclusion: 'success'
  };
  assert.equal(
    isDailyPerformanceEvidence(summary, legacyRun, 'site-performance-integrity.yml'),
    true
  );
  assert.equal(
    isDailyPerformanceEvidence(summary, legacyRun, 'quality-pulse.yml', true),
    true
  );
  assert.equal(
    isDailyPerformanceEvidence(summary, { ...legacyRun, conclusion: 'failure' }, 'site-performance-integrity.yml'),
    false
  );
  assert.equal(
    isDailyPerformanceEvidence(summary, legacyRun, 'quality-pulse.yml', false),
    false
  );
  assert.equal(
    isDailyPerformanceEvidence(summary, { ...legacyRun, event: 'workflow_dispatch' }, 'quality-pulse.yml', true),
    false
  );
  assert.equal(
    isDailyPerformanceEvidence(summary, { ...legacyRun, event: 'pull_request' }, 'quality-pulse.yml', true),
    false
  );
  assert.equal(
    isDailyPerformanceEvidence(summary, { ...legacyRun, head_branch: 'copilot/change' }, 'quality-pulse.yml', true),
    false
  );
  assert.equal(
    isDailyPerformanceEvidence(
      { ...summary, source: { ...summary.source, runId: '999' } },
      legacyRun,
      'quality-pulse.yml',
      true
    ),
    false
  );
});

test('history loads prior standalone runs and successful performance jobs from orchestrated runs', async () => {
  const runs = {
    'site-performance-integrity.yml': [{
      ...makeRun('legacy-run', '2026-11-16T06:17:00Z'),
      conclusion: 'success'
    }],
    'quality-pulse.yml': [
      { ...makeRun('orchestrated-run', '2026-11-18T06:17:00Z'), conclusion: 'failure' },
      { ...makeRun('failed-performance-run', '2026-11-17T06:17:00Z'), conclusion: 'success' },
      { ...makeRun('manual-run', '2026-11-19T06:17:00Z'), event: 'workflow_dispatch' },
      { ...makeRun('pull-request-run', '2026-11-15T06:17:00Z'), event: 'pull_request' },
      { ...makeRun('branch-run', '2026-11-14T06:17:00Z'), head_branch: 'copilot/change' }
    ]
  };
  const performanceJobs = {
    'orchestrated-run': [{
      name: 'Quality Pulse / Site: Performance Integrity',
      status: 'completed',
      conclusion: 'success'
    }],
    'failed-performance-run': [{
      name: 'Site: Performance Integrity',
      status: 'completed',
      conclusion: 'failure'
    }]
  };
  const artifacts = { 'legacy-run': 901, 'orchestrated-run': 902 };
  let downloadedArtifact;
  const fetchImpl = async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith('/actions/workflows/site-performance-integrity.yml/runs')) {
      return { ok: true, json: async () => ({ workflow_runs: runs['site-performance-integrity.yml'] }) };
    }
    if (url.pathname.endsWith('/actions/workflows/quality-pulse.yml/runs')) {
      return { ok: true, json: async () => ({ workflow_runs: runs['quality-pulse.yml'] }) };
    }
    const jobsMatch = url.pathname.match(/\/actions\/runs\/([^/]+)\/jobs$/);
    if (jobsMatch) {
      return {
        ok: true,
        json: async () => ({
          total_count: performanceJobs[jobsMatch[1]].length,
          jobs: performanceJobs[jobsMatch[1]]
        })
      };
    }
    const artifactsMatch = url.pathname.match(/\/actions\/runs\/([^/]+)\/artifacts$/);
    if (artifactsMatch) {
      return {
        ok: true,
        json: async () => ({
          artifacts: [{
            name: 'performance-summary',
            expired: false,
            size_in_bytes: 10,
            id: artifacts[artifactsMatch[1]]
          }]
        })
      };
    }
    const downloadMatch = url.pathname.match(/\/actions\/artifacts\/(\d+)\/zip$/);
    if (downloadMatch) {
      downloadedArtifact = Number(downloadMatch[1]);
      return { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
    }
    throw new Error(`Unexpected request: ${url.pathname}`);
  };
  const unzipImpl = () => {
    const runId = downloadedArtifact === 902 ? 'orchestrated-run' : 'legacy-run';
    const summary = makeSummary({
      capturedAt: runId === 'orchestrated-run'
        ? '2026-11-18T06:30:00Z'
        : '2026-11-16T06:30:00Z'
    });
    summary.source.runId = runId;
    summary.source.runUrl = `https://github.com/basil-one/circle/actions/runs/${runId}`;
    return JSON.stringify(summary);
  };

  const history = await loadDailyHistory({
    fetchImpl,
    token: 'test-token',
    repository: 'basil-one/circle',
    unzipImpl
  });
  assert.equal(history.status, 'collecting_history');
  assert.deepEqual(history.summaries.map((summary) => summary.source.runId), [
    'orchestrated-run',
    'legacy-run'
  ]);
  assert.ok(history.skipped.some((item) => item.includes('failed-performance-run')));
  assert.equal(history.summaries.some((summary) => summary.source.runId === 'manual-run'), false);
  assert.equal(history.summaries.some((summary) => summary.source.runId === 'pull-request-run'), false);
  assert.equal(history.summaries.some((summary) => summary.source.runId === 'branch-run'), false);
});

test('does not compare with history collected under different tools', () => {
  const prior = Array.from({ length: 28 }, (_, index) => {
    const summary = makeSummary({
      capturedAt: new Date(Date.UTC(2026, 9, index + 1)).toISOString()
    });
    summary.environment.chrome = 'different-version';
    return summary;
  });
  const { lines, comparison } = buildComparison(
    makeSummary({ eventName: 'pull_request', branch: 'copilot/change', lcp: 1_200 }),
    undefined,
    makeHistory(prior)
  );

  assert.match(lines.join('\n'), /0\/28 compatible successful daily-main runs\)/);
  assert.equal(comparison.history.provenance.length, 0);
  assert.equal(comparison.comparisons[0].rollingBaseline, null);
});
