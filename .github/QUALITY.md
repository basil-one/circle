# Quality Stance

**Quality is a continuous practice in service of human connection.**

Technical quality protects people's ability to understand, trust, navigate, and engage. A sustainable quality practice also respects the time and attention of the people caring for the experience.

## Our Stance

- **Human connection is the purpose.** Reliability, accessibility, clarity, and performance serve the reader's experience. A passing score is evidence, not a substitute for human judgment.
- **Quality is sustained.** Relevant changes are checked before merging; conditions that can deteriorate without a code change are revisited over time.
- **Automation brings actionable work to human judgment.** When a problem has a safe, practical remedy, an agent or workflow brings a reviewable pull request. Otherwise it brings a focused finding with evidence, impact, and a recommended next action. Humans decide what matters, approve changes, and merge.
- **Care creates capacity.** Healthy checks stay quiet. Alerts are proportionate, understandable, and worth acting on. The practice reduces maintenance work rather than displacing attention from human connection.

## How We Operate

**On change:** Pull requests are checked against the applicable quality safeguards. Failures require correction or an explicit authorized human decision.

**Over time:** Risks that can change without a code change are revisited. Meaningful findings or proposed remedies come to human judgment; healthy checks stay quiet.

**With judgment:** Human judgment governs intent, editorial decisions, priorities, approval, merge, and publication. Automation supports those decisions with evidence and reviewable proposals; it does not replace them. Privileged automation remains narrowly scoped.

**Through learning:** Useful misses improve coverage. Noisy checks, fragile automation, and maintenance disproportionate to the needs of the work are refined or retired. The practice evolves with evidence.

## Quality Domains

A single quality practice serves the website and the paper.

- **Common:** Shared code quality and dependency security.
- **Site:** Integrity of website development, generation, publication, and the visitor's experience.
- **Paper:** Integrity of paper development, generation, review, and publication.

Domain-specific workflows follow the naming convention `Domain: Capability`. Cross-domain capabilities retain unqualified names.

## Capabilities in Operation

| Capability | Purpose |
| --- | --- |
| [Common: Code Quality](workflows/common-code-quality.yml) | Source quality, workflow correctness, and maintainability. Function complexity is capped at 10. |
| [Common: Dependency Security](workflows/common-dependency-security.yml) | Dependency changes and open vulnerability alerts at moderate severity or higher. |
| [Site: Build Integrity](workflows/site-build-integrity.yml) | Successful production website generation. |
| [Site: Link Integrity](workflows/site-link-integrity.yml) | Internal links, fragments, assets, and external destinations. |
| [Site: SEO Integrity](workflows/site-seo-integrity.yml) | Page titles, canonical URLs, and indexing directives. |
| [Site: Rendering Integrity](workflows/site-rendering-integrity.yml) | Browser verification, accessibility, and responsive usability at 320px, 768px, and 1280px. |
| [Site: Performance Integrity](workflows/site-performance-integrity.yml) | Loading performance, responsiveness, and visual stability across representative pages. |
| [Site: Publish](workflows/site-publish.yml) | Automatic publication of the website using the verified build environment. |
| [Paper: Build Integrity](workflows/paper-build-integrity.yml) | Reliable generation of a fresh, readable paper PDF. |
| [Paper: Content Integrity](workflows/paper-content-integrity.yml) | Verification of sampled source content and citation evidence in the generated PDF. |
| [Paper: Link Integrity](workflows/paper-link-integrity.yml) | Internal navigation, preserved source links, and external destinations in the generated PDF; uncertain external responses are reported as advisories. |
| [Paper: Rendering Integrity](workflows/paper-rendering-integrity.yml) | Verification that every generated PDF page renders without errors or wholly blank output; visual fidelity remains for human review. |
| [Paper: Preview](workflows/paper-preview.yml) | On-demand PDF generation for human review, independent of full quality verification. |
| [Paper: Publish](workflows/paper-publish.yml) | Human-initiated, controlled publication of the paper. |

## Quality Gate

Required checks protect against known defects before changes are merged. Quality safeguards that verify proposed changes are required; safeguards that verify existing published experiences run over time.

Performance Integrity protects against confirmed budget violations and substantial loading regressions against a compatible reviewed reference. Rolling comparisons inform human judgment.

## Publication

Website and paper publication use their respective verified build mechanisms.

The website is published automatically following an approved merge. The paper is published through an explicit human decision.

## Quality Pulse

A [daily workflow](workflows/quality-pulse.yml) coordinates the Common, Site, and Paper safeguards in a single verification cycle and provides a coherent view of quality. It can also be run on demand.

Passed checks, nonblocking advisories, confirmed failures, incomplete checks, and unavailable evidence remain distinguishable. Findings bring actionable evidence to human judgment. A cycle with advisories may succeed, but is not reported as an unconditional pass.

Performance measurements are compared against a reviewed reference and recent compatible measurements.

## Evolution

Quality Gate activation and additional Paper safeguards remain in progress. See [North Star — Circle Quality](https://github.com/basil-one/circle/issues/20).
