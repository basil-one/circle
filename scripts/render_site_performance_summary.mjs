import { readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import {
  performanceRoutes,
  performanceRunCount
} from '../tests/browser/site-routes.js';

const metrics = [
  ['first-contentful-paint', 'FCP'],
  ['largest-contentful-paint', 'LCP'],
  ['speed-index', 'SI'],
  ['total-blocking-time', 'TBT'],
  ['cumulative-layout-shift', 'CLS']
];
const formFactors = ['mobile', 'desktop'];

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return undefined;
  }
}

function trimSentenceEnd(value) {
  return value?.replace(/[.!?]+$/, '') ?? '';
}

function routeLabel(route) {
  if (route === '/') return 'Home';
  if (route === '/moves/') return 'Moves';
  if (route === '/lenses/') return 'Lenses';
  if (route === '/method/') return 'Method';
  return route;
}

function formatValue(metric, value) {
  if (!Number.isFinite(value)) return 'unavailable';
  if (metric === 'cumulative-layout-shift') return value.toFixed(3);
  return value >= 1_000 ? `${(value / 1_000).toFixed(2)} s` : `${Math.round(value)} ms`;
}

function formatDelta(metric, current, baseline) {
  if (!Number.isFinite(current) || !Number.isFinite(baseline)) return '—';
  const difference = current - baseline;
  const sign = difference > 0 ? '+' : '';
  const absolute = metric === 'cumulative-layout-shift'
    ? `${sign}${difference.toFixed(3)}`
    : Math.abs(difference) >= 1_000
      ? `${sign}${(difference / 1_000).toFixed(2)} s`
      : `${sign}${Math.round(difference)} ms`;
  if (baseline === 0) return `${absolute} (percentage n/a)`;
  const percent = (difference / Math.abs(baseline)) * 100;
  return `${absolute} (${percent > 0 ? '+' : ''}${percent.toFixed(1)}%)`;
}

function statusOf(inputs, key) {
  return inputs.steps?.[key] || 'not run';
}

function diagnosticStatus(inputs) {
  const status = statusOf(inputs, 'diagnostic');
  return status === 'success' ? 'passed' : status;
}

function failedStepNames(inputs) {
  const names = {
    tests: 'performance tests',
    classify: 'changed-file classification',
    ruby: 'Ruby setup',
    node: 'Node setup',
    build: 'site build',
    dependencies: 'browser test dependency installation',
    chromium: 'Chromium installation',
    history: 'daily-main history lookup',
    measure: 'Lighthouse measurement',
    comparison: 'baseline comparison',
    diagnostic: 'missing-route diagnostic',
    reports: 'raw report upload',
    summary: 'summary artifact upload'
  };
  return Object.entries(names)
    .filter(([key]) => statusOf(inputs, key) === 'failure')
    .map(([, name]) => name);
}

function baselineLines(comparison) {
  if (!comparison) {
    return [
      '- **Fixed reference:** unavailable; comparison did not produce a readable result.',
      '- **Rolling daily-main baseline:** unavailable; comparison did not produce a readable result.',
      '- No comparison/deltas available yet.'
    ];
  }
  const fixed = comparison.fixedReference;
  const history = comparison.history ?? {};
  const fixedStatus = fixed?.available
    ? `[available](${fixed.runUrl})`
    : fixed?.reason?.includes('different tool')
      ? 'not comparable; tool or runner environment differs'
      : 'pending reviewed successful `main` anchor';
  let rollingStatus;
  if (history.status === 'available' && history.availableDailySamples >= history.requiredDailySamples) {
    rollingStatus = `${history.availableDailySamples}/${history.requiredDailySamples} compatible successful daily-main runs available`;
  } else if (history.status === 'unavailable') {
    rollingStatus = `unavailable${history.reason ? ` (${trimSentenceEnd(history.reason)})` : ''}`;
  } else {
    rollingStatus = `collecting (${history.availableDailySamples ?? 0}/${history.requiredDailySamples ?? 28})`;
  }
  const lines = [
    `- **Fixed reference:** ${fixedStatus}.`,
    `- **Rolling daily-main baseline:** ${rollingStatus}.`
  ];
  if (comparison.deltasAvailable) {
    lines.push('- Deltas are descriptive lab comparisons only; no threshold or regression verdict is applied.');
  } else if (history.status === 'unavailable') {
    lines.push('- No comparison/deltas available; inspect the history lookup details below and rerun when access is restored.');
  } else {
    lines.push('- No comparison/deltas available yet; after the first successful `main` measurement, review and version it as the fixed anchor.');
  }
  return lines;
}

