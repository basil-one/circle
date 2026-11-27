import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';

const workflows = [
  ['common-code-quality.yml', 'common-code-quality', 'Common: Code Quality'],
  ['common-coherence-integrity.yml', 'common-coherence-integrity', 'Common: Coherence Integrity'],
  ['common-dependency-security.yml', 'common-dependency-security-review', 'Common: Dependency Security'],
  ['site-build-integrity.yml', 'site-build-integrity', 'Site: Build Integrity'],
  ['site-link-integrity.yml', 'site-link-integrity', 'Site: Link Integrity'],
  ['site-seo-integrity.yml', 'site-seo-integrity', 'Site: SEO Integrity'],
  ['site-rendering-integrity.yml', 'site-rendering-integrity', 'Site: Rendering Integrity'],
  ['site-performance-integrity.yml', 'site-performance-integrity', 'Site: Performance Integrity'],
  ['paper-build-integrity.yml', 'paper-build-integrity', 'Paper: Build Integrity'],
  ['paper-content-integrity.yml', 'paper-content-integrity', 'Paper: Content Integrity'],
  ['paper-link-integrity.yml', 'paper-link-integrity', 'Paper: Link Integrity'],
  ['paper-rendering-integrity.yml', 'paper-rendering-integrity', 'Paper: Rendering Integrity']
];

function workflowRunScript(contents, stepName) {
  const step = contents.indexOf(`      - name: ${stepName}\n`);
  assert.notEqual(step, -1, `workflow contains step ${stepName}`);
  const run = contents.indexOf('        run: |\n', step);
  assert.notEqual(run, -1, `step ${stepName} contains a shell block`);
  const lines = contents.slice(run + '        run: |\n'.length).split('\n');
  const script = [];
  for (const line of lines) {
    if (line === '') {
      script.push('');
    } else if (line.startsWith('          ')) {
      script.push(line.slice(10));
    } else {
      break;
    }
  }
  return script.join('\n');
}

test('required PR checks are emitted by their substantive workflow jobs', async () => {
  for (const [file, jobId, displayName] of workflows) {
    const yaml = await readFile(new URL(`../../.github/workflows/${file}`, import.meta.url), 'utf8');
    assert.match(yaml, /^  pull_request:\n    branches: \[main\]$/m, `${file} checks PRs targeting main`);
    assert.match(yaml, /^  workflow_dispatch:$/m, `${file} supports manual diagnostics`);
    assert.match(yaml, /^  workflow_call:\s*$/m, `${file} can be called by Quality Pulse`);
    assert.doesNotMatch(yaml, /^  schedule:$/m, `${file} has no independent schedule`);

    const jobsStart = yaml.indexOf('\njobs:\n') + '\njobs:\n'.length;
    const jobs = yaml.slice(jobsStart);
    const jobHeaders = [...jobs.matchAll(/^  ([a-z][a-z0-9-]*):\s*$/gm)];
    assert.ok(jobHeaders.some(([header]) => header === `  ${jobId}:`), `${file} defines ${jobId}`);
    assert.doesNotMatch(jobs, /^  bridge-[a-z0-9-]+:/m, `${file} has no bridge jobs`);
    const namedJobs = [...jobs.matchAll(/^    name: (.+)$/gm)]
      .map(([, name]) => name.replace(/^"(.*)"$/, '$1'));
    assert.equal(namedJobs.filter((name) => name === displayName).length, 1, `${file} emits one canonical job`);

    const header = jobHeaders.find(([line]) => line === `  ${jobId}:`);
    const nextHeader = jobHeaders.find((match) => match.index > header.index);
    const job = jobs.slice(header.index, nextHeader?.index ?? jobs.length);
    assert.match(job, new RegExp(`^    name: "${displayName.replaceAll(':', '\\:')}"$`, 'm'));
    assert.match(job, /^    steps:\s*$/m, `${jobId} has substantive steps`);
    assert.doesNotMatch(job, /^    needs:/m, `${jobId} is independent, not a proxy job`);

    const condition = job.match(/^    if: (.+)$/m)?.[1];
    if (jobId === 'common-dependency-security-review') {
      assert.equal(condition, "github.event_name == 'pull_request'");
    } else {
      assert.equal(condition, undefined, `${jobId} cannot be skipped by a job condition`);
    }
  }
});

