import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import test from "node:test";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { responsiveViewports } from "../../scripts/site/routes.js";
import { renderSiteRenderingSummary } from "../../scripts/site/render_rendering_summary.mjs";

const scriptPath = resolve("scripts/site/render_rendering_summary.mjs");
const diagnosticOutcomes = {
  BUILD_OUTCOME: "success",
  SMOKE_OUTCOME: "success",
  NEGATIVE_OUTCOME: "success",
  ACCESSIBILITY_NEGATIVE_OUTCOME: "success",
  RESPONSIVE_NEGATIVE_OUTCOME: "success",
};

function makeTest(title, status = "passed", options = {}) {
  return {
    title,
    tests: [
      {
        annotations: options.annotations ?? [],
        results:
          options.noResult === true
            ? []
            : [
                {
                  status,
                  duration: options.duration ?? 120,
                  errors: options.errors ?? [],
                },
              ],
      },
    ],
  };
}

function makeReport(options = {}) {
  const routes = options.routes ?? ["/"];
  const smokeTests = routes
    .filter((route) => !options.missingSmoke?.includes(route))
    .map((route) =>
      makeTest(
        `${route} is present in generated output and loads`,
        options.routeStatus ?? "passed",
        { errors: options.routeErrors },
      ),
    );
  const axeTests = routes
    .filter((route) => !options.missingAxe?.includes(route))
    .map((route) =>
      makeTest(
        `${route} has no detectable accessibility violations`,
        options.axeStatus ?? "passed",
        {
          annotations:
            options.axeScanned === false
              ? []
              : [{ type: "axe-scan-complete" }],
        },
      ),
    );
  const responsiveTests = routes.flatMap((route) =>
    responsiveViewports.flatMap((width, index) => {
      if (options.missingResponsive?.includes(`${route}\u0000${width}`)) {
        return [];
      }
      const overriddenTitle =
        route === "/" ? options.responsiveTitles?.[index] : undefined;
      const title =
        overriddenTitle ?? `Responsive usability: ${route} at ${width}px`;
      const annotations =
        options.noResponsiveHitArea === true
          ? []
          : [
              {
                type: "responsive-hit-area",
                description:
                  options.malformedHitArea && route === "/" && index === 0
                    ? options.malformedHitArea
                    : "navigation:Menu:44x44",
              },
              {
                type: "responsive-hit-area",
                description: "footer:Home:42x42",
              },
            ];
      return [
        makeTest(
          title,
          options.responsiveStatuses?.[index] ??
            options.responsiveStatus ??
            "passed",
          {
            duration: 250,
            noResult: options.noResponsiveResult === index,
            annotations,
          },
        ),
      ];
    }),
  );
  const specs = [...smokeTests, ...axeTests, ...responsiveTests];
  if (options.duplicateSmoke) specs.push(smokeTests[0]);
  if (options.duplicateAxe) specs.push(axeTests[0]);
  if (options.duplicateResponsive) specs.push(responsiveTests[0]);
  if (options.unexpectedRoute) {
    specs.push(
      makeTest(
        "/unexpected/ is present in generated output and loads",
        "passed",
      ),
      makeTest(
        "/unexpected/ has no detectable accessibility violations",
        "passed",
        { annotations: [{ type: "axe-scan-complete" }] },
      ),
      makeTest("Responsive usability: /unexpected/ at 320px", "passed", {
        annotations: [
          {
            type: "responsive-hit-area",
            description: "navigation:Menu:44x44",
          },
        ],
      }),
    );
  }
  return {
    stats: { duration: 1200 },
    suites: [
      {
        suites: [
          {
            specs,
          },
        ],
      },
    ],
  };
}

function render(report, overrides = {}) {
  return renderSiteRenderingSummary({
    reportContents: report === undefined ? undefined : JSON.stringify(report),
    env: { ...diagnosticOutcomes, ...overrides },
    expectedRoutes: ["/"],
  });
}

