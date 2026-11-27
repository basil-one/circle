import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.paper import publish

VERSION = "0.2.3"
BRANCH = "paper-release/v0.2.3"
HEAD_SHA = "a" * 40
MERGE_SHA = "b" * 40
MAIN_SHA = "c" * 40
URL = "https://github.com/basil-one/circle/pull/42"
CHANGELOG = """# Changelog

## [Unreleased]

## [0.2.3] - 2026-11-27

### Added
- A reviewed change.

## [0.2.2] - 2026-11-25
- Previous release.
"""


def pull_request(**overrides):
    result = {
        "number": 42,
        "state": "open",
        "merged": False,
        "title": "Prepare Circle3 paper v0.2.3",
        "html_url": URL,
        "head": {
            "ref": BRANCH,
            "sha": HEAD_SHA,
            "repo": {"full_name": "basil-one/circle"},
        },
        "base": {
            "ref": "main",
            "sha": MAIN_SHA,
            "repo": {"full_name": "basil-one/circle"},
        },
    }
    result.update(overrides)
    if result.get("merged") is True and "merged_at" not in overrides:
        result["merged_at"] = "2026-11-27T15:00:00Z"
    elif result.get("merged") is False and "merged_at" not in overrides:
        result["merged_at"] = None
    return result


def check_runs(conclusion="success"):
    return [
        {
            "name": name,
            "head_sha": HEAD_SHA,
            "status": "completed",
            "conclusion": conclusion,
            "started_at": "2026-11-27T15:00:00Z",
            "app": {"id": publish.EXPECTED_CHECK_APP_ID},
        }
        for name in publish.CHECKS
    ]


