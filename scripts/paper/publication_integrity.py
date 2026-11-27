"""Read-only verification of the latest published Circle3 paper release."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from scripts.paper.publish import (
    PublishError,
    branch_name,
    pull_request_merged,
    validate_check_runs,
    validate_pull_request,
)
from scripts.paper_release import (
    ReleaseError,
    changelog_versions,
    git_tags,
    latest_stable_tag,
    parse_version,
    promote_changelog,
    release_section,
    required_asset_names,
    stable_tag_versions,
)

ROOT = Path(__file__).resolve().parents[2]
API = "https://api.github.com/repos/basil-one/circle"
DOWNLOAD = "https://github.com/basil-one/circle/releases"
MAX_BYTES = 50_000_000


class Mismatch(ValueError):
    """Verified publication inconsistency."""


class Unavailable(ValueError):
    """Evidence could not be obtained or interpreted."""


class Pending(ValueError):
    """A verified human-reviewed release is progressing but is not published."""


class NotFound(Unavailable):
    """A requested URL returned HTTP 404."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, limit):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "circle-quality-pulse",
    }
    api_request = url == API or url.startswith(f"{API}/")
    if api_request and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    request = urllib.request.Request(url, headers=headers)
    try:
        opener = (
            urllib.request.build_opener(NoRedirect())
            if api_request
            else urllib.request.build_opener()
        )
        with opener.open(request, timeout=30) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise NotFound(f"HTTP 404 retrieving {url}.") from error
        raise Unavailable(f"HTTP {error.code} retrieving {url}.") from error
    except (OSError, urllib.error.URLError) as error:
        raise Unavailable(f"Could not retrieve {url}: {error}") from error
    if len(data) > limit:
        raise Unavailable(f"Response exceeds the {limit}-byte limit: {url}")
    return data


def api_release(path):
    content = fetch(f"{API}/releases/{path}", 2_000_000)
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise TypeError("not an object")
        return data
    except (TypeError, ValueError, UnicodeDecodeError) as error:
        raise Unavailable(f"Invalid release API response for {path}: {error}") from error


def validate_changelogs(version, current, tagged, *, allow_newer_current=False):
    tag = f"v{version}"
    current_versions = changelog_versions(current)
    if not current_versions or (
        current_versions[0] != version and not (allow_newer_current and version in current_versions)
    ):
        raise Mismatch(f"Latest stable tag {tag} differs from the latest changelog entry.")
    if not changelog_versions(tagged) or changelog_versions(tagged)[0] != version:
        raise Mismatch(f"The changelog at {tag} does not identify that version as latest.")
    try:
        changes = release_section(version, current)
        if changes != release_section(version, tagged):
            raise Mismatch(f"Changelog entry for {tag} differs from the tagged entry.")
    except ReleaseError as error:
        raise Mismatch(str(error)) from error
    return changes


def validate_release_asset(tag, name, asset):
    if not isinstance(asset["size"], int) or isinstance(asset["size"], bool):
        raise TypeError(f"invalid asset size for {name}")
    if not isinstance(asset["state"], str):
        raise TypeError(f"invalid asset state for {name}")
    if asset["size"] < 0 or (asset["state"] == "uploaded" and asset["size"] == 0):
        raise Mismatch(f"Release asset {name} is incomplete or empty.")
    digest = asset.get("digest")
    if digest is None:
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise TypeError(f"invalid asset digest for {name}")
    return digest


def release_asset_index(assets):
    if not isinstance(assets, list):
        raise TypeError("assets is not a list")
    if not all(isinstance(asset, dict) for asset in assets):
        raise TypeError("asset evidence contains a non-object")
    by_name = {asset["name"]: asset for asset in assets}
    if len(by_name) != len(assets):
        raise TypeError("duplicate asset names")
    return by_name


