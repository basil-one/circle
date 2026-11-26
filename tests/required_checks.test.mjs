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
  ['paper-build-integrity.yml', 'paper-build', 'Paper: Build Integrity'],
  ['paper-content-integrity.yml', 'paper-content', 'Paper: Content Integrity'],
  ['paper-link-integrity.yml', 'paper-link', 'Paper: Link Integrity'],
  ['paper-rendering-integrity.yml', 'paper-rendering', 'Paper: Rendering Integrity']
];

const capabilities = [
  ...workflows.map(([file, , name]) => [name, `workflows/${file}`]),
  ['Paper: Publication Integrity', 'workflows/paper-publication-integrity.yml'],
  ['Site: Publish', 'workflows/site-publish.yml'],
  ['Paper: Preview', 'workflows/paper-preview.yml'],
  ['Paper: Publish', 'workflows/paper-publish.yml']
];

function assertQualityLinks(quality, files) {
  const section = quality.split('## Capabilities in Operation\n')[1]?.split('\n## ')[0];
  assert.ok(section, 'capability table exists');
  const rows = section.split('\n').filter((line) => /^\| (?!Capability \||---)/.test(line));
  const links = rows.map((row) => {
    const match = row.match(/^\| \[([^\]]+)\]\(([^)]+)\) \|/);
    assert.ok(match, `capability row must link a workflow: ${row}`);
    return [match[1], match[2]];
  });
  assert.equal(new Set(links.map(([name]) => name)).size, links.length, 'capability names are unique');
  assert.deepEqual([...links].sort(), [...capabilities].sort());
  for (const [name, path] of links) {
    assert.ok(files.has(path), `${path} exists`);
    const title = files.get(path).match(/^name:\s*["']?([^"'\n]+)["']?\s*$/m)?.[1];
    assert.equal(title, name, `${path} has the matching workflow name`);
  }
  assert.match(quality, /\[daily workflow\]\(workflows\/quality-pulse\.yml\)/);
  assert.match(files.get('workflows/quality-pulse.yml') ?? '', /^name: Quality Pulse$/m);
}

test('quality capability table links the current named workflows', async () => {
  const quality = await readFile(new URL('../.github/QUALITY.md', import.meta.url), 'utf8');
  const files = new Map(await Promise.all(
    [...capabilities.map(([, path]) => path), 'workflows/quality-pulse.yml'].map(async (path) =>
      [path, await readFile(new URL(`../.github/${path}`, import.meta.url), 'utf8')]
    )
  ));
  assertQualityLinks(quality, files);
  assert.throws(() => assertQualityLinks(quality.replace('workflows/paper-preview.yml', 'workflows/missing.yml'), files));
  assert.throws(() => assertQualityLinks(quality.replace('[Paper: Preview](workflows/paper-preview.yml)', 'Paper: Preview'), files));
  assert.throws(() => assertQualityLinks(quality.replace('[Paper: Preview](workflows/paper-preview.yml)', '[Paper: Publish](workflows/paper-preview.yml)'), files));
  const missing = new Map(files);
  missing.delete('workflows/paper-preview.yml');
  assert.throws(() => assertQualityLinks(quality, missing));
  const mismatched = new Map(files);
  mismatched.set('workflows/paper-preview.yml', 'name: "Wrong name"\n');
  assert.throws(() => assertQualityLinks(quality, mismatched));
  assert.throws(() => assertQualityLinks(quality.replace('workflows/quality-pulse.yml', 'workflows/missing.yml'), files));
});

test('required PR checks are emitted by their substantive workflow jobs', async () => {
  for (const [file, jobId, displayName] of workflows) {
    const yaml = await readFile(new URL(`../.github/workflows/${file}`, import.meta.url), 'utf8');
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
    if (jobId === 'dependency-review') {
      assert.equal(condition, "github.event_name == 'pull_request'");
    } else {
      assert.equal(condition, undefined, `${jobId} cannot be skipped by a job condition`);
    }
  }
});

