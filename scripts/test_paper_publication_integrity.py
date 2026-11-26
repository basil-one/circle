import json
import subprocess
import unittest
from unittest.mock import patch

import paper_publication_integrity as publication


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
    def check(self, data=None, stable=b"pdf", versioned=b"pdf"):
        data = release() if data is None else data

        def fetch(url, _limit):
            if url.endswith("/tags/v0.2.2"):
                return json.dumps(data).encode()
            if url.endswith("/releases/latest"):
                return json.dumps({"tag_name": TAG}).encode()
            if url.endswith("/latest/download/circle3-paper.pdf"):
                return stable
            if url.endswith(f"/download/{TAG}/{VERSIONED}"):
                return versioned
            raise AssertionError(url)

        completed = subprocess.CompletedProcess(["git"], 0, CHANGELOG, "")
        with (
            patch.object(publication, "git_tags", return_value=["v0.2.1", TAG]),
            patch.object(publication.Path, "read_text", return_value=CHANGELOG),
            patch.object(publication.subprocess, "run", return_value=completed),
            patch.object(publication, "fetch", side_effect=fetch),
            patch.object(publication, "validate_pdf"),
        ):
            return publication.check_publication()

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

    def test_version_tag_changelog_and_notes_consistency(self):
        data = release()
        for field, value in (("tag_name", "v0.2.1"), ("name", "wrong"), ("body", "wrong")):
            altered = dict(data, **{field: value})
            with self.subTest(field=field), self.assertRaises(publication.Mismatch):
                self.check(altered)
        with self.assertRaises(publication.Mismatch):
            publication.validate_metadata("0.2.2", CHANGELOG, CHANGELOG.replace("A change", "other"), data, data)
        with self.assertRaises(publication.Mismatch):
            publication.validate_metadata("0.2.2", CHANGELOG, CHANGELOG, data, {"tag_name": "v0.2.1"})

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

    def test_empty_and_unreadable_pdf_fail_but_missing_tool_is_unknown(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            with self.assertRaises(publication.Mismatch):
                publication.validate_pdf(b"", STABLE, Path(directory))
            with patch.object(publication.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
                with self.assertRaises(publication.Mismatch):
                    publication.validate_pdf(b"bad", STABLE, Path(directory))
            with patch.object(publication.subprocess, "run", side_effect=FileNotFoundError("pdfinfo")):
                with self.assertRaises(publication.Unavailable):
                    publication.validate_pdf(b"pdf", STABLE, Path(directory))


if __name__ == "__main__":
    unittest.main()
