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

from scripts.paper.release import (
    ReleaseError,
    changelog_versions,
    git_tags,
    parse_version,
    release_section,
    required_asset_names,
    stable_tag_versions,
)

REPOSITORY = "basil-one/circle"
API = f"https://api.github.com/repos/{REPOSITORY}"
DOWNLOAD = f"https://github.com/{REPOSITORY}/releases"
MAX_BYTES = 50_000_000
# v0.2.2 predates release PRs. Its annotated tag resolves to this commit.
LEGACY_SOURCE = "a5e28c0b5ac838ac5d8251c6e37b18cb3ebe0c15"


class Mismatch(ValueError):
    """Confirmed publication inconsistency."""


class Unavailable(ValueError):
    """Evidence could not be obtained or interpreted."""


class Pending(ValueError):
    """A merged release is awaiting publication."""


class NotFound(Unavailable):
    """A requested URL returned HTTP 404."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, limit):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "circle-quality-pulse"}
    api_request = url == API or url.startswith(f"{API}/")
    if api_request and (token := os.environ.get("GITHUB_TOKEN")):
        headers["Authorization"] = f"Bearer {token}"
    opener = urllib.request.build_opener(*([NoRedirect()] if api_request else []))
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise NotFound(f"HTTP 404 retrieving {url}.") from error
        raise Unavailable(f"HTTP {error.code} retrieving {url}.") from error
    except OSError as error:
        raise Unavailable(f"Could not retrieve {url}: {error}") from error
    if len(data) > limit:
        raise Unavailable(f"Response exceeds the {limit}-byte limit: {url}")
    return data


def api(path="", expected=dict):
    url = f"{API}/{path}" if path else API
    try:
        data = json.loads(fetch(url, 2_000_000))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise Unavailable(f"Invalid GitHub response from {url}.") from error
    if not isinstance(data, expected):
        raise Unavailable(f"Malformed GitHub response from {url}.")
    return data


def git(*args, allowed=(0,)):
    result = subprocess.run(["git", *args], check=False, capture_output=True, text=True, timeout=30)
    if result.returncode not in allowed:
        raise Unavailable(f"Could not inspect Git evidence: {result.stderr.strip()}")
    return result


def verify_ancestry(source, main):
    if git("merge-base", "--is-ancestor", source, main, allowed=(0, 1)).returncode == 1:
        raise Mismatch(f"Release source {source} is not an ancestor of published main.")


def changelog_at(revision):
    return git("show", f"{revision}:CHANGELOG.md").stdout


def latest_release():
    try:
        return api("releases/latest")
    except NotFound as error:
        repository = api()
        if repository.get("full_name") == REPOSITORY and repository.get("private") is False:
            raise Mismatch("No latest published release exists for Circle.") from error
        raise Unavailable("Cannot establish whether Circle's latest release is absent.") from error


def latest_version(changelog):
    versions = changelog_versions(changelog)
    if not versions or len(set(versions)) != len(versions):
        raise Mismatch("CHANGELOG.md has missing or duplicate stable release headings.")
    return versions[0]


def release_pr_source(version):
    tag = f"v{version}"
    branch = f"paper-release/{tag}"
    items = api(f"pulls?state=closed&base=main&head=basil-one:{branch}&per_page=100", list)
    if len(items) != 1:
        raise Mismatch(f"Expected one closed release PR for {tag}; found {len(items)}.")
    number = items[0]["number"]
    if type(number) is not int or number < 1:
        raise Unavailable(f"Invalid release PR number for {tag}.")
    pr = api(f"pulls/{number}")
    if (
        pr["number"] != number
        or pr["title"] != f"Prepare Circle3 paper {tag}"
        or pr["base"]["ref"] != "main"
        or pr["base"]["repo"]["full_name"] != REPOSITORY
        or pr["head"]["ref"] != branch
        or pr["head"]["repo"]["full_name"] != REPOSITORY
    ):
        raise Mismatch(f"PR #{number} does not match the {tag} release identity.")
    if pr["state"] != "closed" or pr["merged"] is not True or not pr["merged_at"]:
        raise Mismatch(f"Release PR #{number} has no confirmed merge.")
    if pr["merged_by"]["type"] != "User":
        raise Mismatch(f"Release PR #{number} was not merged by a human GitHub user.")
    source = pr["merge_commit_sha"]
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise Unavailable(f"Release PR #{number} has no valid merge revision.")
    return number, source


def tag_commit(version):
    ref = f"refs/tags/v{version}"
    if git("cat-file", "-t", ref).stdout.strip() != "tag" or (
        git("for-each-ref", "--format=%(contents)", ref).stdout.strip() != f"Release v{version}"
    ):
        raise Mismatch(f"Tag v{version} has unexpected release provenance.")
    return git("rev-parse", f"{ref}^{{commit}}").stdout.strip()


def verify_entry(version, current, source):
    released = changelog_at(source)
    if latest_version(released) != version:
        raise Mismatch(f"Release source does not identify v{version} as its latest entry.")
    changes = release_section(version, current)
    if changes != release_section(version, released):
        raise Mismatch(f"Current changelog entry for v{version} differs from the release source.")
    return changes


def asset_metadata(release, tag):
    assets = release["assets"]
    if not isinstance(assets, list) or any(not isinstance(item["name"], str) for item in assets):
        raise Unavailable(f"Malformed release assets for {tag}.")
    by_name = {asset["name"]: asset for asset in assets}
    if len(by_name) != len(assets):
        raise Mismatch(f"Release {tag} contains duplicate asset names.")
    names = required_asset_names(tag)
    if missing := names - by_name.keys():
        raise Mismatch(f"Release {tag} is missing assets: {', '.join(sorted(missing))}.")
    selected = {name: by_name[name] for name in sorted(names)}
    for name, asset in selected.items():
        size, state, digest = asset["size"], asset["state"], asset.get("digest")
        if type(size) is not int or not isinstance(state, str):
            raise Unavailable(f"Invalid size or upload state for {name}.")
        if state != "uploaded" or size <= 0:
            raise Mismatch(f"Release asset {name} is incomplete or empty.")
        if digest is not None and (
            not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        ):
            raise Unavailable(f"Invalid release digest for {name}.")
    first, second = selected.values()
    if first["size"] != second["size"] or (
        first.get("digest") and second.get("digest") and first["digest"] != second["digest"]
    ):
        raise Mismatch(f"Release {tag} PDF asset metadata shows different bytes.")
    return selected


def public_pdf(url, name):
    try:
        return fetch(url, MAX_BYTES)
    except NotFound as error:
        if getattr(error.__cause__, "url", None) == url:
            raise Mismatch(f"Public download {name} is missing (HTTP 404).") from error
        raise Unavailable(f"Public download {name} redirected to an unavailable asset.") from error


def validate_download(data, name, asset, directory):
    digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
    if len(data) != asset["size"] or asset.get("digest") not in (None, digest):
        raise Mismatch(f"Public download {name} differs from its release asset metadata.")
    path = directory / name
    path.write_bytes(data)
    result = subprocess.run(["pdfinfo", str(path)], capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise Mismatch(f"Downloaded {name} is not a readable PDF.")


def verify_public_pdfs(tag, assets):
    stable, versioned = "circle3-paper.pdf", f"circle3-paper-{tag}.pdf"
    downloads = {}
    failures, unavailable = [], []
    with tempfile.TemporaryDirectory() as temporary:
        for name, url in (
            (stable, f"{DOWNLOAD}/latest/download/{stable}"),
            (versioned, f"{DOWNLOAD}/download/{tag}/{versioned}"),
        ):
            try:
                data = public_pdf(url, name)
                validate_download(data, name, assets[name], Path(temporary))
                downloads[name] = data
            except Mismatch as error:
                failures.append(str(error))
            except (Unavailable, OSError, subprocess.SubprocessError) as error:
                unavailable.append(str(error))
    if failures:
        raise Mismatch(" ".join(failures + unavailable))
    if unavailable:
        raise Unavailable(" ".join(unavailable))
    if downloads[stable] != downloads[versioned]:
        raise Mismatch(f"Stable and versioned public PDFs for {tag} differ.")


def verify_published(release, version, main, current):
    tag = f"v{version}"
    if type(release["draft"]) is not bool or type(release["prerelease"]) is not bool:
        raise Unavailable(f"Malformed publication state for {tag}.")
    if release["draft"] or release["prerelease"] or not release["published_at"]:
        raise Mismatch(f"Release {tag} is not a published stable release.")
    source = tag_commit(version)
    expected = LEGACY_SOURCE if version == "0.2.2" else release_pr_source(version)[1]
    if source != expected:
        raise Mismatch(f"Published tag {tag} does not match its release source.")
    verify_ancestry(source, main)
    changes = verify_entry(version, current, source)
    if not isinstance(release["body"], str):
        raise Unavailable(f"Malformed release notes for {tag}.")
    if changes not in release["body"]:
        raise Mismatch(f"Release notes for {tag} differ from CHANGELOG.md.")
    verify_public_pdfs(tag, asset_metadata(release, tag))
    return f"{tag} at {source}: release, changelog and readable public PDFs agree."


def pending_release(version, latest_tag, main, current):
    number, source = release_pr_source(version)
    verify_ancestry(source, main)
    verify_entry(version, current, source)
    if version == latest_tag and tag_commit(version) != source:
        raise Mismatch(f"Pending tag v{version} does not match merged PR #{number}.")
    try:
        release = api(f"releases/tags/v{version}")
    except NotFound:
        release = None
    if release is not None:
        if release["tag_name"] != f"v{version}" or release["draft"] is False:
            raise Mismatch(f"Release v{version} conflicts with the latest published release.")
        if release["draft"] is not True:
            raise Unavailable(f"Malformed release state for v{version}.")
        if version != latest_tag or release["prerelease"] is not False:
            raise Mismatch(f"Draft release v{version} conflicts with its stable tag or version.")
    return f"Release PR #{number} for v{version} is merged and awaiting publication."


def check_publication():
    main = git("rev-parse", "--verify", "refs/remotes/origin/main^{commit}").stdout.strip()
    current = changelog_at(main)
    intended = latest_version(current)
    release = latest_release()
    tag = release["tag_name"]
    if not isinstance(tag, str):
        raise Unavailable("Malformed latest release tag.")
    tags = sorted(stable_tag_versions(git_tags()), key=parse_version, reverse=True)
    published = tag.removeprefix("v")
    if not tag.startswith("v") or published not in tags:
        raise Mismatch(f"Latest published release {tag} has no matching stable tag.")
    # At most one promoted version may be ahead of the publication.
    allowed = [published]
    if intended != published:
        if parse_version(intended) <= parse_version(published) or (
            changelog_versions(current)[:2] != [intended, published]
        ):
            raise Mismatch("Latest publication and main disagree on the release sequence.")
        allowed.insert(0, intended)
    if [version for version in tags if parse_version(version) >= parse_version(published)] not in (
        [published],
        allowed,
    ):
        raise Mismatch("Stable tags and main disagree on the release sequence.")
    verified = verify_published(release, published, main, current)
    if intended != published:
        detail = pending_release(intended, tags[0], main, current)
        raise Pending(f"{detail} Existing publication verified. {verified}")
    return verified


def main():
    try:
        status, detail, code = "passed", check_publication(), 0
    except Pending as error:
        status, detail, code = "pending", f"{error} Resume with Release Publish.", 4
    except (Mismatch, ReleaseError) as error:
        status, detail, code = "failed", f"{error} Review release evidence before repair.", 3
    except (Unavailable, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        status, detail, code = "unknown", f"{error} Restore access and rerun the check.", 2
    detail = " ".join(str(detail).split())[:1000]
    print(f"{status.upper()}: {detail}")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            print(f"status={status}\ndetail={detail}", file=stream)
    return code


if __name__ == "__main__":
    sys.exit(main())
