import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.site.check_seo import audit_site, format_summary, main


class CheckSeoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.site = self.root / "_site"
        self.site.mkdir()
        self.config = self.root / "_config.yml"
        self.config.write_text('url: "https://circle.example"\nbaseurl: ""\n')

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_page(self, content, relative_path="index.html"):
        path = self.site / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_accepts_expected_title_canonical_and_indexable_page(self):
        self.write_page(
            "<html><head><title>Home | Circle</title>"
            '<link rel="canonical" href="https://circle.example/"></head></html>'
        )

        page_count, findings = audit_site(self.site, self.config)

        self.assertEqual(page_count, 1)
        self.assertEqual(findings, [])

    def test_reports_wrong_canonical_with_expected_url_and_remedy(self):
        self.write_page(
            "<title>Reflection</title>"
            '<link rel="canonical" href="https://wrong.example/moves/listening/">',
            "moves/listening/index.html",
        )

        _, findings = audit_site(self.site, self.config)

        self.assertEqual(len(findings), 1)
        self.assertIn("/moves/listening/", findings[0])
        self.assertIn("https://circle.example/moves/listening/", findings[0])
        self.assertIn("_layouts/default.html", findings[0])

    def test_reports_accidental_noindex_with_page_and_remedy(self):
        self.write_page(
            '<title>Home</title><link rel="canonical" href="https://circle.example/">'
            '<meta name="robots" content="index, noindex">'
        )

        _, findings = audit_site(self.site, self.config)

        self.assertEqual(len(findings), 1)
        self.assertIn("/ (index.html)", findings[0])
        self.assertIn("noindex", findings[0])
        self.assertIn("review the indexing intent", findings[0])

    def test_reports_missing_title_and_googlebot_noindex(self):
        self.write_page(
            '<link rel="canonical" href="https://circle.example/">'
            '<meta name="googlebot" content="none">'
        )

        _, findings = audit_site(self.site, self.config)

        self.assertEqual(len(findings), 2)
        self.assertIn("nonempty <title>", findings[0])
        self.assertIn("googlebot", findings[1])
        self.assertIn("none", findings[1])

    def test_summary_reports_page_count_and_successful_checks(self):
        summary = format_summary(3, [])

        self.assertIn("Generated HTML pages audited: 3", summary)
        self.assertIn(
            "Page titles, canonical URLs, and indexability: **passed** (0 findings).",
            summary,
        )

    def test_summary_reports_failed_checks_and_actionable_findings(self):
        finding = "ERROR: / (index.html): expected one nonempty <title>."

        summary = format_summary(1, [finding])

        self.assertIn("Generated HTML pages audited: 1", summary)
        self.assertIn(
            "Page titles, canonical URLs, and indexability: **failed** (1 finding).",
            summary,
        )
        self.assertIn(f"- {finding}", summary)

    def test_failed_audit_writes_report_and_keeps_failure_status(self):
        self.write_page(
            '<link rel="canonical" href="https://circle.example/">'
            '<meta name="robots" content="noindex">'
        )
        report = self.root / "seo-summary.md"
        arguments = [
            "check_seo.py",
            "--site-dir",
            str(self.site),
            "--config",
            str(self.config),
            "--report",
            str(report),
        ]

        with patch("sys.argv", arguments), redirect_stdout(StringIO()) as output:
            result = main()

        self.assertEqual(result, 1)
        self.assertIn("noindex", output.getvalue())
        self.assertIn("Generated HTML pages audited: 1", report.read_text())
        self.assertIn("indexability: **failed**", report.read_text())


if __name__ == "__main__":
    unittest.main()
