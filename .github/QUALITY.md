# Quality Stance

**Quality is a continuous practice in service of human connection.**

Technical quality protects people's ability to understand, trust, navigate, and engage. A sustainable quality practice also respects the time and attention of the people caring for the experience.

## Our Stance

- **Human connection is the purpose.** Reliability, accessibility, clarity, and performance serve the reader's experience. A passing score is evidence, not a substitute for human judgment.
- **Quality is sustained.** Relevant changes are checked before merging; conditions that can deteriorate without a code change are revisited over time.
- **Automation brings actionable work to human judgment.** When a problem has a safe, practical remedy, an agent or workflow brings a reviewable pull request. Otherwise it brings a focused finding with evidence, impact, and a recommended next action. Humans decide what matters, approve changes, and merge.
- **Care creates capacity.** Healthy checks stay quiet. Alerts are proportionate, understandable, and worth acting on. The practice reduces maintenance work rather than displacing attention from human connection.

## How We Operate

**On change:** Pull requests are checked against the safeguards in operation below. Failures require correction or an explicit authorized human decision.

**Over time:** Risks that can change without a code change are revisited. Meaningful findings or proposed remedies come to human judgment; healthy checks stay quiet.

**With judgment:** Human judgment governs intent, editorial decisions, priorities, approval, merge, and publication. Automation supports those decisions with evidence and reviewable proposals; it does not replace them. Privileged automation remains narrowly scoped.

**Through learning:** Useful misses improve coverage. Noisy checks, fragile automation, and maintenance disproportionate to the needs of the work are refined or retired. The practice evolves with evidence.

## Quality Domains

A single quality practice serves the website and the paper.

- **Common:** Shared code quality and dependency security.
- **Site:** Integrity of the generated website and the visitor's experience.
- **Paper:** Integrity of paper development, generation, review, and publication.

Domain-specific workflows follow the naming convention `Domain: Capability`. Cross-domain capabilities retain unqualified names.

## Safeguards in Operation

| Domain | Capability | In operation |
| --- | --- | --- |
| Common | [Common: Code Quality](workflows/common-code-quality.yml) | Checks source and workflow quality, including regression contracts for quality workflows and reports. |
| Common | [Common: Dependency Security](workflows/common-dependency-security.yml) | Reviews dependency changes and checks GitHub Dependabot alerts; unavailable alert evidence is reported as unknown, not clean. |
| Site | [Site: Build Integrity](workflows/site-build-integrity.yml) | Builds the production Jekyll site. |
| Site | [Site: Link Integrity](workflows/site-link-integrity.yml) | Checks generated internal links, fragments, assets, and visitor destinations. |
| Site | [Site: SEO Integrity](workflows/site-seo-integrity.yml) | Checks generated-page titles, canonical URLs, and indexability directives. |
| Site | [Site: Rendering Integrity](workflows/site-rendering-integrity.yml) | Exercises generated pages in browsers, including accessibility and responsive behavior. |
| Site | [Site: Performance Integrity](workflows/site-performance-integrity.yml) | Collects controlled Lighthouse evidence for representative Circle routes; comparisons are informational, not an automatic regression gate. |
| Paper | [Paper: Preview](workflows/paper-preview.yml) | Builds a downloadable PDF for paper-related pull requests and on demand, supporting human review before merge. |
| Paper | [Paper: Publish](workflows/paper-publish.yml) | Human-initiated publication from `main`, with version validation, PDF generation, changelog promotion, tagging, and release safeguards. |

The seven Common and Site workflows also run independently on pull requests targeting `main` and can be dispatched manually. Their substantive job names are the required check names for a future Quality Gate:

- `Common: Code Quality`
- `Common: Dependency Security`
- `Site: Build Integrity`
- `Site: Link Integrity`
- `Site: SEO Integrity`
- `Site: Rendering Integrity`
- `Site: Performance Integrity`

### Quality Gate

The checks are implemented, but no repository ruleset has been activated by this change. A repository administrator must configure and review a ruleset for `main` that requires pull requests, human approval, and the seven named checks above. The checks must be demonstrated on a pull request before the ruleset is activated. Preserve human merge authority and do not add a broad Actions bypass.

Paper: Publish currently commits its changelog update directly to `main`. A ruleset that requires pull requests will block that push. Before activating such a ruleset, a human must approve a PR-based release path that preserves publication review and release safeguards; no bypass or Paper workflow change is included here.

### Quality Pulse

[Quality Pulse](workflows/quality-pulse.yml) is the sole scheduled workflow (daily at 03:17 UTC) and can also be started manually. It runs the seven Common and Site checks in parallel, waits for them to settle, and reports same-cycle results. Passed, failed, intentionally skipped, incomplete or cancelled, missing, and unavailable evidence remain distinct. A failed or incomplete cycle is never reported as healthy. Each capability workflow remains independently available for pull-request checks and manual diagnostics.

### Circle-specific adaptation

Site audits follow Circle's generated content and routes, including `/moves/` and `/lenses/`; browser route discovery and representative Lighthouse pages are based on Circle rather than Agency. Performance evidence uses `/`, `/moves/`, `/lenses/`, and `/method/`. No Agency performance measurements or baseline were imported: the fixed reference is pending a reviewed successful measurement from `main`. Measurement may be intentionally skipped only for narrowly classified prose and title/description edits to existing move or lens pages; uncertain or structural changes are measured.

The reference implementation was inspected at Agency revision [`30c77c677c2e9e4186bdf0f3f7f44525e7d91de1`](https://github.com/basil-one/agency/tree/30c77c677c2e9e4186bdf0f3f7f44525e7d91de1). These route, measurement, and skip-classification adaptations are Circle-specific and are identified for human review and possible later reconciliation with Agency.

Additional Paper quality safeguards and daily PDF generation remain planned and are not part of this increment. See [North Star — Circle Quality](https://github.com/basil-one/circle/issues/20) for the progression.
