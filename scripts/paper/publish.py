"""Prepare and safely resume the human-reviewed Circle3 paper publication."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts.paper_release import (
    ReleaseError,
    changelog_versions,
    git_tags,
    latest_stable_tag,
    parse_version,
    promote_changelog,
    release_section,
    stable_tag_versions,
    validate_latest_release,
    validate_resume,
)

REPOSITORY = "basil-one/circle"
RELEASE_BRANCH = "paper-release/v{version}"
RELEASE_TITLE = "Prepare Circle3 paper v{version}"
CHECKS = (
    "Common: Code Quality",
    "Common: Coherence Integrity",
    "Common: Dependency Security",
    "Site: Build Integrity",
    "Site: Link Integrity",
    "Site: SEO Integrity",
    "Site: Rendering Integrity",
    "Site: Performance Integrity",
    "Paper: Build Integrity",
    "Paper: Content Integrity",
    "Paper: Link Integrity",
    "Paper: Rendering Integrity",
)
EXPECTED_CHECK_APP_ID = 15368


class PublishError(RuntimeError):
    """Raised when publication evidence is incomplete or conflicting."""


def git_result(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], check=False, capture_output=True, text=True, timeout=60)
    return result


def git(args: list[str], check: bool = True) -> str:
    result = git_result(args)
    if check and result.returncode:
        raise PublishError(result.stderr.strip() or f"git {' '.join(args)} failed.")
    return result.stdout.strip()


def is_ancestor(ancestor: str, descendant: str) -> bool:
    result = git_result(["merge-base", "--is-ancestor", ancestor, descendant])
    if result.returncode not in (0, 1):
        raise PublishError(result.stderr.strip() or "Unable to verify commit ancestry.")
    return result.returncode == 0


def gh_api(path: str, method: str = "GET", data: dict | None = None):
    command = ["gh", "api", "--method", method, path]
    input_data = None
    if data is not None:
        command.extend(["--input", "-"])
        input_data = json.dumps(data)
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        input=input_data,
        timeout=60,
    )
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "GitHub API request failed."
        raise PublishError(f"GitHub API {method} {path}: {message}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PublishError(f"GitHub API returned invalid JSON for {path}.") from error


def branch_name(version: str) -> str:
    parse_version(version)
    return RELEASE_BRANCH.format(version=version)


def changelog_at(revision: str) -> str:
    result = git_result(["show", f"{revision}:CHANGELOG.md"])
    if result.returncode:
        raise PublishError(f"Unable to read CHANGELOG.md at {revision}.")
    return result.stdout


def unreleased_entries(changelog: str) -> str:
    lines = changelog.splitlines()
    headings = [index for index, line in enumerate(lines) if line == "## [Unreleased]"]
    if len(headings) != 1:
        raise PublishError("CHANGELOG.md must contain exactly one Unreleased section.")
    end = next(
        (index for index in range(headings[0] + 1, len(lines)) if lines[index].startswith("## [")),
        len(lines),
    )
    return "\n".join(lines[headings[0] + 1 : end]).strip()


def release_date(changelog: str, version: str) -> str:
    match = re.search(
        rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$",
        changelog,
        re.MULTILINE,
    )
    if not match:
        raise PublishError(f"Release changelog heading for v{version} is missing or malformed.")
    return match.group(1)


def validate_pull_request(pr: dict, number: int, version: str, branch: str) -> None:
    if not isinstance(pr, dict):
        raise PublishError("GitHub returned malformed pull request evidence.")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    head_repo = head.get("repo") if isinstance(head, dict) else None
    base_repo = base.get("repo") if isinstance(base, dict) else None
    if (
        not isinstance(head, dict)
        or not isinstance(base, dict)
        or not isinstance(head_repo, dict)
        or not isinstance(base_repo, dict)
        or not isinstance(number, int)
        or isinstance(number, bool)
        or number < 1
        or pr.get("number") != number
        or pr.get("state") not in {"open", "closed"}
        or head.get("ref") != branch
        or head_repo.get("full_name") != REPOSITORY
        or base.get("ref") != "main"
        or base_repo.get("full_name") != REPOSITORY
        or pr.get("title") != RELEASE_TITLE.format(version=version)
    ):
        raise PublishError(
            f"PR #{number} does not match the expected repository, base, head, and version."
        )


def pull_request_merged(pr: dict) -> bool:
    merged_at = pr.get("merged_at")
    merged = pr.get("merged")
    if pr.get("state") == "open" and merged_at is None and merged in (False, None):
        return False
    if merged is True and isinstance(merged_at, str) and merged_at:
        return True
    if merged is False and merged_at is None:
        return False
    if merged is None and pr.get("state") == "closed" and isinstance(merged_at, str) and merged_at:
        return True
    raise PublishError("GitHub returned contradictory or incomplete merged pull request evidence.")


def validate_changed_files(files: list[dict]) -> None:
    if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
        raise PublishError("GitHub returned malformed pull request file evidence.")
    names = [item.get("filename") for item in files]
    if names != ["CHANGELOG.md"]:
        raise PublishError(
            "The release PR must change only CHANGELOG.md; "
            f"observed files: {', '.join(str(name) for name in names) or 'none'}."
        )


def validate_check_run(run: dict, head_sha: str) -> str | None:
    if not isinstance(run, dict):
        raise PublishError(f"Required check evidence for {head_sha} is malformed.")
    name = run.get("name")
    if name not in CHECKS:
        return None
    if run.get("head_sha") != head_sha:
        raise PublishError(f"Required check {name} has evidence for a different revision.")
    app = run.get("app")
    if not isinstance(app, dict) or app.get("id") != EXPECTED_CHECK_APP_ID:
        raise PublishError(f"Required check {name} is not from the expected GitHub Actions app.")
    status = run.get("status")
    conclusion = run.get("conclusion")
    if status not in {"queued", "in_progress", "completed"}:
        raise PublishError(f"Required check {name} has malformed status evidence.")
    if (status == "completed") != isinstance(conclusion, str):
        raise PublishError(f"Required check {name} has contradictory status evidence.")
    return name


def select_latest_check(latest: dict, name: str, run: dict) -> None:
    previous = latest.get(name)
    timestamp = run.get("started_at") or run.get("created_at") or ""
    previous_timestamp = (
        (previous.get("started_at") or previous.get("created_at") or "")
        if previous is not None
        else None
    )
    if not isinstance(timestamp, str) or (
        previous is not None and not isinstance(previous_timestamp, str)
    ):
        raise PublishError(f"Required check {name} has malformed timestamp evidence.")
    if (
        previous is not None
        and timestamp == previous_timestamp
        and (
            run.get("status"),
            run.get("conclusion"),
        )
        != (previous.get("status"), previous.get("conclusion"))
    ):
        raise PublishError(f"Required check {name} has contradictory duplicate evidence.")
    if previous is None or timestamp >= previous_timestamp:
        latest[name] = run


def latest_check_runs(check_runs: list[dict], head_sha: str) -> dict:
    if not isinstance(check_runs, list):
        raise PublishError(f"Required check evidence for {head_sha} is malformed.")
    latest = {}
    for run in check_runs:
        name = validate_check_run(run, head_sha)
        if name:
            select_latest_check(latest, name, run)
    return latest


def check_run_outcome(name: str, run: dict | None, head_sha: str) -> tuple[bool, str]:
    if run is None:
        return False, f"{name} has not reported for {head_sha}."
    if run.get("status") == "completed" and run.get("conclusion") != "success":
        raise PublishError(
            f"Required check {name} concluded {run.get('conclusion') or 'without a result'} "
            f"for {head_sha}; refusing publication."
        )
    if run.get("status") != "completed":
        return False, f"{name} is still running for {head_sha}."
    if run.get("conclusion") != "success":
        raise PublishError(f"Required check {name} has no successful conclusion for {head_sha}.")
    return True, ""


def validate_check_runs(check_runs: list[dict], head_sha: str) -> tuple[bool, str]:
    latest = latest_check_runs(check_runs, head_sha)
    incomplete = []
    for name in CHECKS:
        passed, detail = check_run_outcome(name, latest.get(name), head_sha)
        if not passed:
            incomplete.append(detail)
    if incomplete:
        return False, incomplete[0]
    return True, f"All {len(CHECKS)} required checks passed for {head_sha}."


class Publisher:
    def __init__(self, wait_seconds: int | None = None, interval: int | None = None):
        self.repo = os.environ.get("GITHUB_REPOSITORY", REPOSITORY)
        if self.repo != REPOSITORY:
            raise PublishError(f"Refusing publication from unexpected repository {self.repo}.")
        self.wait_seconds = (
            int(os.environ.get("PUBLISH_WAIT_SECONDS", "1800"))
            if wait_seconds is None
            else wait_seconds
        )
        self.interval = (
            int(os.environ.get("PUBLISH_POLL_SECONDS", "60")) if interval is None else interval
        )

    def fetch_main(self) -> str:
        git(["fetch", "origin", "main", "--tags"])
        return git(["rev-parse", "refs/remotes/origin/main"])

    def tag_source(self, version: str, main_sha: str) -> tuple[str, str] | None:
        tag = f"v{version}"
        git(["fetch", "origin", f"refs/tags/{tag}:refs/tags/{tag}"], check=False)
        source = git(["rev-parse", "--verify", f"{tag}^{{commit}}"], check=False)
        if not source:
            return None
        if not is_ancestor(source, main_sha):
            raise PublishError(f"Existing tag {tag} is not an ancestor of current main.")
        validate_resume(version, changelog_at(source), git_tags())
        pulls = self.pull_requests(branch_name(version))
        pr_number = ""
        if len(pulls) > 1:
            raise PublishError(
                f"Multiple release PRs exist for {tag}; reconcile them before resuming."
            )
        if pulls:
            pr = pulls[0]
            validate_pull_request(pr, pr.get("number"), version, branch_name(version))
            if not pull_request_merged(pr) or pr.get("merge_commit_sha") != source:
                raise PublishError(
                    f"Existing tag {tag} does not match its release PR merge revision."
                )
            self.files_for_pr(pr["number"])
            merged_sha, _ = self.verify_merged_pr(version, pr["number"], wait=False)
            if merged_sha != source:
                raise PublishError(f"Existing tag {tag} does not match its verified PR revision.")
            pr_number = str(pr["number"])
        elif version != "0.2.2" or git(["show", "-s", "--format=%s", source]) != "Release v0.2.2":
            raise PublishError(
                f"Existing tag {tag} has no matching verified release PR and is not a recognized "
                "historical release commit."
            )
        return source, pr_number

    def pull_requests(self, branch: str) -> list[dict]:
        pulls = gh_api(
            f"repos/{self.repo}/pulls?state=all&per_page=100&head="
            f"{self.repo.split('/')[0]}:{branch}&base=main"
        )
        if (
            not isinstance(pulls, list)
            or len(pulls) >= 100
            or not all(isinstance(pr, dict) for pr in pulls)
        ):
            raise PublishError(f"Pull request evidence for {branch} is incomplete or malformed.")
        details = []
        for item in pulls:
            number = item.get("number")
            if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                raise PublishError(f"Pull request evidence for {branch} has no valid number.")
            details.append(gh_api(f"repos/{self.repo}/pulls/{number}"))
        return details

    def check_runs(self, head_sha: str) -> list[dict]:
        path = f"repos/{self.repo}/commits/{head_sha}/check-runs?per_page=100&page=1"
        first = gh_api(path)
        if not isinstance(first, dict):
            raise PublishError(f"Required check evidence for {head_sha} is malformed.")
        total = first.get("total_count")
        runs = first.get("check_runs")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0 or total > 10_000:
            raise PublishError(f"Required check count for {head_sha} is unavailable.")
        if not isinstance(runs, list) or len(runs) > 100 or len(runs) > total:
            raise PublishError(f"Required check evidence for {head_sha} is malformed.")
        collected = list(runs)
        page = 2
        while len(collected) < total:
            data = gh_api(
                f"repos/{self.repo}/commits/{head_sha}/check-runs?per_page=100&page={page}"
            )
            if (
                not isinstance(data, dict)
                or data.get("total_count") != total
                or not isinstance(data.get("check_runs"), list)
                or len(data["check_runs"]) > 100
                or not data["check_runs"]
            ):
                raise PublishError(f"Required check evidence for {head_sha} is incomplete.")
            collected.extend(data["check_runs"])
            if len(collected) > total:
                raise PublishError(f"Required check evidence for {head_sha} is contradictory.")
            page += 1
        return collected

    def files_for_pr(self, number: int) -> list[dict]:
        files = gh_api(f"repos/{self.repo}/pulls/{number}/files?per_page=100")
        if not isinstance(files, list) or len(files) >= 100:
            raise PublishError(f"PR #{number} has too many files to verify safely.")
        validate_changed_files(files)
        return files

    def verify_branch(self, version: str, branch: str, main_sha: str) -> str:
        if not is_ancestor(main_sha, branch):
            raise PublishError(
                f"Release branch {branch} is behind main. Update it through GitHub, then rerun "
                "the same version; the workflow will not rewrite or force-push it."
            )
        base = git(["merge-base", main_sha, branch])
        base_changelog = changelog_at(base)
        main_changelog = changelog_at(main_sha)
        if unreleased_entries(base_changelog) != unreleased_entries(main_changelog):
            raise PublishError(
                "Unreleased changelog intent changed after this release branch was prepared; "
                "reconcile the branch and rerun."
            )
        actual = changelog_at(branch)
        release_day = release_date(actual, version)
        try:
            expected = promote_changelog(version, release_day, base_changelog, git_tags())
        except ReleaseError as error:
            raise PublishError(str(error)) from error
        if expected != actual:
            raise PublishError(
                "Release branch changelog does not exactly match the expected promotion."
            )
        if git(["diff", "--name-only", f"{main_sha}...{branch}"]).splitlines() != ["CHANGELOG.md"]:
            raise PublishError(
                "Release branch contains changes beyond the expected CHANGELOG.md promotion."
            )
        return actual

    def create_release_branch(self, version: str, branch: str, main_sha: str) -> None:
        remote = git_result(["ls-remote", "--exit-code", "--heads", "origin", branch])
        if remote.returncode == 0:
            git(["fetch", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}"])
            git(["branch", branch, f"refs/remotes/origin/{branch}"])
            self.verify_branch(version, branch, main_sha)
            return
        if remote.returncode != 2:
            raise PublishError(
                remote.stderr.strip() or f"Unable to inspect remote branch {branch}."
            )
        validate_latest_release(git_tags())
        changelog = changelog_at(main_sha)
        promoted = promote_changelog(
            version,
            datetime.now(UTC).date().isoformat(),
            changelog,
            git_tags(),
        )
        git(["switch", "--create", branch, main_sha])
        Path("CHANGELOG.md").write_text(promoted, encoding="utf-8")
        git(["add", "--", "CHANGELOG.md"])
        git(
            [
                "-c",
                "user.name=github-actions[bot]",
                "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "commit",
                "-m",
                f"Prepare paper release v{version}",
            ]
        )
        git(["push", "origin", f"HEAD:refs/heads/{branch}"])
        self.verify_branch(version, branch, main_sha)

    def create_or_find_pr(self, version: str, branch: str) -> dict:
        pull_requests = self.pull_requests(branch)
        if len(pull_requests) > 1:
            raise PublishError(
                f"Multiple PRs exist for {branch}; human reconciliation is required."
            )
        if pull_requests:
            pr = pull_requests[0]
            validate_pull_request(pr, pr.get("number"), version, branch)
            merged = pull_request_merged(pr) if pr.get("state") == "closed" else False
            if pr.get("state") == "closed" and not merged:
                raise PublishError(
                    f"PR #{pr['number']} was closed without merging. Reopen or reconcile it, "
                    f"then rerun v{version}."
                )
            return pr
        description = (
            f"Automated promotion of the existing Unreleased entries for v{version}.\n\n"
            "Human actions required: review this changelog-only PR, approve the requested "
            "GitHub Actions workflow runs if GitHub requires approval, and merge it through "
            "the repository Quality Gate. Paper Publish will wait up to 30 minutes for the "
            "exact PR and its twelve required checks. It will not approve, merge, or bypass."
        )
        try:
            pr = gh_api(
                f"repos/{self.repo}/pulls",
                method="POST",
                data={
                    "title": RELEASE_TITLE.format(version=version),
                    "head": branch,
                    "base": "main",
                    "body": description,
                },
            )
        except PublishError as error:
            raise PublishError(
                f"Could not create the release PR. Confirm repository Actions settings allow "
                f"GITHUB_TOKEN to create pull requests and that pull-requests:write is granted. "
                f"GitHub reported: {error}"
            ) from error
        validate_pull_request(pr, pr.get("number"), version, branch)
        return pr

    def reject_competing_releases(self, branch: str) -> None:
        pending = gh_api(f"repos/{self.repo}/pulls?state=open&base=main&per_page=100")
        if (
            not isinstance(pending, list)
            or len(pending) >= 100
            or not all(
                isinstance(pr, dict)
                and isinstance(pr.get("head"), dict)
                and isinstance(pr.get("base"), dict)
                for pr in pending
            )
        ):
            raise PublishError("Open pull request evidence is incomplete or malformed.")
        competing = [
            pr
            for pr in pending
            if (pr.get("head") or {}).get("ref", "").startswith("paper-release/")
            and (pr.get("head") or {}).get("ref") != branch
        ]
        if competing:
            names = ", ".join(f"#{pr['number']} ({pr['head']['ref']})" for pr in competing)
            raise PublishError(f"Another paper release PR is already pending: {names}.")
        orphaned = git_result(["ls-remote", "--heads", "origin", "refs/heads/paper-release/*"])
        if orphaned.returncode:
            raise PublishError(orphaned.stderr.strip() or "Unable to inspect release branches.")
        other_branches = [
            line.split("\t", 1)[1].removeprefix("refs/heads/")
            for line in orphaned.stdout.splitlines()
            if "\t" in line
            and line.split("\t", 1)[1].startswith("refs/heads/paper-release/")
            and line.split("\t", 1)[1] != f"refs/heads/{branch}"
        ]
        if other_branches:
            raise PublishError(
                "Another paper release branch exists without a matching pending PR: "
                f"{', '.join(other_branches)}. Reconcile it before publishing."
            )

    def prepare(self, version: str) -> tuple[str, str, str]:
        parse_version(version)
        main_sha = self.fetch_main()
        if git(["rev-parse", "HEAD"]) != main_sha:
            raise PublishError("main changed before publication began; rerun from current main.")
        existing_tag = self.tag_source(version, main_sha)
        if existing_tag:
            source, pr_number = existing_tag
            return "tag", pr_number, source
        branch = branch_name(version)
        self.reject_competing_releases(branch)
        existing_prs = self.pull_requests(branch)
        if len(existing_prs) > 1:
            raise PublishError(
                f"Multiple PRs exist for {branch}; human reconciliation is required."
            )
        if existing_prs:
            pr = existing_prs[0]
            validate_pull_request(pr, pr.get("number"), version, branch)
            self.files_for_pr(pr["number"])
            merged = pull_request_merged(pr) if pr.get("state") == "closed" else False
            if pr.get("state") == "closed" and not merged:
                raise PublishError(
                    f"PR #{pr['number']} was closed without merging. Reopen or reconcile it, "
                    f"then rerun v{version}."
                )
            if merged:
                return "pr", str(pr["number"]), pr["html_url"]
            git(["fetch", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}"])
            git(["branch", branch, f"refs/remotes/origin/{branch}"])
            self.verify_branch(version, branch, main_sha)
        else:
            self.create_release_branch(version, branch, main_sha)
        pr = self.create_or_find_pr(version, branch)
        self.files_for_pr(pr["number"])
        url = pr.get("html_url")
        if not isinstance(url, str) or not re.fullmatch(
            r"https://github\.com/basil-one/circle/pull/\d+", url
        ):
            raise PublishError("GitHub returned an unexpected release PR URL.")
        return "pr", str(pr["number"]), url

    def checked_merged_pr(self, version: str, number: int):
        branch = branch_name(version)
        pr = gh_api(f"repos/{self.repo}/pulls/{number}")
        validate_pull_request(pr, number, version, branch)
        self.files_for_pr(number)
        merged = pull_request_merged(pr) if pr.get("state") == "closed" else False
        if pr.get("state") == "closed" and not merged:
            raise PublishError(f"PR #{number} closed without merging; no release was published.")
        if not merged:
            return None, "awaiting human merge", pr
        head_sha = (pr.get("head") or {}).get("sha")
        merge_sha = pr.get("merge_commit_sha")
        if not re.fullmatch(r"[0-9a-f]{40,64}", str(head_sha)) or not re.fullmatch(
            r"[0-9a-f]{40,64}", str(merge_sha)
        ):
            raise PublishError(f"PR #{number} is merged but its source/merge SHA is invalid.")
        checks = self.check_runs(head_sha)
        all_passed, detail = validate_check_runs(checks, head_sha)
        if not all_passed:
            return None, detail, pr
        self.verify_merged_source(version, number, head_sha, merge_sha)
        return (merge_sha, pr["html_url"]), detail, pr

    def verify_merged_source(
        self, version: str, number: int, head_sha: str, merge_sha: str
    ) -> None:
        git(
            [
                "fetch",
                "origin",
                f"refs/pull/{number}/head:refs/remotes/paper-pr/{number}",
            ]
        )
        pr_head = f"refs/remotes/paper-pr/{number}"
        if not git(["rev-parse", "--verify", pr_head], check=False):
            raise PublishError(f"PR #{number} source revision is unavailable.")
        fetched_head = git(["rev-parse", pr_head])
        if fetched_head != head_sha:
            raise PublishError(
                f"PR #{number} fetched source {fetched_head} differs from checked revision "
                f"{head_sha}."
            )
        self.verify_promoted_revision(pr_head, version)
        main_sha = self.fetch_main()
        if not is_ancestor(merge_sha, main_sha):
            raise PublishError(f"PR #{number} merge SHA {merge_sha} is not on current main.")
        candidate_tree = git(["rev-parse", f"{pr_head}^{{tree}}"])
        merged_tree = git(["rev-parse", f"{merge_sha}^{{tree}}"])
        if merged_tree != candidate_tree:
            raise PublishError(
                f"PR #{number} merged tree differs from its exact checked candidate tree."
            )
        merged_changelog = changelog_at(merge_sha)
        if changelog_versions(merged_changelog)[:1] != [version]:
            raise PublishError(
                f"PR #{number} merge revision does not promote v{version} as latest."
            )
        if unreleased_entries(merged_changelog):
            raise PublishError(
                f"PR #{number} merge revision changed Unreleased beyond the promotion."
            )

    def verify_merged_pr(self, version: str, number: int, wait: bool) -> tuple[str, str]:
        deadline = time.monotonic() + self.wait_seconds
        while True:
            result, detail, pr = self.checked_merged_pr(version, number)
            if result:
                merge_sha, url = result
                print(f"RESUMED: PR #{number} merged; {detail} Source: {merge_sha}.")
                return merge_sha, url
            if not wait:
                raise PublishError(
                    f"PR #{number} has not been merged with verified check evidence."
                )
            if time.monotonic() >= deadline:
                raise PublishError(
                    f"Timed out waiting for PR #{number}. It remains available for review; "
                    "rerun Paper: Publish with the same version to resume."
                )
            print(
                f"WAITING: PR #{number} {pr.get('html_url')} — {detail}; review, approve "
                "required workflow runs if prompted, and merge through the Quality Gate.",
                flush=True,
            )
            time.sleep(self.interval)

    def verify_promoted_revision(self, revision: str, version: str) -> None:
        commits = git(["log", "--reverse", "--format=%H", revision, "--", "CHANGELOG.md"])
        for commit in commits.splitlines():
            current = changelog_at(commit)
            if version not in changelog_versions(current):
                continue
            parents = git(["rev-list", "--parents", "-n", "1", commit]).split()[1:]
            if len(parents) != 1 or version in changelog_versions(changelog_at(parents[0])):
                continue
            baseline = changelog_at(parents[0])
            tags = git(["tag", "--list", "v*", "--merged", parents[0]]).splitlines()
            try:
                expected = promote_changelog(
                    version, release_date(current, version), baseline, tags
                )
            except ReleaseError as error:
                raise PublishError(f"Release PR promotion is no longer valid: {error}") from error
            if expected != current:
                raise PublishError("Release PR changelog is not the exact expected promotion.")
            if changelog_at(revision) != current:
                raise PublishError("Release PR source changed CHANGELOG.md after its promotion.")
            return
        raise PublishError(f"Could not establish the original changelog promotion for v{version}.")

    def verify_finalize_source(
        self, version: str, source_sha: str, pr_number: int | None
    ) -> tuple[str, str]:
        if git(["rev-parse", "HEAD"]) != source_sha:
            raise PublishError("Checked-out revision does not equal the verified release source.")
        main_sha = self.fetch_main()
        if not is_ancestor(source_sha, main_sha):
            raise PublishError(f"Approved source {source_sha} is not an ancestor of main.")
        changelog = changelog_at(source_sha)
        if changelog_versions(changelog)[:1] != [version] or unreleased_entries(changelog):
            raise PublishError(f"Approved source does not contain the exact v{version} promotion.")
        if pr_number is not None:
            verified_sha, _ = self.verify_merged_pr(version, pr_number, wait=False)
            if verified_sha != source_sha:
                raise PublishError("The PR merge revision changed before release finalization.")
        else:
            existing_tag_source = git(
                ["rev-parse", "--verify", f"refs/tags/v{version}^{{commit}}"], check=False
            )
            if existing_tag_source != source_sha:
                raise PublishError(
                    "A new release tag requires an exact verified release PR; historical "
                    "recovery is allowed only from its existing tag."
                )
            if (
                version != "0.2.2"
                or git(["show", "-s", "--format=%s", source_sha]) != "Release v0.2.2"
            ):
                raise PublishError("Existing tag is not a recognized historical release.")
        return main_sha, changelog

    def verify_current_release_state(
        self, version: str, main_sha: str, changelog: str, tags: list[str]
    ) -> None:
        prior_tags = [
            f"v{tag_version}"
            for tag_version in stable_tag_versions(tags)
            if parse_version(tag_version) < parse_version(version)
        ]
        try:
            validate_latest_release(prior_tags)
        except ReleaseError as error:
            raise PublishError(str(error)) from error
        current_main_changelog = changelog_at(main_sha)
        if changelog_versions(current_main_changelog)[:1] != [version]:
            raise PublishError("Current main has a conflicting promoted release version.")
        try:
            if release_section(version, current_main_changelog) != release_section(
                version, changelog
            ):
                raise PublishError("Current main changed the approved release changelog entry.")
        except ReleaseError as error:
            raise PublishError(f"Current main release entry is invalid: {error}") from error
        prior = latest_stable_tag(tags)
        if (
            prior is not None
            and prior != version
            and parse_version(version) <= parse_version(prior)
        ):
            raise PublishError(
                f"v{version} is no longer newer than the latest stable tag v{prior}."
            )

    def finalize(self, version: str, source_sha: str, pr_number: int | None) -> None:
        main_sha, changelog = self.verify_finalize_source(version, source_sha, pr_number)
        tags = git_tags()
        self.verify_current_release_state(version, main_sha, changelog, tags)
        tag = f"v{version}"
        existing = git(["rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"], check=False)
        if existing and existing != source_sha:
            raise PublishError(
                f"Existing tag {tag} points to {existing}, not approved source {source_sha}."
            )
        if not existing:
            if pr_number is None:
                raise PublishError("Refusing to create a tag without a verified release PR.")
            git(
                [
                    "-c",
                    "user.name=github-actions[bot]",
                    "-c",
                    "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "tag",
                    "-a",
                    tag,
                    source_sha,
                    "-m",
                    f"Release {tag}",
                ]
            )
            git(["push", "origin", f"refs/tags/{tag}"])
        validate_resume(version, changelog, git_tags())
        helper = os.environ.get("PAPER_RELEASE_HELPER", "scripts/paper_release.py")
        subprocess.run(
            [
                "python3",
                helper,
                "release",
                "--version",
                version,
                "--pdf",
                "assets/pdfs/circle3-paper.pdf",
            ],
            check=True,
            timeout=1800,
        )
        print(
            f"PUBLISHED: v{version}; PR: {f'https://github.com/{self.repo}/pull/{pr_number}' if pr_number else 'historical recovery'}; "
            f"source: {source_sha}; tag: https://github.com/{self.repo}/tree/{tag}; "
            f"release: https://github.com/{self.repo}/releases/tag/{tag}"
        )


def write_output(**values: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            for name, value in values.items():
                print(f"{name}={value}", file=stream)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--version", required=True)
    wait = commands.add_parser("wait")
    wait.add_argument("--version", required=True)
    wait.add_argument("--pr", required=True, type=int)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--version", required=True)
    finalize.add_argument("--source", required=True)
    finalize.add_argument("--pr", type=int)
    args = parser.parse_args()
    try:
        publisher = Publisher()
        if args.command == "prepare":
            mode, pr_number, url_or_sha = publisher.prepare(args.version)
            write_output(
                mode=mode,
                pr_number=pr_number,
                url=url_or_sha if mode == "pr" else "",
                source_sha=url_or_sha if mode == "tag" else "",
            )
            if mode == "pr":
                print(
                    f"PR CREATED/REUSED: {url_or_sha}\n"
                    "Human action required: review, approve the requested checks if GitHub "
                    "requires it, and merge through the Quality Gate."
                )
            else:
                print(f"RESUMED: existing verified tag source {url_or_sha}.")
        elif args.command == "wait":
            source, _ = publisher.verify_merged_pr(args.version, args.pr, wait=True)
            write_output(source_sha=source)
        else:
            publisher.finalize(args.version, args.source, args.pr)
    except (
        OSError,
        PublishError,
        ReleaseError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as error:
        print(f"Paper publication stopped safely: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
