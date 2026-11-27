import { execFileSync } from 'node:child_process';
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import {
  assessPerformance,
  finalizePerformanceAssessment
} from './performance_policy.mjs';

const apiVersion = '2022-11-28';
const historyLimit = 28;
const runSearchLimit = 100;
const metrics = [
  ['first-contentful-paint', 'FCP'],
  ['largest-contentful-paint', 'LCP'],
  ['speed-index', 'SI'],
  ['total-blocking-time', 'TBT'],
  ['cumulative-layout-shift', 'CLS']
];

function median(values) {
  const ordered = [...values].sort((left, right) => left - right);
  if (!ordered.length) return undefined;
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
}

function formatValue(metric, value) {
  return metric === 'cumulative-layout-shift'
    ? value.toFixed(3)
    : `${Math.round(value)} ms`;
}

function formatDelta(metric, current, baseline) {
  if (!Number.isFinite(baseline)) return '—';
  const difference = current - baseline;
  const sign = difference > 0 ? '+' : '';
  const value = `${sign}${formatValue(metric, difference)}`;
  if (baseline === 0) return `${value} (percentage n/a: baseline zero)`;
  const percentage = (difference / Math.abs(baseline)) * 100;
  const percentageSign = percentage > 0 ? '+' : '';
  return `${value} (${percentageSign}${percentage.toFixed(1)}%)`;
}

function getResults(summary) {
  return new Map(
    (summary?.results ?? []).map((result) => [
      `${result.route}\u0000${result.formFactor}`,
      result
    ])
  );
}

function isCompleteSummary(summary) {
  if (summary?.schemaVersion !== 1 || summary.complete !== true || !Array.isArray(summary.results)) {
    return false;
  }
  const slots = new Set(summary.results.map((result) => `${result.route}\u0000${result.formFactor}`));
  const routes = new Set(summary.results.map((result) => result.route));
  return summary.coverage?.routeCount === 4 &&
    summary.coverage?.runCount === 3 &&
    summary.coverage?.measurementCount === 24 &&
    summary.results.length === 8 &&
    slots.size === 8 &&
    routes.size === 4 &&
    summary.results.every((result) =>
    result.route &&
    ['mobile', 'desktop'].includes(result.formFactor) &&
    metrics.every(([key]) => Number.isFinite(result.values?.[key]))
  );
}

function referenceIsReady(reference) {
  return isCompleteSummary(reference) &&
    reference.source?.branch === 'main' &&
    ['schedule', 'workflow_dispatch'].includes(reference.source?.eventName) &&
    Boolean(reference.source?.runUrl);
}

function runtimeMajorMinor(key, value) {
  if (typeof value !== 'string') return undefined;
  const version = key === 'ruby'
    ? value.match(/^(?:ruby\s+)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\s+\([^)]*\)(?:\s+\[[^\]]+\])?)?$/)
    : value.match(/^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/);
  return version ? `${version[1]}.${version[2]}` : undefined;
}

function runtimeCompatibility(current, baseline, key) {
  const currentVersion = runtimeMajorMinor(key, current.environment?.[key]);
  const baselineVersion = runtimeMajorMinor(key, baseline.environment?.[key]);
  if (!currentVersion || !baselineVersion) return 'unknown';
  return currentVersion === baselineVersion ? 'compatible' : 'different';
}

function sameMeasurementEnvironment(current, baseline) {
  const exactMatches = ['runner', 'lighthouse', 'chrome', 'jekyll'].every((key) =>
    typeof current.environment?.[key] === 'string' &&
    current.environment[key].length > 0 &&
    current.environment[key] === baseline.environment?.[key]
  );
  return exactMatches &&
    ['ruby', 'node'].every((key) => runtimeCompatibility(current, baseline, key) === 'compatible');
}

