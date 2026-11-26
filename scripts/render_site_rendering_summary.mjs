import { appendFileSync, existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import {
  discoverHtmlRoutes,
  responsiveViewports,
} from "../tests/browser/site-routes.js";

const routeSmokeSuffix = " is present in generated output and loads";
const axeSuffix = " has no detectable accessibility violations";
const responsivePrefix = "Responsive usability: ";
const responsivePattern = /^Responsive usability: (.+) at (\d+)px$/;

function validateAnnotation(annotation, title) {
  if (!annotation || typeof annotation !== "object" || Array.isArray(annotation)) {
    return `Test "${title}" contains a non-object annotation.`;
  }
  if (typeof annotation.type !== "string" || !annotation.type.trim()) {
    return `Test "${title}" contains an annotation without a valid type.`;
  }
  if (
    annotation.description !== undefined &&
    typeof annotation.description !== "string"
  ) {
    return `Test "${title}" has a non-string "${annotation.type}" annotation description.`;
  }
  if (annotation.type === "responsive-hit-area") {
    const match =
      typeof annotation.description === "string" &&
      /^(navigation|footer):(.+):(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$/.exec(
        annotation.description,
      );
    if (
      !match ||
      !Number.isFinite(Number(match[3])) ||
      !Number.isFinite(Number(match[4])) ||
      Number(match[3]) <= 0 ||
      Number(match[4]) <= 0
    ) {
      return `Test "${title}" has a malformed responsive-hit-area annotation; expected navigation/footer:label:WIDTHxHEIGHT with positive pixel dimensions.`;
    }
  }
  return undefined;
}

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
    const annotationIssues = [];
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
          const annotations = test.annotations ?? [];
          for (const annotation of annotations) {
            const issue = validateAnnotation(annotation, spec.title);
            if (issue) annotationIssues.push(issue);
          }
          tests.push({
            title: spec.title,
            status: result?.status ?? "not completed",
            duration: result?.duration,
            annotations,
            axeScanned: annotations.some(
              (annotation) => annotation?.type === "axe-scan-complete",
            ),
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
      ? {
          tests,
          duration: report.stats?.duration ?? report.duration,
          annotationIssues,
        }
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

function collectCoverageIssues({
  tests,
  expectedRoutes,
  expectedViewports,
  annotationIssues,
}) {
  const issues = [...annotationIssues];
  if (!Array.isArray(expectedRoutes) || expectedRoutes.length === 0) {
    issues.push(
      "Expected generated HTML routes are unavailable; verify Jekyll output and route discovery.",
    );
    return issues;
  }
  if (
    !Array.isArray(expectedViewports) ||
    expectedViewports.length === 0 ||
    expectedViewports.some((width) => !Number.isInteger(width) || width <= 0)
  ) {
    issues.push(
      "Expected responsive viewport definitions are unavailable or invalid; inspect the shared browser test matrix.",
    );
    return issues;
  }

  const routeSet = new Set(expectedRoutes);
  const viewportSet = new Set(expectedViewports);
  const smoke = new Map();
  const axe = new Map();
  const responsive = new Map();
  const addCase = (cases, key, item) => {
    const entries = cases.get(key) ?? [];
    entries.push(item);
    cases.set(key, entries);
  };

  for (const item of tests) {
    if (item.title.endsWith(routeSmokeSuffix)) {
      const route = item.title.slice(0, -routeSmokeSuffix.length);
      addCase(smoke, route, item);
      if (!routeSet.has(route)) {
        issues.push(`Unexpected generated-route smoke case "${item.title}".`);
      }
    } else if (item.title.endsWith(axeSuffix)) {
      const route = item.title.slice(0, -axeSuffix.length);
      addCase(axe, route, item);
      if (!routeSet.has(route)) {
        issues.push(`Unexpected full-site axe case "${item.title}".`);
      }
    } else if (item.title.startsWith(responsivePrefix)) {
      const match = responsivePattern.exec(item.title);
      if (!match) {
        issues.push(
          `Malformed responsive case "${item.title}"; expected "Responsive usability: ROUTE at WIDTHpx".`,
        );
        continue;
      }
      const [, route, widthText] = match;
      const width = Number(widthText);
      const key = `${route}\u0000${width}`;
      addCase(responsive, key, item);
      if (!routeSet.has(route) || !viewportSet.has(width)) {
        issues.push(
          `Unexpected responsive case "${item.title}"; route and viewport must belong to generated HTML routes and the shared viewport matrix.`,
        );
      }
    }
  }

  const requireOnePassingCase = (cases, key, label, display = key) => {
    const entries = cases.get(key) ?? [];
    if (entries.length === 0) {
      issues.push(`Missing ${label} evidence for "${display}".`);
      return;
    }
    if (entries.length > 1) {
      issues.push(
        `Duplicate ${label} evidence for "${display}" (${entries.length} cases).`,
      );
    }
    if (entries.some((entry) => entry.status !== "passed")) {
      issues.push(
        `Incomplete ${label} evidence for "${display}"; every expected case must complete and pass.`,
      );
    }
    return entries;
  };

  for (const route of expectedRoutes) {
    requireOnePassingCase(smoke, route, "generated-route smoke");
    const axeCases = requireOnePassingCase(axe, route, "axe");
    if (axeCases?.some((item) => !item.axeScanned)) {
      issues.push(
        `Axe scan annotation is missing for "${route}"; a completed scan is required for every generated page.`,
      );
    }
    for (const width of expectedViewports) {
      const responsiveCases = requireOnePassingCase(
        responsive,
        `${route}\u0000${width}`,
        "responsive",
        `${route} at ${width}px`,
      );
      if (responsiveCases?.some((item) => !item.annotations.some(
        (annotation) => annotation.type === "responsive-hit-area",
      ))) {
        issues.push(
          `Responsive hit-area evidence is missing for "${route}" at ${width}px.`,
        );
      }
    }
  }
  return issues;
}

export function renderSiteRenderingSummary({
  reportContents,
  env = {},
  expectedRoutes,
  expectedViewports = responsiveViewports,
}) {
  const {
    tests,
    reason: reportUnavailableReason,
    duration,
    annotationIssues = [],
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
    .map((test) => test.title.match(responsivePattern))
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
      .filter((annotation) => annotation?.type === "responsive-hit-area")
      .map((annotation) => annotation.description),
  );
  const smallestHitArea = (region) => {
    const areas = hitAreas
      .filter(
        (area) => typeof area === "string" && area.startsWith(`${region}:`),
      )
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
  const coverageIssues = reportUnavailableReason
    ? []
    : collectCoverageIssues({
        tests,
        expectedRoutes,
        expectedViewports,
        annotationIssues,
      });
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

  if (coverageIssues.length > 0) {
    lines.push("- Coverage verification: **failed**;");
    lines.push(...coverageIssues.map((issue) => `  - ${issue}`));
    if (env.SMOKE_OUTCOME === "success") exitCode = 1;
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
  let expectedRoutes;
  try {
    expectedRoutes = discoverHtmlRoutes(
      resolve(process.env.SITE_DIRECTORY ?? "_site"),
    );
  } catch {
    expectedRoutes = undefined;
  }
  const { markdown, exitCode } = renderSiteRenderingSummary({
    reportContents,
    env: process.env,
    expectedRoutes,
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
