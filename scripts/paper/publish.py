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


class PublishError(RuntimeError):
    """Raised when publication evidence is incomplete or conflicting."""


def git_result(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], check=False, capture_output=True, text=True, timeout=60
    )
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
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "GitHub API request failed."
        )
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
        (
            index
            for index in range(headings[0] + 1, len(lines))
            if lines[index].startswith("## [")
        ),
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
        raise PublishError(
            f"Release changelog heading for v{version} is missing or malformed."
        )
    return match.group(1)


def validate_pull_request(pr: dict, number: int, version: str, branch: str) -> None:
    if not isinstance(pr, dict):
        raise PublishError("GitHub returned malformed pull request evidence.")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    if (
        not isinstance(head, dict)
        or not isinstance(base, dict)
        or not isinstance(number, int)
        or number < 1
        or pr.get("number") != number
        or pr.get("state") not in {"open", "closed"}
        or head.get("ref") != branch
        or (head.get("repo") or {}).get("full_name") != REPOSITORY
        or base.get("ref") != "main"
        or (base.get("repo") or {}).get("full_name") != REPOSITORY
        or pr.get("title") != RELEASE_TITLE.format(version=version)
    ):
        raise PublishError(
            f"PR #{number} does not match the expected repository, base, head, and version."
        )


def validate_changed_files(files: list[dict]) -> None:
    if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
        raise PublishError("GitHub returned malformed pull request file evidence.")
    names = [item.get("filename") for item in files]
    if names != ["CHANGELOG.md"]:
        raise PublishError(
            "The release PR must change only CHANGELOG.md; "
            f"observed files: {', '.join(str(name) for name in names) or 'none'}."
        )


def validate_check_runs(check_runs: list[dict], head_sha: str) -> tuple[bool, str]:
    latest = {}
    for run in check_runs:
        if not isinstance(run, dict):
            raise PublishError(f"Required check evidence for {head_sha} is malformed.")
        name = run.get("name")
        if name in CHECKS:
            if run.get("head_sha") != head_sha:
                raise PublishError(
                    f"Required check {name} has evidence for a different revision."
                )
            previous = latest.get(name)
            if previous is None or (run.get("started_at") or "") >= (
                previous.get("started_at") or ""
            ):
                latest[name] = run
    for name in CHECKS:
        run = latest.get(name)
        if run is None:
            return False, f"{name} has not reported for {head_sha}."
        if run.get("status") == "completed" and run.get("conclusion") != "success":
            raise PublishError(
                f"Required check {name} concluded {run.get('conclusion') or 'without a result'} "
                f"for {head_sha}; refusing publication."
            )
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            return False, f"{name} is still running for {head_sha}."
    return True, f"All {len(CHECKS)} required checks passed for {head_sha}."


