import assert from 'node:assert/strict';
import test from 'node:test';
import {
  applyDependencyPosture,
  buildOrchestratedPulse,
  buildPulse,
  completionEvidenceFromJobs,
  describePerformanceComparison,
  evaluateRuns,
  formatTrendDelta,
  homepageTrendRows,
  isPerformanceComparison,
  paperValidatorResult,
  siteLinkResult,
  renderPerformanceTrends,
  renderPulse,
  validPaperReport
} from '../scripts/quality_pulse.mjs';

const date = '2026-11-16';
const passingRun = {
  created_at: `${date}T06:24:00Z`,
  updated_at: `${date}T06:26:00Z`,
  run_started_at: `${date}T06:24:05Z`,
  status: 'completed',
  conclusion: 'success',
  html_url: 'https://github.com/basil-one/circle/actions/runs/123',
  id: 123
};
const jobsCompletedBeforeCutoff = new Map([
  [123, completionEvidenceFromJobs([
    { status: 'completed', conclusion: 'success', completed_at: `${date}T06:26:00Z` }
  ])]
]);
const performanceComparison = {
  schemaVersion: 1,
  fixedReference: { available: true, runUrl: 'https://github.com/basil-one/circle/actions/runs/99' },
  history: {
    status: 'available',
    availableDailySamples: 28,
    requiredDailySamples: 28,
    searchedRuns: 28,
    reason: null,
    skipped: [],
    provenance: []
  },
  comparisons: [
    {
      route: '/',
      formFactor: 'mobile',
      deltas: {
        'largest-contentful-paint': {
          fixed: { absolute: 1500, percentage: 10 },
          rolling: { absolute: -200, percentage: -5 }
        },
        'total-blocking-time': {
          fixed: { absolute: 25, percentage: null },
          rolling: { absolute: 5, percentage: 2 }
        }
      }
    },
    {
      route: '/',
      formFactor: 'desktop',
      deltas: {
        'largest-contentful-paint': {
          fixed: { absolute: -100, percentage: -4 },
          rolling: { absolute: 200, percentage: 8 }
        }
      }
    },
    {
      route: '/not-the-homepage/',
      formFactor: 'mobile',
      deltas: {
        'largest-contentful-paint': {
          fixed: { absolute: 999, percentage: 999 },
          rolling: { absolute: 999, percentage: 999 }
        }
      }
    }
  ]
};

test('daily scheduled main run completed before cutoff is fresh', () => {
  const result = evaluateRuns([passingRun], date, undefined, jobsCompletedBeforeCutoff);
  assert.equal(result.state, 'fresh');
  assert.equal(result.label, 'Fresh — passed');
  assert.equal(result.run.id, 123);
  assert.equal(result.run.completionEvidence.completedAt, `${date}T06:26:00.000Z`);
});

test('failed and intentionally skipped runs remain distinct', () => {
  assert.equal(
    evaluateRuns([{ ...passingRun, conclusion: 'failure' }], date).state,
    'failed'
  );
  assert.equal(
    evaluateRuns([{ ...passingRun, conclusion: 'skipped' }], date).state,
    'skipped'
  );
});

test('completed job evidence after cutoff is late; run started before cutoff', () => {
  const completedAfterCutoff = new Map([
    [123, completionEvidenceFromJobs([
      { status: 'completed', conclusion: 'success', completed_at: `${date}T18:01:00Z` }
    ])]
  ]);
  assert.equal(
    evaluateRuns([passingRun], date, undefined, completedAfterCutoff).state,
    'late'
  );
  assert.equal(
    evaluateRuns([passingRun], date, undefined, jobsCompletedBeforeCutoff).state,
    'fresh'
  );
});

test('incomplete workflow runs are not fresh', () => {
  assert.equal(
    evaluateRuns([{ ...passingRun, status: 'in_progress', conclusion: null }], date, undefined, jobsCompletedBeforeCutoff).state,
    'late'
  );
});

test('completed run without valid job completion metadata is unknown, not late or green', () => {
  for (const jobs of [
    [],
    [{ status: 'in_progress', conclusion: null, completed_at: null }],
    [{ status: 'completed', conclusion: 'success', completed_at: null }],
    [{ status: 'completed', conclusion: 'success', completed_at: 'not-a-timestamp' }]
  ]) {
    const evidence = new Map([[123, completionEvidenceFromJobs(jobs)]]);
    const result = evaluateRuns([passingRun], date, undefined, evidence);
    assert.equal(result.state, 'unknown');
    assert.match(result.label, /Unknown completion time/);
    assert.match(result.detail, /not marked fresh/);
  }
});

test('a successful workflow run with a failed job is not marked fresh', () => {
  const failedJob = new Map([
    [123, completionEvidenceFromJobs([
      { status: 'completed', conclusion: 'failure', completed_at: `${date}T06:26:00Z` }
    ])]
  ]);
  assert.equal(evaluateRuns([passingRun], date, undefined, failedJob).state, 'unknown');
});

test('Pulse uses actual job completion time, not workflow updated_at', async () => {
  const markdown = await buildPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    date,
    generatedAt: `${date}T18:17:00Z`,
    fetchImpl: async (url) => {
      const path = new URL(url).pathname;
      if (path.endsWith('/actions/workflows/site-build-integrity.yml/runs')) {
        return {
          ok: true,
          json: async () => ({ workflow_runs: [{
            ...passingRun,
            updated_at: `${date}T18:04:00Z`
          }] })
        };
      }
      if (path.endsWith('/actions/runs/123/jobs')) {
        return {
          ok: true,
          json: async () => ({
            total_count: 1,
            jobs: [{
              status: 'completed',
              conclusion: 'success',
              completed_at: `${date}T17:59:00Z`
            }]
          })
        };
      }
      return { ok: true, json: async () => ({ workflow_runs: [] }) };
    }
  });
  assert.match(
    markdown,
    /\| Site: Build Integrity \| Fresh — passed \| Completed 2026-11-16 17:59:00\.000 UTC \|/
  );
});

test('Pulse reports unknown when completion-time job metadata is inaccessible', async () => {
  const markdown = await buildPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    date,
    generatedAt: `${date}T18:17:00Z`,
    fetchImpl: async (url) => {
      const path = new URL(url).pathname;
      if (path.endsWith('/actions/workflows/site-build-integrity.yml/runs')) {
        return { ok: true, json: async () => ({ workflow_runs: [passingRun] }) };
      }
      if (path.endsWith('/actions/runs/123/jobs')) {
        return { ok: false, status: 403 };
      }
      return { ok: true, json: async () => ({ workflow_runs: [] }) };
    }
  });
  assert.match(markdown, /\| Site: Build Integrity \| Unknown completion time \| Started 2026-11-16 06:24:05\.000 UTC \|/);
  assert.match(markdown, /Jobs API returned HTTP 403/);
  assert.match(markdown, /Review required/);
});

test('legacy paper lookup retains failed jobs but cannot establish a pass without reports', async () => {
  const markdown = await buildPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    date,
    fetchImpl: async (url) => {
      const path = new URL(url).pathname;
      if (path.endsWith('/actions/workflows/paper-content-integrity.yml/runs')) {
        return { ok: true, json: async () => ({ workflow_runs: [{ ...passingRun, conclusion: 'failure' }] }) };
      }
      if (path.endsWith('/actions/workflows/paper-build-integrity.yml/runs')) {
        return { ok: true, json: async () => ({ workflow_runs: [passingRun] }) };
      }
      if (path.endsWith('/actions/runs/123/jobs')) {
        return { ok: true, json: async () => ({ total_count: 1, jobs: [
          { status: 'completed', conclusion: 'success', completed_at: `${date}T06:26:00Z` }
        ] }) };
      }
      return { ok: true, json: async () => ({ workflow_runs: [] }) };
    }
  });
  assert.match(markdown, /\| Paper: Build Integrity \| Unknown \/ PDF evidence unavailable \|/);
  assert.match(markdown, /\| Paper: Content Integrity \| Fresh — failed \|/);
});

test('missing today reports prior verification separately from stale data', () => {
  const prior = {
    ...passingRun,
    created_at: '2026-11-15T06:24:00Z',
    updated_at: '2026-11-15T06:26:00Z',
    run_started_at: '2026-11-15T06:24:05Z'
  };
  assert.equal(evaluateRuns([prior], date).state, 'prior');
  const stale = {
    ...prior,
    created_at: '2026-11-10T06:24:00Z',
    updated_at: '2026-11-10T06:26:00Z',
    run_started_at: '2026-11-10T06:24:05Z'
  };
  assert.equal(evaluateRuns([stale], date).state, 'stale');
  assert.equal(evaluateRuns([], date).state, 'missing');
});

