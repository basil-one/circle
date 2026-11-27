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

from scripts.paper.publish import PublishError, validate_check_runs
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


def validate_release_asset(tag, name, asset, *, allow_pending=False):
    if not isinstance(asset["size"], int) or isinstance(asset["size"], bool):
        raise TypeError(f"invalid asset size for {name}")
    if allow_pending and asset["state"] != "uploaded":
        raise Pending(f"Draft release {tag} is still uploading {name}.")
    if asset["state"] != "uploaded" or asset["size"] <= 0:
        raise Mismatch(f"Release asset {name} is incomplete or empty.")
    digest = asset.get("digest")
    if digest is None:
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise TypeError(f"invalid asset digest for {name}")
    return digest


def validate_release_assets(tag, assets, *, allow_pending=False):
    if not isinstance(assets, list):
        raise TypeError("assets is not a list")
    by_name = {asset["name"]: asset for asset in assets}
    if len(by_name) != len(assets):
        raise TypeError("duplicate asset names")
    missing = required_asset_names(tag) - by_name.keys()
    if missing:
        if allow_pending:
            raise Pending(f"Draft release {tag} is awaiting assets: {', '.join(sorted(missing))}.")
        raise Mismatch(f"Release {tag} is missing assets: {', '.join(sorted(missing))}.")
    digests = [
        digest
        for name in required_asset_names(tag)
        if (digest := validate_release_asset(tag, name, by_name[name], allow_pending=allow_pending))
    ]
    if allow_pending and len(digests) == 2 and len(set(digests)) != 1:
        raise Mismatch(f"Draft release {tag} PDF assets have contradictory digests.")
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
    try:
        pulls = json.loads(fetch(f"{API}/pulls?state=closed&base=main&per_page=100", 2_000_000))
    except (ValueError, UnicodeDecodeError) as error:
        raise Unavailable(f"Invalid pull request API response: {error}") from error
    if not isinstance(pulls, list) or len(pulls) >= 100:
        raise Unavailable("Pull request API evidence is malformed or incomplete.")
    candidates = [
        pr
        for pr in pulls
        if isinstance(pr, dict)
        and (pr.get("head") or {}).get("ref") == branch
        and (pr.get("base") or {}).get("ref") == "main"
    ]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise Mismatch(
            f"Multiple release PRs exist for v{version}; reconcile them before publishing."
        )
    return candidates[0]


def pending_release_identity(pr, version):
    title = f"Prepare Circle3 paper v{version}"
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    merge_sha = pr.get("merge_commit_sha")
    if pr.get("title") != title or pr.get("state") != "closed" or not pr.get("merged"):
        return None
    if (head.get("repo") or {}).get("full_name") != "basil-one/circle":
        return None
    if (base.get("repo") or {}).get("full_name") != "basil-one/circle":
        return None
    if not isinstance(merge_sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", merge_sha):
        return None
    return merge_sha, head.get("sha")


def validate_pending_release_files(pr_number, version):
    try:
        files = json.loads(fetch(f"{API}/pulls/{pr_number}/files?per_page=100", 2_000_000))
    except (KeyError, ValueError, UnicodeDecodeError) as error:
        raise Unavailable(f"Invalid files evidence for release PR v{version}: {error}") from error
    if not isinstance(files, list) or len(files) >= 100:
        raise Unavailable(f"Release PR #{pr_number} file evidence is incomplete.")
    if [item.get("filename") for item in files if isinstance(item, dict)] != ["CHANGELOG.md"]:
        raise Mismatch(f"Release PR #{pr_number} contains unexpected files.")


def pending_release_changelog(version, pr_number, merge_sha, main_sha, current):
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
    if (
        changelog_versions(current)[:1] != [version]
        or changelog_versions(merged_changelog)[:1] != [version]
        or unreleased_entries(current)
        or unreleased_entries(merged_changelog)
    ):
        return False
    try:
        verify_exact_promotion(version, merge_sha, merged_changelog)
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"Could not verify release PR #{pr_number} promotion: {error}") from error
    try:
        return release_section(version, current) == release_section(version, merged_changelog)
    except ReleaseError:
        return False