async function githubRequest(path, token, fetchImpl = fetch) {
  const response = await fetchImpl(`${process.env.GITHUB_API_URL ?? 'https://api.github.com'}${path}`, {
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: 'Bearer ' + token,
      'X-GitHub-Api-Version': apiVersion
    },
    signal: AbortSignal.timeout(10_000)
  });
  if (!response.ok) {
    throw new Error(`GitHub Actions API returned HTTP ${response.status} for ${path}.`);
  }
  return response;
}

function isDailyPerformanceEvidence(summary, run, workflowFile, performanceJobSucceeded = false) {
  if (
    run?.status !== 'completed' ||
    run.event !== 'schedule' ||
    run.head_branch !== 'main' ||
    summary?.source?.eventName !== 'schedule' ||
    summary.source.branch !== 'main' ||
    String(summary.source.runId) !== String(run.id)
  ) {
    return false;
  }
  if (workflowFile === 'site-performance-integrity.yml') {
    return run.conclusion === 'success';
  }
  return workflowFile === 'quality-pulse.yml' && performanceJobSucceeded;
}

async function loadDailyHistory({
  fetchImpl = fetch,
  unzipImpl = execFileSync,
  token = process.env.GITHUB_TOKEN,
  repository = process.env.GITHUB_REPOSITORY
} = {}) {
  if (!token || !repository) {
    return {
      status: 'unavailable',
      reason: 'GitHub Actions read access is unavailable in this run.',
      summaries: [],
      skipped: []
    };
  }

  const repoPath = repository.split('/').map(encodeURIComponent).join('/');
  const summaries = [];
  const skipped = [];

  try {
    const sources = [
      { workflowFile: 'site-performance-integrity.yml' },
      { workflowFile: 'quality-pulse.yml' }
    ];
    const candidates = [];
    for (const source of sources) {
      const workflowPath = encodeURIComponent(source.workflowFile);
      const runsPath = `/repos/${repoPath}/actions/workflows/${workflowPath}/runs?branch=main&event=schedule&status=completed&per_page=${runSearchLimit}`;
      const runsResponse = await githubRequest(runsPath, token, fetchImpl);
      const runsData = await runsResponse.json();
      candidates.push(...(runsData.workflow_runs ?? [])
        .filter((run) =>
          run.status === 'completed' &&
          run.event === 'schedule' &&
          run.head_branch === 'main' &&
          (source.workflowFile !== 'site-performance-integrity.yml' || run.conclusion === 'success')
        )
        .map((run) => ({ run, ...source })));
    }
    candidates.sort((left, right) =>
      Date.parse(right.run.created_at) - Date.parse(left.run.created_at)
    );

    for (const { run, workflowFile } of candidates) {
      try {
        let performanceJobSucceeded = workflowFile === 'site-performance-integrity.yml';
        if (workflowFile === 'quality-pulse.yml') {
          const jobsResponse = await githubRequest(
            `/repos/${repoPath}/actions/runs/${run.id}/jobs?per_page=100&page=1`,
            token,
            fetchImpl
          );
          const jobsData = await jobsResponse.json();
          if (
            !Array.isArray(jobsData.jobs) ||
            !Number.isInteger(jobsData.total_count) ||
            jobsData.total_count > jobsData.jobs.length
          ) {
            skipped.push(`run ${run.id}: performance job evidence was incomplete`);
            continue;
          }
          performanceJobSucceeded = jobsData.jobs.some((job) =>
            typeof job.name === 'string' &&
            job.name.split('/').at(-1).trim() === 'Site: Performance Integrity' &&
            job.status === 'completed' &&
            job.conclusion === 'success'
          );
          if (!performanceJobSucceeded) {
            skipped.push(`run ${run.id}: Site: Performance Integrity job did not succeed`);
            continue;
          }
        }
        const artifactsPath = `/repos/${repoPath}/actions/runs/${run.id}/artifacts?per_page=100`;
        const artifactsResponse = await githubRequest(artifactsPath, token, fetchImpl);
        const artifactData = await artifactsResponse.json();
        const artifact = artifactData.artifacts?.find((item) =>
          item.name === 'performance-summary' &&
          !item.expired &&
          item.size_in_bytes <= 1_000_000
        );
        if (!artifact) {
          skipped.push(`run ${run.id}: summary artifact unavailable or expired`);
          continue;
        }

        const archiveResponse = await githubRequest(
          `/repos/${repoPath}/actions/artifacts/${artifact.id}/zip`,
          token,
          fetchImpl
        );
        const archive = Buffer.from(await archiveResponse.arrayBuffer());
        if (archive.length > 1_000_000) {
          skipped.push(`run ${run.id}: summary artifact exceeded the size limit`);
          continue;
        }

        const directory = mkdtempSync(join(tmpdir(), 'circle-performance-history-'));
        try {
          const archivePath = join(directory, 'summary.zip');
          writeFileSync(archivePath, archive);
          const contents = unzipImpl(
            'unzip',
            ['-p', archivePath, 'performance-summary.json'],
            { encoding: 'utf8', maxBuffer: 1_000_000 }
          );
          const summary = JSON.parse(contents);
          if (
            !isCompleteSummary(summary) ||
            !isDailyPerformanceEvidence(summary, run, workflowFile, performanceJobSucceeded)
          ) {
            skipped.push(`run ${run.id}: summary provenance or coverage did not match`);
            continue;
          }
          summaries.push(summary);
          if (summaries.length === historyLimit) break;
        } finally {
          rmSync(directory, { recursive: true, force: true });
        }
      } catch (error) {
        skipped.push(`run ${run.id}: ${error.message}`);
      }
    }

    return {
      status: summaries.length === historyLimit ? 'available' : 'collecting_history',
      reason: summaries.length === historyLimit
        ? undefined
        : `Only ${summaries.length} of ${historyLimit} recent complete daily-main summaries are available.`,
      summaries,
      skipped,
      searchedRuns: candidates.length
    };
  } catch (error) {
    return {
      status: 'unavailable',
      reason: error.message,
      summaries: [],
      skipped
    };
  }
}