test('partial pulse highlights missing coverage and exact run evidence', () => {
  const results = [
    { name: 'Site: Build Integrity', result: evaluateRuns([passingRun], date, undefined, jobsCompletedBeforeCutoff) },
    { name: 'Site: Link Integrity', result: evaluateRuns([], date) },
    {
      name: 'Site: SEO Integrity',
      result: { state: 'unknown', label: 'Unknown / API inaccessible', detail: 'HTTP 403; unsafe\\|cell.' }
    }
  ];
  const summary = renderPulse({
    date,
    generatedAt: `${date}T10:17:00Z`,
    results
  });
  assert.match(summary, /^# Quality Pulse\n/);
  assert.doesNotMatch(summary, /Agency Quality Pulse/);
  assert.match(summary, /Review required — 2 of 3 dimensions/);
  assert.match(summary, /Missing/);
  assert.match(summary, /Unknown \/ API inaccessible/);
  assert.match(summary, /actions\/runs\/123/);
  assert.ok(summary.includes('unsafe\\\\\\|cell.'));
  assert.match(summary, /late or missing runs are not replaced with prior green results/);
});

test('permission-denied Actions API is reported as unknown, not passed', async () => {
  const markdown = await buildPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    date,
    generatedAt: `${date}T10:17:00Z`,
    fetchImpl: async () => ({
      ok: false,
      status: 403,
      json: async () => ({})
    })
  });
  assert.match(markdown, /Unknown \/ API inaccessible/);
  assert.match(markdown, /HTTP 403/);
  assert.match(markdown, /Review required/);
});

test('dependency posture artifacts keep API denial, findings, and clean checks distinct', () => {
  const run = evaluateRuns([passingRun], date, undefined, jobsCompletedBeforeCutoff);
  const unknown = applyDependencyPosture(run, {
    status: 'unknown',
    detail: 'Dependabot alerts API returned HTTP 403'
  });
  assert.equal(unknown.state, 'unknown');
  assert.match(unknown.label, /API inaccessible/);
  assert.match(unknown.detail, /no result is inferred/);

  const review = applyDependencyPosture(run, {
    status: 'review',
    detail: '3 open alerts need review'
  });
  assert.equal(review.state, 'failed');
  assert.match(review.label, /review required/);

  const passed = applyDependencyPosture(run, { status: 'passed' });
  assert.equal(passed.state, 'fresh');
  assert.match(passed.detail, /none met the review threshold/);
});

test('performance baseline details distinguish pending and available comparisons', () => {
  const collectingHistory = (availableDailySamples) => ({
    status: 'collecting_history',
    availableDailySamples,
    requiredDailySamples: 28,
    searchedRuns: 28,
    reason: `${availableDailySamples} of 28 recent successful daily-main summaries match the current tool and runner versions.`,
    skipped: [],
    provenance: []
  });
  assert.equal(
    describePerformanceComparison({
      fixedReference: {
        available: false,
        reason: 'No versioned successful main reference has been established.'
      },
      history: collectingHistory(3)
    }),
    'Fixed reference: pending; rolling baseline: collecting 3/28'
  );
  assert.equal(
    describePerformanceComparison({
      fixedReference: { available: true },
      history: performanceComparison.history
    }),
    'Fixed reference: available; rolling baseline: available 28/28'
  );
  assert.equal(
    describePerformanceComparison({
      fixedReference: { available: true },
      history: {
        status: 'unavailable',
        availableDailySamples: 0,
        requiredDailySamples: 28,
        searchedRuns: 0,
        reason: 'History lookup failed.',
        skipped: [],
        provenance: []
      }
    }),
    'Fixed reference: available; rolling baseline: unavailable'
  );
});

