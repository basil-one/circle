import unittest
from pathlib import Path
from unittest.mock import patch

import check_paper_content as content

ROOT = Path(__file__).resolve().parents[1]


class PaperContentTests(unittest.TestCase):
    def setUp(self):
        self.builder = content.BUILD_PDF.CirclePDFBuilder(
            str(ROOT / "scripts/plop.paper.config.yaml"), str(ROOT)
        )
        self.plain = patch.object(content, "plain_text", side_effect=lambda text: text)
        self.plain.start()
        self.addCleanup(self.plain.stop)
        builder = self.builder
        used = {"abstract", "introduction"}
        sections = builder._plan_pdf_sections(builder._get_pattern_sections(), used, [], [], {})
        self.destinations = used | {"discussion", "conclusion", "references"}
        sources = [
            builder._get_markdown_content(builder.config["framing"], inline_key="abstract", file_key="abstract_file"),
            builder._get_body_intro()[0],
            builder._get_body_back_matter()[0],
        ]
        for key in ("discussion", "conclusion"):
            sources.append(builder._get_markdown_content(
                builder.config[key], inline_key="main_text", file_key="main_text_file"
            ))
        for section in sections:
            for pattern in section["patterns"]:
                sources.append(pattern["body"])
        self.pdf_text = " ".join(content.evidence(source) for source in sources)
        self.pattern = sections[0]["patterns"][0]

    def test_configured_content_and_destinations_pass(self):
        self.assertEqual(content.check_content(self.builder, self.pdf_text, self.destinations), [])

    def test_missing_pattern_text_and_section_destination_are_reported(self):
        fragment = content.evidence(self.pattern["body"])
        text = self.pdf_text.replace(fragment, "")
        destinations = self.destinations - {"section-three-guiding-moves"}
        errors = content.check_content(self.builder, text, destinations)
        self.assertTrue(any(self.pattern["file"] in error and "text evidence" in error for error in errors))
        self.assertTrue(any("section-three-guiding-moves" in error for error in errors))

    def test_unmatched_citation_is_reported(self):
        self.builder.config["citations"].append({
            "url": "https://example.invalid/not-in-paper", "annotation": "[9]"
        })
        errors = content.check_content(self.builder, self.pdf_text, self.destinations)
        self.assertTrue(any("citations[4]" in error and "does not match" in error for error in errors))

    def test_unicode_and_wrapped_text_normalize_consistently(self):
        self.assertEqual(content.compact("Circle3™ devel-\noping"), content.compact("Circle3™ developing"))


if __name__ == "__main__":
    unittest.main()
