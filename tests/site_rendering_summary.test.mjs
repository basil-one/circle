import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import test from "node:test";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { renderSiteRenderingSummary } from "../scripts/render_site_rendering_summary.mjs";

const scriptPath = resolve("scripts/render_site_rendering_summary.mjs");
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
  const route = "/";
  const routeTest = makeTest(
    `${route} is present in generated output and loads`,
    options.routeStatus ?? "passed",
    {
      errors: options.routeErrors,
    },
  );
  const axeTest = makeTest(
    `${route} has no detectable accessibility violations`,
    options.axeStatus ?? "passed",
    {
      annotations:
        options.axeScanned === false ? [] : [{ type: "axe-scan-complete" }],
    },
  );
  const responsiveTests = (
    options.responsiveTitles ?? [
      `Responsive usability: ${route} at 320px`,
      `Responsive usability: ${route} at 768px`,
      `Responsive usability: ${route} at 1280px`,
    ]
  ).map((title, index) =>
    makeTest(
      title,
      options.responsiveStatuses?.[index] ??
        options.responsiveStatus ??
        "passed",
      {
        duration: 250,
        noResult: options.noResponsiveResult === index,
        annotations:
          index === 0
            ? [
                {
                  type: "responsive-hit-area",
                  description: "navigation:Menu:44x44",
                },
                {
                  type: "responsive-hit-area",
                  description: "footer:Home:42x42",
                },
              ]
            : [],
      },
    ),
  );
  return {
    stats: { duration: 1200 },
    suites: [
      {
        suites: [
          {
            specs: [routeTest, axeTest, ...responsiveTests],
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

test("the executable writes its summary to the GitHub step summary and stdout", () => {
  const directory = mkdtempSync(join(tmpdir(), "circle-rendering-summary-"));
  try {
    const reportPath = join(directory, "playwright.json");
    const summaryPath = join(directory, "summary.md");
    writeFileSync(reportPath, JSON.stringify(makeReport()));
    const result = spawnSync(process.execPath, [scriptPath], {
      encoding: "utf8",
      env: {
        ...process.env,
        ...diagnosticOutcomes,
        RESULTS_FILE: reportPath,
        GITHUB_STEP_SUMMARY: summaryPath,
      },
    });

    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /^# Site: Rendering Integrity/m);
    assert.equal(readFileSync(summaryPath, "utf8"), result.stdout);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
