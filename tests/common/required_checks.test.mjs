import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { readdir, readFile } from 'node:fs/promises';
import { readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, relative, resolve, sep } from 'node:path';
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
  ['Common: Code Quality', 'workflows/common-code-quality.yml'],
  ['Common: Dependency Security', 'workflows/common-dependency-security.yml'],
  ['Site: Build Integrity', 'workflows/site-build-integrity.yml'],
  ['Site: Link Integrity', 'workflows/site-link-integrity.yml'],
  ['Site: SEO Integrity', 'workflows/site-seo-integrity.yml'],
  ['Site: Rendering Integrity', 'workflows/site-rendering-integrity.yml'],
  ['Site: Performance Integrity', 'workflows/site-performance-integrity.yml'],
  ['Site: Publish', 'workflows/site-publish.yml'],
  ['Paper: Build Integrity', 'workflows/paper-build-integrity.yml'],
  ['Paper: Content Integrity', 'workflows/paper-content-integrity.yml'],
  ['Paper: Link Integrity', 'workflows/paper-link-integrity.yml'],
  ['Paper: Rendering Integrity', 'workflows/paper-rendering-integrity.yml'],
  ['Paper: Publication Integrity', 'workflows/paper-publication-integrity.yml'],
  ['Paper: Preview', 'workflows/paper-preview.yml'],
  ['Paper: Publish', 'workflows/paper-publish.yml']
];

const domains = new Set(['common', 'site', 'paper', 'orchestration']);
const rootScriptExceptions = new Set([
  'scripts/build-paper-pdf.sh',
  'scripts/build-pdf.py',
  'scripts/paper_release.py'
]);
const testSupportExceptions = new Set([
  'tests/site/browser/accessibility.js',
  'tests/site/browser/responsive.js'
]);

function capabilityOrderDiagnostic(links) {
  const expected = capabilities.map(([name]) => name);
  const names = links.map(([name]) => name);
  const mismatch = names.findIndex((name, index) => name !== expected[index]);
  return names.length === expected.length && mismatch === -1
    ? undefined
    : `QUALITY.md: capability row ${Math.max(mismatch, 0) + 1} must be ${expected[Math.max(mismatch, 0)] ?? 'removed'} in canonical Common → Site → Paper order; actual inventory or order differs.`;
}

function assertQualityLinks(quality, files) {
  const section = quality.split('## Capabilities in Operation\n')[1]?.split('\n## ')[0];
  assert.ok(section, 'capability table exists');
  const rows = section.split('\n').filter((line) => /^\| (?!Capability \||---)/.test(line));
  const links = rows.map((row) => {
    const match = row.match(/^\| \[([^\]]+)\]\(([^)]+)\) \|/);
    assert.ok(match, `capability row must link a workflow: ${row}`);
    return [match[1], match[2]];
  });
  const orderDiagnostic = capabilityOrderDiagnostic(links);
  assert.equal(orderDiagnostic, undefined, orderDiagnostic);
  assert.equal(new Set(links.map(([name]) => name)).size, links.length, 'capability names are unique');
  assert.deepEqual(links, capabilities);
  for (const [name, path] of links) {
    assert.ok(files.has(path), `${path} exists`);
    const title = files.get(path).match(/^name:\s*["']?([^"'\n]+)["']?\s*$/m)?.[1];
    assert.equal(title, name, `${path} has the matching workflow name`);
  }
  assert.match(quality, /\[daily workflow\]\(workflows\/quality-pulse\.yml\)/);
  assert.match(files.get('workflows/quality-pulse.yml') ?? '', /^name: Quality Pulse$/m);
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

function repositoryFiles(directory) {
  const root = resolve(repositoryRoot, directory);
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = `${directory}/${entry.name}`;
    return entry.isDirectory() ? repositoryFiles(path) : [path];
  });
}

function globRegex(pattern) {
  return new RegExp(`^${pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replaceAll('*', '.*').replaceAll('?', '.')}$`);
}

function referencedPath(source, path) {
  const cleanPath = path.replace(/[),;:]+$/, '');
  if (cleanPath.startsWith('scripts/') || cleanPath.startsWith('tests/')) {
    return cleanPath;
  }
  if (cleanPath.startsWith('./') || cleanPath.startsWith('../')) {
    return relative(repositoryRoot, resolve(repositoryRoot, dirname(source), cleanPath)).split(sep).join('/');
  }
  return undefined;
}

