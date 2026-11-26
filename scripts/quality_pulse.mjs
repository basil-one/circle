import { appendFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  formatPerformanceValue,
  performanceBudgets
} from './site_performance_policy.mjs';

const dimensions = [
  { name: 'Site: Build Integrity', file: 'site-build-integrity.yml' },
  { name: 'Site: Link Integrity', file: 'site-link-integrity.yml' },
  { name: 'Site: SEO Integrity', file: 'site-seo-integrity.yml' },
  { name: 'Common: Code Quality', file: 'common-code-quality.yml' },
  { name: 'Common: Dependency Security', file: 'common-dependency-security.yml' },
  { name: 'Site: Rendering Integrity', file: 'site-rendering-integrity.yml' },
  { name: 'Site: Performance Integrity', file: 'site-performance-integrity.yml' },
  { name: 'Paper: Build Integrity', file: 'paper-build-integrity.yml' }
];

const cutoffTime = '18:00:00Z';
const priorDays = 90;
const staleAfterDays = 2;

function utcDate(value) {
  return value.toISOString().slice(0, 10);
}

function dateDaysAgo(date, days) {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() - days);
  return utcDate(value);
}

function timestamp(value) {
  if (!value) return 'time unavailable';
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? 'time unavailable'
    : `${parsed.toISOString().replace('T', ' ').replace(/Z$/, ' UTC')}`;
}

function runTime(run) {
  if (run.cycle) return 'Same-cycle job result';
  if (run.completionEvidence?.completedAt) {
    return `Completed ${timestamp(run.completionEvidence.completedAt)}`;
  }
  if (run.run_started_at) return `Started ${timestamp(run.run_started_at)}`;
  if (run.created_at) return `Created ${timestamp(run.created_at)}`;
  return 'run time unavailable';
}

function runLink(run) {
  return run?.html_url ?? '';
}

function completionEvidenceFromJobs(jobs) {
  if (!Array.isArray(jobs) || jobs.length === 0) {
    return { status: 'unknown', reason: 'No workflow job completion records were returned.' };
  }
  if (jobs.some((job) => job.status !== 'completed')) {
    return { status: 'incomplete', reason: 'One or more workflow jobs are still incomplete.' };
  }
  const executedJobs = jobs.filter((job) => job.conclusion !== 'skipped');
  if (executedJobs.length === 0) {
    return { status: 'unknown', reason: 'No workflow job ran to completion.' };
  }
  if (executedJobs.some((job) => !['success', 'neutral', 'failure', 'timed_out', 'cancelled'].includes(job.conclusion))) {
    return { status: 'unknown', reason: 'Workflow job conclusions are unavailable or invalid.' };
  }
  const completionTimes = executedJobs.map((job) =>
    typeof job.completed_at === 'string' ? Date.parse(job.completed_at) : Number.NaN
  );
  if (completionTimes.some((value) => !Number.isFinite(value))) {
    return { status: 'unknown', reason: 'Workflow job completion timestamps are unavailable or invalid.' };
  }
  const completedAt = new Date(Math.max(...completionTimes)).toISOString();
  return {
    status: 'completed',
    completedAt,
    successful: executedJobs.every((job) => ['success', 'neutral'].includes(job.conclusion))
  };
}

function evaluateRuns(runs, date, cutoff = cutoffTime, completionByRunId = new Map()) {
  const dailyRuns = runs
    .filter((run) => run.created_at?.slice(0, 10) === date)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const cutoffAt = new Date(`${date}T${cutoff}`);
  const today = dailyRuns[0];
  if (today) {
    const todayEvidenceRun = {
      ...today,
      completionEvidence: completionByRunId.get(today.id)
    };
    if (today.conclusion === 'failure' || today.conclusion === 'timed_out') {
      return {
        state: 'failed',
        label: 'Fresh — failed',
        run: todayEvidenceRun,
        detail: 'Open the workflow run for findings and the next corrective action.'
      };
    }
    if (today.conclusion === 'cancelled') {
      return {
        state: 'failed',
        label: 'Cancelled',
        run: todayEvidenceRun,
        detail: 'The scheduled verification was cancelled; inspect the run and rerun if appropriate.'
      };
    }
    if (today.conclusion === 'skipped') {
      return {
        state: 'skipped',
        label: 'Skipped',
        run: todayEvidenceRun,
        detail: 'The daily verification was skipped; confirm whether the skip was intentional.'
      };
    }
    if (today.status !== 'completed') {
      return {
        state: 'late',
        label: 'Late / incomplete',
        run: todayEvidenceRun,
        detail: `The scheduled run was still ${today.status ?? 'incomplete'} at the ${cutoff} cutoff.`
      };
    }
    if (today.conclusion === 'success') {
      const completion = completionByRunId.get(today.id);
      if (completion?.status !== 'completed' || completion.successful !== true) {
        return {
          state: 'unknown',
          label: 'Unknown completion time',
          run: today,
          detail: `${completion?.reason ?? 'Workflow job completion evidence does not establish a successful finished check'} The run is not marked fresh.`
        };
      }
      if (Date.parse(completion.completedAt) > cutoffAt.valueOf()) {
        return {
          state: 'late',
          label: 'Late',
          run: todayEvidenceRun,
          detail: `The latest workflow job completed after the ${cutoff} cutoff; today's result is late.`
        };
      }
      return {
        state: 'fresh',
        label: 'Fresh — passed',
        run: todayEvidenceRun,
        detail: 'All workflow jobs completed by the cutoff.'
      };
    }
    return {
      state: 'unknown',
      label: 'Unknown outcome',
      run: todayEvidenceRun,
      detail: `The workflow reported ${today.conclusion ?? 'no conclusion'}; inspect the run.`
    };
  }

  const previous = runs
    .filter(
      (run) =>
        run.conclusion === 'success' &&
        run.created_at?.slice(0, 10) < date
    )
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
  if (!previous) {
    return {
      state: 'missing',
      label: 'Missing',
      detail: 'No scheduled main run today or earlier successful verification was found.'
    };
  }
  const ageDays = Math.floor(
    (Date.parse(`${date}T00:00:00Z`) -
      Date.parse(`${previous.created_at.slice(0, 10)}T00:00:00Z`)) /
      86_400_000
  );
  if (ageDays <= staleAfterDays) {
    return {
      state: 'prior',
      label: 'Prior verification',
      run: { ...previous, completionEvidence: completionByRunId.get(previous.id) },
      detail: `No fresh run today. Last successful verification was ${ageDays} day(s) earlier; do not treat it as today's check.`
    };
  }
  return {
    state: 'stale',
    label: 'Stale',
    run: { ...previous, completionEvidence: completionByRunId.get(previous.id) },
    detail: `No fresh run today. Last successful verification was ${ageDays} days earlier; inspect schedule health and rerun.`
  };
}

