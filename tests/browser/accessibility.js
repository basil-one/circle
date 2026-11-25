import AxeBuilder from '@axe-core/playwright';

export async function assertNoAccessibilityViolations(page, pageLabel, testInfo) {
  const { violations } = await new AxeBuilder({ page }).analyze();
  testInfo?.annotations.push({ type: 'axe-scan-complete' });

  if (violations.length === 0) return;

  const findings = violations.flatMap((violation) =>
    violation.nodes.map((node) => {
      const element = node.target.join(', ') || node.html;
      const nextStep =
        node.failureSummary?.replace(/\s+/g, ' ').trim() ??
        `Review the rule guidance at ${violation.helpUrl}.`;

      return [
        `  Element: ${element}`,
        `  Rule: ${violation.id} (${violation.impact ?? 'impact not specified'}) - ${violation.help}`,
        `  Next step: ${nextStep}`,
        `  Guidance: ${violation.helpUrl}`
      ].join('\n');
    })
  );

  throw new Error(
    [`Accessibility violations detected on ${pageLabel}.`, ...findings].join('\n')
  );
}
