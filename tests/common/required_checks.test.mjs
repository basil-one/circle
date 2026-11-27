import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import test from 'node:test';

const repositoryRoot = fileURLToPath(new URL('../../', import.meta.url));

const workflows = [
  ['common-code-quality.yml', 'common-code-quality', 'Common: Code Quality'],
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
  const orderDiagnostic = capabilityOrderDiagnostic(links.map(([name]) => name));
  assert.equal(orderDiagnostic, undefined, orderDiagnostic);
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

function capabilityOrderDiagnostic(names) {
  const domainOrder = { Common: 0, Site: 1, Paper: 2 };
  const domains = names.map((name) => domainOrder[name.split(':', 1)[0]]);
  return domains.some((domain, index) => index > 0 && domain < domains[index - 1])
    ? 'QUALITY.md: capability rows must remain ordered Common → Site → Paper; move the out-of-order domain block.'
    : undefined;
}

function expectedJobIds(file) {
  if (file === 'common-dependency-security.yml') {
    return ['common-dependency-security-review', 'common-dependency-security-posture'];
  }
  if (file === 'site-publish.yml') {
    return ['site-publish-build', 'site-publish-deploy'];
  }
  return [file.replace(/\.yml$/, '')];
}

function jobIdDiagnostics(file, actualIds) {
  const expected = expectedJobIds(file);
  return JSON.stringify(actualIds) === JSON.stringify(expected)
    ? []
    : [`${file}: expected substantive job IDs ${expected.join(', ')}; use the filename stem for one job or qualified role IDs for Dependency Security and Site Publish.`];
}

function repositoryPathDiagnostics(source, contents) {
  const paths = [...contents.matchAll(/\b(?:scripts|tests)\/[A-Za-z0-9_./-]+\.(?:mjs|py|js|sh|yaml|txt)\b/g)]
    .map(([path]) => path);
  return [...new Set(paths)]
    .filter((path) => !existsSync(resolve(repositoryRoot, path)))
    .map((path) => `${source}: ${path} does not exist; update the invocation or restore the referenced file.`);
}

function testPlacementDiagnostic(path) {
  const [, parent] = path.match(/^tests\/([^/]+)\//) ?? [];
  return ['common', 'site', 'paper', 'orchestration'].includes(parent)
    ? undefined
    : `${path}: place automated tests under tests/common, tests/site, tests/paper, or tests/orchestration.`;
}

test('quality capability table links the current named workflows', async () => {
  const quality = await readFile(new URL('../../.github/QUALITY.md', import.meta.url), 'utf8');
  const files = new Map(await Promise.all(
    [...capabilities.map(([, path]) => path), 'workflows/quality-pulse.yml'].map(async (path) =>
      [path, await readFile(new URL(`../../.github/${path}`, import.meta.url), 'utf8')]
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

test('workflow IDs, script references, test placement, and developer navigation follow repository conventions', async () => {
  const workflowDirectory = new URL('../../.github/workflows/', import.meta.url);
  const workflowFiles = (await readdir(workflowDirectory)).filter((file) => file.endsWith('.yml'));
  const pathErrors = [];
  for (const file of workflowFiles) {
    const yaml = await readFile(new URL(file, workflowDirectory), 'utf8');
    if (file !== 'quality-pulse.yml') {
      const jobs = yaml.slice(yaml.indexOf('\njobs:\n') + '\njobs:\n'.length);
      const ids = [...jobs.matchAll(/^  ([a-z][a-z0-9-]*):\s*$/gm)].map(([, id]) => id);
      assert.deepEqual(jobIdDiagnostics(file, ids), [], `${file} uses its capability stem or qualified role IDs`);
    }
    pathErrors.push(...repositoryPathDiagnostics(file, yaml));
  }
  assert.deepEqual(pathErrors, [], pathErrors.join('\n'));
  assert.match(jobIdDiagnostics('site-build-integrity.yml', ['build'])[0], /site-build-integrity\.yml: expected substantive job IDs site-build-integrity/);
  assert.deepEqual(
    repositoryPathDiagnostics('fixture.yml', 'run: node scripts/site/missing.mjs'),
    ['fixture.yml: scripts/site/missing.mjs does not exist; update the invocation or restore the referenced file.']
  );
  assert.match(
    capabilityOrderDiagnostic(['Site: Build Integrity', 'Common: Code Quality']),
    /QUALITY\.md: capability rows must remain ordered Common → Site → Paper/
  );

  const packageJson = JSON.parse(await readFile(new URL('../../package.json', import.meta.url), 'utf8'));
  const packageErrors = repositoryPathDiagnostics('package.json', JSON.stringify(packageJson.scripts));
  assert.deepEqual(packageErrors, [], packageErrors.join('\n'));

  const testEntries = await readdir(new URL('../../tests/', import.meta.url), { withFileTypes: true });
  const misplacedTests = testEntries
    .filter((entry) => entry.isFile() && /(?:\.test\.mjs|\.spec\.js|^test_.*\.py)$/.test(entry.name))
    .map((entry) => `tests/${entry.name}`);
  assert.deepEqual(misplacedTests.map(testPlacementDiagnostic).filter(Boolean), []);
  assert.match(testPlacementDiagnostic('tests/test_orphan.test.mjs'), /tests\/common, tests\/site, tests\/paper, or tests\/orchestration/);

  const readme = await readFile(new URL('../../README.md', import.meta.url), 'utf8');
  const conventions = await readFile(new URL('../../.github/REPOSITORY_CONVENTIONS.md', import.meta.url), 'utf8');
  const jekyllConfig = await readFile(new URL('../../_config.yml', import.meta.url), 'utf8');
  const exclusions = jekyllConfig.match(/^exclude:\n((?:  - .+\n)*)/m)?.[1] ?? '';
  assert.match(readme, /\.github\/REPOSITORY_CONVENTIONS\.md/);
  assert.match(conventions, /Common → Site → Paper/);
  assert.match(exclusions, /^  - scripts$/m, 'supporting scripts stay out of the generated site');
  assert.match(exclusions, /^  - tests$/m, 'automated tests stay out of the generated site');
});

test('all eleven current Common, Site, and Paper checks have substantive evidence semantics', async () => {
  assert.equal(workflows.length, 11);
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

  const paper = await readFile(new URL('../../.github/workflows/paper-build-integrity.yml', import.meta.url), 'utf8');
  assert.doesNotMatch(paper, /python3 -m unittest discover -s scripts -p 'test_paper_\*\.py' -v/);
  assert.match(paper, /python3 -m unittest discover -s tests\/paper -p 'test_paper_config\.py' -v/);
  const codeQuality = await readFile(new URL('../../.github/workflows/common-code-quality.yml', import.meta.url), 'utf8');
  for (const testFile of ['test_paper_release.py', 'test_paper_publication_integrity.py']) {
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
  assert.match(link, /unittest discover -s tests\/paper -p test_paper_links\.py/);
  assert.match(link, /python" -m scripts\.paper\.check_links/);
  assert.match(link, /name: paper-link-report/);
  const rendering = await readFile(new URL('../../.github/workflows/paper-rendering-integrity.yml', import.meta.url), 'utf8');
  assert.match(rendering, /run: bash scripts\/build-paper-pdf\.sh/);
  assert.doesNotMatch(rendering, /^\s+ref:|download-artifact|name: circle3-paper-preview|paper-results:/m);
  assert.match(rendering, /git rev-parse HEAD/);
  assert.match(rendering, /SOURCE_SHA: \$\{\{ steps\.revision\.outputs\.sha \}\}/);
  assert.match(rendering, /unittest discover -s tests\/paper -p 'test_paper_rendering\.py'/);
  assert.match(rendering, /python3 -m scripts\.paper\.check_rendering/);
  assert.match(rendering, /name: paper-rendering-report/);
  assert.match(rendering, /value: \$\{\{ jobs\.paper-rendering-integrity\.outputs\.status \}\}/);
  const common = await readFile(new URL('../../.github/workflows/common-code-quality.yml', import.meta.url), 'utf8');
  assert.match(common, /python3 -m unittest discover -s tests\/paper -p 'test_paper_release\.py' -v/);
});

test('manual paper preview only builds and uploads the shared PDF', async () => {
  const yaml = await readFile(new URL('../../.github/workflows/paper-preview.yml', import.meta.url), 'utf8');
  assert.match(yaml, /run: bash scripts\/build-paper-pdf\.sh/);
  assert.match(yaml, /name: circle3-paper-preview/);
  assert.match(yaml, /actions\/upload-artifact@v6/);
  assert.doesNotMatch(yaml, /paper-build-integrity\.yml|check_paper_content|quality-pulse/);
});