test('prior performance evidence can show its fixed and rolling state without claiming a fresh measurement', () => {
  const run = {
    ...passingRun,
    created_at: '2026-11-15T06:24:00Z',
    updated_at: '2026-11-15T06:26:00Z',
    run_started_at: '2026-11-15T06:24:05Z'
  };
  const result = evaluateRuns([run], date);
  result.baseline = 'fixed reference: pending reviewed main anchor; rolling baseline: collecting (4/28)';
  const summary = renderPulse({
    date,
    generatedAt: `${date}T18:17:00Z`,
    results: [{ name: 'Site: Performance Integrity', result }]
  });
  assert.match(summary, /Prior verification/);
  assert.match(summary, /fixed reference: pending reviewed main anchor/);
  assert.match(summary, /do not treat it as today's check/);
});

test('homepage trends render only representative measures and linked source evidence', () => {
  const markdown = renderPerformanceTrends({
    state: 'fresh',
    trends: {
      state: 'fresh',
      measurementDate: date,
      comparison: performanceComparison,
      runUrl: 'https://github.com/basil-one/circle/actions/runs/123',
      artifactUrl: 'https://github.com/basil-one/circle/actions/runs/123/artifacts/456'
    }
  }).join('\n');
  assert.match(markdown, /Measurement — 2026-11-16 UTC \(fresh scheduled run\)/);
  assert.match(markdown, /Fixed reference: available; rolling baseline: available 28\/28/);
  assert.match(markdown, /\| Mobile LCP \| \+1\.50 s \(\+10\.0%\) \| -200 ms \(-5\.0%\) \|/);
  assert.match(markdown, /\| Mobile TBT \| \+25 ms \| \+5 ms \(\+2\.0%\) \|/);
  assert.match(markdown, /\| Desktop LCP \| -100 ms \(-4\.0%\) \| \+200 ms \(\+8\.0%\) \|/);
  assert.doesNotMatch(markdown, /999/);
  assert.match(markdown, /[Ll]ower is better/);
  assert.match(markdown, /\[Site: Performance Integrity run\]\(https:\/\/github\.com\/basil-one\/circle\/actions\/runs\/123\)/);
  assert.match(markdown, /\[comparison artifact\]\(https:\/\/github\.com\/basil-one\/circle\/actions\/runs\/123\/artifacts\/456\)/);
  assert.match(markdown, /\[fixed reference run\]\(https:\/\/github\.com\/basil-one\/circle\/actions\/runs\/99\)/);
});

test('trend delta formatting uses stored values and never calculates a zero-baseline percentage', () => {
  assert.equal(formatTrendDelta({ absolute: 25, percentage: null }), '+25 ms');
  assert.equal(formatTrendDelta({ absolute: -1_250, percentage: -10 }), '-1.25 s (-10.0%)');
  assert.equal(formatTrendDelta(null), '—');
});

test('fixed-only, rolling-only, and unavailable trend references stay distinct', () => {
  const fixedOnly = structuredClone(performanceComparison);
  fixedOnly.fixedReference = { available: true };
  fixedOnly.history = {
    ...fixedOnly.history,
    status: 'collecting_history',
    availableDailySamples: 3,
    reason: '3 of 28 recent successful daily-main summaries match the current tool and runner versions.'
  };
  for (const comparison of fixedOnly.comparisons) {
    for (const deltas of Object.values(comparison.deltas)) deltas.rolling = null;
  }
  let output = renderPerformanceTrends({
    trends: { state: 'fresh', measurementDate: date, comparison: fixedOnly }
  }).join('\n');
  assert.match(output, /Fixed reference: available; rolling baseline: collecting 3\/28/);
  assert.match(output, /\| Mobile LCP \| \+1\.50 s \(\+10\.0%\) \| — \|/);

  const rollingOnly = structuredClone(performanceComparison);
  rollingOnly.fixedReference = { available: false, reason: 'No versioned successful main reference has been established.' };
  for (const comparison of rollingOnly.comparisons) {
    for (const deltas of Object.values(comparison.deltas)) deltas.fixed = null;
  }
  output = renderPerformanceTrends({
    trends: { state: 'fresh', measurementDate: date, comparison: rollingOnly }
  }).join('\n');
  assert.match(output, /Fixed reference: pending; rolling baseline: available 28\/28/);
  assert.match(output, /\| Mobile LCP \| — \| -200 ms \(-5\.0%\) \|/);

  const noBaselines = structuredClone(rollingOnly);
  noBaselines.fixedReference = { available: false, reason: 'The versioned reference uses a different tool or runner environment.' };
  noBaselines.history = {
    status: 'unavailable',
    availableDailySamples: 0,
    requiredDailySamples: 28,
    searchedRuns: 0,
    reason: 'History lookup failed.',
    skipped: [],
    provenance: []
  };
  output = renderPerformanceTrends({
    trends: { state: 'fresh', measurementDate: date, comparison: noBaselines }
  }).join('\n');
  assert.match(output, /Fixed reference: incompatible; rolling baseline: unavailable/);
  assert.match(output, /\| Mobile LCP \| — \| — \|/);
  assert.equal(
    describePerformanceComparison({
      fixedReference: { available: true },
      history: { status: 'available', availableDailySamples: 4, requiredDailySamples: 28 }
    }),
    'Fixed reference: available; rolling baseline: unknown'
  );
});

test('missing homepage route or metric never borrows another route or invents deltas', () => {
  const comparison = structuredClone(performanceComparison);
  comparison.comparisons = comparison.comparisons.filter((item) => item.route !== '/');
  assert.deepEqual(homepageTrendRows(comparison), []);
  let output = renderPerformanceTrends({
    trends: { state: 'fresh', measurementDate: date, comparison }
  }).join('\n');
  assert.match(output, /\| Homepage metrics \| — \| — \|/);

  comparison.comparisons = [{
    route: '/',
    formFactor: 'mobile',
    deltas: { 'largest-contentful-paint': { fixed: null, rolling: null } }
  }];
  assert.deepEqual(homepageTrendRows(comparison).map((row) => row.label), ['Mobile LCP']);
  output = renderPerformanceTrends({
    trends: { state: 'fresh', measurementDate: date, comparison }
  }).join('\n');
  assert.match(output, /\| Mobile LCP \| — \| — \|/);
  assert.doesNotMatch(output, /\| Desktop LCP \|/);
});

test('prior trends are dated, while non-successful performance evidence has no deltas', () => {
  const priorOutput = renderPerformanceTrends({
    trends: {
      state: 'prior',
      measurementDate: '2026-11-15',
      comparison: performanceComparison
    }
  }).join('\n');
  assert.match(priorOutput, /Prior successful measurement — 2026-11-15; not today's verification/);

  for (const [state, label] of [
    ['failed', 'Fresh — failed'],
    ['skipped', 'Skipped'],
    ['missing', 'Missing'],
    ['late', 'Late'],
    ['unknown', 'Unknown outcome']
  ]) {
    const summary = renderPulse({
      date,
      generatedAt: `${date}T18:17:00Z`,
      results: [{
        name: 'Site: Performance Integrity',
        result: {
          state,
          label,
          detail: 'No successful current measurement is established.',
          trends: {
            state,
            measurementDate: date,
            comparison: performanceComparison,
            reason: `today's Site: Performance Integrity result is ${label.toLowerCase()}; no successful current trend is shown`
          }
        }
      }]
    });
    assert.match(summary, /Performance comparison unavailable/);
    assert.doesNotMatch(summary, /\| Mobile LCP \|/);
    assert.doesNotMatch(summary, /fresh scheduled run/);
  }
});

test('performance comparison artifact schema is checked before rendering', () => {
  assert.equal(isPerformanceComparison(performanceComparison), true);
  assert.equal(isPerformanceComparison({ schemaVersion: 1, comparisons: [] }), false);
  assert.equal(isPerformanceComparison({ ...performanceComparison, schemaVersion: 2 }), false);
});

test('Pulse loads a valid comparison artifact and keeps failed or unavailable artifacts out of trends', async () => {
  const successRun = {
    ...passingRun,
    id: 700,
    html_url: 'https://github.com/basil-one/circle/actions/runs/700'
  };
  const successfulJobs = {
    total_count: 1,
    jobs: [{ status: 'completed', conclusion: 'success', completed_at: `${date}T06:26:00Z` }]
  };
  const buildWithArtifact = async ({
    artifactResponse,
    artifactJson = performanceComparison,
    conclusion = 'success'
  }) => {
    const fetchImpl = async (url) => {
      const path = new URL(url).pathname;
      if (path.endsWith('/actions/workflows/site-performance-integrity.yml/runs')) {
        return { ok: true, json: async () => ({ workflow_runs: [{ ...successRun, conclusion }] }) };
      }
      if (path.endsWith('/actions/runs/700/jobs')) {
        return { ok: true, json: async () => successfulJobs };
      }
      if (path.endsWith('/actions/runs/700/artifacts')) return artifactResponse;
      if (path.endsWith('/actions/artifacts/456/zip')) {
        return { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
      }
      return { ok: true, json: async () => ({ workflow_runs: [] }) };
    };
    return buildPulse({
      apiUrl: 'https://api.github.com',
      repository: 'basil-one/circle',
      token: 'test-token',
      date,
      generatedAt: `${date}T18:17:00Z`,
      fetchImpl,
      unzipImpl: () => JSON.stringify(artifactJson)
    });
  };

  const valid = await buildWithArtifact({
    artifactResponse: {
      ok: true,
      json: async () => ({ artifacts: [{ name: 'performance-summary', expired: false, id: 456 }] })
    }
  });
  assert.match(valid, /\| Mobile LCP \| \+1\.50 s/);
  assert.match(valid, /actions\/runs\/700\/artifacts\/456/);
  assert.ok(valid.indexOf('## Findings') < valid.indexOf('## Performance Trends'));
  assert.ok(valid.indexOf('## Performance Trends') < valid.indexOf('## Evidence'));

  const malformed = await buildWithArtifact({
    artifactResponse: {
      ok: true,
      json: async () => ({ artifacts: [{ name: 'performance-summary', expired: false, id: 456 }] })
    },
    artifactJson: { schemaVersion: 1, comparisons: [] }
  });
  assert.match(malformed, /comparison artifact unavailable or invalid/);
  assert.doesNotMatch(malformed, /\| Mobile LCP \|/);

  const expired = await buildWithArtifact({
    artifactResponse: {
      ok: true,
      json: async () => ({ artifacts: [{ name: 'performance-summary', expired: true, id: 456 }] })
    }
  });
  assert.match(expired, /artifact is missing or expired/);
  assert.doesNotMatch(expired, /\| Mobile LCP \|/);

  const inaccessible = await buildWithArtifact({
    artifactResponse: { ok: false, status: 403 }
  });
  assert.match(inaccessible, /Artifacts API returned HTTP 403/);
  assert.doesNotMatch(inaccessible, /\| Mobile LCP \|/);

  const failedMeasurement = await buildWithArtifact({
    artifactResponse: { ok: true, json: async () => ({ artifacts: [] }) },
    conclusion: 'failure'
  });
  assert.match(failedMeasurement, /Site: Performance Integrity result is fresh — failed/);
  assert.doesNotMatch(failedMeasurement, /\| Mobile LCP \|/);
});

test('real comparison artifact history states render through source lookup with honest homepage deltas', async () => {
  const artifactResponse = {
    ok: true,
    json: async () => ({ artifacts: [{ name: 'performance-summary', expired: false, id: 456 }] })
  };
  const buildArtifact = async (history) => {
    const comparison = structuredClone(performanceComparison);
    comparison.history = history;
    for (const result of comparison.comparisons) {
      for (const deltas of Object.values(result.deltas)) {
        deltas.rolling = history.status === 'available' ? deltas.rolling : null;
      }
    }
    return buildPulse({
      apiUrl: 'https://api.github.com',
      repository: 'basil-one/circle',
      token: 'test-token',
      date,
      generatedAt: `${date}T18:17:00Z`,
      fetchImpl: async (url) => {
        const path = new URL(url).pathname;
        if (path.endsWith('/actions/workflows/site-performance-integrity.yml/runs')) {
          return {
            ok: true,
            json: async () => ({ workflow_runs: [{ ...passingRun, id: 700 }] })
          };
        }
        if (path.endsWith('/actions/runs/700/jobs')) {
          return {
            ok: true,
            json: async () => ({
              total_count: 1,
              jobs: [{
                status: 'completed',
                conclusion: 'success',
                completed_at: `${date}T06:26:00Z`
              }]
            })
          };
        }
        if (path.endsWith('/actions/runs/700/artifacts')) return artifactResponse;
        if (path.endsWith('/actions/artifacts/456/zip')) {
          return { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
        }
        return { ok: true, json: async () => ({ workflow_runs: [] }) };
      },
      unzipImpl: () => JSON.stringify(comparison)
    });
  };
  const history = (status, availableDailySamples, extra = {}) => ({
    status,
    availableDailySamples,
    requiredDailySamples: 28,
    searchedRuns: 28,
    reason: status === 'collecting_history'
      ? `${availableDailySamples} of 28 recent successful daily-main summaries match the current tool and runner versions.`
      : undefined,
    skipped: [],
    provenance: [],
    ...extra
  });

  for (const count of [0, 3, 27]) {
    const markdown = await buildArtifact(history('collecting_history', count));
    assert.match(markdown, new RegExp(`rolling baseline: collecting ${count}/28`));
    assert.match(markdown, /\| Mobile LCP \| \+1\.50 s \(\+10\.0%\) \| — \|/);
    assert.match(markdown, /\[comparison artifact\]\(https:\/\/github\.com\/basil-one\/circle\/actions\/runs\/700\/artifacts\/456\)/);
    assert.ok(markdown.indexOf('## Performance Trends') < markdown.indexOf('## Evidence'));
  }

  const available = await buildArtifact(history('available', 28));
  assert.match(available, /rolling baseline: available 28\/28/);
  assert.match(available, /\| Mobile LCP \| \+1\.50 s \(\+10\.0%\) \| -200 ms \(-5\.0%\) \|/);

  const unavailable = await buildArtifact(history('unavailable', 0, {
    reason: 'History lookup failed.'
  }));
  assert.match(unavailable, /rolling baseline: unavailable/);
  assert.match(unavailable, /\| Mobile LCP \| \+1\.50 s \(\+10\.0%\) \| — \|/);

  for (const malformed of [
    history('collecting_history', 28),
    history('collecting_history', -1),
    history('available', 27),
    history('unavailable', 1),
    history('collecting_history', 3, { requiredDailySamples: 27 }),
    history('other', 3)
  ]) {
    const markdown = await buildArtifact(malformed);
    assert.match(markdown, /rolling baseline: unknown/);
    assert.match(markdown, /\| Mobile LCP \| \+1\.50 s \(\+10\.0%\) \| — \|/);
  }
});

test('orchestrated Pulse reports same-cycle job results and requires valid evidence artifacts', async () => {
  const runId = '888';
  const runUrl = `https://github.com/basil-one/circle/actions/runs/${runId}`;
  const needs = Object.fromEntries([
    'site-build-integrity',
    'site-link-integrity',
    'site-seo-integrity',
    'common-code-quality',
    'common-dependency-security',
    'site-rendering-integrity',
    'site-performance-integrity',
    'paper-build-integrity',
    'paper-content-integrity',
    'paper-link-integrity',
    'paper-rendering-integrity'
  ].map((jobId) => [jobId, { result: 'success' }]));
  needs['site-link-integrity'].outputs = { status: 'success' };
  needs['paper-publication-integrity'] = {
    result: 'success',
    outputs: {
      status: 'passed', detail: 'Published release agrees.',
      main_sha: 'a'.repeat(40), validator_sha: 'b'.repeat(40)
    }
  };
  needs['paper-content-integrity'].outputs = { status: 'success' };
  needs['paper-link-integrity'].outputs = { status: 'success' };
  needs['paper-rendering-integrity'].outputs = { status: 'success' };
  const performanceSummary = {
    complete: true,
    capturedAt: `${date}T06:30:00.000Z`,
    source: { eventName: 'schedule', branch: 'main', runId }
  };
  const fetchImpl = async (url) => {
    const parsed = new URL(url);
    if (parsed.pathname.endsWith('/actions/runs/888/artifacts')) {
      const artifactName = parsed.searchParams.get('name');
      const id = artifactName === 'performance-summary'
        ? 456
        : artifactName === 'circle3-paper-preview'
          ? 458
          : 457;
      return {
        ok: true,
        json: async () => ({ artifacts: [{ name: artifactName, expired: false, id }] })
      };
    }
    if (/\/actions\/artifacts\/(456|457)\/zip$/.test(parsed.pathname)) {
      return { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
    }
    throw new Error(`Unexpected request: ${parsed.pathname}`);
  };
  const unzipImpl = (_command, args) => {
    if (args.at(-1) === 'performance-comparison.json') return JSON.stringify(performanceComparison);
    if (args.at(-1) === 'performance-summary.json') return JSON.stringify(performanceSummary);
    if (args.at(-1) === 'dependency-posture.json') {
      return JSON.stringify({ status: 'passed', detail: 'No open medium-or-higher alerts' });
    }
    if (['paper-content-report.json', 'paper-link-report.json', 'paper-rendering-report.json'].includes(args.at(-1))) {
      return JSON.stringify({ status: 'passed', findings: [] });
    }
    throw new Error(`Unexpected artifact file: ${args.at(-1)}`);
  };
  const pulse = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    needs,
    runId,
    runUrl,
    validatorRevision: 'b'.repeat(40),
    date,
    generatedAt: `${date}T06:40:00Z`,
    fetchImpl,
    unzipImpl
  });

  assert.equal(pulse.successful, true);
  assert.match(pulse.markdown, /All 12 dimensions passed in this cycle/);
  assert.match(pulse.markdown, /\| Site: Build Integrity \| Passed \| Same-cycle job result \|/);
  assert.match(pulse.markdown, /\| Paper: Build Integrity \| Passed \| Same-cycle job result \|/);
  assert.match(pulse.markdown, /\| Paper: Publication Integrity \| Passed \| Same-cycle job result \|/);
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Passed \| Same-cycle job result \|/);
  assert.match(pulse.markdown, /\| Paper: Link Integrity \| Passed \| Same-cycle job result \|/);
  assert.match(pulse.markdown, /\| Paper: Rendering Integrity \| Passed \| Same-cycle job result \|/);
  assert.match(pulse.markdown, /actions\/runs\/888\/artifacts\/458/);
  assert.match(pulse.markdown, /\[same-cycle run\]\(https:\/\/github\.com\/basil-one\/circle\/actions\/runs\/888\)/);
  assert.match(pulse.markdown, /actions\/runs\/888\/artifacts\/456/);
  assert.match(pulse.markdown, /actions\/runs\/888\/artifacts\/457/);
  assert.match(pulse.markdown, /No fixed wall-clock completion cutoff is applied/);
  assert.doesNotMatch(pulse.markdown, /18:00:00Z|prior verification/);
  const inventory = [...pulse.markdown.matchAll(/^\| ([^|]+) \| (?:Passed|Advisory|Failed|Unknown|Incomplete)/gm)]
    .map(([, name]) => name);
  assert.deepEqual(inventory, [
    'Common: Code Quality',
    'Common: Dependency Security',
    'Site: Build Integrity',
    'Site: Link Integrity',
    'Site: SEO Integrity',
    'Site: Rendering Integrity',
    'Site: Performance Integrity',
    'Paper: Build Integrity',
    'Paper: Content Integrity',
    'Paper: Link Integrity',
    'Paper: Rendering Integrity',
    'Paper: Publication Integrity'
  ]);

  performanceSummary.source.eventName = 'workflow_dispatch';
  const manualPulse = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    needs,
    runId,
    runUrl,
    eventName: 'workflow_dispatch',
    validatorRevision: 'b'.repeat(40),
    date,
    generatedAt: `${date}T06:40:00Z`,
    fetchImpl,
    unzipImpl
  });
  assert.equal(manualPulse.successful, true);
  assert.match(manualPulse.markdown, /manually dispatched cycle/);

  needs['paper-link-integrity'].outputs.status = 'advisory';
  const linkAdvisory = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    needs,
    runId,
    runUrl,
    eventName: 'workflow_dispatch',
    validatorRevision: 'b'.repeat(40),
    date,
    fetchImpl,
    unzipImpl: (command, args) => args.at(-1) === 'paper-link-report.json'
      ? JSON.stringify({ status: 'advisory', findings: ['External destination timed out; retry.'] })
      : unzipImpl(command, args)
  });
  assert.equal(linkAdvisory.successful, true);
  assert.match(linkAdvisory.markdown, /Cycle successful with advisories — 1 of 12 dimensions/);
  assert.match(linkAdvisory.markdown, /\| Paper: Publication Integrity \| Passed \|/);
  needs['paper-link-integrity'].outputs.status = 'success';

  needs['paper-build-integrity'].result = 'failure';
  const independent = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com', repository: 'basil-one/circle', token: 'test-token',
    needs, runId, runUrl, date, fetchImpl, unzipImpl, eventName: 'workflow_dispatch'
  });
  assert.equal(independent.successful, false);
  assert.match(independent.markdown, /\| Paper: Build Integrity \| Failed \|/);
  assert.match(independent.markdown, /\| Paper: Content Integrity \| Passed \|/);
  assert.match(independent.markdown, /\| Paper: Link Integrity \| Passed \|/);
  assert.match(independent.markdown, /\| Paper: Rendering Integrity \| Passed \|/);
  needs['paper-build-integrity'].result = 'success';
  const noPreview = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com', repository: 'basil-one/circle', token: 'test-token',
    needs, runId, runUrl, date, unzipImpl, eventName: 'workflow_dispatch',
    fetchImpl: async (url) => new URL(url).searchParams.get('name') === 'circle3-paper-preview'
      ? { ok: true, json: async () => ({ artifacts: [] }) }
      : fetchImpl(url)
  });
  assert.equal(noPreview.successful, false);
  assert.match(noPreview.markdown, /\| Paper: Build Integrity \| Unknown \/ PDF preview unavailable \|/);
  assert.match(noPreview.markdown, /\| Paper: Content Integrity \| Passed \|/);

  needs['paper-content-integrity'].outputs.status = 'advisory';
  const advisoryPulse = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com', repository: 'basil-one/circle', token: 'test-token',
    needs, runId, runUrl, date, fetchImpl, eventName: 'workflow_dispatch',
    unzipImpl: (command, args) => args.at(-1) === 'paper-content-report.json'
      ? JSON.stringify({ status: 'advisory', findings: ['External source timed out; retry'] })
      : unzipImpl(command, args)
  });
  assert.equal(advisoryPulse.successful, false);
  assert.match(advisoryPulse.markdown, /\| Paper: Content Integrity \| Unknown outcome \|/);
  assert.match(advisoryPulse.markdown, /External source timed out; retry/);
  assert.doesNotMatch(advisoryPulse.markdown, /Cycle successful with advisories|All 11 dimensions passed/);
});

