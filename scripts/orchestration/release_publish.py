"""Prepare and publish a human-reviewed, changelog-only Circle release."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts.paper_release import (
    LEGACY_SOURCE,
    ReleaseError,
    changelog_versions,
    get_release,
    git_tags,
    latest_stable_tag,
    parse_version,
    promote_changelog,
    publish_release,
    release_section,
    required_asset_names,
    stable_tag_versions,
    validate_latest_release,
    validate_resume,
    verify_asset_parity,
)

REPOSITORY = "basil-one/circle"
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
SHA = re.compile(r"[0-9a-f]{40}")
BUILDER = Path(__file__).resolve().parents[1] / "build-paper-pdf.sh"
PDF = Path("assets/pdfs/circle3-paper.pdf")
BOT = (
    "-c",
    "user.name=github-actions[bot]",
    "-c",
    "user.email=41898282+github-actions[bot]@users.noreply.github.com",
)


class PublishError(RuntimeError):
    """Publication evidence is incomplete or conflicting."""


class ChecksPending(PublishError):
    """Required checks have not finished reporting."""


def command(*args, payload=None, allowed=(0,)):
    result = subprocess.run(
        args, input=payload, check=False, capture_output=True, text=True, timeout=120
    )
    if result.returncode not in allowed:
        raise PublishError(f"{args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def git(*args):
    return command("git", *args).stdout.strip()


def is_ancestor(ancestor, descendant):
    return (
        command(
            "git", "merge-base", "--is-ancestor", ancestor, descendant, allowed=(0, 1)
        ).returncode
        == 0
    )


def gh_api(path, data=None):
    prefix = f"repos/{REPOSITORY}/{path}"
    args = ("--method", "POST", "--input", "-") if data is not None else ()
    result = command("gh", "api", prefix, *args, payload=json.dumps(data) if args else None)
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise PublishError(f"Malformed GitHub evidence for {path}.") from error


def pulls(query):
    items = gh_api(f"pulls?{query}&base=main&per_page=100")
    if not isinstance(items, list) or len(items) >= 100:
        raise PublishError("Release PR list is incomplete or malformed.")
    return items


def changelog_at(revision):
    return command("git", "show", f"{revision}:CHANGELOG.md").stdout


def safe_sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise PublishError("Invalid commit SHA in release evidence.")
    return value


def check_identity(run, head):
    if run.get("head_sha") != head or (run.get("app") or {}).get("id") != EXPECTED_CHECK_APP_ID:
        raise PublishError(f"Required check {run['name']} has the wrong revision or app.")
    stamp = run.get("started_at") or run.get("created_at")
    if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp):
        raise PublishError(f"Required check {run['name']} has no valid timestamp.")
    status = run.get("status")
    if status not in ("queued", "in_progress", "completed") or (
        (status == "completed") != isinstance(run.get("conclusion"), str)
    ):
        raise PublishError(f"Required check {run['name']} has contradictory status evidence.")
    return stamp


def verify_checks(runs, head):
    latest = {}
    for run in runs:
        name = run.get("name")
        if name not in CHECKS:
            continue
        stamp = check_identity(run, head)
        if name in latest and stamp == latest[name][0]:
            raise PublishError(f"Ambiguous check evidence for {name}.")
        if name not in latest or stamp > latest[name][0]:
            latest[name] = (stamp, run)
    pending = []
    for name in CHECKS:
        run = latest[name][1] if name in latest else None
        if run is None or run["status"] != "completed":
            pending.append(name)
        elif run["conclusion"] != "success":
            raise PublishError(f"Required check {name} concluded {run['conclusion']} for {head}.")
    if pending:
        raise ChecksPending(f"{pending[0]} has not passed yet ({len(pending)} outstanding).")


def check_pages(head):
    collected = []
    ids = set()
    total = None
    for page in range(1, 11):
        data = gh_api(f"commits/{head}/check-runs?filter=all&per_page=100&page={page}")
        count, runs = data["total_count"], data["check_runs"]
        if type(count) is not int or not 0 <= count <= 1000 or total not in (None, count):
            raise PublishError("Check count changed or is outside the GitHub API limit.")
        if not isinstance(runs, list) or len(runs) != min(100, count - len(collected)):
            raise PublishError("Required check pages are incomplete or contradictory.")
        for run in runs:
            run_id = run.get("id")
            if type(run_id) is not int or run_id < 1 or run_id in ids:
                raise PublishError("Check evidence contains a missing or duplicate ID.")
            ids.add(run_id)
        collected.extend(runs)
        total = count
        if len(collected) == total:
            return collected
    raise PublishError("Required check pages are incomplete.")


def verify_promotion(version, base, head):
    if git("diff", "--name-status", "--no-renames", base, head) != "M\tCHANGELOG.md":
        raise PublishError("Release PR must modify only CHANGELOG.md.")
    actual = changelog_at(head)
    heading = re.search(rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d\d-\d\d)$", actual, re.M)
    if not heading:
        raise PublishError(f"Changelog heading for v{version} is missing or malformed.")
    tags = git("tag", "--list", "v*", "--merged", base).splitlines()
    expected = promote_changelog(version, heading[1], changelog_at(base), tags)
    if actual != expected:
        raise PublishError("Release changelog does not match the exact promotion.")


def merged_base(head, source, version):
    parents = git("show", "-s", "--format=%P", source).split()
    if len(parents) == 2 and parents[1] == head and is_ancestor(parents[0], head):
        return parents[0]
    if len(parents) != 1:
        raise PublishError("Merge parents do not establish the checked PR relationship.")
    base = git("merge-base", head, source)
    if base == head:
        # Fast-forward history can share the promoted commit with the PR head.
        heading = r"^## \[" + re.escape(version) + r"\]"
        promotion = git("log", "-1", "--format=%H", "-G", heading, head, "--", "CHANGELOG.md")
        base = git("rev-parse", f"{safe_sha(promotion)}^")
    changed = git("log", "--format=", "--name-only", f"{base}..{source}").splitlines()
    if set(changed) != {"CHANGELOG.md"} or git("rev-list", "--merges", f"{base}..{source}"):
        raise PublishError("Squashed or rebased release history must only change CHANGELOG.md.")
    return base


class Publisher:
    def __init__(self, version):
        parse_version(version)
        self.version = version
        self.tag = f"v{version}"
        self.branch = f"paper-release/{self.tag}"
        self.title = f"Prepare Circle3 paper {self.tag}"

    @staticmethod
    def fetch_main():
        git("fetch", "origin", "+refs/heads/main:refs/remotes/origin/main", "--tags")
        return git("rev-parse", "refs/remotes/origin/main")

    def pr(self, number):
        if type(number) is not int or number < 1:
            raise PublishError("Invalid release PR number.")
        pr = gh_api(f"pulls/{number}")
        if (
            pr["number"] != number
            or pr["title"] != self.title
            or pr["head"]["ref"] != self.branch
            or pr["head"]["repo"]["full_name"] != REPOSITORY
            or pr["base"]["ref"] != "main"
            or pr["base"]["repo"]["full_name"] != REPOSITORY
        ):
            raise PublishError(f"PR #{number} does not match the release identity.")
        safe_sha(pr["head"]["sha"])
        if pr["state"] == "open" and pr.get("merged") is False and pr.get("merged_at") is None:
            return pr
        if pr["state"] != "closed" or pr.get("merged") is not True or not pr.get("merged_at"):
            raise PublishError(
                f"PR #{number} closed without a verified merge; reconcile and rerun."
            )
        if (pr.get("merged_by") or {}).get("type") != "User":
            raise PublishError(f"PR #{number} must be merged by a human GitHub user.")
        safe_sha(pr["merge_commit_sha"])
        return pr

    def find_pr(self):
        items = pulls(f"state=all&head=basil-one:{self.branch}")
        if len(items) > 1:
            raise PublishError(f"Multiple release PRs exist for {self.tag}; reconcile and rerun.")
        return self.pr(items[0]["number"]) if items else None

    def reject_competing_releases(self):
        for item in pulls("state=open"):
            self.reject_other_branch(item["head"]["ref"])
        remote = git("ls-remote", "--heads", "origin", "refs/heads/paper-release/*")
        for line in remote.splitlines():
            self.reject_other_branch(line.split("\t")[1].removeprefix("refs/heads/"))

    def reject_other_branch(self, branch):
        if branch.startswith("paper-release/") and branch != self.branch:
            raise PublishError(f"Another release is pending on {branch}; reconcile and rerun.")

    def prepare_pr(self, main, pr):
        ref = f"refs/heads/{self.branch}"
        remote = command(
            "git", "ls-remote", "--exit-code", "--heads", "origin", ref, allowed=(0, 2)
        )
        if remote.returncode == 0:
            git("fetch", "origin", ref)
            head = git("rev-parse", "FETCH_HEAD")
            if pr and pr["head"]["sha"] != head:
                raise PublishError("Release branch differs from the PR head.")
            if not is_ancestor(main, head):
                raise PublishError("Release branch is behind main; update it through GitHub.")
            verify_promotion(self.version, main, head)
        elif pr:
            raise PublishError("The open release PR has no release branch.")
        else:
            validate_latest_release(git_tags())
            promoted = promote_changelog(
                self.version, datetime.now(UTC).date().isoformat(), changelog_at(main), git_tags()
            )
            git("switch", "--create", self.branch, main)
            Path("CHANGELOG.md").write_text(promoted, encoding="utf-8")
            git("add", "--", "CHANGELOG.md")
            git(*BOT, "commit", "-m", self.title)
            git("push", "origin", f"HEAD:{ref}")
        if pr:
            return pr
        try:
            created = gh_api(
                "pulls",
                {
                    "title": self.title,
                    "head": self.branch,
                    "base": "main",
                    "body": (
                        "Review this changelog-only PR, approve requested workflow runs if prompted, "
                        "and merge through the Quality Gate. Rerun Release Publish with the same "
                        "version if interrupted."
                    ),
                },
            )
        except PublishError as error:
            raise PublishError(
                f"Could not create release PR. Confirm Actions may create pull requests: {error}"
            ) from error
        return self.pr(created["number"])

    def verify_merged_source(self, pr, main, *, require_checks):
        number = pr["number"]
        head, source = pr["head"]["sha"], pr["merge_commit_sha"]
        if require_checks:
            verify_checks(check_pages(head), head)
        git("fetch", "origin", f"refs/pull/{number}/head")
        if git("rev-parse", "FETCH_HEAD") != head:
            raise PublishError(f"Fetched PR #{number} head differs from the checked revision.")
        if not is_ancestor(source, main):
            raise PublishError(f"PR #{number} merge is not on current main.")
        if git("rev-parse", f"{head}^{{tree}}") != git("rev-parse", f"{source}^{{tree}}"):
            raise PublishError(f"PR #{number} merged tree differs from the checked head.")
        verify_promotion(self.version, merged_base(head, source, self.version), head)
        return source

    def await_merge(self, pr):
        number = pr["number"]
        url = f"https://github.com/{REPOSITORY}/pull/{number}"
        print(f"Release PR #{number}: {url}", flush=True)
        if pr["state"] == "open" and (summary := os.environ.get("GITHUB_STEP_SUMMARY")):
            with open(summary, "a", encoding="utf-8") as stream:
                print(
                    f"Review {url}, approve checks if prompted, and merge through the Quality Gate. "
                    "Rerun Release Publish with the same version if interrupted.",
                    file=stream,
                )
        deadline = time.monotonic() + 1800
        while True:
            if pr["state"] == "closed":
                try:
                    source = self.verify_merged_source(pr, self.fetch_main(), require_checks=True)
                    print(f"All {len(CHECKS)} required checks passed for PR #{number}.", flush=True)
                    return pr, source
                except ChecksPending as error:
                    detail = str(error)
            else:
                detail = "awaiting human merge"
            print(f"Waiting for PR #{number} — {detail}", flush=True)
            if time.monotonic() >= deadline:
                raise PublishError("Timed out waiting for merge/checks; rerun the same version.")
            time.sleep(60)
            pr = self.pr(number)

    def tag_source(self):
        ref = f"refs/tags/{self.tag}"
        local = command("git", "show-ref", "--verify", "--quiet", ref, allowed=(0, 1))
        remote = git("ls-remote", "--refs", "origin", ref)
        expected = f"{git('rev-parse', ref)}\t{ref}" if local.returncode == 0 else ""
        if remote != expected:
            raise PublishError(f"Local and remote {self.tag} tags differ; rerun after reconciling.")
        return git("rev-parse", f"{ref}^{{commit}}") if remote else None

    def verify_tag(self, tagged, source):
        if tagged != source:
            raise PublishError(f"Existing tag {self.tag} does not point to verified source.")
        ref = f"refs/tags/{self.tag}"
        if git("cat-file", "-t", ref) != "tag":
            raise PublishError(f"Expected an annotated release tag for {self.tag}.")
        if git("for-each-ref", "--format=%(contents)", ref) != f"Release {self.tag}":
            raise PublishError(f"Existing tag {self.tag} has unexpected release provenance.")

    def verify_current_release(self, main, source, tagged):
        tags = git_tags()
        latest = latest_stable_tag(tags)
        if latest and parse_version(latest) > parse_version(self.version):
            raise PublishError(f"A newer stable tag v{latest} exists.")
        approved, current = changelog_at(source), changelog_at(main)
        if changelog_versions(current)[:1] != [self.version] or (
            release_section(self.version, current) != release_section(self.version, approved)
        ):
            raise PublishError("Current main changed the approved release entry or version.")
        if tagged:
            validate_resume(self.version, approved, tags)
        prior = [
            f"v{version}"
            for version in stable_tag_versions(tags)
            if parse_version(version) < parse_version(self.version)
        ]
        validate_latest_release(prior)
        return approved

    def build_and_publish(self, pr, source, tagged):
        # The current wrapper drives the builder and configuration from verified source.
        # In particular, v0.2.2 predates the wrapper.
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "source"
            git("worktree", "add", "--detach", str(checkout), source)
            try:
                env = os.environ.copy()
                env.pop("GH_TOKEN", None)
                env.pop("GITHUB_TOKEN", None)
                print(f"Building Circle3 paper from {source}.", flush=True)
                subprocess.run(
                    ["bash", str(BUILDER)], cwd=checkout, env=env, check=True, timeout=3600
                )
                self.finalize(pr, source, tagged, checkout / PDF)
            finally:
                git("worktree", "remove", "--force", str(checkout))

    def finalize(self, pr, source, tagged, pdf):
        main = self.fetch_main()
        if not is_ancestor(source, main) or self.tag_source() != tagged:
            raise PublishError("Verified source ancestry or release tag changed during the build.")
        self.reject_competing_releases()
        if pr:
            current = self.pr(pr["number"])
            if current["state"] != "closed" or (
                current["head"]["sha"],
                current["merge_commit_sha"],
            ) != (pr["head"]["sha"], source):
                raise PublishError("The checked PR changed during the build.")
            verify_checks(check_pages(current["head"]["sha"]), current["head"]["sha"])
        approved = self.verify_current_release(main, source, tagged)
        if not tagged:
            if not pr or get_release(self.tag) is not None:
                raise PublishError("Cannot tag without a verified PR and a vacant release.")
            git(*BOT, "tag", "-a", self.tag, source, "-m", f"Release {self.tag}")
            git("push", "origin", f"refs/tags/{self.tag}")
        publish_release(self.version, pdf, approved, git_tags())
        print(
            f"Published {self.tag} at {source}: https://github.com/{REPOSITORY}/releases/tag/{self.tag}"
        )

    def resolve_source(self, pr, main, tagged, complete):
        if not tagged:
            if not pr or pr["state"] == "open":
                pr = self.prepare_pr(main, pr)
            return self.await_merge(pr)
        if pr is None:
            if self.version != "0.2.2" or tagged != LEGACY_SOURCE:
                raise PublishError("Existing tag has no merged release PR or historical source.")
            return None, tagged
        if pr["state"] != "closed" or pr["merge_commit_sha"] != tagged:
            raise PublishError("Existing tag does not point to the merged release PR.")
        if complete:
            return pr, self.verify_merged_source(pr, main, require_checks=False)
        return self.await_merge(pr)

    def run(self):
        main = self.fetch_main()
        if git("rev-parse", "HEAD") != main:
            raise PublishError("Dispatch revision is no longer current main; rerun.")
        tagged = self.tag_source()
        release = get_release(self.tag)
        if release is not None and not tagged:
            raise PublishError("An existing release must have its verified tag.")
        complete = (
            release is not None
            and not release["isDraft"]
            and (required_asset_names(self.tag) <= release["assetNames"])
        )
        if not complete:
            self.reject_competing_releases()
        pr, source = self.resolve_source(self.find_pr(), main, tagged, complete)
        if tagged:
            self.verify_tag(tagged, source)
        main = self.fetch_main()
        if not is_ancestor(source, main):
            raise PublishError("Verified source is not on current main.")
        if complete:
            if release_section(self.version, changelog_at(source)) not in release["body"]:
                raise PublishError("Published release notes differ from the tagged changelog.")
            with tempfile.TemporaryDirectory() as directory:
                verify_asset_parity(self.tag, release, Path(directory))
            print(f"Verified existing publication {self.tag} at {source}.")
            return
        self.verify_current_release(main, source, tagged)
        self.build_and_publish(pr, source, tagged)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        if (
            os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
        ):
            raise PublishError("Release Publish requires manual dispatch from Circle main.")
        Publisher(args.version).run()
    except (
        PublishError,
        OSError,
        ReleaseError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        subprocess.SubprocessError,
    ) as error:
        print(f"Release stopped safely: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
