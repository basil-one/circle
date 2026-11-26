import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:net';
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { extname, resolve } from 'node:path';
import { chromium } from 'playwright';
import {
  performanceRunCount,
  selectPerformanceRoutes
} from '../tests/browser/site-routes.js';
import { median } from './site_performance_policy.mjs';

const root = process.cwd();
const siteDirectory = resolve(root, '_site');
const reportDirectory = resolve(
  process.env.PERFORMANCE_REPORT_DIR ?? resolve(root, 'performance-reports')
);
const host = '127.0.0.1';
const runCount = performanceRunCount;
const metrics = [
  ['first-contentful-paint', formatMilliseconds],
  ['largest-contentful-paint', formatMilliseconds],
  ['speed-index', formatMilliseconds],
  ['total-blocking-time', formatMilliseconds],
  ['cumulative-layout-shift', (value) => value.toFixed(3)]
];

function formatMilliseconds(value) {
  return value >= 1_000 ? `${(value / 1_000).toFixed(2)} s` : `${Math.round(value)} ms`;
}

function collectFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const filePath = resolve(directory, entry.name);
    if (entry.isDirectory()) return collectFiles(filePath);
    return entry.isFile() ? [filePath] : [];
  });
}

function recordLoadedAssets(report, origin, assets) {
  const requests = report.audits['network-requests']?.details?.items;
  if (!Array.isArray(requests)) {
    throw new Error(`Lighthouse report for ${report.requestedUrl} is missing network request details.`);
  }
  for (const request of requests) {
    const url = new URL(request.url);
    if (url.origin !== origin || !url.pathname.startsWith('/assets/') || request.statusCode !== 200) {
      continue;
    }
    if (!Number.isFinite(request.resourceSize)) {
      throw new Error(`Lighthouse report for ${report.requestedUrl} is missing the resource size for ${url.pathname}.`);
    }
    assets.set(url.pathname, { path: url.pathname.slice(1), bytes: request.resourceSize });
  }
}

function collectExternalScripts(origin) {
  const scriptSources = new Set();
  for (const filePath of collectFiles(siteDirectory).filter((path) => extname(path).toLowerCase() === '.html')) {
    const html = readFileSync(filePath, 'utf8');
    for (const match of html.matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["']/gi)) {
      const source = new URL(match[1], origin);
      if (source.origin !== origin) scriptSources.add(`${source.origin}${source.pathname}`);
    }
  }
  return [...scriptSources].sort();
}

function recordExternalScriptObservations(report, origin, observations) {
  const requests = report.audits['network-requests']?.details?.items;
  for (const request of requests ?? []) {
    if (request.resourceType !== 'Script') continue;
    const url = new URL(request.url);
    if (url.origin === origin) continue;
    const key = `${url.origin}${url.pathname}`;
    const observation = observations.get(key) ?? { samples: 0, successful: 0, transferSizes: [] };
    observation.samples += 1;
    if (request.statusCode >= 200 && request.statusCode < 400) {
      observation.successful += 1;
      if (Number.isFinite(request.transferSize)) {
        observation.transferSizes.push(request.transferSize);
      }
    }
    observations.set(key, observation);
  }
}

function formatMetric(name, value) {
  const definition = metrics.find(([key]) => key === name);
  if (!definition || !Number.isFinite(value)) {
    throw new Error(`Lighthouse report is missing a finite ${name} measurement.`);
  }
  return definition[1](value);
}

function describeExternalScript(source, observations) {
  const observation = observations.get(source);
  if (!observation) return `\`${source}\` (not requested in Lighthouse samples)`;
  const transferSize = observation.transferSizes.length
    ? `; median transfer ${(median(observation.transferSizes) / 1024).toFixed(1)} KiB`
    : '; no successful transfer';
  return `\`${source}\` (${observation.successful}/${observation.samples} responses succeeded${transferSize})`;
}

function runCommand(command, args, env = process.env) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, { env, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.once('error', reject);
    child.once('close', (code) => {
      if (code === 0) {
        resolvePromise({ stdout, stderr });
      } else {
        reject(new Error(`${command} exited with code ${code}.\n${stderr || stdout}`));
      }
    });
  });
}

