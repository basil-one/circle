import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { readdir, readFile } from 'node:fs/promises';
import { readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, relative, resolve, sep } from 'node:path';
import test from 'node:test';

const repositoryRoot = fileURLToPath(new URL('../../', import.meta.url));

const capabilities = [
  ['Common: Code Quality', 'workflows/common-code-quality.yml'],
  ['Common: Coherence Integrity', 'workflows/common-coherence-integrity.yml'],
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
  ['Release Publish', 'workflows/release-publish.yml']
];

const domains = new Set(['common', 'site', 'paper', 'orchestration']);
const rootScriptExceptions = new Set([
  'scripts/build-paper-pdf.sh',
  'scripts/build-pdf.py'
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

function workflowInventoryDiagnostics(actualFiles) {
  const expectedFiles = new Set([
    ...capabilities.map(([, path]) => `.github/${path}`),
    '.github/workflows/quality-pulse.yml'
  ]);
  const actual = new Set(actualFiles);
  const errors = [];
  for (const path of [...actual].filter((file) => !expectedFiles.has(file)).sort()) {
    errors.push(`${path}: unregistered workflow; add it to the canonical capability inventory or remove it.`);
  }
  for (const path of [...expectedFiles].filter((file) => !actual.has(file)).sort()) {
    errors.push(`${path}: canonical workflow is missing; restore it or update the capability inventory.`);
  }
  return errors;
}

function coherenceCommandDiagnostics(workflows, packageScripts) {
  const expectedScript = 'node --test tests/common/coherence_integrity.test.mjs';
  const command = 'npm run test:coherence-integrity';
  const errors = [];
  if (packageScripts['test:coherence-integrity'] !== expectedScript) {
    errors.push(`package.json: test:coherence-integrity must run ${expectedScript}.`);
  }
  const owners = workflows
    .filter(([, contents]) => contents.includes(command))
    .map(([file]) => file);
  if (owners.length !== 1 || owners[0] !== 'common-coherence-integrity.yml') {
    errors.push(
      `${command} must be invoked only by common-coherence-integrity.yml; found ${owners.join(', ') || 'no workflow'}.`
    );
  }
  return errors;
}

function externalActionVersionDiagnostics(workflows) {
  const references = new Map();
  for (const [file, contents] of workflows) {
    for (const match of contents.matchAll(/^\s*uses:\s+([^\s#]+)@([^\s#]+)/gm)) {
      const [, action, version] = match;
      if (action.startsWith('./') || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_./-]+$/.test(action)) continue;
      const line = contents.slice(0, match.index).split('\n').length;
      const found = references.get(action) ?? [];
      found.push({ file, line, version });
      references.set(action, found);
    }
  }
  return [...references]
    .filter(([, found]) => new Set(found.map(({ version }) => version)).size > 1)
    .map(([action, found]) => {
      const details = found.map(({ file, line, version }) => `${file}:${line} @${version}`).join(', ');
      return `${action}: conflicting workflow refs (${details}); align all workflows on one release version.`;
    });
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

function isPythonTestFile(path) {
  return /^test_[a-z][a-z0-9_]*\.py$/.test(path.split('/').at(-1));
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
  const moduleInvocation =
    /(?:["'](?:[^"'\n]*\/)?python(?:3)?["']|(?:[^\s"'=]*\/)?python(?:3)?)\s+-m\s+(scripts(?:\.[A-Za-z_]\w*)+)\b/g;
  for (const [, moduleName] of contents.matchAll(moduleInvocation)) {
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
    if (!existsSync(resolve(repositoryRoot, start))) {
      errors.push(`${source}: unittest discovery start ${start} does not exist; use a tests/<domain> directory.`);
      continue;
    }
    const matches = repositoryFiles(start).filter((path) => {
      const relativePath = path.slice(start.length + 1);
      return !relativePath.includes('/') && isPythonTestFile(relativePath) && globRegex(pattern).test(relativePath);
    });
    if (matches.length === 0) {
      errors.push(`${source}: unittest discovery ${start} with pattern ${pattern} matches no tests; correct the directory or pattern.`);
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

test('quality capability table links the canonical workflow inventory', async () => {
  const quality = await readFile(new URL('../../.github/QUALITY.md', import.meta.url), 'utf8');
  const files = new Map(await Promise.all(
    [...capabilities.map(([, path]) => path), 'workflows/quality-pulse.yml'].map(async (path) =>
      [path, await readFile(new URL(`../../.github/${path}`, import.meta.url), 'utf8')]
    )
  ));
  assertQualityLinks(quality, files);
  assert.doesNotMatch(
    quality,
    /\[Common: Code Quality\][^\n]*coherence integrity/i,
    'coherence has independent capability ownership'
  );
  assert.throws(() => assertQualityLinks(quality.replace('workflows/paper-preview.yml', 'workflows/missing.yml'), files));
  assert.throws(() => assertQualityLinks(quality.replace('[Paper: Preview](workflows/paper-preview.yml)', 'Paper: Preview'), files));
  assert.throws(() => assertQualityLinks(quality.replace('[Paper: Preview](workflows/paper-preview.yml)', '[Release Publish](workflows/paper-preview.yml)'), files));
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

test('repository architecture, placement, boundaries, and references are enforced', async () => {
  const workflowDirectory = new URL('../../.github/workflows/', import.meta.url);
  const workflowFiles = (await readdir(workflowDirectory))
    .filter((file) => /\.(?:yml|yaml)$/.test(file))
    .sort();
  const workflowInventory = workflowFiles.map((file) => `.github/workflows/${file}`);
  assert.deepEqual(workflowInventoryDiagnostics(workflowInventory), []);
  assert.match(
    workflowInventoryDiagnostics([...workflowInventory, '.github/workflows/unregistered-check.yaml'])[0],
    /\.github\/workflows\/unregistered-check\.yaml: unregistered workflow; add it to the canonical capability inventory or remove it\./
  );
  const pathErrors = [];
  const repositoryCodeFiles = [...repositoryFiles('scripts'), ...repositoryFiles('tests')];
  const workflowContents = [];
  for (const file of workflowFiles) {
    const yaml = await readFile(new URL(file, workflowDirectory), 'utf8');
    workflowContents.push([file, yaml]);
    if (file !== 'quality-pulse.yml') {
      const jobs = yaml.slice(yaml.indexOf('\njobs:\n') + '\njobs:\n'.length);
      const ids = [...jobs.matchAll(/^  ([a-z][a-z0-9-]*):\s*$/gm)].map(([, id]) => id);
      assert.deepEqual(jobIdDiagnostics(file, ids), [], `${file} uses its capability stem or qualified role IDs`);
    }
    pathErrors.push(...repositoryPathDiagnostics(file, yaml));
  }
  assert.deepEqual(pathErrors, [], pathErrors.join('\n'));
  const actionReferences = workflowContents.map(([file, contents]) => [
    `.github/workflows/${file}`,
    contents
  ]);
  assert.deepEqual(externalActionVersionDiagnostics(actionReferences), []);
  assert.match(
    externalActionVersionDiagnostics([
      ['.github/workflows/common-code-quality.yml', 'uses: actions/upload-artifact@v6\n'],
      ['.github/workflows/paper-preview.yml', 'uses: actions/upload-artifact@v7\n']
    ])[0],
    /actions\/upload-artifact: conflicting workflow refs \(.*common-code-quality\.yml:1 @v6, .*paper-preview\.yml:1 @v7\); align all workflows on one release version/
  );
  assert.match(jobIdDiagnostics('site-build-integrity.yml', ['build'])[0], /site-build-integrity\.yml: expected substantive job IDs site-build-integrity/);
  assert.deepEqual(
    repositoryPathDiagnostics('fixture.yml', 'run: node scripts/site/missing.mjs'),
    ['fixture.yml: scripts/site/missing.mjs does not exist; update the invocation or restore the referenced file.']
  );
  assert.match(
    repositoryPathDiagnostics('fixture.yml', 'run: python3 -m scripts.paper.missing_check')[0],
    /Python module scripts\.paper\.missing_check does not resolve/
  );
  assert.deepEqual(
    repositoryPathDiagnostics(
      'fixture.yml',
      'run: "$RUNNER_TEMP/paper-links-venv/bin/python" -m scripts.paper.check_links'
    ),
    []
  );
  assert.match(
    repositoryPathDiagnostics(
      'fixture.yml',
      'run: "$RUNNER_TEMP/paper-links-venv/bin/python" -m scripts.paper.missing_check'
    )[0],
    /Python module scripts\.paper\.missing_check does not resolve/
  );
  assert.match(
    repositoryPathDiagnostics('fixture.yml', "run: python3 -m unittest discover -s tests/paper -p 'test_missing_*.py'")[0],
    /unittest discovery tests\/paper with pattern test_missing_\*\.py matches no tests/
  );
  assert.match(
    repositoryPathDiagnostics('fixture.yml', "run: python3 -m unittest discover -s scripts/paper -p 'check_links.py'")[0],
    /unittest discovery scripts\/paper with pattern check_links\.py matches no tests/
  );
  assert.match(
    repositoryPathDiagnostics('fixture.yml', "run: python3 -m unittest discover -s tests/site -p '*.test.mjs'")[0],
    /unittest discovery tests\/site with pattern \*\.test\.mjs matches no tests/
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
  assert.deepEqual(coherenceCommandDiagnostics(workflowContents, packageJson.scripts), []);
  assert.match(
    coherenceCommandDiagnostics(
      [...workflowContents, ['common-code-quality.yml', 'run: npm run test:coherence-integrity']],
      packageJson.scripts
    )[0],
    /must be invoked only by common-coherence-integrity\.yml; found common-coherence-integrity\.yml, common-code-quality\.yml/
  );
  const packageErrors = repositoryPathDiagnostics('package.json', JSON.stringify(packageJson.scripts));
  assert.deepEqual(packageErrors, [], packageErrors.join('\n'));
  const misplaced = placementDiagnostics(repositoryCodeFiles);
  const boundaries = boundaryDiagnostics(repositoryCodeFiles);
  assert.deepEqual([...misplaced, ...boundaries], [], [...misplaced, ...boundaries].join('\n'));

  const readme = await readFile(new URL('../../README.md', import.meta.url), 'utf8');
  const quality = await readFile(new URL('../../.github/QUALITY.md', import.meta.url), 'utf8');
  const conventions = await readFile(new URL('../../.github/CONVENTIONS.md', import.meta.url), 'utf8');
  const scriptsReadme = await readFile(new URL('../../scripts/README.md', import.meta.url), 'utf8');
  const documentationPathErrors = [
    ['README.md', readme],
    ['.github/CONVENTIONS.md', conventions],
    ['scripts/README.md', scriptsReadme]
  ].flatMap(([source, contents]) => repositoryPathDiagnostics(source, contents));
  assert.deepEqual(documentationPathErrors, [], documentationPathErrors.join('\n'));
  const jekyllConfig = await readFile(new URL('../../_config.yml', import.meta.url), 'utf8');
  const exclusions = jekyllConfig.match(/^exclude:\n((?:  - .+\n)*)/m)?.[1] ?? '';
  assert.match(readme, /\.github\/CONVENTIONS\.md/);
  assert.match(conventions, /Common → Site → Paper/);
  assert.match(
    conventions,
    /Use release version tags for external actions and one version of each shared dependency across workflows, unless a documented compatibility constraint requires otherwise\./
  );
  assert.deepEqual([
    ...markdownLinkDiagnostics('README.md', readme),
    ...markdownLinkDiagnostics('.github/QUALITY.md', quality),
    ...markdownLinkDiagnostics('.github/CONVENTIONS.md', conventions),
    ...markdownLinkDiagnostics('scripts/README.md', scriptsReadme)
  ], []);
  assert.match(
    markdownLinkDiagnostics(
      '.github/QUALITY.md',
      quality.replace('(CONVENTIONS.md)', '(MISSING_CONVENTIONS.md)')
    )[0],
    /\.github\/QUALITY\.md: documentation link MISSING_CONVENTIONS\.md does not resolve/
  );
  assert.match(scriptsReadme, /\.github\/QUALITY\.md/);
  assert.match(exclusions, /^  - scripts$/m, 'supporting scripts stay out of the generated site');
  assert.match(exclusions, /^  - tests$/m, 'automated tests stay out of the generated site');
});

test('Common Coherence Integrity has a standalone read-only workflow and actionable summary', async () => {
  const workflow = await readFile(new URL('../../.github/workflows/common-coherence-integrity.yml', import.meta.url), 'utf8');
  assert.match(workflow, /^name: "Common: Coherence Integrity"$/m);
  assert.match(workflow, /^  pull_request:\n    branches: \[main\]$/m);
  assert.match(workflow, /^  workflow_dispatch:$/m);
  assert.match(workflow, /^  workflow_call:$/m);
  assert.match(workflow, /^permissions:\n  contents: read$/m);
  assert.match(workflow, /^  common-coherence-integrity:\n    name: "Common: Coherence Integrity"$/m);
  assert.match(workflow, /git rev-parse HEAD/);
  assert.match(workflow, /run: npm run test:coherence-integrity/);
  assert.match(workflow, /if: always\(\)/);
  assert.match(workflow, /COHERENCE_OUTCOME: \$\{\{ steps\.coherence\.outcome \}\}/);
  assert.match(workflow, /Checked-out revision:/);
  assert.match(workflow, /\*\*incomplete\*\*; contract evidence was not produced/);
});
