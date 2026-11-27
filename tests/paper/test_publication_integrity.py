import json
import subprocess
import unittest
import urllib.error
from unittest.mock import patch

from scripts.paper import publication_integrity as publication
from scripts.paper.publish import CHECKS

CHANGELOG = """# Changelog

## [Unreleased]

## [0.2.2] - 2026-11-25

### Changed
- A change.

## [0.2.1] - 2026-11-24
- Earlier.
"""
TAG = "v0.2.2"
STABLE = "circle3-paper.pdf"
VERSIONED = f"circle3-paper-{TAG}.pdf"
MAIN_SHA = "a" * 40


def release():
    return {
        "tag_name": TAG,
        "name": TAG,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-11-25T00:00:00Z",
        "body": "## What changed\n\n### Changed\n- A change.\n\n## Includes",
        "assets": [
            {"name": name, "state": "uploaded", "size": 3, "digest": None}
            for name in (STABLE, VERSIONED)
        ],
    }


class PublicationIntegrityTests(unittest.TestCase):
    def check(self, data=None, stable=b"pdf", versioned=b"pdf", pdf_error=None):
        data = release() if data is None else data

        def fetch(url, _limit):
            if "/pulls?" in url:
                return b"[]"
            if url.endswith("/tags/v0.2.2"):
                return json.dumps(data).encode()
            if url.endswith("/releases/latest"):
                return json.dumps({"tag_name": TAG}).encode()
            if url.endswith("/latest/download/circle3-paper.pdf"):
                result = stable
            elif url.endswith(f"/download/{TAG}/{VERSIONED}"):
                result = versioned
            else:
                raise AssertionError(url)
            if isinstance(result, Exception):
                raise result
            return result

        def git(command, **_kwargs):
            output = MAIN_SHA if command[1] == "rev-parse" else CHANGELOG
            return subprocess.CompletedProcess(command, 0, output, "")

        with (
            patch.object(publication, "git_tags", return_value=["v0.2.1", TAG]),
            patch.object(publication.subprocess, "run", side_effect=git) as run,
            patch.object(publication, "fetch", side_effect=fetch),
            patch.object(publication, "validate_pdf", side_effect=pdf_error),
        ):
            result = publication.check_publication()
            self.assertIn(
                ["git", "show", f"{MAIN_SHA}:CHANGELOG.md"], [c.args[0] for c in run.call_args_list]
            )
            return result

    def test_valid_release(self):
        self.assertIn("agree", self.check())

    def test_missing_draft_prerelease_and_incomplete_assets_fail(self):
        for mutation in ("missing", "draft", "prerelease", "empty", "uploading"):
            data = release()
            if mutation == "missing":
                data["assets"].pop()
            elif mutation in ("draft", "prerelease"):
                data[mutation] = True
            elif mutation == "empty":
                data["assets"][0]["size"] = 0
            else:
                data["assets"][0]["state"] = "new"
            with self.subTest(mutation=mutation), self.assertRaises(publication.Mismatch):
                self.check(data)

    def test_downloads_and_metadata_must_match(self):
        with self.assertRaisesRegex(publication.Mismatch, "differ"):
            self.check(stable=b"pdf", versioned=b"bad")
        data = release()
        data["assets"][0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(publication.Mismatch, "metadata"):
            self.check(data)

    def test_available_download_defects_survive_other_download_unavailability(self):
        for defective in (STABLE, VERSIONED):
            for kind in ("empty", "unreadable", "metadata"):
                with self.subTest(defective=defective, kind=kind):
                    other = VERSIONED if defective == STABLE else STABLE
                    values = {defective: b"bad", other: publication.Unavailable(f"{other} offline")}
                    if kind == "empty":
                        values[defective] = b""

                    def validate(data, name, _directory):
                        if kind == "empty" and not data:
                            raise publication.Mismatch(f"Downloaded {name} is empty.")
                        if kind == "unreadable" and name == defective:
                            raise publication.Mismatch(f"Downloaded {name} is not a readable PDF.")

                    data = release()
                    if kind == "metadata":
                        next(asset for asset in data["assets"] if asset["name"] == defective)[
                            "digest"
                        ] = "sha256:" + "0" * 64
                    with self.assertRaises(publication.Mismatch) as caught:
                        self.check(data, values[STABLE], values[VERSIONED], validate)
                    self.assertIn(defective, str(caught.exception))
                    self.assertIn(f"{other} offline", str(caught.exception))
                    self.assertIn(
                        {
                            "empty": "empty",
                            "unreadable": "not a readable PDF",
                            "metadata": "metadata",
                        }[kind],
                        str(caught.exception),
                    )

    def test_unavailable_download_without_defect_remains_unknown(self):
        for missing in (STABLE, VERSIONED):
            values = {STABLE: b"pdf", VERSIONED: b"pdf"}
            values[missing] = publication.Unavailable(f"{missing} offline")
            with (
                self.subTest(missing=missing),
                self.assertRaisesRegex(publication.Unavailable, f"{missing} offline"),
            ):
                self.check(stable=values[STABLE], versioned=values[VERSIONED])

    def test_metadata_mismatch_survives_unavailable_pdf_inspection(self):
        data = release()
        data["assets"][0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(publication.Mismatch) as caught:
            self.check(data, pdf_error=publication.Unavailable("pdfinfo unavailable"))
        self.assertIn(f"{STABLE} differs from its release asset metadata", str(caught.exception))
        self.assertIn("pdfinfo unavailable", str(caught.exception))

    def test_confirmed_missing_download_survives_other_unavailable_download(self):
        with self.assertRaises(publication.Mismatch) as caught:
            self.check(
                stable=publication.Mismatch(f"{STABLE} missing"),
                versioned=publication.Unavailable(f"{VERSIONED} offline"),
            )
        self.assertIn(f"{STABLE} missing", str(caught.exception))
        self.assertIn(f"{VERSIONED} offline", str(caught.exception))

    def test_version_tag_changelog_and_notes_consistency(self):
        data = release()
        for field, value in (("tag_name", "v0.2.1"), ("body", "wrong")):
            altered = dict(data, **{field: value})
            with self.subTest(field=field), self.assertRaises(publication.Mismatch):
                self.check(altered)
        self.assertIn("agree", self.check(dict(data, name="Paper release")))
        with self.assertRaises(publication.Mismatch):
            publication.validate_metadata(
                "0.2.2", CHANGELOG, CHANGELOG.replace("A change", "other"), data, data
            )
        with self.assertRaises(publication.Mismatch):
            publication.validate_metadata(
                "0.2.2", CHANGELOG, CHANGELOG, data, {"tag_name": "v0.2.1"}
            )

    def test_inaccessible_and_malformed_api_are_unknown(self):
        with patch.object(publication, "fetch", side_effect=publication.Unavailable("network")):
            with self.assertRaises(publication.Unavailable):
                publication.api_release("latest")
        with patch.object(publication, "fetch", return_value=b"not json"):
            with self.assertRaises(publication.Unavailable):
                publication.api_release("latest")
        data = release()
        del data["assets"]
        with self.assertRaises(publication.Unavailable):
            self.check(data)
        data = release()
        data["draft"] = None
        with self.assertRaises(publication.Unavailable):
            self.check(data)

    def test_latest_release_older_than_tag_is_incomplete(self):
        completed = subprocess.CompletedProcess(["git"], 0, CHANGELOG, "")
        with (
            patch.object(publication, "git_tags", return_value=[TAG]),
            patch.object(publication.Path, "read_text", return_value=CHANGELOG),
            patch.object(publication.subprocess, "run", return_value=completed),
            patch.object(publication, "api_release", return_value={"tag_name": "v0.2.1"}) as api,
        ):
            with self.assertRaisesRegex(publication.Mismatch, "incomplete"):
                publication.check_publication()
            self.assertEqual(api.call_args_list, [unittest.mock.call("tags/v0.2.2"), unittest.mock.call("latest")])

    def test_exit_codes_reserve_mismatch_from_unexpected_failure(self):
        with patch.object(
            publication, "check_publication", side_effect=publication.Mismatch("mismatch")
        ):
            self.assertEqual(publication.main(), 3)
        with patch.object(
            publication, "check_publication", side_effect=publication.Unavailable("offline")
        ):
            self.assertEqual(publication.main(), 2)
        with patch.object(
            publication, "check_publication", side_effect=publication.Pending("release is waiting")
        ):
            self.assertEqual(publication.main(), 4)

    def test_exact_merged_release_pr_distinguishes_pending_from_unexplained_mismatch(self):
        baseline = (
            CHANGELOG.replace(
                "## [Unreleased]\n\n",
                "## [Unreleased]\n\n### Added\n- A reviewed change.\n\n",
            )
            + "\n[Unreleased]: https://github.com/basil-one/circle/compare/v0.2.2...HEAD\n"
            + "[0.2.2]: https://github.com/basil-one/circle/compare/v0.2.1...v0.2.2\n"
        )
        promoted = publication.promote_changelog(
            "0.2.3", "2026-11-27", baseline, ["v0.2.2"]
        )
        snapshot = (MAIN_SHA, "0.2.2", promoted, CHANGELOG)
        with (
            patch.object(publication, "publication_snapshot", return_value=snapshot),
            patch.object(
                publication, "pending_release_pr", return_value={"number": 42, "merge_sha": "d" * 40}
            ) as pending_pr,
            patch.object(
                publication, "check_published_release", return_value="v0.2.2 is intact."
            ) as old_release,
        ):
            with self.assertRaisesRegex(publication.Pending, "PR #42"):
                publication.check_publication()
        pending_pr.assert_called_once_with("0.2.3", MAIN_SHA, promoted)
        self.assertTrue(old_release.call_args.kwargs["allow_newer_current"])

        with (
            patch.object(publication, "publication_snapshot", return_value=snapshot),
            patch.object(publication, "pending_release_pr", return_value=None),
            self.assertRaisesRegex(publication.Mismatch, "no exact merged release PR"),
        ):
            publication.check_publication()

    def test_pending_release_pr_requires_exact_identity_files_ancestry_and_promotion(self):
        baseline = (
            CHANGELOG.replace(
                "## [Unreleased]\n\n",
                "## [Unreleased]\n\n### Added\n- A reviewed change.\n\n",
            )
            + "\n[Unreleased]: https://github.com/basil-one/circle/compare/v0.2.2...HEAD\n"
            + "[0.2.2]: https://github.com/basil-one/circle/compare/v0.2.1...v0.2.2\n"
        )
        promoted = publication.promote_changelog(
            "0.2.3", "2026-11-27", baseline, ["v0.2.2"]
        )
        pr = {
            "number": 42,
            "title": "Prepare Circle3 paper v0.2.3",
            "state": "closed",
            "merged": True,
            "merge_commit_sha": "d" * 40,
            "head": {
                "ref": "paper-release/v0.2.3",
                "sha": "e" * 40,
                "repo": {"full_name": "basil-one/circle"},
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": "basil-one/circle"},
            },
        }
        with (
            patch.object(
                publication,
                "fetch",
                side_effect=[
                    json.dumps([pr]).encode(),
                    json.dumps([{"filename": "CHANGELOG.md"}]).encode(),
                    json.dumps(
                        {
                            "check_runs": [
                                {
                                    "name": name,
                                    "head_sha": "e" * 40,
                                    "status": "completed",
                                    "conclusion": "success",
                                }
                                for name in CHECKS
                            ]
                        }
                    ).encode(),
                ],
            ),
            patch.object(
                publication,
                "verify_exact_promotion",
            ) as verify,
            patch.object(
                publication.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, b"", b""),
                    subprocess.CompletedProcess([], 0, promoted, ""),
                ],
            ),
        ):
            evidence = publication.pending_release_pr("0.2.3", MAIN_SHA, promoted)
        self.assertEqual(evidence["number"], 42)
        verify.assert_called_once_with("0.2.3", "d" * 40, promoted)

    def test_draft_asset_absence_is_pending_only_when_explicitly_allowed(self):
        data = release()
        data["draft"] = True
        data["assets"].pop()
        with self.assertRaisesRegex(publication.Mismatch, "not a published"):
            publication.validate_metadata("0.2.2", CHANGELOG, CHANGELOG, data, data)
        with self.assertRaisesRegex(publication.Pending, "awaiting assets"):
            publication.validate_metadata(
                "0.2.2",
                CHANGELOG,
                CHANGELOG,
                data,
                {"tag_name": "v0.2.1"},
                allow_draft=True,
                allow_pending_assets=True,
            )

    def test_public_download_404_is_distinct_from_redirect_or_denial(self):
        url = f"{publication.DOWNLOAD}/latest/download/{STABLE}"
        for error_url, exception in (
            (url, publication.Mismatch),
            ("https://cdn.example/expired", publication.Unavailable),
        ):
            cause = urllib.error.HTTPError(error_url, 404, "Not Found", {}, None)
            error = publication.NotFound("404")
            error.__cause__ = cause
            with (
                self.subTest(error_url=error_url),
                patch.object(publication, "fetch", side_effect=error),
            ):
                with self.assertRaises(exception):
                    publication.public_pdf(url, STABLE)
        with patch.object(publication, "fetch", side_effect=publication.Unavailable("403")):
            with self.assertRaises(publication.Unavailable):
                publication.public_pdf(url, STABLE)

    def test_missing_latest_release_requires_confirmed_public_repository(self):
        completed = subprocess.CompletedProcess(["git"], 0, CHANGELOG, "")
        for repository, outcome in (
            ({"private": False, "full_name": "basil-one/circle"}, publication.Mismatch),
            ({"private": True, "full_name": "basil-one/circle"}, publication.Unavailable),
        ):
            with (
                self.subTest(repository=repository),
                patch.object(publication, "git_tags", return_value=[TAG]),
                patch.object(publication.subprocess, "run", return_value=completed),
                patch.object(publication, "api_release", side_effect=publication.NotFound("404")),
                patch.object(
                    publication,
                    "fetch",
                    side_effect=lambda url, _limit: (
                        b"[]" if "/pulls?" in url else json.dumps(repository).encode()
                    ),
                ),
            ):
                with self.assertRaises(outcome):
                    publication.check_publication()

    def test_empty_and_unreadable_pdf_fail_but_missing_tool_is_unknown(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            with self.assertRaises(publication.Mismatch):
                publication.validate_pdf(b"", STABLE, Path(directory))
            with patch.object(
                publication.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)
            ):
                with self.assertRaises(publication.Mismatch):
                    publication.validate_pdf(b"bad", STABLE, Path(directory))
            with patch.object(
                publication.subprocess, "run", side_effect=FileNotFoundError("pdfinfo")
            ):
                with self.assertRaises(publication.Unavailable):
                    publication.validate_pdf(b"pdf", STABLE, Path(directory))


if __name__ == "__main__":
    unittest.main()