test('paper validator outcomes and bounded reports never infer a pass', () => {
  const run = { id: '1', html_url: 'https://example.test/1', cycle: true };
  for (const [job, status, expected] of [
    ['success', 'success', 'fresh'],
    ['success', 'advisory', 'advisory'],
    ['failure', 'failure', 'failed'],
    ['cancelled', 'failure', 'failed'],
    ['skipped', '', 'unknown'],
    ['failure', 'incomplete', 'incomplete'],
    ['failure', '', 'incomplete'],
    ['cancelled', 'success', 'incomplete'],
    ['success', 'failure', 'unknown'],
    ['failure', 'success', 'incomplete']
  ]) {
    assert.equal(paperValidatorResult('Paper: Link Integrity', job, status, run).result.state, expected);
  }
  assert.equal(validPaperReport({ status: 'passed', findings: [] }, 'passed'), true);
  assert.equal(validPaperReport({ status: 'incomplete', findings: ['Extraction failed'] }, 'incomplete'), true);
  assert.equal(validPaperReport({ status: 'advisory', findings: ['Timeout'] }, 'advisory'), true);
  assert.equal(validPaperReport({ status: 'failed', findings: [] }, 'failed'), false);
  assert.equal(validPaperReport({ status: 'passed', findings: [] }, 'failed'), false);
  assert.equal(validPaperReport({ status: 'failed', findings: Array(101).fill('x') }, 'failed'), false);
  assert.equal(validPaperReport({ status: 'failed', findings: ['x'.repeat(2001)] }, 'failed'), false);
  const linkIncomplete = paperValidatorResult('Paper: Link Integrity', 'failure', 'incomplete', run);
  assert.equal(linkIncomplete.result.label, 'Incomplete / link verification');
  assert.match(linkIncomplete.result.detail, /link job/);
  assert.equal(paperValidatorResult('Paper: Content Integrity', 'success', 'advisory', run).result.state, 'unknown');
  assert.equal(paperValidatorResult('Paper: Rendering Integrity', 'success', 'advisory', run).result.state, 'unknown');
  assert.equal(paperValidatorResult('Paper: Link Integrity', 'failure', 'advisory', run).result.state, 'incomplete');
  const advisoryOnly = renderPulse({
    date, generatedAt: `${date}T06:40:00Z`, orchestrated: true,
    results: [paperValidatorResult('Paper: Link Integrity', 'success', 'advisory', run)]
  });

  test('Site Link Pulse outcomes distinguish advisory, defects, and incomplete evidence', () => {
    const run = { id: '2', html_url: 'https://example.test/2', cycle: true };
    for (const [job, status, expected] of [
      ['success', 'success', 'fresh'],
      ['success', 'advisory', 'advisory'],
      ['failure', 'failure', 'failed'],
      ['failure', undefined, 'incomplete'],
      ['failure', 'incomplete', 'incomplete'],
      ['success', undefined, 'unknown'],
      ['success', 'failure', 'unknown'],
      ['failure', 'advisory', 'unknown']
    ]) {
      const result = siteLinkResult({
        'site-link-integrity': { result: job, outputs: status === undefined ? undefined : { status } }
      }, run);
      assert.equal(result.result.state, expected);
    }
  });
  assert.match(advisoryOnly, /Cycle successful with advisories/);
  const mixed = renderPulse({
    date, generatedAt: `${date}T06:40:00Z`, orchestrated: true,
    results: [
      paperValidatorResult('Paper: Link Integrity', 'success', 'advisory', run),
      paperValidatorResult('Paper: Content Integrity', 'failure', 'failure', run),
      paperValidatorResult('Paper: Rendering Integrity', 'failure', 'incomplete', run)
    ]
  });
  assert.match(mixed, /Cycle incomplete — 2 of 3 dimensions/);
  assert.match(mixed, /Paper: Link Integrity \| Advisory/);
  assert.match(mixed, /Paper: Content Integrity \| Failed/);
  assert.match(mixed, /Paper: Rendering Integrity \| Incomplete/);
});