async function fetchRuns({ apiUrl, repository, workflow, token, date, fetchImpl }) {
  if (!token) throw new Error('GitHub Actions read token is unavailable');
  const url = new URL(
    `/repos/${repository}/actions/workflows/${workflow.file}/runs`,
    apiUrl
  );
  url.search = new URLSearchParams({
    branch: 'main',
    event: 'schedule',
    created: `${dateDaysAgo(date, priorDays)}..${date}`,
    per_page: '100',
    sort: 'created',
    direction: 'desc'
  });
  const response = await fetchImpl(url, {
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: ['Bearer', token].join(' '),
      'X-GitHub-Api-Version': '2022-11-28'
    },
    signal: AbortSignal.timeout(15_000)
  });
  if (!response.ok) throw new Error(`GitHub Actions API returned HTTP ${response.status}`);
  const data = await response.json();
  if (!Array.isArray(data.workflow_runs)) {
    throw new Error('GitHub Actions API returned an unreadable workflow-run list');
  }
  return data.workflow_runs;
}

async function fetchRunArtifact({
  apiUrl,
  repository,
  runId,
  artifactName,
  token,
  fetchImpl
}) {
  if (!token) throw new Error('GitHub Actions read token is unavailable');
  const url = new URL(
    `/repos/${repository}/actions/runs/${runId}/artifacts`,
    apiUrl
  );
  url.search = new URLSearchParams({ name: artifactName, per_page: '100' });
  const response = await fetchImpl(url, {
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: ['Bearer', token].join(' '),
      'X-GitHub-Api-Version': '2022-11-28'
    },
    signal: AbortSignal.timeout(15_000)
  });
  if (!response.ok) throw new Error(`Artifacts API returned HTTP ${response.status}`);
  const data = await response.json();
  if (!Array.isArray(data.artifacts)) {
    throw new Error('Artifacts API returned an unreadable artifact list');
  }
  const artifact = data.artifacts.find(
    (item) => item.name === artifactName && !item.expired
  );
  if (!artifact) throw new Error(`${artifactName} artifact is missing or expired`);
  if (!Number.isInteger(artifact.id)) throw new Error(`${artifactName} artifact ID is invalid`);
  return artifact;
}

async function fetchArtifactLink(options) {
  const artifact = await fetchRunArtifact(options);
  return `https://github.com/${options.repository}/actions/runs/${options.runId}/artifacts/${artifact.id}`;
}

async function fetchRunCompletion({ apiUrl, repository, runId, token, fetchImpl }) {
  const url = new URL(
    `/repos/${repository}/actions/runs/${runId}/jobs`,
    apiUrl
  );
  url.search = new URLSearchParams({ per_page: '100', page: '1' });
  const response = await fetchImpl(url, {
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: ['Bearer', token].join(' '),
      'X-GitHub-Api-Version': '2022-11-28'
    },
    signal: AbortSignal.timeout(15_000)
  });
  if (!response.ok) {
    return { status: 'unknown', reason: `GitHub Actions Jobs API returned HTTP ${response.status}.` };
  }
  const data = await response.json();
  if (
    !Array.isArray(data.jobs) ||
    !Number.isInteger(data.total_count) ||
    data.total_count > data.jobs.length
  ) {
    return { status: 'unknown', reason: 'GitHub Actions Jobs API returned incomplete job records.' };
  }
  return completionEvidenceFromJobs(data.jobs);
}