class PaperPublishTests(unittest.TestCase):
    def test_gate_inventory_matches_the_twelve_required_check_names(self):
        self.assertEqual(
            publish.CHECKS,
            (
                "Common: Code Quality",
                "Common: Coherence Integrity",
                "Common: Dependency Security",
                "Site: Build Integrity",
                "Site: Link Integrity",
                "Site: SEO Integrity",
                "Site: Rendering Integrity",
                "Site: Performance Integrity",
                "Paper: Build Integrity",
                "Paper: Content Integrity",
                "Paper: Link Integrity",
                "Paper: Rendering Integrity",
            ),
        )

    def test_check_evidence_requires_all_twelve_on_exact_sha(self):
        complete, detail = publish.validate_check_runs(check_runs(), HEAD_SHA)
        self.assertTrue(complete)
        self.assertIn("12 required checks passed", detail)
        complete, detail = publish.validate_check_runs(check_runs()[:-1], HEAD_SHA)
        self.assertFalse(complete)
        self.assertIn(publish.CHECKS[-1], detail)

    def test_running_failed_skipped_and_wrong_revision_checks_never_pass(self):
        runs = check_runs()
        runs[-1]["status"] = "in_progress"
        runs[-1]["conclusion"] = None
        self.assertFalse(publish.validate_check_runs(runs, HEAD_SHA)[0])
        for conclusion in (
            "failure",
            "cancelled",
            "skipped",
            "timed_out",
            "action_required",
        ):
            with (
                self.subTest(conclusion=conclusion),
                self.assertRaises(publish.PublishError),
            ):
                publish.validate_check_runs(check_runs(conclusion), HEAD_SHA)
        wrong_revision = check_runs()
        wrong_revision[0]["head_sha"] = "d" * 40
        with self.assertRaisesRegex(publish.PublishError, "different revision"):
            publish.validate_check_runs(wrong_revision, HEAD_SHA)
        wrong_app = check_runs()
        wrong_app[0]["app"]["id"] = 1
        with self.assertRaisesRegex(publish.PublishError, "expected GitHub Actions app"):
            publish.validate_check_runs(wrong_app, HEAD_SHA)
        contradictory = check_runs()
        contradictory.append({**contradictory[0], "conclusion": "failure"})
        with self.assertRaisesRegex(publish.PublishError, "contradictory duplicate"):
            publish.validate_check_runs(contradictory, HEAD_SHA)

    def test_pull_request_identity_and_diff_are_strict(self):
        valid = pull_request()
        publish.validate_pull_request(valid, 42, VERSION, BRANCH)
        for change in (
            {"number": 43},
            {"title": "Unrelated"},
            {"head": {**valid["head"], "ref": "other"}},
            {"base": {**valid["base"], "ref": "release"}},
            {"head": {**valid["head"], "repo": {"full_name": "attacker/fork"}}},
        ):
            with self.subTest(change=change), self.assertRaises(publish.PublishError):
                publish.validate_pull_request({**valid, **change}, 42, VERSION, BRANCH)
        for files in ([], [{"filename": "CHANGELOG.md"}, {"filename": "README.md"}]):
            with self.subTest(files=files), self.assertRaises(publish.PublishError):
                publish.validate_changed_files(files)
        publish.validate_changed_files([{"filename": "CHANGELOG.md"}])

    def test_prepare_reuses_merged_pr_before_tag_without_rewriting_branch(self):
        publisher = publish.Publisher()
        list_result = pull_request(state="closed", merged=True)
        list_result.pop("merged")
        with (
            patch.object(publisher, "fetch_main", return_value=MAIN_SHA),
            patch.object(publish, "git", side_effect=[MAIN_SHA]),
            patch.object(
                publish,
                "git_result",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
            patch.object(publisher, "tag_source", return_value=None),
            patch.object(
                publish,
                "gh_api",
                side_effect=[[], [list_result], pull_request(state="closed", merged=True)],
            ),
            patch.object(publisher, "files_for_pr"),
            patch.object(publisher, "verify_branch") as verify_branch,
        ):
            self.assertEqual(publisher.prepare(VERSION), ("pr", "42", URL))
            verify_branch.assert_not_called()

    def test_pull_request_list_without_merged_field_uses_detail_even_after_branch_deletion(self):
        publisher = publish.Publisher()
        list_item = pull_request(state="closed", merged=True)
        list_item.pop("merged")
        list_item.pop("merged_at")
        with patch.object(
            publish,
            "gh_api",
            side_effect=[
                [list_item],
                pull_request(state="closed", merged=True),
            ],
        ) as api:
            details = publisher.pull_requests(BRANCH)
        self.assertTrue(publish.pull_request_merged(details[0]))
        self.assertEqual(api.call_count, 2)
        self.assertEqual(api.call_args_list[1].args[0], "repos/basil-one/circle/pulls/42")

    def test_check_runs_are_paginated_and_require_complete_count(self):
        publisher = publish.Publisher()
        full_page = [check_runs()[index % len(publish.CHECKS)] for index in range(100)]
        first = {"total_count": 101, "check_runs": full_page}
        second = {"total_count": 101, "check_runs": [check_runs()[0]]}
        with patch.object(publish, "gh_api", side_effect=[first, second]) as api:
            result = publisher.check_runs(HEAD_SHA)
        self.assertEqual(len(result), 101)
        self.assertEqual(api.call_count, 2)
        with (
            patch.object(
                publish,
                "gh_api",
                side_effect=[
                    {"total_count": 13, "check_runs": check_runs()},
                    {"total_count": 13, "check_runs": []},
                ],
            ),
            self.assertRaisesRegex(publish.PublishError, "incomplete"),
        ):
            publisher.check_runs(HEAD_SHA)

    def test_prepare_resumes_an_existing_tag_before_release(self):
        publisher = publish.Publisher()
        with (
            patch.object(publisher, "fetch_main", return_value=MAIN_SHA),
            patch.object(publish, "git", return_value=MAIN_SHA),
            patch.object(publisher, "tag_source", return_value=(MERGE_SHA, "42")),
        ):
            self.assertEqual(publisher.prepare(VERSION), ("tag", "42", MERGE_SHA))

    def test_historical_release_tag_is_recovered_only_from_legacy_release_commit(self):
        publisher = publish.Publisher()
        historical_changelog = "## [0.2.2] - 2026-11-25\n"
        with (
            patch.object(
                publish,
                "git",
                side_effect=["", MERGE_SHA, "Release v0.2.2"],
            ),
            patch.object(publish, "is_ancestor", return_value=True),
            patch.object(publish, "validate_resume") as validate_resume,
            patch.object(publisher, "pull_requests", return_value=[]),
            patch.object(publish, "changelog_at", return_value=historical_changelog),
            patch.object(publish, "git_tags", return_value=["v0.2.2"]),
        ):
            self.assertEqual(
                publisher.tag_source("0.2.2", MAIN_SHA),
                (MERGE_SHA, ""),
            )
        validate_resume.assert_called_once_with("0.2.2", historical_changelog, ["v0.2.2"])

    def test_existing_tag_resumes_release_without_retagging_or_retargeting(self):
        publisher = publish.Publisher()
        historical_changelog = """# Changelog

## [Unreleased]

## [0.2.2] - 2026-11-25

### Changed
- Historical change.
"""

        def git_call(args, check=True):
            if args[:2] == ["rev-parse", "HEAD"]:
                return MERGE_SHA
            if args[:2] == ["rev-parse", "--verify"]:
                return MERGE_SHA
            if args[:2] == ["show", "-s"]:
                return "Release v0.2.2"
            return ""

        with (
            patch.object(publisher, "fetch_main", return_value=MAIN_SHA),
            patch.object(publish, "is_ancestor", return_value=True),
            patch.object(publish, "changelog_at", return_value=historical_changelog),
            patch.object(publish, "git_tags", return_value=["v0.2.1", "v0.2.2"]),
            patch.object(publish, "validate_latest_release"),
            patch.object(publish, "git", side_effect=git_call) as git,
            patch.object(publish, "validate_resume"),
            patch.object(publish.subprocess, "run") as run,
            patch.dict(os.environ, {"PAPER_RELEASE_HELPER": "/tmp/paper_release.py"}),
        ):
            publisher.finalize("0.2.2", MERGE_SHA, None)
        git.assert_any_call(["rev-parse", "--verify", "refs/tags/v0.2.2^{commit}"], check=False)
        self.assertFalse(any(call.args[0][:2] == ["tag", "-a"] for call in git.call_args_list))
        run.assert_called_once_with(
            [
                "python3",
                "/tmp/paper_release.py",
                "release",
                "--version",
                "0.2.2",
                "--pdf",
                "assets/pdfs/circle3-paper.pdf",
            ],
            check=True,
            timeout=1800,
        )

    def test_new_tag_is_created_at_exact_verified_source(self):
        publisher = publish.Publisher()

        def git_call(args, check=True):
            if args[:2] == ["rev-parse", "HEAD"]:
                return MERGE_SHA
            if args[:2] == ["rev-parse", "--verify"]:
                return ""
            return ""

        with (
            patch.object(publisher, "fetch_main", return_value=MAIN_SHA),
            patch.object(publish, "is_ancestor", return_value=True),
            patch.object(publish, "changelog_at", return_value=CHANGELOG),
            patch.object(publish, "git_tags", side_effect=[["v0.2.2"], ["v0.2.3"]]),
            patch.object(publish, "validate_latest_release"),
            patch.object(publisher, "verify_merged_pr", return_value=(MERGE_SHA, URL)),
            patch.object(publish, "git", side_effect=git_call) as git,
            patch.object(publish, "validate_resume"),
            patch.object(publish.subprocess, "run"),
            patch.dict(os.environ, {"PAPER_RELEASE_HELPER": "/tmp/paper_release.py"}),
        ):
            publisher.finalize(VERSION, MERGE_SHA, 42)
        self.assertEqual(
            git.call_args_list,
            [
                unittest.mock.call(["rev-parse", "HEAD"]),
                unittest.mock.call(
                    ["rev-parse", "--verify", f"refs/tags/v{VERSION}^{{commit}}"],
                    check=False,
                ),
                unittest.mock.call(
                    [
                        "-c",
                        "user.name=github-actions[bot]",
                        "-c",
                        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                        "tag",
                        "-a",
                        f"v{VERSION}",
                        MERGE_SHA,
                        "-m",
                        f"Release v{VERSION}",
                    ]
                ),
                unittest.mock.call(["push", "origin", f"refs/tags/v{VERSION}"]),
            ],
        )

    def test_finalize_cannot_create_a_new_tag_without_verified_release_pr(self):
        publisher = publish.Publisher()
        with (
            patch.object(publisher, "fetch_main", return_value=MAIN_SHA),
            patch.object(publish, "is_ancestor", return_value=True),
            patch.object(publish, "git", side_effect=[MERGE_SHA, ""]),
            patch.object(publish, "changelog_at", return_value=CHANGELOG),
            self.assertRaisesRegex(publish.PublishError, "requires an exact verified release PR"),
        ):
            publisher.finalize(VERSION, MERGE_SHA, None)

    def test_finalize_halts_if_main_release_entry_changed_after_approval(self):
        publisher = publish.Publisher()
        altered = CHANGELOG.replace("A reviewed change.", "A different change.")

        def git_call(args, check=True):
            if args[:2] == ["rev-parse", "HEAD"]:
                return MERGE_SHA
            if args[:2] == ["rev-parse", "--verify"]:
                return ""
            return ""

        with (
            patch.object(publisher, "fetch_main", return_value=MAIN_SHA),
            patch.object(publisher, "verify_merged_pr", return_value=(MERGE_SHA, URL)),
            patch.object(publish, "is_ancestor", return_value=True),
            patch.object(publish, "changelog_at", side_effect=[CHANGELOG, altered]),
            patch.object(publish, "git_tags", return_value=["v0.2.2"]),
            patch.object(publish, "validate_latest_release"),
            patch.object(publish, "git", side_effect=git_call) as git,
            self.assertRaisesRegex(publish.PublishError, "changed the approved release"),
        ):
            publisher.finalize(VERSION, MERGE_SHA, 42)
        self.assertFalse(any("tag" in call.args[0] for call in git.call_args_list))

    def test_competing_release_pr_stops_before_branch_creation(self):
        other = pull_request(
            number=41,
            head={
                "ref": "paper-release/v0.2.4",
                "repo": {"full_name": "basil-one/circle"},
            },
        )
        publisher = publish.Publisher()
        with (
            patch.object(publisher, "fetch_main", return_value=MAIN_SHA),
            patch.object(publish, "git", return_value=MAIN_SHA),
            patch.object(publisher, "tag_source", return_value=None),
            patch.object(publish, "gh_api", return_value=[other]),
            patch.object(publisher, "create_release_branch") as create_branch,
            self.assertRaisesRegex(publish.PublishError, "already pending"),
        ):
            publisher.prepare(VERSION)
        create_branch.assert_not_called()

    def test_competing_orphan_release_branch_stops_before_branch_creation(self):
        publisher = publish.Publisher()
        with (
            patch.object(publisher, "fetch_main", return_value=MAIN_SHA),
            patch.object(publish, "git", return_value=MAIN_SHA),
            patch.object(publisher, "tag_source", return_value=None),
            patch.object(publish, "gh_api", return_value=[]),
            patch.object(
                publish,
                "git_result",
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    f"{HEAD_SHA}\trefs/heads/paper-release/v0.2.4\n",
                    "",
                ),
            ),
            patch.object(publisher, "create_release_branch") as create_branch,
            self.assertRaisesRegex(publish.PublishError, "without a matching pending PR"),
        ):
            publisher.prepare(VERSION)
        create_branch.assert_not_called()

    def test_pull_request_permission_failure_is_actionable(self):
        publisher = publish.Publisher()

        def create_failure(path, method="GET", data=None):
            if "/pulls?state=all" in path:
                return []
            raise publish.PublishError("Resource not accessible")

        with (
            patch.object(publish, "gh_api", side_effect=create_failure),
            self.assertRaisesRegex(
                publish.PublishError, "Actions settings allow.*create pull requests"
            ),
        ):
            publisher.create_or_find_pr(VERSION, BRANCH)

    def test_wait_stops_on_closed_unmerged_pr_or_api_failure(self):
        publisher = publish.Publisher(wait_seconds=0, interval=0)
        with (
            patch.object(
                publish,
                "gh_api",
                side_effect=[
                    pull_request(state="closed", merged=False),
                    [{"filename": "CHANGELOG.md"}],
                ],
            ),
            self.assertRaisesRegex(publish.PublishError, "closed without merging"),
        ):
            publisher.verify_merged_pr(VERSION, 42, wait=True)
        with (
            patch.object(publish, "gh_api", side_effect=publish.PublishError("API denied")),
            self.assertRaisesRegex(publish.PublishError, "API denied"),
        ):
            publisher.verify_merged_pr(VERSION, 42, wait=True)

    def test_wait_reports_timeout_without_release_side_effects(self):
        publisher = publish.Publisher(wait_seconds=1, interval=1)
        with (
            patch.object(
                publish,
                "gh_api",
                side_effect=[pull_request(), [{"filename": "CHANGELOG.md"}]],
            ),
            patch.object(publish.time, "monotonic", side_effect=[0, 2]),
            patch.object(publish.time, "sleep"),
            patch.object(publish, "git") as git,
            self.assertRaisesRegex(publish.PublishError, "Timed out waiting"),
        ):
            publisher.verify_merged_pr(VERSION, 42, wait=True)
        git.assert_not_called()

    def test_cancellation_during_wait_cannot_reach_publication(self):
        publisher = publish.Publisher(wait_seconds=30, interval=1)
        with (
            patch.object(
                publish,
                "gh_api",
                side_effect=[pull_request(), [{"filename": "CHANGELOG.md"}]],
            ),
            patch.object(publish.time, "monotonic", return_value=0),
            patch.object(publish.time, "sleep", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            publisher.verify_merged_pr(VERSION, 42, wait=True)

    def test_merged_pr_needs_positive_checks_and_exact_ancestral_merge_sha(self):
        merged = pull_request(
            state="closed",
            merged=True,
            head={**pull_request()["head"], "sha": HEAD_SHA},
            merge_commit_sha=MERGE_SHA,
        )
        changelog = CHANGELOG
        responses = [
            merged,
            [{"filename": "CHANGELOG.md"}],
            {"total_count": len(publish.CHECKS), "check_runs": check_runs()},
        ]
        publisher = publish.Publisher(wait_seconds=1, interval=0)
        with (
            patch.object(publish, "gh_api", side_effect=responses),
            patch.object(
                publish,
                "git",
                side_effect=[
                    "",
                    "refs/remotes/paper-pr/42",
                    HEAD_SHA,
                    "same-tree",
                    "same-tree",
                ],
            ),
            patch.object(publisher, "verify_promoted_revision"),
            patch.object(publisher, "fetch_main", return_value=MAIN_SHA),
            patch.object(publish, "is_ancestor", return_value=True),
            patch.object(publish, "changelog_at", return_value=changelog),
        ):
            source, url = publisher.verify_merged_pr(VERSION, 42, wait=True)
        self.assertEqual(source, MERGE_SHA)
        self.assertEqual(url, URL)

    def test_missing_check_evidence_times_out_even_for_merged_pr(self):
        merged = pull_request(state="closed", merged=True, merge_commit_sha=MERGE_SHA)
        responses = [
            merged,
            [{"filename": "CHANGELOG.md"}],
            {
                "total_count": len(publish.CHECKS) - 1,
                "check_runs": check_runs()[:-1],
            },
        ]
        publisher = publish.Publisher(wait_seconds=1, interval=0)
        with (
            patch.object(publish, "gh_api", side_effect=responses),
            patch.object(publish.time, "monotonic", side_effect=[0, 2]),
            patch.object(publish.time, "sleep"),
            self.assertRaisesRegex(publish.PublishError, "Timed out waiting"),
        ):
            publisher.verify_merged_pr(VERSION, 42, wait=True)

    def test_failed_or_skipped_required_check_stops_merged_release(self):
        merged = pull_request(state="closed", merged=True, merge_commit_sha=MERGE_SHA)
        for conclusion in ("failure", "skipped"):
            with self.subTest(conclusion=conclusion):
                publisher = publish.Publisher()
                with (
                    patch.object(
                        publish,
                        "gh_api",
                        side_effect=[
                            merged,
                            [{"filename": "CHANGELOG.md"}],
                            {
                                "total_count": len(publish.CHECKS),
                                "check_runs": check_runs(conclusion),
                            },
                        ],
                    ),
                    self.assertRaisesRegex(publish.PublishError, "Required check"),
                ):
                    publisher.verify_merged_pr(VERSION, 42, wait=True)

    def test_local_fixture_validates_promotion_and_rejects_stale_or_extra_changes(self):
        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            os.chdir(temporary)
            try:
                subprocess.run(["git", "init", "-q", "-b", "main"], check=True)
                subprocess.run(["git", "config", "user.name", "Fixture"], check=True)
                subprocess.run(
                    ["git", "config", "user.email", "fixture@example.invalid"],
                    check=True,
                )
                Path("CHANGELOG.md").write_text(
                    """# Changelog

## [Unreleased]

### Added
- A reviewed change.

## [0.2.2] - 2026-11-25
- Previous release.

[Unreleased]: https://github.com/basil-one/circle/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/basil-one/circle/compare/v0.2.1...v0.2.2
""",
                    encoding="utf-8",
                )
                subprocess.run(["git", "add", "CHANGELOG.md"], check=True)
                subprocess.run(["git", "commit", "-qm", "base"], check=True)
                main_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                subprocess.run(["git", "tag", "v0.2.2"], check=True)
                subprocess.run(["git", "switch", "-qc", BRANCH], check=True)
                promoted = publish.promote_changelog(
                    VERSION,
                    "2026-11-27",
                    Path("CHANGELOG.md").read_text(encoding="utf-8"),
                    ["v0.2.2"],
                )
                Path("CHANGELOG.md").write_text(promoted, encoding="utf-8")
                subprocess.run(["git", "add", "CHANGELOG.md"], check=True)
                subprocess.run(["git", "commit", "-qm", "Prepare release"], check=True)
                publisher = publish.Publisher()
                publisher.verify_branch(VERSION, BRANCH, main_sha)
                publisher.verify_promoted_revision(BRANCH, VERSION)
                subprocess.run(["git", "switch", "-q", "main"], check=True)
                Path("unrelated.txt").write_text("main advanced", encoding="utf-8")
                subprocess.run(["git", "add", "unrelated.txt"], check=True)
                subprocess.run(["git", "commit", "-qm", "main advanced"], check=True)
                concurrent_main = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                subprocess.run(["git", "switch", "-q", BRANCH], check=True)
                with self.assertRaisesRegex(publish.PublishError, "behind main"):
                    publisher.verify_branch(VERSION, BRANCH, concurrent_main)
                Path("unexpected.txt").write_text("extra", encoding="utf-8")
                subprocess.run(["git", "add", "unexpected.txt"], check=True)
                subprocess.run(["git", "commit", "-qm", "unexpected"], check=True)
                with self.assertRaisesRegex(publish.PublishError, "beyond the expected"):
                    publisher.verify_branch(VERSION, BRANCH, main_sha)
            finally:
                os.chdir(original_directory)

    def test_branch_created_without_pr_is_reused_without_rewriting(self):
        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            os.chdir(temporary)
            try:
                subprocess.run(["git", "init", "-q", "-b", "main"], check=True)
                subprocess.run(["git", "config", "user.name", "Fixture"], check=True)
                subprocess.run(
                    ["git", "config", "user.email", "fixture@example.invalid"],
                    check=True,
                )
                Path("CHANGELOG.md").write_text(
                    """# Changelog

## [Unreleased]

### Added
- A reviewed change.

## [0.2.2] - 2026-11-25
- Previous release.

[Unreleased]: https://github.com/basil-one/circle/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/basil-one/circle/compare/v0.2.1...v0.2.2
""",
                    encoding="utf-8",
                )
                subprocess.run(["git", "add", "CHANGELOG.md"], check=True)
                subprocess.run(["git", "commit", "-qm", "base"], check=True)
                subprocess.run(["git", "tag", "v0.2.2"], check=True)
                main_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                remote = Path(temporary) / "remote.git"
                subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
                subprocess.run(["git", "remote", "add", "origin", str(remote)], check=True)
                subprocess.run(["git", "push", "-q", "origin", "main"], check=True)
                publisher = publish.Publisher()
                with patch.object(publish, "validate_latest_release"):
                    publisher.create_release_branch(VERSION, BRANCH, main_sha)
                    original_tip = subprocess.run(
                        ["git", "rev-parse", BRANCH],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    subprocess.run(["git", "switch", "-q", "main"], check=True)
                    subprocess.run(["git", "branch", "-D", BRANCH], check=True, capture_output=True)
                    publisher.create_release_branch(VERSION, BRANCH, main_sha)
                resumed_tip = subprocess.run(
                    ["git", "rev-parse", BRANCH],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                self.assertEqual(resumed_tip, original_tip)
            finally:
                os.chdir(original_directory)

    def test_merged_tree_verification_accepts_supported_methods_and_rejects_wrong_sources(self):
        original_directory = Path.cwd()
        for strategy in ("merge", "squash", "rebase"):
            with (
                self.subTest(strategy=strategy),
                tempfile.TemporaryDirectory() as temporary,
            ):
                os.chdir(temporary)
                try:
                    subprocess.run(["git", "init", "-q", "-b", "main"], check=True)
                    subprocess.run(["git", "config", "user.name", "Fixture"], check=True)
                    subprocess.run(
                        ["git", "config", "user.email", "fixture@example.invalid"],
                        check=True,
                    )
                    Path("CHANGELOG.md").write_text(
                        """# Changelog

## [Unreleased]

### Added
- A reviewed change.

## [0.2.2] - 2026-11-25
- Previous release.

[Unreleased]: https://github.com/basil-one/circle/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/basil-one/circle/compare/v0.2.1...v0.2.2
""",
                        encoding="utf-8",
                    )
                    subprocess.run(["git", "add", "CHANGELOG.md"], check=True)
                    subprocess.run(["git", "commit", "-qm", "base"], check=True)
                    subprocess.run(["git", "tag", "v0.2.2"], check=True)
                    remote = Path(temporary) / "remote.git"
                    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
                    subprocess.run(["git", "remote", "add", "origin", str(remote)], check=True)
                    subprocess.run(["git", "push", "-q", "origin", "main", "--tags"], check=True)
                    subprocess.run(["git", "switch", "-qc", "candidate"], check=True)
                    baseline = Path("CHANGELOG.md").read_text(encoding="utf-8")
                    promoted = publish.promote_changelog(
                        VERSION, "2026-11-27", baseline, ["v0.2.2"]
                    )
                    Path("CHANGELOG.md").write_text(promoted, encoding="utf-8")
                    subprocess.run(["git", "add", "CHANGELOG.md"], check=True)
                    subprocess.run(["git", "commit", "-qam", "candidate"], check=True)
                    head_sha = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    subprocess.run(
                        ["git", "push", "-q", "origin", "HEAD:refs/pull/42/head"],
                        check=True,
                    )
                    subprocess.run(["git", "switch", "-q", "main"], check=True)
                    if strategy == "merge":
                        subprocess.run(
                            ["git", "merge", "--no-ff", "-m", "merge", "candidate"],
                            check=True,
                            capture_output=True,
                        )
                    elif strategy == "squash":
                        subprocess.run(
                            ["git", "merge", "--squash", "candidate"],
                            check=True,
                            capture_output=True,
                        )
                        subprocess.run(["git", "commit", "-qm", "squash"], check=True)
                    else:
                        subprocess.run(["git", "cherry-pick", head_sha], check=True)
                    merged_sha = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    subprocess.run(["git", "push", "-q", "origin", "main"], check=True)
                    publisher = publish.Publisher()
                    publisher.verify_merged_source(VERSION, 42, head_sha, merged_sha)
                    self.assertTrue(publish.is_ancestor(merged_sha, "main"))

                    subprocess.run(["git", "switch", "-q", "candidate"], check=True)
                    Path("extra.txt").write_text("unreviewed\n", encoding="utf-8")
                    subprocess.run(["git", "add", "extra.txt"], check=True)
                    subprocess.run(
                        ["git", "commit", "-qm", "unreviewed candidate edit"], check=True
                    )
                    subprocess.run(
                        ["git", "push", "-q", "origin", "HEAD:refs/pull/42/head"],
                        check=True,
                    )
                    with self.assertRaisesRegex(publish.PublishError, "differs from checked"):
                        publisher.verify_merged_source(VERSION, 42, head_sha, merged_sha)
                finally:
                    os.chdir(original_directory)

    def test_merged_source_with_same_changelog_but_different_tree_is_rejected(self):
        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            os.chdir(temporary)
            try:
                subprocess.run(["git", "init", "-q", "-b", "main"], check=True)
                subprocess.run(["git", "config", "user.name", "Fixture"], check=True)
                subprocess.run(
                    ["git", "config", "user.email", "fixture@example.invalid"],
                    check=True,
                )
                baseline = """# Changelog

## [Unreleased]

### Added
- A reviewed change.

## [0.2.2] - 2026-11-25
- Previous release.

[Unreleased]: https://github.com/basil-one/circle/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/basil-one/circle/compare/v0.2.1...v0.2.2
"""
                Path("CHANGELOG.md").write_text(baseline, encoding="utf-8")
                subprocess.run(["git", "add", "CHANGELOG.md"], check=True)
                subprocess.run(["git", "commit", "-qm", "base"], check=True)
                subprocess.run(["git", "tag", "v0.2.2"], check=True)
                subprocess.run(["git", "switch", "-qc", "candidate"], check=True)
                promoted = publish.promote_changelog(VERSION, "2026-11-27", baseline, ["v0.2.2"])
                Path("CHANGELOG.md").write_text(promoted, encoding="utf-8")
                subprocess.run(["git", "add", "CHANGELOG.md"], check=True)
                subprocess.run(["git", "commit", "-qm", "candidate"], check=True)
                head_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
                ).stdout.strip()
                subprocess.run(["git", "switch", "-q", "main"], check=True)
                Path("extra.txt").write_text("unreviewed\n", encoding="utf-8")
                subprocess.run(["git", "add", "extra.txt"], check=True)
                subprocess.run(["git", "commit", "-qm", "unrelated main change"], check=True)
                subprocess.run(
                    ["git", "merge", "--no-ff", "-m", "merge reviewed source", "candidate"],
                    check=True,
                    capture_output=True,
                )
                merged_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
                ).stdout.strip()
                remote = Path(temporary) / "remote.git"
                subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
                subprocess.run(["git", "remote", "add", "origin", str(remote)], check=True)
                subprocess.run(["git", "push", "-q", "origin", "main", "--tags"], check=True)
                subprocess.run(
                    ["git", "push", "-q", "origin", f"{head_sha}:refs/pull/42/head"],
                    check=True,
                )
                with self.assertRaisesRegex(publish.PublishError, "merged tree differs"):
                    publish.Publisher().verify_merged_source(VERSION, 42, head_sha, merged_sha)
            finally:
                os.chdir(original_directory)

    def test_tag_is_created_with_bot_identity_in_clean_git_configuration(self):
        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            os.chdir(temporary)
            try:
                root = Path(temporary)
                home = root / "home"
                home.mkdir()
                remote = root / "remote.git"
                subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
                env = os.environ.copy()
                env["HOME"] = str(home)
                env["GIT_CONFIG_NOSYSTEM"] = "1"
                for name in (
                    "GIT_AUTHOR_NAME",
                    "GIT_AUTHOR_EMAIL",
                    "GIT_COMMITTER_NAME",
                    "GIT_COMMITTER_EMAIL",
                ):
                    env.pop(name, None)
                subprocess.run(
                    ["git", "init", "-q", "-b", "main"],
                    check=True,
                    env=env,
                )
                subprocess.run(["git", "remote", "add", "origin", str(remote)], check=True, env=env)
                baseline = """# Changelog

## [Unreleased]

### Added
- A reviewed change.

## [0.2.2] - 2026-11-25
- Previous release.

[Unreleased]: https://github.com/basil-one/circle/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/basil-one/circle/compare/v0.2.1...v0.2.2
"""
                Path("CHANGELOG.md").write_text(baseline, encoding="utf-8")
                subprocess.run(["git", "add", "CHANGELOG.md"], check=True, env=env)
                fixture_env = {
                    **env,
                    "GIT_AUTHOR_NAME": "Fixture",
                    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "Fixture",
                    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                }
                subprocess.run(["git", "commit", "-qm", "base"], check=True, env=fixture_env)
                subprocess.run(["git", "tag", "v0.2.2"], check=True, env=env)
                subprocess.run(["git", "switch", "-qc", BRANCH], check=True, env=env)
                promoted = publish.promote_changelog(VERSION, "2026-11-27", baseline, ["v0.2.2"])
                Path("CHANGELOG.md").write_text(promoted, encoding="utf-8")
                subprocess.run(["git", "add", "CHANGELOG.md"], check=True, env=env)
                subprocess.run(["git", "commit", "-qm", "promote"], check=True, env=fixture_env)
                subprocess.run(["git", "switch", "-q", "main"], check=True, env=env)
                subprocess.run(
                    ["git", "merge", "--no-ff", "-m", "verified merge", BRANCH],
                    check=True,
                    capture_output=True,
                    env=fixture_env,
                )
                source = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                ).stdout.strip()
                subprocess.run(
                    ["git", "push", "-q", "origin", "main", "--tags"], check=True, env=env
                )
                helper = root / "release_helper.py"
                helper.write_text("raise SystemExit(0)\n", encoding="utf-8")
                publisher = publish.Publisher()
                with (
                    patch.dict(os.environ, env, clear=True),
                    patch.object(publisher, "fetch_main", return_value=source),
                    patch.object(publisher, "verify_merged_pr", return_value=(source, URL)),
                    patch.object(publish, "validate_latest_release"),
                    patch.dict(
                        os.environ,
                        {"PAPER_RELEASE_HELPER": str(helper)},
                    ),
                ):
                    publisher.finalize(VERSION, source, 42)
                tagged = subprocess.run(
                    ["git", "rev-parse", "--verify", f"refs/tags/v{VERSION}^{{commit}}"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                ).stdout.strip()
                self.assertEqual(tagged, source)
                tag_object = subprocess.run(
                    ["git", "cat-file", "-p", f"refs/tags/v{VERSION}"],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                ).stdout
                self.assertIn("tagger github-actions[bot]", tag_object)
            finally:
                os.chdir(original_directory)

    def test_finalization_halts_before_tagging_unapproved_source(self):
        publisher = publish.Publisher()
        with (
            patch.object(publish, "git", return_value=MERGE_SHA) as git,
            patch.object(publisher, "fetch_main", return_value=MAIN_SHA),
            patch.object(publish, "is_ancestor", return_value=False),
            self.assertRaisesRegex(publish.PublishError, "not an ancestor"),
        ):
            publisher.finalize(VERSION, MERGE_SHA, 42)
        self.assertFalse(
            any(
                call.args[0][:2] in (["tag", "-a"], ["push", "origin"])
                for call in git.call_args_list
            )
        )

    def test_workflow_uses_one_reviewed_job_without_direct_main_push(self):
        workflow = (
            Path(__file__).resolve().parents[2] / ".github/workflows/paper-publish.yml"
        ).read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("timeout-minutes: 120", workflow)
        self.assertIn("PUBLISH_WAIT_SECONDS: 1800", workflow)
        self.assertIn("pull-requests: write", workflow)
        self.assertIn("checks: read", workflow)
        self.assertIn("group: paper-publication", workflow)
        self.assertIn("approve requested GitHub Actions runs if", workflow)
        self.assertNotIn("git push --atomic", workflow)
        self.assertNotIn("HEAD:refs/heads/main", workflow)
        job_headers = re.findall(
            r"^  [a-z][a-z0-9-]*:$", workflow.split("\njobs:\n", 1)[1], re.MULTILINE
        )
        self.assertEqual(job_headers, ["  paper-publish:"])


if __name__ == "__main__":
    unittest.main()
