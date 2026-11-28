import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.orchestration import release_publish as release

VERSION = "0.3.1"
HEAD = "a" * 40
BASE_CHANGELOG = """# Changelog

## [Unreleased]

### Changed
- An approved change.

## [0.3.0] - 2026-11-27

- Prior publication.

[Unreleased]: https://github.com/basil-one/circle/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/basil-one/circle/compare/v0.2.2...v0.3.0
"""


def check_runs(head=HEAD):
    return [
        {
            "id": index,
            "name": name,
            "head_sha": head,
            "app": {"id": 15368},
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-11-28T00:00:00Z",
        }
        for index, name in enumerate(release.CHECKS, 1)
    ]


def pr_detail(publisher, head, source=None):
    repo = {"full_name": release.REPOSITORY}
    return {
        "number": 42,
        "title": publisher.title,
        "state": "closed" if source else "open",
        "merged": bool(source),
        "merged_at": "2026-11-28T00:00:00Z" if source else None,
        "merged_by": {"type": "User"} if source else None,
        "head": {"ref": publisher.branch, "repo": repo, "sha": head},
        "base": {"ref": "main", "repo": repo},
        "merge_commit_sha": source,
    }


class GateTests(unittest.TestCase):
    def test_inventory_matches_the_twelve_pr_workflows(self):
        workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows"
        names = []
        for workflow in workflows.glob("*.yml"):
            lines = workflow.read_text().splitlines()
            if "  pull_request:" in lines:
                names.append(lines[0].split(": ", 1)[1].strip('"'))
        self.assertEqual(len(release.CHECKS), 12)
        self.assertCountEqual(release.CHECKS, names)

    def test_missing_checks_never_mask_failed_checks(self):
        runs = check_runs()
        release.verify_checks(runs, HEAD)
        with self.assertRaises(release.ChecksPending):
            release.verify_checks(runs[1:], HEAD)
        for conclusion in ("failure", "skipped", "cancelled", "neutral"):
            with (
                self.subTest(conclusion=conclusion),
                self.assertRaisesRegex(release.PublishError, conclusion),
            ):
                release.verify_checks([dict(runs[-1], conclusion=conclusion)], HEAD)

    def test_later_attempt_and_exact_head_and_app(self):
        runs = check_runs()
        retry = dict(
            runs[0], id=15, status="queued", conclusion=None, started_at="2026-11-28T00:01:00Z"
        )
        with self.assertRaises(release.ChecksPending):
            release.verify_checks(runs + [retry], HEAD)
        with self.assertRaisesRegex(release.PublishError, "Ambiguous"):
            release.verify_checks(runs + [dict(runs[0], id=15)], HEAD)
        for change in ({"head_sha": "b" * 40}, {"app": {"id": 1}}):
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(release.PublishError, "wrong revision or app"),
            ):
                release.verify_checks(runs[1:] + [dict(runs[0], **change)], HEAD)

    def test_check_api_requires_complete_pages_with_distinct_run_ids(self):
        page = [{"id": number} for number in range(1, 101)]
        first = {"total_count": 101, "check_runs": page}
        valid = {"total_count": 101, "check_runs": [{"id": 101}]}
        with patch.object(release, "gh_api", side_effect=[first, valid]) as api:
            self.assertEqual(len(release.check_pages(HEAD)), 101)
            self.assertIn("filter=all", api.call_args.args[0])
        for bad in (
            dict(valid, total_count=102),
            dict(valid, check_runs=[]),
            first,
        ):
            with (
                self.subTest(count=bad["total_count"], runs=len(bad["check_runs"])),
                patch.object(release, "gh_api", side_effect=[first, bad]),
                self.assertRaises(release.PublishError),
            ):
                release.check_pages(HEAD)
        duplicate = dict(valid, check_runs=[{"id": 1}])
        with (
            patch.object(release, "gh_api", side_effect=[first, duplicate]),
            self.assertRaisesRegex(release.PublishError, "duplicate ID"),
        ):
            release.check_pages(HEAD)

    def test_dispatch_context_and_version_are_enforced(self):
        with (
            patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}, clear=True),
            patch("sys.argv", ["release_publish", "--version", VERSION]),
            self.assertRaises(SystemExit),
        ):
            release.main()
        for version in ("v0.3.1", "0.3.01", "0.3.1-rc.1", "0.3.1١"):
            with self.subTest(version=version), self.assertRaises(release.ReleaseError):
                release.Publisher(version)

    def test_human_merge_and_closed_pr_evidence(self):
        publisher = release.Publisher(VERSION)
        merged = pr_detail(publisher, HEAD, "b" * 40)
        with patch.object(release, "gh_api", return_value=merged):
            self.assertEqual(publisher.pr(42), merged)
        for change in ({"merged_by": {"type": "Bot"}}, {"merged": False}, {"title": "Other PR"}):
            with (
                self.subTest(change=change),
                patch.object(release, "gh_api", return_value=dict(merged, **change)),
                self.assertRaises(release.PublishError),
            ):
                publisher.pr(42)

    def test_waiting_for_merge_and_checks_uses_minute_polls(self):
        publisher = release.Publisher(VERSION)
        opened = pr_detail(publisher, HEAD)
        merged = pr_detail(publisher, HEAD, "b" * 40)
        with (
            patch.object(publisher, "pr", return_value=merged),
            patch.object(publisher, "fetch_main"),
            patch.object(
                publisher,
                "verify_merged_source",
                side_effect=[
                    release.ChecksPending("checks outstanding"),
                    merged["merge_commit_sha"],
                ],
            ),
            patch.object(release.time, "sleep") as sleep,
            patch("builtins.print") as log,
        ):
            self.assertEqual(publisher.await_merge(opened), (merged, merged["merge_commit_sha"]))
        self.assertEqual([call.args for call in sleep.call_args_list], [(60,), (60,)])
        self.assertTrue(any("awaiting human merge" in call.args[0] for call in log.call_args_list))
        self.assertTrue(any("checks outstanding" in call.args[0] for call in log.call_args_list))
        with (
            patch.object(release.time, "monotonic", side_effect=[0, 1800]),
            patch.object(publisher, "pr") as refresh,
            patch("builtins.print"),
            self.assertRaisesRegex(release.PublishError, "Timed out"),
        ):
            publisher.await_merge(opened)
        refresh.assert_not_called()


class GitPublicationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(directory.name)
        release.git("init", "--bare", "-q", "remote.git")
        Path("repo").mkdir()
        os.chdir("repo")
        release.git("init", "-q", "-b", "main")
        release.git("config", "user.name", "Fixture")
        release.git("config", "user.email", "fixture@example.invalid")
        release.git("remote", "add", "origin", "../remote.git")
        builder = Path(release.BUILDER)
        builder.parent.mkdir()
        builder.write_text(
            "set -e\nmkdir -p assets/pdfs\ngit rev-parse HEAD > " + str(release.PDF) + "\n"
        )
        release.git("add", release.BUILDER)
        self.base = self.commit_changelog(BASE_CHANGELOG)
        release.git("tag", "v0.3.0")
        release.git("push", "origin", "main", "--tags")
        self.publisher = release.Publisher(VERSION)
        self.promoted = release.promote_changelog(VERSION, "2026-11-28", BASE_CHANGELOG, ["v0.3.0"])
        self.check_pages = self.enterContext(
            patch.object(release, "check_pages", side_effect=check_runs)
        )

    @staticmethod
    def commit_changelog(content):
        Path("CHANGELOG.md").write_text(content, encoding="utf-8")
        release.git("add", "--", "CHANGELOG.md")
        release.git("commit", "-qm", "Changelog")
        return release.git("rev-parse", "HEAD")

    def merge_candidate(self, method):
        release.git("switch", "-qc", "candidate")
        head = self.commit_changelog(self.promoted)
        release.git("push", "origin", "HEAD:refs/pull/42/head")
        release.git("switch", "-q", "main")
        if method == "merge":
            release.git("merge", "--no-ff", "-m", "merge", head)
        elif method == "squash":
            release.git("merge", "--squash", head)
            release.git("commit", "-qm", "squash")
        else:
            release.git("cherry-pick", head)
        release.git("push", "origin", "main")
        return pr_detail(self.publisher, head, release.git("rev-parse", "HEAD"))

    def test_squash_must_match_the_checked_tree(self):
        pr = self.merge_candidate("squash")
        source = pr["merge_commit_sha"]
        self.assertEqual(self.publisher.verify_merged_source(pr, source), source)
        Path("extra.txt").write_text("unreviewed change")
        release.git("add", "extra.txt")
        release.git("commit", "-qm", "unreviewed change")
        other = release.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(release.PublishError, "merged tree differs"):
            self.publisher.verify_merged_source(dict(pr, merge_commit_sha=other), other)
        with self.assertRaisesRegex(release.PublishError, "not on current main"):
            self.publisher.verify_merged_source(pr, self.base)

    def test_merge_commit_requires_the_fetched_checked_head(self):
        pr = self.merge_candidate("merge")
        source = pr["merge_commit_sha"]
        self.assertEqual(self.publisher.verify_merged_source(pr, source), source)
        pr["head"]["sha"] = self.base
        with self.assertRaisesRegex(release.PublishError, "differs from the checked revision"):
            self.publisher.verify_merged_source(pr, source)

    def test_fast_forward_candidate_remains_verifiable(self):
        pr = self.merge_candidate("rebase")
        source = pr["merge_commit_sha"]
        self.assertEqual(self.publisher.verify_merged_source(pr, source), source)

    def test_orphan_branch_is_reused_and_unexpected_changes_are_rejected(self):
        branch = self.publisher.branch
        release.git("switch", "-qc", branch)
        head = self.commit_changelog(self.promoted)
        release.git("push", "origin", branch)
        release.git("switch", "main")
        with (
            patch.object(release, "gh_api", return_value={"number": 42}) as api,
            patch.object(self.publisher, "pr", return_value=pr_detail(self.publisher, head)),
        ):
            self.assertEqual(self.publisher.prepare_pr(self.base, None)["head"]["sha"], head)
        self.assertEqual(api.call_args.args[1]["head"], branch)
        self.assertEqual(release.git("ls-remote", "--heads", "origin", branch).split()[0], head)
        release.git("switch", branch)
        self.commit_changelog(self.promoted.replace("approved change", "altered change"))
        release.git("push", "origin", branch)
        release.git("switch", "main")
        with self.assertRaisesRegex(release.PublishError, "exact promotion"):
            self.publisher.prepare_pr(self.base, None)

    def test_competing_orphan_version_stops_before_preparing_a_new_pr(self):
        release.git("push", "origin", "HEAD:refs/heads/paper-release/v0.4.0")
        with (
            patch.object(release, "pulls", return_value=[]),
            self.assertRaisesRegex(release.PublishError, "Another release is pending"),
        ):
            self.publisher.reject_competing_releases()

    def test_changed_changelog_after_build_stops_before_tag(self):
        pr = self.merge_candidate("merge")
        source = pr["merge_commit_sha"]
        self.commit_changelog(self.promoted.replace("approved change", "altered change"))
        release.git("push", "origin", "main")
        with (
            patch.object(release, "pulls", return_value=[]),
            patch.object(release, "publish_release") as upload,
            self.assertRaisesRegex(release.PublishError, "Current main changed"),
        ):
            self.publisher.finalize(source, None, None, Path("fixture.pdf"))
        upload.assert_not_called()
        self.assertIsNone(self.publisher.tag_source())

    def test_new_release_builds_from_exact_merge_even_after_main_moves(self):
        pr = self.merge_candidate("merge")
        source = pr["merge_commit_sha"]
        Path(release.BUILDER).write_text("exit 99\n")
        release.git("commit", "-qam", "Later builder change")
        release.git("push", "origin", "main")

        def publish(tag, notes, draft, pdf):
            self.assertEqual(tag, self.publisher.tag)
            self.assertIn("An approved change.", notes)
            self.assertIsNone(draft)
            self.assertEqual(pdf.read_text().strip(), source)

        with (
            patch.object(self.publisher, "find_pr", return_value=pr),
            patch.object(self.publisher, "pr", return_value=pr),
            patch.object(release, "pulls", return_value=[]),
            patch.object(release, "get_release", return_value=None),
            patch.object(release, "validate_latest_release"),
            patch.object(release, "publish_release", side_effect=publish) as upload,
        ):
            self.publisher.run()
        self.assertEqual(self.publisher.tag_source(), source)
        self.publisher.verify_tag(source, source)
        upload.assert_called_once()
        self.assertEqual(self.check_pages.call_count, 2)

    def test_tagged_draft_builds_only_when_no_required_asset_exists(self):
        pr = self.merge_candidate("merge")
        source = pr["merge_commit_sha"]
        tag = self.publisher.tag
        release.git("tag", "-a", tag, "-m", f"Release {tag}")
        release.git("push", "origin", f"refs/tags/{tag}")
        ref = f"refs/tags/{tag}"
        original = release.git("rev-parse", ref)
        names = sorted(release.required_asset_names(tag))
        for existing in ([], names[:1], names):
            with (
                self.subTest(existing=existing),
                patch.object(self.publisher, "find_pr", return_value=pr),
                patch.object(self.publisher, "pr", return_value=pr),
                patch.object(release, "pulls", return_value=[]),
                patch.object(release, "validate_latest_release"),
                patch.object(
                    release,
                    "get_release",
                    return_value={"draft": True, "assets": dict.fromkeys(existing)},
                ),
                patch.object(self.publisher, "build_and_publish") as build,
                patch.object(release.shutil, "which", return_value="/usr/bin/pdfinfo"),
                patch.object(release, "publish_release") as publish,
            ):
                self.publisher.run()
                if existing:
                    build.assert_not_called()
                    publish.assert_called_once()
                    self.assertIsNone(publish.call_args.args[-1])
                else:
                    build.assert_called_once_with(pr, source, source)
                    publish.assert_not_called()
        self.assertEqual(release.git("rev-parse", ref), original)

    def test_published_release_never_builds_or_mutates_even_with_missing_assets(self):
        names = sorted(release.required_asset_names(self.publisher.tag))
        for existing in ([], names[:1], names):
            published = {"draft": False, "assets": dict.fromkeys(existing)}
            with (
                self.subTest(existing=existing),
                patch.object(release, "get_release", return_value=published),
                patch.object(self.publisher, "find_pr") as pr,
                patch.object(self.publisher, "build_and_publish") as build,
                patch.object(release, "publish_release") as publish,
                self.assertRaisesRegex(release.ReleaseError, "human repair|already published"),
            ):
                self.publisher.run()
            pr.assert_not_called()
            build.assert_not_called()
            publish.assert_not_called()
        self.check_pages.assert_not_called()
        self.assertIsNone(self.publisher.tag_source())

    def test_historical_tag_without_reviewed_pr_never_rebuilds(self):
        publisher = release.Publisher("0.2.2")
        with (
            patch.object(publisher, "tag_source", return_value=self.base),
            patch.object(release, "get_release", return_value=None),
            patch.object(release, "pulls", return_value=[]),
            patch.object(publisher, "build_and_publish") as build,
            patch.object(release, "publish_release") as publish,
            self.assertRaisesRegex(release.PublishError, "no merged release PR"),
        ):
            publisher.run()
        build.assert_not_called()
        publish.assert_not_called()
        self.check_pages.assert_not_called()


if __name__ == "__main__":
    unittest.main()
