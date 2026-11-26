import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import test from 'node:test';

const workflowDirectory = new URL('../.github/workflows/', import.meta.url);
const children = [
  ['site-build-integrity.yml', 'site-build'],
  ['site-link-integrity.yml', 'link'],
  ['site-seo-integrity.yml', 'seo'],
  ['common-code-quality.yml', 'code-quality'],
  ['common-dependency-security.yml', 'dependency-security'],
  ['site-rendering-integrity.yml', 'rendering'],
  ['site-performance-integrity.yml', 'performance'],
  ['paper-build-integrity.yml', 'paper'],
  ['paper-content-integrity.yml', 'paper-content'],
  ['paper-link-integrity.yml', 'paper-link'],
  ['paper-rendering-integrity.yml', 'paper-rendering'],
  ['paper-publication-integrity.yml', 'paper-publication']
];

test('one daily workflow owns the schedule and calls all twelve reusable checks', async () => {
  const files = await readdir(workflowDirectory);
  const scheduled = [];
  for (const file of files.filter((name) => name.endsWith('.yml'))) {
    const yaml = await readFile(new URL(file, workflowDirectory), 'utf8');
    if (/^  schedule:/m.test(yaml)) scheduled.push(file);
  }
  assert.deepEqual(scheduled, ['quality-pulse.yml']);

  const orchestrator = await readFile(new URL('quality-pulse.yml', workflowDirectory), 'utf8');
  assert.match(orchestrator, /cron: '17 3 \* \* \*'/);
  assert.match(orchestrator, /^  workflow_dispatch:$/m);
  for (const [file, jobId] of children) {
    const child = await readFile(new URL(file, workflowDirectory), 'utf8');
    assert.match(child, /^  workflow_call:\s*$/m, `${file} can be called`);
    assert.doesNotMatch(child, /^  schedule:$/m, `${file} has no independent daily schedule`);
    assert.match(orchestrator, new RegExp(`^  ${jobId}:\\n    name: .+\\n    uses: \\.\\/\\.github\\/workflows\\/${file}$`, 'm'));
  }
});

test('Pulse waits for every substantive result and preserves least-privilege permissions', async () => {
  const orchestrator = await readFile(new URL('quality-pulse.yml', workflowDirectory), 'utf8');
  const pulse = orchestrator.slice(orchestrator.indexOf('\n  pulse:\n'));
  assert.match(pulse, /if: \$\{\{ always\(\) && !cancelled\(\) \}\}/);
  for (const [, jobId] of children) {
    assert.match(pulse, new RegExp(`^      - ${jobId}$`, 'm'));
  }
  assert.match(pulse, /PULSE_RESULTS_JSON: \$\{\{ toJSON\(needs\) \}\}/);
  assert.match(pulse, /actions: read/);
  assert.match(pulse, /contents: read/);
  assert.match(orchestrator, /dependency-security:\n    name: "Common: Dependency Security"\n    uses: \.\/\.github\/workflows\/common-dependency-security\.yml\n    permissions:\n      contents: read\n      vulnerability-alerts: read/);
  assert.match(orchestrator, /performance:\n    name: "Site: Performance Integrity"\n    uses: \.\/\.github\/workflows\/site-performance-integrity\.yml\n    with:\n      caller_event_name: \$\{\{ github\.event_name \}\}\n    permissions:\n      actions: read\n      contents: read/);
  assert.match(orchestrator, /group: daily-quality-\$\{\{ github\.ref \}\}\n  cancel-in-progress: false/);
  for (const [file, group] of [
    ['site-performance-integrity.yml', 'site-performance-integrity'],
    ['site-rendering-integrity.yml', 'site-rendering-integrity']
  ]) {
    const yaml = await readFile(new URL(file, workflowDirectory), 'utf8');
    assert.match(yaml, new RegExp(
      `group: ${group}-\\$\\{\\{ github\\.workflow \\}\\}-\\$\\{\\{ github\\.ref \\}\\}\\n  cancel-in-progress: true`
    ));
  }
});

test('publication audit is read-only, independently callable, and preserves its evidence outputs', async () => {
  const yaml = await readFile(new URL('paper-publication-integrity.yml', workflowDirectory), 'utf8');
  assert.match(yaml, /^  workflow_dispatch:$/m);
  assert.doesNotMatch(yaml, /^  pull_request:$/m);
  assert.match(yaml, /name: "Paper: Publication Integrity"/);
  assert.match(yaml, /permissions:\n  contents: read/);
  assert.doesNotMatch(yaml, /^\s+ref:/m);
  assert.match(yaml, /validator_sha: \$\{\{ steps\.checkout\.outputs\.commit \}\}/);
  assert.match(yaml, /fetch-depth: 0\n          persist-credentials: false/);
  assert.match(yaml, /refs\/heads\/main:refs\/remotes\/origin\/main/);
  assert.match(yaml, /python3 scripts\/paper_publication_integrity\.py/);
  for (const output of ['status', 'detail', 'main_sha', 'validator_sha']) {
    assert.match(yaml, new RegExp(`value: \\$\\{\\{ jobs\\.publication\\.outputs\\.${output} \\}\\}`));
  }
  assert.match(yaml, /test "\$STATUS" = passed/);
  assert.doesNotMatch(yaml, /build-paper-pdf|upload-artifact|contents: write|ref: main\n/);
});

test('Performance Integrity confirms only affected profiles and enforces the final result', async () => {
  const performance = await readFile(
    new URL('site-performance-integrity.yml', workflowDirectory),
    'utf8'
  );
  assert.match(performance, /name: "Site: Performance Integrity"/);
  assert.match(performance, /id: comparison[\s\S]*confirmation_required/);
  assert.match(performance, /name: Confirm initially violating route profiles/);
  assert.match(performance, /PERFORMANCE_PROFILES_JSON: \$\{\{ steps\.comparison\.outputs\.confirmation_profiles \}\}/);
  assert.match(performance, /PERFORMANCE_REPORT_DIR: \$\{\{ runner\.temp \}\}\/performance-confirmation-reports/);
  assert.match(performance, /node scripts\/compare_site_performance\.mjs --finalize/);
  assert.match(performance, /name: performance-confirmation-reports/);
  assert.match(performance, /performance-reports\/performance-summary\.json[\s\S]*performance-reports\/performance-comparison\.json/);
  assert.doesNotMatch(performance, /performance-confirmation-reports\/performance-summary\.json[\s\S]*name: performance-summary/);
});
