#!/usr/bin/env python3
"""Audit link annotations and navigation in an already generated paper PDF."""

from __future__ import annotations

import argparse
import importlib.util
import json
import multiprocessing
import queue
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urldefrag, urlsplit

from check_site_links import check_external, same_site
from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    NullObject,
    NumberObject,
)

ROOT = Path(__file__).resolve().parents[1]


def paper_expectations(config: Path):
    spec = importlib.util.spec_from_file_location("build_pdf", ROOT / "scripts/build-pdf.py")
    builder_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder_module)
    builder = builder_module.CirclePDFBuilder(str(config), str(ROOT))
    data = builder.config
    framing = data["framing"]
    abstract_label, abstract, include_abstract = builder._get_framing_abstract_toc_settings()
    intro_text, intro_label, intro_anchor, include_intro = builder._get_body_intro()
    back_text, back_label, back_anchor, include_back = builder._get_body_back_matter()
    discussion_title, discussion_anchor = builder._get_discussion_meta()
    conclusion_title, conclusion_anchor = builder._get_conclusion_meta()
    (
        sections, intro_anchor, discussion_anchor, conclusion_anchor, back_anchor,
        core, appendix, routes,
    ) = builder._plan_paper_content(
        builder._get_pattern_sections(), abstract, intro_text, intro_anchor,
        discussion_anchor, conclusion_anchor, back_text, back_anchor,
    )
    toc = {abstract: abstract_label} if include_abstract and abstract_label and abstract else {}
    if intro_text.strip() and include_intro and intro_label:
        toc[intro_anchor] = intro_label
    toc.update(
        (item["anchor"], item["label"])
        for item in builder._build_pdf_toc(
            core, appendix, discussion_title, discussion_anchor, conclusion_title,
            conclusion_anchor, back_text.strip(), include_back, back_label, back_anchor,
        )
    )
    anchors = {section["anchor"] for section in sections}
    anchors.update(pattern["anchor"] for section in sections for pattern in section["patterns"])
    anchors.update((abstract, discussion_anchor, conclusion_anchor))
    if intro_text.strip():
        anchors.add(intro_anchor)
    if back_text.strip():
        anchors.add(back_anchor)
    source_links = _source_expectations(builder, sections, routes)
    return (
        framing["toc_label"],
        toc,
        routes,
        builder._get_root_url(),
        source_links,
        anchors,
        builder._normalize_site_path,
    )


def _source_expectations(builder, sections, routes):
    source_links = {}
    for section in sections:
        for pattern in section["patterns"]:
            targets = set()
            for match in re.finditer(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", pattern["body"]):
                raw = match.group(2).strip().split()
                if not raw:
                    continue
                url = raw[0]
                route = builder._normalize_site_path(url)
                if route in routes:
                    targets.add(("internal", routes[route]))
                elif url.startswith("#"):
                    targets.add(("internal", url[1:]))
                elif urlsplit(url).scheme in {"http", "https"}:
                    targets.add(("external", url))
            source_links[pattern["anchor"]] = targets
    return source_links


def _page_reference_valid(reference, page_refs: set[tuple[int, int]]) -> bool:
    return (
        isinstance(reference, IndirectObject)
        and (reference.idnum, reference.generation) in page_refs
    )


def _destination_error(destination, named, page_refs) -> str:
    if isinstance(destination, IndirectObject):
        destination = destination.get_object()
    if isinstance(destination, DictionaryObject):
        destination = destination.get("/D")
    if isinstance(destination, (str, NameObject)):
        name = str(destination)
        if name not in named:
            return f"named destination {name!r} does not exist"
        destination = named[name].dest_array
    if not isinstance(destination, ArrayObject) or len(destination) < 2:
        return "invalid destination array"
    if not _page_reference_valid(destination[0], page_refs):
        return "destination page is missing from this PDF"
    if str(destination[1]) not in {
        "/XYZ",
        "/Fit",
        "/FitH",
        "/FitV",
        "/FitR",
        "/FitB",
        "/FitBH",
        "/FitBV",
    }:
        return "invalid destination fit type"
    operand_counts = {
        "/XYZ": 3,
        "/Fit": 0,
        "/FitH": 1,
        "/FitV": 1,
        "/FitR": 4,
        "/FitB": 0,
        "/FitBH": 1,
        "/FitBV": 1,
    }
    kind = str(destination[1])
    operands = destination[2:]
    if (
        len(operands) != operand_counts[kind]
        or any(not isinstance(value, (NumberObject, FloatObject, NullObject)) for value in operands)
        or (kind == "/FitR" and any(isinstance(value, NullObject) for value in operands))
    ):
        return "invalid destination operands"
    return ""


def _destination_key(destination, named):
    if isinstance(destination, IndirectObject):
        destination = destination.get_object()
    if isinstance(destination, DictionaryObject):
        destination = destination.get("/D")
    if isinstance(destination, str):
        destination = named[str(destination)].dest_array
    page = destination[0]
    return (
        page.idnum,
        page.generation,
        str(destination[1]),
        *(None if isinstance(value, NullObject) else float(value) for value in destination[2:]),
    )


def _outline_findings(outline, reader, issues):
    count = 0
    for item in outline:
        if isinstance(item, list):
            count += _outline_findings(item, reader, issues)
        else:
            count += 1
            page = reader.get_destination_page_number(item)
            if page is None or page < 0 or page >= len(reader.pages):
                issues.append(f"Bookmark {item.title!r}: destination page is missing")
    return count


def _toc_words(path: Path, page: int):
    result = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-bbox", str(path), "-"],
        capture_output=True,
        check=True,
        timeout=30,
    )
    root = ET.fromstring(result.stdout)
    return [
        (
            word.text or "",
            float(word.get("xMin")),
            float(word.get("yMin")),
            float(word.get("xMax")),
            float(word.get("yMax")),
        )
        for word in root.iter("{http://www.w3.org/1999/xhtml}word")
    ]


