import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_pdf", ROOT / "scripts/build-pdf.py")
BUILD_PDF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_PDF)


class CopyrightNoticeConfigTests(unittest.TestCase):
    def setUp(self):
        self.builder = BUILD_PDF.CirclePDFBuilder(
            str(ROOT / "scripts/plop.paper.config.yaml"), str(ROOT)
        )

    def test_paper_config_supplies_notice_values(self):
        self.assertEqual(
            self.builder._get_copyright_notice(),
            {
                "year": "2026",
                "holder": "Michael Basil",
                "license_name": "CC BY-SA 4.0",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                "additional_text": "Third-party rights are not covered by this license.",
            },
        )

    def test_notice_is_optional_for_other_editions(self):
        del self.builder.config["framing"]["copyright_notice"]
        self.assertIsNone(self.builder._get_copyright_notice())

    def test_notice_must_be_a_mapping_with_all_fields(self):
        for notice in ("", {"year": "2026", "holder": "Author"}):
            with self.subTest(notice=notice):
                self.builder.config["framing"]["copyright_notice"] = notice
                with self.assertRaises(ValueError):
                    self.builder._get_copyright_notice()

    def test_notice_year_and_license_url_are_validated(self):
        notice = self.builder.config["framing"]["copyright_notice"]
        notice["year"] = "26"
        with self.assertRaisesRegex(ValueError, "four-digit year"):
            self.builder._get_copyright_notice()

        notice["year"] = "2026"
        notice["license_url"] = "javascript:alert(1)"
        with self.assertRaisesRegex(ValueError, "HTTPS URL"):
            self.builder._get_copyright_notice()

        notice["license_url"] = "https://example.test/}{\\input{secrets}"
        with self.assertRaisesRegex(ValueError, "HTTPS URL"):
            self.builder._get_copyright_notice()

    def test_notice_variables_are_injected_only_when_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "preview.pdf"
            success = subprocess.CompletedProcess(["pandoc"], 0, b"", b"")
            with patch.object(BUILD_PDF.subprocess, "run", return_value=success) as run:
                self.assertTrue(self.builder._call_pandoc("", output))
            args = run.call_args.args[0]
            self.assertIn("-V", args)
            self.assertIn("copyright_notice=true", args)
            self.assertIn("copyright_notice_year=2026", args)
            self.assertIn("copyright_notice_holder=Michael Basil", args)
            self.assertIn("copyright_notice_license_name=CC BY-SA 4.0", args)
            self.assertIn(
                "copyright_notice_license_url=https://creativecommons.org/licenses/by-sa/4.0/",
                args,
            )

            self.assertIn(
                "copyright_notice_additional_text=Third-party rights are not covered by this license.",
                args,
            )
            del self.builder.config["framing"]["copyright_notice"]["additional_text"]
            with patch.object(BUILD_PDF.subprocess, "run", return_value=success) as run:
                self.assertTrue(self.builder._call_pandoc("", output))
            self.assertFalse(
                any(
                    arg.startswith("copyright_notice_additional_text=")
                    for arg in run.call_args.args[0]
                )
            )

            del self.builder.config["framing"]["copyright_notice"]
            with patch.object(BUILD_PDF.subprocess, "run", return_value=success) as run:
                self.assertTrue(self.builder._call_pandoc("", output))
            self.assertFalse(
                any(arg.startswith("copyright_notice") for arg in run.call_args.args[0])
            )

    def test_template_places_only_configured_notice_in_existing_slot(self):
        template = (ROOT / "scripts/template.tex").read_text()
        notice_block = template.split("$if(copyright_notice)$", 1)[1].split(r"\end{center}", 1)[0]
        self.assertIn(r"© $copyright_notice_year$ $copyright_notice_holder$\par", notice_block)
        self.assertIn(
            r"\href{$copyright_notice_license_url$}{$copyright_notice_license_name$}",
            notice_block,
        )
        self.assertIn("$if(copyright_notice_additional_text)$", notice_block)
        self.assertIn("$copyright_notice_additional_text$\\par\n$endif$", notice_block)
        self.assertIn(
            r"\href{$copyright_notice_license_url$}{$copyright_notice_license_name$}.\par",
            notice_block,
        )
        self.assertNotIn(r"\newline", notice_block)
        self.assertNotIn("Third-party rights are not covered by this license.", template)
        self.assertNotIn("2026 Michael Basil", template)
        self.assertNotIn("creativecommons.org/licenses/by-sa/4.0/", template)


