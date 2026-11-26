import { appendFileSync, existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

function parsePlaywrightReport(contents) {
  if (contents === undefined) {
    return {
      tests: [],
      reason:
        "Playwright JSON report was not produced; the build or browser test step may not have completed",
    };
  }

  try {
    const report = JSON.parse(contents);
    if (!Array.isArray(report?.suites)) {
      throw new Error("Playwright JSON report has no suite list");
    }

    const tests = [];
    const collect = (suite) => {
      if (!suite || typeof suite !== "object") {
        throw new Error("Playwright JSON report contains a malformed suite");
      }
      const specs = suite.specs ?? [];
      const childSuites = suite.suites ?? [];
      if (!Array.isArray(specs) || !Array.isArray(childSuites)) {
        throw new Error("Playwright JSON report contains malformed suites");
      }
      for (const spec of specs) {
        if (!spec || typeof spec.title !== "string") {
          throw new Error(
            "Playwright JSON report contains a malformed test spec",
          );
        }
        const specTests = spec.tests ?? [];
        if (!Array.isArray(specTests)) {
          throw new Error(
            "Playwright JSON report contains malformed test cases",
          );
        }
        for (const test of specTests) {
          if (
            !test ||
            (test.results !== undefined && !Array.isArray(test.results))
          ) {
            throw new Error(
              "Playwright JSON report contains malformed test results",
            );
          }
          if (
            test.annotations !== undefined &&
            !Array.isArray(test.annotations)
          ) {
            throw new Error(
              "Playwright JSON report contains malformed annotations",
            );
          }
          const result = test.results?.at(-1);
          if (result?.errors !== undefined && !Array.isArray(result.errors)) {
            throw new Error(
              "Playwright JSON report contains malformed diagnostics",
            );
          }
          tests.push({
            title: spec.title,
            status: result?.status ?? "not completed",
            duration: result?.duration,
            annotations: test.annotations ?? [],
            axeScanned:
              test.annotations?.some(
                (annotation) => annotation.type === "axe-scan-complete",
              ) ?? false,
            diagnostic: [
              ...new Set(
                [
                  result?.error?.message,
                  ...(result?.errors ?? []).map((error) => error.message),
                ].filter(Boolean),
              ),
            ].join("\n"),
          });
        }
      }
      for (const child of childSuites) collect(child);
    };
    for (const suite of report.suites ?? []) collect(suite);

    return tests.length
      ? { tests, duration: report.stats?.duration ?? report.duration }
      : { tests, reason: "Playwright JSON report contained no test cases" };
  } catch {
    return {
      tests: [],
      reason: "Playwright JSON report was unreadable or malformed",
    };
  }
}

function statusCounts(items) {
  return {
    passed: items.filter((test) => test.status === "passed").length,
    failed: items.filter(
      (test) => !["passed", "skipped", "not completed"].includes(test.status),
    ).length,
    skipped: items.filter((test) => test.status === "skipped").length,
    incomplete: items.filter((test) => test.status === "not completed").length,
  };
}

function summarizeStepOutcome(value) {
  if (value === "success") return "passed";
  if (value === "failure") return "failed";
  return "not completed";
}

function diagnosticLines(lines, env) {
  const diagnostics = [
    [
      "NEGATIVE_OUTCOME",
      "Missing-route diagnostic (GitHub step `Verify missing-route diagnostic`)",
      "the deliberate HTTP 404 is expected, not a site regression.",
      "inspect that step logs. This deliberate HTTP 404 is not itself a site regression.",
      "Its deliberate HTTP 404 is expected.",
    ],
    [
      "ACCESSIBILITY_NEGATIVE_OUTCOME",
      "In-memory axe diagnostic (GitHub step `Verify accessibility diagnostic`)",
      "the deliberate `image-alt` failure produced actionable details and is not a site-page finding.",
      "inspect that step logs. The deliberate fixture is not a site page.",
      "The fixture exists only in memory.",
    ],
    [
      "RESPONSIVE_NEGATIVE_OUTCOME",
      "In-memory responsive diagnostic (GitHub step `Verify responsive diagnostic`)",
      "the deliberate 320px page-overflow fixture produced actionable details and is not a site-page finding.",
      "inspect that step logs. The deliberate fixture is not a site-page finding.",
      "The fixture exists only in memory.",
    ],
  ];

  for (const [key, label, passed, failed, incomplete] of diagnostics) {
    const value = env[key];
    const state =
      value === "success"
        ? `**passed**; ${passed}`
        : value === "failure"
          ? `**failed**; ${failed}`
          : `**not completed**; ${incomplete}`;
    lines.push(`- ${label}: ${state}`);
  }
}

export function renderSiteRenderingSummary({ reportContents, env = {} }) {
  const {
    tests,
    reason: reportUnavailableReason,
    duration,
  } = parsePlaywrightReport(reportContents);
  const routeTests = tests.filter((test) =>
    test.title.endsWith("is present in generated output and loads"),
  );
  const axeTests = tests.filter((test) =>
    test.title.endsWith("has no detectable accessibility violations"),
  );
  const responsiveTests = tests.filter((test) =>
    test.title.startsWith("Responsive usability: "),
  );
  const responsiveCases = responsiveTests
    .map((test) => test.title.match(/^Responsive usability: (.+) at (\d+)px$/))
    .filter(Boolean);
  const responsiveRoutes = [
    ...new Set(responsiveCases.map((match) => match[1])),
  ].sort();
  const responsiveViewports = [
    ...new Set(responsiveCases.map((match) => Number(match[2]))),
  ].sort((a, b) => a - b);
  const routeCounts = statusCounts(routeTests);
  const axeCounts = statusCounts(axeTests);
  const responsiveCounts = statusCounts(responsiveTests);
  const allBrowserCounts = statusCounts(tests);
  const scansCompleted = axeTests.filter((test) => test.axeScanned).length;
  const responsiveDuration = responsiveTests.reduce(
    (total, test) => total + (test.duration ?? 0),
    0,
  );
  const responsiveDurationCount = responsiveTests.filter((test) =>
    Number.isFinite(test.duration),
  ).length;
  const hitAreas = responsiveTests.flatMap((test) =>
    test.annotations
      .filter((annotation) => annotation.type === "responsive-hit-area")
      .map((annotation) => annotation.description),
  );
  const smallestHitArea = (region) => {
    const areas = hitAreas
      .filter((area) => area.startsWith(`${region}:`))
      .map((area) => {
        const match = area.match(/^([^:]+):(.+):([\d.]+)x([\d.]+)$/);
        return match
          ? {
              label: match[2],
              width: Number(match[3]),
              height: Number(match[4]),
            }
          : undefined;
      })
      .filter(Boolean)
      .sort(
        (left, right) => left.width * left.height - right.width * right.height,
      );
    return areas[0];
  };
  const smallestNavigationTarget = smallestHitArea("navigation");
  const smallestFooterTarget = smallestHitArea("footer");
  const noAxeViolations =
    axeTests.length > 0 &&
    scansCompleted === axeTests.length &&
    axeCounts.passed === axeTests.length;
  const lines = [
    "# Site: Rendering Integrity",
    "",
    `- Production build (GitHub step \`Build site\`): **${summarizeStepOutcome(env.BUILD_OUTCOME)}**.`,
    `- Browser suite (GitHub step \`Run rendered-site smoke tests\`): **${summarizeStepOutcome(env.SMOKE_OUTCOME)}**.`,
  ];
  let exitCode = 0;

  if (reportUnavailableReason) {
    lines.push(
      `- Smoke route checks: **not available** (${reportUnavailableReason})`,
      `- Responsive usability: **not available** (${reportUnavailableReason}); route counts, viewport counts, and duration are unavailable, not zero.`,
      `- Full-site axe results: **not available** (${reportUnavailableReason})`,
      "- Route counts and Playwright duration are unavailable, not zero.",
    );
  } else {
    const responsiveSummary =
      responsiveTests.length === 0
        ? "- Responsive usability: **not available** (Playwright report contains no responsive cases; the browser step may not have reached that test file); route counts, viewport counts, and duration are unavailable, not zero."
        : `- Responsive usability: **${responsiveCounts.passed}/${responsiveTests.length} passed, ${responsiveCounts.failed} failed, ${responsiveCounts.skipped} skipped, ${responsiveCounts.incomplete} not completed** across ${responsiveRoutes.length ? responsiveRoutes.map((route) => `\`${route}\``).join(", ") : "unavailable routes"} at ${responsiveViewports.length ? responsiveViewports.map((width) => `${width}px`).join(", ") : "unavailable viewports"}; checked document/main overflow, control visibility/bounds, and navigation/footer operability; sum of individual test durations: **${responsiveDurationCount ? `${(responsiveDuration / 1000).toFixed(1)}s` : "unavailable"}**.`;
    lines.push(
      `- Generated routes smoke-tested: **${routeTests.length}**; route checks **${routeCounts.passed}/${routeTests.length} passed, ${routeCounts.failed} failed, ${routeCounts.skipped} skipped, ${routeCounts.incomplete} not completed**.`,
      responsiveSummary,
      `- Smallest measured link targets (Playwright touch emulation, CSS pixels; measurement only, not a pass/fail threshold): navigation **${smallestNavigationTarget ? `${smallestNavigationTarget.width}×${smallestNavigationTarget.height}px (${smallestNavigationTarget.label})` : "unavailable"}**; footer **${smallestFooterTarget ? `${smallestFooterTarget.width}×${smallestFooterTarget.height}px (${smallestFooterTarget.label})` : "unavailable"}**.`,
      `- Full-site axe accessibility (generated HTML route test cases from \`_site\`): **${scansCompleted}/${axeTests.length} scans completed; ${axeCounts.passed} passed, ${axeCounts.failed} failed, ${axeCounts.skipped} skipped, ${axeCounts.incomplete} not completed${noAxeViolations ? "; no violations detected" : ""}**.`,
      `- Browser tests overall: **${allBrowserCounts.passed} passed, ${allBrowserCounts.failed} failed, ${allBrowserCounts.skipped} skipped, ${allBrowserCounts.incomplete} not completed** (${tests.length} total).`,
      `- Playwright duration (from JSON report): **${Number.isFinite(duration) ? `${(duration / 1000).toFixed(1)}s` : "not available in report"}**.`,
      "- Sources: route cases come from generated `_site` HTML discovery; responsive routes/viewports come from the Circle-specific test matrix; counts, statuses, target measurements, and durations come from Playwright JSON; build and diagnostic outcomes come from GitHub steps.",
    );
  }

  if (
    !reportUnavailableReason &&
    (routeTests.length === 0 ||
      axeTests.length === 0 ||
      scansCompleted !== axeTests.length ||
      responsiveTests.length === 0 ||
      responsiveCases.length !== responsiveTests.length)
  ) {
    lines.push(
      "- Coverage verification: **failed**; the report must contain smoke and responsive cases, every responsive case must identify its route and viewport, and every generated HTML route case must record a completed axe scan.",
    );
    exitCode = 1;
  }

  if (reportUnavailableReason && env.SMOKE_OUTCOME === "success") {
    lines.push(
      "- Coverage results: **failed verification**; the browser step succeeded but its Playwright report has no usable test cases.",
    );
    exitCode = 1;
  }

  const failedTests = tests.filter((test) => test.status !== "passed");
  if (failedTests.length > 0) {
    lines.push("", "### Non-passing browser tests");
    for (const test of failedTests) {
      lines.push(`- **${test.status}:** \`${test.title}\`.`);
      if (test.diagnostic) {
        lines.push(...test.diagnostic.split("\n").map((line) => `  ${line}`));
      }
    }
    lines.push(
      "- See the exact GitHub step **Run rendered-site smoke tests** for full Playwright/axe diagnostics and stack traces.",
    );
  } else if (reportUnavailableReason) {
    lines.push(
      "- Inspect the exact GitHub step **Run rendered-site smoke tests** for Playwright output and report-generation errors.",
    );
  }

  diagnosticLines(lines, env);
  return { markdown: `${lines.join("\n")}\n`, exitCode };
}

function main() {
  const reportPath = resolve(process.env.RESULTS_FILE ?? "");
  let reportContents;
  if (existsSync(reportPath)) {
    try {
      reportContents = readFileSync(reportPath, "utf8");
    } catch {
      reportContents = null;
    }
  }
  const { markdown, exitCode } = renderSiteRenderingSummary({
    reportContents,
    env: process.env,
  });
  if (process.env.GITHUB_STEP_SUMMARY) {
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, markdown);
  }
  process.stdout.write(markdown);
  process.exitCode = exitCode;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  main();
}

export { parsePlaywrightReport };