def pending_release_checks(pr_number, head_sha):
    try:
        check_data = json.loads(
            fetch(f"{API}/commits/{head_sha}/check-runs?per_page=100", 2_000_000)
        )
    except (ValueError, UnicodeDecodeError) as error:
        raise Unavailable(
            f"Invalid required-check evidence for PR #{pr_number}: {error}"
        ) from error
    check_runs = check_data.get("check_runs") if isinstance(check_data, dict) else None
    if not isinstance(check_runs, list) or len(check_runs) >= 100:
        raise Unavailable(f"Required-check evidence for PR #{pr_number} is incomplete.")
    try:
        checks_passed, detail = validate_check_runs(check_runs, head_sha)
    except PublishError as error:
        raise Mismatch(str(error)) from error
    if not checks_passed:
        raise Unavailable(f"Required-check evidence for PR #{pr_number} is incomplete: {detail}")
    return detail


def pending_release_pr(version, main_sha, current):
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
    if not pending_release_changelog(version, number, merge_sha, main_sha, current):
        return None
    pending_release_checks(number, head_sha)
    return {"number": number, "merge_sha": merge_sha}


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
        is_newer = parse_version(version) > parse_version(latest_tag_version)
    except ReleaseError as error:
        raise Mismatch(str(error)) from error
    pending = pending_release_pr(version, main_sha, current) if is_newer else None
    if not pending:
        raise Mismatch(
            f"Latest stable tag v{latest_tag_version} differs from the latest changelog "
            "entry; no exact merged release PR verifies an in-flight publication."
        )
    published = check_published_release(
        main_sha,
        latest_tag_version,
        current,
        latest_tag_changelog,
        allow_newer_current=True,
    )
    raise Pending(
        f"Release PR #{pending['number']} for v{version} is merged, but its tag "
        f"is not present yet. Existing publication verified: {published} Rerun Paper: "
        "Publish with the same version to resume."
    )


def prior_release_data(version):
    previous = latest_stable_tag(
        [
            f"v{tag_version}"
            for tag_version in stable_tag_versions(git_tags())
            if parse_version(tag_version) < parse_version(version)
        ]
    )
    if previous is None:
        raise Mismatch(f"No prior stable release exists before v{version}.")
    previous_changelog = subprocess.run(
        ["git", "show", f"v{previous}:CHANGELOG.md"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    return previous, previous_changelog


def check_pending_release(main_sha, version, current, latest_tag_changelog, candidate):
    if candidate is not None and candidate.get("draft") is not True:
        return
    evidence = pending_release_pr(version, main_sha, current)
    if not evidence:
        return
    previous, previous_changelog = prior_release_data(version)
    draft_state = None
    if candidate is not None:
        latest = api_release("latest")
        try:
            validate_metadata(
                version,
                current,
                latest_tag_changelog,
                candidate,
                latest,
                allow_draft=True,
                allow_pending_assets=True,
            )
        except Pending as error:
            draft_state = str(error)
    published = check_published_release(
        main_sha,
        previous,
        current,
        previous_changelog,
        allow_newer_current=True,
    )
    state = "tag exists but its GitHub release is not yet available"
    if candidate is not None:
        state = draft_state or (
            "tag and verified draft release exist, but assets are ready for publication"
        )
    raise Pending(
        f"Release PR #{evidence['number']} for v{version} is in a verified "
        f"pending state: {state}. Existing publication verified: {published} Rerun "
        "Paper: Publish with the same version to resume."
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
    check_pending_release(main_sha, intended_version, current, latest_tag_changelog, candidate)

    return check_published_release(
        main_sha,
        latest_tag_version,
        current,
        latest_tag_changelog,
    )


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