test("summarizes nested route, axe, and responsive coverage with measurements and diagnostics", () => {
  const result = render(makeReport());

  assert.equal(result.exitCode, 0);
  assert.match(result.markdown, /Production build .* \*\*passed\*\*/);
  assert.match(
    result.markdown,
    /Generated routes smoke-tested: \*\*1\*\*; route checks \*\*1\/1 passed/,
  );
  assert.match(
    result.markdown,
    /Responsive usability: \*\*3\/3 passed, 0 failed, 0 skipped, 0 not completed\*\*/,
  );
  assert.match(result.markdown, /across `\/` at 320px, 768px, 1280px/);
  assert.match(
    result.markdown,
    /navigation \*\*44×44px \(Menu\)\*\*; footer \*\*42×42px \(Home\)\*\*/,
  );
  assert.match(
    result.markdown,
    /Full-site axe accessibility .* \*\*1\/1 scans completed; 1 passed/,
  );
  assert.match(
    result.markdown,
    /Browser tests overall: \*\*5 passed, 0 failed, 0 skipped, 0 not completed\*\*/,
  );
  assert.match(result.markdown, /Missing-route diagnostic .* \*\*passed\*\*/);
  assert.match(
    result.markdown,
    /deliberate HTTP 404 is expected, not a site regression/,
  );
  assert.match(result.markdown, /In-memory axe diagnostic .* \*\*passed\*\*/);
  assert.match(
    result.markdown,
    /In-memory responsive diagnostic .* \*\*passed\*\*/,
  );
});

test("requires exactly one passing smoke, axe, and responsive case for every discovered route", () => {
  const expectedRoutes = ["/", "/moves/establish/", "/lenses/sense/"];
  const valid = renderSiteRenderingSummary({
    reportContents: JSON.stringify(makeReport({ routes: expectedRoutes })),
    env: diagnosticOutcomes,
    expectedRoutes,
  });
  assert.equal(valid.exitCode, 0);

  const missingRoute = renderSiteRenderingSummary({
    reportContents: JSON.stringify(
      makeReport({ routes: expectedRoutes, missingSmoke: ["/moves/establish/"] }),
    ),
    env: diagnosticOutcomes,
    expectedRoutes,
  });
  assert.equal(missingRoute.exitCode, 1);
  assert.match(missingRoute.markdown, /Missing generated-route smoke evidence/);

  const missingAxe = renderSiteRenderingSummary({
    reportContents: JSON.stringify(
      makeReport({
        routes: expectedRoutes,
        missingAxe: ["/lenses/sense/"],
      }),
    ),
    env: diagnosticOutcomes,
    expectedRoutes,
  });
  assert.equal(missingAxe.exitCode, 1);
  assert.match(missingAxe.markdown, /Missing axe evidence/);

  const duplicateAndUnexpected = renderSiteRenderingSummary({
    reportContents: JSON.stringify(
      makeReport({
        duplicateSmoke: true,
        duplicateAxe: true,
        duplicateResponsive: true,
        unexpectedRoute: true,
      }),
    ),
    env: diagnosticOutcomes,
    expectedRoutes: ["/"],
  });
  assert.equal(duplicateAndUnexpected.exitCode, 1);
  assert.match(duplicateAndUnexpected.markdown, /Duplicate generated-route smoke evidence/);
  assert.match(duplicateAndUnexpected.markdown, /Duplicate axe evidence/);
  assert.match(duplicateAndUnexpected.markdown, /Duplicate responsive evidence/);
  assert.match(duplicateAndUnexpected.markdown, /Unexpected generated-route smoke case/);
  assert.match(duplicateAndUnexpected.markdown, /Unexpected full-site axe case/);
  assert.match(duplicateAndUnexpected.markdown, /Unexpected responsive case/);
});