def _annotation_label(annotation, page, words):
    left, bottom, right, top = map(float, annotation["/Rect"])
    height = float(page.mediabox.height)
    selected = [
        text
        for text, x1, y1, x2, y2 in words
        if left <= (x1 + x2) / 2 <= right and height - top <= (y1 + y2) / 2 <= height - bottom
    ]
    return " ".join(selected)


class PDFLinkInspection:
    def __init__(self, path, toc_label, expected_toc, routes, site_url, anchors):
        self.reader = PdfReader(path, strict=True)
        if not self.reader.pages:
            raise ValueError("PDF has no pages")
        self.named = self.reader.named_destinations
        self.page_refs = {
            (page.indirect_reference.idnum, page.indirect_reference.generation)
            for page in self.reader.pages
            if page.indirect_reference is not None
        }
        self.destination_names = {}
        for name, destination in self.named.items():
            if not _destination_error(destination.dest_array, self.named, self.page_refs):
                key = _destination_key(destination.dest_array, self.named)
                self.destination_names.setdefault(key, set()).add(name)
        self.issues, self.sources, self.toc_labels = [], {}, {}
        self.expected_toc, self.routes, self.site_url = expected_toc, routes, site_url
        self.anchors = anchors
        self.internal_count, self.page_links = 0, []
        toc_pages = [
            i for i, page in enumerate(self.reader.pages) if toc_label in page.extract_text()
        ]
        if len(toc_pages) != 1:
            self.issues.append(
                f"Table of contents label {toc_label!r} found on {len(toc_pages)} pages"
            )
        self.toc_start = toc_pages[0] if len(toc_pages) == 1 else -1
        positions = self.anchor_positions()
        self.toc_end = min(
            (pos for pos in positions.values() if pos is not None and pos > self.toc_start),
            default=len(self.reader.pages),
        )
        self.words = (
            {index: _toc_words(path, index + 1) for index in range(self.toc_start, self.toc_end)}
            if self.toc_start >= 0
            else {}
        )

    def anchor_positions(self):
        return {
            anchor: self.reader.get_destination_page_number(self.named[anchor])
            for anchor in self.anchors
            if anchor in self.named
        }

    def _internal(self, annotation, action, page, index, observed):
        destination = annotation.get("/Dest") if "/Dest" in annotation else action.get("/D")
        self.internal_count += 1
        error = _destination_error(destination, self.named, self.page_refs)
        if error:
            self.issues.append(f"Page {index + 1}: {destination!r} — {error}")
        else:
            names = self.destination_names.get(_destination_key(destination, self.named), set())
            for name in names:
                observed.add(("internal", name))
            for name in names & self.expected_toc.keys() if index in self.words else ():
                label = _annotation_label(annotation, page, self.words[index])
                if label:
                    self.toc_labels.setdefault(name, []).append(label)

    def _uri(self, action, location, observed):
        url = str(action.get("/URI", ""))
        try:
            parts = urlsplit(url)
            if parts.scheme in {"http", "https"} and parts.hostname:
                observed.add(("external", url))
                self.sources.setdefault(urldefrag(url).url, set()).add(location)
                if same_site(url, self.site_url):
                    normalized = self.normalize_route(url)
                    if normalized in self.routes:
                        self.issues.append(
                            f"{location}: {url} should link to PDF destination "
                            f"{self.routes[normalized]!r}"
                        )
            elif parts.scheme != "mailto" or not parts.path:
                self.issues.append(f"{location}: unsupported or invalid URI {url!r}")
        except ValueError as error:
            self.issues.append(f"{location}: invalid URI {url!r}: {error}")

    def scan_page(self, index, page):
        observed = set()
        for ref in page.get("/Annots", []):
            annotation = ref.get_object()
            if annotation.get("/Subtype") != "/Link":
                continue
            location = f"Page {index + 1}"
            action = annotation.get("/A")
            action = action.get_object() if action else None
            if "/Dest" in annotation or (action and action.get("/S") == "/GoTo"):
                self._internal(annotation, action, page, index, observed)
            elif action and action.get("/S") == "/URI":
                self._uri(action, location, observed)
            else:
                self.issues.append(f"{location}: unsupported or missing link action")
        self.page_links.append(observed)

    def check_source_links(self, source_links):
        positions = self.anchor_positions()
        for anchor, targets in source_links.items():
            start = positions.get(anchor)
            if start is None or start < 0:
                self.issues.append(f"Source {anchor!r}: destination page is unavailable")
                continue
            end = min(
                (pos for pos in positions.values() if pos is not None and pos > start),
                default=len(self.reader.pages),
            )
            observed = set().union(*self.page_links[start:end])
            for kind, target in sorted(targets - observed):
                self.issues.append(f"Source {anchor!r}: {kind} link to {target!r} is missing")