function performanceDetail(result) {
  if (result.state === 'fresh') {
    return `Lighthouse measurement executed; ${result.baseline ?? 'fixed-reference and rolling-baseline state are in the linked run summary.'}`;
  }
  if (result.state === 'failed') {
    if (result.run?.cycle) return result.detail;
    return `Performance safeguard failed or required measurement/confirmation was incomplete; ${result.baseline ?? 'inspect the linked run summary for confirmed violations and the next action.'}`;
  }
  if (result.state === 'skipped') {
    return 'Measurement was skipped; no current samples or comparison are claimed.';
  }
  return `${result.detail} ${result.baseline ? `Baseline: ${result.baseline}.` : 'Fixed-reference and rolling-baseline state is available only in the linked run summary.'}`;
}

function performancePolicyFailureDetail(policy) {
  if (!policy) return undefined;
  if (['incomplete', 'confirmation-required', 'pending'].includes(policy.status)) {
    const reason = typeof policy.confirmationReason === 'string'
      ? policy.confirmationReason
      : 'required confirmation evidence is unavailable';
    return `Performance confirmation incomplete (${reason}); no pass is inferred. Rerun the check to obtain complete confirmation evidence.`;
  }
  if (policy.status === 'passed') {
    return 'The policy evidence reports no confirmed violation, but the performance job failed in another required operation. Inspect the failed step and rerun Performance Integrity.';
  }
  if (policy.status !== 'failed') {
    return 'Performance policy evidence is incomplete: its final outcome is missing or unrecognized. Inspect the linked run and rerun to obtain a valid safeguard result.';
  }
  const violations = policy.confirmedViolations;
  const validViolations = Array.isArray(violations) &&
    violations.length > 0 &&
    violations.every((violation) => {
      const metricAllowed = Object.hasOwn(
        performanceBudgets[violation?.formFactor] ?? {},
        violation?.metric
      );
      if (
        typeof violation?.route !== 'string' ||
        !metricAllowed ||
        !Number.isFinite(violation.value) ||
        !Number.isFinite(violation.confirmationValue)
      ) return false;
      if (violation.criterion === 'absolute') return Number.isFinite(violation.limit);
      return violation.criterion === 'reference-deterioration' &&
        violation.metric === 'largest-contentful-paint' &&
        Number.isFinite(violation.referenceValue) &&
        Number.isFinite(violation.delta) &&
        Number.isFinite(violation.deltaPercentage);
    });
  if (!validViolations) {
    return 'Performance policy evidence is incomplete: the job failed without a valid confirmed-violation record. Inspect the linked run and rerun if confirmation evidence is missing.';
  }
  const findings = violations.map((violation) => {
    const profile = `\`${violation.route}\`/${violation.formFactor}`;
    const initial = formatPerformanceValue(violation.metric, violation.value);
    const confirmation = formatPerformanceValue(violation.metric, violation.confirmationValue);
    if (violation.criterion === 'absolute') {
      return `${profile} ${violation.metric}: ${initial} initially, ${confirmation} confirmed vs ${formatPerformanceValue(violation.metric, violation.limit)} limit`;
    }
    return `${profile} LCP: ${initial} initially, ${confirmation} confirmed vs reference ${formatPerformanceValue(violation.metric, violation.referenceValue)} (Δ ${formatPerformanceValue(violation.metric, violation.delta)}, ${violation.deltaPercentage > 0 ? '+' : ''}${violation.deltaPercentage.toFixed(1)}%)`;
  });
  return `Confirmed performance violation(s): ${findings.join('; ')}. Next step: correct the affected route/profile and rerun Performance Integrity.`;
}

function describePerformanceComparison(comparison) {
  if (!comparison) {
    return 'fixed reference: unknown; rolling baseline: unknown';
  }
  const fixed = comparison.fixedReference;
  const fixedState = fixed?.available === true
    ? 'available'
    : typeof fixed?.reason !== 'string'
      ? 'unknown'
      : /different tool|runner environment/i.test(fixed.reason)
        ? 'incompatible'
        : /no versioned successful main reference/i.test(fixed.reason)
          ? 'pending'
          : 'unknown';
  const rollingState = rollingBaselineState(comparison.history ?? {});
  return `Fixed reference: ${fixedState}; rolling baseline: ${rollingState}`;
}

function rollingBaselineState(history) {
  const validCounts = Number.isInteger(history.availableDailySamples) &&
    Number.isInteger(history.requiredDailySamples) &&
    history.requiredDailySamples === 28 &&
    history.availableDailySamples >= 0 &&
    history.availableDailySamples <= history.requiredDailySamples;
  if (!validCounts) return 'unknown';
  if (history.status === 'available') {
    return history.availableDailySamples === 28 ? 'available 28/28' : 'unknown';
  }
  if (['collecting_history', 'collecting'].includes(history.status)) {
    return history.availableDailySamples < 28
      ? `collecting ${history.availableDailySamples}/28`
      : 'unknown';
  }
  if (history.status === 'unavailable') {
    return history.availableDailySamples === 0 ? 'unavailable' : 'unknown';
  }
  return 'unknown';
}