function commandVersion(command, args, label) {
  const result = spawnSync(command, args, { encoding: 'utf8' });
  if (result.status !== 0) {
    throw new Error(`Could not read ${label} version: ${result.stderr}`);
  }
  return result.stdout.trim();
}

async function findFreePort() {
  const server = createServer();
  await new Promise((resolvePromise, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolvePromise);
  });
  const { port } = server.address();
  await new Promise((resolvePromise, reject) => {
    server.close((error) => error ? reject(error) : resolvePromise());
  });
  return port;
}

async function waitForServer(server, origin) {
  const deadline = Date.now() + 15_000;
  let lastError;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) {
      throw new Error(`Local Jekyll server exited before becoming ready (code ${server.exitCode}).`);
    }
    try {
      const response = await fetch(`${origin}/`, { signal: AbortSignal.timeout(1_000) });
      if (response.status === 200) return;
      lastError = new Error(`Local site returned HTTP ${response.status} for /.`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
  }
  throw new Error(`Local Jekyll server did not become ready: ${lastError?.message ?? 'timeout'}`);
}

async function stopServer(server) {
  if (server.exitCode !== null) return;
  const stopped = new Promise((resolvePromise) => server.once('close', resolvePromise));
  server.kill('SIGTERM');
  await Promise.race([
    stopped,
    new Promise((resolvePromise) => setTimeout(resolvePromise, 3_000))
  ]);
  if (server.exitCode === null) server.kill('SIGKILL');
}

function reportFileName(route, formFactor, run) {
  const slug = route === '/' ? 'home' : route.replace(/^\/|\/$/g, '').replaceAll('/', '-');
  return resolve(reportDirectory, `${slug}-${formFactor}-${run}.json`);
}

function loadMetric(report, key) {
  const value = report.audits[key]?.numericValue;
  if (!Number.isFinite(value)) {
    throw new Error(`Lighthouse report for ${report.requestedUrl} is missing ${key}.`);
  }
  return value;
}

