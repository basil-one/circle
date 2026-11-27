export const performanceBudgets = Object.freeze({
  mobile: Object.freeze({
    'largest-contentful-paint': 3_000,
    'total-blocking-time': 300,
    'cumulative-layout-shift': 0.1
  }),
  desktop: Object.freeze({
    'largest-contentful-paint': 1_500,
    'total-blocking-time': 150,
    'cumulative-layout-shift': 0.1
  })
});

export const lcpDeteriorationPolicy = Object.freeze({
  minimumPercentage: 25,
  minimumMilliseconds: 500
});

export function median(values) {
  const ordered = [...values].sort((left, right) => left - right);
  if (!ordered.length || ordered.some((value) => !Number.isFinite(value))) {
    throw new Error('A median requires finite measurements.');
  }
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2
    ? ordered[middle]
    : (ordered[middle - 1] + ordered[middle]) / 2;
}

export function formatPerformanceValue(metric, value) {
  if (!Number.isFinite(value)) return 'unavailable';
  return metric === 'cumulative-layout-shift'
    ? value.toFixed(3)
    : value >= 1_000
      ? `${(value / 1_000).toFixed(2)} s`
      : `${Math.round(value)} ms`;
}

function isLcpDeterioration(value, referenceValue) {
  if (!Number.isFinite(value) || !Number.isFinite(referenceValue) || referenceValue <= 0) {
    return false;
  }
  const delta = value - referenceValue;
  return delta > lcpDeteriorationPolicy.minimumMilliseconds &&
    (delta / Math.abs(referenceValue)) * 100 > lcpDeteriorationPolicy.minimumPercentage;
}

function profileViolations(result, referenceResult, referenceAvailable) {
  const violations = [];
  for (const [metric, limit] of Object.entries(performanceBudgets[result.formFactor] ?? {})) {
    const value = result.values?.[metric];
    if (Number.isFinite(value) && value > limit) {
      violations.push({
        route: result.route,
        formFactor: result.formFactor,
        metric,
        criterion: 'absolute',
        value,
        limit
      });
    }
  }

  const value = result.values?.['largest-contentful-paint'];
  const referenceValue = referenceResult?.values?.['largest-contentful-paint'];
  if (referenceAvailable && isLcpDeterioration(value, referenceValue)) {
    violations.push({
      route: result.route,
      formFactor: result.formFactor,
      metric: 'largest-contentful-paint',
      criterion: 'reference-deterioration',
      value,
      referenceValue,
      delta: value - referenceValue,
      deltaPercentage: ((value - referenceValue) / Math.abs(referenceValue)) * 100,
      minimumMilliseconds: lcpDeteriorationPolicy.minimumMilliseconds,
      minimumPercentage: lcpDeteriorationPolicy.minimumPercentage
    });
  }
  return violations;
}

export function assessPerformance(results, referenceResults, referenceAvailable, referenceReason) {
  const references = new Map((referenceResults ?? []).map((result) => [
    `${result.route}\u0000${result.formFactor}`,
    result
  ]));
  const violations = results.flatMap((result) =>
    profileViolations(
      result,
      references.get(`${result.route}\u0000${result.formFactor}`),
      referenceAvailable
    )
  );
  const profiles = [...new Map(violations.map((violation) => [
    `${violation.route}\u0000${violation.formFactor}`,
    { route: violation.route, formFactor: violation.formFactor }
  ])).values()];
  return {
    status: violations.length ? 'confirmation-required' : 'passed',
    referenceAvailable,
    referenceReason,
    referenceResults: referenceAvailable ? (referenceResults ?? []) : [],
    initialViolations: violations,
    confirmationProfiles: profiles
  };
}

function criterionStillViolated(violation, result, referenceResults, referenceAvailable) {
  const value = result?.values?.[violation.metric];
  if (!Number.isFinite(value)) return false;
  if (violation.criterion === 'absolute') return value > violation.limit;
  if (violation.criterion !== 'reference-deterioration' || !referenceAvailable) return false;
  const referenceValue = referenceResults.get(
    `${violation.route}\u0000${violation.formFactor}`
  )?.values?.['largest-contentful-paint'];
  return isLcpDeterioration(value, referenceValue);
}

export function finalizePerformanceAssessment(
  assessment,
  confirmationSummary,
  { compatible = false, reason = 'Confirmation evidence is unavailable.' } = {}
) {
  if (assessment.initialViolations.length === 0) {
    return { ...assessment, status: 'passed', confirmations: [], confirmedViolations: [] };
  }

  const targets = assessment.confirmationProfiles;
  const expected = new Set(targets.map(({ route, formFactor }) => `${route}\u0000${formFactor}`));
  const results = confirmationSummary?.results;
  const resultMap = new Map(
    (Array.isArray(results) ? results : []).map((result) => [
      `${result.route}\u0000${result.formFactor}`,
      result
    ])
  );
  const complete = compatible &&
    confirmationSummary?.complete === true &&
    confirmationSummary.coverage?.runCount === 3 &&
    confirmationSummary.coverage?.routeProfiles === targets.length &&
    confirmationSummary.coverage?.measurementCount === targets.length * 3 &&
    resultMap.size === expected.size &&
    [...expected].every((key) => {
      const result = resultMap.get(key);
      return result &&
        Object.keys(performanceBudgets[result.formFactor] ?? {}).every((metric) =>
          Number.isFinite(result.values?.[metric])
        );
    });

  if (!complete) {
    return {
      ...assessment,
      status: 'incomplete',
      confirmationReason: reason,
      confirmations: assessment.initialViolations.map((violation) => ({
        ...violation,
        result: 'incomplete',
        reason
      })),
      confirmedViolations: []
    };
  }

  const referenceResults = new Map(
    (assessment.referenceResults ?? []).map((result) => [
      `${result.route}\u0000${result.formFactor}`,
      result
    ])
  );
  const confirmations = assessment.initialViolations.map((violation) => {
    const result = resultMap.get(`${violation.route}\u0000${violation.formFactor}`);
    const confirmationValue = result.values[violation.metric];
    const confirmed = criterionStillViolated(
      violation,
      result,
      referenceResults,
      assessment.referenceAvailable
    );
    return {
      ...violation,
      confirmationValue,
      result: confirmed ? 'confirmed' : 'unconfirmed'
    };
  });
  const confirmedViolations = confirmations.filter((item) => item.result === 'confirmed');
  return {
    ...assessment,
    status: confirmedViolations.length ? 'failed' : 'passed',
    confirmations,
    confirmedViolations
  };
}