function summarizeRolling(history) {
  const keys = new Set();
  for (const summary of history.summaries) {
    for (const key of getResults(summary).keys()) keys.add(key);
  }
  return new Map([...keys].map((key) => {
    const perSummary = history.summaries
      .map((summary) => getResults(summary).get(key))
      .filter(Boolean);
    const route = perSummary.at(-1)?.route;
    const formFactor = perSummary.at(-1)?.formFactor;
    const values = {};
    for (const [metric] of metrics) {
      values[metric] = median(perSummary.map((result) => result.values[metric]));
    }
    return [key, { route, formFactor, values, sampleCount: perSummary.length }];
  }));
}

function conditionDifferences(current, baseline) {
  const differences = [];
  for (const [key, label] of Object.entries({
    runner: 'runner',
    lighthouse: 'Lighthouse',
    chrome: 'Chrome',
    jekyll: 'Jekyll'
  })) {
    if (
      typeof current.environment?.[key] !== 'string' ||
      !current.environment[key] ||
      typeof baseline.environment?.[key] !== 'string' ||
      !baseline.environment[key]
    ) {
      differences.push(`${label} version/environment is missing`);
    } else if (current.environment[key] !== baseline.environment[key]) {
      differences.push(`${label} version/environment differs`);
    }
  }
  for (const [key, label] of [['ruby', 'Ruby'], ['node', 'Node']]) {
    const compatibility = runtimeCompatibility(current, baseline, key);
    if (compatibility === 'unknown') {
      differences.push(`${label} version is missing or unparseable`);
    } else if (compatibility === 'different') {
      differences.push(`${label} major.minor version differs`);
    }
  }
  const currentScripts = new Map((current.externalScripts ?? []).map((script) => [script.source, script]));
  const baselineScripts = new Map((baseline.externalScripts ?? []).map((script) => [script.source, script]));
  for (const source of new Set([...currentScripts.keys(), ...baselineScripts.keys()])) {
    const now = currentScripts.get(source);
    const then = baselineScripts.get(source);
    if (!now || !then || now.successful !== then.successful || now.samples !== then.samples) {
      differences.push(`${source} load outcome differs`);
    } else if (
      Number.isFinite(now.medianTransferBytes) &&
      Number.isFinite(then.medianTransferBytes) &&
      Math.abs(now.medianTransferBytes - then.medianTransferBytes) >
        Math.max(16 * 1024, then.medianTransferBytes * 0.1)
    ) {
      differences.push(`${source} transfer size differs`);
    }
  }
  return differences;
}