test('orchestrated Pulse never treats failed, skipped, cancelled, unknown, or missing jobs as passed', async () => {
  const pulse = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    needs: {
      'site-build-integrity': { result: 'failure' },
      'site-link-integrity': { result: 'skipped' },
      'site-seo-integrity': { result: 'cancelled' },
      'common-code-quality': { result: 'unexpected' },
      'common-dependency-security': { result: 'skipped' },
      'site-rendering-integrity': { result: 'skipped' },
      'site-performance-integrity': { result: 'skipped' },
      'paper-build-integrity': { result: 'cancelled' },
      'paper-publication-integrity': { result: 'failure', outputs: { status: 'unknown' } },
      'paper-content-integrity': { result: 'cancelled' }
    },
    runId: '889',
    runUrl: 'https://github.com/basil-one/circle/actions/runs/889',
    date,
    generatedAt: `${date}T06:40:00Z`,
    fetchImpl: async () => {
      throw new Error('No artifact should be requested for an unexecuted job');
    }
  });
  assert.equal(pulse.successful, false);
  assert.match(pulse.markdown, /Cycle incomplete — 12 of 12 dimensions/);
  assert.match(pulse.markdown, /\| Site: Build Integrity \| Failed \|/);
  assert.match(pulse.markdown, /\| Site: Link Integrity \| Unknown \/ link evidence unavailable \|/);
  assert.match(pulse.markdown, /\| Site: SEO Integrity \| Incomplete \/ cancelled \|/);
  assert.match(pulse.markdown, /\| Common: Code Quality \| Unknown outcome \|/);
  assert.match(pulse.markdown, /\| Site: Performance Integrity \| Skipped \|/);
  assert.match(pulse.markdown, /\| Paper: Build Integrity \| Incomplete \/ cancelled \|/);
  assert.match(pulse.markdown, /\| Paper: Publication Integrity \| Unknown \/ publication evidence unavailable \|/);

  const missing = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    needs: {},
    runId: '890',
    runUrl: 'https://github.com/basil-one/circle/actions/runs/890',
    date
  });
  assert.equal(missing.successful, false);
  assert.match(missing.markdown, /\| Site: Build Integrity \| Missing \|/);
  assert.match(missing.markdown, /\| Paper: Build Integrity \| Missing \|/);
  assert.match(missing.markdown, /\| Paper: Content Integrity \| Unknown outcome \|/);
  assert.match(missing.markdown, /\| Paper: Link Integrity \| Unknown outcome \|/);
  assert.match(missing.markdown, /\| Paper: Rendering Integrity \| Unknown outcome \|/);
});

