#!/usr/bin/env python3
"""Validate paper release versions, promote changelog entries, and publish assets."""

from __future__ import annotations

import argparse
from datetime import date
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION_PATTERN = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
RELEASE_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\](?: - .*)?$", re.MULTILINE)
TAG_PATTERN = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
UNRELEASED_HEADING = "## [Unreleased]"
REPOSITORY_URL = "https://github.com/basil-one/circle"


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
    headings = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == UNRELEASED_HEADING]
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


def validate_resume(version: str, changelog: str, tags: list[str]) -> None:
    parse_version(version)
    if f"v{version}" not in tags:
        raise ReleaseError(f"Tag v{version} does not exist; there is nothing to resume.")
    tagged_versions = stable_tag_versions(tags)
    if not tagged_versions or max(tagged_versions, key=parse_version) != version:
        raise ReleaseError("A newer stable tag exists; this version can no longer be resumed.")
    versions = changelog_versions(changelog)
    if not versions or versions[0] != version:
        raise ReleaseError(f"Tag v{version} is not the latest changelog version.")
    start, end = unreleased_bounds(changelog)
    if any(line.strip() for line in changelog.splitlines()[start:end]):
        raise ReleaseError("The tagged version still has Unreleased entries; refusing resume.")


def validate_latest_release(tags: list[str]) -> None:
    latest = latest_stable_tag(tags)
    if latest is None:
        return
    tag = f"v{latest}"
    result = run_gh(["release", "view", tag, "--json", "assets,isDraft"])
    if result.returncode:
        raise ReleaseError(
            f"Latest tag {tag} has no accessible release; resume or repair it before publishing."
        )
    try:
        release = json.loads(result.stdout)
        names = {asset["name"] for asset in release["assets"]}
    except (KeyError, json.JSONDecodeError, TypeError) as error:
        raise ReleaseError(f"Unable to verify assets for latest release {tag}.") from error
    required = {"circle3-paper.pdf", f"circle3-paper-{tag}.pdf"}
    if not required.issubset(names):
        raise ReleaseError(
            f"Latest release {tag} is missing required PDF assets; repair it before publishing."
        )
    if release.get("isDraft", True):
        raise ReleaseError(f"Latest release {tag} is still a draft; publish it before continuing.")


def promote_changelog(version: str, release_date: str, changelog: str, tags: list[str]) -> str:
    try:
        parsed_date = date.fromisoformat(release_date)
    except ValueError as error:
        raise ReleaseError(f"Invalid release date {release_date!r}; expected YYYY-MM-DD.") from error
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
        "## License\n\n"
        "The notice applies to original content in this paper. Third-party materials retain "
        "their own rights; see LICENSE.md for repository-wide licensing.\n"
    )


def latest_stable_tag(tags: list[str]) -> str | None:
    versions = stable_tag_versions(tags)
    return max(versions, key=parse_version) if versions else None


def run_gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], check=False, text=True, capture_output=True)


def publish_release(version: str, pdf_path: Path, changelog: str, tags: list[str]) -> None:
    tag = f"v{version}"
    if latest_stable_tag(tags) != version:
        raise ReleaseError("Refusing to publish: the requested version is not the latest stable tag.")
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise ReleaseError(f"PDF not found or empty: {pdf_path}")

    versioned_pdf = pdf_path.with_name(f"circle3-paper-{tag}.pdf")
    shutil.copyfile(pdf_path, versioned_pdf)
    notes = render_release_notes(version, changelog)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md") as notes_file:
        notes_file.write(notes)
        notes_file.flush()
        existing = run_gh(["release", "view", tag, "--json", "tagName"])
        if existing.returncode:
            error = (existing.stderr + existing.stdout).lower()
            if "not found" not in error and "404" not in error:
                raise ReleaseError(f"Unable to check release {tag}: {existing.stderr.strip()}")
            result = run_gh(
                [
                    "release",
                    "create",
                    tag,
                    str(pdf_path),
                    str(versioned_pdf),
                    "--title",
                    tag,
                    "--notes-file",
                    notes_file.name,
                    "--verify-tag",
                    "--latest",
                ]
            )
        else:
            upload = run_gh(
                [
                    "release",
                    "upload",
                    tag,
                    str(pdf_path),
                    str(versioned_pdf),
                    "--clobber",
                ]
            )
            if upload.returncode:
                raise ReleaseError(
                    f"Unable to upload assets for release {tag}: {upload.stderr.strip()}"
                )
            result = run_gh(
                [
                    "release",
                    "edit",
                    tag,
                    "--title",
                    tag,
                    "--notes-file",
                    notes_file.name,
                    "--draft=false",
                    "--latest",
                ]
            )
        if result.returncode:
            raise ReleaseError(f"Unable to publish release {tag}: {result.stderr.strip()}")


def git_tags() -> list[str]:
    result = subprocess.run(
        ["git", "tag", "--list", "v*"], check=True, text=True, capture_output=True
    )
    return result.stdout.splitlines()


def command_prepare(args: argparse.Namespace) -> None:
    path = Path(args.changelog)
    source = path.read_text(encoding="utf-8")
    promoted = promote_changelog(args.version, args.date, source, git_tags())
    path.write_text(promoted, encoding="utf-8")
    print(f"Prepared changelog for v{args.version}.")


def command_validate_resume(args: argparse.Namespace) -> None:
    validate_resume(args.version, Path(args.changelog).read_text(encoding="utf-8"), git_tags())
    print(f"Validated resumable publication for v{args.version}.")


def command_validate_latest(args: argparse.Namespace) -> None:
    validate_latest_release(git_tags())


def command_release(args: argparse.Namespace) -> None:
    publish_release(
        args.version,
        Path(args.pdf),
        Path(args.changelog).read_text(encoding="utf-8"),
        git_tags(),
    )


def command_notes(args: argparse.Namespace) -> None:
    notes = render_release_notes(
        args.version, Path(args.changelog).read_text(encoding="utf-8")
    )
    Path(args.output).write_text(notes, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare")
    prepare.add_argument("--version", required=True)
    prepare.add_argument("--date", required=True)
    prepare.add_argument("--changelog", default="CHANGELOG.md")
    prepare.set_defaults(func=command_prepare)

    resume = commands.add_parser("validate-resume")
    resume.add_argument("--version", required=True)
    resume.add_argument("--changelog", default="CHANGELOG.md")
    resume.set_defaults(func=command_validate_resume)

    latest = commands.add_parser("validate-latest")
    latest.set_defaults(func=command_validate_latest)

    release = commands.add_parser("release")
    release.add_argument("--version", required=True)
    release.add_argument("--pdf", required=True)
    release.add_argument("--changelog", default="CHANGELOG.md")
    release.set_defaults(func=command_release)

    notes = commands.add_parser("notes")
    notes.add_argument("--version", required=True)
    notes.add_argument("--changelog", default="CHANGELOG.md")
    notes.add_argument("--output", required=True)
    notes.set_defaults(func=command_notes)

    args = parser.parse_args()
    try:
        args.func(args)
    except (OSError, ReleaseError, subprocess.CalledProcessError) as error:
        print(f"Paper release error: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
