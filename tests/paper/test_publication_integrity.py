import hashlib
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts.paper import publication_integrity as publication

MAIN = "a" * 40
PUBLISHED = "b" * 40
PENDING = "c" * 40
TAG = "v0.3.0"
NEXT_TAG = "v0.3.1"
STABLE = "circle3-paper.pdf"
VERSIONED = "circle3-paper-v0.3.0.pdf"
PDF = b"fixture PDF bytes"
CHANGELOG = "## [Unreleased]\n\n## [0.3.0] - 2026-11-27\n\n- Published change.\n"
PROMOTED = CHANGELOG.replace(
    "## [0.3.0]", "## [0.3.1] - 2026-11-28\n\n- Next change.\n\n## [0.3.0]"
)


def asset(name, data=PDF):
    return {
        "name": name,
        "size": len(data),
        "state": "uploaded",
        "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
    }


def detail(tag, number, source):
    repo = {"full_name": publication.REPOSITORY}
    return {
        "number": number,
        "title": f"Prepare Circle3 paper {tag}",
        "state": "closed",
        "merged": True,
        "merged_at": "2026-11-28T00:00:00Z",
        "merged_by": {"type": "User"},
        "merge_commit_sha": source,
        "base": {"ref": "main", "repo": repo},
        "head": {"ref": f"paper-release/{tag}", "repo": repo},
    }


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tags = [TAG]
        self.current = CHANGELOG
        self.candidate = None
        self.release = {
            "tag_name": TAG,
            "body": "## What changed\n\n- Published change.",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-11-27T00:00:00Z",
            "assets": [asset(STABLE), asset(VERSIONED)],
        }
        self.pr = detail(TAG, 59, PUBLISHED)
        self.pending_pr = detail(NEXT_TAG, 66, PENDING)
        self.downloads = {STABLE: PDF, VERSIONED: PDF}
        self.git_calls = {
            ("rev-parse", "--verify", "refs/remotes/origin/main^{commit}"): MAIN,
            ("cat-file", "-t", f"refs/tags/{TAG}"): "tag",
            ("cat-file", "-t", f"refs/tags/{NEXT_TAG}"): "tag",
            ("for-each-ref", "--format=%(contents)", f"refs/tags/{TAG}"): f"Release {TAG}",
            (
                "for-each-ref",
                "--format=%(contents)",
                f"refs/tags/{NEXT_TAG}",
            ): f"Release {NEXT_TAG}",
            ("rev-parse", f"refs/tags/{TAG}^{{commit}}"): PUBLISHED,
            ("rev-parse", f"refs/tags/{NEXT_TAG}^{{commit}}"): PENDING,
            ("show", f"{PUBLISHED}:CHANGELOG.md"): CHANGELOG,
            ("show", f"{PENDING}:CHANGELOG.md"): PROMOTED,
        }
        self.nonancestor = None
        for name, effect in (
            ("api", self.api_response),
            ("git", self.git_response),
            ("git_tags", lambda: self.tags),
            ("public_pdf", self.download),
        ):
            mock = self.enterContext(patch.object(publication, name, side_effect=effect))
            setattr(self, f"{name}_mock", mock)
        self.inspector = self.enterContext(patch.object(publication.subprocess, "run"))
        self.inspector.return_value.returncode = 0

    def api_response(self, path, expected=dict):
        if path == "releases/latest":
            return self.release
        if path == f"releases/tags/{NEXT_TAG}":
            if self.candidate is None:
                raise publication.NotFound("no candidate release")
            return self.candidate
        if path == f"pulls?state=closed&base=main&head=basil-one:paper-release/{TAG}&per_page=100":
            return [{"number": 59}]
        if (
            path
            == f"pulls?state=closed&base=main&head=basil-one:paper-release/{NEXT_TAG}&per_page=100"
        ):
            return [{"number": 66}] if self.pending_pr else []
        if path == "pulls/59":
            return self.pr
        if path == "pulls/66":
            return self.pending_pr
        raise AssertionError(f"Unexpected API call: {path}")

    def git_response(self, *args, **_options):
        code = 0
        if args == ("show", f"{MAIN}:CHANGELOG.md"):
            stdout = self.current
        elif args[:2] == ("merge-base", "--is-ancestor"):
            self.assertEqual(args[-1], MAIN)
            code = int(args[2] == self.nonancestor)
            stdout = ""
        else:
            stdout = self.git_calls[args]
        return subprocess.CompletedProcess(args, code, stdout, "")

    def download(self, url, name):
        result = self.downloads[name]
        if isinstance(result, Exception):
            raise result
        return result

    def test_published_release_uses_durable_pr_and_both_public_downloads(self):
        self.assertIn("readable public PDFs agree", publication.check_publication())
        self.assertEqual(
            [call.args[0] for call in self.public_pdf_mock.call_args_list],
            [
                f"{publication.DOWNLOAD}/latest/download/{STABLE}",
                f"{publication.DOWNLOAD}/download/{TAG}/{VERSIONED}",
            ],
        )
        self.assertEqual(self.inspector.call_count, 2)

    def test_pending_before_tag_after_tag_and_with_draft_keeps_checking_prior_publication(self):
        self.current = PROMOTED
        for tagged, draft in ((False, False), (True, False), (True, True)):
            with self.subTest(tagged=tagged, draft=draft):
                self.tags = [TAG, NEXT_TAG] if tagged else [TAG]
                self.candidate = (
                    {"tag_name": NEXT_TAG, "draft": True, "prerelease": False} if draft else None
                )
                self.public_pdf_mock.reset_mock()
                with self.assertRaisesRegex(publication.Pending, "Existing publication verified"):
                    publication.check_publication()
                self.assertEqual(self.public_pdf_mock.call_count, 2)

    def test_contradictory_pending_evidence_cannot_be_reported_as_pending(self):
        self.current = PROMOTED
        for change in (
            {"merged": False},
            {"merge_commit_sha": PUBLISHED},
            {"title": "Unrelated merge"},
        ):
            with self.subTest(change=change), self.assertRaises(publication.Mismatch):
                self.pending_pr = dict(detail(NEXT_TAG, 66, PENDING), **change)
                publication.check_publication()
        self.pending_pr = None
        with self.assertRaisesRegex(publication.Mismatch, "found 0"):
            publication.check_publication()

    def test_pending_tag_and_published_latest_cannot_disagree_with_github(self):
        self.current = PROMOTED
        self.tags = [TAG, NEXT_TAG]
        ref = f"refs/tags/{NEXT_TAG}^{{commit}}"
        self.git_calls["rev-parse", ref] = PUBLISHED
        with self.assertRaisesRegex(publication.Mismatch, "does not match merged PR"):
            publication.check_publication()
        self.git_calls["rev-parse", ref] = PENDING
        self.candidate = {"tag_name": NEXT_TAG, "draft": False}
        with self.assertRaisesRegex(publication.Mismatch, "conflicts with the latest"):
            publication.check_publication()

    def test_confirmed_prior_download_defect_precedes_pending(self):
        self.current = PROMOTED
        self.downloads[STABLE] = publication.Unavailable("temporarily unavailable")
        self.downloads[VERSIONED] = b"wrong bytes"
        with self.assertRaisesRegex(publication.Mismatch, "metadata"):
            publication.check_publication()
        self.downloads[VERSIONED] = PDF
        with self.assertRaisesRegex(publication.Unavailable, "temporarily unavailable"):
            publication.check_publication()

    def test_published_tag_source_and_ancestry_must_match(self):
        self.pr["merge_commit_sha"] = PENDING
        with self.assertRaisesRegex(publication.Mismatch, "does not match its release source"):
            publication.check_publication()
        self.pr["merge_commit_sha"] = PUBLISHED
        self.nonancestor = PUBLISHED
        with self.assertRaisesRegex(publication.Mismatch, "not an ancestor"):
            publication.check_publication()

    def test_release_notes_and_published_entry_must_agree(self):
        self.current = CHANGELOG.replace("Published change.", "Modified release entry.")
        with self.assertRaisesRegex(publication.Mismatch, "differs from the release source"):
            publication.check_publication()
        self.current = CHANGELOG
        self.release["body"] = "Unrelated release notes."
        with self.assertRaisesRegex(publication.Mismatch, "notes"):
            publication.check_publication()

    def test_unreadable_pdfs_and_unequal_bytes_fail_even_without_digests(self):
        self.inspector.return_value.returncode = 1
        with self.assertRaisesRegex(publication.Mismatch, "not a readable PDF"):
            publication.check_publication()
        self.inspector.return_value.returncode = 0
        for item in self.release["assets"]:
            item["digest"] = None
        self.downloads[VERSIONED] = b"damaged PDF bytes"
        self.assertEqual(len(self.downloads[VERSIONED]), len(PDF))
        with self.assertRaisesRegex(publication.Mismatch, "public PDFs .* differ"):
            publication.check_publication()

    def test_bad_asset_metadata_fails_closed(self):
        for change, error in (
            ({"state": "new"}, publication.Mismatch),
            ({"size": 0}, publication.Mismatch),
            ({"size": 1}, publication.Mismatch),
            ({"digest": "sha256:" + "0" * 64}, publication.Mismatch),
            ({"digest": "invalid digest"}, publication.Unavailable),
        ):
            with self.subTest(change=change), self.assertRaises(error):
                self.release["assets"] = [asset(STABLE), dict(asset(VERSIONED), **change)]
                publication.check_publication()
        self.release["assets"] = [asset(STABLE)]
        with self.assertRaisesRegex(publication.Mismatch, "missing assets"):
            publication.check_publication()

    def test_missing_pdf_inspector_does_not_pass(self):
        self.inspector.side_effect = FileNotFoundError("pdfinfo")
        with self.assertRaisesRegex(publication.Unavailable, "pdfinfo"):
            publication.check_publication()