test('Paper Link summary distinguishes audit results from supporting evidence', async () => {
  const workflow = await readFile(new URL('../../.github/workflows/paper-link-integrity.yml', import.meta.url), 'utf8');
  const script = workflowRunScript(workflow, 'Summarize link integrity');
  const directory = mkdtempSync(join(tmpdir(), 'paper-link-summary-'));
  const runnerTemp = join(directory, 'runner');
  mkdirSync(runnerTemp);
  const summaryPath = join(directory, 'summary.md');
  const summarize = (overrides = {}) => {
    writeFileSync(summaryPath, '');
    execFileSync('bash', ['-c', script], {
      encoding: 'utf8',
      env: {
        ...process.env,
        SOURCE_SHA: 'fixture-sha',
        BUILD_OUTCOME: 'success',
        TOOLS_OUTCOME: 'success',
        AUDIT_OUTCOME: 'success',
        AUDIT_STATUS: 'success',
        TEST_OUTCOME: 'success',
        UPLOAD_OUTCOME: 'success',
        ARTIFACT_URL: 'https://example.test/paper-link-report',
        RUNNER_TEMP: runnerTemp,
        GITHUB_STEP_SUMMARY: summaryPath,
        ...overrides
      }
    });
    const summary = readFileSync(summaryPath, 'utf8');
    return summary.match(/- Overall verification: \*\*([^*]+)\*\*/)?.[1];
  };

  try {
    assert.equal(summarize(), 'passed');
    assert.equal(summarize({ AUDIT_STATUS: 'advisory' }), 'advisory');
    assert.equal(
      summarize({
        AUDIT_STATUS: 'failure',
        AUDIT_OUTCOME: 'failure',
        BUILD_OUTCOME: 'failure',
        TOOLS_OUTCOME: 'failure',
        TEST_OUTCOME: 'skipped',
        UPLOAD_OUTCOME: 'failure'
      }),
      'failed'
    );
    for (const overrides of [
      { BUILD_OUTCOME: 'failure', AUDIT_OUTCOME: 'skipped', AUDIT_STATUS: '' },
      { TOOLS_OUTCOME: 'failure', AUDIT_OUTCOME: 'skipped', AUDIT_STATUS: '' },
      { TEST_OUTCOME: 'failure' },
      { UPLOAD_OUTCOME: 'failure' },
      { TEST_OUTCOME: 'skipped' }
    ]) {
      assert.equal(summarize(overrides), 'incomplete');
    }
    assert.equal(
      summarize({ AUDIT_OUTCOME: 'skipped', AUDIT_STATUS: '', TOOLS_OUTCOME: 'success' }),
      'incomplete'
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('all twelve current Common, Site, and Paper checks have substantive evidence semantics', async () => {
  assert.equal(workflows.length, 12);
  const coherence = await readFile(
    new URL('../../.github/workflows/common-coherence-integrity.yml', import.meta.url),
    'utf8'
  );
  assert.match(coherence, /run: npm run test:coherence-integrity/);
  assert.match(coherence, /SOURCE_SHA: \$\{\{ steps\.revision\.outputs\.sha \}\}/);
  assert.match(coherence, /success\) result="\*\*passed\*\*"/);

  const file = 'site-performance-integrity.yml';
  const yaml = await readFile(new URL(`../../.github/workflows/${file}`, import.meta.url), 'utf8');
  assert.ok(workflows.some(([requiredFile]) => requiredFile === file));
  assert.match(yaml, /^  pull_request:\n    branches: \[main\]$/m);
  assert.match(yaml, /^  workflow_dispatch:$/m);
  assert.match(yaml, /^  workflow_call:\n    inputs:\n      caller_event_name:/m);
  assert.doesNotMatch(yaml, /^  schedule:$/m);
  assert.match(yaml, /^  site-performance-integrity:\n    name: "Site: Performance Integrity"$/m);
  assert.match(yaml, /^      - name: Run performance classifier and history tests\n        id: tests\n        run: npm run test:performance-history/m);
  assert.match(yaml, /^      - name: Classify pull request changes for Lighthouse\n        id: classify\n        if: github\.event_name == 'pull_request'\n        continue-on-error: true/m);
  assert.match(yaml, /^      - name: Measure rendered pages\n        id: measure\n        if: .+\n        run: npm run measure:performance/m);
  assert.match(yaml, /steps\.classify\.outcome == 'failure' \|\| steps\.classify\.outputs\.skip_measurement != 'true'/);
  assert.match(yaml, /^      - name: Render Performance Integrity summary\n        if: always\(\)/m);
  assert.match(yaml, /node scripts\/site\/render_performance_summary\.mjs/);
  assert.match(yaml, /SOURCE_SHA: \$\{\{ steps\.revision\.outputs\.sha \}\}/);
  assert.match(yaml, /STEP_TESTS: \$\{\{ steps\.tests\.outcome \}\}/);
  assert.match(yaml, /STEP_CLASSIFY: \$\{\{ steps\.classify\.outcome \}\}/);
  assert.match(yaml, /STEP_MEASURE: \$\{\{ steps\.measure\.outcome \}\}/);
  const pulse = await readFile(new URL('../../.github/workflows/quality-pulse.yml', import.meta.url), 'utf8');
  assert.match(pulse, /site-performance-integrity:\n    name: "Site: Performance Integrity"\n    uses: \.\/\.github\/workflows\/site-performance-integrity\.yml\n    with:\n      caller_event_name: \$\{\{ github\.event_name \}\}/);

  const siteRendering = await readFile(
    new URL('../../.github/workflows/site-rendering-integrity.yml', import.meta.url),
    'utf8'
  );
  const summaryEnvironment = siteRendering.split('- name: Summarize rendered-site checks')[1] ?? '';
  assert.match(siteRendering, /SOURCE_SHA: \$\{\{ steps\.revision\.outputs\.sha \}\}/);
  assert.equal(
    [...summaryEnvironment.matchAll(/^\s+SOURCE_SHA:/gm)].length,
    1,
    'rendering summary has one source revision'
  );

  const summary = await readFile(new URL('../../scripts/site/render_performance_summary.mjs', import.meta.url), 'utf8');
  assert.match(summary, /performanceMeasurementExitCode/);
  assert.match(summary, /Checked-out revision:/);

  const paper = await readFile(new URL('../../.github/workflows/paper-build-integrity.yml', import.meta.url), 'utf8');
  assert.doesNotMatch(paper, /python3 -m unittest discover -s scripts -p 'test_paper_\*\.py' -v/);
  assert.match(paper, /python3 -m unittest discover -s tests\/paper -p 'test_config\.py' -v/);
  const codeQuality = await readFile(new URL('../../.github/workflows/common-code-quality.yml', import.meta.url), 'utf8');
  for (const testFile of ['test_release.py', 'test_publication_integrity.py']) {
    assert.ok(codeQuality.includes(`python3 -m unittest discover -s tests/paper -p '${testFile}' -v`));
  }
  assert.match(paper, /run: bash scripts\/build-paper-pdf\.sh/);
  assert.match(paper, /name: circle3-paper-preview/);
  assert.doesNotMatch(paper, /paper-content:|paper-results:/);
  const content = await readFile(new URL('../../.github/workflows/paper-content-integrity.yml', import.meta.url), 'utf8');
  assert.match(content, /run: bash scripts\/build-paper-pdf\.sh/);
  for (const workflow of [paper, content]) {
    assert.doesNotMatch(workflow, /^\s+ref:/m);
    assert.match(workflow, /git rev-parse HEAD/);
    assert.match(workflow, /SOURCE_SHA: \$\{\{ steps\.revision\.outputs\.sha \}\}/);
  }
  assert.match(content, /python3 -m scripts\.paper\.check_content scripts\/plop\.paper\.config\.yaml assets\/pdfs\/circle3-paper\.pdf/);
  assert.match(content, /value: \$\{\{ jobs\.paper-content-integrity\.outputs\.status \}\}/);
  assert.match(content, /PAPER_PDF_PATH: assets\/pdfs\/circle3-paper\.pdf/);
  assert.match(content, /if: \$\{\{ always\(\) && steps\.build\.outcome == 'success' \}\}/);
  assert.match(content, /name: paper-content-report/);
  assert.doesNotMatch(content, /download-artifact|name: circle3-paper-preview|paper-results:/);
  const link = await readFile(new URL('../../.github/workflows/paper-link-integrity.yml', import.meta.url), 'utf8');
  assert.match(link, /run: bash scripts\/build-paper-pdf\.sh/);
  assert.doesNotMatch(link, /^\s+ref:|download-artifact|paper-results:/m);
  assert.match(link, /SOURCE_SHA: \$\{\{ steps\.checkout\.outputs\.commit \}\}/);
  assert.match(link, /--require-hashes --only-binary=:all:/);
  assert.match(link, /unittest discover -s tests\/paper -p test_links\.py/);
  assert.match(link, /python" -m scripts\.paper\.check_links/);
  assert.match(link, /name: paper-link-report/);
  const rendering = await readFile(new URL('../../.github/workflows/paper-rendering-integrity.yml', import.meta.url), 'utf8');
  assert.match(rendering, /run: bash scripts\/build-paper-pdf\.sh/);
  assert.doesNotMatch(rendering, /^\s+ref:|download-artifact|name: circle3-paper-preview|paper-results:/m);
  assert.match(rendering, /git rev-parse HEAD/);
  assert.match(rendering, /SOURCE_SHA: \$\{\{ steps\.revision\.outputs\.sha \}\}/);
  assert.match(rendering, /unittest discover -s tests\/paper -p 'test_rendering\.py'/);
  assert.match(rendering, /python3 -m scripts\.paper\.check_rendering/);
  assert.match(rendering, /name: paper-rendering-report/);
  assert.match(rendering, /value: \$\{\{ jobs\.paper-rendering-integrity\.outputs\.status \}\}/);
  assert.match(codeQuality, /python3 -m unittest discover -s tests\/paper -p 'test_release\.py' -v/);
  assert.match(codeQuality, /id: invalid_permissions/);
  assert.match(codeQuality, /INVALID_PERMISSIONS_OUTCOME: \$\{\{ steps\.invalid_permissions\.outcome \}\}/);
  assert.match(codeQuality, /uses: jdx\/jactionlint@v2\.6\.0/);
  assert.match(codeQuality, /Workflow, publication, and operational regression contracts/);
  assert.match(codeQuality, /Invalid permission fixture rejected with the expected diagnostic/);
  assert.match(link, /BUILD_OUTCOME: \$\{\{ steps\.build\.outcome \}\}/);
  assert.match(link, /TOOLS_OUTCOME: \$\{\{ steps\.tools\.outcome \}\}/);
  assert.match(link, /# Paper: Link Integrity/);
  assert.match(link, /Overall verification: \*\*advisory\*\*/);
  assert.match(rendering, /REPORT_OUTCOME: \$\{\{ steps\.report\.outcome \}\}/);
  assert.match(rendering, /AUDIT_STATUS: \$\{\{ steps\.rendering\.outputs\.status \}\}/);
  assert.match(rendering, /Rendering report upload: \*\*passed\*\*/);
  assert.match(rendering, /Rendering audit: \*\*incomplete\*\*/);
  const siteBuild = await readFile(new URL('../../.github/workflows/site-build-integrity.yml', import.meta.url), 'utf8');
  assert.match(siteBuild, /test ! -e _site\/scripts/);
  assert.match(siteBuild, /test ! -e _site\/tests/);
  assert.match(siteBuild, /Repository changelog, scripts, and tests excluded from generated site/);
});

test('manual paper preview only builds and uploads the shared PDF', async () => {
  const yaml = await readFile(new URL('../../.github/workflows/paper-preview.yml', import.meta.url), 'utf8');
  assert.match(yaml, /run: bash scripts\/build-paper-pdf\.sh/);
  assert.match(yaml, /name: circle3-paper-preview/);
  assert.match(yaml, /actions\/upload-artifact@v7/);
  assert.doesNotMatch(yaml, /paper-build-integrity\.yml|check_paper_content|quality-pulse/);
});