function repositoryPathDiagnostics(source, contents) {
  const errors = [];
  const addPath = (rawPath) => {
    const path = referencedPath(source, rawPath);
    if (path?.includes('*') || path?.includes('?')) {
      const root = path.startsWith('scripts/') ? 'scripts' : path.startsWith('tests/') ? 'tests' : undefined;
      if (root && !repositoryFiles(root).some((candidate) => globRegex(path).test(candidate))) {
        errors.push(`${source}: ${path} matches no repository files; correct the documented path or pattern.`);
      }
      return;
    }
    if (path && !existsSync(resolve(repositoryRoot, path))) {
      errors.push(`${source}: ${path} does not exist; update the invocation or restore the referenced file.`);
    }
  };
  for (const [path] of contents.matchAll(/\b(?:scripts|tests)\/[A-Za-z0-9_./*-]+/g)) {
    addPath(path);
  }
  for (const [, moduleName] of contents.matchAll(/\bpython(?:3)?\s+-m\s+(scripts(?:\.[A-Za-z_]\w*)+)\b/g)) {
    const modulePath = `${moduleName.replaceAll('.', '/')}.py`;
    if (!existsSync(resolve(repositoryRoot, modulePath))) {
      errors.push(`${source}: Python module ${moduleName} does not resolve to ${modulePath}; correct the -m invocation.`);
    }
  }
  const packageScripts = JSON.parse(readFileSync(resolve(repositoryRoot, 'package.json'), 'utf8')).scripts;
  for (const [, command] of contents.matchAll(/\bnpm\s+(?:run|run-script)\s+([a-z0-9:-]+)/gi)) {
    if (!packageScripts[command]) {
      errors.push(`${source}: npm run ${command} is not defined in package.json; add the script or correct the invocation.`);
    }
  }
  for (const [, , start, , pattern] of contents.matchAll(
    /\bunittest\s+discover\s+-s\s+(['"]?)([^'"\s]+)\1\s+-p\s+(['"]?)([^'"\s]+)\3/g
  )) {
    const startPath = start;
    if (!existsSync(resolve(repositoryRoot, startPath))) {
      errors.push(`${source}: unittest discovery start ${startPath} does not exist; use a tests/<domain> directory.`);
      continue;
    }
    const matches = repositoryFiles(startPath).filter((path) => {
      const relativePath = path.slice(startPath.length + 1);
      return !relativePath.includes('/') && globRegex(pattern).test(relativePath);
    });
    if (matches.length === 0) {
      errors.push(`${source}: unittest discovery ${startPath} with pattern ${pattern} matches no tests; correct the directory or pattern.`);
    }
  }
  return [...new Set(errors)];
}

function isTestFile(path) {
  return /(?:\.test\.mjs|\.spec\.js|^test_.*\.py)$/.test(path.split('/').at(-1));
}

function testNamingDiagnostic(path) {
  const filename = path.split('/').at(-1);
  const valid =
    (/^[a-z][a-z0-9_]*\.test\.mjs$/.test(filename) ||
      /^[a-z][a-z0-9_-]*\.spec\.js$/.test(filename) ||
      /^test_[a-z][a-z0-9_]*\.py$/.test(filename)) &&
    !/^(?:site|paper|common|orchestration)_/.test(filename) &&
    !/^test_(?:site|paper|common|orchestration)_/.test(filename);
  return valid
    ? undefined
    : `${path}: use a lowercase domain-neutral test name (*.test.mjs, *.spec.js, or test_*.py); the parent directory identifies ownership.`;
}

function placementDiagnostics(files) {
  const errors = [];
  for (const path of files) {
    const parts = path.split('/');
    if (path.startsWith('scripts/') && /\.(?:mjs|py|js|sh)$/.test(path)) {
      const expectedDomain = parts[1];
      if (rootScriptExceptions.has(path)) continue;
      if (!domains.has(expectedDomain) || parts.length < 3) {
        errors.push(`${path}: place production code in scripts/{common,site,paper,orchestration}/; only established build/release entry points stay at scripts/.`);
      } else if (!/^[a-z][a-z0-9_]*\.(?:mjs|py|js|sh)$/.test(parts.at(-1))) {
        errors.push(`${path}: use a lowercase snake_case production-script filename within its domain.`);
      }
    }
    if (path.startsWith('tests/') && /\.(?:mjs|py|js)$/.test(path) && !isTestFile(path) && !testSupportExceptions.has(path)) {
      errors.push(`${path}: test sources must follow the test naming convention; browser helpers are bounded to tests/site/browser/.`);
    }
    if (isTestFile(path)) {
      const [, domain, nested] = path.match(/^tests\/([^/]+)\/(?:(browser)\/)?([^/]+)$/) ?? [];
      if (!domains.has(domain) || (nested === 'browser' && domain !== 'site')) {
        errors.push(`${path}: place tests under tests/{common,site,paper,orchestration}/; browser tests belong under tests/site/browser/.`);
      }
      const namingError = testNamingDiagnostic(path);
      if (namingError) errors.push(namingError);
    }
  }
  return errors;
}

function boundaryDiagnostics(files, sourceFor = (path) => readFileSync(resolve(repositoryRoot, path), 'utf8')) {
  const errors = [];
  for (const path of files.filter((file) => file.startsWith('scripts/') && /\.(?:mjs|py|js)$/.test(file))) {
    const domain = path.split('/')[1];
    const contents = sourceFor(path);
    if (/\bfrom\s+tests(?:\.|\s+import)|\bimport\s+tests(?:\.|['"])/.test(contents)) {
      errors.push(`${path}: production scripts cannot import test code; move shared support into scripts/<domain> or scripts/common.`);
    }
    const relativeImports = [...contents.matchAll(/from\s+['"]([^'"]+)['"]/g)]
      .map(([, imported]) => {
        if (!imported.startsWith('.')) return undefined;
        return relative(repositoryRoot, resolve(repositoryRoot, dirname(path), imported)).split(sep);
      })
      .filter(Boolean);
    if (relativeImports.some(([first]) => first === 'tests')) {
      errors.push(`${path}: production scripts cannot import test code; move shared support into scripts/<domain> or scripts/common.`);
    }
    const importedDomains = new Set([
      ...[...contents.matchAll(/\b(?:from|import)\s+scripts\.(common|site|paper|orchestration)\b/g)].map(([, imported]) => imported),
      ...relativeImports.map((relativeTarget) => relativeTarget[0] === 'scripts' ? relativeTarget[1] : undefined).filter(Boolean)
    ]);
    const allowed = {
      common: new Set(['common']),
      site: new Set(['common', 'site']),
      paper: new Set(['common', 'paper']),
      orchestration: domains
    }[domain] ?? new Set();
    for (const imported of importedDomains) {
      if (!allowed.has(imported)) {
        errors.push(`${path}: ${domain} production code cannot depend on scripts/${imported}; put shared support in scripts/common or keep it within its owning domain.`);
      }
    }
  }
  return errors;
}

function markdownLinkDiagnostics(source, contents) {
  const errors = [];
  for (const [, target] of contents.matchAll(/!?\[[^\]]*\]\(([^)\s]+)(?:\s+["'][^)]*["'])?\)/g)) {
    if (/^(?:[a-z][a-z0-9+.-]*:|#)/i.test(target)) continue;
    const path = target.split('#', 1)[0];
    if (!path) continue;
    const resolved = resolve(repositoryRoot, dirname(source), path);
    if (!existsSync(resolved)) {
      errors.push(`${source}: documentation link ${target} does not resolve; correct the local path or remove the broken link.`);
    }
  }
  return errors;
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
  const wrongWithinDomainOrder = quality.replace(
    '[Site: Link Integrity](workflows/site-link-integrity.yml)',
    '[Site: SEO Integrity](workflows/site-seo-integrity.yml)'
  ).replace(
    '[Site: SEO Integrity](workflows/site-seo-integrity.yml) | Page titles',
    '[Site: Link Integrity](workflows/site-link-integrity.yml) | Page titles'
  );
  assert.throws(() => assertQualityLinks(wrongWithinDomainOrder, files));
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
  const repositoryCodeFiles = [...repositoryFiles('scripts'), ...repositoryFiles('tests')];
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
    repositoryPathDiagnostics('fixture.yml', 'run: python3 -m scripts.paper.missing_check')[0],
    /Python module scripts\.paper\.missing_check does not resolve/
  );
  assert.match(
    repositoryPathDiagnostics('fixture.yml', "run: python3 -m unittest discover -s tests/paper -p 'test_missing_*.py'")[0],
    /unittest discovery tests\/paper with pattern test_missing_\*\.py matches no tests/
  );
  assert.match(
    repositoryPathDiagnostics('fixture.yml', 'run: npm run missing-contract-test')[0],
    /npm run missing-contract-test is not defined in package\.json/
  );
  assert.match(
    placementDiagnostics(['scripts/site/test_misplaced.py', 'tests/site/nested/test_misplaced.py']).join('\n'),
    /place tests under tests\/{common,site,paper,orchestration}/
  );
  assert.match(
    placementDiagnostics(['scripts/extra/misplaced.py']).join('\n'),
    /place production code in scripts\/{common,site,paper,orchestration}/
  );
  assert.match(
    placementDiagnostics(['scripts/site/Bad-Name.mjs']).join('\n'),
    /lowercase snake_case production-script filename/
  );
  assert.match(
    placementDiagnostics(['tests/site/site_routes.test.mjs', 'tests/site/misnamed.js']).join('\n'),
    /test sources must follow the test naming convention|lowercase domain-neutral test name/
  );
  assert.match(
    markdownLinkDiagnostics('README.md', '[broken link](scripts/missing.md)')[0],
    /documentation link scripts\/missing\.md does not resolve/
  );
  assert.match(
    boundaryDiagnostics(['scripts/site/bad_import.mjs'], () => "import { x } from '../../tests/site/helper.js';")[0],
    /production scripts cannot import test code/
  );

  const packageJson = JSON.parse(await readFile(new URL('../../package.json', import.meta.url), 'utf8'));
  const packageErrors = repositoryPathDiagnostics('package.json', JSON.stringify(packageJson.scripts));
  assert.deepEqual(packageErrors, [], packageErrors.join('\n'));
  const misplaced = placementDiagnostics(repositoryCodeFiles);
  const boundaries = boundaryDiagnostics(repositoryCodeFiles);
  assert.deepEqual([...misplaced, ...boundaries], [], [...misplaced, ...boundaries].join('\n'));

  const readme = await readFile(new URL('../../README.md', import.meta.url), 'utf8');
  const conventions = await readFile(new URL('../../.github/REPOSITORY_CONVENTIONS.md', import.meta.url), 'utf8');
  const scriptsReadme = await readFile(new URL('../../scripts/README.md', import.meta.url), 'utf8');
  const documentationPathErrors = [
    ['README.md', readme],
    ['.github/REPOSITORY_CONVENTIONS.md', conventions],
    ['scripts/README.md', scriptsReadme]
  ].flatMap(([source, contents]) => repositoryPathDiagnostics(source, contents));
  assert.deepEqual(documentationPathErrors, [], documentationPathErrors.join('\n'));
  const jekyllConfig = await readFile(new URL('../../_config.yml', import.meta.url), 'utf8');
  const exclusions = jekyllConfig.match(/^exclude:\n((?:  - .+\n)*)/m)?.[1] ?? '';
  assert.match(readme, /\.github\/REPOSITORY_CONVENTIONS\.md/);
  assert.match(conventions, /Common → Site → Paper/);
  assert.deepEqual([
    ...markdownLinkDiagnostics('README.md', readme),
    ...markdownLinkDiagnostics('.github/REPOSITORY_CONVENTIONS.md', conventions),
    ...markdownLinkDiagnostics('scripts/README.md', scriptsReadme)
  ], []);
  assert.match(scriptsReadme, /\.github\/QUALITY\.md/);
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
  const common = await readFile(new URL('../../.github/workflows/common-code-quality.yml', import.meta.url), 'utf8');
  assert.match(common, /python3 -m unittest discover -s tests\/paper -p 'test_release\.py' -v/);
  assert.match(common, /id: invalid_permissions/);
  assert.match(common, /INVALID_PERMISSIONS_OUTCOME: \$\{\{ steps\.invalid_permissions\.outcome \}\}/);
  assert.match(common, /Coherence, workflow, and repository contracts/);
  assert.match(common, /Invalid permission fixture rejected with the expected diagnostic/);
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
  assert.match(yaml, /actions\/upload-artifact@v6/);
  assert.doesNotMatch(yaml, /paper-build-integrity\.yml|check_paper_content|quality-pulse/);
});
