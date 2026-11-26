import assert from 'node:assert/strict';
import test from 'node:test';
import {
  assessPerformance,
  finalizePerformanceAssessment,
  lcpDeteriorationPolicy,
  median,
  performanceBudgets
} from '../scripts/site_performance_policy.mjs';

const route = '/';

function result(formFactor = 'mobile', values = {}) {
  return {
    route,
    formFactor,
    values: {
      'largest-contentful-paint': 1_000,
      'total-blocking-time': 50,
      'cumulative-layout-shift': 0,
      ...values
    }
  };
}

function assessment(results, referenceResults = [], referenceAvailable = false) {
  return assessPerformance(
    results,
    referenceResults,
    referenceAvailable,
    referenceAvailable ? undefined : 'Reviewed fixed reference is unavailable.'
  );
}

function confirmation(results) {
  return {
    complete: true,
    coverage: {
      runCount: 3,
      routeProfiles: results.length,
      measurementCount: results.length * 3
    },
    results
  };
}

test('passes medians at and below every profile-specific absolute budget', () => {
  assert.equal(median([3_001, 2_999, 3_000]), 3_000);
  const results = ['mobile', 'desktop'].map((formFactor) =>
    result(formFactor, performanceBudgets[formFactor])
  );
  assert.equal(assessment(results).status, 'passed');
});

test('uses strict greater-than boundaries for every absolute budget', () => {
  for (const formFactor of ['mobile', 'desktop']) {
    for (const [metric, limit] of Object.entries(performanceBudgets[formFactor])) {
      const exact = assessment([result(formFactor, { [metric]: limit })]);
      assert.equal(exact.initialViolations.length, 0, `${formFactor} ${metric} at limit`);
      const over = assessment([result(formFactor, { [metric]: limit + 0.001 })]);
      assert.ok(
        over.initialViolations.some((violation) => violation.metric === metric),
        `${formFactor} ${metric} above limit`
      );
    }
  }
});

test('applies LCP deterioration only when both strict thresholds are exceeded', () => {
  const reference = [result('mobile', { 'largest-contentful-paint': 1_000 })];
  for (const value of [
    1_000 + lcpDeteriorationPolicy.minimumMilliseconds,
    1_000 * (1 + lcpDeteriorationPolicy.minimumPercentage / 100)
  ]) {
    const policy = assessment(
      [result('mobile', { 'largest-contentful-paint': value })],
      reference,
      true
    );
    assert.equal(
      policy.initialViolations.some((violation) => violation.criterion === 'reference-deterioration'),
      false
    );
  }
  const increaseOnly = assessment(
    [result('mobile', { 'largest-contentful-paint': 3_501 })],
    [result('mobile', { 'largest-contentful-paint': 3_000 })],
    true
  );
  assert.equal(
    increaseOnly.initialViolations.some((violation) => violation.criterion === 'reference-deterioration'),
    false
  );
  const percentageOnly = assessment(
    [result('mobile', { 'largest-contentful-paint': 1_251 })],
    reference,
    true
  );
  assert.equal(
    percentageOnly.initialViolations.some((violation) => violation.criterion === 'reference-deterioration'),
    false
  );
  const violation = assessment(
    [result('mobile', { 'largest-contentful-paint': 1_501 })],
    reference,
    true
  );
  assert.equal(violation.initialViolations[0].criterion, 'reference-deterioration');
  assert.equal(violation.initialViolations[0].delta, 501);
  assert.equal(violation.initialViolations[0].deltaPercentage, 50.1);
});

test('does not apply relative TBT or CLS gates', () => {
  const reference = [result('mobile', {
    'total-blocking-time': 10,
    'cumulative-layout-shift': 0.001
  })];
  const current = result('mobile', {
    'total-blocking-time': 299,
    'cumulative-layout-shift': 0.099
  });
  assert.equal(assessment([current], reference, true).initialViolations.length, 0);
});

