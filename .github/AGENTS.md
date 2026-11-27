# Working on GitHub automation

Apply the repository's [root guidance](../AGENTS.md) as well. This file adds expectations for quality and publication workflows under `.github/` and related automation in `scripts/` and `tests/`. The [Quality Stance](QUALITY.md) explains intent; executable workflows and tests define current behavior.

## Map the existing contracts

- Before editing, inspect the relevant workflow triggers (`pull_request`, `schedule`, `workflow_dispatch`, `workflow_call`), job names, required PR check contexts, permissions, concurrency, artifacts, and downstream consumers.
- Use [CONVENTIONS.md](CONVENTIONS.md) for canonical domain boundaries, workflow identities, and supporting script and test placement.
- Preserve substantive required checks and their identities unless an owner-approved migration includes a verified protection transition. Reconcile against the latest `main`; do not restore obsolete workflows.
- Trace evidence end to end: producer, artifact name, run ID, branch/ref, event, and historical readers. Keep scheduled-`main` evidence distinct from PR and manual runs; retain compatible history across migrations.

## Make failures and tests meaningful

- Use least privilege. Never expose write-capable credentials to untrusted PR code or suppress a legitimate security finding to achieve green CI. Escalate missing permissions or repository-setting prerequisites early.
- Report passed, failed, skipped, missing, and unknown states honestly. A successful summarizer must not hide a failed check; missing API or artifact evidence is not a pass.
- Add focused success and failure/edge-case tests, including missing or malformed evidence where relevant. Ensure new tests execute in an actual CI path, and avoid misleading error annotations from intentionally failing fixtures.
- When modifying orchestration, API integration, permissions, artifacts, or history, plan a hosted end-to-end check early. Verify the combined workflow, not just its component PR checks; if access prevents dispatch, provide the exact owner action and mark integration unverified.
- Check cancellation and rerun behavior when touching concurrency. Preserve valid historic comparisons and explain any compatibility assumptions.

## Protect publication

- Use read-only checks and preview capabilities for verification. Publication changes release or deployment state; respect the documented triggers, approved intent, and human authorization. Never bypass the Quality Gate or overwrite published release assets to repair a failure.

## Keep reporting proportional

- Keep `QUALITY.md` a concise stance and operating map, not a transcript of implementation details. Put technical specifics in workflows, scripts, tests, and linked run artifacts.
- In the draft PR, report the final commit, executed checks and links, observed behavior, and unresolved owner-only actions. Distinguish measured facts from conclusions.
