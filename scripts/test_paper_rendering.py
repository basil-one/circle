import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import check_paper_rendering as rendering

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts/check_paper_rendering.py"


def fixture_pdf(path, page_streams, *, broken_image=False):
    image = zlib.compress(bytes([0, 0, 0]))
    image_filter = b"/Bogus" if broken_image else b"/FlateDecode"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # page tree, filled in once page references are known
        (
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter "
            + image_filter
            + b" /Length "
            + str(len(image)).encode()
            + b" >>\nstream\n"
            + image
            + b"\nendstream"
        ),
    ]
    page_refs = []
    for stream in page_streams:
        page_id = len(objects) + 1
        page_refs.append(f"{page_id} 0 R".encode())
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /XObject << /Im0 3 0 R >> >> /Contents "
            + f"{page_id + 1} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects[1] = (
        b"<< /Type /Pages /Count "
        + str(len(page_refs)).encode()
        + b" /Kids ["
        + b" ".join(page_refs)
        + b"] >>"
    )
    data = b"%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        data += f"{offset:010d} 00000 n \n".encode()
    data += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(data)


class RenderingUnitTests(unittest.TestCase):
    def test_unwritable_report_is_incomplete_not_passed(self):
        output = io.StringIO()
        errors = io.StringIO()
        with (
            patch.object(rendering, "check_rendering", return_value=(1, [])),
            patch.object(sys, "argv", [str(CHECK), "paper.pdf", "--report", "/missing/report"]),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            self.assertEqual(rendering.main(), 2)
        self.assertNotIn("passed", output.getvalue())
        self.assertIn("incomplete: cannot write report", errors.getvalue())

    def test_bad_raster_is_not_treated_as_blank_or_passed(self):
        for data in (b"", b"P6\n1 1\n255\n\xff", b"P6\n0 1\n255\n"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                rendering.raster_pixels(data)

    def test_missing_tool_is_incomplete(self):
        with patch.object(rendering.subprocess, "run", side_effect=FileNotFoundError("pdfinfo")):
            with self.assertRaisesRegex(rendering.IncompleteCheck, "pdfinfo"):
                rendering.run_poppler(["pdfinfo"])

    def test_timeout_is_incomplete(self):
        with patch.object(
            rendering.subprocess, "run", side_effect=subprocess.TimeoutExpired("pdftoppm", 1)
        ):
            with self.assertRaisesRegex(rendering.IncompleteCheck, "pdftoppm"):
                rendering.run_poppler(["pdftoppm"])

    def test_incomplete_preserves_earlier_findings_in_report(self):
        info = subprocess.CompletedProcess([], 0, b"Pages: 2\n", b"")
        blank = subprocess.CompletedProcess([], 0, b"P6\n1 1\n255\n\xff\xff\xff", b"")
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "paper.pdf"
            report = Path(directory) / "report.json"
            pdf.write_bytes(b"fixture")
            with (
                patch.object(
                    rendering,
                    "run_poppler",
                    side_effect=[info, blank, rendering.IncompleteCheck("renderer timed out")],
                ),
                patch.object(sys, "argv", [str(CHECK), str(pdf), "--report", str(report)]),
            ):
                self.assertEqual(rendering.main(), 1)
            data = json.loads(report.read_text())
        self.assertEqual(data["status"], "failed")
        self.assertIn("page 1: rendered completely blank", data["findings"][0])
        self.assertIn("page 2: renderer timed out", data["findings"][1])

    def test_incomplete_without_findings_has_incomplete_status(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with (
                patch.object(
                    rendering, "check_rendering", side_effect=rendering.IncompleteCheck("timeout")
                ),
                patch.object(sys, "argv", [str(CHECK), "paper.pdf", "--report", str(report)]),
            ):
                self.assertEqual(rendering.main(), 2)
            data = json.loads(report.read_text())
        self.assertEqual(data["status"], "incomplete")
        self.assertIn("timeout", data["findings"][0])

    def test_failed_result_survives_unwritable_report(self):
        with (
            patch.object(rendering, "check_rendering", return_value=(1, ["page 1: blank"])),
            patch.object(sys, "argv", [str(CHECK), "paper.pdf", "--report", "/missing/report"]),
        ):
            self.assertEqual(rendering.main(), 1)

    def test_report_is_bounded_and_keeps_incomplete_coverage(self):
        findings = [f"page {page}: rendered completely blank" for page in range(1, 302)]
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with (
                patch.object(
                    rendering,
                    "check_rendering",
                    side_effect=rendering.IncompleteCheck("page 302: timeout", findings),
                ),
                patch.object(sys, "argv", [str(CHECK), "paper.pdf", "--report", str(report)]),
                patch("sys.stderr"),
            ):
                self.assertEqual(rendering.main(), 1)
            data = json.loads(report.read_text())
        self.assertEqual(len(data["findings"]), 300)
        self.assertIn("further findings omitted", data["findings"][-2])
        self.assertIn("page 302: timeout", data["findings"][-1])

    def test_time_limit_reports_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "paper.pdf"
            pdf.write_bytes(b"fixture")
            with self.assertRaisesRegex(rendering.IncompleteCheck, "time limit"):
                rendering.check_rendering(pdf, time_limit=0)

    def test_invalid_raster_is_a_page_failure(self):
        info = subprocess.CompletedProcess([], 0, b"Pages: 1\n", b"")
        raster = subprocess.CompletedProcess([], 0, b"not a raster", b"")
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "paper.pdf"
            pdf.write_bytes(b"fixture")
            with patch.object(rendering, "run_poppler", side_effect=[info, raster]):
                _, findings = rendering.check_rendering(pdf)
        self.assertIn("page 1: rendering error: invalid PPM header", findings)

    def test_renderer_error_is_reported_even_with_zero_exit(self):
        info = subprocess.CompletedProcess([], 0, b"Pages: 1\n", b"")
        error = subprocess.CompletedProcess(
            [], 0, b"P6\n1 1\n255\n\x00\x00\x00", b"Syntax Error: bad image"
        )
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "paper.pdf"
            pdf.write_bytes(b"fixture")
            with patch.object(rendering, "run_poppler", side_effect=[info, error]):
                _, findings = rendering.check_rendering(pdf)
        self.assertIn("page 1: rendering error: Syntax Error: bad image", findings)

    def test_blank_page_is_reported_without_requiring_poppler(self):
        info = subprocess.CompletedProcess([], 0, b"Pages: 2\n", b"")
        visible = subprocess.CompletedProcess([], 0, b"P6\n1 1\n255\n\x00\x00\x00", b"")
        blank = subprocess.CompletedProcess([], 0, b"P6\n1 1\n255\n\xff\xff\xff", b"")
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "paper.pdf"
            pdf.write_bytes(b"fixture")
            with patch.object(rendering, "run_poppler", side_effect=[info, visible, blank]):
                _, findings = rendering.check_rendering(pdf)
        self.assertIn("page 2: rendered completely blank; review for an unintended page", findings)


@unittest.skipUnless(shutil.which("pdfinfo") and shutil.which("pdftoppm"), "Poppler is required")
class RenderingIntegrationTests(unittest.TestCase):
    def run_check(self, streams, *, broken_image=False):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "paper.pdf"
            fixture_pdf(pdf, streams, broken_image=broken_image)
            return subprocess.run(
                [sys.executable, str(CHECK), str(pdf)], capture_output=True, text=True, check=False
            )

    def test_sparse_and_image_only_pages_pass(self):
        result = self.run_check([b"0 0 0 rg 100 100 2 2 re f", b"q 100 0 0 100 50 50 cm /Im0 Do Q"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rendered 2 nonblank pages", result.stdout)

    def test_wholly_blank_page_identifies_page(self):
        result = self.run_check([b"0 0 0 rg 100 100 2 2 re f", b""])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("page 2: rendered completely blank", result.stderr)

    def test_broken_image_is_not_reported_as_passed(self):
        result = self.run_check([b"q 100 0 0 100 50 50 cm /Im0 Do Q"], broken_image=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("page 1: rendering error", result.stderr)

    def test_missing_pdf_is_incomplete(self):
        result = subprocess.run(
            [sys.executable, str(CHECK), "/nonexistent/circle-paper.pdf"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("incomplete", result.stderr)


if __name__ == "__main__":
    unittest.main()