function getCurrentSummary(reportDirectory) {
  try {
    const summary = JSON.parse(readFileSync(join(reportDirectory, 'performance-summary.json'), 'utf8'));
    return isCompleteSummary(summary) ? summary : undefined;
  } catch {
    return undefined;
  }
}

function getHistory() {
  const historyPath = resolve(
    process.env.PERFORMANCE_HISTORY_PATH ?? join(tmpdir(), 'performance-history.json')
  );
  try {
    const history = JSON.parse(readFileSync(historyPath, 'utf8'));
    if (
      ['available', 'collecting_history', 'unavailable'].includes(history.status) &&
      Array.isArray(history.summaries) &&
      Array.isArray(history.skipped)
    ) {
      return history;
    }
  } catch {
    return undefined;
  }
  return undefined;
}

function buildComparison(current, reference, history) {
  const compatibleHistory = history.summaries.filter((summary) =>
    sameMeasurementEnvironment(current, summary)
  );
  const comparisonHistory = {
    ...history,
    status: history.status === 'unavailable'
      ? 'unavailable'
      : compatibleHistory.length === historyLimit
        ? 'available'
        : 'collecting_history',
    reason: history.status === 'unavailable'
      ? history.reason
      : compatibleHistory.length === historyLimit
        ? undefined
        : `${compatibleHistory.length} of ${historyLimit} recent successful daily-main summaries match the current environment (Ruby and Node major.minor; other versions exact).`
  };
  comparisonHistory.summaries = compatibleHistory;
  const rolling = compatibleHistory.length === historyLimit
    ? summarizeRolling(comparisonHistory)
    : new Map();
  const currentResults = getResults(current);
  const referenceReady = referenceIsReady(reference) ? reference : undefined;
  const readyReference = referenceReady &&
    sameMeasurementEnvironment(current, referenceReady)
    ? referenceReady
    : undefined;
  const referenceResults = getResults(readyReference);
  const fixedReferenceReason = readyReference
    ? undefined
    : referenceReady
      ? 'The reviewed fixed reference is incompatible with the current tool or runner environment.'
      : 'The reviewed fixed reference is missing, incomplete, or lacks successful main-run provenance.';
  const performanceAssessment = assessPerformance(
    current.results,
    readyReference ? reference.results : [],
    Boolean(readyReference),
    fixedReferenceReason
  );
  const historyProvenance = compatibleHistory.map((summary) => ({
    runId: summary.source.runId,
    runUrl: summary.source.runUrl,
    capturedAt: summary.capturedAt,
    sha: summary.source.sha
  }));
  const deltasAvailable = Boolean(readyReference || rolling.size);
  const historyReason = comparisonHistory.reason?.replace(/[.!?]+$/, '') ?? 'history lookup failed';
  const lines = [
    '',
    '## Baseline comparison',
    '',
    `- **Run:** ${current.source.eventName} on \`${current.source.branch ?? 'unknown'}\`; ${current.coverage.routeCount} routes × mobile/desktop × ${current.coverage.runCount} runs; tools: Lighthouse ${current.environment.lighthouse}, Chrome ${current.environment.chrome}, Node ${current.environment.node}.`,
    `- **Fixed reference:** ${readyReference
      ? `[successful main run](${readyReference.source.runUrl})`
      : referenceReady
        ? 'not comparable; tool or runner versions differ from the reviewed reference'
        : 'unavailable; no complete reviewed successful main reference is available'}.`,
    `- **Rolling daily-main baseline:** ${comparisonHistory.status === 'unavailable'
      ? `unavailable (${historyReason})`
      : comparisonHistory.status === 'available'
        ? `${historyLimit}/${historyLimit} compatible successful daily-main runs; available`
        : `collecting (${compatibleHistory.length}/${historyLimit} compatible successful daily-main runs)`}.`,
    '- **Environment compatibility:** runner, Lighthouse, Chrome, and Jekyll must match exactly; Ruby and Node must match by major.minor. Full versions remain in the summary artifact.',
    '- Rolling comparisons are descriptive only and never establish an enforcement threshold.'
  ];
  if (deltasAvailable) {
    lines.push('- A dash means no matching route/profile baseline metric is available for a selected Circle route.');
  }
  if (historyProvenance.length) {
    const newest = historyProvenance[0];
    const oldest = historyProvenance.at(-1);
    lines.push(
      `- **Rolling sample sources:** ${historyProvenance.length} matching daily-main summaries, from [${newest.runId}](${newest.runUrl}) (${newest.capturedAt}) through [${oldest.runId}](${oldest.runUrl}) (${oldest.capturedAt}); full provenance is in the summary artifact.`
    );
  }
  if (history.skipped?.length) {
    lines.push(`- **History lookup details:** ${history.skipped.slice(0, 3).join('; ')}.`);
  }
  const rows = [];

  for (const [slot, result] of currentResults) {
    const fixedResult = referenceResults.get(slot);
    const rollingResult = rolling.get(slot);
    const fixedDeltas = metrics.map(([metric]) =>
      fixedResult ? formatDelta(metric, result.values[metric], fixedResult.values[metric]) : '—'
    );
    const rollingDeltas = metrics.map(([metric]) =>
      rollingResult ? formatDelta(metric, result.values[metric], rollingResult.values[metric]) : '—'
    );
    rows.push(
      `| \`${result.route}\` | ${result.formFactor} | ${fixedDeltas.join(' · ')} | ${rollingDeltas.join(' · ')} |`
    );

  }

  if (rows.length && deltasAvailable) {
    lines.push(
      '',
      '| Route | Profile | Fixed Δ (FCP · LCP · SI · TBT · CLS) | Rolling Δ (same order) |',
      '| --- | --- | --- | --- |',
      ...rows
    );
  } else {
    lines.push('', '- No comparison/deltas available yet.');
  }

  const comparisons = [referenceReady, ...history.summaries].filter(Boolean);
  const differences = [...new Set(comparisons.flatMap((baseline) => conditionDifferences(current, baseline)))];
  if (differences.length) {
    lines.push(`- **Conditions to consider:** ${differences.join('; ')}.`);
  } else if (comparisons.length) {
    lines.push('- **Conditions:** compatible tool/runtime versions, runner and observed external-script load outcomes match available references.');
  } else {
    lines.push('- **Conditions:** no reference is available for environment or external-script comparison.');
  }

  const comparison = {
    schemaVersion: 1,
    history: {
      status: comparisonHistory.status,
      availableDailySamples: compatibleHistory.length,
      requiredDailySamples: historyLimit,
      searchedRuns: history.searchedRuns ?? null,
      reason: comparisonHistory.reason,
      skipped: history.skipped,
      provenance: historyProvenance
    },
    fixedReference: readyReference
      ? { available: true, runUrl: readyReference.source.runUrl }
      : {
          available: false,
          reason: fixedReferenceReason
        },
    deltasAvailable,
    conditions: differences,
    policy: performanceAssessment,
    comparisons: [...currentResults].map(([slot, result]) => {
      const fixedResult = referenceResults.get(slot);
      const rollingResult = rolling.get(slot);
      const deltas = Object.fromEntries(metrics.map(([metric]) => [
        metric,
        {
          fixed: fixedResult ? {
            absolute: result.values[metric] - fixedResult.values[metric],
            percentage: fixedResult.values[metric] === 0
              ? null
              : ((result.values[metric] - fixedResult.values[metric]) / Math.abs(fixedResult.values[metric])) * 100
          } : null,
          rolling: rollingResult ? {
            absolute: result.values[metric] - rollingResult.values[metric],
            percentage: rollingResult.values[metric] === 0
              ? null
              : ((result.values[metric] - rollingResult.values[metric]) / Math.abs(rollingResult.values[metric])) * 100
          } : null
        }
      ]));
      return {
        route: result.route,
        formFactor: result.formFactor,
        values: result.values,
        fixedReference: fixedResult ? fixedResult.values : null,
        rollingBaseline: rollingResult ? rollingResult.values : null,
        rollingSampleCount: rollingResult?.sampleCount ?? 0,
        deltas
      };
    })
  };
  return { lines, comparison };
}