function isPerformanceComparison(value) {
  return Boolean(value?.schemaVersion === 1 &&
    value.fixedReference &&
    typeof value.fixedReference === 'object' &&
    value.history &&
    typeof value.history === 'object' &&
    Array.isArray(value.comparisons));
}

function formatTrendDelta(delta) {
  if (!delta || typeof delta !== 'object') return '—';
  const absolute = Number.isFinite(delta.absolute)
    ? `${delta.absolute > 0 ? '+' : ''}${Math.abs(delta.absolute) >= 1_000
      ? `${(delta.absolute / 1_000).toFixed(2)} s`
      : `${Math.round(delta.absolute)} ms`}`
    : undefined;
  const percentage = Number.isFinite(delta.percentage)
    ? `(${delta.percentage > 0 ? '+' : ''}${delta.percentage.toFixed(1)}%)`
    : undefined;
  return [absolute, percentage].filter(Boolean).join(' ') || '—';
}

function homepageTrendRows(comparison) {
  const homepage = comparison.comparisons.find((item) => item.route === '/');
  if (!homepage) return [];
  const fixedAvailable = comparison.fixedReference.available === true;
  const rollingAvailable = rollingBaselineState(comparison.history) === 'available 28/28';
  const measures = [
    ['mobile', 'largest-contentful-paint', 'Mobile LCP'],
    ['mobile', 'total-blocking-time', 'Mobile TBT'],
    ['desktop', 'largest-contentful-paint', 'Desktop LCP']
  ];
  return measures.flatMap(([formFactor, metric, label]) => {
    const result = comparison.comparisons.find(
      (item) => item.route === homepage.route && item.formFactor?.toLowerCase() === formFactor
    );
    if (!result || !Object.hasOwn(result.deltas ?? {}, metric)) return [];
    return [{
      label,
      fixed: fixedAvailable ? formatTrendDelta(result.deltas?.[metric]?.fixed) : '—',
      rolling: rollingAvailable ? formatTrendDelta(result.deltas?.[metric]?.rolling) : '—'
    }];
  });
}

function renderPerformanceTrends(result) {
  const trends = result?.trends;
  const lines = [
    '## Performance Trends',
    '',
    'Lower is better for these lab metrics; positive deltas mean a higher measured value. Comparisons are informational and do not establish a sustained direction.'
  ];
  const canShowComparison = ['fresh', 'prior'].includes(trends?.state) &&
    isPerformanceComparison(trends?.comparison) &&
    /^\d{4}-\d{2}-\d{2}$/.test(trends.measurementDate ?? '');
  if (!canShowComparison) {
    const reason = trends?.reason ??
      (trends?.comparison
        ? 'the measurement is not a dated successful current or prior run'
        : 'no successful measurement evidence is available.');
    lines.push('', `Performance comparison unavailable: ${reason}`);
    if (trends?.runUrl) lines.push(`Source: [Site: Performance Integrity run](${trends.runUrl}).`);
    if (trends?.baseline) lines.push(`Baselines: ${trends.baseline}.`);
    return lines;
  }

  const measurement = trends.state === 'prior'
    ? `Prior successful measurement — ${trends.measurementDate}; not today's verification.`
    : trends.eventName === 'workflow_dispatch'
      ? `Measurement — ${trends.measurementDate} UTC (manually dispatched cycle).`
      : `Measurement — ${trends.measurementDate} UTC (fresh scheduled run).`;
  lines.push(
    '',
    `**${measurement}**`,
    `**Baselines:** ${describePerformanceComparison(trends.comparison)}.`
  );
  const sources = [];
  if (trends.runUrl) sources.push(`[Site: Performance Integrity run](${trends.runUrl})`);
  if (trends.artifactUrl) sources.push(`[comparison artifact](${trends.artifactUrl})`);
  const fixedRunUrl = trends.comparison.fixedReference?.runUrl;
  if (fixedRunUrl) sources.push(`[fixed reference run](${fixedRunUrl})`);
  if (sources.length) lines.push(`**Evidence:** ${sources.join(' · ')}.`);
  lines.push(
    '',
    '| Measure | vs. fixed reference | vs. rolling baseline |',
    '| --- | --- | --- |'
  );
  const rows = homepageTrendRows(trends.comparison);
  if (rows.length) {
    for (const row of rows) lines.push(`| ${row.label} | ${row.fixed} | ${row.rolling} |`);
  } else {
    lines.push('| Homepage metrics | — | — |');
  }
  lines.push('', 'Other pages and full metric details are in the linked Site: Performance Integrity summary.');
  return lines;
}