function renderSummary(
  results,
  assetInventory,
  externalScripts,
  externalScriptObservations,
  lighthouseVersion,
  chromeVersion,
  rubyVersion,
  jekyllVersion,
  elapsedSeconds,
  machineSummary
) {
  const lines = [
    '# Performance integrity',
    '',
    `- **Mode:** ${process.env.PERFORMANCE_EVENT_NAME ?? 'local'} run on \`${process.env.PERFORMANCE_SOURCE_BRANCH ?? 'local'}\`; informational lab baseline, not a performance-quality judgment.`,
    `- **Tools:** Lighthouse ${lighthouseVersion}; ${chromeVersion}; ${rubyVersion}; ${jekyllVersion}; Node ${process.version}.`,
    `- **Conditions:** ${process.env.PERFORMANCE_RUNNER ?? `local ${process.platform}/${process.arch}`}; Jekyll production build served locally; Lighthouse default mobile configuration and desktop preset; ${runCount} fresh-browser runs per route and form factor; medians shown; external scripts are allowed but Google Analytics collection requests are blocked to avoid synthetic visits. These are lab measurements, not field data or real-user Core Web Vitals.`,
    `- **Coverage:** ${new Set(results.map((result) => result.route)).size} generated routes × ${results.length} distinct route/profile pairs × ${results.length * runCount} samples (${runCount} per pair).`,
    `- **Measurement runtime:** ${elapsedSeconds.toFixed(1)} seconds, excluding workflow setup, build, dependency/browser installation, missing-route verification, and artifact upload; total Actions job duration is shown in the run overview.`,
    '',
    '| Generated route | Form factor | Lab FCP | Lab LCP | Lab Speed Index | Lab TBT | Lab CLS |',
    '| --- | --- | ---: | ---: | ---: | ---: | ---: |'
  ];

  for (const result of results) {
    lines.push(
      `| \`${result.route}\` | ${result.formFactor} | ${formatMetric('first-contentful-paint', result.values['first-contentful-paint'])} | ${formatMetric('largest-contentful-paint', result.values['largest-contentful-paint'])} | ${formatMetric('speed-index', result.values['speed-index'])} | ${formatMetric('total-blocking-time', result.values['total-blocking-time'])} | ${formatMetric('cumulative-layout-shift', result.values['cumulative-layout-shift'])} |`
    );
  }

  const totalAssetBytes = assetInventory.reduce((total, asset) => total + asset.bytes, 0);
  lines.push(
    '',
    `- **First-party assets loaded across measured pages:** ${assetInventory.length} distinct \`/assets/\` files, ${(totalAssetBytes / 1024).toFixed(1)} KiB resource size; largest files: ${assetInventory.slice(0, 5).map((asset) => `\`${asset.path}\` (${(asset.bytes / 1024).toFixed(1)} KiB)`).join(', ') || 'none found'}.`,
    `- **External scripts in rendered HTML** (query strings omitted; network requests are observed, not gated): ${externalScripts.map((source) => describeExternalScript(source, externalScriptObservations)).join(', ') || 'none found'}.`,
    '- **Evidence sources:** route pages and external-script references come from generated `_site`; metrics, tool versions, loaded assets, and external-script requests come from the raw Lighthouse JSON reports. The workflow records measurement/diagnostic outcomes separately; results are not a pass/fail performance judgment.'
  );
  writeFileSync(
    resolve(reportDirectory, 'performance-summary.json'),
    `${JSON.stringify(machineSummary, null, 2)}\n`
  );
  process.stdout.write(`${lines.join('\n')}\n`);
}

async function main() {
  if (!existsSync(resolve(siteDirectory, 'index.html'))) {
    throw new Error(`Generated site not found at ${siteDirectory}; build it before measuring performance.`);
  }
  mkdirSync(reportDirectory, { recursive: true });

  const availableRoutes = selectPerformanceRoutes(siteDirectory);
  const requestedProfiles = process.env.PERFORMANCE_PROFILES_JSON
    ? JSON.parse(process.env.PERFORMANCE_PROFILES_JSON)
    : availableRoutes.flatMap((route) =>
      ['mobile', 'desktop'].map((formFactor) => ({ route, formFactor }))
    );
  if (
    !Array.isArray(requestedProfiles) ||
    !requestedProfiles.length ||
    requestedProfiles.some((profile) =>
      !profile ||
      !availableRoutes.includes(profile.route) ||
      !['mobile', 'desktop'].includes(profile.formFactor)
    ) ||
    new Set(requestedProfiles.map(({ route, formFactor }) => `${route}\u0000${formFactor}`)).size !== requestedProfiles.length
  ) {
    throw new Error('PERFORMANCE_PROFILES_JSON must contain unique generated route/profile pairs.');
  }
  const routes = [...new Set(requestedProfiles.map(({ route }) => route))];
  const measuredRoutes = process.argv.includes('--negative-test')
    ? [...routes, '/__performance-test-missing-route__/']
    : routes;
  for (const route of measuredRoutes) {
    if (!existsSync(resolve(siteDirectory, `.${route}`, 'index.html'))) {
      throw new Error(`Expected generated output for performance route ${route}.`);
    }
  }

  const port = await findFreePort();
  const origin = `http://${host}:${port}`;
  const lighthousePath = resolve(root, 'node_modules/lighthouse/cli/index.js');
  const chromePath = chromium.executablePath();
  const chromeVersionResult = spawnSync(chromePath, ['--version'], { encoding: 'utf8' });
  if (chromeVersionResult.status !== 0) {
    throw new Error(`Could not read Chromium version: ${chromeVersionResult.stderr}`);
  }
  const chromeVersion = chromeVersionResult.stdout.trim();
  const rubyVersion = commandVersion('ruby', ['--version'], 'Ruby');
  const jekyllVersion = commandVersion('bundle', ['exec', 'jekyll', '--version'], 'Jekyll');
  const startedAt = Date.now();
  const server = spawn(
    'bundle',
    ['exec', 'ruby', '-run', '-e', 'httpd', siteDirectory, '-p', String(port), '-b', host],
    { cwd: root, stdio: 'ignore' }
  );

  try {
    await waitForServer(server, origin);
    for (const route of measuredRoutes) {
      const response = await fetch(`${origin}${route}`);
      if (response.status !== 200) {
        throw new Error(`Expected generated route ${route} to return HTTP 200; received HTTP ${response.status}.`);
      }
    }

    const results = [];
    const loadedAssets = new Map();
    const externalScriptObservations = new Map();
    for (const { route, formFactor } of requestedProfiles) {
      const reports = [];
      for (let run = 1; run <= runCount; run += 1) {
        const outputPath = reportFileName(route, formFactor, run);
        await runCommand(process.execPath, [
          lighthousePath,
          `${origin}${route}`,
          '--only-categories=performance',
          '--output=json',
          `--output-path=${outputPath}`,
          ...(formFactor === 'desktop' ? ['--preset=desktop'] : []),
          '--blocked-url-patterns=*google-analytics.com/*/collect*',
          '--chrome-flags=--headless --no-sandbox --disable-dev-shm-usage',
          '--quiet'
        ], { ...process.env, CHROME_PATH: chromePath });
        const report = JSON.parse(readFileSync(outputPath, 'utf8'));
        if (report.lighthouseVersion === undefined) {
          throw new Error(`Lighthouse report for ${route} did not include its version.`);
        }
        if (run === 1) {
          recordLoadedAssets(report, origin, loadedAssets);
          recordExternalScriptObservations(report, origin, externalScriptObservations);
        }
        reports.push(report);
      }
      const values = {};
      for (const [key] of metrics) {
        values[key] = median(reports.map((report) => loadMetric(report, key)));
      }
      results.push({ route, formFactor, values });
    }

    const firstProfile = requestedProfiles[0];
    const lighthouseVersion = JSON.parse(
      readFileSync(reportFileName(firstProfile.route, firstProfile.formFactor, 1), 'utf8')
    ).lighthouseVersion;
    const externalScripts = collectExternalScripts(origin);
    const externalScriptSummary = externalScripts.map((source) => {
      const observation = externalScriptObservations.get(source);
      return {
        source,
        samples: observation?.samples ?? 0,
        successful: observation?.successful ?? 0,
        medianTransferBytes: observation?.transferSizes.length
          ? median(observation.transferSizes)
          : null
      };
    });
    const elapsedSeconds = (Date.now() - startedAt) / 1000;
    const machineSummary = {
      schemaVersion: 1,
      complete: true,
      capturedAt: new Date().toISOString(),
      source: {
        eventName: process.env.PERFORMANCE_EVENT_NAME ?? 'local',
        branch: process.env.PERFORMANCE_SOURCE_BRANCH ?? null,
        sha: process.env.GITHUB_SHA ?? null,
        runId: process.env.GITHUB_RUN_ID ?? null,
        runUrl: process.env.GITHUB_RUN_ID && process.env.GITHUB_SERVER_URL && process.env.GITHUB_REPOSITORY
          ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}`
          : null
      },
      coverage: {
        routeCount: routes.length,
        formFactors: [...new Set(requestedProfiles.map(({ formFactor }) => formFactor))],
        runCount,
        routeProfiles: results.length,
        measurementCount: results.length * runCount
      },
      environment: {
        runner: process.env.PERFORMANCE_RUNNER ?? `local ${process.platform}/${process.arch}`,
        lighthouse: lighthouseVersion,
        chrome: chromeVersion,
        ruby: rubyVersion,
        jekyll: jekyllVersion,
        node: process.version
      },
      conditions: {
        site: 'Jekyll production build served locally',
        mobile: 'Lighthouse default mobile configuration',
        desktop: 'Lighthouse desktop preset',
        analyticsCollectionBlocked: true,
        labOnly: true
      },
      timing: {
        measurementSeconds: elapsedSeconds
      },
      firstPartyAssets: [...loadedAssets.values()].sort(
        (left, right) => right.bytes - left.bytes || left.path.localeCompare(right.path)
      ),
      externalScripts: externalScriptSummary,
      results
    };
    renderSummary(
      results,
      [...loadedAssets.values()].sort((left, right) => right.bytes - left.bytes || left.path.localeCompare(right.path)),
      externalScripts,
      externalScriptObservations,
      lighthouseVersion,
      chromeVersion,
      rubyVersion,
      jekyllVersion,
      elapsedSeconds,
      machineSummary
    );
  } finally {
    await stopServer(server);
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
