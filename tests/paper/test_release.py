import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.paper import release
from scripts.paper.release import (
    ReleaseError,
    parse_version,
    promote_changelog,
    render_release_notes,
    validate_new_release,
)

ROOT = Path(__file__).resolve().parents[2]


CHANGELOG = """# Changelog

## [Unreleased]

### Added
- A reviewed change.

## [0.2.0] - 2026-11-23

### Added
- Previous release.

[Unreleased]: https://github.com/basil-one/circle/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/basil-one/circle/compare/v0.1.11...v0.2.0
"""


APPROVED_NOTES = render_release_notes(
    "0.2.1", promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
)


class PaperReleaseTests(unittest.TestCase):
    def test_domain_first_workflow_names_and_references(self):
        preview = (ROOT / ".github/workflows/paper-preview.yml").read_text()
        integrity = (ROOT / ".github/workflows/paper-build-integrity.yml").read_text()
        code_quality = (ROOT / ".github/workflows/common-code-quality.yml").read_text()
        changelog = (ROOT / "CHANGELOG.md").read_text()
        build_script = (ROOT / "scripts/build-paper-pdf.sh").read_text()
        self.assertIn('name: "Paper: Preview"', preview)
        self.assertIn('name: "Paper: Preview"', preview.split("jobs:", 1)[1])
        self.assertIn("workflow_dispatch:", preview)
        self.assertIn("run: bash scripts/build-paper-pdf.sh", preview)
        self.assertIn("name: circle3-paper-preview", preview)
        self.assertNotIn("uses: ./.github/workflows/paper-build-integrity.yml", preview)
        self.assertIn('name: "Paper: Build Integrity"', integrity)
        self.assertIn("pull_request:\n    branches: [main]", integrity)
        self.assertIn("workflow_call:", integrity)
        self.assertIn('name: "Paper: Build Integrity"', integrity.split("jobs:", 1)[1])
        self.assertNotIn("ref:", integrity)
        self.assertIn("git rev-parse HEAD", integrity)
        self.assertIn(
            "python3 -m unittest discover -s tests/paper -p 'test_config.py' -v", integrity
        )
        self.assertIn(
            "python3 -m unittest discover -s tests/paper -p 'test_release.py' -v",
            code_quality,
        )
        self.assertIn(
            "python3 -m unittest discover -s tests/paper -p 'test_publication_integrity.py' -v",
            code_quality,
        )
        self.assertIn("run: bash scripts/build-paper-pdf.sh", integrity)
        self.assertIn("rm -f assets/pdfs/circle3-paper.pdf", build_script)
        self.assertIn("python3 scripts/build-pdf.py scripts/plop.paper.config.yaml", build_script)
        self.assertIn("test -s assets/pdfs/circle3-paper.pdf", build_script)
        self.assertIn("pdfinfo -isodates assets/pdfs/circle3-paper.pdf", build_script)
        self.assertIn("poppler-utils", build_script)
        self.assertIn("name: circle3-paper-preview", integrity)
        self.assertIn("SOURCE_SHA:", integrity)
        self.assertIn("contents: read", preview)
        self.assertNotIn("release-pdf.yml", changelog)
        self.assertIn("At the time, pushing a `v*.*.*` tag", changelog)
        self.assertFalse((ROOT / ".github/workflows/release-pdf.yml").exists())
        self.assertFalse((ROOT / ".github/workflows/publish-paper.yml").exists())

    def test_accepts_canonical_semver(self):
        self.assertEqual(parse_version("0.2.1"), (0, 2, 1))

    def test_rejects_noncanonical_versions(self):
        for version in ("v0.2.1", "0.02.1", "0.2", "0.2.1-rc.1", "0.2.1\n"):
            with self.subTest(version=version), self.assertRaises(ReleaseError):
                parse_version(version)

    def test_requires_strictly_increasing_unique_version(self):
        self.assertEqual(validate_new_release("0.2.1", CHANGELOG, ["v0.2.0"]), "0.2.0")
        for version, tags in (
            ("0.2.0", ["v0.2.0"]),
            ("0.2.0", []),
            ("0.2.1", ["v0.2.1"]),
            ("0.2.1", ["v0.3.0"]),
        ):
            with self.subTest(version=version, tags=tags), self.assertRaises(ReleaseError):
                validate_new_release(version, CHANGELOG, tags)

    def test_rejects_empty_unreleased_entries(self):
        changelog = CHANGELOG.replace("- A reviewed change.\n", "")
        with self.assertRaisesRegex(ReleaseError, "no entries"):
            validate_new_release("0.2.1", changelog, ["v0.2.0"])

    def test_rejects_duplicate_comparison_link(self):
        changelog = CHANGELOG.replace(
            "[Unreleased]:",
            "[0.2.1]: https://example.test/duplicate\n[Unreleased]:",
        )
        with self.assertRaisesRegex(ReleaseError, "comparison link"):
            promote_changelog("0.2.1", "2026-11-24", changelog, ["v0.2.0"])

    def test_promotes_entries_and_updates_comparison_links(self):
        promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
        self.assertIn(
            "## [Unreleased]\n\n## [0.2.1] - 2026-11-24\n\n### Added\n- A reviewed change.",
            promoted,
        )
        self.assertIn(
            "[Unreleased]: https://github.com/basil-one/circle/compare/v0.2.1...HEAD\n"
            "[0.2.1]: https://github.com/basil-one/circle/compare/v0.2.0...v0.2.1",
            promoted,
        )
        self.assertIn("## [0.2.0] - 2026-11-23", promoted)

    def test_release_notes_use_curated_entry_and_precise_license_scope(self):
        promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
        notes = render_release_notes("0.2.1", promoted)
        self.assertIn("- A reviewed change.", notes)
        self.assertIn(
            "## License\n\nOriginal paper content licensed under "
            '<a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>. '
            'See <a href="https://github.com/basil-one/circle/blob/main/LICENSE.md">'
            "LICENSE.md</a> for repository-wide licensing.",
            notes,
        )