async function fetchArtifactJson({
  apiUrl,
  repository,
  token,
  runId,
  artifactName,
  fileName,
  fileNames,
  fetchImpl,
  includeMetadata = false,
  unzipImpl = execFileSync
}) {
  const headers = {
    Accept: 'application/vnd.github+json',
    Authorization: ['Bearer', token].join(' '),
    'X-GitHub-Api-Version': '2022-11-28'
  };
  const artifact = await fetchRunArtifact({
    apiUrl,
    repository,
    runId,
    artifactName,
    token,
    fetchImpl
  });
  const downloadUrl = new URL(
    `/repos/${repository}/actions/artifacts/${artifact.id}/zip`,
    apiUrl
  );
  const downloadResponse = await fetchImpl(downloadUrl, {
    headers,
    signal: AbortSignal.timeout(30_000)
  });
  if (!downloadResponse.ok) {
    throw new Error(`${artifactName} artifact download returned HTTP ${downloadResponse.status}`);
  }
  const archive = Buffer.from(await downloadResponse.arrayBuffer());
  if (archive.length > 10 * 1024 * 1024) {
    throw new Error(`${artifactName} artifact exceeded the 10 MB safety limit`);
  }
  const directory = mkdtempSync(join(tmpdir(), 'circle-quality-pulse-'));
  const archivePath = join(directory, `${artifactName}.zip`);
  try {
    writeFileSync(archivePath, archive);
    const data = fileNames
      ? Object.fromEntries(fileNames.map((name) => [
        name,
        JSON.parse(unzipImpl(
          'unzip',
          ['-p', archivePath, name],
          { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }
        ))
      ]))
      : JSON.parse(unzipImpl(
        'unzip',
        ['-p', archivePath, fileName],
        { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }
      ));
    return includeMetadata ? { data, artifact } : data;
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

function applyDependencyPosture(result, posture) {
  if (posture.status === 'unknown') {
    return {
      ...result,
      state: 'unknown',
      label: 'Unknown / API inaccessible',
      detail: `${posture.detail ?? 'Dependency posture could not be read'}; no result is inferred.`
    };
  }
  if (posture.status === 'review') {
    return {
      ...result,
      state: 'failed',
      label: 'Fresh — review required',
      detail: `${posture.detail}; open the linked run for alert findings.`
    };
  }
  if (posture.status === 'passed' && result.state === 'fresh') {
    return {
      ...result,
      detail: 'Open Dependabot alerts were checked across enabled ecosystems; none met the review threshold.'
    };
  }
  return result;
}

function renderPulse({
  date,
  generatedAt,
  results,
  cutoff = cutoffTime,
  orchestrated = false
}) {
  const needsReview = results.filter((item) => item.result.state !== 'fresh');
  const aggregate = needsReview.length
    ? orchestrated
      ? `Cycle incomplete — ${needsReview.length} of ${results.length} dimensions are not confirmed successful.`
      : `Review required — ${needsReview.length} of ${results.length} dimensions are not freshly successful.`
    : orchestrated
      ? `All ${results.length} dimensions passed in this cycle.`
      : `All ${results.length} dimensions have fresh successful scheduled checks.`;
  const lines = [
    '# Quality Pulse',
    '',
    `**Reporting date:** ${date} UTC  ·  **Generated:** ${timestamp(generatedAt)}${orchestrated ? '' : `  ·  **Cutoff:** ${date} ${cutoff}`}`,
    '',
    `**Outcome:** ${aggregate} ${orchestrated
      ? 'This summary reflects the same-cycle substantive job results and does not replace their check outcomes.'
      : 'This is an informational review summary, not a required PR check or remediation verdict.'}`,
    '',
    '## Findings',
    ''
  ];
  if (needsReview.length) {
    for (const item of needsReview) {
      const state = item.result.state;
      const action = state === 'failed'
        ? item.name === 'Site: Performance Integrity' && item.result.run?.cycle
          ? ''
          : 'Open the run and address the reported failure.'
        : state === 'unknown'
          ? 'Check Actions/API access and inspect the last available workflow run.'
          : state === 'missing' || state === 'stale'
            ? 'Check whether the scheduled workflow ran; dispatch it manually if needed.'
              : state === 'prior'
                ? "Check today's schedule; an earlier pass is not today's verification."
              : state === 'skipped'
                ? 'Confirm the skip is expected and arrange a full verification if not.'
              : state === 'incomplete'
                ? 'Inspect the cancelled or incomplete job and rerun the cycle if appropriate.'
                : 'Wait for completion or inspect the late run; rerun if it does not finish.';
      lines.push(`- **${item.name} — ${item.result.label}:** ${item.result.detail}${action ? ` ${action}` : ''}`);
    }
  } else {
    lines.push('No material failures or missing coverage were reported.');
  }
  const performance = results.find((item) => item.name === 'Site: Performance Integrity');
  lines.push('', ...renderPerformanceTrends(performance?.result), '');
  lines.push(
    '',
    '## Evidence',
    '',
    '| Dimension | Outcome | Measurement / run time (UTC) | Source / run | Useful detail or next action |',
    '| --- | --- | --- | --- | --- |'
  );
  for (const item of results) {
    const result = item.result;
    const sources = [];
    if (result.run && runLink(result.run)) {
      sources.push(`[${orchestrated ? 'same-cycle run' : 'main scheduled run'}](${runLink(result.run)})`);
    }
    if (result.artifactUrl) sources.push(`[evidence artifact](${result.artifactUrl})`);
    const source = sources.length ? sources.join(' · ') : 'No run link available';
    const detail = item.name === 'Site: Performance Integrity'
      ? performanceDetail(result)
      : result.detail;
    lines.push(
      `| ${item.name} | ${result.label} | ${result.run ? runTime(result.run) : 'not measured today'} | ${source} | ${detail.replace(/[\\|]/g, '\\$&')} |`
    );
  }
  lines.push('');
  lines.push(orchestrated
    ? 'Each outcome below comes from the corresponding reusable workflow job in this run. No fixed wall-clock completion cutoff is applied.'
    : `Daily workflow runs are queried from \`main\` with \`schedule\` provenance. Runs must complete by ${cutoff} UTC to count as fresh; late or missing runs are not replaced with prior green results.`);
  return `${lines.join('\n')}\n`;
}

function orchestratedJobResult(needs, jobId, name, run) {
  if (!needs || !Object.hasOwn(needs, jobId)) {
    return {
      name,
      result: {
        state: 'missing',
        label: 'Missing',
        run,
        detail: `The orchestration did not provide a result for the ${jobId} job.`
      }
    };
  }
  const outcome = needs[jobId]?.result;
  const outcomes = {
    success: ['fresh', 'Passed', 'The substantive check completed successfully in this cycle.'],
    failure: ['failed', 'Failed', 'The substantive check failed in this cycle; inspect its job logs and artifacts.'],
    skipped: ['skipped', 'Skipped', 'The substantive check was skipped; confirm whether the skip was intentional.'],
    cancelled: ['incomplete', 'Incomplete / cancelled', 'The substantive check was cancelled before a result was established.']
  };
  const mapped = outcomes[outcome];
  if (!mapped) {
    return {
      name,
      result: {
        state: 'unknown',
        label: 'Unknown outcome',
        run,
        detail: `The orchestration reported an unavailable or unrecognized ${jobId} result; no pass is inferred.`
      }
    };
  }
  return {
    name,
    result: { state: mapped[0], label: mapped[1], run, detail: mapped[2] }
  };
}

async function buildOrchestratedPulse({
  apiUrl,
  repository,
  token,
  needs,
  runId,
  runUrl,
  eventName = 'schedule',
  branch = 'main',
  date,
  generatedAt = new Date().toISOString(),
  fetchImpl = fetch,
  unzipImpl = execFileSync
}) {
  const run = {
    id: runId,
    html_url: runUrl,
    cycle: true
  };
  const jobMap = [
    ['site-build', 'Site: Build Integrity'],
    ['link', 'Site: Link Integrity'],
    ['seo', 'Site: SEO Integrity'],
    ['code-quality', 'Common: Code Quality'],
    ['dependency-security', 'Common: Dependency Security'],
    ['rendering', 'Site: Rendering Integrity'],
    ['performance', 'Site: Performance Integrity']
  ];
  const results = jobMap.map(([jobId, name]) =>
    orchestratedJobResult(needs, jobId, name, run)
  );
  const paperNeeds = needs?.paper;
  const paperBuild = paperNeeds?.outputs?.build ?? (paperNeeds?.result === 'cancelled' ? 'cancelled' : undefined);
  const paperLinks = paperNeeds?.outputs?.links;
  results.push(paperNeeds
    ? orchestratedJobResult({ build: { result: paperBuild } }, 'build', 'Paper: Build Integrity', run)
    : orchestratedJobResult(needs, 'paper', 'Paper: Build Integrity', run));
  const linkResult = orchestratedJobResult(
    { links: { result: paperLinks } }, 'links', 'Paper: Link Integrity', run
  );
  if (['incomplete', 'uncertain'].includes(paperLinks) || paperBuild !== 'success') {
    linkResult.result.state = 'incomplete';
    linkResult.result.label = 'Incomplete / link evidence';
    linkResult.result.detail = 'Link verification or the same-cycle PDF was unavailable; inspect the link job report, retry inaccessible destinations or review them manually.';
  }
  results.push(linkResult);
  const artifactOptions = { apiUrl, repository, token, runId, fetchImpl, unzipImpl };
  const performance = results.find((item) => item.name === 'Site: Performance Integrity');
  if (['fresh', 'failed'].includes(performance.result.state)) {
    try {
      const { data: files, artifact } = await fetchArtifactJson({
        ...artifactOptions,
        artifactName: 'performance-summary',
        fileNames: ['performance-comparison.json', 'performance-summary.json'],
        includeMetadata: true
      });
      const comparison = files['performance-comparison.json'];
      const summary = files['performance-summary.json'];
      if (
        !isPerformanceComparison(comparison) ||
        summary?.complete !== true ||
        summary.source?.eventName !== eventName ||
        summary.source?.branch !== branch ||
        String(summary.source?.runId) !== String(runId)
      ) {
        throw new Error('performance artifacts have incomplete or mismatched cycle provenance');
      }
      const measurementDate = summary.capturedAt?.slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(measurementDate ?? '')) {
        throw new Error('Site: Performance Integrity summary has no valid measurement date');
      }
      performance.result.baseline = describePerformanceComparison(comparison);
      if (performance.result.state === 'failed') {
        performance.result.detail =
          performancePolicyFailureDetail(comparison.policy) ??
          'Performance check failed; this comparison artifact predates structured policy evidence. Inspect the linked run summary and failed step.';
      }
      performance.result.trends = {
        state: performance.result.state,
        measurementDate,
        eventName: summary.source.eventName,
        comparison,
        runUrl,
        artifactUrl: `https://github.com/${repository}/actions/runs/${runId}/artifacts/${artifact.id}`
      };
      performance.result.artifactUrl = performance.result.trends.artifactUrl;
    } catch (error) {
      if (performance.result.state === 'fresh') {
        performance.result.state = 'unknown';
        performance.result.label = 'Unknown / performance evidence unavailable';
      }
      performance.result.detail += ` Performance evidence incomplete or unavailable (${error.message}); no comparison or pass is inferred. Review the run and artifact upload, recover complete measurements/confirmations, and rerun Performance Integrity.`;
      performance.result.baseline = 'fixed reference: unknown; rolling baseline: unknown';
      performance.result.trends = {
        reason: `comparison artifact unavailable or invalid (${error.message})`,
        baseline: performance.result.baseline,
        runUrl
      };
    }
  } else {
    performance.result.trends = {
      reason: `this cycle's Site: Performance Integrity result is ${performance.result.label.toLowerCase()}; no successful current trend is shown`,
      runUrl
    };
  }

  const dependency = results.find((item) => item.name === 'Common: Dependency Security');
  if (['fresh', 'failed'].includes(dependency.result.state)) {
    try {
      const { data: posture, artifact } = await fetchArtifactJson({
        ...artifactOptions,
        artifactName: 'dependency-posture',
        fileName: 'dependency-posture.json',
        includeMetadata: true
      });
      if (!['passed', 'review', 'unknown'].includes(posture?.status)) {
        throw new Error('dependency posture artifact has an invalid outcome');
      }
      dependency.result = applyDependencyPosture(dependency.result, posture);
      dependency.result.artifactUrl = `https://github.com/${repository}/actions/runs/${runId}/artifacts/${artifact.id}`;
    } catch (error) {
      if (dependency.result.state === 'fresh') {
        dependency.result.state = 'unknown';
        dependency.result.label = 'Unknown / posture artifact unavailable';
      }
      dependency.result.detail += ` Dependency posture evidence unavailable or invalid (${error.message}); no clean result is inferred.`;
    }
  }

  const paper = results.find((item) => item.name === 'Paper: Build Integrity');
  if (paper.result.state === 'fresh') {
    try {
      paper.result.artifactUrl = await fetchArtifactLink({
        apiUrl,
        repository,
        runId,
        artifactName: 'circle3-paper-preview',
        token,
        fetchImpl
      });
    } catch (error) {
      paper.result.state = 'unknown';
      paper.result.label = 'Unknown / PDF preview unavailable';
      paper.result.detail += ` PDF preview evidence unavailable (${error.message}); no successful artifact is inferred.`;
    }
  }

  if (paper.result.state !== 'fresh' && ['fresh', 'incomplete'].includes(linkResult.result.state)) {
    linkResult.result.state = 'incomplete';
    linkResult.result.label = 'Incomplete / PDF preview unavailable';
    linkResult.result.detail = 'The same-cycle PDF preview could not be verified; inspect the paper build and rerun.';
  }

  if (paperBuild === 'success' && ['success', 'failure', 'uncertain', 'incomplete'].includes(paperLinks)) {
    try {
      linkResult.result.artifactUrl = await fetchArtifactLink({
        apiUrl, repository, runId, artifactName: 'paper-link-report', token, fetchImpl
      });
      linkResult.result.detail += ' Open the linked report for page-level destinations and next steps.';
    } catch (error) {
      if (linkResult.result.state === 'fresh') {
        linkResult.result.state = 'unknown';
        linkResult.result.label = 'Unknown / link report unavailable';
      }
      linkResult.result.detail += ` Link report unavailable (${error.message}); inspect the link job and rerun.`;
    }
  }

  const markdown = renderPulse({
    date,
    generatedAt,
    results,
    orchestrated: true
  });
  return {
    markdown,
    successful: results.every((item) =>
      item.result.state === 'fresh' ||
      (item === linkResult && paperLinks === 'uncertain' &&
        paper.result.state === 'fresh' && Boolean(item.result.artifactUrl))
    )
  };
}

async function buildPulse({
  apiUrl,
  repository,
  token,
  date,
  generatedAt = new Date().toISOString(),
  fetchImpl = fetch,
  unzipImpl = execFileSync
}) {
  const results = await Promise.all(
    dimensions.map(async (dimension) => {
      try {
        const runs = await fetchRuns({
          apiUrl,
          repository,
          workflow: dimension,
          token,
          date,
          fetchImpl
        });
        const todayRun = runs
          .filter((run) => run.created_at?.slice(0, 10) === date)
          .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
        const previousRun = runs
          .filter(
            (run) =>
              run.conclusion === 'success' &&
              run.created_at?.slice(0, 10) < date
          )
          .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
        const evidenceRun = todayRun ?? previousRun;
        const completionByRunId = new Map();
        if (evidenceRun?.id && evidenceRun.status === 'completed') {
          completionByRunId.set(
            evidenceRun.id,
            await fetchRunCompletion({
              apiUrl,
              repository,
              runId: evidenceRun.id,
              token,
              fetchImpl
            })
          );
        }
        return {
          name: dimension.name,
          result: evaluateRuns(runs, date, cutoffTime, completionByRunId)
        };
      } catch (error) {
        return {
          name: dimension.name,
          result: {
            state: 'unknown',
            label: 'Unknown / API inaccessible',
            detail: `${error.message}; no result is inferred.`
          }
        };
      }
    })
  );
  const performance = results.find((item) => item.name === 'Site: Performance Integrity');
  if (
    performance?.result.run?.id &&
    performance.result.run.status === 'completed' &&
    ['fresh', 'prior'].includes(performance.result.state) &&
    performance.result.run.conclusion === 'success'
  ) {
    try {
      const { data: comparison, artifact } = await fetchArtifactJson({
        apiUrl,
        repository,
        token,
        runId: performance.result.run.id,
        artifactName: 'performance-summary',
        fileName: 'performance-comparison.json',
        fetchImpl,
        includeMetadata: true,
        unzipImpl
      });
      if (!isPerformanceComparison(comparison)) {
        throw new Error('performance-comparison.json has an unsupported or incomplete structure');
      }
      const measurementDate = performance.result.run.created_at?.slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(measurementDate ?? '')) {
        throw new Error('Site: Performance Integrity run has no valid measurement date');
      }
      performance.result.baseline = describePerformanceComparison(comparison);
      performance.result.trends = {
        state: performance.result.state,
        measurementDate,
        comparison,
        runUrl: runLink(performance.result.run),
        artifactUrl: `https://github.com/${repository}/actions/runs/${performance.result.run.id}/artifacts/${artifact.id}`
      };
    } catch (error) {
      performance.result.baseline = `fixed reference: unknown; rolling baseline: unknown`;
      performance.result.trends = {
        reason: `comparison artifact unavailable or invalid (${error.message})`,
        baseline: performance.result.baseline,
        runUrl: runLink(performance.result.run)
      };
    }
  } else if (performance) {
    const result = performance.result;
    performance.result.trends = {
      reason: result.state === 'prior'
        ? 'no prior successful comparison artifact is available'
        : `today's Site: Performance Integrity result is ${result.label.toLowerCase()}; no successful current trend is shown`,
      runUrl: runLink(result.run)
    };
  }
  const dependency = results.find((item) => item.name === 'Common: Dependency Security');
  if (
    dependency?.result.run?.id &&
    dependency.result.run.status === 'completed' &&
    ['success', 'failure'].includes(dependency.result.run.conclusion)
  ) {
    try {
      const posture = await fetchArtifactJson({
        apiUrl,
        repository,
        token,
        runId: dependency.result.run.id,
        artifactName: 'dependency-posture',
        fileName: 'dependency-posture.json',
        fetchImpl
      });
      dependency.result = applyDependencyPosture(dependency.result, posture);
    } catch (error) {
      dependency.result = {
        ...dependency.result,
        state: 'unknown',
        label: 'Unknown / posture artifact unavailable',
        detail: `${error.message}; no clean dependency result is inferred.`
      };
    }
  }
  return renderPulse({ date, generatedAt, results });
}

