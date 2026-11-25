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
| Paper | [Paper: Preview](workflows/paper-preview.yml) | Builds a downloadable PDF for paper-related pull requests and on demand, supporting human review before merge. |
| Paper | [Paper: Publish](workflows/paper-publish.yml) | Human-initiated publication from `main`, with version validation, PDF generation, changelog promotion, tagging, and release safeguards. |

The Common and Site safeguards, cross-domain Quality Gate, daily Quality Pulse, and additional Paper safeguards are planned, not yet established. See [North Star — Circle Quality](https://github.com/basil-one/circle/issues/20) for the progression.

The intended daily Quality Pulse includes website and paper generation so technical problems can be discovered and addressed independently of creative work.
