#!/usr/bin/env python3
"""Check configured paper content against the generated PDF, not editorial meaning."""

import importlib.util
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_pdf", ROOT / "scripts/build-pdf.py")
BUILD_PDF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_PDF)


def compact(text):
    text = unicodedata.normalize("NFKD", text).casefold()
    return "".join(char for char in text if char.isalnum())


def plain_text(markdown):
    result = subprocess.run(
        ["pandoc", "-f", "markdown", "-t", "plain", "--wrap=none"],
        input=markdown, text=True, capture_output=True, check=True,
    )
    return result.stdout


def evidence(markdown):
    markdown = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", markdown)
    paragraphs = re.split(r"\n\s*\n", plain_text(markdown))
    fallback = []
    for paragraph in paragraphs:
        words = re.findall(r"\w+", unicodedata.normalize("NFKD", paragraph), flags=re.UNICODE)
        if len(words) >= 12:
            return compact(" ".join(words[:12]))
        fallback.extend(words)
    return compact(" ".join(fallback[:12]))


def citation_errors(builder, sections):
    errors = []
    config = builder.config
    rules = builder._get_citation_annotations()
    original = config.get("citations")
    try:
        config["citations"] = []
        bodies = [
            builder._read_and_clean_pattern(pattern["file"])[1]
            for section in sections for pattern in section["patterns"]
        ]
        for index, rule in enumerate(rules):
            config["citations"] = [original[index]]
            if not any(builder._apply_citation_annotations(body) != body for body in bodies):
                errors.append(f"citations[{index}]: configured target does not match any pattern body")
    finally:
        if original is None:
            config.pop("citations", None)
        else:
            config["citations"] = original
    return errors


def output_errors(expected, anchors, pdf_text, destinations):
    errors = []
    for label, value in anchors:
        if value not in destinations:
            errors.append(f"{label}: PDF destination '{value}' is missing")
    output = compact(pdf_text)
    for label, markdown in expected:
        fragment = evidence(markdown)
        if fragment and fragment not in output:
            errors.append(f"{label}: configured source text evidence is missing from PDF")
    return errors


def check_content(builder, pdf_text, destinations):
    config = builder.config
    expected = []
    anchors = []
    used = set()

    def anchor(label, value):
        anchors.append((label, builder._reserve_pdf_anchor(value, used)))

    _, abstract_anchor, _ = builder._get_framing_abstract_toc_settings()
    anchor("abstract", abstract_anchor)
    expected.append(("abstract", builder._get_markdown_content(
        config["framing"], inline_key="abstract", file_key="abstract_file"
    )))
    intro, _, intro_anchor, _ = builder._get_body_intro()
    back, _, back_anchor, _ = builder._get_body_back_matter()
    if intro:
        anchor("introduction", intro_anchor)
        expected.append(("introduction", intro))

    sections = builder._plan_pdf_sections(
        builder._get_pattern_sections(), used, [], [], {}
    )
    for section in sections:
        anchors.append((section["title"], section["anchor"]))
        for pattern in section["patterns"]:
            anchors.append((pattern["file"], pattern["anchor"]))
            expected.append((pattern["file"], pattern["body"]))

    for key in ("discussion", "conclusion"):
        _, value = getattr(builder, f"_get_{key}_meta")()
        anchor(key, value)
        expected.append((key, builder._get_markdown_content(
            config[key], inline_key="main_text", file_key="main_text_file"
        )))
    if back:
        anchor("back matter", back_anchor)
        expected.append(("back matter", back))

    errors = output_errors(expected, anchors, pdf_text, destinations)
    errors.extend(citation_errors(builder, sections))
    return errors


def main():
    if len(sys.argv) != 3:
        print("Usage: check_paper_content.py CONFIG PDF", file=sys.stderr)
        return 2
    try:
        builder = BUILD_PDF.CirclePDFBuilder(sys.argv[1])
        pdf = sys.argv[2]
        text = subprocess.run(
            ["pdftotext", "-layout", pdf, "-"], text=True, capture_output=True, check=True
        ).stdout
        dests = subprocess.run(
            ["pdfinfo", "-dests", pdf], text=True, capture_output=True, check=True
        ).stdout
        destinations = set(re.findall(r'"([^"]+)"\s*$', dests, re.MULTILINE))
        errors = check_content(builder, text, destinations)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Paper content integrity incomplete: {error}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"Paper content integrity: {error}", file=sys.stderr)
        return 1
    print("Paper content integrity: configured destinations and source text evidence found.")
    print("Text extraction does not establish semantic or visual fidelity; author review remains necessary.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