async function main() {
  const date = process.env.PULSE_DATE ?? utcDate(new Date());
  const pulse = await buildOrchestratedPulse({
    apiUrl: process.env.GITHUB_API_URL ?? 'https://api.github.com',
    repository: process.env.GITHUB_REPOSITORY,
    token: process.env.GITHUB_TOKEN,
    needs: JSON.parse(process.env.PULSE_RESULTS_JSON ?? '{}'),
    runId: process.env.PULSE_RUN_ID,
    runUrl: process.env.PULSE_RUN_URL,
    eventName: process.env.PULSE_EVENT_NAME ?? 'schedule',
    branch: process.env.PULSE_RUN_BRANCH ?? 'main',
    date,
    generatedAt: process.env.PULSE_GENERATED_AT ?? new Date().toISOString()
  });
  if (process.env.GITHUB_STEP_SUMMARY) {
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, pulse.markdown);
  }
  process.stdout.write(pulse.markdown);
  if (!pulse.successful) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  await main();
}

export {
  applyDependencyPosture,
  buildOrchestratedPulse,
  buildPulse,
  completionEvidenceFromJobs,
  describePerformanceComparison,
  evaluateRuns,
  formatTrendDelta,
  homepageTrendRows,
  isPerformanceComparison,
  renderPerformanceTrends,
  renderPulse
};