test("requires every shared responsive viewport for every generated route", () => {
  const expectedRoutes = ["/", "/moves/"];
  const result = renderSiteRenderingSummary({
    reportContents: JSON.stringify(
      makeReport({
        routes: expectedRoutes,
        missingResponsive: ["/moves/\u0000768"],
      }),
    ),
    env: diagnosticOutcomes,
    expectedRoutes,
  });

  assert.equal(result.exitCode, 1);
  assert.match(
    result.markdown,
    /Missing responsive evidence for "\/moves\/ at 768px"/,
  );
});

test("skipped or incomplete expected cases fail coverage instead of appearing complete", () => {
  const result = renderSiteRenderingSummary({
    reportContents: JSON.stringify(
      makeReport({
        routeStatus: "skipped",
        axeStatus: "timedOut",
        axeScanned: false,
        responsiveStatus: "skipped",
      }),
    ),
    env: diagnosticOutcomes,
    expectedRoutes: ["/"],
  });

  assert.equal(result.exitCode, 1);
  assert.match(result.markdown, /Incomplete generated-route smoke evidence/);
  assert.match(result.markdown, /Incomplete axe evidence/);
  assert.match(result.markdown, /Axe scan annotation is missing/);
  assert.match(result.markdown, /Incomplete responsive evidence/);
});

test("malformed hit-area annotations are reported without crashing summary generation", () => {
  const result = renderSiteRenderingSummary({
    reportContents: JSON.stringify(
      makeReport({ malformedHitArea: { invalid: "shape" } }),
    ),
    env: diagnosticOutcomes,
    expectedRoutes: ["/"],
  });

  assert.equal(result.exitCode, 1);
  assert.match(
    result.markdown,
    /non-string "responsive-hit-area" annotation description/,
  );
  assert.match(result.markdown, /Coverage verification: \*\*failed\*\*/);
});

test("a missing report is unavailable rather than zero and fails coverage after a successful browser step", () => {
  const result = render(undefined);

  assert.equal(result.exitCode, 1);
  assert.match(result.markdown, /Playwright JSON report was not produced/);
  assert.match(
    result.markdown,
    /route counts, viewport counts, and duration are unavailable, not zero/,
  );
  assert.match(
    result.markdown,
    /Coverage results: \*\*failed verification\*\*/,
  );
  assert.match(
    result.markdown,
    /Inspect the exact GitHub step \*\*Run rendered-site smoke tests\*\*/,
  );
});

test("a missing report after a failed browser step stays unavailable without replacing the browser failure", () => {
  const result = render(undefined, { SMOKE_OUTCOME: "failure" });

  assert.equal(result.exitCode, 0);
  assert.match(result.markdown, /Browser suite .* \*\*failed\*\*/);
  assert.match(result.markdown, /Smoke route checks: \*\*not available\*\*/);
  assert.doesNotMatch(
    result.markdown,
    /Coverage results: \*\*failed verification\*\*/,
  );
});

test("malformed and empty reports fail coverage when the browser step reported success", () => {
  const malformed = renderSiteRenderingSummary({
    reportContents: "{malformed",
    env: diagnosticOutcomes,
  });
  const empty = render({ suites: [] });

  for (const result of [malformed, empty]) {
    assert.equal(result.exitCode, 1);
    assert.match(
      result.markdown,
      /Playwright JSON report (was unreadable or malformed|contained no test cases)/,
    );
    assert.match(
      result.markdown,
      /Coverage results: \*\*failed verification\*\*/,
    );
  }
});

test("malformed nested report structures are reported as unavailable instead of throwing", () => {
  const result = renderSiteRenderingSummary({
    reportContents: JSON.stringify({ suites: [{ specs: "invalid" }] }),
    env: diagnosticOutcomes,
  });

  assert.equal(result.exitCode, 1);
  assert.match(
    result.markdown,
    /Playwright JSON report was unreadable or malformed/,
  );
});

