import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NullObject,
    NumberObject,
    TextStringObject,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
links = importlib.import_module("check_paper_links")


class PaperLinkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "fixture.pdf"

    def make_pdf(
        self,
        internal="target",
        url="https://example.com/resource",
        alias_page=None,
        page_number=False,
    ):
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        writer.add_blank_page(width=200, height=200)
        writer.add_named_destination("target", 1)
        writer.add_named_destination("source", 0)
        if alias_page is not None:
            writer.add_named_destination("alias", alias_page)
        writer.add_outline_item("Target", 1)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        contents = DecodedStreamObject()
        text = b"BT /F1 12 Tf 10 180 Td (Contents) Tj 0 -80 Td (Target) Tj"
        contents.set_data(text + (b" 50 0 Td (2) Tj" if page_number else b"") + b" ET")
        page[NameObject("/Contents")] = writer._add_object(contents)
        annotations = ArrayObject()
        for action in (
            {"/S": NameObject("/GoTo"), "/D": TextStringObject(internal)},
            {"/S": NameObject("/URI"), "/URI": TextStringObject(url)},
        ):
            annotation = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Annot"),
                    NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/Rect"): ArrayObject([FloatObject(n) for n in (10, 98, 45, 112)]),
                    NameObject("/A"): DictionaryObject(
                        {NameObject(key): value for key, value in action.items()}
                    ),
                }
            )
            annotations.append(writer._add_object(annotation))
        if page_number:
            annotation = DictionaryObject(
                {
                    NameObject("/Subtype"): NameObject("/Link"),
                    NameObject("/Rect"): ArrayObject([FloatObject(n) for n in (60, 98, 75, 112)]),
                    NameObject("/A"): DictionaryObject(
                        {
                            NameObject("/S"): NameObject("/GoTo"),
                            NameObject("/D"): TextStringObject(internal),
                        }
                    ),
                }
            )
            annotations.append(writer._add_object(annotation))
        page[NameObject("/Annots")] = annotations
        with self.path.open("wb") as stream:
            writer.write(stream)

    def inspect(self, expected=None, routes=None, normalize_route=None):
        return links.inspect_pdf(
            self.path,
            "Contents",
            {"target": "Target"} if expected is None else expected,
            routes or {},
            "https://circle.basil.one",
            normalize_route=normalize_route,
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
            self.path,
            "Contents",
            {"source": "Source", "target": "Target"},
            {},
            "https://circle.basil.one",
            {
                "source": {
                    ("internal", "target"),
                    ("external", "https://example.com/resource"),
                    ("internal", "lost"),
                }
            },
        )
        self.assertTrue(any("internal link to 'lost' is missing" in i for i in issues))
        self.assertFalse(any("internal link to 'target' is missing" in i for i in issues))
        self.assertFalse(
            any("external link to 'https://example.com/resource' is missing" in i for i in issues)
        )

    def test_equivalent_named_destinations_preserve_toc_and_source_links(self):
        self.make_pdf(internal="alias", alias_page=1, page_number=True)
        issues, *_ = links.inspect_pdf(
            self.path,
            "Contents",
            {"target": "Target"},
            {},
            "https://circle.basil.one",
            {"source": {("internal", "target")}},
            {"source", "target"},
        )
        self.assertEqual(issues, [])

        self.make_pdf(internal="alias", alias_page=0)
        issues, *_ = links.inspect_pdf(
            self.path,
            "Contents",
            {"target": "Target"},
            {},
            "https://circle.basil.one",
            {"source": {("internal", "target")}},
            {"source", "target"},
        )
        self.assertTrue(any("internal link to 'target' is missing" in issue for issue in issues))
        self.assertTrue(
            any("to 'target' is missing or points elsewhere" in issue for issue in issues)
        )

    def test_source_without_toc_entry_still_has_a_valid_range(self):
        self.make_pdf()
        issues, *_ = links.inspect_pdf(
            self.path,
            "Contents",
            {"target": "Target"},
            {},
            "https://circle.basil.one",
            {"source": {("external", "https://example.com/resource")}},
            {"source", "target"},
        )
        self.assertEqual(issues, [])

    def test_wrapped_and_continued_toc_annotations(self):
        writer = PdfWriter()
        pages = [writer.add_blank_page(width=200, height=200) for _ in range(3)]
        writer.add_named_destination("target", 2)
        font = writer._add_object(
            DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
        )
        for page, text, y in ((pages[0], b"Contents", 180), (pages[1], b"Wrapped Label", 100)):
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
            )
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 10 " + str(y).encode() + b" Td (" + text + b") Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
        annotations = ArrayObject()
        for rect in ((10, 98, 56, 112), (56, 98, 90, 112)):
            annotations.append(
                writer._add_object(
                    DictionaryObject(
                        {
                            NameObject("/Subtype"): NameObject("/Link"),
                            NameObject("/Rect"): ArrayObject([FloatObject(n) for n in rect]),
                            NameObject("/A"): DictionaryObject(
                                {
                                    NameObject("/S"): NameObject("/GoTo"),
                                    NameObject("/D"): TextStringObject("target"),
                                }
                            ),
                        }
                    )
                )
            )
        pages[1][NameObject("/Annots")] = annotations
        with self.path.open("wb") as stream:
            writer.write(stream)
        issues, *_ = links.inspect_pdf(
            self.path, "Contents", {"target": "Wrapped Label"}, {}, "https://circle.basil.one"
        )
        self.assertEqual(issues, [])

    def test_broken_internal_target_and_missing_toc_link_are_reported(self):
        self.make_pdf(internal="missing")
        issues, *_ = self.inspect()
        self.assertTrue(
            any("named destination 'missing' does not exist" in issue for issue in issues)
        )
        self.assertTrue(
            any("to 'target' is missing or points elsewhere" in issue for issue in issues)
        )

    def test_toc_entry_pointing_to_wrong_existing_destination_is_rejected(self):
        self.make_pdf(internal="source")
        issues, *_ = self.inspect(expected={"target": "Target", "source": "Source"})
        self.assertTrue(any("'Target' to 'target' is missing" in issue for issue in issues))
        self.assertTrue(any("'Source' to 'source' is missing" in issue for issue in issues))

    def test_invalid_destination_operands_are_rejected(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        reference = page.indirect_reference
        refs = {(reference.idnum, reference.generation)}
        invalid = ArrayObject([reference, NameObject("/FitR")])
        self.assertIn("operands", links._destination_error(invalid, {}, refs))
        invalid = ArrayObject([reference, NameObject("/XYZ"), NumberObject(1)])
        self.assertIn("operands", links._destination_error(invalid, {}, refs))
        valid = ArrayObject(
            [reference, NameObject("/XYZ"), NullObject(), NullObject(), NumberObject(1)]
        )
        self.assertEqual(links._destination_error(valid, {}, refs), "")

    def test_range_rejection_is_uncertain(self):
        results = Queue()
        with patch.object(links, "check_external", return_value=("likely broken", "HTTP 416")):
            links._external_worker("https://example.com", results)
        self.assertEqual(results.get()[0], "uncertain")

    def test_config_false_and_normalized_anchor_follow_builder(self):
        config = (ROOT / "scripts/plop.paper.config.yaml").read_text()
        config = config.replace("include_abstract_in_toc: true", "include_abstract_in_toc: false")
        config = config.replace(
            "include_back_matter_in_toc: true", "include_back_matter_in_toc: false"
        )
        config = config.replace(
            "introduction_anchor: introduction", "introduction_anchor: Intro Heading"
        )
        config = config.replace(
            "introduction_file: content/", f"introduction_file: {ROOT}/scripts/content/"
        )
        config = config.replace(
            "back_matter_file: content/", f"back_matter_file: {ROOT}/scripts/content/"
        )
        config_path = Path(self.directory.name) / "paper.yaml"
        config_path.write_text(config)
        _, toc, *_ = links.paper_expectations(config_path)
        self.assertNotIn("abstract", toc)
        self.assertNotIn("references", toc)
        self.assertIn("intro-heading", toc)

    def test_source_link_to_included_move_must_be_rewritten(self):
        normalizer = links.paper_expectations(ROOT / "scripts/plop.paper.config.yaml")[-1]
        for suffix in ("", "index.html", ".html"):
            self.make_pdf(url=f"https://circle.basil.one/moves/establish/{suffix}")
            if suffix == ".html":
                self.make_pdf(url="https://circle.basil.one/moves/establish.html")
            issues, *_ = self.inspect(
                routes={"/moves/establish/": "pattern-establish"}, normalize_route=normalizer
            )
            self.assertTrue(
                any("should link to PDF destination 'pattern-establish'" in i for i in issues)
            )

    def test_external_failure_and_uncertainty_are_distinct(self):
        self.make_pdf()
        expected = (
            "Contents",
            {"target": "Target"},
            {},
            "https://circle.basil.one",
            {},
            {"target"},
            None,
        )
        with patch.object(links, "paper_expectations", return_value=expected):
            report, state, findings = links.audit(
                self.path,
                Path("unused"),
                checker=lambda url, timeout: ("likely broken", "HTTP 404"),
            )
            self.assertEqual(state, "failed")
            self.assertIn("Page 1", report)
            self.assertIn("HTTP 404", report)
            self.assertTrue(any("HTTP 404" in finding for finding in findings))
            report, state, _ = links.audit(
                self.path, Path("unused"), checker=lambda url, timeout: ("uncertain", "HTTP 403")
            )
            self.assertEqual(state, "advisory")
            self.assertIn("HTTP 403", report)
            report, state, _ = links.audit(
                self.path, Path("unused"), checker=lambda url, timeout: ("healthy", "HTTP 200")
            )
            self.assertEqual(state, "passed")

    def test_confirmed_defect_takes_precedence_over_uncertainty_and_unavailable(self):
        self.make_pdf(internal="missing")
        expected = (
            "Contents",
            {"target": "Target"},
            {},
            "https://circle.basil.one",
            {},
            {"target"},
            None,
        )
        with patch.object(links, "paper_expectations", return_value=expected):
            for category in ("uncertain", "unavailable"):
                with self.subTest(category=category):
                    report, state, findings = links.audit(
                        self.path, Path("unused"), checker=lambda url, timeout: (category, "test")
                    )
                    self.assertEqual(state, "failed")
                    self.assertIn("named destination 'missing'", report)
                    self.assertTrue(any(category in item for item in findings))

    def test_external_failure_takes_precedence_over_uncertainty(self):
        self.make_pdf()
        expected = (
            "Contents",
            {"target": "Target"},
            {},
            "https://circle.basil.one",
            {},
            {"target"},
            None,
        )
        inspected = (
            [],
            {
                "https://example.com/a": {"Page 1"},
                "https://example.com/b": {"Page 2"},
            },
            1,
            1,
            1,
        )
        with (
            patch.object(links, "paper_expectations", return_value=expected),
            patch.object(links, "inspect_pdf", return_value=inspected),
        ):
            _, state, findings = links.audit(
                self.path,
                Path("unused"),
                checker=lambda url, timeout: (
                    ("likely broken", "HTTP 404")
                    if url.endswith("/a")
                    else ("uncertain", "HTTP 403")
                ),
            )
        self.assertEqual(state, "failed")
        self.assertTrue(any("HTTP 404" in item and "Page 1" in item for item in findings))
        self.assertTrue(any("HTTP 403" in item and "Page 2" in item for item in findings))

    def test_skipped_network_is_incomplete_not_passed(self):
        self.make_pdf()
        with patch.object(
            links,
            "paper_expectations",
            return_value=(
                "Contents",
                {"target": "Target"},
                {},
                "https://circle.basil.one",
                {},
                {"target"},
                None,
            ),
        ):
            report, state, _ = links.audit(self.path, Path("unused"), check_network=False)
        self.assertEqual(state, "incomplete")
        self.assertIn("network check not requested", report)

    def test_missing_pdf_is_not_a_pass(self):
        with patch.object(
            links,
            "paper_expectations",
            return_value=(
                "Contents",
                {"target": "Target"},
                {},
                "https://circle.basil.one",
                {},
                {"target"},
                None,
            ),
        ):
            with self.assertRaises(FileNotFoundError):
                links.audit(self.path, Path("unused"))

    def test_cli_distinguishes_network_uncertainty_from_unavailable_audit(self):
        argv = ["check_paper_links.py", str(self.path)]
        with (
            patch.object(sys, "argv", argv),
            patch.object(
                links, "audit", return_value=("advisory network\n", "advisory", ["HTTP 403"])
            ),
        ):
            self.assertEqual(links.main(), 3)
        with (
            patch.object(sys, "argv", argv),
            patch.object(links, "audit", side_effect=OSError("PDF unavailable")),
        ):
            self.assertEqual(links.main(), 2)

    def test_cli_writes_structured_advisory_and_incomplete_evidence(self):
        report_path = Path(self.directory.name) / "report.json"
        argv = ["check_paper_links.py", str(self.path), "--json-report", str(report_path)]
        for result, expected in (
            (("summary\n", "advisory", ["Page 1: HTTP 403"]), 3),
            (OSError("PDF unavailable"), 2),
        ):
            with self.subTest(expected=expected), patch.object(sys, "argv", argv):
                if isinstance(result, Exception):
                    with patch.object(links, "audit", side_effect=result):
                        self.assertEqual(links.main(), expected)
                else:
                    with patch.object(links, "audit", return_value=result):
                        self.assertEqual(links.main(), expected)
                evidence = json.loads(report_path.read_text())
                self.assertEqual(evidence["status"], "advisory" if expected == 3 else "incomplete")
                self.assertTrue(evidence["findings"])

    def test_confirmed_status_survives_report_write_failure(self):
        status_path = Path(self.directory.name) / "status"
        report_path = Path(self.directory.name) / "missing" / "report.md"
        argv = [
            "check_paper_links.py",
            str(self.path),
            "--status-file",
            str(status_path),
            "--report",
            str(report_path),
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(links, "audit", return_value=("report\n", "failed", ["broken"])),
            self.assertRaises(FileNotFoundError),
        ):
            links.main()
        self.assertEqual(status_path.read_text(), "failed")

    def test_status_file_records_each_audit_outcome(self):
        status_path = Path(self.directory.name) / "status"
        argv = ["check_paper_links.py", str(self.path), "--status-file", str(status_path)]
        for state, exit_code, findings in (
            ("passed", 0, []),
            ("advisory", 3, ["HTTP 403"]),
            ("failed", 1, ["Page 1: broken"]),
            ("incomplete", 2, ["network unavailable"]),
        ):
            with (
                self.subTest(state=state),
                patch.object(sys, "argv", argv),
                patch.object(links, "audit", return_value=("report\n", state, findings)),
            ):
                self.assertEqual(links.main(), exit_code)
                self.assertEqual(status_path.read_text(), state)

    def test_checker_failure_does_not_hide_internal_defect(self):
        self.make_pdf(internal="missing")
        expected = (
            "Contents",
            {"target": "Target"},
            {},
            "https://circle.basil.one",
            {},
            {"target"},
            None,
        )
        with patch.object(links, "paper_expectations", return_value=expected):
            _, state, findings = links.audit(
                self.path, Path("unused"), checker=lambda url, timeout: 1 / 0
            )
        self.assertEqual(state, "failed")
        self.assertTrue(any("named destination 'missing'" in finding for finding in findings))
        self.assertTrue(any("network checker failed" in finding for finding in findings))

    def test_json_findings_are_bounded(self):
        report_path = Path(self.directory.name) / "report.json"
        with (
            patch.object(
                sys,
                "argv",
                ["check_paper_links.py", str(self.path), "--json-report", str(report_path)],
            ),
            patch.object(links, "audit", return_value=("report\n", "failed", ["x" * 3000] * 101)),
        ):
            self.assertEqual(links.main(), 1)
        findings = json.loads(report_path.read_text())["findings"]
        self.assertEqual(len(findings), 100)
        self.assertTrue(all(0 < len(item) <= 2000 for item in findings))
        self.assertIn("omitted", findings[-1])

    def test_json_report_rejects_contradictory_and_corrupted_evidence(self):
        report_path = Path(self.directory.name) / "report.json"
        for state, findings in (("passed", ["broken"]), ("advisory", []), ("unknown", [])):
            with self.subTest(state=state), self.assertRaises(ValueError):
                links.write_json_report(report_path, state, findings)
        with (
            patch.object(Path, "read_text", return_value='{"status":"failed","findings":[]}'),
            self.assertRaises(ValueError),
        ):
            links.write_json_report(report_path, "passed", [])


if __name__ == "__main__":
    unittest.main()