class SimpleYamlLoadTests(unittest.TestCase):
    def test_nested_sections_lists_and_block_scalars_keep_the_supported_shape(self):
        parsed = BUILD_PDF.simple_yaml_load(
            """title: Circle3
sections:
  - title: Moves
    patterns:
      - file: moves/establish.md
        image: images/moves.png
description: |
  First line.

  Third line.
"""
        )

        self.assertEqual(parsed["title"], "Circle3")
        self.assertEqual(
            parsed["sections"],
            [
                {
                    "title": "Moves",
                    "patterns": [
                        {
                            "file": "moves/establish.md",
                            "image": "images/moves.png",
                        }
                    ],
                }
            ],
        )
        self.assertEqual(parsed["description"], "First line.\n\nThird line.")

    def test_invalid_list_and_mapping_contexts_keep_line_diagnostics(self):
        for content, diagnostic in (
            ("- item\n", "line 1"),
            ("items:\n  - item\n    invalid\n", "line 3"),
        ):
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, diagnostic):
                BUILD_PDF.simple_yaml_load(content)


class PatternConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.builder = BUILD_PDF.CirclePDFBuilder(
            str(ROOT / "scripts/plop.paper.config.yaml"), str(ROOT)
        )

    def test_site_path_normalization_keeps_local_routes_and_ignores_assets(self):
        self.assertEqual(
            self.builder._normalize_site_path("/moves/establish/index.html"),
            "/moves/establish/",
        )
        self.assertEqual(
            self.builder._normalize_site_path("https://circle.basil.one/moves/establish/"),
            "/moves/establish/",
        )
        self.assertIsNone(self.builder._normalize_site_path("/images/example.png"))
        self.assertIsNone(self.builder._normalize_site_path("https://other.example/moves/"))

    def test_pattern_sections_keep_string_coercion_and_required_file_validation(self):
        self.builder.config["sections"] = [
            {
                "title": "Moves",
                "ignored": None,
                "patterns": [{"file": "moves/example.md", "count": 3, "ignored": None}],
            }
        ]
        self.assertEqual(
            self.builder._get_pattern_sections(),
            [
                {
                    "title": "Moves",
                    "patterns": [{"file": "moves/example.md", "count": "3"}],
                }
            ],
        )

        self.builder.config["sections"][0]["patterns"] = [{"title": "No file"}]
        with self.assertRaisesRegex(ValueError, "must include 'file'"):
            self.builder._get_pattern_sections()

    def test_missing_pattern_list_diagnostic_preserves_original_section(self):
        self.builder.config["sections"] = [{"title": "Moves", "ignored": None, "patterns": None}]
        with self.assertRaisesRegex(ValueError, r"Got: \{'title': 'Moves', 'ignored': None"):
            self.builder._get_pattern_sections()


class PaperMarkdownAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.builder = BUILD_PDF.CirclePDFBuilder(
            str(ROOT / "scripts/plop.paper.config.yaml"), str(ROOT)
        )

    def test_markdown_and_toc_keep_anchor_collisions_and_section_order(self):
        markdown, toc = self.builder._build_markdown_for_pandoc(
            [],
            abstract_label="Abstract",
            abstract_anchor="shared",
            include_abstract_in_toc=True,
            intro_markdown="Introduction text",
            intro_label="Introduction",
            intro_anchor="shared",
            include_intro_in_toc=True,
            back_matter_markdown="References text",
            back_matter_label="References",
            back_matter_anchor="references",
            include_back_matter_in_toc=True,
            discussion_title="Discussion",
            discussion_anchor="discussion",
            discussion_image="",
            discussion_main_markdown="Discussion text",
            conclusion_title="Conclusion",
            conclusion_anchor="conclusion",
            conclusion_image="",
            conclusion_main_markdown="Conclusion text",
        )

        anchors = [item["anchor"] for item in toc]
        self.assertEqual(anchors, ["shared", "shared-2", "discussion", "conclusion", "references"])
        self.assertLess(markdown.index("Introduction text"), markdown.index("Discussion text"))
        self.assertLess(markdown.index("Discussion text"), markdown.index("Conclusion text"))
        self.assertLess(markdown.index("Conclusion text"), markdown.index("References text"))


if __name__ == "__main__":
    unittest.main()