test('orchestrated Pulse reports confirmed performance detail and distinguishes incomplete or legacy evidence', async () => {
  const runId = '892';
  const runUrl = `https://github.com/basil-one/circle/actions/runs/${runId}`;
  const performanceSummary = {
    complete: true,
    capturedAt: `${date}T06:30:00.000Z`,
    source: { eventName: 'schedule', branch: 'main', runId }
  };
  const needs = {
    'site-build-integrity': { result: 'success' },
    'site-link-integrity': { result: 'success', outputs: { status: 'success' } },
    'site-seo-integrity': { result: 'success' },
    'common-code-quality': { result: 'success' },
    'common-dependency-security': { result: 'success' },
    'site-rendering-integrity': { result: 'success' },
    'site-performance-integrity': { result: 'failure' },
    'paper-build-integrity': { result: 'success' },
    'paper-content-integrity': { result: 'success', outputs: { status: 'success' } }
  };
  const buildFailurePulse = async (comparison) => buildOrchestratedPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    needs,
    runId,
    runUrl,
    date,
    fetchImpl: async (url) => {
      const parsed = new URL(url);
      if (parsed.pathname.endsWith(`/actions/runs/${runId}/artifacts`)) {
        const artifactName = parsed.searchParams.get('name');
        if (artifactName === 'circle3-paper-preview') {
          return { ok: true, json: async () => ({ artifacts: [] }) };
        }
        return {
          ok: true,
          json: async () => ({
            artifacts: [{
              name: artifactName,
              expired: false,
              id: artifactName === 'performance-summary' ? 456 : 457
            }]
          })
        };
      }
      if (/\/actions\/artifacts\/(456|457)\/zip$/.test(parsed.pathname)) {
        return { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
      }
      throw new Error(`Unexpected request: ${parsed.pathname}`);
    },
    unzipImpl: (_command, args) => {
      if (args.at(-1) === 'performance-comparison.json') return JSON.stringify(comparison);
      if (args.at(-1) === 'performance-summary.json') return JSON.stringify(performanceSummary);
      if (args.at(-1) === 'dependency-posture.json') {
        return JSON.stringify({ status: 'passed', detail: 'No open medium-or-higher alerts' });
      }
      throw new Error(`Unexpected artifact file: ${args.at(-1)}`);
    }
  });

  const confirmedComparison = structuredClone(performanceComparison);
  confirmedComparison.policy = {
    status: 'failed',
    confirmedViolations: [
      {
        route: '/method/',
        formFactor: 'mobile',
        metric: 'largest-contentful-paint',
        criterion: 'absolute',
        value: 3_604,
        confirmationValue: 3_201,
        limit: 3_000
      },
      {
        route: '/method/',
        formFactor: 'mobile',
        metric: 'largest-contentful-paint',
        criterion: 'reference-deterioration',
        value: 3_604,
        confirmationValue: 3_201,
        referenceValue: 2_405,
        delta: 1_199,
        deltaPercentage: 49.8
      }
    ]
  };
  const confirmed = await buildFailurePulse(confirmedComparison);
  assert.equal(confirmed.successful, false);
  assert.match(confirmed.markdown, /\| Site: Performance Integrity \| Failed \|/);
  assert.match(confirmed.markdown, /\| Paper: Build Integrity \| Unknown \/ PDF preview unavailable \|/);
  assert.match(confirmed.markdown, /Confirmed performance violation\(s\)/);
  assert.match(confirmed.markdown, /`\/method\/`\/mobile largest-contentful-paint: 3\.60 s initially, 3\.20 s confirmed vs 3\.00 s limit/);
  assert.match(confirmed.markdown, /reference 2\.40 s \(Δ 1\.20 s, \+49\.8%\)/);
  assert.match(confirmed.markdown, /Next step: correct the affected route\/profile and rerun Performance Integrity/);

  const incompleteComparison = structuredClone(performanceComparison);
  incompleteComparison.policy = {
    status: 'incomplete',
    confirmationReason: 'repeat sample set was incomplete',
    confirmedViolations: []
  };
  const incomplete = await buildFailurePulse(incompleteComparison);
  assert.match(incomplete.markdown, /Performance confirmation incomplete \(repeat sample set was incomplete\); no pass is inferred/);
  assert.doesNotMatch(incomplete.markdown, /Confirmed performance violation\(s\)/);

  const failedAfterPassComparison = structuredClone(performanceComparison);
  failedAfterPassComparison.policy = { status: 'passed', confirmedViolations: [] };
  const failedAfterPass = await buildFailurePulse(failedAfterPassComparison);
  assert.match(failedAfterPass.markdown, /policy evidence reports no confirmed violation, but the performance job failed in another required operation/);

  const malformedPolicyComparison = structuredClone(performanceComparison);
  malformedPolicyComparison.policy = { status: 'failed', confirmedViolations: [{}] };
  const malformedPolicy = await buildFailurePulse(malformedPolicyComparison);
  assert.match(malformedPolicy.markdown, /Performance policy evidence is incomplete: the job failed without a valid confirmed-violation record/);

  const legacy = await buildFailurePulse(performanceComparison);
  assert.match(legacy.markdown, /comparison artifact predates structured policy evidence/);
});

test('orchestrated Pulse marks missing same-cycle artifacts unknown rather than successful', async () => {
  const pulse = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    needs: {
      'site-build-integrity': { result: 'success' },
      'site-link-integrity': { result: 'success', outputs: { status: 'success' } },
      'site-seo-integrity': { result: 'success' },
      'common-code-quality': { result: 'success' },
      'common-dependency-security': { result: 'success' },
      'site-rendering-integrity': { result: 'success' },
      'site-performance-integrity': { result: 'success' },
      'paper-build-integrity': { result: 'success' },
      'paper-content-integrity': { result: 'success', outputs: { status: 'success' } }
    },
    runId: '891',
    runUrl: 'https://github.com/basil-one/circle/actions/runs/891',
    date,
    fetchImpl: async () => ({ ok: true, json: async () => ({ artifacts: [] }) })
  });

  assert.equal(pulse.successful, false);
  assert.match(pulse.markdown, /\| Common: Dependency Security \| Unknown \/ posture artifact unavailable \|/);
  assert.match(pulse.markdown, /\| Site: Performance Integrity \| Unknown \/ performance evidence unavailable \|/);
  assert.match(pulse.markdown, /\| Paper: Build Integrity \| Unknown \/ PDF preview unavailable \|/);
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Unknown \/ content evidence unavailable \|/);
  assert.match(pulse.markdown, /no clean result is inferred/);
  assert.match(pulse.markdown, /no comparison or pass is inferred/);
});

test('publication mismatch, inaccessible evidence and contradictory outputs remain distinct', async () => {
  for (const [result, status, expected] of [
    ['failure', 'failed', 'Failed'],
    ['failure', 'unknown', 'Unknown / publication evidence unavailable'],
    ['success', undefined, 'Unknown / publication evidence unavailable'],
    ['success', 'failed', 'Failed'],
    ['failure', 'passed', 'Unknown / publication evidence unavailable'],
    ['cancelled', 'failed', 'Failed'],
    ['cancelled', undefined, 'Incomplete / cancelled']
  ]) {
    const pulse = await buildOrchestratedPulse({
      apiUrl: 'https://api.github.com',
      repository: 'basil-one/circle',
      needs: { 'paper-publication-integrity': { result, outputs: { status } } },
      runId: '900',
      runUrl: 'https://github.com/basil-one/circle/actions/runs/900',
      date
    });
    assert.equal(pulse.successful, false);
    assert.ok(pulse.markdown.includes(`| Paper: Publication Integrity | ${expected} |`));
  }
});

test('publication can pass independently when the paper build fails', async () => {
  const pulse = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    needs: {
      'paper-build-integrity': { result: 'failure' },
      'paper-publication-integrity': {
        result: 'success',
        outputs: { status: 'passed', detail: 'Published release agrees.', main_sha: 'a'.repeat(40), validator_sha: 'b'.repeat(40) }
      }
    },
    runId: '903',
    runUrl: 'https://github.com/basil-one/circle/actions/runs/903',
    date
  });
  assert.equal(pulse.successful, false);
  assert.match(pulse.markdown, /\| Paper: Build Integrity \| Failed \|/);
  assert.match(pulse.markdown, /\| Paper: Publication Integrity \| Passed \|/);
});

test('publication pass requires bounded detail and matching revision provenance', async () => {
  const valid = {
    status: 'passed',
    detail: 'Published release agrees.',
    main_sha: 'a'.repeat(40),
    validator_sha: 'b'.repeat(40)
  };
  const cases = [
    { detail: undefined }, { detail: '  ' }, { detail: 'x'.repeat(1001) },
    { detail: 'bad\noutput' }, { main_sha: undefined }, { main_sha: 'a'.repeat(41) },
    { validator_sha: undefined }, { validator_sha: 'z'.repeat(40) },
    { validator_sha: 'c'.repeat(40) }
  ];
  for (const invalid of cases) {
    const pulse = await buildOrchestratedPulse({
      apiUrl: 'https://api.github.com',
      repository: 'basil-one/circle',
      needs: { 'paper-publication-integrity': { result: 'success', outputs: { ...valid, ...invalid } } },
      validatorRevision: 'b'.repeat(40),
      runId: '905',
      runUrl: 'https://github.com/basil-one/circle/actions/runs/905',
      date
    });
    assert.equal(pulse.successful, false);
    assert.match(pulse.markdown, /\| Paper: Publication Integrity \| Unknown \/ publication evidence unavailable \|/);
  }
});

