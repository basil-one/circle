"""Validate paper release versions, promote changelog entries, and publish assets."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path

VERSION_PATTERN = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z", re.ASCII)
RELEASE_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\](?: - .*)?$", re.MULTILINE)
TAG_PATTERN = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$", re.ASCII)
UNRELEASED_HEADING = "## [Unreleased]"
REPOSITORY = "basil-one/circle"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}"
RELEASES = f"repos/{REPOSITORY}/releases"
MAX_BYTES = 50_000_000


class ReleaseError(ValueError):
    """Raised when a requested release is unsafe or cannot be prepared."""


def parse_version(version: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(version)
    if not match:
        raise ReleaseError(f"Invalid version {version!r}; expected X.Y.Z without a v prefix.")
    return tuple(int(part) for part in match.groups())


def changelog_versions(changelog: str) -> list[str]:
    return RELEASE_HEADING.findall(changelog)


def stable_tag_versions(tags: list[str]) -> list[str]:
    versions = []
    for tag in tags:
        match = TAG_PATTERN.fullmatch(tag)
        if match:
            versions.append(".".join(match.groups()))
    return versions


def unreleased_bounds(changelog: str) -> tuple[int, int]:
    lines = changelog.splitlines(keepends=True)
    headings = [
        index for index, line in enumerate(lines) if line.rstrip("\r\n") == UNRELEASED_HEADING
    ]
    if len(headings) != 1:
        raise ReleaseError("CHANGELOG.md must contain exactly one '## [Unreleased]' heading.")
    start = headings[0] + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## [")),
        len(lines),
    )
    return start, end


def validate_new_release(version: str, changelog: str, tags: list[str]) -> str:
    requested = parse_version(version)
    versions = changelog_versions(changelog)
    if version in versions:
        raise ReleaseError(f"Version {version} already has a changelog entry.")
    if f"v{version}" in tags:
        raise ReleaseError(f"Tag v{version} already exists.")

    start, end = unreleased_bounds(changelog)
    lines = changelog.splitlines()
    if not any(re.match(r"^\s*[-*]\s+\S", line) for line in lines[start:end]):
        raise ReleaseError("The Unreleased section has no entries to publish.")

    prior_changelog = [parse_version(item) for item in versions]
    prior_tags = [parse_version(item) for item in stable_tag_versions(tags)]
    if prior_changelog and prior_tags and max(prior_changelog) != max(prior_tags):
        raise ReleaseError(
            "The latest stable changelog version and tag differ; reconcile them before publishing."
        )
    prior = max(prior_changelog + prior_tags, default=None)
    if prior is not None and requested <= prior:
        raise ReleaseError(f"Version {version} must be greater than the latest stable version.")
    if not versions:
        raise ReleaseError("Cannot publish without a previous version for the comparison link.")
    return max(versions, key=parse_version)


def validate_latest_release(tags: list[str]) -> None:
    latest = latest_stable_tag(tags)
    if latest is None:
        return
    tag = f"v{latest}"
    release = get_release(tag)
    if release is None:
        raise ReleaseError(f"Latest tag {tag} has no release; resume or repair it first.")
    if release["draft"]:
        raise ReleaseError(f"Latest release {tag} is still a draft; publish it before continuing.")
    if not required_asset_names(tag).issubset(release["assets"]):
        raise ReleaseError(f"Latest release {tag} is missing PDF assets; repair it first.")


def promote_changelog(version: str, release_date: str, changelog: str, tags: list[str]) -> str:
    try:
        parsed_date = date.fromisoformat(release_date)
    except ValueError as error:
        raise ReleaseError(
            f"Invalid release date {release_date!r}; expected YYYY-MM-DD."
        ) from error
    if parsed_date.isoformat() != release_date:
        raise ReleaseError(f"Invalid release date {release_date!r}; expected YYYY-MM-DD.")
    previous = validate_new_release(version, changelog, tags)
    if re.search(rf"^\[{re.escape(version)}\]:", changelog, re.MULTILINE):
        raise ReleaseError(f"Version {version} already has a comparison link.")
    lines = changelog.splitlines(keepends=True)
    start, end = unreleased_bounds(changelog)
    body = "".join(lines[start:end]).strip()
    if not body:
        raise ReleaseError("The Unreleased section has no entries to publish.")

    heading_index = start - 1
    next_heading_index = end
    promoted = (
        "".join(lines[:heading_index])
        + f"{UNRELEASED_HEADING}\n\n"
        + f"## [{version}] - {release_date}\n\n"
        + body
        + "\n\n"
        + "".join(lines[next_heading_index:])
    )

    unreleased_url = f"{REPOSITORY_URL}/compare/v{version}...HEAD"
    previous_url = f"{REPOSITORY_URL}/compare/v{previous}...v{version}"
    unreleased_link = re.compile(r"^\[Unreleased\]: .*$", re.MULTILINE)
    if not unreleased_link.search(promoted):
        raise ReleaseError("CHANGELOG.md is missing its [Unreleased] comparison link.")
    promoted = unreleased_link.sub(f"[Unreleased]: {unreleased_url}", promoted, count=1)
    promoted = promoted.replace(
        f"[Unreleased]: {unreleased_url}\n",
        f"[Unreleased]: {unreleased_url}\n[{version}]: {previous_url}\n",
        1,
    )
    return promoted


def release_section(version: str, changelog: str) -> str:
    headings = list(RELEASE_HEADING.finditer(changelog))
    for index, heading in enumerate(headings):
        if heading.group(1) == version:
            start = heading.end()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
            content = changelog[start:end].strip()
            if not content:
                raise ReleaseError(f"Changelog entry for {version} is empty.")
            return content
    raise ReleaseError(f"CHANGELOG.md has no release entry for {version}.")


def render_release_notes(version: str, changelog: str) -> str:
    changes = release_section(version, changelog)
    return (
        "## What changed\n\n"
        f"{changes}\n\n"
        "## Includes\n\n"
        "- The Circle3™ paper PDF (attached as a release asset)\n"
        "- Three Guiding Moves™ (patterns): Establish the Circle, Integrate the Conversation, "
        "Reconcile the Intention\n"
        "- Three Leadership Lenses™ (reflective exercises): Sense-Making, Energy Awareness, "
        "Paradigm Awareness\n\n"
        '## License\n\nOriginal paper content licensed under <a href="'
        "https://creativecommons.org/licenses/by-sa/4.0/"
        '">CC BY-SA 4.0</a>. See <a href="'
        "https://github.com/basil-one/circle/blob/main/LICENSE.md"
        '">LICENSE.md</a> for repository-wide licensing.\n'
    )


def latest_stable_tag(tags: list[str]) -> str | None:
    versions = stable_tag_versions(tags)
    return max(versions, key=parse_version) if versions else None


def git_tags() -> list[str]:
    result = subprocess.run(
        ["git", "tag", "--list", "v*"], check=True, text=True, capture_output=True, timeout=30
    )
    return result.stdout.splitlines()


def required_asset_names(tag: str) -> set[str]:
    return {"circle3-paper.pdf", f"circle3-paper-{tag}.pdf"}


def run_gh(*args, stdout=subprocess.PIPE, allowed_not_found=False):
    result = subprocess.run(
        ["gh", *args], check=False, stdout=stdout, stderr=subprocess.PIPE, timeout=120
    )
    if result.returncode:
        error = result.stderr.decode(errors="replace").strip()
        if allowed_not_found and error == "release not found":
            return None
        raise ReleaseError(f"GitHub {args[0]} request failed: {error}")
    return result.stdout


def asset_record(asset):
    return {field: asset.get(field) for field in ("id", "size", "state", "digest")}


def release_record(raw, tag, release_id):
    try:
        data = json.loads(raw)
        if (
            data["id"] != release_id
            or data["tag_name"] != tag
            or type(data["draft"]) is not bool
            or data["prerelease"] is not False
            or not isinstance(data["name"], str)
            or not isinstance(data["body"], str)
            or not isinstance(data["assets"], list)
        ):
            raise ValueError("invalid release identity")
        # Retain fields that describe bytes and upload state, independent of download counts.
        assets = {asset["name"]: asset_record(asset) for asset in data["assets"]}
        if len(assets) != len(data["assets"]):
            raise ValueError("duplicate asset names")
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ReleaseError(f"Unable to verify release metadata for {tag}.") from error
    return {
        "id": release_id,
        "draft": data["draft"],
        "name": data["name"],
        "body": data["body"],
        "assets": assets,
    }


def get_release(tag):
    # gh release view can find unpublished drafts. Fetch their REST record for asset digests.
    found = run_gh(
        "release", "view", tag, "--json", "apiUrl", "--jq", ".apiUrl", allowed_not_found=True
    )
    if found is None:
        return None
    match = re.fullmatch(
        rb"https://api\.github\.com/" + RELEASES.encode() + rb"/([1-9][0-9]*)", found.strip()
    )
    if not match:
        raise ReleaseError(f"Unexpected release API identity for {tag}.")
    release_id = int(match[1])
    return release_record(run_gh("api", f"{RELEASES}/{release_id}"), tag, release_id)


def require_unpublished(tag, release):
    if release is not None and not release["draft"]:
        if missing := required_asset_names(tag) - release["assets"].keys():
            names = ", ".join(sorted(missing))
            raise ReleaseError(
                f"Published release {tag} is missing {names}; human repair is required."
            )
        raise ReleaseError(f"Release {tag} is already published. Use Paper Publication Integrity.")


def require_draft(tag, release, notes):
    if release is None:
        raise ReleaseError(f"Draft release {tag} could not be verified.")
    require_unpublished(tag, release)
    if release["name"] != tag or release["body"].strip() != notes.strip():
        raise ReleaseError(f"Draft release {tag} does not match the approved title and notes.")


def verify_asset(name, metadata, path):
    asset_id, size = metadata["id"], metadata["size"]
    if (
        type(asset_id) is not int
        or asset_id < 1
        or type(size) is not int
        or not 0 < size <= MAX_BYTES
    ):
        raise ReleaseError(f"Asset {name} has invalid identity or length.")
    if metadata["state"] != "uploaded":
        raise ReleaseError(f"Asset {name} is not uploaded.")
    with path.open("wb") as stream:
        run_gh(
            "api",
            f"{RELEASES}/assets/{asset_id}",
            "-H",
            "Accept: application/octet-stream",
            stdout=stream,
        )
    if path.stat().st_size != size:
        raise ReleaseError(f"Asset {name} differs from its release size.")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if metadata["digest"] not in (None, f"sha256:{digest}"):
        raise ReleaseError(f"Asset {name} differs from its release digest.")
    try:
        subprocess.run(["pdfinfo", str(path)], check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseError(f"Asset {name} is not a readable PDF.") from error
    return digest


def upload_asset(release_id, name, source):
    raw = run_gh(
        "api",
        f"https://uploads.github.com/{RELEASES}/{release_id}/assets?name={name}",
        "--method",
        "POST",
        "-H",
        "Content-Type: application/pdf",
        "-H",
        f"Content-Length: {source.stat().st_size}",
        "--input",
        str(source),
    )
    asset = json.loads(raw)
    if asset["name"] != name:
        raise ReleaseError(f"GitHub returned the wrong asset after uploading {name}.")
    return asset_record(asset)


def current_draft_assets(tag, notes, release_id, verified):
    current = release_record(run_gh("api", f"{RELEASES}/{release_id}"), tag, release_id)
    require_draft(tag, current, notes)
    if any(current["assets"].get(name) != asset for name, asset in verified.items()):
        raise ReleaseError(f"Draft release {tag} assets changed or are missing.")
    return current["assets"]


def fill_draft(tag, notes, release, directory, pdf):
    names = required_asset_names(tag)
    release_id = release["id"]
    verified = {name: release["assets"][name] for name in sorted(names & release["assets"].keys())}
    digests = {verify_asset(name, asset, directory / name) for name, asset in verified.items()}
    if len(digests) > 1:
        raise ReleaseError(f"Draft release {tag} contains different PDFs.")

    missing = names - verified.keys()
    if missing:
        source = directory / next(iter(verified)) if verified else pdf
        if not source or not source.is_file() or not 0 < source.stat().st_size <= MAX_BYTES:
            raise ReleaseError(f"Release {tag} requires a nonempty, bounded paper build.")
        expected = (
            next(iter(digests)) if digests else hashlib.sha256(source.read_bytes()).hexdigest()
        )
        for name in sorted(missing):
            if name in current_draft_assets(tag, notes, release_id, verified):
                raise ReleaseError(f"Draft asset {name} appeared during publication.")
            asset = upload_asset(release_id, name, source)
            if verify_asset(name, asset, directory / name) != expected:
                raise ReleaseError(f"Uploaded asset {name} differs from the approved PDF.")
            verified[name] = asset
    return verified


def publish_release(tag, notes, release, pdf=None):
    require_unpublished(tag, release)
    if release is None:
        if pdf is None or not pdf.is_file() or not 0 < pdf.stat().st_size <= MAX_BYTES:
            raise ReleaseError(f"Release {tag} requires a nonempty, bounded paper build.")
        run_gh(
            "release", "create", tag, "--draft", "--title", tag, "--notes", notes, "--verify-tag"
        )
        release = get_release(tag)
    require_draft(tag, release, notes)
    with tempfile.TemporaryDirectory() as temporary:
        assets = fill_draft(tag, notes, release, Path(temporary), pdf)
    release_id = release["id"]
    current_draft_assets(tag, notes, release_id, assets)
    result = run_gh(
        "api",
        f"{RELEASES}/{release_id}",
        "--method",
        "PATCH",
        "-F",
        "draft=false",
        "-f",
        "make_latest=true",
    )
    published = release_record(result, tag, release_id)
    if (
        published["draft"]
        or published["name"] != tag
        or published["body"].strip() != notes.strip()
        or any(published["assets"].get(name) != asset for name, asset in assets.items())
    ):
        raise ReleaseError(f"Publication of {tag} could not be verified.")