class Publisher:
    def __init__(self, wait_seconds: int | None = None, interval: int | None = None):
        self.repo = os.environ.get("GITHUB_REPOSITORY", REPOSITORY)
        if self.repo != REPOSITORY:
            raise PublishError(
                f"Refusing publication from unexpected repository {self.repo}."
            )
        self.wait_seconds = (
            int(os.environ.get("PUBLISH_WAIT_SECONDS", "1800"))
            if wait_seconds is None
            else wait_seconds
        )
        self.interval = (
            int(os.environ.get("PUBLISH_POLL_SECONDS", "60"))
            if interval is None
            else interval
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
            raise PublishError(
                f"Existing tag {tag} is not an ancestor of current main."
            )
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
            if not pr.get("merged") or pr.get("merge_commit_sha") != source:
                raise PublishError(
                    f"Existing tag {tag} does not match its release PR merge revision."
                )
            self.files_for_pr(pr["number"])
            merged_sha, _ = self.verify_merged_pr(version, pr["number"], wait=False)
            if merged_sha != source:
                raise PublishError(
                    f"Existing tag {tag} does not match its verified PR revision."
                )
            pr_number = str(pr["number"])
        elif git(["show", "-s", "--format=%s", source]) != f"Release {tag}":
            raise PublishError(
                f"Existing tag {tag} has no matching verified release PR and is not a recognized "
                "historical release commit."
            )
        return source, pr_number

    def pull_requests(self, branch: str) -> list[dict]:
        pulls = gh_api(
            f"repos/{self.repo}/pulls?state=all&per_page=100&head={self.repo.split('/')[0]}:{branch}"
        )
        if (
            not isinstance(pulls, list)
            or len(pulls) >= 100
            or not all(isinstance(pr, dict) for pr in pulls)
        ):
            raise PublishError(
                f"Pull request evidence for {branch} is incomplete or malformed."
            )
        return pulls

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
            expected = promote_changelog(
                version, release_day, base_changelog, git_tags()
            )
        except ReleaseError as error:
            raise PublishError(str(error)) from error
        if expected != actual:
            raise PublishError(
                "Release branch changelog does not exactly match the expected promotion."
            )
        if git(["diff", "--name-only", f"{main_sha}...{branch}"]).splitlines() != [
            "CHANGELOG.md"
        ]:
            raise PublishError(
                "Release branch contains changes beyond the expected CHANGELOG.md promotion."
            )
        return actual

    def create_release_branch(self, version: str, branch: str, main_sha: str) -> None:
        remote = git_result(["ls-remote", "--exit-code", "--heads", "origin", branch])
        if remote.returncode == 0:
            git(
                ["fetch", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}"]
            )
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
            if pr.get("state") == "closed" and not pr.get("merged"):
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

    def prepare(self, version: str) -> tuple[str, str, str]:
        parse_version(version)
        main_sha = self.fetch_main()
        if git(["rev-parse", "HEAD"]) != main_sha:
            raise PublishError(
                "main changed before publication began; rerun from current main."
            )
        existing_tag = self.tag_source(version, main_sha)
        if existing_tag:
            source, pr_number = existing_tag
            return "tag", pr_number, source
        branch = branch_name(version)
        pending = gh_api(f"repos/{self.repo}/pulls?state=open&base=main&per_page=100")
        if (
            not isinstance(pending, list)
            or len(pending) >= 100
            or not all(isinstance(pr, dict) for pr in pending)
        ):
            raise PublishError("Open pull request evidence is incomplete or malformed.")
        competing = [
            pr
            for pr in pending
            if (pr.get("head") or {}).get("ref", "").startswith("paper-release/")
            and (pr.get("head") or {}).get("ref") != branch
        ]
        if competing:
            names = ", ".join(
                f"#{pr['number']} ({pr['head']['ref']})" for pr in competing
            )
            raise PublishError(f"Another paper release PR is already pending: {names}.")
        existing_prs = self.pull_requests(branch)
        if len(existing_prs) > 1:
            raise PublishError(
                f"Multiple PRs exist for {branch}; human reconciliation is required."
            )
        if existing_prs:
            pr = existing_prs[0]
            validate_pull_request(pr, pr.get("number"), version, branch)
            self.files_for_pr(pr["number"])
            if pr.get("state") == "closed" and not pr.get("merged"):
                raise PublishError(
                    f"PR #{pr['number']} was closed without merging. Reopen or reconcile it, "
                    f"then rerun v{version}."
                )
            if pr.get("merged"):
                return "pr", str(pr["number"]), pr["html_url"]
            git(
                ["fetch", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}"]
            )
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

    def verify_merged_pr(
        self, version: str, number: int, wait: bool
    ) -> tuple[str, str]:
        branch = branch_name(version)
        deadline = time.monotonic() + self.wait_seconds
        while True:
            pr = gh_api(f"repos/{self.repo}/pulls/{number}")
            validate_pull_request(pr, number, version, branch)
            self.files_for_pr(number)
            if pr.get("state") == "closed" and not pr.get("merged"):
                raise PublishError(
                    f"PR #{number} closed without merging; no release was published."
                )
            if pr.get("merged"):
                head_sha = (pr.get("head") or {}).get("sha")
                merge_sha = pr.get("merge_commit_sha")
                if not re.fullmatch(
                    r"[0-9a-f]{40,64}", str(head_sha)
                ) or not re.fullmatch(r"[0-9a-f]{40,64}", str(merge_sha)):
                    raise PublishError(
                        f"PR #{number} is merged but its source/merge SHA is invalid."
                    )
                checks = gh_api(
                    f"repos/{self.repo}/commits/{head_sha}/check-runs?per_page=100"
                ).get("check_runs")
                if not isinstance(checks, list):
                    raise PublishError(
                        f"Required check evidence for PR #{number} is unavailable."
                    )
                all_passed, detail = validate_check_runs(checks, head_sha)
                if all_passed:
                    git(
                        [
                            "fetch",
                            "origin",
                            f"refs/pull/{number}/head:refs/remotes/paper-pr/{number}",
                        ],
                        check=False,
                    )
                    pr_head = f"refs/remotes/paper-pr/{number}"
                    if not git(["rev-parse", "--verify", pr_head], check=False):
                        raise PublishError(
                            f"PR #{number} source revision is unavailable."
                        )
                    self.verify_promoted_revision(pr_head, version)
                    main_sha = self.fetch_main()
                    if not is_ancestor(merge_sha, main_sha):
                        raise PublishError(
                            f"PR #{number} merge SHA {merge_sha} is not on current main."
                        )
                    merged_changelog = changelog_at(merge_sha)
                    if merged_changelog != changelog_at(pr_head):
                        raise PublishError(
                            f"PR #{number} merged changelog differs from its reviewed head."
                        )
                    if changelog_versions(merged_changelog)[:1] != [version]:
                        raise PublishError(
                            f"PR #{number} merge revision does not promote v{version} as latest."
                        )
                    if unreleased_entries(merged_changelog):
                        raise PublishError(
                            f"PR #{number} merge revision changed Unreleased beyond the promotion."
                        )
                    print(
                        f"RESUMED: PR #{number} merged; {detail} Source: {merge_sha}."
                    )
                    return merge_sha, pr["html_url"]
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
                f"WAITING: PR #{number} {pr.get('html_url')} — review, approve required "
                "workflow runs if prompted, and merge through the Quality Gate."
            )
            time.sleep(self.interval)

    def verify_promoted_revision(self, revision: str, version: str) -> None:
        commits = git(
            ["log", "--reverse", "--format=%H", revision, "--", "CHANGELOG.md"]
        )
        for commit in commits.splitlines():
            current = changelog_at(commit)
            if version not in changelog_versions(current):
                continue
            parents = git(["rev-list", "--parents", "-n", "1", commit]).split()[1:]
            if len(parents) != 1 or version in changelog_versions(
                changelog_at(parents[0])
            ):
                continue
            baseline = changelog_at(parents[0])
            tags = git(["tag", "--list", "v*", "--merged", parents[0]]).splitlines()
            try:
                expected = promote_changelog(
                    version, release_date(current, version), baseline, tags
                )
            except ReleaseError as error:
                raise PublishError(
                    f"Release PR promotion is no longer valid: {error}"
                ) from error
            if expected != current:
                raise PublishError(
                    "Release PR changelog is not the exact expected promotion."
                )
            if changelog_at(revision) != current:
                raise PublishError(
                    "Release PR source changed CHANGELOG.md after its promotion."
                )
            return
        raise PublishError(
            f"Could not establish the original changelog promotion for v{version}."
        )

    def finalize(self, version: str, source_sha: str, pr_number: int | None) -> None:
        main_sha = self.fetch_main()
        if not is_ancestor(source_sha, main_sha):
            raise PublishError(
                f"Approved source {source_sha} is not an ancestor of main."
            )
        changelog = changelog_at(source_sha)
        if changelog_versions(changelog)[:1] != [version] or unreleased_entries(
            changelog
        ):
            raise PublishError(
                f"Approved source does not contain the exact v{version} promotion."
            )
        if pr_number is not None:
            verified_sha, _ = self.verify_merged_pr(version, pr_number, wait=False)
            if verified_sha != source_sha:
                raise PublishError(
                    "The PR merge revision changed before release finalization."
                )
        prior = latest_stable_tag(git_tags())
        if (
            prior is not None
            and prior != version
            and parse_version(version) <= parse_version(prior)
        ):
            raise PublishError(
                f"v{version} is no longer newer than the latest stable tag v{prior}."
            )
        tag = f"v{version}"
        existing = git(
            ["rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"], check=False
        )
        if existing and existing != source_sha:
            raise PublishError(
                f"Existing tag {tag} points to {existing}, not approved source {source_sha}."
            )
        if not existing:
            git(["tag", "-a", tag, source_sha, "-m", f"Release {tag}"])
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