function representativeTable(summary) {
  const results = new Map((summary?.results ?? []).map((result) => [
    `${result.route}\u0000${result.formFactor}`,
    result
  ]));
  const routes = [...new Set((summary?.results ?? []).map((result) => result.route))];
  if (!routes.length) return [];
  return [
    '',
    '| Page | Mobile LCP | Desktop LCP | Mobile TBT |',
    '| --- | ---: | ---: | ---: |',
    ...routes.map((route) => {
      const mobile = results.get(`${route}\u0000mobile`);
      const desktop = results.get(`${route}\u0000desktop`);
      return `| ${routeLabel(route)} | ${formatValue('largest-contentful-paint', mobile?.values?.['largest-contentful-paint'])} | ${formatValue('largest-contentful-paint', desktop?.values?.['largest-contentful-paint'])} | ${formatValue('total-blocking-time', mobile?.values?.['total-blocking-time'])} |`;
    })
  ];
}

function deltaTable(comparison) {
  if (!comparison?.deltasAvailable || !comparison.comparisons?.length) return [];
  const rows = comparison.comparisons.map((result) => {
    const fixed = result.fixedReference;
    const rolling = result.rollingBaseline;
    const lcp = result.values?.['largest-contentful-paint'];
    const tbt = result.values?.['total-blocking-time'];
    return `| ${routeLabel(result.route)} | ${result.formFactor} | ${formatDelta('largest-contentful-paint', lcp, fixed?.['largest-contentful-paint'])} | ${formatDelta('largest-contentful-paint', lcp, rolling?.['largest-contentful-paint'])} | ${formatDelta('total-blocking-time', tbt, fixed?.['total-blocking-time'])} | ${formatDelta('total-blocking-time', tbt, rolling?.['total-blocking-time'])} |`;
  });
  return [
    '',
    '| Page | Profile | Fixed LCP Δ | Rolling LCP Δ | Fixed TBT Δ | Rolling TBT Δ |',
    '| --- | --- | ---: | ---: | ---: | ---: |',
    ...rows
  ];
}

