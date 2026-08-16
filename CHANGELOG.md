# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) for repository tags.

For licensing, see `LICENSE.md`.

## [Unreleased]

## [0.1.6] - 2026-08-16

### Changed
- Replaced the **Session Intention Reflection** lens with **Paradigm Awareness Reflection**, a new lens for noticing the paradigm (Survival, Neutrality, Generative) from which a person, group, or organization is responding under uncertainty, disruption, conflict, or change, and the thresholds (Courage, Curiosity) between them.
- Added **Paradigm Awareness Reflection** as a practical resource within **Integrate the Conversation**, where surfacing protective positions without prematurely resolving them is most relevant.
- Refined **Paradigm Awareness Reflection** to stay focused on noticing rather than resolving: narrowed the exercise to reflect on the situation, the paradigm operating within it, and what that paradigm may be revealing (protection under Survival, or curiosity under Neutrality), and aligned its Reflection/Sharing structure with **Sense Making Reflection** and **Energy Awareness Reflection**.
- Refreshed imagery across several lenses, moves, and pages (including new artwork for **Paradigm Awareness Reflection**) for improved visual quality.

### Removed
- Removed the **Session Intention Reflection** practical-resource link from **Reconcile the Intention**; its session-preparation purpose has no direct equivalent in the new lens set.

## [0.1.5] - 2026-08-09

### Changed
- PDF build now sources its imagery from the smaller web-resolution images (`images/`) instead of `images/full/`, cutting the generated PDF from ~28MB to ~2.4MB with no meaningful quality loss for on-screen reading.

## [0.1.4] - 2026-08-09

### Removed
- Stopped committing the generated paper PDF (`assets/pdfs/circle3-paper.pdf` is now git-ignored, built locally/by CI, and distributed via GitHub Releases). Historical PDF blobs were also purged from git history to shrink the repository.

## [0.1.3] - 2026-08-09

### Added
- GitHub Actions workflow ([.github/workflows/release-pdf.yml](.github/workflows/release-pdf.yml)) that builds the paper PDF and attaches it to the GitHub Release when a `v*.*.*` tag is pushed.

### Changed
- The paper download link now points to `https://github.com/basil-one/circle/releases/latest/download/circle3-paper.pdf` instead of the committed `assets/pdfs/circle3-paper.pdf`, so it always resolves to the latest release.
- Refined language across **Establish the Circle**, **Integrate the Conversation**, and **Reconcile the Intention** for clarity and coherence, expanding the Problem, Forces, Solution, and Resulting Context sections with fuller explanations.
- Condensed **Related Patterns** cross-links into inline descriptions for **Establish the Circle**, **Integrate the Conversation**, and **Reconcile the Intention**.

### Removed
- Removed the **Two Sides Reflection** and **Accessing Energy Patterns** practical-resource links from **Integrate the Conversation**.

### Fixed
- Regenerated `assets/pdfs/circle3-paper.pdf` so the release PDF matches the current content.

## [0.1.2] - 2026-07-30

### Changed
- Reframed the second Guiding Move from **Balance the Conversation** to **Integrate the Conversation**.
- Revised the concluding material and appended an appendix section for the Leadership Lenses.
- Refined language in the Origin Story and examples for clarity and consistency.
- Updated section headings and reflective framing across Lenses.

### Added
- New imagery and references for **Integrate the Conversation**.
- Expanded PDF build behavior to better support the current paper structure and output.

### Fixed
- Updated links and supporting references related to **Integrate the Conversation**.
- Regenerated `assets/pdfs/circle3-paper.pdf` so the release PDF matches the current content.

## [0.1.1] - 2026-06-14

### Changed
- Markdown formatting consistency across Moves and Lenses (headings, list structure, small copy edits).
- Standardized/introduced a **Framing** section in Lenses.
- Clarified **Reconcile the Intention** pitfalls by adding mitigation notes.

### Added
- Analytics attributes for external links (e.g. `data-ga-event="external_link"`) for consistent outbound-link tracking.
- Expanded PDF build documentation and troubleshooting steps in `scripts/README.md`.

### Fixed
- Regenerated `assets/pdfs/circle3-paper.pdf` so the release PDF matches the current content.

## [0.1.0] - 2026-06-12

### Notes
- First public draft release of Circle3™ as submitted to PLoP/PLoPCon 2026.
- **Status:** Pre–Writer’s Workshop snapshot. Changes are expected before and after the conference.

### Added
- Paper PDF attached as a release asset.
- Three Guiding Moves™ (patterns): Establish the Circle, Balance the Conversation, Reconcile the Intention.
- Three Leadership Lenses™ (reflective exercises): Sense-Making, Energy Awareness, Session Intention.

### Feedback requested (Writer’s Workshop)
1. Pattern quality: Are the Forces credible and the solutions repeatable?
2. Language design: Do the three Moves form a coherent minimum language (relationships/sequence)?
3. Fit: Do the Lenses belong in the submission, and is the framing clear for a PLoP/PLoPCon workshop?


[Unreleased]: https://github.com/basil-one/circle/compare/v0.1.6...HEAD
[0.1.6]: https://github.com/basil-one/circle/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/basil-one/circle/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/basil-one/circle/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/basil-one/circle/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/basil-one/circle/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/basil-one/circle/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/basil-one/circle/releases/tag/v0.1.0