TAG = "v0.2.1"
STABLE, VERSIONED = sorted(release.required_asset_names(TAG), reverse=True)
PDF_BYTES = b"paper from the approved source"
RELEASE_PATH = f"{release.RELEASES}/123"


class DraftFixture:
    def __init__(self, assets=None):
        self.record = {
            "id": 123,
            "tag_name": TAG,
            "name": TAG,
            "body": APPROVED_NOTES,
            "draft": True,
            "prerelease": False,
            "assets": [],
        }
        self.contents = {}
        self.operations = []
        self.wrong_upload = False
        self.exists = assets is not None
        for name, data in (assets or {}).items():
            self.attach(name, data)

    def attach(self, name, data):
        asset_id = 10 + len(self.contents)
        self.contents[asset_id] = data
        asset = {
            "name": name,
            "id": asset_id,
            "size": len(data),
            "state": "uploaded",
            "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
        }
        self.record["assets"].append(asset)
        return json.dumps(asset).encode()

    def request(self, *args, stdout=None, **_options):
        if args[:2] == ("release", "view"):
            return f"https://api.github.com/{RELEASE_PATH}\n".encode() if self.exists else None
        if args[:2] == ("release", "create"):
            assert "--draft" in args and "--verify-tag" in args
            self.exists = True
            self.operations.append(("create", TAG))
            return b""
        if args == ("api", RELEASE_PATH):
            return json.dumps(self.record).encode()
        if args[:2] == ("api", RELEASE_PATH) and "PATCH" in args:
            assert self.record["draft"] and "draft=false" in args and "make_latest=true" in args
            self.operations.append(("publish", TAG))
            self.record["draft"] = False
            return json.dumps(self.record).encode()
        if args[1].startswith(f"{release.RELEASES}/assets/"):
            asset_id = int(args[1].split("/")[-1])
            name = next(asset["name"] for asset in self.record["assets"] if asset["id"] == asset_id)
            self.operations.append(("download", name))
            stdout.write(self.contents[asset_id])
            return None
        upload = f"https://uploads.github.com/{RELEASE_PATH}/assets?name="
        if args[1].startswith(upload):
            assert self.record["draft"] and "POST" in args
            name = args[1].removeprefix(upload)
            assert not any(item["name"] == name for item in self.record["assets"])
            self.operations.append(("upload", name))
            data = Path(args[args.index("--input") + 1]).read_bytes()
            assert f"Content-Length: {len(data)}" in args
            return self.attach(name, b"changed after upload" if self.wrong_upload else data)
        raise AssertionError(f"Unexpected GitHub operation {args}")