function technicalDetails(summary, comparison, inputs) {
  const lines = [
    '<details>',
    '<summary>Technical details</summary>',
    '',
    '### Reported route/profile metrics',
    ''
  ];
  if (summary?.results?.length) {
    if (summary.complete !== true) lines.push('Partial measurement data only; the full route/profile set was not completed.', '');
    lines.push(
      '| Page | Profile | FCP | LCP | Speed Index | TBT | CLS |',
      '| --- | --- | ---: | ---: | ---: | ---: | ---: |'
    );
    for (const result of summary.results) {
      lines.push(`| ${routeLabel(result.route)} | ${result.formFactor} | ${formatValue('first-contentful-paint', result.values?.['first-contentful-paint'])} | ${formatValue('largest-contentful-paint', result.values?.['largest-contentful-paint'])} | ${formatValue('speed-index', result.values?.['speed-index'])} | ${formatValue('total-blocking-time', result.values?.['total-blocking-time'])} | ${formatValue('cumulative-layout-shift', result.values?.['cumulative-layout-shift'])} |`);
    }
  } else {
    lines.push('No complete route/profile metric table is available.');
  }
  if (summary) {
    const env = summary.environment ?? {};
    const conditions = summary.conditions ?? {};
    lines.push(
      '',
      '### Environment and conditions',
      '',
      `- Lighthouse ${env.lighthouse ?? 'unknown'}; ${env.chrome ?? 'Chrome version unknown'}; Node ${env.node ?? 'unknown'}; ${env.ruby ?? 'Ruby version unknown'}; ${env.jekyll ?? 'Jekyll version unknown'}; runner ${env.runner ?? 'unknown'}.`,
      `- ${conditions.site ?? 'Site build conditions unavailable'}; mobile: ${conditions.mobile ?? 'unknown'}; desktop: ${conditions.desktop ?? 'unknown'}; Analytics collection blocked: ${conditions.analyticsCollectionBlocked === true ? 'yes' : 'unknown'}; lab-only: ${conditions.labOnly === true ? 'yes' : 'unknown'}.`,
      `- Captured: ${summary.capturedAt ?? 'unknown'}; source SHA: \`${summary.source?.sha ?? 'unknown'}\`; source run: ${summary.source?.runUrl ?? 'not available'}.`,
      `- Timing: ${Number.isFinite(summary.timing?.measurementSeconds) ? `${summary.timing.measurementSeconds.toFixed(1)} seconds for measurement` : 'measurement duration unavailable'}; total Actions duration is shown in the run overview.`
    );
    if (summary.firstPartyAssets?.length) {
      lines.push('', '### First-party assets', '');
      for (const asset of summary.firstPartyAssets) {
        lines.push(`- \`${asset.path}\` — ${Number.isFinite(asset.bytes) ? `${(asset.bytes / 1024).toFixed(1)} KiB` : 'size unavailable'}.`);
      }
    } else {
      lines.push('', '- First-party asset inventory is unavailable.');
    }
    lines.push('', '### External scripts', '');
    if (summary.externalScripts?.length) {
      for (const script of summary.externalScripts) {
        const transfer = Number.isFinite(script.medianTransferBytes)
          ? `; median transfer ${(script.medianTransferBytes / 1024).toFixed(1)} KiB`
          : '';
        lines.push(`- \`${script.source}\` — ${script.successful}/${script.samples} requests succeeded${transfer}.`);
      }
    } else {
      lines.push('- No external scripts were observed in the generated pages.');
    }
  }
  if (comparison) {
    lines.push(
      '',
      '### Baseline provenance and diagnostics',
      '',
      `- Fixed reference: ${comparison.fixedReference?.available ? comparison.fixedReference.runUrl : trimSentenceEnd(comparison.fixedReference?.reason) || 'unavailable'}.`,
      `- Rolling history: ${comparison.history?.status ?? 'unavailable'}; ${comparison.history?.availableDailySamples ?? 0}/${comparison.history?.requiredDailySamples ?? 28} compatible summaries; ${trimSentenceEnd(comparison.history?.reason) || 'no additional history diagnostic'}.`
    );
    for (const item of (comparison.history?.skipped ?? []).slice(0, 5)) {
      lines.push(`- History lookup detail: ${item}.`);
    }
    lines.push('', '- Evidence sources: generated `_site` pages, Lighthouse JSON for measurements/network observations/tool versions, and the Actions workflow run for step outcomes and diagnostic status.');
    for (const source of (comparison.history?.provenance ?? []).slice(0, 28)) {
      lines.push(`- Rolling source: [run ${source.runId}](${source.runUrl}) — ${source.capturedAt}, commit \`${source.sha}\`.`);
    }
    if (comparison.conditions?.length) {
      lines.push(`- Conditions differing from available baseline(s): ${comparison.conditions.join('; ')}.`);
    }
  }
  const diagnostic = diagnosticStatus(inputs);
  lines.push('', `- Controlled missing-route diagnostic: ${diagnostic}.`);
  const failures = failedStepNames(inputs);
  if (failures.length) lines.push(`- Failed operation(s): ${failures.join(', ')}.`);
  lines.push('', '</details>');
  return lines;
}