test('publication defect survives missing or inconsistent provenance', async () => {
  const pulse = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    needs: { 'paper-publication-integrity': { result: 'failure', outputs: {
      status: 'failed', detail: 'Published PDFs differ; review before repair.', validator_sha: 'c'.repeat(40)
    } } },
    validatorRevision: 'b'.repeat(40),
    runId: '906',
    runUrl: 'https://github.com/basil-one/circle/actions/runs/906',
    date
  });
  assert.match(pulse.markdown, /\| Paper: Publication Integrity \| Failed \|/);
  assert.match(pulse.markdown, /Published PDFs differ; review before repair/);
  assert.match(pulse.markdown, /provenance is unavailable or inconsistent/);
});

test('confirmed publication defects survive contradictory success or incomplete outcomes', async () => {
  for (const [result, context] of [
    ['success', 'contradicts'], ['cancelled', 'incomplete'], ['skipped', 'incomplete'], [undefined, 'unavailable']
  ]) {
    const pulse = await buildOrchestratedPulse({
      apiUrl: 'https://api.github.com',
      repository: 'basil-one/circle',
      needs: {
        'paper-publication-integrity': {
          result,
          outputs: { status: 'failed', detail: 'Published PDFs differ; review before repair.' }
        }
      },
      runId: '904',
      runUrl: 'https://github.com/basil-one/circle/actions/runs/904',
      date
    });
    assert.equal(pulse.successful, false);
    assert.match(pulse.markdown, /\| Paper: Publication Integrity \| Failed \|/);
    assert.match(pulse.markdown, /Published PDFs differ; review before repair/);
    assert.ok(pulse.markdown.includes(context));
  }
});

test('publication mismatch retains actionable evidence independently of build failure', async () => {
  const mainSha = 'a'.repeat(40);
  const validatorSha = 'b'.repeat(40);
  const pulse = await buildOrchestratedPulse({
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    needs: {
      'paper-build-integrity': { result: 'failure' },
      'paper-publication-integrity': {
        result: 'failure',
        outputs: {
          status: 'failed',
          detail: 'Release v0.2.2 PDF assets differ; review before repair.',
          main_sha: mainSha,
          validator_sha: validatorSha
        }
      }
    },
    runId: '901',
    runUrl: 'https://github.com/basil-one/circle/actions/runs/901',
    date
  });
  assert.match(pulse.markdown, /\| Paper: Build Integrity \| Failed \|/);
  assert.match(pulse.markdown, /\| Paper: Publication Integrity \| Failed \|/);
  assert.match(pulse.markdown, /Release v0\.2\.2 PDF assets differ; review before repair/);
  assert.match(pulse.markdown, new RegExp(`Published main: ${mainSha}; validator revision: ${validatorSha}`));
});

