import os
import re
import subprocess
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

    def test_evidence_samples_start_middle_end_and_short_or_image_only_content(self):
        with patch.object(content, "plain_text", side_effect=lambda text: text):
            self.assertEqual(content.evidence("A brief note."), ["abriefnote"])
            self.assertEqual(content.evidence("![Image](image.png)"), [])
            paragraphs = [" ".join([word] * 12) for word in ("first", "middle", "last")]
            self.assertEqual(
                content.evidence("\n\n".join(paragraphs)),
                [word * 12 for word in ("first", "middle", "last")],
            )

    def test_page_scope_does_not_accept_other_sections(self):
        blocks = [("one", "a", "missing"), ("two", "b", "present")]
        scopes = content.block_text(blocks, ["first", "missing"], {"a": 1, "b": 2})
        with patch.object(content, "evidence", return_value=["missing"]):
            errors = content.source_errors(blocks[:1], scopes)
        self.assertIn("one", errors[0])

    def test_missing_and_out_of_order_destinations_are_incomplete(self):
        blocks = [("one", "a", ""), ("two", "b", "")]
        for destinations in ({"b": 1}, {"a": 2, "b": 1}):
            with self.subTest(destinations=destinations), self.assertRaises(ValueError):
                content.block_text(blocks, ["a", "b"], destinations)

    def test_citation_target_and_reference_number(self):
        self.builder.config["citations"] = [{"text": "source", "annotation": "[9]"}]
        rules = self.builder._get_citation_annotations()
        with patch.object(content, "plain_text", side_effect=lambda text: text):
            errors = content.check_citation(
                self.builder,
                0,
                rules[0],
                [("p", "a source")],
                {"p": (1, 1, "a source [9]")},
                {"1": "reference"},
            )
        self.assertTrue(any("no numbered References entry" in error for error in errors))
        with patch.object(content, "plain_text", side_effect=lambda text: text):
            errors = content.check_citation(
                self.builder, 0, rules[0], [("p", "no match")], {}, {"1": "reference"}
            )
        self.assertTrue(any("does not match" in error for error in errors))

    def test_subprocess_timeout_is_bounded(self):
        with patch.object(
            content.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 60)
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                content.plain_text("text")

    def test_builder_normalizes_abstract_and_reserves_collisions(self):
        self.builder.config["framing"]["abstract_anchor"] = "Abstract heading!"
        self.builder.config["body"]["introduction_anchor"] = "abstract-heading"
        blocks, _, _ = content.plan(self.builder)
        self.assertEqual(blocks[0][1], "abstract-heading")
        self.assertEqual(blocks[1][1], "abstract-heading-2")


@unittest.skipUnless(os.environ.get("PAPER_PDF_PATH"), "requires same-cycle generated PDF")
class ActualPaperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = content.BUILD_PDF.CirclePDFBuilder(
            str(ROOT / "scripts/plop.paper.config.yaml"), str(ROOT)
        )
        pdf = os.environ["PAPER_PDF_PATH"]
        cls.text = content.run_tool(["pdftotext", "-layout", pdf, "-"])
        dests = content.run_tool(["pdfinfo", "-dests", pdf])
        cls.destinations = {
            name: int(page)
            for page, name in re.findall(r'(?m)^\s*(\d+)\s+\[.*?\]\s+"([^"]+)"\s*$', dests)
        }

    def test_actual_pdf_passes_and_missing_body_page_fails(self):
        self.assertEqual(content.check_content(self.builder, self.text, self.destinations), [])
        blocks, _, _ = content.plan(self.builder)
        pattern = next(block for block in blocks if block[0].startswith("_moves/"))
        start = self.destinations[pattern[1]]
        next_block = blocks[blocks.index(pattern) + 1]
        end = self.destinations[next_block[1]]
        pages = self.text.split("\f")
        for index in range(start, end - 1):
            pages[index] = ""
        errors = content.check_content(self.builder, "\f".join(pages), self.destinations)
        self.assertTrue(any(pattern[0] in error and "fragment" in error for error in errors))

    def test_actual_pdf_missing_citation_marker_fails(self):
        annotation = self.builder._get_citation_annotations()[0]["annotation"]
        self.assertIn(annotation, self.text)
        errors = content.check_content(
            self.builder, self.text.replace(annotation, ""), self.destinations
        )
        self.assertTrue(any("citations[0]" in error and "marker" in error for error in errors))

    def test_actual_pdf_wrong_reference_number_fails(self):
        _, _, back = content.plan(self.builder)
        number, entry = next(iter(content.reference_entries(back).items()))
        first_word = re.search(r"\w+", entry).group()
        corrupted, replacements = re.subn(
            rf"(?m)^(\s*){re.escape(number)}\.(\s+{re.escape(first_word)})",
            r"\g<1>999.\2",
            self.text,
            count=1,
        )
        self.assertEqual(replacements, 1)
        errors = content.check_content(self.builder, corrupted, self.destinations)
        self.assertTrue(any(f"reference {number} number" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