function renderPerformanceSummary(inputs) {
  const summary = inputs.summary;
  const comparison = inputs.comparison;
  const skipped = isClassifiedSkip(inputs);
  const measure = statusOf(inputs, 'measure');
  const job = inputs.jobStatus ?? 'unknown';
  const failures = failedStepNames(inputs);
  const lines = [
    '# Site: Performance Integrity',
    ''
  ];
  if (skipped) {
    lines.push(
      '**Intentionally skipped — existing collection text only**',
      '',
      '- Informational lab measurement; this is not a performance pass, field Core Web Vitals result, or regression gate.',
      '- Classification evidence: pull-request prose-only classifier **passed**; Lighthouse steps were intentionally skipped.',
      `- Reason: ${inputs.skip.reason ?? 'all changed files are existing move/lens text-only Markdown edits'}.`,
      `- Changed paths (${inputs.skip.paths?.length ?? 0}): ${inputs.skip.paths?.length ? inputs.skip.paths.map((path) => `\`${path}\``).join(', ') : 'none reported'}.`,
      '- Lighthouse samples collected: **0**.',
      '- No metrics, baseline comparison, or artifacts were produced.',
      '- Next step: the next daily `main` run performs the full post-merge measurement.'
    );
    return lines.join('\n');
  }

  const complete =
    measure === 'success' && completeMeasurementEvidence(summary);
  const measurementFailed = measure === 'failure';
  const statusText = complete
    ? `**Measurement completed — 24/24 Lighthouse samples${job === 'failure' ? '; a later workflow operation failed' : ''}**`
    : measurementFailed
      ? '**Measurement failed or incomplete**'
      : '**Measurement failed or incomplete — no complete measurement was produced**';
  lines.push(
    statusText,
    '',
    complete
    ? '- Measurements were collected using controlled Lighthouse tests, not actual visitors. Comparisons are informational; no automatic regression thresholds are enforced.'
    : '- No complete measurement is reported. Any partial output is not treated as complete; comparisons are informational and no automatic regression thresholds are enforced.',
    `- Run: \`${inputs.eventName ?? 'unknown'}\` on ref \`${inputs.ref ?? 'unknown'}\`; job status: ${job}.`
  );
  if (complete) {
    lines.push(`- Coverage: ${summary.coverage.routeCount} generated routes × mobile/desktop × ${summary.coverage.runCount} runs.`);
    if (Number.isFinite(summary.timing?.measurementSeconds)) {
      lines.push(`- Measurement runtime: ${summary.timing.measurementSeconds.toFixed(1)} seconds; total job duration is available in the Actions run overview.`);
    }
  } else {
    lines.push('- Complete sample count: **unavailable**; no zero or partial count is inferred.');
  }
  if (failures.length) {
    lines.push(`- **Investigate:** ${failures.join(', ')} failed; [open this workflow run](${inputs.runUrl ?? ''}) for logs.`);
  } else if (!complete) {
    lines.push(`- **Investigate:** inspect the relevant Actions step in [this workflow run](${inputs.runUrl ?? ''}); the measurement is not reported as successful.`);
  }

  lines.push('', '## Findings', '');
  lines.push(...baselineLines(comparison));
  if (comparison?.deltasAvailable) {
    lines.push(...deltaTable(comparison));
  } else if (complete) {
    lines.push(...representativeTable(summary));
  }

  lines.push('', '## Evidence', '');
  lines.push(`- Controlled missing-route diagnostic: **${diagnosticStatus(inputs)}**.`);
  if (inputs.reports?.outcome === 'success' && inputs.reports.url) {
    lines.push(`- [Raw Lighthouse reports](${inputs.reports.url}) — 14-day retention.`);
  } else {
    lines.push(`- Raw Lighthouse reports unavailable (${inputs.reports?.outcome ?? 'not uploaded'}); inspect the upload step in [the workflow run](${inputs.runUrl ?? ''}).`);
  }
  if (inputs.machineArtifact?.outcome === 'success' && inputs.machineArtifact.url) {
    const artifactLabel = inputs.machineArtifact.hasComparison
      ? 'Machine-readable summary and comparison'
      : 'Machine-readable performance summary (comparison unavailable)';
    lines.push(`- [${artifactLabel}](${inputs.machineArtifact.url}) — 90-day retention.`);
  } else {
    lines.push(`- Machine-readable summary/comparison artifact unavailable (${inputs.machineArtifact?.outcome ?? 'not uploaded'}); inspect the upload step in [the workflow run](${inputs.runUrl ?? ''}).`);
  }

  lines.push('', ...technicalDetails(summary, comparison, inputs));
  return lines.join('\n');
}