test('cancelled paper validators retain confirmed report failures and cancellation', async () => {
  for (const [job, name] of [
    ['paper-content-integrity', 'Paper: Content Integrity'],
    ['paper-link-integrity', 'Paper: Link Integrity'],
    ['paper-rendering-integrity', 'Paper: Rendering Integrity']
  ]) {
    const needs = { [job]: { result: 'cancelled' } };
    let report = { status: 'failed', findings: ['Page 8: confirmed defect; correct and rerun'] };
    let available = true;
    const options = {
      apiUrl: 'https://api.github.com', repository: 'basil-one/circle', token: 'test-token',
      needs, runId: '895', runUrl: 'https://github.com/basil-one/circle/actions/runs/895', date,
      fetchImpl: async (url) => {
        const parsed = new URL(url);
        if (parsed.pathname.endsWith('/zip')) return { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
        return { ok: true, json: async () => ({
          artifacts: available ? [{ name: parsed.searchParams.get('name'), expired: false, id: 1 }] : []
        }) };
      },
      unzipImpl: () => JSON.stringify(report)
    };
    for (const outputs of [undefined, { status: '' }, { status: 'success' }]) {
      needs[job].outputs = outputs;
      const pulse = await buildOrchestratedPulse(options);
      assert.equal(pulse.successful, false);
      assert.ok(pulse.markdown.includes(`| ${name} | Failed |`));
      assert.match(pulse.markdown, /job was cancelled/);
      assert.match(pulse.markdown, /Page 8: confirmed defect; correct and rerun/);
      assert.match(pulse.markdown, /actions\/runs\/895\/artifacts\/1/);
    }

    needs[job].outputs = undefined;
    report = { status: 'failed', findings: [] };
    let pulse = await buildOrchestratedPulse(options);
    assert.ok(pulse.markdown.includes(`| ${name} | Incomplete / cancelled |`));
    assert.match(pulse.markdown, /report unavailable or invalid/);
    available = false;
    pulse = await buildOrchestratedPulse(options);
    assert.ok(pulse.markdown.includes(`| ${name} | Incomplete / cancelled |`));
    assert.match(pulse.markdown, /report unavailable or invalid/);

    needs[job] = { result: 'success', outputs: { status: 'success' } };
    pulse = await buildOrchestratedPulse(options);
    assert.equal(pulse.successful, false);
    assert.ok(pulse.markdown.includes(`| ${name} | Unknown /`));
    available = true;
    pulse = await buildOrchestratedPulse(options);
    assert.equal(pulse.successful, false);
    assert.ok(pulse.markdown.includes(`| ${name} | Unknown /`));
  }
});

test('paper rendering preserves defects and incomplete coverage without inferring a pass', async () => {
  const outputs = { status: 'failure' };
  let job = 'failure';
  let available = true;
  let report = { status: 'failed', findings: [
    'page 1: blank', 'page 2: blank', 'page 3: blank', 'page 4: blank',
    'Rendering inspection incomplete: page 5: renderer timed out'
  ] };
  const build = () => buildOrchestratedPulse({
    apiUrl: 'https://api.github.com', repository: 'basil-one/circle', token: 'test-token',
    needs: { 'paper-rendering-integrity': { result: job, outputs } },
    runId: '893', runUrl: 'https://github.com/basil-one/circle/actions/runs/893', date,
    fetchImpl: async (url) => {
      const parsed = new URL(url);
      if (parsed.pathname.endsWith('/zip')) return { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
      const name = parsed.searchParams.get('name');
      return { ok: true, json: async () => ({
        artifacts: name === 'paper-rendering-report' && available ? [{ name, expired: false, id: 2 }] : []
      }) };
    },
    unzipImpl: () => JSON.stringify(report)
  });
  let pulse = await build();
  assert.match(pulse.markdown, /\| Paper: Rendering Integrity \| Failed \|/);
  assert.match(pulse.markdown, /page 1: blank/);
  assert.match(pulse.markdown, /page 5: renderer timed out/);
  assert.match(pulse.markdown, /actions\/runs\/893\/artifacts\/2/);

  job = 'cancelled';
  outputs.status = '';
  pulse = await build();
  assert.match(pulse.markdown, /\| Paper: Rendering Integrity \| Failed \|/);
  assert.match(pulse.markdown, /job was cancelled/);
  assert.match(pulse.markdown, /page 5: renderer timed out/);

  job = 'failure';
  outputs.status = 'incomplete';
  report = { status: 'incomplete', findings: ['page 3: renderer timed out'] };
  pulse = await build();
  assert.match(pulse.markdown, /Incomplete \/ rendering verification/);
  assert.match(pulse.markdown, /page 3: renderer timed out/);

  job = 'success';
  outputs.status = 'success';
  available = false;
  pulse = await build();
  assert.match(pulse.markdown, /Unknown \/ rendering evidence unavailable/);
  available = true;
  for (const invalid of [
    { status: 'passed', findings: ['page 1: blank'] },
    { status: 'failed', findings: ['page 1: blank'] },
    { status: 'passed', findings: 'invalid' }
  ]) {
    report = invalid;
    pulse = await build();
    assert.match(pulse.markdown, /Unknown \/ rendering evidence unavailable/);
  }
  outputs.status = 'advisory';
  report = { status: 'advisory', findings: ['unexpected advisory'] };
  pulse = await build();
  assert.match(pulse.markdown, /\| Paper: Rendering Integrity \| Unknown outcome \|/);
  assert.equal(pulse.successful, false);

  job = 'failure';
  outputs.status = 'failure';
  available = false;
  pulse = await build();
  assert.match(pulse.markdown, /\| Paper: Rendering Integrity \| Failed \|/);
  assert.match(pulse.markdown, /paper-rendering-report unavailable or invalid/);
});

test('paper build, content failure, and missing evidence remain distinct', async () => {
  const needs = {
    'paper-build-integrity': { result: 'success' },
    'paper-content-integrity': { result: 'failure', outputs: { status: 'failure' } }
  };
  const options = {
    apiUrl: 'https://api.github.com',
    repository: 'basil-one/circle',
    token: 'test-token',
    needs,
    runId: '893',
    runUrl: 'https://github.com/basil-one/circle/actions/runs/893',
    date,
    fetchImpl: async (url) => {
      const parsed = new URL(url);
      if (parsed.pathname.endsWith('/zip')) return { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
      return {
        ok: true,
        json: async () => ({ artifacts: [{ name: parsed.searchParams.get('name'), expired: false, id: 1 }] })
      };
    },
    unzipImpl: () => JSON.stringify({ status: 'failed', findings: ['_moves/establish.md page 8: missing text; regenerate'] })
  };
  let pulse = await buildOrchestratedPulse(options);
  assert.match(pulse.markdown, /\| Paper: Build Integrity \| Passed \|/);
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Failed \|/);
  assert.match(pulse.markdown, /_moves\/establish\.md page 8: missing text; regenerate/);
  const noReport = await buildOrchestratedPulse({
    ...options,
    fetchImpl: async (url) => new URL(url).searchParams.get('name') === 'paper-content-report'
      ? { ok: true, json: async () => ({ artifacts: [] }) }
      : options.fetchImpl(url)
  });

  await test('paper link evidence preserves defects, uncertainty, and independent outcomes', async () => {
    const needs = {
      'paper-build-integrity': { result: 'failure' },
      'paper-link-integrity': { result: 'success', outputs: { status: 'advisory' } }
    };
    let report = { status: 'advisory', findings: ['Page 8: https://example.test/ returned HTTP 429; retry'] };
    let available = true;
    const options = {
      apiUrl: 'https://api.github.com', repository: 'basil-one/circle', token: 'test-token',
      needs, runId: '894', runUrl: 'https://github.com/basil-one/circle/actions/runs/894', date,
      fetchImpl: async (url) => {
        const parsed = new URL(url);
        if (parsed.pathname.endsWith('/zip')) return { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
        return { ok: true, json: async () => ({
          artifacts: available ? [{ name: parsed.searchParams.get('name'), expired: false, id: 1 }] : []
        }) };
      },
      unzipImpl: () => JSON.stringify(report)
    };
    let pulse = await buildOrchestratedPulse(options);
    assert.match(pulse.markdown, /\| Paper: Build Integrity \| Failed \|/);
    assert.match(pulse.markdown, /\| Paper: Link Integrity \| Advisory \|/);
    assert.match(pulse.markdown, /Page 8: https:\/\/example\.test\/ returned HTTP 429; retry/);

    needs['paper-link-integrity'] = { result: 'failure', outputs: { status: 'failure' } };
    report = { status: 'failed', findings: ['Page 4: broken destination', 'Page 8: HTTP 429; retry'] };
    pulse = await buildOrchestratedPulse(options);
    assert.match(pulse.markdown, /\| Paper: Link Integrity \| Failed \|/);
    assert.match(pulse.markdown, /Page 4: broken destination; Page 8: HTTP 429; retry/);
    available = false;
    pulse = await buildOrchestratedPulse(options);
    assert.match(pulse.markdown, /\| Paper: Link Integrity \| Failed \|/);
    assert.match(pulse.markdown, /paper-link-report unavailable or invalid/);

    available = true;
    needs['paper-link-integrity'] = { result: 'failure', outputs: { status: 'incomplete' } };
    report = { status: 'incomplete', findings: ['PDF annotations unavailable; rerun'] };
    pulse = await buildOrchestratedPulse(options);
    assert.match(pulse.markdown, /\| Paper: Link Integrity \| Incomplete \/ link verification \|/);
    assert.match(pulse.markdown, /PDF annotations unavailable; rerun/);
    report = { status: 'failed', findings: ['Page 4: broken destination'] };
    pulse = await buildOrchestratedPulse(options);
    assert.match(pulse.markdown, /\| Paper: Link Integrity \| Failed \|/);
    assert.match(pulse.markdown, /failed report establishes a defect/);

    needs['paper-link-integrity'] = { result: 'success', outputs: { status: 'success' } };
    pulse = await buildOrchestratedPulse(options);
    assert.match(pulse.markdown, /\| Paper: Link Integrity \| Unknown \/ link evidence unavailable \|/);
    report = { status: 'passed', findings: ['contradiction'] };
    pulse = await buildOrchestratedPulse(options);
    assert.match(pulse.markdown, /\| Paper: Link Integrity \| Unknown \/ link evidence unavailable \|/);
    available = false;
    pulse = await buildOrchestratedPulse(options);
    assert.match(pulse.markdown, /\| Paper: Link Integrity \| Unknown \/ link evidence unavailable \|/);
    needs['paper-link-integrity'] = { result: 'success', outputs: { status: 'failure' } };
    pulse = await buildOrchestratedPulse(options);
    assert.match(pulse.markdown, /\| Paper: Link Integrity \| Unknown outcome \|/);
    needs['paper-link-integrity'] = { result: 'failure', outputs: { status: 'advisory' } };
    pulse = await buildOrchestratedPulse(options);
    assert.match(pulse.markdown, /\| Paper: Link Integrity \| Incomplete \/ link verification \|/);
  });
  assert.match(noReport.markdown, /\| Paper: Content Integrity \| Failed \|/);
  assert.match(noReport.markdown, /paper-content-report unavailable or invalid/);

  needs['paper-content-integrity'].outputs.status = 'incomplete';
  pulse = await buildOrchestratedPulse({
    ...options,
    unzipImpl: () => JSON.stringify({ status: 'incomplete', findings: ['Citation conversion unavailable; inspect source'] })
  });
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Incomplete \/ content verification \|/);
  assert.match(pulse.markdown, /Citation conversion unavailable; inspect source/);

  needs['paper-content-integrity'].outputs.status = 'failure';
  needs['paper-content-integrity'].result = 'cancelled';
  pulse = await buildOrchestratedPulse(options);
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Failed \|/);
  assert.match(pulse.markdown, /workflow was also cancelled/);
  assert.match(pulse.markdown, /_moves\/establish\.md page 8/);

  needs['paper-content-integrity'].result = 'failure';
  needs['paper-content-integrity'].outputs.status = '';
  pulse = await buildOrchestratedPulse(options);
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Failed \|/);
  assert.match(pulse.markdown, /failed report establishes a defect/);
  const missingStatusAndReport = await buildOrchestratedPulse({
    ...options,
    fetchImpl: async (url) => new URL(url).searchParams.get('name') === 'paper-content-report'
      ? { ok: true, json: async () => ({ artifacts: [] }) }
      : options.fetchImpl(url)
  });
  assert.match(missingStatusAndReport.markdown, /\| Paper: Content Integrity \| Incomplete \/ content verification \|/);

  needs['paper-content-integrity'].outputs.status = 'success';
  pulse = await buildOrchestratedPulse(options);
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Failed \|/);
  assert.match(pulse.markdown, /contradictory job\/validator outcome/);

  needs['paper-content-integrity'].outputs.status = 'incomplete';
  pulse = await buildOrchestratedPulse({
    ...options,
    unzipImpl: () => JSON.stringify({ status: 'incomplete', findings: ['PDF extraction incomplete'] })
  });
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Incomplete \/ content verification \|/);

  needs['paper-content-integrity'].result = 'success';
  needs['paper-content-integrity'].outputs.status = 'failure';
  pulse = await buildOrchestratedPulse(options);
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Failed \|/);
  assert.match(pulse.markdown, /contradictory job\/validator outcome/);
  const contradictoryWithoutReport = await buildOrchestratedPulse({
    ...options,
    unzipImpl: () => JSON.stringify({ status: 'failed', findings: [] })
  });
  assert.match(contradictoryWithoutReport.markdown, /\| Paper: Content Integrity \| Unknown outcome \|/);

  needs['paper-content-integrity'].result = 'cancelled';
  needs['paper-content-integrity'].outputs.status = 'success';
  pulse = await buildOrchestratedPulse(options);
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Failed \|/);
  assert.match(pulse.markdown, /job was cancelled/);
  assert.match(pulse.markdown, /_moves\/establish\.md page 8/);

  needs['paper-content-integrity'].result = 'success';
  needs['paper-content-integrity'].outputs.status = 'success';
  pulse = await buildOrchestratedPulse(options);
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Unknown \/ content evidence unavailable \|/);

  needs['paper-build-integrity'].result = 'failure';
  pulse = await buildOrchestratedPulse({
    ...options,
    unzipImpl: () => JSON.stringify({ status: 'passed', findings: [] })
  });
  assert.match(pulse.markdown, /\| Paper: Build Integrity \| Failed \|/);
  assert.match(pulse.markdown, /\| Paper: Content Integrity \| Passed \|/);
});
