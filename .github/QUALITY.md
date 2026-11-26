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
| Common | [Common: Code Quality](workflows/common-code-quality.yml) | Validates Ruby Gemfile syntax and GitHub Actions with `jactionlint`; Super-Linter checks selected site, workflow, Python, JavaScript, and MJS sources. Ruff C901 complexity remains capped at 10. Regression tests cover the Quality Pulse, required-check contract, and generated-route discovery. |
| Common | [Common: Dependency Security](workflows/common-dependency-security.yml) | Pull-request dependency review fails at moderate-or-higher severity across runtime, development, and unknown scopes. Scheduled/manual alert posture checks report missing API or permission evidence as unknown, never clean; the posture artifact is retained for 14 days. |
| Site | [Site: Build Integrity](workflows/site-build-integrity.yml) | Builds the production Jekyll site and verifies the repository-only `CHANGELOG.md` is not emitted as `/CHANGELOG.html`; the source changelog remains available to Paper: Publish. |
| Site | [Site: Link Integrity](workflows/site-link-integrity.yml) | Audits generated internal links, fragments, assets, and visitor destinations. Confirmed likely external failures are actionable; inconclusive DNS/network responses remain separately reported rather than silently treated as valid. |
| Site | [Site: SEO Integrity](workflows/site-seo-integrity.yml) | Audits each generated page for one nonempty title, one canonical URL matching its route, and absence of unintended `noindex`/`none` directives. |
| Site | [Site: Rendering Integrity](workflows/site-rendering-integrity.yml) | Discovers all generated HTML routes for browser smoke tests and axe accessibility scans. Responsive checks cover every generated route at 320px, 768px, and 1280px, including page overflow and navigation/footer operability. |
| Site | [Site: Performance Integrity](workflows/site-performance-integrity.yml) | Measures four Circle routes at mobile and desktop profiles, three runs each (24 measurements), and compares FCP, LCP, speed index, TBT, and CLS. Fixed and rolling comparisons are informational, not an automatic regression gate. |
| Paper | [Paper: Preview](workflows/paper-preview.yml) | Builds a downloadable PDF for paper-related pull requests and on demand, supporting human review before merge. |
| Paper | [Paper: Publish](workflows/paper-publish.yml) | Human-initiated publication from `main`, with version validation, PDF generation, changelog promotion, tagging, and release safeguards. |

Routine build, link, SEO, and browser findings are reported in workflow summaries and logs. They do not upload separate retained artifacts. Dependency posture JSON is retained for 14 days. Performance run reports are retained for 14 days, while the comparison summary is retained for 90 days. Rolling comparison history considers up to 28 compatible successful daily `main` summaries; pull-request and manual runs do not enter that history. Missing, expired, incomplete, or incompatible evidence is reported as unavailable rather than substituted with zero.

The six Agency-required Common and Site PR checks are emitted by substantive jobs and can be dispatched manually:

- `Common: Code Quality`
- `Common: Dependency Security`
- `Site: Build Integrity`
- `Site: Link Integrity`
- `Site: SEO Integrity`
- `Site: Rendering Integrity`

### Quality Gate

The six checks above follow Agency's required-check contract. `Site: Performance Integrity` remains a substantive PR and Quality Pulse capability, but is intentionally not required by that contract: its fixed and rolling performance comparisons are informational, not threshold-based pass/fail gates. Requiring a seventh check would be a Circle-specific departure that needs human review, not an assumed default.

The checks are implemented, but no repository ruleset has been activated by this change. A repository administrator must configure and review a ruleset for `main` that requires pull requests, human approval, and the exact six check names above. Demonstrate the checks on a pull request before activating the ruleset. Preserve human merge authority and do not add a broad Actions bypass.

Paper: Publish currently commits its changelog update directly to `main`. A ruleset that requires pull requests will block that push. The publication and ruleset implications require a separate human decision before activation. This increment does not change Paper workflows, create a PR-based publication path, or add bypass permissions.

### Quality Pulse

[Quality Pulse](workflows/quality-pulse.yml) is the sole scheduled workflow (daily at 03:17 UTC) and can also be started manually. It runs the seven Common and Site checks in parallel, waits for them to settle, and reports same-cycle results. Passed, failed, intentionally skipped, incomplete or cancelled, missing, and unavailable evidence remain distinct. A failed or incomplete cycle is never reported as healthy. Each capability workflow remains independently available for pull-request checks and manual diagnostics.

### Circle-specific adaptation

Site audits follow Circle's generated content and routes, including `/moves/` and `/lenses/`; browser route discovery and representative Lighthouse pages are based on Circle rather than Agency. Performance evidence uses `/`, `/moves/`, `/lenses/`, and `/method/`. No Agency performance measurements or baseline were imported; a fixed reference is pending review of successful `main` evidence. Measurement may be intentionally skipped only for narrowly classified prose and title/description edits to existing move or lens pages; uncertain or structural changes are measured. Available fixed and rolling comparisons remain informational and do not fail a check solely because of a performance delta.

The Super-Linter filter intentionally adds Circle's `scripts/*.mjs`, `tests/*.{js,mjs}`, and `playwright.config.js` to the applicable source coverage because Circle's quality tooling and browser tests use these languages. It continues to include the Python sources/tests, site CSS/JavaScript, workflow and linter configuration, and Jekyll configuration, while excluding generated output and installed dependencies.

The reference implementation was inspected at Agency revision [`30c77c677c2e9e4186bdf0f3f7f44525e7d91de1`](https://github.com/basil-one/agency/tree/30c77c677c2e9e4186bdf0f3f7f44525e7d91de1). Circle-specific routes, performance skip classification, JS/MJS source selection, the `/CHANGELOG.html` exclusion, and the distinction between all seven operating capabilities and Agency's six required PR checks are identified for human review and possible later reconciliation with Agency.

Additional Paper quality safeguards and daily PDF generation remain planned and are not part of this increment. See [North Star — Circle Quality](https://github.com/basil-one/circle/issues/20) for the progression.
