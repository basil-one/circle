import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_release import (
    ReleaseError,
    parse_version,
    promote_changelog,
    publish_release,
    render_release_notes,
    validate_new_release,
    validate_resume,
)


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


class PaperReleaseTests(unittest.TestCase):
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
        self.assertIn("## [Unreleased]\n\n## [0.2.1] - 2026-11-24\n\n### Added\n- A reviewed change.", promoted)
        self.assertIn(
            "[Unreleased]: https://github.com/basil-one/circle/compare/v0.2.1...HEAD\n"
            "[0.2.1]: https://github.com/basil-one/circle/compare/v0.2.0...v0.2.1",
            promoted,
        )
        self.assertIn("## [0.2.0] - 2026-11-23", promoted)

    def test_resume_requires_tagged_latest_version_and_empty_unreleased(self):
        promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
        validate_resume("0.2.1", promoted, ["v0.2.0", "v0.2.1"])
        with self.assertRaises(ReleaseError):
            validate_resume("0.2.1", promoted, ["v0.2.0"])
        with self.assertRaises(ReleaseError):
            validate_resume("0.2.1", promoted, ["v0.2.0", "v0.2.1", "v0.2.2"])

    def test_release_notes_use_curated_entry_and_precise_license_scope(self):
        promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
        notes = render_release_notes("0.2.1", promoted)
        self.assertIn("- A reviewed change.", notes)
        self.assertIn("Third-party materials retain their own rights", notes)
        self.assertNotIn("written content and patterns in this repository are licensed", notes)

    def test_release_repair_commands_are_retryable_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "circle3-paper.pdf"
            pdf.write_bytes(b"preview PDF")
            view = subprocess.CompletedProcess(
                ["gh"], 0, json.dumps({"tagName": "v0.2.1"}), ""
            )
            success = subprocess.CompletedProcess(["gh"], 0, "", "")
            promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
            with patch("paper_release.run_gh", side_effect=[view, success, success]) as run:
                publish_release("0.2.1", pdf, promoted, ["v0.2.1"])

            self.assertEqual(run.call_args_list[1].args[0][:3], [
                "release",
                "upload",
                "v0.2.1",
            ])
            self.assertIn("--clobber", run.call_args_list[1].args[0])
            self.assertIn("--draft=false", run.call_args_list[2].args[0])
            self.assertTrue((Path(directory) / "circle3-paper-v0.2.1.pdf").exists())


if __name__ == "__main__":
    unittest.main()
