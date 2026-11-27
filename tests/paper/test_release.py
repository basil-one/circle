import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.paper_release import (
    ReleaseError,
    parse_version,
    promote_changelog,
    publish_release,
    render_release_notes,
    validate_new_release,
    validate_resume,
    verify_asset_parity,
    verify_downloaded_assets,
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


class PaperReleaseTests(unittest.TestCase):
    @staticmethod
    def gh_result(draft, assets=(), returncode=0, stderr=""):
        stdout = json.dumps(
            {
                "tagName": "v0.2.1",
                "isDraft": draft,
                "assets": [{"name": name, "size": 11, "state": "uploaded"} for name in assets],
            }
        )
        return subprocess.CompletedProcess(["gh"], returncode, stdout, stderr)

    def test_domain_first_workflow_names_and_references(self):
        preview = (ROOT / ".github/workflows/paper-preview.yml").read_text()
        integrity = (ROOT / ".github/workflows/paper-build-integrity.yml").read_text()
        code_quality = (ROOT / ".github/workflows/common-code-quality.yml").read_text()
        publish = (ROOT / ".github/workflows/paper-publish.yml").read_text()
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
        self.assertLess(
            publish.index('cp scripts/paper_release.py "$RUNNER_TEMP/paper_release.py"'),
            publish.index('git checkout --detach "refs/tags/v$VERSION"'),
        )
        self.assertIn('python3 "$RUNNER_TEMP/paper_release.py" release', publish)
        self.assertNotIn("tags:", publish)
        self.assertNotIn("release-pdf.yml", changelog)
        self.assertIn("At the time, pushing a `v*.*.*` tag", changelog)
        self.assertFalse((ROOT / ".github/workflows/release-pdf.yml").exists())
        self.assertFalse((ROOT / ".github/workflows/publish-paper.yml").exists())

    def test_historical_build_and_release_interfaces_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            checkout = fixture / "historical-checkout"
            checkout.mkdir()
            archive = subprocess.run(
                ["git", "archive", "v0.2.2"],
                check=True,
                capture_output=True,
            ).stdout
            subprocess.run(
                ["tar", "-xf", "-", "-C", str(checkout)],
                input=archive,
                check=True,
            )
            runner_temp = fixture / "runner-temp"
            runner_temp.mkdir()
            builder = runner_temp / "build-paper-pdf.sh"
            release_tool = runner_temp / "paper_release.py"
            shutil.copyfile(ROOT / "scripts/build-paper-pdf.sh", builder)
            shutil.copyfile(ROOT / "scripts/paper_release.py", release_tool)

            subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
            subprocess.run(["git", "config", "user.name", "Fixture"], cwd=checkout, check=True)
            subprocess.run(
                ["git", "config", "user.email", "fixture@example.invalid"], cwd=checkout, check=True
            )
            subprocess.run(["git", "add", "-A"], cwd=checkout, check=True)
            subprocess.run(["git", "commit", "-qm", "Historical fixture"], cwd=checkout, check=True)
            actual_tags = subprocess.run(
                ["git", "tag", "--list", "v*"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            stable_versions = {
                tag: tuple(int(part) for part in tag[1:].split("."))
                for tag in actual_tags
                if tag.startswith("v")
                and len(tag[1:].split(".")) == 3
                and all(part.isdigit() for part in tag[1:].split("."))
            }
            stable_tags_at_v022 = [
                tag for tag, version in stable_versions.items() if version <= (0, 2, 2)
            ]
            self.assertIn("v0.2.1", stable_tags_at_v022)
            self.assertIn("v0.2.2", stable_tags_at_v022)
            self.assertEqual(
                max(tuple(int(part) for part in tag[1:].split(".")) for tag in stable_tags_at_v022),
                (0, 2, 2),
            )
            for tag in stable_tags_at_v022:
                subprocess.run(["git", "tag", tag], cwd=checkout, check=True)
            fixture_tags = subprocess.run(
                ["git", "tag", "--list", "v*"],
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(set(fixture_tags), set(stable_tags_at_v022))
            (checkout / "assets/pdfs").mkdir(parents=True, exist_ok=True)

            tools = runner_temp / "bin"
            tools.mkdir()
            for name in ("sudo", "fc-cache", "pdfinfo", "xelatex"):
                command = tools / name
                command.write_text("#!/bin/sh\nexit 0\n")
                command.chmod(0o755)
            pandoc_stub = tools / "pandoc"
            pandoc_stub.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "arguments = sys.argv[1:]\n"
                "if '-t' in arguments and arguments[arguments.index('-t') + 1] == 'latex':\n"
                "    sys.stdout.write(sys.stdin.read())\n"
                "else:\n"
                "    output = pathlib.Path(arguments[arguments.index('--output') + 1])\n"
                "    output.write_bytes(b'%PDF-1.4\\nfixture\\n')\n"
            )
            pandoc_stub.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{tools}{os.pathsep}{environment['PATH']}"
            subprocess.run(
                ["bash", str(builder)],
                cwd=checkout,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue((checkout / "assets/pdfs/circle3-paper.pdf").is_file())

            historical_release_tool = checkout / "scripts/paper_release.py"
            for tool in (historical_release_tool, release_tool):
                result = subprocess.run(
                    [sys.executable, str(tool), "validate-resume", "--version", "0.2.2"],
                    cwd=checkout,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            notes_path = runner_temp / "historical-release-notes.md"
            result = subprocess.run(
                [
                    sys.executable,
                    str(historical_release_tool),
                    "notes",
                    "--version",
                    "0.2.2",
                    "--output",
                    str(notes_path),
                ],
                cwd=checkout,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Clarified intellectual property boundaries", notes_path.read_text())
            self.assertIn("## License", notes_path.read_text())

            for tool in (historical_release_tool, release_tool):
                result = subprocess.run(
                    [sys.executable, str(tool), "validate-resume", "--version", "0.2.1"],
                    cwd=checkout,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("newer stable tag exists", result.stderr)

            gh_log = runner_temp / "gh-operations.jsonl"
            gh_stub = tools / "gh"
            gh_stub.write_text(
                "#!/usr/bin/env python3\n"
                "import hashlib, json, os, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "log = pathlib.Path(os.environ['GH_LOG'])\n"
                "with log.open('a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps(args) + '\\n')\n"
                "payload = b'fixture PDF'\n"
                "if args[:2] == ['release', 'view']:\n"
                "    count_path = pathlib.Path(os.environ['GH_VIEW_COUNT'])\n"
                "    count = int(count_path.read_text()) + 1 if count_path.exists() else 1\n"
                "    count_path.write_text(str(count))\n"
                "    names = ['circle3-paper-v0.2.2.pdf']\n"
                "    if count > 1:\n"
                "        names.append('circle3-paper.pdf')\n"
                "    assets = [{'name': name, 'size': len(payload), 'state': 'uploaded', "
                "'digest': 'sha256:' + hashlib.sha256(payload).hexdigest()} for name in names]\n"
                "    print(json.dumps({'tagName': 'v0.2.2', 'isDraft': False, 'assets': assets}))\n"
                "elif args[:2] == ['release', 'download']:\n"
                "    name = args[args.index('--pattern') + 1]\n"
                "    directory = pathlib.Path(args[args.index('--dir') + 1])\n"
                "    directory.mkdir(parents=True, exist_ok=True)\n"
                "    (directory / name).write_bytes(payload)\n"
                "elif args[:2] not in (['release', 'upload'], ['release', 'edit']):\n"
                "    raise SystemExit('unexpected release fixture operation: ' + json.dumps(args))\n"
            )
            gh_stub.chmod(0o755)
            recovery_environment = environment.copy()
            recovery_environment["GH_LOG"] = str(gh_log)
            recovery_environment["GH_VIEW_COUNT"] = str(runner_temp / "gh-view-count")
            result = subprocess.run(
                [
                    sys.executable,
                    str(release_tool),
                    "release",
                    "--version",
                    "0.2.2",
                    "--pdf",
                    str(checkout / "assets/pdfs/circle3-paper.pdf"),
                ],
                cwd=checkout,
                env=recovery_environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            operations = [json.loads(line) for line in gh_log.read_text().splitlines()]
            uploads = [operation for operation in operations if operation[:2] == ["release", "upload"]]
            self.assertEqual(len(uploads), 1)
            self.assertEqual(uploads[0][2], "v0.2.2")
            self.assertEqual(Path(uploads[0][3]).name, "circle3-paper.pdf")
            self.assertIn(
                ["release", "edit", "v0.2.2", "--latest"],
                operations,
            )

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
            with (
                patch("scripts.paper_release.run_gh", side_effect=states) as run,
                patch("scripts.paper_release.verify_asset_parity"),
            ):
                publish_release("0.2.1", pdf, promoted, ["v0.2.1"])

            self.assertIn("--draft", run.call_args_list[1].args[0])
            self.assertEqual(Path(run.call_args_list[3].args[0][3]).name, "circle3-paper.pdf")
            self.assertEqual(
                Path(run.call_args_list[4].args[0][3]).name,
                "circle3-paper-v0.2.1.pdf",
            )
            self.assertIn("--draft=false", run.call_args_list[6].args[0])
            self.assertIn("--latest", run.call_args_list[6].args[0])
            self.assertNotIn("--latest", run.call_args_list[1].args[0])
            self.assertEqual(run.call_args_list[5].args[0][1], "view")
            self.assertEqual(run.call_args_list[6].args[0][1], "edit")
            self.assertFalse(any("--clobber" in call.args[0] for call in run.call_args_list))

    def test_upload_failure_does_not_publish_draft_or_change_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "circle3-paper.pdf"
            pdf.write_bytes(b"preview PDF")
            promoted = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
            missing = subprocess.CompletedProcess(["gh"], 1, "", "release not found")
            success = subprocess.CompletedProcess(["gh"], 0, "", "")
            failure = subprocess.CompletedProcess(["gh"], 1, "", "upload failed")
            states = [missing, success, self.gh_result(True), success, failure]
            with patch("scripts.paper_release.run_gh", side_effect=states) as run:
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
            with (
                patch("scripts.paper_release.run_gh", side_effect=states) as run,
                patch(
                    "scripts.paper_release.download_release_assets",
                    return_value={"circle3-paper.pdf": pdf},
                ),
                patch("scripts.paper_release.verify_asset_parity"),
            ):
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
                "scripts.paper_release.run_gh",
                return_value=self.gh_result(
                    False, ("circle3-paper.pdf", "circle3-paper-v0.2.1.pdf")
                ),
            ) as run:
                with self.assertRaisesRegex(ReleaseError, "already published"):
                    publish_release("0.2.1", pdf, promoted, ["v0.2.1"])
            run.assert_called_once()
            self.assertFalse(versioned.exists())

    def test_published_release_without_surviving_assets_requires_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "circle3-paper.pdf"
            pdf.write_bytes(b"new build")
            with patch("scripts.paper_release.run_gh", return_value=self.gh_result(False)) as run:
                with self.assertRaisesRegex(ReleaseError, "no surviving PDF"):
                    publish_release("0.2.1", pdf, CHANGELOG, ["v0.2.1"])
            run.assert_called_once()

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
            with (
                patch("scripts.paper_release.run_gh", side_effect=states) as run,
                patch(
                    "scripts.paper_release.download_release_assets",
                    return_value={"circle3-paper.pdf": pdf},
                ),
                patch("scripts.paper_release.verify_asset_parity"),
            ):
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
            with (
                patch("scripts.paper_release.run_gh", side_effect=states) as run,
                patch(
                    "scripts.paper_release.download_release_assets",
                    return_value={"circle3-paper-v0.2.1.pdf": pdf},
                ),
                patch("scripts.paper_release.verify_asset_parity"),
            ):
                publish_release("0.2.1", pdf, promoted, ["v0.2.1"])
            self.assertEqual(run.call_args_list[1].args[0][2], "v0.2.1")
            self.assertEqual(Path(run.call_args_list[1].args[0][3]).name, "circle3-paper.pdf")
            self.assertEqual(
                run.call_args_list[3].args[0], ["release", "edit", "v0.2.1", "--latest"]
            )

    def test_asset_parity_rejects_mismatch_and_accepts_identical_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.pdf"
            second = Path(directory) / "second.pdf"
            first.write_bytes(b"released")
            second.write_bytes(b"other")
            with patch(
                "scripts.paper_release.download_release_assets",
                return_value={"a": first, "b": second},
            ):
                with self.assertRaisesRegex(ReleaseError, "differ"):
                    verify_asset_parity(
                        "v0.2.1",
                        {
                            "assetMetadata": {
                                "a": {"size": 8, "state": "uploaded"},
                                "b": {"size": 5, "state": "uploaded"},
                            }
                        },
                        Path(directory),
                    )
                second.write_bytes(b"released")
                verify_asset_parity(
                    "v0.2.1",
                    {"assetMetadata": dict.fromkeys(("a", "b"), {"size": 8, "state": "uploaded"})},
                    Path(directory),
                )

    def test_partial_recovery_uses_surviving_asset_not_rebuild(self):
        names = ("circle3-paper.pdf", "circle3-paper-v0.2.1.pdf")
        for existing in names:
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pdf = root / "circle3-paper.pdf"
                pdf.write_bytes(b"new build")
                surviving = root / "surviving.pdf"
                surviving.write_bytes(b"original release")
                initial = {
                    "isDraft": True,
                    "assetNames": {existing},
                    "assetSizes": {existing: 16},
                    "assetMetadata": {existing: {"size": 16, "state": "uploaded"}},
                }
                complete = {
                    "isDraft": True,
                    "assetNames": set(names),
                    "assetSizes": dict.fromkeys(names, 16),
                    "assetMetadata": dict.fromkeys(names, {"size": 16, "state": "uploaded"}),
                }
                published = dict(complete, isDraft=False)
                uploads = {}

                def capture(_tag, _release, assets):
                    for name, path in assets.items():
                        if name != existing:
                            uploads[name] = path.read_bytes()

                with (
                    patch(
                        "scripts.paper_release.get_release",
                        side_effect=[initial, complete, published],
                    ),
                    patch(
                        "scripts.paper_release.download_release_assets",
                        return_value={existing: surviving},
                    ),
                    patch(
                        "scripts.paper_release.upload_missing_release_assets",
                        side_effect=capture,
                    ),
                    patch("scripts.paper_release.verify_asset_parity"),
                    patch("scripts.paper_release.mark_release_published"),
                ):
                    changelog = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
                    publish_release("0.2.1", pdf, changelog, ["v0.2.1"])
                self.assertEqual(
                    uploads, {next(name for name in names if name != existing): b"original release"}
                )

    def test_mismatch_or_inaccessible_existing_assets_never_publishes(self):
        names = {"circle3-paper.pdf", "circle3-paper-v0.2.1.pdf"}
        state = {
            "isDraft": True,
            "assetNames": names,
            "assetSizes": dict.fromkeys(names, 11),
            "assetMetadata": dict.fromkeys(names, {"size": 11, "state": "uploaded"}),
        }
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "circle3-paper.pdf"
            pdf.write_bytes(b"new build")
            for failure in ("download", "parity"):
                with (
                    self.subTest(failure=failure),
                    patch("scripts.paper_release.get_release", return_value=state),
                    patch(
                        "scripts.paper_release.download_release_assets",
                        side_effect=ReleaseError("unavailable") if failure == "download" else None,
                    ),
                    patch(
                        "scripts.paper_release.verify_asset_parity",
                        side_effect=ReleaseError("differ") if failure == "parity" else None,
                    ),
                    patch("scripts.paper_release.mark_release_published") as publish,
                ):
                    with self.assertRaises(ReleaseError):
                        changelog = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
                        publish_release("0.2.1", pdf, changelog, ["v0.2.1"])
                    publish.assert_not_called()

    def test_downloaded_asset_matches_size_digest_and_upload_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.pdf"
            path.write_bytes(b"released")
            metadata = {
                "size": 8,
                "state": "uploaded",
                "digest": "sha256:" + hashlib.sha256(b"released").hexdigest(),
            }
            release = {"assetMetadata": {"asset.pdf": metadata}}
            verify_downloaded_assets("v0.2.1", release, {"asset.pdf": path})
            for key, value in (("size", 9), ("digest", "sha256:" + "0" * 64), ("state", "new")):
                with self.subTest(key=key):
                    bad = {"assetMetadata": {"asset.pdf": dict(metadata, **{key: value})}}
                    with self.assertRaises(ReleaseError):
                        verify_downloaded_assets("v0.2.1", bad, {"asset.pdf": path})
            metadata["digest"] = None
            verify_downloaded_assets("v0.2.1", release, {"asset.pdf": path})

    def test_bad_survivor_digest_causes_no_upload_or_publish(self):
        name = "circle3-paper.pdf"
        state = {
            "isDraft": False,
            "assetNames": {name},
            "assetSizes": {name: 8},
            "assetMetadata": {
                name: {"size": 8, "state": "uploaded", "digest": "sha256:" + "0" * 64}
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(b"released")
            with (
                patch("scripts.paper_release.get_release", return_value=state),
                patch("scripts.paper_release.download_release_assets", return_value={name: path}),
                patch("scripts.paper_release.upload_missing_release_assets") as upload,
                patch("scripts.paper_release.mark_release_published") as publish,
            ):
                with self.assertRaisesRegex(ReleaseError, "digest"):
                    changelog = promote_changelog("0.2.1", "2026-11-24", CHANGELOG, ["v0.2.0"])
                    publish_release("0.2.1", path, changelog, ["v0.2.1"])
                upload.assert_not_called()
                publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
