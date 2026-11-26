#!/usr/bin/env python3
"""Check that each page of an existing paper PDF renders and is not wholly blank."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


class IncompleteCheck(Exception):
    def __init__(self, message, findings=()):
        super().__init__(message)
        self.findings = list(findings)


def run_poppler(arguments, timeout=60):
    try:
        return subprocess.run(
            arguments,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise IncompleteCheck(f"could not run {arguments[0]}: {error}") from error


def page_count(pdf, timeout=60):
    result = run_poppler(["pdfinfo", str(pdf)], timeout=timeout)
    match = re.search(rb"^Pages:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    if result.returncode or not match or int(match.group(1)) < 1:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise IncompleteCheck(
            f"could not determine PDF pages: {detail or 'invalid pdfinfo output'}"
        )
    return int(match.group(1))


def raster_pixels(data):
    # Poppler emits binary PPM (P6); retain the binary data after the header.
    header = re.match(rb"\AP6\s+(\d+)\s+(\d+)\s+255[\r\n]", data)
    if not header:
        raise ValueError("invalid PPM header")
    width, height = (int(value) for value in header.groups())
    pixels = data[header.end() :]
    if width < 1 or height < 1 or len(pixels) != width * height * 3:
        raise ValueError("incomplete PPM raster")
    return pixels


def check_rendering(pdf, time_limit=300):
    deadline = time.monotonic() + time_limit

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise IncompleteCheck("rendering time limit exceeded")
        return min(60, seconds)

    try:
        pdf = pdf.resolve()
        if not pdf.is_file() or pdf.stat().st_size == 0:
            raise IncompleteCheck(f"PDF is missing or empty: {pdf}")
    except OSError as error:
        raise IncompleteCheck(f"cannot access PDF {pdf}: {error}") from error
    pages = page_count(pdf, timeout=remaining())
    findings = []
    for page in range(1, pages + 1):
        try:
            result = run_poppler(
                [
                    "pdftoppm",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    "-scale-to",
                    "1000",
                    str(pdf),
                ],
                timeout=remaining(),
            )
        except IncompleteCheck as error:
            raise IncompleteCheck(f"page {page}: {error}", findings) from error
        diagnostic = result.stderr.decode("utf-8", "replace").strip()
        if result.returncode or re.search(r"\b(?:error|fatal)\b", diagnostic, re.IGNORECASE):
            detail = " ".join(diagnostic.split())[:500] or "pdftoppm failed"
            findings.append(f"page {page}: rendering error: {detail}")
            continue
        try:
            pixels = raster_pixels(result.stdout)
        except ValueError as error:
            findings.append(f"page {page}: rendering error: {error}")
            continue
        if pixels.count(b"\xff") == len(pixels):
            findings.append(
                f"page {page}: rendered completely blank; review for an unintended page"
            )
    return pages, findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="existing author-edition PDF to inspect")
    parser.add_argument("--report", type=Path, help="write a JSON outcome for orchestration")
    args = parser.parse_args()
    try:
        pages, findings = check_rendering(args.pdf)
    except IncompleteCheck as error:
        pages, findings = None, error.findings
        outcome, detail, code = "incomplete", str(error), 2
    else:
        outcome = "failure" if findings else "success"
        detail = ""
        code = 1 if findings else 0

    if args.report:
        report = {"status": outcome, "detail": detail, "findings": findings[:20]}
        if len(findings) > 20:
            report["detail"] += f" {len(findings) - 20} further findings omitted."
        try:
            args.report.write_text(json.dumps(report), encoding="utf-8")
        except OSError as error:
            print(
                f"Paper: Rendering Integrity — incomplete: cannot write report: {error}",
                file=sys.stderr,
            )
            code = 2
    if outcome == "incomplete":
        print(f"Paper: Rendering Integrity — incomplete: {detail}", file=sys.stderr)
    elif findings:
        print("Paper: Rendering Integrity — failed:", file=sys.stderr)
    else:
        print(f"Paper: Rendering Integrity — passed: rendered {pages} nonblank pages.")
        print("Rendering checks do not replace human review of images, clipping, or layout.")
    for finding in findings:
        print(f"- {finding}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
