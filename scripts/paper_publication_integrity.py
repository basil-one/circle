#!/usr/bin/env python3
"""Read-only verification of the latest published Circle3 paper release."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from paper_release import (
    ReleaseError,
    changelog_versions,
    git_tags,
    latest_stable_tag,
    release_section,
    required_asset_names,
)

API = "https://api.github.com/repos/basil-one/circle"
DOWNLOAD = "https://github.com/basil-one/circle/releases"
MAX_BYTES = 50_000_000


class Mismatch(ValueError):
    """Verified publication inconsistency."""


class Unavailable(ValueError):
    """Evidence could not be obtained or interpreted."""


class NotFound(Unavailable):
    """A requested URL returned HTTP 404."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, limit):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "circle-quality-pulse"}
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
            raise ValueError("not an object")
        return data
    except (ValueError, UnicodeDecodeError) as error:
        raise Unavailable(f"Invalid release API response for {path}: {error}") from error


def validate_changelogs(version, current, tagged):
    tag = f"v{version}"
    if not changelog_versions(current) or changelog_versions(current)[0] != version:
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


def validate_release_assets(tag, assets):
    if not isinstance(assets, list):
        raise TypeError("assets is not a list")
    by_name = {asset["name"]: asset for asset in assets}
    if len(by_name) != len(assets):
        raise TypeError("duplicate asset names")
    missing = required_asset_names(tag) - by_name.keys()
    if missing:
        raise Mismatch(f"Release {tag} is missing assets: {', '.join(sorted(missing))}.")
    for name in required_asset_names(tag):
        asset = by_name[name]
        if not isinstance(asset["size"], int) or isinstance(asset["size"], bool):
            raise TypeError(f"invalid asset size for {name}")
        if asset["state"] != "uploaded" or asset["size"] <= 0:
            raise Mismatch(f"Release asset {name} is incomplete or empty.")
    return by_name


def validate_metadata(version, current, tagged, release, latest):
    tag = f"v{version}"
    changes = validate_changelogs(version, current, tagged)
    try:
        if not all(isinstance(release[key], bool) for key in ("draft", "prerelease")):
            raise TypeError("invalid release state")
        if not all(isinstance(release[key], str) for key in ("tag_name", "body")):
            raise TypeError("invalid release identity or notes")
        if release["tag_name"] != tag:
            raise Mismatch(f"Release identity does not match {tag}.")
        if release["draft"] or release["prerelease"] or not release["published_at"]:
            raise Mismatch(f"Release {tag} is not a published stable release.")
        if latest["tag_name"] != tag:
            raise Mismatch(f"Latest published release is not {tag}.")
        if changes not in release["body"]:
            raise Mismatch(f"Release notes for {tag} differ from CHANGELOG.md.")
        return validate_release_assets(tag, release["assets"])
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


def check_publication():
    main_sha, version, current, tagged = publication_snapshot()
    tag = f"v{version}"
    latest = latest_release(tag)
    release = api_release(f"tags/{tag}")
    assets = validate_metadata(version, current, tagged, release, latest)
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
                mismatches.append(f"Public download {name} differs from its release asset metadata.")
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
    except Mismatch as error:
        status, detail, code = "failed", f"{error} Review release evidence before repair.", 3
    except Unavailable as error:
        status, detail, code = "unknown", f"{error} Restore access and rerun the check.", 2
    detail = " ".join(str(detail).split())[:1000]
    print(f"{status.upper()}: {detail}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            print(f"status={status}\ndetail={detail}", file=output)
    return code


if __name__ == "__main__":
    sys.exit(main())
