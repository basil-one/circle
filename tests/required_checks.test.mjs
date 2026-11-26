import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const workflows = [
  ['common-code-quality.yml', 'lint', 'Common: Code Quality'],
  ['common-dependency-security.yml', 'dependency-review', 'Common: Dependency Security'],
  ['site-build-integrity.yml', 'build', 'Site: Build Integrity'],
  ['site-link-integrity.yml', 'links', 'Site: Link Integrity'],
  ['site-seo-integrity.yml', 'seo', 'Site: SEO Integrity'],
  ['site-rendering-integrity.yml', 'browser-smoke', 'Site: Rendering Integrity'],
  ['site-performance-integrity.yml', 'lighthouse', 'Site: Performance Integrity'],
  ['paper-build-integrity.yml', 'paper-build', 'Paper: Build Integrity']
];

test('required PR checks are emitted by their substantive workflow jobs', async () => {
  for (const [file, jobId, displayName] of workflows) {
    const yaml = await readFile(new URL(`../.github/workflows/${file}`, import.meta.url), 'utf8');
    assert.match(yaml, /^  pull_request:\n    branches: \[main\]$/m, `${file} checks PRs targeting main`);
    assert.match(yaml, /^  workflow_dispatch:$/m, `${file} supports manual diagnostics`);
    assert.match(yaml, /^  workflow_call:$/m, `${file} can be called by Quality Pulse`);
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
    const nextHeader = jobHeaders.find(([, , offset]) => offset > header.index);
    const job = jobs.slice(header.index, nextHeader?.index ?? jobs.length);
    assert.match(job, new RegExp(`^    name: "${displayName.replaceAll(':', '\\:')}"$`, 'm'));
    assert.match(job, /^    steps:\s*$/m, `${jobId} has substantive steps`);
    assert.doesNotMatch(job, /^    needs:/m, `${jobId} is not a proxy job`);

    const condition = job.match(/^    if: (.+)$/m)?.[1];
    if (jobId === 'dependency-review') {
      assert.equal(condition, "github.event_name == 'pull_request'");
    } else {
      assert.equal(condition, undefined, `${jobId} cannot be skipped by a job condition`);
    }
  }
});

test('all eight Common, Site, and Paper checks are required and performance has substantive evidence semantics', async () => {
  const file = 'site-performance-integrity.yml';
  const yaml = await readFile(new URL(`../.github/workflows/${file}`, import.meta.url), 'utf8');
  assert.ok(workflows.some(([requiredFile]) => requiredFile === file));
  assert.match(yaml, /^  pull_request:\n    branches: \[main\]$/m);
  assert.match(yaml, /^  workflow_dispatch:$/m);
  assert.match(yaml, /^  workflow_call:\n    inputs:\n      caller_event_name:/m);
  assert.doesNotMatch(yaml, /^  schedule:$/m);
  assert.match(yaml, /^  lighthouse:\n    name: "Site: Performance Integrity"$/m);
  assert.match(yaml, /^      - name: Run performance classifier and history tests\n        id: tests\n        run: npm run test:performance-history/m);
  assert.match(yaml, /^      - name: Classify pull request changes for Lighthouse\n        id: classify\n        if: github\.event_name == 'pull_request'\n        continue-on-error: true/m);
  assert.match(yaml, /^      - name: Measure rendered pages\n        id: measure\n        if: .+\n        run: npm run measure:performance/m);
  assert.match(yaml, /steps\.classify\.outcome == 'failure' \|\| steps\.classify\.outputs\.skip_measurement != 'true'/);
  assert.match(yaml, /^      - name: Render Performance Integrity summary\n        if: always\(\)/m);
  assert.match(yaml, /node scripts\/render_site_performance_summary\.mjs/);
  assert.match(yaml, /STEP_TESTS: \$\{\{ steps\.tests\.outcome \}\}/);
  assert.match(yaml, /STEP_CLASSIFY: \$\{\{ steps\.classify\.outcome \}\}/);
  assert.match(yaml, /STEP_MEASURE: \$\{\{ steps\.measure\.outcome \}\}/);
  const pulse = await readFile(new URL('../.github/workflows/quality-pulse.yml', import.meta.url), 'utf8');
  assert.match(pulse, /performance:\n    name: "Site: Performance Integrity"\n    uses: \.\/\.github\/workflows\/site-performance-integrity\.yml\n    with:\n      caller_event_name: \$\{\{ github\.event_name \}\}/);

  const summary = await readFile(new URL('../scripts/render_site_performance_summary.mjs', import.meta.url), 'utf8');
  assert.match(summary, /performanceMeasurementExitCode/);

  const paper = await readFile(new URL('../.github/workflows/paper-build-integrity.yml', import.meta.url), 'utf8');
  assert.match(paper, /python3 -m unittest discover -s scripts -p 'test_paper_\*\.py' -v/);
  assert.match(paper, /run: bash scripts\/build-paper-pdf\.sh/);
  assert.match(paper, /name: circle3-paper-preview/);
});
