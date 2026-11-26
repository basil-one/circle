import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    TextStringObject,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_paper_links as links


class PaperLinkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "fixture.pdf"

    def make_pdf(self, internal="target", url="https://example.com/resource"):
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        writer.add_blank_page(width=200, height=200)
        writer.add_named_destination("target", 1)
        writer.add_named_destination("source", 0)
        writer.add_outline_item("Target", 1)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
        })
        contents = DecodedStreamObject()
        contents.set_data(b"BT /F1 12 Tf 10 100 Td (Contents) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(contents)
        annotations = ArrayObject()
        for action in (
            {"/S": NameObject("/GoTo"), "/D": TextStringObject(internal)},
            {"/S": NameObject("/URI"), "/URI": TextStringObject(url)},
        ):
            annotation = DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Link"),
                NameObject("/Rect"): ArrayObject([FloatObject(0)] * 4),
                NameObject("/A"): DictionaryObject(
                    {NameObject(key): value for key, value in action.items()}
                ),
            })
            annotations.append(writer._add_object(annotation))
        page[NameObject("/Annots")] = annotations
        with self.path.open("wb") as stream:
            writer.write(stream)

    def inspect(self, expected=None, routes=None):
        return links.inspect_pdf(
            self.path, "Contents", {"target"} if expected is None else expected,
            routes or {}, "https://circle.basil.one"
        )

    def test_real_annotations_and_bookmark_resolve(self):
        self.make_pdf()
        issues, urls, named, internal, outline = self.inspect()
        self.assertEqual(issues, [])
        self.assertEqual(urls, {"https://example.com/resource": {"Page 1"}})
        self.assertEqual((named, internal, outline), (2, 1, 1))

    def test_source_links_must_survive_in_the_source_section(self):
        self.make_pdf()
        issues, *_ = links.inspect_pdf(
            self.path, "Contents", {"source", "target"}, {}, "https://circle.basil.one",
            {"source": {("internal", "target"), ("external", "https://example.com/resource"),
                        ("internal", "lost")}},
        )
        self.assertTrue(any("internal link to 'lost' is missing" in i for i in issues))
        self.assertFalse(any("internal link to 'target' is missing" in i for i in issues))
        self.assertFalse(any("external link to 'https://example.com/resource' is missing" in i
                             for i in issues))

    def test_broken_internal_target_and_missing_toc_link_are_reported(self):
        self.make_pdf(internal="missing")
        issues, *_ = self.inspect()
        self.assertTrue(any("named destination 'missing' does not exist" in issue for issue in issues))
        self.assertTrue(any("Table of contents: link to 'target' is missing" in issue for issue in issues))

    def test_source_link_to_included_move_must_be_rewritten(self):
        self.make_pdf(url="https://circle.basil.one/moves/establish/")
        issues, *_ = self.inspect(routes={"/moves/establish/": "pattern-establish"})
        self.assertTrue(any("should link to PDF destination 'pattern-establish'" in i for i in issues))

    def test_external_failure_and_uncertainty_are_distinct(self):
        self.make_pdf()
        expected = ("Contents", {"target"}, {}, "https://circle.basil.one", {})
        with patch.object(links, "paper_expectations", return_value=expected):
            report, state = links.audit(
                self.path, Path("unused"), checker=lambda url, timeout: ("likely broken", "HTTP 404")
            )
            self.assertEqual(state, "failed")
            self.assertIn("Page 1", report)
            self.assertIn("HTTP 404", report)
            report, state = links.audit(
                self.path, Path("unused"), checker=lambda url, timeout: ("uncertain", "HTTP 403")
            )
            self.assertEqual(state, "incomplete")
            self.assertIn("HTTP 403", report)
            report, state = links.audit(
                self.path, Path("unused"), checker=lambda url, timeout: ("healthy", "HTTP 200")
            )
            self.assertEqual(state, "passed")

    def test_skipped_network_is_incomplete_not_passed(self):
        self.make_pdf()
        with patch.object(
            links, "paper_expectations",
            return_value=("Contents", {"target"}, {}, "https://circle.basil.one", {}),
        ):
            report, state = links.audit(self.path, Path("unused"), check_network=False)
        self.assertEqual(state, "incomplete")
        self.assertIn("network check not requested", report)

    def test_missing_pdf_is_not_a_pass(self):
        with patch.object(
            links, "paper_expectations",
            return_value=("Contents", {"target"}, {}, "https://circle.basil.one", {}),
        ):
            with self.assertRaises(FileNotFoundError):
                links.audit(self.path, Path("unused"))


if __name__ == "__main__":
    unittest.main()