class OutputAndTransportTests(unittest.TestCase):
    def test_exit_codes_and_workflow_outputs(self):
        for error, status, code in (
            (None, "passed", 0),
            (publication.Pending("waiting"), "pending", 4),
            (publication.Mismatch("conflict"), "failed", 3),
            (publication.Unavailable("no evidence"), "unknown", 2),
        ):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "outputs"
                with (
                    patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}),
                    patch.object(
                        publication, "check_publication", return_value="verified", side_effect=error
                    ),
                    patch("builtins.print", wraps=print),
                ):
                    self.assertEqual(publication.main(), code)
                self.assertTrue(output.read_text().startswith(f"status={status}\ndetail="))

    def test_confirmed_missing_public_download_differs_from_redirect_404(self):
        url = f"{publication.DOWNLOAD}/latest/download/{STABLE}"
        for failed, expected in (
            (url, publication.Mismatch),
            ("https://cdn.example", publication.Unavailable),
        ):
            with self.subTest(failed=failed):
                error = publication.NotFound("not found")
                error.__cause__ = urllib.error.HTTPError(failed, 404, "", None, None)
                with (
                    patch.object(publication, "fetch", side_effect=error),
                    self.assertRaises(expected),
                ):
                    publication.public_pdf(url, STABLE)

    def test_public_repo_visibility_is_needed_to_confirm_absent_latest_release(self):
        for is_private, expected in (
            (False, publication.Mismatch),
            (True, publication.Unavailable),
        ):
            data = {"full_name": publication.REPOSITORY, "private": is_private}
            with (
                self.subTest(is_private=is_private),
                patch.object(publication, "api", side_effect=[publication.NotFound(), data]),
                self.assertRaises(expected),
            ):
                publication.latest_release()


if __name__ == "__main__":
    unittest.main()
