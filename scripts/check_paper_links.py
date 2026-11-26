#!/usr/bin/env python3
"""Audit link annotations and navigation in an already generated paper PDF."""

from __future__ import annotations

import argparse
import importlib.util
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
    used = {abstract}
    toc = {abstract: abstract_label} if include_abstract else {}
    if intro_text:
        intro = builder._reserve_pdf_anchor(intro_anchor, used)
        if include_intro:
            toc[intro] = intro_label
    core, appendix, routes = [], [], {}
    sections = builder._plan_pdf_sections(data["sections"], used, core, appendix, routes)
    toc.update((item["anchor"], item["label"]) for item in core + appendix)
    for key in ("discussion", "conclusion"):
        section = data[key]
        anchor = builder._reserve_pdf_anchor(
            builder._latex_id(section["anchor"], default=key), used
        )
        if section["title"]:
            toc[anchor] = section["title"]
    if back_text:
        anchor = builder._reserve_pdf_anchor(back_anchor, used)
        if include_back:
            toc[anchor] = back_label
    source_links = _source_expectations(builder, sections, routes)
    return framing["toc_label"], toc, routes, builder._get_root_url(), source_links


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


def _route_path(path):
    path = path.rstrip("/")
    if path.endswith("/index.html"):
        path = path[: -len("/index.html")]
    elif path.endswith("index.html"):
        path = path[: -len("index.html")]
    elif path.endswith(".html"):
        path = path[: -len(".html")]
    return path.rstrip("/") + "/"


class PDFLinkInspection:
    def __init__(self, path, toc_label, expected_toc, routes, site_url):
        self.reader = PdfReader(path, strict=True)
        if not self.reader.pages:
            raise ValueError("PDF has no pages")
        self.named = self.reader.named_destinations
        self.page_refs = {
            (page.indirect_reference.idnum, page.indirect_reference.generation)
            for page in self.reader.pages
            if page.indirect_reference is not None
        }
        self.issues, self.sources, self.toc_targets = [], {}, set()
        self.expected_toc, self.routes, self.site_url = expected_toc, routes, site_url
        self.internal_count, self.page_links = 0, []
        toc_pages = [
            i for i, page in enumerate(self.reader.pages) if toc_label in page.extract_text()
        ]
        if len(toc_pages) != 1:
            self.issues.append(
                f"Table of contents label {toc_label!r} found on {len(toc_pages)} pages"
            )
        self.toc_start = toc_pages[0] if len(toc_pages) == 1 else -1
        self.words = _toc_words(path, self.toc_start + 1) if self.toc_start >= 0 else []

    def _internal(self, annotation, action, page, index, observed):
        destination = annotation.get("/Dest") if "/Dest" in annotation else action.get("/D")
        self.internal_count += 1
        error = _destination_error(destination, self.named, self.page_refs)
        if error:
            self.issues.append(f"Page {index + 1}: {destination!r} — {error}")
        if isinstance(destination, str):
            observed.add(("internal", str(destination)))
            if index == self.toc_start and str(destination) in self.expected_toc:
                label = _annotation_label(annotation, page, self.words)
                if label == self.expected_toc[str(destination)]:
                    self.toc_targets.add(str(destination))

    def _uri(self, action, location, observed):
        url = str(action.get("/URI", ""))
        try:
            parts = urlsplit(url)
            if parts.scheme in {"http", "https"} and parts.hostname:
                observed.add(("external", url))
                self.sources.setdefault(urldefrag(url).url, set()).add(location)
                if same_site(url, self.site_url):
                    normalized = _route_path(parts.path)
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
        positions = {
            anchor: self.reader.get_destination_page_number(self.named[anchor])
            for anchor in self.expected_toc
            if anchor in self.named
        }
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
):
    inspection = PDFLinkInspection(path, toc_label, expected_toc, routes, site_url)
    for name, destination in inspection.named.items():
        error = _destination_error(destination.dest_array, inspection.named, inspection.page_refs)
        if error:
            inspection.issues.append(f"Named destination {name!r}: {error}")
    for index, page in enumerate(inspection.reader.pages):
        inspection.scan_page(index, page)
    for anchor in sorted(expected_toc.keys() - inspection.toc_targets):
        inspection.issues.append(
            f"Table of contents: link labelled {expected_toc[anchor]!r} "
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
            result = ("uncertain", "network checker produced no evidence")
    results.close()
    results.join_thread()
    return result


def audit(path: Path, config: Path, check_network=True, checker=bounded_external):
    label, toc, routes, site_url, source_links = paper_expectations(config)
    issues, sources, named_count, internal_count, outline_count = inspect_pdf(
        path, label, toc, routes, site_url, source_links
    )
    results = {}
    deadline = time.monotonic() + 120
    for url in sorted(sources):
        remaining = deadline - time.monotonic()
        if not check_network:
            results[url] = ("uncertain", "network check not requested")
        elif remaining <= 0:
            results[url] = ("uncertain", "overall network time budget exhausted")
        else:
            results[url] = checker(url, min(20, remaining))
    broken = [url for url, (category, _) in results.items() if category == "likely broken"]
    uncertain = [url for url, (category, _) in results.items() if category == "uncertain"]
    state = "failed" if issues or broken else "incomplete" if uncertain else "passed"
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
        "",
    ]
    lines.extend(f"- {issue}" for issue in issues)
    for url, (category, detail) in results.items():
        if category != "healthy":
            lines.append(f"- {category}: `{url}` ({', '.join(sorted(sources[url]))}) — {detail}")
    return "\n".join(lines) + "\n", state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "scripts/plop.paper.config.yaml")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--skip-external", action="store_true")
    args = parser.parse_args()
    try:
        report, state = audit(args.pdf, args.config, not args.skip_external)
    except Exception as error:
        report, state = (
            f"# Paper: Link Integrity\n\n**Outcome: incomplete**\n\n- Audit unavailable: {error}\n",
            "incomplete",
        )
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    print(report, end="")
    return {"passed": 0, "failed": 1, "incomplete": 2}[state]


if __name__ == "__main__":
    raise SystemExit(main())
