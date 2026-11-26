#!/usr/bin/env python3
"""Check bounded source-derived evidence in the generated paper PDF."""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_pdf", ROOT / "scripts/build-pdf.py")
BUILD_PDF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_PDF)
TIMEOUT = 60


def compact(text):
    text = unicodedata.normalize("NFKD", text).casefold()
    return "".join(char for char in text if char.isalnum())


def run_tool(args, *, input_text=None):
    return subprocess.run(
        args, input=input_text, text=True, capture_output=True, check=True, timeout=TIMEOUT
    ).stdout


def plain_text(markdown):
    return run_tool(["pandoc", "-f", "markdown", "-t", "plain", "--wrap=none"], input_text=markdown)


def evidence(markdown):
    markdown = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", markdown)
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", plain_text(markdown)):
        words = re.findall(r"\w+", unicodedata.normalize("NFKD", paragraph))
        if words:
            paragraphs.append(words)
    if not paragraphs:
        return []
    substantive = [words for words in paragraphs if len(words) >= 12] or paragraphs
    indexes = sorted({0, len(substantive) // 2, len(substantive) - 1})
    return [compact(" ".join(substantive[index][:12])) for index in indexes]


def plan(builder):
    config = builder.config
    _, abstract_anchor, _ = builder._get_framing_abstract_toc_settings()
    intro, _, intro_anchor, _ = builder._get_body_intro()
    back, _, back_anchor, _ = builder._get_body_back_matter()
    _, discussion_anchor = builder._get_discussion_meta()
    _, conclusion_anchor = builder._get_conclusion_meta()
    sections, intro_anchor, discussion_anchor, conclusion_anchor, back_anchor, *_ = (
        builder._plan_paper_content(
            builder._get_pattern_sections(),
            abstract_anchor,
            intro,
            intro_anchor,
            discussion_anchor,
            conclusion_anchor,
            back,
            back_anchor,
        )
    )
    abstract = builder._get_markdown_content(
        config["framing"], inline_key="abstract", file_key="abstract_file"
    )
    blocks = [("abstract", abstract_anchor, abstract)]
    if intro.strip():
        blocks.append(("introduction", intro_anchor, intro))
    for section in sections:
        if not section["is_appendix"]:
            blocks.extend((p["file"], p["anchor"], p["body"]) for p in section["patterns"])
    for key, anchor in (("discussion", discussion_anchor), ("conclusion", conclusion_anchor)):
        markdown = builder._get_markdown_content(
            config[key], inline_key="main_text", file_key="main_text_file"
        )
        blocks.append((key, anchor, markdown))
    if back.strip():
        blocks.append(("back matter", back_anchor, back))
    for section in sections:
        if section["is_appendix"]:
            blocks.extend((p["file"], p["anchor"], p["body"]) for p in section["patterns"])
    return blocks, sections, back


def block_text(blocks, pages, destinations):
    result = {}
    for index, (label, anchor, _) in enumerate(blocks):
        start = destinations.get(anchor)
        if not start or start > len(pages):
            raise ValueError(
                f"{label}: destination {anchor!r} missing or invalid; cannot scope content"
            )
        end = len(pages)
        for _, next_anchor, _ in blocks[index + 1 :]:
            next_page = destinations.get(next_anchor)
            if next_page:
                if next_page < start:
                    raise ValueError(f"{label}: destination {anchor!r} is out of document order")
                end = next_page
                break
        result[label] = (start, end, "\n".join(pages[start - 1 : end]))
    return result


def source_errors(blocks, scopes):
    errors = []
    for label, anchor, markdown in blocks:
        start, end, text = scopes[label]
        for index, fragment in enumerate(evidence(markdown), 1):
            if fragment not in compact(text):
                errors.append(
                    f"{label} (destination {anchor}, pages {start}-{end}): "
                    f"source fragment {index} missing; inspect this section and regenerate"
                )
    return errors


def reference_entries(back):
    section = re.search(r"(?ms)^#{1,6}\s+References\s*$\n(.*?)(?=^#{1,6}\s|\Z)", back)
    if not section:
        return {}
    return dict(re.findall(r"(?m)^\s*(\d+)\.\s+(.+)$", section.group(1)))


def citation_errors(builder, sections, scopes, back):
    errors = []
    original = builder.config.get("citations")
    rules = builder._get_citation_annotations()
    references = reference_entries(back)
    try:
        builder.config["citations"] = []
        patterns = [
            (pattern["file"], builder._read_and_clean_pattern(pattern["file"])[1])
            for section in sections
            for pattern in section["patterns"]
        ]
        for index, rule in enumerate(rules):
            builder.config["citations"] = [original[index]]
            errors.extend(check_citation(builder, index, rule, patterns, scopes, references))
    finally:
        if original is None:
            builder.config.pop("citations", None)
        else:
            builder.config["citations"] = original
    return errors


def check_citation(builder, index, rule, patterns, scopes, references):
    errors = []
    matched = False
    annotation = rule["annotation"]
    number = re.fullmatch(r"\[(\d+)\]", annotation)
    if number and references and number.group(1) not in references:
        errors.append(f"citations[{index}]: {annotation} has no numbered References entry")
    for label, body in patterns:
        annotated = builder._apply_citation_annotations(body)
        if annotated == body:
            continue
        matched = True
        rendered = plain_text(annotated)
        for match in re.finditer(re.escape(annotation), rendered):
            before = re.findall(r"\w+", rendered[max(0, match.start() - 150) : match.start()])
            context = " ".join(before[-6:])
            start, end, pdf_text = scopes[label]
            needle = compact(context) + "marker" + compact(annotation)
            pdf_marked = re.sub(re.escape(annotation), " marker " + compact(annotation), pdf_text)
            if needle not in compact(pdf_marked):
                errors.append(
                    f"citations[{index}] in {label} (pages {start}-{end}): "
                    f"marker {annotation} missing after matched text; regenerate PDF"
                )
    if not matched:
        errors.append(f"citations[{index}]: configured target does not match any pattern body")
    return errors


def check_content(builder, pdf_text, destinations):
    blocks, sections, back = plan(builder)
    pages = pdf_text.split("\f")
    if pages[-1] == "":
        pages.pop()
    scopes = block_text(blocks, pages, destinations)
    errors = source_errors(blocks, scopes)
    errors.extend(citation_errors(builder, sections, scopes, back))
    if "back matter" in scopes:
        for number, entry in reference_entries(back).items():
            if compact(entry)[:50] not in compact(scopes["back matter"][2]):
                errors.append(
                    f"back matter: reference {number} source evidence missing; regenerate PDF"
                )
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("pdf")
    parser.add_argument("--report")
    args = parser.parse_args()
    status = "passed"
    try:
        builder = BUILD_PDF.CirclePDFBuilder(args.config)
        text = run_tool(["pdftotext", "-layout", args.pdf, "-"])
        dests = run_tool(["pdfinfo", "-dests", args.pdf])
        destinations = {
            name: int(page)
            for page, name in re.findall(r'(?m)^\s*(\d+)\s+\[.*?\]\s+"([^"]+)"\s*$', dests)
        }
        errors = check_content(builder, text, destinations)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.SubprocessError,
        SystemExit,
    ) as error:
        status = "incomplete"
        errors = [f"Extraction or configuration unavailable: {error}; inspect logs and rerun"]
    if errors:
        if status != "incomplete":
            status = "failed"
        for error in errors:
            print(f"Paper content integrity: {error}", file=sys.stderr)
    else:
        print("Paper content integrity: sampled source and citation evidence found.")
        print(
            "Samples do not prove completeness or semantic/visual fidelity; author review is necessary."
        )
        print(
            "Reference numbering is checked only for numeric markers and a numbered References list."
        )
    if args.report:
        Path(args.report).write_text(
            json.dumps({"status": status, "findings": errors}, ensure_ascii=False), encoding="utf-8"
        )
    return {"passed": 0, "failed": 1, "incomplete": 2}[status]


if __name__ == "__main__":
    sys.exit(main())
