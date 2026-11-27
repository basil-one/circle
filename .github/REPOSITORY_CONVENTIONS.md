# Repository map and checks

Circle's shared quality practice covers the website and paper. The domains are ordered **Common → Site → Paper**; [Quality Stance](QUALITY.md) describes the capabilities, and this guide describes where their implementation lives.

## Find the work

| Area | Workflows | Supporting code | Tests |
| --- | --- | --- | --- |
| Common | `.github/workflows/common-*.yml` | `scripts/common/` | `tests/common/` |
| Site | `.github/workflows/site-*.yml` | `scripts/site/` | `tests/site/` |
| Paper | `.github/workflows/paper-*.yml` | `scripts/paper/` | `tests/paper/` |
| Cross-domain orchestration | `quality-pulse.yml` | `scripts/orchestration/` | `tests/orchestration/` |

Use workflow filenames as capability IDs. A workflow with one substantive job uses its filename stem as that job ID; multi-job workflows qualify each role (`common-dependency-security-review` / `-posture`, `site-publish-build` / `-deploy`). Keep human-facing check names exactly `Domain: Capability`. Quality Pulse is the unqualified Common → Site → Paper coordinator.

Paper source content, configuration, template, PDF builder, release helper, and `build-paper-pdf.sh` remain at their established `scripts/` paths. The shell entry point invokes the historical `scripts/build-pdf.py` and `scripts/plop.paper.config.yaml` paths in the checked-out tree, including when current release tooling resumes a release from an older tag such as v0.2.2. Keep these compatibility paths unless every tagged checkout they serve is deliberately migrated. Paper link dependency pins remain under `scripts/paper-link/`.

## Workflow anatomy and intentional differences

Make the checked-out revision and task context clear; prepare only the needed environment; generate artifacts when required; run the check and focused tests; preserve named evidence; summarize passed, advisory, failed, incomplete, unknown, and skipped outcomes without inferring success from missing evidence.

The order is purposeful, not a template: Site Publish validates and deploys a same-run Pages artifact; Paper Preview builds only for review; Paper Publish is human-initiated and retains its resume/recovery safeguards; Paper Publication Integrity is read-only. Paper validators build independently rather than sharing generated PDFs. Dependency Security separates pull-request review from scheduled/manual alert posture. Performance keeps classification, initial measurement, confirmation, and artifact evidence distinct.

## Run and maintain checks

- `npm run test:quality-pulse` — Pulse outcomes, orchestration, and dependency posture.
- `npm run test:required-checks` — capability names/order, workflow IDs and triggers, script/test path traceability, publication contracts, and repository conventions.
- `python3 -m unittest discover -s tests/paper -p 'test_*.py' -v` — Paper contracts and validator tests. The PDF link tests need the hash-locked dependency in `scripts/paper-link/requirements.txt` and Poppler's `pdftotext`, as in Paper Link Integrity.
- `npm run test:performance-history`, `npm run test:performance-policy`, `npm run test:performance-skip`, and `npm run test:performance-summary` — focused Site Performance checks.
- `npm run test:site-routes` and `npm run test:rendering-summary` — generated-route and browser-summary contracts. The browser suite needs a built `_site` and the installed Playwright browser.
- `bundle exec jekyll build --trace` — production site build.

Common Code Quality runs the workflow contracts and focused tests on pull requests. The executable conventions cover workflow IDs, canonical capability order, checked-in script and package test paths, test placement, and documented navigation. Reviewers still judge conceptual simplicity, teaching clarity, and whether a boundary or exception earns its maintenance cost; these are not reducible to a score.

**Recommendation:** do not add a separate Common: Coherence Integrity capability in this change. Its objective checks are repository-wide source and workflow maintainability contracts already owned by Common Code Quality; another Gate check or Pulse dimension would repeat that responsibility without distinct evidence or an independent operating decision. Reconsider only if evidence shows the conventions need a separate owner, cadence, or response.