class AssetPublicationTests(unittest.TestCase):
    def publish(self, fixture, pdf=None):
        with (
            patch.object(release, "run_gh", side_effect=fixture.request),
            patch.object(release.subprocess, "run") as inspect,
        ):
            draft = release.get_release(TAG)
            release.publish_release(TAG, APPROVED_NOTES, draft, pdf)
        return inspect.call_count

    def test_fresh_or_empty_draft_uploads_and_verifies_both_names_before_publication(self):
        for initial in (None, {}):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as directory:
                fixture = DraftFixture(initial)
                pdf = Path(directory) / STABLE
                pdf.write_bytes(PDF_BYTES)
                self.assertEqual(self.publish(fixture, pdf), 2)
                operations = [
                    ("upload", VERSIONED),
                    ("download", VERSIONED),
                    ("upload", STABLE),
                    ("download", STABLE),
                    ("publish", TAG),
                ]
                if initial is None:
                    operations.insert(0, ("create", TAG))
                self.assertEqual(fixture.operations, operations)
                self.assertEqual(list(fixture.contents.values()), [PDF_BYTES, PDF_BYTES])

    def test_one_asset_supplies_the_missing_name_without_a_new_build(self):
        for survivor, missing in ((STABLE, VERSIONED), (VERSIONED, STABLE)):
            with self.subTest(survivor=survivor):
                fixture = DraftFixture({survivor: PDF_BYTES})
                self.assertEqual(self.publish(fixture), 2)
                self.assertEqual(
                    fixture.operations,
                    [
                        ("download", survivor),
                        ("upload", missing),
                        ("download", missing),
                        ("publish", TAG),
                    ],
                )
                self.assertEqual(list(fixture.contents.values()), [PDF_BYTES, PDF_BYTES])

    def test_complete_draft_downloads_each_asset_once_and_finishes_without_upload(self):
        fixture = DraftFixture({STABLE: PDF_BYTES, VERSIONED: PDF_BYTES})
        self.assertEqual(self.publish(fixture), 2)
        self.assertEqual(
            fixture.operations,
            [
                ("download", VERSIONED),
                ("download", STABLE),
                ("publish", TAG),
            ],
        )

    def test_conflicting_draft_evidence_stops_before_writes(self):
        for defect in ("notes", "digest", "size", "upload state", "parity"):
            with self.subTest(defect=defect):
                fixture = DraftFixture({STABLE: PDF_BYTES})
                asset = fixture.record["assets"][0]
                if defect == "notes":
                    fixture.record["body"] = "Unapproved notes"
                elif defect == "digest":
                    asset["digest"] = f"sha256:{'0' * 64}"
                elif defect == "size":
                    asset["size"] += 1
                elif defect == "upload state":
                    asset["state"] = "new"
                else:
                    fixture.attach(VERSIONED, b"conflicting PDF")
                with self.assertRaises(ReleaseError):
                    self.publish(fixture)
                self.assertTrue(all(action == "download" for action, _name in fixture.operations))

    def test_unreadable_survivor_is_never_used(self):
        fixture = DraftFixture({STABLE: PDF_BYTES})
        with (
            patch.object(release, "run_gh", side_effect=fixture.request),
            patch.object(release.subprocess, "run", side_effect=OSError("pdfinfo failed")),
            self.assertRaisesRegex(ReleaseError, "not a readable PDF"),
        ):
            release.publish_release(TAG, APPROVED_NOTES, release.get_release(TAG))
        self.assertEqual(fixture.operations, [("download", STABLE)])

    def test_nonmatching_bytes_after_upload_stop_before_publication(self):
        fixture = DraftFixture({STABLE: PDF_BYTES})
        fixture.wrong_upload = True
        with self.assertRaisesRegex(ReleaseError, "differs from the approved PDF"):
            self.publish(fixture)
        self.assertTrue(fixture.record["draft"])
        self.assertEqual(
            fixture.operations,
            [
                ("download", STABLE),
                ("upload", VERSIONED),
                ("download", VERSIONED),
            ],
        )

    def test_published_release_stops_even_when_an_asset_is_missing(self):
        fixture = DraftFixture({STABLE: PDF_BYTES})
        fixture.record["draft"] = False
        with self.assertRaisesRegex(ReleaseError, "human repair"):
            self.publish(fixture)
        self.assertFalse(fixture.operations)

    def test_draft_published_during_verification_is_never_repaired(self):
        fixture = DraftFixture({STABLE: PDF_BYTES})

        def request(*args, **kwargs):
            data = fixture.request(*args, **kwargs)
            if args[1].startswith(f"{release.RELEASES}/assets/"):
                fixture.record["draft"] = False
            return data

        with (
            patch.object(release, "run_gh", side_effect=request),
            patch.object(release.subprocess, "run"),
            self.assertRaisesRegex(ReleaseError, "human repair"),
        ):
            release.publish_release(TAG, APPROVED_NOTES, release.get_release(TAG))
        self.assertEqual(fixture.operations, [("download", STABLE)])


if __name__ == "__main__":
    unittest.main()
