import http.server
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.site.check_links import check_external, main, public_destination, report_for_site


class LinkAuditHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1/private")
            self.end_headers()
            return
        status = {"/ok": 200, "/blocked": 403, "/missing": 404}[self.path]
        self.send_response(status)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class CheckLinksTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.site = self.root / "_site"
        self.site.mkdir()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LinkAuditHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.external_origin = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temp_dir.cleanup()

    def write(self, relative_path, content):
        path = self.site / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_scans_generated_pages_baseurl_clean_urls_fragments_and_assets(self):
        self.write(
            "index.html",
            '<a href="/preview/moves/listening/">Move</a>'
            '<a href="#home">Home</a><h1 id="home">Home</h1>'
            '<img src="/preview/assets/photo.jpg" '
            'srcset="data:image/svg+xml,%3Csvg%3E 1x, /preview/assets/photo.jpg 2x">',
        )
        self.write(
            "moves/listening/index.html",
            '<a href="../missing/">Broken move</a>'
            '<a href="/preview/moves/listening/#absent">Broken fragment</a>',
        )
        self.write("assets/photo.jpg", "image")
        config = self.root / "_config.yml"
        config.write_text('url: "https://circle.example"\nbaseurl: "/preview"\n')

        report, errors, _, _ = report_for_site(self.site, config, check_externals=False)

        self.assertEqual(errors, 2)
        self.assertIn("Generated HTML pages scanned: 2", report)
        self.assertIn("/moves/listening/", report)
        self.assertIn("target does not exist", report)
        self.assertIn("fragment '#absent' not found", report)
        self.assertNotIn("assets/photo.jpg", report)

    def test_external_results_distinguish_reachable_blocked_and_missing(self):
        with patch("scripts.site.check_links.public_destination", return_value=(True, "")):
            self.assertEqual(check_external(f"{self.external_origin}/ok")[0], "healthy")
            self.assertEqual(check_external(f"{self.external_origin}/blocked")[0], "uncertain")
            self.assertEqual(check_external(f"{self.external_origin}/missing")[0], "likely broken")
        with patch(
            "scripts.site.check_links.public_destination",
            side_effect=[(True, ""), (False, "non-public")],
        ):
            category, detail = check_external(f"{self.external_origin}/redirect")
        self.assertEqual(category, "uncertain")
        self.assertIn("redirect to", detail)
        self.assertFalse(public_destination("http://127.0.0.1/private")[0])
        self.assertFalse(public_destination("not-a-url")[0])

    def test_uncertain_external_failures_are_reported_without_failing_audit(self):
        self.write(
            "index.html",
            '<a href="https://connect.example/blocked">Action</a>',
        )
        config = self.root / "_config.yml"
        config.write_text(
            'url: "https://circle.example"\n'
            'baseurl: ""\naction_links:\n  connect: "https://connect.example/blocked"\n'
        )
        report_path = self.root / "report.md"

        with patch(
            "scripts.site.check_links.check_external",
            return_value=("uncertain", "HTTP 403 Forbidden"),
        ):
            with patch(
                "sys.argv",
                [
                    "check_links.py",
                    "--site-dir",
                    str(self.site),
                    "--config",
                    str(config),
                    "--report",
                    str(report_path),
                    "--fail-on-likely-external",
                ],
            ):
                self.assertEqual(main(), 0)
        report = report_path.read_text(encoding="utf-8")

        self.assertIn("Uncertain external responses", report)
        self.assertIn("HTTP 403", report)
        self.assertIn("ACTION: `connect`", report)
        self.assertIn("## Key visitor destinations", report)
        self.assertIn("source pages: /", report)

    def test_confirmed_defect_and_uncertainty_are_both_reported(self):
        broken = "https://broken.example/missing"
        uncertain = "https://blocked.example/private"
        self.write("index.html", f'<a href="{broken}">Broken</a><a href="{uncertain}">Blocked</a>')
        config = self.root / "_config.yml"
        config.write_text('url: "https://circle.example"\nbaseurl: ""\n')
        report_path = self.root / "report.md"
        status_path = self.root / "status"

        with patch(
            "scripts.site.check_links.check_external",
            side_effect=[
                ("likely broken", "HTTP 404 Not Found"),
                ("uncertain", "HTTP 403 Forbidden"),
            ],
        ):
            with patch(
                "sys.argv",
                [
                    "check_links.py",
                    "--site-dir",
                    str(self.site),
                    "--config",
                    str(config),
                    "--report",
                    str(report_path),
                    "--status-file",
                    str(status_path),
                    "--fail-on-likely-external",
                ],
            ):
                self.assertEqual(main(), 1)

        report = report_path.read_text(encoding="utf-8")
        self.assertEqual(status_path.read_text(encoding="utf-8").strip(), "failure")
        self.assertIn("## Likely broken external links", report)
        self.assertIn(broken, report)
        self.assertIn("## Uncertain external responses", report)
        self.assertIn(uncertain, report)

    def test_status_file_distinguishes_healthy_advisory_and_failure(self):
        config = self.root / "_config.yml"
        config.write_text('url: "https://circle.example"\nbaseurl: ""\n')
        report_path = self.root / "report.md"
        status_path = self.root / "status"
        for destination, expected in (
            ("https://healthy.example/path", "success"),
            ("https://blocked.example/path", "advisory"),
            ("https://broken.example/path", "failure"),
        ):
            self.write("index.html", f'<a href="{destination}">External</a>')
            category = {"success": "healthy", "advisory": "uncertain", "failure": "likely broken"}[
                expected
            ]
            with patch(
                "scripts.site.check_links.check_external",
                return_value=(category, "HTTP 200" if expected == "success" else "HTTP 403"),
            ):
                with patch(
                    "sys.argv",
                    [
                        "check_links.py",
                        "--site-dir",
                        str(self.site),
                        "--config",
                        str(config),
                        "--report",
                        str(report_path),
                        "--status-file",
                        str(status_path),
                        "--fail-on-likely-external",
                    ],
                ):
                    self.assertEqual(main(), 1 if expected == "failure" else 0)
            self.assertEqual(status_path.read_text(encoding="utf-8").strip(), expected)

    def test_reports_arbitrary_http_action_but_excludes_mailto(self):
        destination = "https://news.example/subscribe"
        email = "mailto:hello@circle.example"
        self.write(
            "index.html",
            f'<a href="{destination}">Newsletter</a><a href="{email}">Email</a>',
        )
        config = self.root / "_config.yml"
        config.write_text(
            'url: "https://circle.example"\n'
            'baseurl: ""\naction_links:\n'
            f'  newsletter: "{destination}"\n'
            f'  email: "{email}"\n'
        )

        with patch(
            "scripts.site.check_links.check_external", return_value=("healthy", "HTTP 200")
        ) as check_external_mock:
            report, errors, likely_broken, _ = report_for_site(self.site, config)

        self.assertEqual((errors, likely_broken), (0, 0))
        check_external_mock.assert_called_once_with(destination)
        self.assertIn("## Key visitor destinations", report)
        self.assertIn(f"- **newsletter**: `{destination}` — healthy: HTTP 200", report)
        self.assertNotIn("**email**", report)

    def test_action_destination_lists_sorted_source_pages(self):
        destination = "https://connect.example/path"
        self.write("index.html", f'<a href="{destination}">Connect</a>')
        self.write("about/index.html", f'<a href="{destination}">Connect</a>')
        config = self.root / "_config.yml"
        config.write_text(
            'url: "https://circle.example"\n'
            'baseurl: ""\naction_links:\n'
            f'  connect: "{destination}"\n'
        )

        with patch("scripts.site.check_links.check_external", return_value=("healthy", "HTTP 200")):
            report, errors, likely_broken, _ = report_for_site(self.site, config)

        self.assertEqual((errors, likely_broken), (0, 0))
        self.assertIn(
            f"- **connect**: `{destination}` — healthy: HTTP 200 — source pages: /, /about/",
            report,
        )

    def test_external_destinations_are_checked_in_order_with_delay(self):
        self.write(
            "index.html",
            '<a href="https://z.example/path">Z</a><a href="https://a.example/path">A</a>',
        )
        config = self.root / "_config.yml"
        config.write_text('url: "https://circle.example"\nbaseurl: ""\n')

        with (
            patch(
                "scripts.site.check_links.check_external", return_value=("healthy", "HTTP 200")
            ) as check,
            patch("scripts.site.check_links.time.sleep") as sleep,
        ):
            report_for_site(self.site, config, external_delay=0.4)

        self.assertEqual(
            check.call_args_list,
            [
                unittest.mock.call("https://a.example/path"),
                unittest.mock.call("https://z.example/path"),
            ],
        )
        sleep.assert_called_once_with(0.4)

    def test_likely_broken_external_link_fails_scheduled_audit(self):
        self.write("index.html", '<a href="https://broken.example/path">Broken</a>')
        config = self.root / "_config.yml"
        config.write_text('url: "https://circle.example"\nbaseurl: ""\n')
        report_path = self.root / "report.md"

        with patch(
            "scripts.site.check_links.check_external",
            return_value=("likely broken", "HTTP 404 Not Found"),
        ):
            with patch(
                "sys.argv",
                [
                    "check_links.py",
                    "--site-dir",
                    str(self.site),
                    "--config",
                    str(config),
                    "--report",
                    str(report_path),
                    "--fail-on-likely-external",
                ],
            ):
                self.assertEqual(main(), 1)
        report = report_path.read_text(encoding="utf-8")
        self.assertIn("Likely broken external links", report)
        self.assertIn("Source:** `/`", report)
        self.assertIn("Destination:** `https://broken.example/path`", report)

    def test_external_404_fails_and_corrected_200_passes_full_audit(self):
        config = self.root / "_config.yml"
        config.write_text('url: "https://circle.example"\nbaseurl: ""\n')
        report_path = self.root / "report.md"

        with patch("scripts.site.check_links.public_destination", return_value=(True, "")):
            for path, expected_status in (("/missing", 1), ("/ok", 0)):
                self.write("index.html", f'<a href="{self.external_origin}{path}">External</a>')
                with patch(
                    "sys.argv",
                    [
                        "check_links.py",
                        "--site-dir",
                        str(self.site),
                        "--config",
                        str(config),
                        "--report",
                        str(report_path),
                        "--fail-on-likely-external",
                    ],
                ):
                    self.assertEqual(main(), expected_status)

                report = report_path.read_text(encoding="utf-8")
                if path == "/missing":
                    self.assertIn("HTTP 404", report)
                    self.assertIn("Likely broken external links", report)
                else:
                    self.assertIn("External destinations checked: 1", report)
                    self.assertIn("Likely broken external links\n\nNone.", report)

    def test_skip_external_does_not_make_requests(self):
        self.write("index.html", '<a href="https://external.example/path">External</a>')
        config = self.root / "_config.yml"
        config.write_text(
            'url: "https://circle.example"\n'
            'baseurl: ""\naction_links:\n'
            '  connect: "https://external.example/path"\n'
        )
        report_path = self.root / "report.md"

        with patch("scripts.site.check_links.check_external") as check_external_mock:
            with patch(
                "sys.argv",
                [
                    "check_links.py",
                    "--site-dir",
                    str(self.site),
                    "--config",
                    str(config),
                    "--report",
                    str(report_path),
                    "--skip-external",
                ],
            ):
                self.assertEqual(main(), 0)
        check_external_mock.assert_not_called()
        report = report_path.read_text(encoding="utf-8")
        self.assertIn("External destinations checked: 0", report)
        self.assertIn(
            "- **connect**: `https://external.example/path` — not checked",
            report,
        )

    def test_command_fails_for_broken_internal_target(self):
        self.write(
            "index.html",
            '<a href="/missing-page/">Broken link</a><img src="/missing.png">',
        )
        config = self.root / "_config.yml"
        config.write_text('url: "https://circle.example"\nbaseurl: ""\n')
        report_path = self.root / "report.md"

        with patch(
            "sys.argv",
            [
                "check_links.py",
                "--site-dir",
                str(self.site),
                "--config",
                str(config),
                "--report",
                str(report_path),
                "--skip-external",
            ],
        ):
            self.assertEqual(main(), 1)
        report = report_path.read_text(encoding="utf-8")
        self.assertIn("/missing-page/", report)
        self.assertIn("/missing.png", report)


if __name__ == "__main__":
    unittest.main()
