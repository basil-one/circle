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

ROOT = Path(__file__).resolve().parents[1]


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
    @staticmethod
    def gh_result(draft, assets=(), returncode=0, stderr=""):
        stdout = json.dumps(
            {"tagName": "v0.2.1", "isDraft": draft, "assets": [{"name": name} for name in assets]}
        )
        return subprocess.CompletedProcess(["gh"], returncode, stdout, stderr)

    def test_domain_first_workflow_names_and_references(self):
        preview = (ROOT / ".github/workflows/paper-preview.yml").read_text()
        integrity = (ROOT / ".github/workflows/paper-build-integrity.yml").read_text()
        publish = (ROOT / ".github/workflows/paper-publish.yml").read_text()
        changelog = (ROOT / "CHANGELOG.md").read_text()
        build_script = (ROOT / "scripts/build-paper-pdf.sh").read_text()
        self.assertIn('name: "Paper: Preview"', preview)
        self.assertIn('name: "Paper: Preview"', preview.split("jobs:", 1)[1])
        self.assertIn("workflow_dispatch:", preview)
        self.assertIn("uses: ./.github/workflows/paper-build-integrity.yml", preview)
        self.assertIn('name: "Paper: Build Integrity"', integrity)
        self.assertIn("pull_request:\n    branches: [main]", integrity)
        self.assertIn("workflow_call:", integrity)
        self.assertIn('name: "Paper: Build Integrity"', integrity.split("jobs:", 1)[1])
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", integrity)
        self.assertIn("python3 -m unittest discover -s scripts -p 'test_paper_*.py' -v", integrity)
        self.assertIn("run: bash scripts/build-paper-pdf.sh", integrity)
        self.assertIn("rm -f assets/pdfs/circle3-paper.pdf", build_script)
        self.assertIn("python3 scripts/build-pdf.py scripts/plop.paper.config.yaml", build_script)
        self.assertIn("test -s assets/pdfs/circle3-paper.pdf", build_script)
        self.assertIn("pdfinfo -isodates assets/pdfs/circle3-paper.pdf", build_script)
        self.assertIn("poppler-utils", build_script)
        self.assertIn("name: circle3-paper-preview", integrity)
        self.assertIn("SOURCE_SHA:", integrity)
        self.assertIn('name: "Paper: Publish"', publish)
        self.assertIn('name: "Paper: Publish"', publish.split("jobs:", 1)[1])
        self.assertIn("contents: read", preview)
        self.assertLess(
            publish.index('cp scripts/build-paper-pdf.sh "$RUNNER_TEMP/build-paper-pdf.sh"'),
            publish.index('git checkout --detach "refs/tags/v$VERSION"'),
        )
        self.assertIn('bash "$RUNNER_TEMP/build-paper-pdf.sh"', publish)
        self.assertIn("workflow_dispatch:", publish)
        self.assertIn("contents: write", publish)
        self.assertIn("if: github.ref == 'refs/heads/main'", publish)
        self.assertIn("group: paper-publication", publish)
        self.assertIn("GH_TOKEN: ${{ github.token }}", publish)
        self.assertIn("python3 scripts/paper_release.py release", publish)
        self.assertNotIn("tags:", publish)
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
        self.assertIn(
            "## License\n\nOriginal paper content licensed under "
            '<a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>. '
            'See <a href="https://github.com/basil-one/circle/blob/main/LICENSE.md">'
            "LICENSE.md</a> for repository-wide licensing.",
            notes,
        )

    def test_new_release_stays_draft_until_both_assets_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "circle3-paper.pdf"
            pdf.write_bytes(b"preview PDF")
            promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
            missing = subprocess.CompletedProcess(["gh"], 1, "", "release not found")
            success = subprocess.CompletedProcess(["gh"], 0, "", "")
            states = [
                missing,
                success,
                self.gh_result(True),
                success,
                success,
                self.gh_result(True, ("circle3-paper.pdf", "circle3-paper-v0.2.1.pdf")),
                success,
                self.gh_result(False, ("circle3-paper.pdf", "circle3-paper-v0.2.1.pdf")),
            ]
            with patch("paper_release.run_gh", side_effect=states) as run:
                publish_release("0.2.1", pdf, promoted, ["v0.2.1"])

            self.assertIn("--draft", run.call_args_list[1].args[0])
            self.assertEqual(run.call_args_list[3].args[0][3], str(pdf))
            self.assertEqual(
                run.call_args_list[4].args[0][3],
                str(Path(directory) / "circle3-paper-v0.2.1.pdf"),
            )
            self.assertIn("--draft=false", run.call_args_list[6].args[0])
            self.assertIn("--latest", run.call_args_list[6].args[0])
            self.assertNotIn("--latest", run.call_args_list[1].args[0])
            self.assertEqual(run.call_args_list[5].args[0][1], "view")
            self.assertEqual(run.call_args_list[6].args[0][1], "edit")
            self.assertFalse(any("--clobber" in call.args[0] for call in run.call_args_list))
            self.assertTrue((Path(directory) / "circle3-paper-v0.2.1.pdf").exists())

    def test_upload_failure_does_not_publish_draft_or_change_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "circle3-paper.pdf"
            pdf.write_bytes(b"preview PDF")
            promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
            missing = subprocess.CompletedProcess(["gh"], 1, "", "release not found")
            success = subprocess.CompletedProcess(["gh"], 0, "", "")
            failure = subprocess.CompletedProcess(["gh"], 1, "", "upload failed")
            states = [missing, success, self.gh_result(True), success, failure]
            with patch("paper_release.run_gh", side_effect=states) as run:
                with self.assertRaisesRegex(ReleaseError, "missing asset"):
                    publish_release("0.2.1", pdf, promoted, ["v0.2.1"])
            self.assertFalse(any(call.args[0][1] == "edit" for call in run.call_args_list))
            self.assertFalse(any("--latest" in call.args[0] for call in run.call_args_list))

    def test_draft_recovery_uploads_only_missing_asset_then_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "circle3-paper.pdf"
            pdf.write_bytes(b"preview PDF")
            promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
            success = subprocess.CompletedProcess(["gh"], 0, "", "")
            states = [
                self.gh_result(True, ("circle3-paper.pdf",)),
                success,
                self.gh_result(True, ("circle3-paper.pdf", "circle3-paper-v0.2.1.pdf")),
                success,
                self.gh_result(False, ("circle3-paper.pdf", "circle3-paper-v0.2.1.pdf")),
            ]
            with patch("paper_release.run_gh", side_effect=states) as run:
                publish_release("0.2.1", pdf, promoted, ["v0.2.1"])
            self.assertEqual(len(run.call_args_list), 5)
            self.assertEqual(
                run.call_args_list[1].args[0][2],
                "v0.2.1",
            )
            self.assertEqual(
                Path(run.call_args_list[1].args[0][3]).name,
                "circle3-paper-v0.2.1.pdf",
            )
            self.assertNotIn("--clobber", run.call_args_list[1].args[0])

    def test_complete_published_release_is_rejected_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "circle3-paper.pdf"
            pdf.write_bytes(b"preview PDF")
            versioned = Path(directory) / "circle3-paper-v0.2.1.pdf"
            promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
            with patch(
                "paper_release.run_gh",
                return_value=self.gh_result(
                    False, ("circle3-paper.pdf", "circle3-paper-v0.2.1.pdf")
                ),
            ) as run:
                with self.assertRaisesRegex(ReleaseError, "already published"):
                    publish_release("0.2.1", pdf, promoted, ["v0.2.1"])
            run.assert_called_once()
            self.assertFalse(versioned.exists())

    def test_published_release_recovery_uploads_only_missing_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "circle3-paper.pdf"
            pdf.write_bytes(b"preview PDF")
            promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
            success = subprocess.CompletedProcess(["gh"], 0, "", "")
            states = [
                self.gh_result(False, ("circle3-paper.pdf",)),
                success,
                self.gh_result(False, ("circle3-paper.pdf", "circle3-paper-v0.2.1.pdf")),
                success,
                self.gh_result(False, ("circle3-paper.pdf", "circle3-paper-v0.2.1.pdf")),
            ]
            with patch("paper_release.run_gh", side_effect=states) as run:
                publish_release("0.2.1", pdf, promoted, ["v0.2.1"])
            self.assertEqual(run.call_args_list[1].args[0][2], "v0.2.1")
            self.assertEqual(
                Path(run.call_args_list[1].args[0][3]).name,
                "circle3-paper-v0.2.1.pdf",
            )
            self.assertEqual(
                run.call_args_list[3].args[0], ["release", "edit", "v0.2.1", "--latest"]
            )
            self.assertNotIn("--clobber", run.call_args_list[1].args[0])

    def test_published_release_recovery_restores_only_missing_stable_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "circle3-paper.pdf"
            pdf.write_bytes(b"preview PDF")
            promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
            success = subprocess.CompletedProcess(["gh"], 0, "", "")
            states = [
                self.gh_result(False, ("circle3-paper-v0.2.1.pdf",)),
                success,
                self.gh_result(False, ("circle3-paper.pdf", "circle3-paper-v0.2.1.pdf")),
                success,
                self.gh_result(False, ("circle3-paper.pdf", "circle3-paper-v0.2.1.pdf")),
            ]
            with patch("paper_release.run_gh", side_effect=states) as run:
                publish_release("0.2.1", pdf, promoted, ["v0.2.1"])
            self.assertEqual(run.call_args_list[1].args[0][2], "v0.2.1")
            self.assertEqual(run.call_args_list[1].args[0][3], str(pdf))
            self.assertEqual(
                run.call_args_list[3].args[0], ["release", "edit", "v0.2.1", "--latest"]
            )


if __name__ == "__main__":
    unittest.main()