test('all eleven current Common, Site, and Paper checks have substantive evidence semantics', async () => {
  assert.equal(workflows.length, 11);
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
  assert.doesNotMatch(paper, /python3 -m unittest discover -s scripts -p 'test_paper_\*\.py' -v/);
  assert.match(paper, /python3 -m unittest discover -s scripts -p 'test_paper_config\.py' -v/);
  const codeQuality = await readFile(new URL('../.github/workflows/common-code-quality.yml', import.meta.url), 'utf8');
  for (const testFile of ['test_paper_release.py', 'test_paper_publication_integrity.py']) {
    assert.ok(codeQuality.includes(`python3 -m unittest discover -s scripts -p '${testFile}' -v`));
  }
  assert.match(paper, /run: bash scripts\/build-paper-pdf\.sh/);
  assert.match(paper, /name: circle3-paper-preview/);
  assert.doesNotMatch(paper, /paper-content:|paper-results:/);
  const content = await readFile(new URL('../.github/workflows/paper-content-integrity.yml', import.meta.url), 'utf8');
  assert.match(content, /run: bash scripts\/build-paper-pdf\.sh/);
  for (const workflow of [paper, content]) {
    assert.doesNotMatch(workflow, /^\s+ref:/m);
    assert.match(workflow, /git rev-parse HEAD/);
    assert.match(workflow, /SOURCE_SHA: \$\{\{ steps\.revision\.outputs\.sha \}\}/);
  }
  assert.match(content, /python3 scripts\/check_paper_content\.py scripts\/plop\.paper\.config\.yaml assets\/pdfs\/circle3-paper\.pdf/);
  assert.match(content, /value: \$\{\{ jobs\.paper-content\.outputs\.status \}\}/);
  assert.match(content, /PAPER_PDF_PATH: assets\/pdfs\/circle3-paper\.pdf/);
  assert.match(content, /if: \$\{\{ always\(\) && steps\.build\.outcome == 'success' \}\}/);
  assert.match(content, /name: paper-content-report/);
  assert.doesNotMatch(content, /download-artifact|name: circle3-paper-preview|paper-results:/);
  const link = await readFile(new URL('../.github/workflows/paper-link-integrity.yml', import.meta.url), 'utf8');
  assert.match(link, /run: bash scripts\/build-paper-pdf\.sh/);
  assert.doesNotMatch(link, /^\s+ref:|download-artifact|paper-results:/m);
  assert.match(link, /SOURCE_SHA: \$\{\{ steps\.checkout\.outputs\.commit \}\}/);
  assert.match(link, /--require-hashes --only-binary=:all:/);
  assert.match(link, /unittest discover -s tests -p test_paper_links\.py/);
  assert.match(link, /python" scripts\/check_paper_links\.py/);
  assert.match(link, /name: paper-link-report/);
  const rendering = await readFile(new URL('../.github/workflows/paper-rendering-integrity.yml', import.meta.url), 'utf8');
  assert.match(rendering, /run: bash scripts\/build-paper-pdf\.sh/);
  assert.doesNotMatch(rendering, /^\s+ref:|download-artifact|name: circle3-paper-preview|paper-results:/m);
  assert.match(rendering, /git rev-parse HEAD/);
  assert.match(rendering, /SOURCE_SHA: \$\{\{ steps\.revision\.outputs\.sha \}\}/);
  assert.match(rendering, /unittest discover -s scripts -p 'test_paper_rendering\.py'/);
  assert.match(rendering, /python3 scripts\/check_paper_rendering\.py/);
  assert.match(rendering, /name: paper-rendering-report/);
  assert.match(rendering, /value: \$\{\{ jobs\.paper-rendering\.outputs\.status \}\}/);
  const common = await readFile(new URL('../.github/workflows/common-code-quality.yml', import.meta.url), 'utf8');
  assert.match(common, /python3 -m unittest discover -s scripts -p 'test_paper_release\.py' -v/);
});

test('manual paper preview only builds and uploads the shared PDF', async () => {
  const yaml = await readFile(new URL('../.github/workflows/paper-preview.yml', import.meta.url), 'utf8');
  assert.match(yaml, /run: bash scripts\/build-paper-pdf\.sh/);
  assert.match(yaml, /name: circle3-paper-preview/);
  assert.match(yaml, /actions\/upload-artifact@v6/);
  assert.doesNotMatch(yaml, /paper-build-integrity\.yml|check_paper_content|quality-pulse/);
});
