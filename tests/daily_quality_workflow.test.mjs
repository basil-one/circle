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
  ['paper-link-integrity.yml', 'paper-links']
];

test('one daily workflow owns the schedule and fans out to all nine reusable checks', async () => {
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
    assert.match(child, /^  workflow_call:$/m, `${file} can be called`);
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