function completeMeasurementEvidence(summary) {
  const expectedProfiles = new Set(
    performanceRoutes.flatMap((route) =>
      formFactors.map((formFactor) => `${route}\u0000${formFactor}`)
    )
  );
  const results = summary?.results;
  if (
    summary?.complete !== true ||
    summary.coverage?.routeCount !== performanceRoutes.length ||
    summary.coverage?.runCount !== performanceRunCount ||
    summary.coverage?.measurementCount !==
      performanceRoutes.length * formFactors.length * performanceRunCount ||
    !Array.isArray(results) ||
    results.length !== expectedProfiles.size
  ) {
    return false;
  }

  const seenProfiles = new Set();
  for (const result of results) {
    const key = `${result?.route}\u0000${result?.formFactor}`;
    if (!expectedProfiles.has(key) || seenProfiles.has(key)) return false;
    seenProfiles.add(key);
    if (metrics.some(([metric]) => !Number.isFinite(result.values?.[metric]))) {
      return false;
    }
  }
  return seenProfiles.size === expectedProfiles.size;
}

function isClassifiedSkip(inputs) {
  const paths = inputs.skip?.paths;
  return inputs.skip?.measurement === true &&
    statusOf(inputs, 'tests') === 'success' &&
    statusOf(inputs, 'classify') === 'success' &&
    inputs.skip.reason === 'all changed files are existing move/lens prose-only Markdown edits' &&
    Array.isArray(paths) &&
    paths.length > 0 &&
    paths.every(
      (path) =>
        typeof path === 'string' &&
        /^_(?:moves|lenses)\/[^/]+\.md$/.test(path)
    ) &&
    statusOf(inputs, 'measure') === 'skipped';
}

function performanceMeasurementExitCode(inputs) {
  if (statusOf(inputs, 'tests') !== 'success') return 1;
  if (inputs.skip?.measurement === true) {
    return isClassifiedSkip(inputs) ? 0 : 1;
  }
  return statusOf(inputs, 'measure') === 'success' &&
    completeMeasurementEvidence(inputs.summary)
    ? 0
    : 1;
}

function main() {
  const reportDirectory = resolve(process.env.PERFORMANCE_REPORT_DIR ?? 'performance-reports');
  const summary = readJson(join(reportDirectory, 'performance-summary.json'));
  const comparison = readJson(join(reportDirectory, 'performance-comparison.json'));
  let changedPaths = [];
  try {
    changedPaths = JSON.parse(process.env.CHANGED_PATHS ?? '[]');
  } catch {
    changedPaths = [];
  }
  const inputs = {
    summary,
    comparison,
    jobStatus: process.env.JOB_STATUS,
    eventName: process.env.TRIGGER_NAME,
    ref: process.env.TRIGGER_REF,
    runUrl: process.env.RUN_URL,
    skip: {
      measurement: process.env.SKIP_MEASUREMENT === 'true',
      reason: process.env.SKIP_REASON,
      paths: changedPaths
    },
    reports: { outcome: process.env.REPORTS_OUTCOME, url: process.env.REPORTS_URL },
    machineArtifact: {
      outcome: process.env.SUMMARY_OUTCOME,
      url: process.env.SUMMARY_URL,
      hasComparison: Boolean(comparison)
    },
    steps: Object.fromEntries([
      'tests', 'classify', 'ruby', 'node', 'build', 'dependencies', 'chromium',
      'history', 'measure', 'comparison', 'diagnostic', 'reports', 'summary'
    ].map((key) => [key, process.env[`STEP_${key.toUpperCase()}`]]))
  };
  const markdown = `${renderPerformanceSummary(inputs)}\n`;
  if (process.env.GITHUB_STEP_SUMMARY) {
    writeFileSync(process.env.GITHUB_STEP_SUMMARY, markdown);
  }
  process.stdout.write(markdown);
  process.exitCode = performanceMeasurementExitCode(inputs);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main();
}

export {
  formatDelta,
  formatValue,
  performanceMeasurementExitCode,
  renderPerformanceSummary,
  routeLabel
};