test("incomplete tests preserve skipped and not-completed states and enforce route, axe, and responsive coverage", () => {
  const report = makeReport({
    axeScanned: false,
    responsiveTitles: [
      "Responsive usability: / at 320px",
      "Responsive usability: / at 768px",
      "Responsive usability: malformed responsive title",
    ],
    responsiveStatuses: ["passed", "skipped", "passed"],
    noResponsiveResult: 2,
  });
  const result = render(report, {
    NEGATIVE_OUTCOME: "failure",
    ACCESSIBILITY_NEGATIVE_OUTCOME: "skipped",
    RESPONSIVE_NEGATIVE_OUTCOME: "cancelled",
  });

  assert.equal(result.exitCode, 1);
  assert.match(
    result.markdown,
    /Responsive usability: \*\*1\/3 passed, 0 failed, 1 skipped, 1 not completed\*\*/,
  );
  assert.match(
    result.markdown,
    /Full-site axe accessibility .* \*\*0\/1 scans completed; 1 passed/,
  );
  assert.match(result.markdown, /Coverage verification: \*\*failed\*\*/);
  assert.match(
    result.markdown,
    /\*\*not completed:\*\* `Responsive usability: malformed responsive title`/,
  );
  assert.match(result.markdown, /Missing-route diagnostic .* \*\*failed\*\*/);
  assert.match(
    result.markdown,
    /In-memory axe diagnostic .* \*\*not completed\*\*/,
  );
  assert.match(
    result.markdown,
    /In-memory responsive diagnostic .* \*\*not completed\*\*/,
  );
});

test("failing browser reports retain case diagnostics and remain distinct from expected negative checks", () => {
  const report = makeReport({
    routeStatus: "failed",
    routeErrors: [{ message: "Expected route to load; received HTTP 500." }],
    axeStatus: "failed",
    responsiveStatus: "timedOut",
  });
  const result = render(report, {
    SMOKE_OUTCOME: "failure",
    NEGATIVE_OUTCOME: "success",
  });

  assert.equal(result.exitCode, 0);
  assert.match(
    result.markdown,
    /route checks \*\*0\/1 passed, 1 failed, 0 skipped, 0 not completed\*\*/,
  );
  assert.match(result.markdown, /Full-site axe accessibility .* 1 failed/);
  assert.match(
    result.markdown,
    /Responsive usability: \*\*0\/3 passed, 3 failed/,
  );
  assert.match(result.markdown, /### Non-passing browser tests/);
  assert.match(result.markdown, /Expected route to load; received HTTP 500\./);
  assert.match(
    result.markdown,
    /\*\*timedOut:\*\* `Responsive usability: \/ at 320px`/,
  );
  assert.match(result.markdown, /Missing-route diagnostic .* \*\*passed\*\*/);
});

test("the executable discovers full generated-route coverage and writes its summary", () => {
  const directory = mkdtempSync(join(tmpdir(), "circle-rendering-summary-"));
  try {
    const reportPath = join(directory, "playwright.json");
    const siteDirectory = join(directory, "site");
    const summaryPath = join(directory, "summary.md");
    const routes = ["/", "/moves/"];
    mkdirSync(join(siteDirectory, "moves"), { recursive: true });
    writeFileSync(join(siteDirectory, "index.html"), "<main>Home</main>");
    writeFileSync(
      join(siteDirectory, "moves", "index.html"),
      "<main>Moves</main>",
    );
    writeFileSync(reportPath, JSON.stringify(makeReport({ routes })));
    const result = spawnSync(process.execPath, [scriptPath], {
      encoding: "utf8",
      env: {
        ...process.env,
        ...diagnosticOutcomes,
        RESULTS_FILE: reportPath,
        SITE_DIRECTORY: siteDirectory,
        GITHUB_STEP_SUMMARY: summaryPath,
      },
    });

    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /^# Site: Rendering Integrity/m);
    assert.match(result.stdout, /Generated routes smoke-tested: \*\*2\*\*/);
    assert.doesNotMatch(result.stdout, /Coverage verification: \*\*failed\*\*/);
    assert.equal(readFileSync(summaryPath, "utf8"), result.stdout);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