test('missing or incompatible references disable only relative LCP comparison', () => {
  const policy = assessment([
    result('mobile', { 'largest-contentful-paint': 3_001 })
  ]);
  assert.equal(policy.status, 'confirmation-required');
  assert.equal(policy.referenceAvailable, false);
  assert.equal(policy.initialViolations.length, 1);
  assert.equal(policy.initialViolations[0].criterion, 'absolute');
  assert.match(policy.referenceReason, /unavailable/);
});

test('an initial absolute violation is unconfirmed at the exact limit and fails when repeated', () => {
  const initial = assessment([
    result('mobile', { 'largest-contentful-paint': 3_001 })
  ]);
  const target = result('mobile', { 'largest-contentful-paint': 3_000 });
  const unconfirmed = finalizePerformanceAssessment(
    initial,
    confirmation([target]),
    { compatible: true }
  );
  assert.equal(unconfirmed.status, 'passed');
  assert.equal(unconfirmed.confirmations[0].result, 'unconfirmed');

  const confirmed = finalizePerformanceAssessment(
    initial,
    confirmation([result('mobile', { 'largest-contentful-paint': 3_001 })]),
    { compatible: true }
  );
  assert.equal(confirmed.status, 'failed');
  assert.equal(confirmed.confirmedViolations.length, 1);
  assert.equal(confirmed.confirmedViolations[0].confirmationValue, 3_001);
});

test('confirms relative LCP only when both deterioration thresholds persist', () => {
  const reference = [result('mobile', { 'largest-contentful-paint': 1_000 })];
  const initial = assessment(
    [result('mobile', { 'largest-contentful-paint': 1_501 })],
    reference,
    true
  );
  assert.equal(
    finalizePerformanceAssessment(
      initial,
      confirmation([result('mobile', { 'largest-contentful-paint': 1_500 })]),
      { compatible: true }
    ).status,
    'passed'
  );
  const confirmed = finalizePerformanceAssessment(
    initial,
    confirmation([result('mobile', { 'largest-contentful-paint': 1_501 })]),
    { compatible: true }
  );
  assert.equal(confirmed.status, 'failed');
  assert.equal(confirmed.confirmedViolations[0].criterion, 'reference-deterioration');
});

test('incomplete or failed confirmation evidence never passes an initial violation', () => {
  const initial = assessment([
    result('mobile', { 'total-blocking-time': 301 })
  ]);
  for (const [summary, options] of [
    [undefined, { compatible: false, reason: 'Confirmation run failed.' }],
    [confirmation([]), { compatible: true, reason: 'A route/profile was omitted.' }],
    [confirmation([result('mobile')]), { compatible: false, reason: 'Environment changed.' }],
    [{
      ...confirmation([result('mobile')]),
      coverage: { runCount: 2, routeProfiles: 1, measurementCount: 2 }
    }, { compatible: true, reason: 'Only two confirmation samples completed.' }]
  ]) {
    const outcome = finalizePerformanceAssessment(initial, summary, options);
    assert.equal(outcome.status, 'incomplete');
    assert.equal(outcome.confirmations[0].result, 'incomplete');
    assert.match(outcome.confirmationReason, /failed|omitted|changed|samples/);
  }
});

test('confirms violations independently across multiple metrics in one route/profile', () => {
  const initial = assessment([
    result('mobile', {
      'largest-contentful-paint': 3_001,
      'total-blocking-time': 301
    })
  ]);
  assert.equal(initial.initialViolations.length, 2);
  assert.equal(initial.confirmationProfiles.length, 1);
  const outcome = finalizePerformanceAssessment(
    initial,
    confirmation([result('mobile', {
      'largest-contentful-paint': 3_002,
      'total-blocking-time': 300
    })]),
    { compatible: true }
  );
  assert.equal(outcome.status, 'failed');
  assert.deepEqual(outcome.confirmations.map(({ result: status }) => status), [
    'confirmed',
    'unconfirmed'
  ]);
});