function sameMeasurementConditions(current, confirmation) {
  return ['site', 'mobile', 'desktop', 'analyticsCollectionBlocked', 'labOnly'].every(
    (key) => current.conditions?.[key] === confirmation.conditions?.[key]
  );
}

function finalizeComparison(reportDirectory, confirmationDirectory) {
  const comparisonPath = join(reportDirectory, 'performance-comparison.json');
  const summaryPath = join(reportDirectory, 'performance-summary.json');
  const comparison = JSON.parse(readFileSync(comparisonPath, 'utf8'));
  const current = JSON.parse(readFileSync(summaryPath, 'utf8'));
  const initialPolicy = comparison.policy;
  if (!initialPolicy) {
    throw new Error('Performance policy assessment is unavailable; this safeguard cannot pass.');
  }
  if (initialPolicy.initialViolations.length === 0) {
    comparison.policy = finalizePerformanceAssessment(initialPolicy);
  } else {
    let confirmation;
    try {
      confirmation = JSON.parse(
        readFileSync(join(confirmationDirectory, 'performance-summary.json'), 'utf8')
      );
    } catch {
      confirmation = undefined;
    }
    const compatible = Boolean(
      confirmation &&
      sameMeasurementEnvironment(current, confirmation) &&
      sameMeasurementConditions(current, confirmation)
    );
    const reason = !confirmation
      ? 'Required confirmation measurement did not produce a readable summary.'
      : !sameMeasurementEnvironment(current, confirmation)
        ? 'Confirmation measurement environment differs from the initial measurement.'
        : !sameMeasurementConditions(current, confirmation)
          ? 'Confirmation measurement conditions differ from the initial measurement.'
          : 'Confirmation measurement did not include every required route/profile sample.';
    comparison.policy = finalizePerformanceAssessment(initialPolicy, confirmation, {
      compatible,
      reason
    });
  }
  writeFileSync(comparisonPath, `${JSON.stringify(comparison, null, 2)}\n`);

  const policy = comparison.policy;
  const lines = ['', '## Performance safeguard'];
  if (policy.status === 'passed' && policy.initialViolations.length === 0) {
    lines.push('- Passed: all route/profile medians are within the absolute budgets.');
  } else if (policy.status === 'passed') {
    lines.push('- Passed: the initial violation(s) were not confirmed by the fresh three-sample measurements.');
  } else if (policy.status === 'incomplete') {
    lines.push(`- **Not passed:** required confirmation is incomplete. ${policy.confirmationReason}`);
  } else {
    lines.push('- **Failed:** one or more performance violations were confirmed.');
  }
  for (const violation of policy.confirmations ?? policy.initialViolations) {
    const profile = `${violation.route} (${violation.formFactor})`;
    const metric = violation.metric;
    if (violation.criterion === 'absolute') {
      const measured = formatValue(metric, violation.value);
      const final = Number.isFinite(violation.confirmationValue)
        ? `; confirmation ${formatValue(metric, violation.confirmationValue)}`
        : '';
      lines.push(`- ${profile}: ${metric} ${measured} exceeded the ${formatValue(metric, violation.limit)} absolute limit${final}; ${violation.result ?? 'confirmation required'}.`);
    } else {
      const confirmation = Number.isFinite(violation.confirmationValue)
        ? `; confirmation ${formatValue(metric, violation.confirmationValue)}`
        : '';
      lines.push(`- ${profile}: LCP ${formatValue(metric, violation.value)} vs reviewed reference ${formatValue(metric, violation.referenceValue)} (+${formatValue(metric, violation.delta)}, +${violation.deltaPercentage.toFixed(1)}%; must exceed both ${formatValue(metric, violation.minimumMilliseconds)} and ${violation.minimumPercentage}%)${confirmation}; ${violation.result ?? 'confirmation required'}.`);
    }
  }
  if (policy.referenceReason) {
    lines.push(`- Relative deterioration gate: unavailable (${policy.referenceReason}); absolute budgets remain enforced.`);
  }
  if (policy.status === 'failed' || policy.status === 'incomplete') {
    lines.push('- Next step: inspect the route/profile reports and address the confirmed regression or restore complete confirmation evidence before relying on this check.');
  }
  process.stdout.write(`${lines.join('\n')}\n`);
  return policy.status === 'failed' || policy.status === 'incomplete' ? 1 : 0;
}