def inspect_present_release_assets(tag, by_name, names):
    digests = []
    uploading = []
    for name in sorted(names):
        asset = by_name[name]
        digest = validate_release_asset(tag, name, asset)
        if digest:
            digests.append(digest)
        if asset["state"] != "uploaded":
            uploading.append(name)
    if len(digests) == 2 and len(set(digests)) != 1:
        raise Mismatch(f"Draft release {tag} PDF assets have contradictory digests.")
    return uploading


def pending_asset_state(tag, missing, uploading):
    if missing:
        raise Pending(f"Draft release {tag} is awaiting assets: {', '.join(sorted(missing))}.")
    if uploading:
        raise Pending(f"Draft release {tag} is still uploading {', '.join(uploading)}.")


def validate_release_assets(tag, assets, *, allow_pending=False):
    by_name = release_asset_index(assets)
    missing = required_asset_names(tag) - by_name.keys()
    present = required_asset_names(tag) & by_name.keys()
    uploading = inspect_present_release_assets(tag, by_name, present)
    if missing:
        if allow_pending:
            pending_asset_state(tag, missing, uploading)
        raise Mismatch(f"Release {tag} is missing assets: {', '.join(sorted(missing))}.")
    if uploading:
        if allow_pending:
            pending_asset_state(tag, missing, uploading)
        raise Mismatch(f"Release asset {', '.join(uploading)} is incomplete or empty.")
    return by_name


def validate_metadata(
    version,
    current,
    tagged,
    release,
    latest,
    *,
    allow_newer_current=False,
    allow_draft=False,
    allow_pending_assets=False,
):
    tag = f"v{version}"
    changes = validate_changelogs(version, current, tagged, allow_newer_current=allow_newer_current)
    try:
        if not all(isinstance(release[key], bool) for key in ("draft", "prerelease")):
            raise TypeError("invalid release state")
        if not all(isinstance(release[key], str) for key in ("tag_name", "body")):
            raise TypeError("invalid release identity or notes")
        if release["tag_name"] != tag:
            raise Mismatch(f"Release identity does not match {tag}.")
        if release["prerelease"] or (release["draft"] and not allow_draft):
            raise Mismatch(f"Release {tag} is not a published stable release.")
        if not release["draft"] and not release["published_at"]:
            raise Mismatch(f"Release {tag} is not a published stable release.")
        if latest["tag_name"] != tag and not (allow_draft and release["draft"]):
            raise Mismatch(f"Latest published release is not {tag}.")
        if changes not in release["body"]:
            raise Mismatch(f"Release notes for {tag} differ from CHANGELOG.md.")
        return validate_release_assets(tag, release["assets"], allow_pending=allow_pending_assets)
    except (KeyError, TypeError, AttributeError) as error:
        raise Unavailable(f"Incomplete release API evidence for {tag}: {error}") from error


def validate_pdf(data, name, directory):
    if not data:
        raise Mismatch(f"Downloaded {name} is empty.")
    path = directory / name
    path.write_bytes(data)
    try:
        result = subprocess.run(
            ["pdfinfo", str(path)], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"Could not inspect {name} with pdfinfo: {error}") from error
    if result.returncode:
        raise Mismatch(f"Downloaded {name} is not a readable PDF.")