def inspect_pdf(
    path: Path,
    toc_label: str,
    expected_toc: dict[str, str],
    routes: dict[str, str],
    site_url: str,
    source_links=None,
    anchors=None,
    normalize_route=None,
):
    inspection = PDFLinkInspection(
        path,
        toc_label,
        expected_toc,
        routes,
        site_url,
        set(expected_toc) | set(source_links or {}) if anchors is None else anchors,
    )
    inspection.normalize_route = normalize_route or (
        lambda url: urlsplit(url).path.rstrip("/") + "/"
    )
    for name, destination in inspection.named.items():
        error = _destination_error(destination.dest_array, inspection.named, inspection.page_refs)
        if error:
            inspection.issues.append(f"Named destination {name!r}: {error}")
    for index, page in enumerate(inspection.reader.pages):
        inspection.scan_page(index, page)
    for anchor, label in sorted(expected_toc.items()):
        actual = " ".join(inspection.toc_labels.get(anchor, [])).split()
        expected = label.split()
        if not any(
            actual[index : index + len(expected)] == expected for index in range(len(actual))
        ):
            inspection.issues.append(
                f"Table of contents: link labelled {label!r} "
                f"to {anchor!r} is missing or points elsewhere"
            )
    if source_links:
        inspection.check_source_links(source_links)
    outline_count = _outline_findings(
        inspection.reader.outline, inspection.reader, inspection.issues
    )
    return (
        inspection.issues,
        inspection.sources,
        len(inspection.named),
        inspection.internal_count,
        outline_count,
    )


def _external_worker(url, results):
    category, detail = check_external(url)
    if category == "likely broken" and detail.startswith("HTTP 416"):
        category = "uncertain"
        detail += "; range request was rejected"
    results.put((category, detail))


