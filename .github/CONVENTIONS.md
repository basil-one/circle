# Repository Conventions

Circle's shared quality practice covers the website and paper. The domains are ordered **Common → Site → Paper**; [Quality Stance](QUALITY.md) describes the capabilities, and this guide describes where their implementation lives.

## Find the work

| Area | Workflows | Supporting code | Tests |
| --- | --- | --- | --- |
| Common | `.github/workflows/common-*.yml` | `scripts/common/` | `tests/common/` |
| Site | `.github/workflows/site-*.yml` | `scripts/site/` | `tests/site/` |
| Paper | `.github/workflows/paper-*.yml` | `scripts/paper/` | `tests/paper/` |
| Cross-domain orchestration | `quality-pulse.yml`, `release-publish.yml` | `scripts/orchestration/` | `tests/orchestration/` |

Use workflow filenames as capability IDs. A workflow with one substantive job uses its filename stem as that job ID; multi-job workflows qualify each role (`common-dependency-security-review` / `-posture`, `site-publish-build` / `-deploy`). Keep human-facing check names exactly `Domain: Capability`. Quality Pulse and Release Publish are unqualified orchestration capabilities.

Production helpers live under `scripts/{common,site,paper,orchestration}/`; automated tests live under matching `tests/` domains. Browser test support stays under `tests/site/browser/`, while production route discovery and matrices live in `scripts/site/`. Shared link safety rules live in `scripts/common/`.

Paper source content, configuration, and template live under `scripts/`. The shared Paper workflows and local development use `scripts/build-paper-pdf.sh` and `scripts/build-pdf.py`. Paper link dependency pins live at `scripts/paper/requirements-links.txt`.

## Workflow anatomy and intentional differences

Make the checked-out revision and task context clear; prepare only the needed environment; generate artifacts when required; run the check and focused tests; preserve named evidence; summarize passed, advisory, failed, incomplete, unknown, and skipped outcomes without inferring success from missing evidence.

Use release version tags for external actions and one version of each shared dependency across workflows, unless a documented compatibility constraint requires otherwise.

The order is purposeful, not a template: Site Publish validates and deploys a same-run Pages artifact; Paper Preview builds only for review; Paper Publication Integrity is read-only. Paper validators build independently rather than sharing generated PDFs. Dependency Security separates pull-request review from scheduled/manual alert posture. Performance keeps classification, initial measurement, confirmation, and artifact evidence distinct. Only Quality Pulse owns the daily schedule; proposed-change safeguards retain pull-request checks, and verification workflows support manual diagnostics. Keep permissions narrow and preserve ordinary pull-request protection.

Site Link Integrity reports uncertain external responses as nonblocking advisories; known defects retain failure precedence. Site Performance Integrity measures four representative routes on mobile and desktop with three-run Lighthouse medians and confirms initial violations with three new measurements per affected route/profile. Incomplete confirmation is not a pass. The executable policy defines the budgets and substantial deterioration criteria; the canonical fixed reference is [site-performance-reference.json](site-performance-reference.json). Use complete, successful, compatible main evidence after human review; only successful scheduled-main evidence contributes to informational rolling history. Retain compatible historical readers.

## Run and maintain checks

- `npm run test:quality-pulse` — Pulse outcomes, orchestration, and dependency posture.
- `npm run test:coherence-integrity` — capability inventory/order, workflow IDs, repository boundaries, script/test placement, reference traceability, and negative fixtures.
- `npm run test:required-checks` — required workflow triggers/jobs, publication contracts, and operational summary behavior.
- `python3 -m unittest discover -s tests/paper -p 'test_*.py' -v` — Paper contracts and validator tests. The PDF link tests need the hash-locked dependency in `scripts/paper/requirements-links.txt` and Poppler's `pdftotext`, as in Paper Link Integrity.
- `npm run test:performance-history`, `npm run test:performance-policy`, `npm run test:performance-skip`, and `npm run test:performance-summary` — focused Site Performance checks.
- `npm run test:site-routes` and `npm run test:rendering-summary` — generated-route and browser-summary contracts. The browser suite needs a built `_site` and the installed Playwright browser.
- `bundle exec jekyll build --trace` — production site build.

## Changelog and releases

Record notable outcomes under Unreleased in [CHANGELOG.md](../CHANGELOG.md) as work is merged. Preserve published entries.

With human authorization, dispatch Release Publish from `main` with a stable version (`X.Y.Z`). Review and merge the changelog-only PR through the Quality Gate, approving its checks if requested. The workflow polls once per minute for up to 30 minutes and requires twelve successful checks on the exact PR head and a matching merged tree on `main`. Rerun the same version to resume an interrupted, unpublished release. Update a stale branch through GitHub and reconcile conflicting release evidence before rerunning.

If GitHub denies PR creation, a repository owner can check **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests**. See the [paper guide](../scripts/README.md#pdfs-and-interrupted-drafts) for asset verification and draft recovery.

## Sustaining coherence

Common: Coherence Integrity runs the architectural contracts on pull requests and in Quality Pulse. It checks the capability inventory/order and job IDs; resolves direct script, Python module, unittest discovery, package-script, and documented local path references; enforces domain script/test naming and placement; and rejects production imports from tests or across Site/Paper ownership. Common: Code Quality retains workflow correctness, source lint/complexity, and operational regression tests. Site Build verifies that the generated `_site` excludes repository scripts and tests. When changing a capability, consider its relationship to the whole: follow the existing conventions, update affected references and contracts, and make intentional exceptions explicit. Reviewers still judge conceptual simplicity, teaching clarity, and whether a boundary or exception earns its maintenance cost; these are not reducible to a score.