async function main() {
  if (process.argv.includes('--finalize')) {
    const reportDirectory = resolve(
      process.env.PERFORMANCE_REPORT_DIR ?? resolve(process.cwd(), 'performance-reports')
    );
    const confirmationDirectory = resolve(
      process.env.PERFORMANCE_CONFIRMATION_REPORT_DIR ??
        join(tmpdir(), 'performance-confirmation-reports')
    );
    process.exitCode = finalizeComparison(reportDirectory, confirmationDirectory);
    return;
  }

  if (process.argv.includes('--load-history')) {
    const history = await loadDailyHistory();
    const historyPath = resolve(
      process.env.PERFORMANCE_HISTORY_PATH ?? join(tmpdir(), 'performance-history.json')
    );
    writeFileSync(historyPath, `${JSON.stringify(history, null, 2)}\n`);
    process.stdout.write(
      `Daily-main history: ${history.status}; ${history.summaries.length} complete summaries available.\n`
    );
    return;
  }

  const reportDirectory = resolve(
    process.env.PERFORMANCE_REPORT_DIR ?? resolve(process.cwd(), 'performance-reports')
  );
  const summaryPath = join(reportDirectory, 'performance-summary.json');
  const summary = getCurrentSummary(reportDirectory);
  if (!summary) {
    const line = '- Baseline comparison unavailable: measurement did not produce a complete summary; inspect the measurement step and any partial JSON artifacts.';
    process.stdout.write(`${line}\n`);
    return;
  }

  const referencePath = resolve(
    process.env.PERFORMANCE_REFERENCE_PATH ?? '.github/site-performance-reference.json'
  );
  let reference;
  try {
    reference = JSON.parse(readFileSync(referencePath, 'utf8'));
  } catch {
    reference = undefined;
  }
  const history = getHistory() ?? {
    status: 'unavailable',
    reason: 'Daily-main history lookup did not produce a readable result.',
    summaries: [],
    skipped: []
  };
  const { lines, comparison } = buildComparison(summary, reference, history);
  writeFileSync(summaryPath, `${JSON.stringify(summary, null, 2)}\n`);
  writeFileSync(join(reportDirectory, 'performance-comparison.json'), `${JSON.stringify(comparison, null, 2)}\n`);
  if (process.env.GITHUB_OUTPUT) {
    writeFileSync(
      process.env.GITHUB_OUTPUT,
      `deltas_available=${comparison.deltasAvailable}\nhistory_status=${comparison.history.status}\nhistory_count=${comparison.history.availableDailySamples}\nconfirmation_required=${comparison.policy.confirmationProfiles.length > 0}\nconfirmation_profiles=${JSON.stringify(comparison.policy.confirmationProfiles)}\n`,
      { flag: 'a' }
    );
  }
  process.stdout.write(`${lines.join('\n')}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}

export {
  buildComparison,
  isCompleteSummary,
  isDailyPerformanceEvidence,
  loadDailyHistory,
  referenceIsReady,
  finalizeComparison
};