def bounded_external(url: str, timeout: float):
    results = multiprocessing.Queue()
    process = multiprocessing.Process(target=_external_worker, args=(url, results))
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join()
        result = ("uncertain", "network check exceeded its time budget")
    else:
        try:
            result = results.get(timeout=1)
        except queue.Empty:
            result = ("unavailable", "network checker produced no evidence")
    results.close()
    results.join_thread()
    return result


def audit(path: Path, config: Path, check_network=True, checker=bounded_external):
    label, toc, routes, site_url, source_links, anchors, normalize_route = paper_expectations(
        config
    )
    issues, sources, named_count, internal_count, outline_count = inspect_pdf(
        path, label, toc, routes, site_url, source_links, anchors, normalize_route
    )
    results = {}
    deadline = time.monotonic() + 120
    for url in sorted(sources):
        remaining = deadline - time.monotonic()
        if not check_network:
            results[url] = ("unavailable", "network check not requested")
        elif remaining <= 0:
            results[url] = ("unavailable", "overall network time budget exhausted before check")
        else:
            try:
                results[url] = checker(url, min(20, remaining))
            except Exception as error:
                results[url] = ("unavailable", f"network checker failed: {error}")
    broken = [url for url, (category, _) in results.items() if category == "likely broken"]
    uncertain = [url for url, (category, _) in results.items() if category == "uncertain"]
    unavailable = [
        url
        for url, (category, _) in results.items()
        if category not in {"healthy", "likely broken", "uncertain"}
    ]
    state = (
        "failed"
        if issues or broken
        else "incomplete"
        if not check_network or unavailable
        else "advisory"
        if uncertain
        else "passed"
    )
    lines = [
        "# Paper: Link Integrity",
        "",
        f"**Outcome: {state}**",
        "",
        f"- Named destinations: {named_count}",
        f"- Internal link annotations: {internal_count}",
        f"- Bookmarks: {outline_count}",
        f"- Unique HTTP(S) destinations: {len(sources)}",
        f"- Internal findings: {len(issues)}",
        f"- Likely broken external destinations: {len(broken)}",
        f"- Uncertain external destinations: {len(uncertain)}",
        f"- Unavailable external checks: {len(unavailable)}",
        "- External URL fragments are not checked.",
        "",
    ]
    findings = list(issues)
    for url, (category, detail) in results.items():
        if category != "healthy":
            findings.append(f"{category}: `{url}` ({', '.join(sorted(sources[url]))}) — {detail}")
    if not check_network and not sources:
        findings.append("External network verification was skipped.")
    lines.extend(f"- {finding}" for finding in findings)
    if issues or broken:
        lines.append(
            "- Next step: inspect the cited PDF page and destination; correct the source or generation and rerun."
        )
    if uncertain or unavailable or not check_network:
        lines.append(
            "- Next step: retry from an unrestricted network or check the destinations manually; do not treat unavailable evidence as a pass."
        )
    return "\n".join(lines) + "\n", state, findings


def write_json_report(path, state, findings):
    bounded = [item[:2000] for item in findings[:99]]
    if len(findings) > 99:
        bounded.append("Additional findings omitted; see the full human-readable report.")
    if (
        state not in {"passed", "failed", "incomplete", "advisory"}
        or any(not isinstance(item, str) or not item or len(item) > 2000 for item in bounded)
        or (state == "passed" and bounded)
        or (state != "passed" and not bounded)
    ):
        raise ValueError("Invalid link report")
    evidence = {"status": state, "findings": bounded}
    path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
    if json.loads(path.read_text(encoding="utf-8")) != evidence:
        raise ValueError("Link report could not be verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "scripts/plop.paper.config.yaml")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--skip-external", action="store_true")
    args = parser.parse_args()
    try:
        report, state, findings = audit(args.pdf, args.config, not args.skip_external)
    except Exception as error:
        findings = [f"Audit unavailable: {error}; inspect the job and rerun."]
        report, state = (
            f"# Paper: Link Integrity\n\n**Outcome: incomplete**\n\n- {findings[0]}\n",
            "incomplete",
        )
        exit_code = 2
    else:
        exit_code = {"passed": 0, "failed": 1, "incomplete": 2, "advisory": 3}[state]
    if args.status_file:
        args.status_file.write_text(state, encoding="utf-8")
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    if args.json_report:
        write_json_report(args.json_report, state, findings)
    print(report, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