def publication_snapshot():
    try:
        main_sha = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        version = latest_stable_tag(git_tags())
        if not version:
            raise Mismatch("No stable version tag exists.")
        current = subprocess.run(
            ["git", "show", f"{main_sha}:CHANGELOG.md"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        tagged = subprocess.run(
            ["git", "show", f"v{version}:CHANGELOG.md"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError, ReleaseError) as error:
        raise Unavailable(f"Could not inspect tags or changelog: {error}") from error
    tag = f"v{version}"
    try:
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", tag, main_sha],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"Could not inspect {tag} ancestry: {error}") from error
    if ancestry.returncode == 1:
        raise Mismatch(f"Latest stable tag {tag} is not an ancestor of published main {main_sha}.")
    if ancestry.returncode:
        raise Unavailable(f"Could not establish {tag} ancestry for published main {main_sha}.")
    return main_sha, version, current, tagged


def latest_release(tag):
    try:
        latest = api_release("latest")
    except NotFound as error:
        try:
            repo = json.loads(fetch(API, 2_000_000))
        except (ValueError, UnicodeDecodeError) as repo_error:
            raise Unavailable(f"Cannot confirm repository visibility: {repo_error}") from repo_error
        if (
            isinstance(repo, dict)
            and repo.get("private") is False
            and repo.get("full_name") == "basil-one/circle"
        ):
            raise Mismatch(
                f"No latest published release exists for public repository; expected {tag}."
            ) from error
        raise Unavailable(
            "Cannot confirm whether the latest release is absent or inaccessible."
        ) from error
    if not isinstance(latest.get("tag_name"), str):
        raise Unavailable("Latest release API response has no tag name.")
    if latest["tag_name"] != tag:
        raise Mismatch(f"Latest published release is not {tag}; publication may be incomplete.")
    return latest


def pending_release_candidate(version):
    branch = f"paper-release/v{version}"
    head = f"basil-one:{branch}"
    try:
        pulls = json.loads(
            fetch(
                f"{API}/pulls?state=closed&base=main&head={head}&per_page=100",
                2_000_000,
            )
        )
    except (ValueError, UnicodeDecodeError) as error:
        raise Unavailable(f"Invalid pull request API response: {error}") from error
    if not isinstance(pulls, list) or len(pulls) >= 100:
        raise Unavailable("Pull request API evidence is malformed or incomplete.")
    if not all(isinstance(pr, dict) for pr in pulls):
        raise Unavailable("Pull request list contains malformed evidence.")
    if not pulls:
        return None
    if len(pulls) != 1:
        raise Mismatch(
            f"Multiple release PRs exist for v{version}; reconcile them before publishing."
        )
    number = pulls[0].get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise Unavailable(f"Release PR list for v{version} has no valid PR number.")
    try:
        detail = json.loads(fetch(f"{API}/pulls/{number}", 2_000_000))
    except (ValueError, UnicodeDecodeError) as error:
        raise Unavailable(f"Invalid release PR detail for v{version}: {error}") from error
    if not isinstance(detail, dict):
        raise Unavailable(f"Release PR #{number} detail is malformed.")
    return detail


def pending_release_identity(pr, version):
    number = pr.get("number")
    try:
        validate_pull_request(pr, number, version, branch_name(version))
    except PublishError as error:
        raise Mismatch(str(error)) from error
    try:
        if not pull_request_merged(pr):
            return None
    except PublishError as error:
        raise Unavailable(str(error)) from error
    head = pr["head"]
    merge_sha = pr.get("merge_commit_sha")
    if not isinstance(merge_sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", merge_sha):
        raise Mismatch(f"Release PR #{number} has no valid merge revision.")
    return merge_sha, head.get("sha")


def validate_pending_release_files(pr_number, version):
    try:
        files = json.loads(fetch(f"{API}/pulls/{pr_number}/files?per_page=100", 2_000_000))
    except (KeyError, ValueError, UnicodeDecodeError) as error:
        raise Unavailable(f"Invalid files evidence for release PR v{version}: {error}") from error
    if not isinstance(files, list) or len(files) >= 100:
        raise Unavailable(f"Release PR #{pr_number} file evidence is incomplete.")
    if not all(isinstance(item, dict) for item in files):
        raise Unavailable(f"Release PR #{pr_number} file evidence is malformed.")
    if [item.get("filename") for item in files] != ["CHANGELOG.md"]:
        raise Mismatch(f"Release PR #{pr_number} contains unexpected files.")


def api_commit_tree(sha):
    try:
        commit = json.loads(fetch(f"{API}/commits/{sha}", 2_000_000))
        tree_sha = commit["commit"]["tree"]["sha"]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise Unavailable(f"Commit tree evidence for {sha} is malformed: {error}") from error
    if not isinstance(tree_sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", tree_sha):
        raise Unavailable(f"Commit tree evidence for {sha} is malformed.")
    return tree_sha


def pending_release_changelog(version, pr_number, head_sha, merge_sha, main_sha, current):
    try:
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", merge_sha, main_sha],
            check=False,
            capture_output=True,
            timeout=30,
        )
        merged_changelog = subprocess.run(
            ["git", "show", f"{merge_sha}:CHANGELOG.md"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"Could not inspect merged release PR #{pr_number}: {error}") from error
    if ancestry.returncode == 1:
        raise Mismatch(f"Release PR #{pr_number} merge revision is not on published main.")
    if ancestry.returncode:
        raise Unavailable(f"Could not establish release PR #{pr_number} ancestry.")
    try:
        promotion_is_current = (
            changelog_versions(current)[:1] == [version]
            and changelog_versions(merged_changelog)[:1] == [version]
            and not unreleased_entries(merged_changelog)
        )
    except PublishError as error:
        raise Mismatch(f"Release PR #{pr_number} changelog is malformed: {error}") from error
    if not promotion_is_current:
        return False
    if api_commit_tree(head_sha) != api_commit_tree(merge_sha):
        raise Mismatch(f"Release PR #{pr_number} merged tree differs from its reviewed source.")
    try:
        verify_exact_promotion(version, merge_sha, merged_changelog)
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"Could not verify release PR #{pr_number} promotion: {error}") from error
    try:
        return release_section(version, current) == release_section(version, merged_changelog)
    except ReleaseError:
        return False


def pending_release_checks(pr_number, head_sha):
    check_runs = []
    total = None
    page = 1
    while total is None or len(check_runs) < total:
        try:
            data = json.loads(
                fetch(
                    f"{API}/commits/{head_sha}/check-runs?per_page=100&page={page}",
                    2_000_000,
                )
            )
        except (ValueError, UnicodeDecodeError) as error:
            raise Unavailable(
                f"Invalid required-check evidence for PR #{pr_number}: {error}"
            ) from error
        if not isinstance(data, dict):
            raise Unavailable(f"Required-check evidence for PR #{pr_number} is malformed.")
        page_total = data.get("total_count")
        page_runs = data.get("check_runs")
        if (
            not isinstance(page_total, int)
            or isinstance(page_total, bool)
            or page_total < 0
            or not isinstance(page_runs, list)
            or len(page_runs) > 100
            or (total is not None and page_total != total)
            or (total is not None and not page_runs)
            or page_total > 10_000
        ):
            raise Unavailable(f"Required-check evidence for PR #{pr_number} is incomplete.")
        total = page_total
        check_runs.extend(page_runs)
        if len(check_runs) > total:
            raise Unavailable(f"Required-check evidence for PR #{pr_number} is contradictory.")
        page += 1
    try:
        checks_passed, detail = validate_check_runs(check_runs, head_sha)
    except PublishError as error:
        raise Mismatch(str(error)) from error
    if not checks_passed:
        raise Unavailable(f"Required-check evidence for PR #{pr_number} is incomplete: {detail}")
    return detail


def verified_release_pr(version, main_sha, current, *, require_checks):
    pr = pending_release_candidate(version)
    if not pr:
        return None
    identity = pending_release_identity(pr, version)
    if not identity:
        return None
    merge_sha, head_sha = identity
    number = pr["number"]
    validate_pending_release_files(number, version)
    if not re.fullmatch(r"[0-9a-f]{40,64}", str(head_sha)):
        raise Mismatch(f"Release PR #{number} has no valid approved head revision.")
    if not pending_release_changelog(version, number, head_sha, merge_sha, main_sha, current):
        return None
    if require_checks:
        pending_release_checks(number, head_sha)
    return {"number": number, "merge_sha": merge_sha}


def pending_release_pr(version, main_sha, current):
    return verified_release_pr(version, main_sha, current, require_checks=True)


def verify_exact_promotion(version, revision, expected_changelog):
    commits = subprocess.run(
        ["git", "log", "--reverse", "--format=%H", revision, "--", "CHANGELOG.md"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.splitlines()
    for commit in commits:
        current = subprocess.run(
            ["git", "show", f"{commit}:CHANGELOG.md"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        if version not in changelog_versions(current):
            continue
        parents = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", commit],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.split()[1:]
        for parent in parents:
            baseline = subprocess.run(
                ["git", "show", f"{parent}:CHANGELOG.md"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
            if version in changelog_versions(baseline):
                continue
            heading = re.search(
                rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$",
                current,
                re.MULTILINE,
            )
            if not heading:
                raise Mismatch(f"Release PR changelog heading for v{version} is invalid.")
            tags = subprocess.run(
                ["git", "tag", "--list", "v*", "--merged", parent],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.splitlines()
            try:
                promoted = promote_changelog(version, heading.group(1), baseline, tags)
            except ReleaseError as error:
                raise Mismatch(f"Release PR changelog promotion is invalid: {error}") from error
            if promoted != current or current != expected_changelog:
                raise Mismatch(
                    f"Release PR for v{version} is not the exact expected changelog promotion."
                )
            return
    raise Mismatch(f"Could not establish the original changelog promotion for v{version}.")


def unreleased_entries(changelog):
    lines = changelog.splitlines()
    headings = [index for index, line in enumerate(lines) if line == "## [Unreleased]"]
    if len(headings) != 1:
        return "invalid"
    end = next(
        (index for index in range(headings[0] + 1, len(lines)) if lines[index].startswith("## [")),
        len(lines),
    )
    return "\n".join(lines[headings[0] + 1 : end]).strip()


def check_published_release(main_sha, version, current, tagged, *, allow_newer_current=False):
    tag = f"v{version}"
    latest = latest_release(tag)
    release = api_release(f"tags/{tag}")
    assets = validate_metadata(
        version,
        current,
        tagged,
        release,
        latest,
        allow_newer_current=allow_newer_current,
    )
    stable_name = "circle3-paper.pdf"
    versioned_name = f"circle3-paper-{tag}.pdf"
    downloads = {}
    mismatches = []
    unavailable = []
    with tempfile.TemporaryDirectory() as temporary:
        for name, url in (
            (stable_name, f"{DOWNLOAD}/latest/download/{stable_name}"),
            (versioned_name, f"{DOWNLOAD}/download/{tag}/{versioned_name}"),
        ):
            try:
                data = public_pdf(url, name)
            except Mismatch as error:
                mismatches.append(str(error))
                continue
            except Unavailable as error:
                unavailable.append(str(error))
                continue
            digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
            if len(data) != assets[name]["size"] or (
                assets[name].get("digest") and assets[name]["digest"] != digest
            ):
                mismatches.append(
                    f"Public download {name} differs from its release asset metadata."
                )
            try:
                validate_pdf(data, name, Path(temporary))
            except Mismatch as error:
                mismatches.append(str(error))
            except Unavailable as error:
                unavailable.append(str(error))
            downloads[name] = data
    if mismatches:
        raise Mismatch(" ".join(mismatches + unavailable))
    if unavailable:
        raise Unavailable(" ".join(unavailable))
    if downloads[stable_name] != downloads[versioned_name]:
        raise Mismatch(f"Stable and versioned public PDFs for {tag} differ.")
    return f"{tag} at published main {main_sha}: release, changelog and readable public PDFs agree."


def check_pending_changelog(main_sha, latest_tag_version, current, latest_tag_changelog, version):
    if version == latest_tag_version:
        return
    try:
        published = check_published_release(
            main_sha,
            latest_tag_version,
            current,
            latest_tag_changelog,
            allow_newer_current=True,
        )
    except Mismatch:
        raise
    except Unavailable as error:
        published = None
        previous_unavailable = str(error)
    else:
        previous_unavailable = None
    try:
        is_newer = parse_version(version) > parse_version(latest_tag_version)
    except ReleaseError as error:
        raise Mismatch(str(error)) from error
    pending = pending_release_pr(version, main_sha, current) if is_newer else None
    if not pending:
        previous = (
            f" Existing publication verified: {published}"
            if published
            else f" Existing publication evidence unavailable: {previous_unavailable}"
        )
        raise Mismatch(
            f"Latest stable tag v{latest_tag_version} differs from the latest changelog "
            f"entry; no exact merged release PR verifies an in-flight publication.{previous}"
        )
    if not published:
        raise Unavailable(
            f"Release PR #{pending['number']} is verified, but the prior published release "
            f"cannot be confirmed: {previous_unavailable}"
        )
    raise Pending(
        f"Release PR #{pending['number']} for v{version} is merged, but its tag "
        f"is not present yet. Existing publication verified: {published} Rerun Paper: "
        "Publish with the same version to resume."
    )


def prior_release_data(version, *, allow_missing=False):
    previous = latest_stable_tag(
        [
            f"v{tag_version}"
            for tag_version in stable_tag_versions(git_tags())
            if parse_version(tag_version) < parse_version(version)
        ]
    )
    if previous is None:
        if allow_missing:
            return None
        raise Mismatch(f"No prior stable release exists before v{version}.")
    previous_changelog = subprocess.run(
        ["git", "show", f"v{previous}:CHANGELOG.md"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    return previous, previous_changelog


def existing_tag_commit(version):
    tag_ref = f"refs/tags/v{version}"
    try:
        exists = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", tag_ref],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"Could not inspect {tag_ref}: {error}") from error
    if exists.returncode == 1:
        return None
    if exists.returncode:
        raise Unavailable(f"Could not inspect {tag_ref}.")
    try:
        return subprocess.run(
            ["git", "rev-parse", "--verify", f"{tag_ref}^{{commit}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"Could not resolve {tag_ref}: {error}") from error


def is_draft_release_candidate(version, candidate):
    if candidate is not None:
        if not isinstance(candidate.get("draft"), bool):
            raise Unavailable(f"Release API state for v{version} is malformed.")
        if candidate.get("draft") is not True:
            return False
    return True


def verified_pending_release(main_sha, version, current, candidate):
    evidence = pending_release_pr(version, main_sha, current)
    if not evidence:
        return
    tag_sha = existing_tag_commit(version)
    if candidate is not None and tag_sha is None:
        raise Mismatch(f"Draft release v{version} exists without its stable tag.")
    if tag_sha is not None and tag_sha != evidence["merge_sha"]:
        raise Mismatch(
            f"Tag v{version} points to {tag_sha}, not verified PR merge {evidence['merge_sha']}."
        )
    previous, previous_changelog = prior_release_data(version)
    return evidence, tag_sha, previous, previous_changelog


def validate_pending_draft(version, current, candidate, tag_sha):
    if candidate is None:
        return None
    latest = api_release("latest")
    try:
        tagged_changelog = subprocess.run(
            ["git", "show", f"{tag_sha}:CHANGELOG.md"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        validate_metadata(
            version,
            current,
            tagged_changelog,
            candidate,
            latest,
            allow_newer_current=True,
            allow_draft=True,
            allow_pending_assets=True,
        )
    except Pending as error:
        return str(error)
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"Could not inspect draft release v{version}: {error}") from error
    return None


def check_pending_release(main_sha, version, current, latest_tag_changelog, candidate):
    if not is_draft_release_candidate(version, candidate):
        return
    if candidate is None and version == "0.2.2":
        return
    previous_unavailable = None
    try:
        previous, previous_changelog = prior_release_data(version)
        published = check_published_release(
            main_sha,
            previous,
            current,
            previous_changelog,
            allow_newer_current=True,
        )
    except Mismatch:
        raise
    except Unavailable as error:
        previous_unavailable = error
    context = verified_pending_release(main_sha, version, current, candidate)
    if not context:
        if previous_unavailable:
            raise previous_unavailable
        return
    evidence, tag_sha, _, _ = context
    draft_state = validate_pending_draft(version, current, candidate, tag_sha)
    if previous_unavailable:
        raise Unavailable(
            f"Release PR #{evidence['number']} is verified, but the prior published release "
            f"cannot be confirmed: {previous_unavailable}"
        )
    state = "tag exists but its GitHub release is not yet available"
    if tag_sha is None:
        state = "verified release PR is merged; its tag has not yet been created"
    if candidate is not None:
        state = draft_state or (
            "draft metadata is present; its PDF bytes are not yet published and validated"
        )
    raise Pending(
        f"Release PR #{evidence['number']} for v{version} is in a verified "
        f"pending state: {state}. Existing publication verified: {published} Rerun "
        "Paper: Publish with the same version to resume."
    )


def verify_published_tag_provenance(main_sha, version, current):
    if version == "0.2.2":
        return
    evidence = verified_release_pr(version, main_sha, current, require_checks=False)
    if not evidence:
        raise Mismatch(f"Published v{version} has no exact merged release PR provenance.")
    tag_sha = existing_tag_commit(version)
    if tag_sha != evidence["merge_sha"]:
        raise Mismatch(
            f"Published tag v{version} points to {tag_sha or 'no local tag'}, not verified PR "
            f"merge {evidence['merge_sha']}."
        )


def check_publication():
    main_sha, latest_tag_version, current, latest_tag_changelog = publication_snapshot()
    versions = changelog_versions(current)
    if not versions:
        raise Mismatch("CHANGELOG.md has no stable release entry.")
    intended_version = versions[0]
    check_pending_changelog(
        main_sha, latest_tag_version, current, latest_tag_changelog, intended_version
    )
    try:
        candidate = api_release(f"tags/v{intended_version}")
    except NotFound:
        candidate = None
    except Unavailable:
        if intended_version == latest_tag_version:
            previous = prior_release_data(intended_version, allow_missing=True)
            if previous:
                previous_version, previous_changelog = previous
                check_published_release(
                    main_sha,
                    previous_version,
                    current,
                    previous_changelog,
                    allow_newer_current=True,
                )
        raise
    check_pending_release(main_sha, intended_version, current, latest_tag_changelog, candidate)

    published = check_published_release(
        main_sha,
        latest_tag_version,
        current,
        latest_tag_changelog,
    )
    verify_published_tag_provenance(main_sha, latest_tag_version, current)
    return published


def public_pdf(url, name):
    try:
        return fetch(url, MAX_BYTES)
    except NotFound as error:
        if error.__cause__ and getattr(error.__cause__, "url", None) == url:
            raise Mismatch(f"Public download {name} is missing (HTTP 404).") from error
        raise Unavailable(f"Public download {name} redirected to an unavailable asset.") from error


def main():
    try:
        status, detail, code = "passed", check_publication(), 0
    except Pending as error:
        status, detail, code = (
            "pending",
            f"{error} Human attention is still required.",
            4,
        )
    except Mismatch as error:
        status, detail, code = (
            "failed",
            f"{error} Review release evidence before repair.",
            3,
        )
    except Unavailable as error:
        status, detail, code = (
            "unknown",
            f"{error} Restore access and rerun the check.",
            2,
        )
    detail = " ".join(str(detail).split())[:1000]
    print(f"{status.upper()}: {detail}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            print(f"status={status}\ndetail={detail}", file=output)
    return code


if __name__ == "__main__":
    sys.exit(main())
