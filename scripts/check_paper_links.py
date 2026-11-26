#!/usr/bin/env python3
"""Audit link annotations and navigation in an already generated paper PDF."""

from __future__ import annotations

import argparse
import importlib.util
import multiprocessing
import queue
import re
import time
from pathlib import Path
from urllib.parse import urldefrag, urlsplit

from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

from check_site_links import check_external, same_site

ROOT = Path(__file__).resolve().parents[1]


def paper_expectations(config: Path):
    spec = importlib.util.spec_from_file_location("build_pdf", ROOT / "scripts/build-pdf.py")
    builder_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder_module)
    builder = builder_module.CirclePDFBuilder(str(config), str(ROOT))
    data = builder.config
    framing, body = data["framing"], data["body"]
    used = {builder._latex_id(framing["abstract_anchor"], default="abstract")}
    toc = set()
    if framing["include_abstract_in_toc"]:
        toc.update(used)
    if body["introduction_file"]:
        intro = builder._reserve_pdf_anchor(body["introduction_anchor"], used)
        if body["include_introduction_in_toc"]:
            toc.add(intro)
    core, appendix, routes = [], [], {}
    sections = builder._plan_pdf_sections(data["sections"], used, core, appendix, routes)
    toc.update(item["anchor"] for item in core + appendix)
    for key in ("discussion", "conclusion"):
        section = data[key]
        anchor = builder._reserve_pdf_anchor(
            builder._latex_id(section["anchor"], default=key), used
        )
        if section["title"]:
            toc.add(anchor)
    if body["back_matter_file"]:
        anchor = builder._reserve_pdf_anchor(body["back_matter_anchor"], used)
        if body["include_back_matter_in_toc"]:
            toc.add(anchor)
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
    return framing["toc_label"], toc, routes, builder._get_root_url(), source_links


def _page_reference_valid(reference, page_refs: set[tuple[int, int]]) -> bool:
    return isinstance(reference, IndirectObject) and (
        reference.idnum, reference.generation
    ) in page_refs


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
        "/XYZ", "/Fit", "/FitH", "/FitV", "/FitR", "/FitB", "/FitBH", "/FitBV"
    }:
        return "invalid destination fit type"
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


def inspect_pdf(path: Path, toc_label: str, expected_toc: set[str],
                routes: dict[str, str], site_url: str, source_links=None):
    reader = PdfReader(path, strict=True)
    if not reader.pages:
        raise ValueError("PDF has no pages")
    named = reader.named_destinations
    page_refs = {
        (page.indirect_reference.idnum, page.indirect_reference.generation)
        for page in reader.pages if page.indirect_reference is not None
    }
    issues, sources, toc_targets = [], {}, set()
    for name, destination in named.items():
        error = _destination_error(destination.dest_array, named, page_refs)
        if error:
            issues.append(f"Named destination {name!r}: {error}")
    toc_pages = [i for i, page in enumerate(reader.pages) if toc_label in page.extract_text()]
    if len(toc_pages) != 1:
        issues.append(f"Table of contents label {toc_label!r} found on {len(toc_pages)} pages")
    toc_start = toc_pages[0] if len(toc_pages) == 1 else -1
    in_toc = False
    internal_count = 0
    page_links = []
    for index, page in enumerate(reader.pages):
        page_targets = set()
        observed_links = set()
        for ref in page.get("/Annots", []):
            annotation = ref.get_object()
            if annotation.get("/Subtype") != "/Link":
                continue
            location = f"Page {index + 1}"
            action = annotation.get("/A")
            action = action.get_object() if action else None
            if "/Dest" in annotation or (action and action.get("/S") == "/GoTo"):
                destination = annotation.get("/Dest") if "/Dest" in annotation else action.get("/D")
                internal_count += 1
                error = _destination_error(destination, named, page_refs)
                if error:
                    issues.append(f"{location}: {destination!r} — {error}")
                if isinstance(destination, str):
                    page_targets.add(str(destination))
                    observed_links.add(("internal", str(destination)))
            elif action and action.get("/S") == "/URI":
                url = str(action.get("/URI", ""))
                try:
                    parts = urlsplit(url)
                    if parts.scheme in {"http", "https"} and parts.hostname:
                        observed_links.add(("external", url))
                        sources.setdefault(urldefrag(url).url, set()).add(location)
                        if same_site(url, site_url):
                            normalized = parts.path.rstrip("/") + "/"
                            if normalized in routes:
                                issues.append(
                                    f"{location}: {url} should link to PDF destination "
                                    f"{routes[normalized]!r}"
                                )
                    elif parts.scheme != "mailto" or not parts.path:
                        issues.append(f"{location}: unsupported or invalid URI {url!r}")
                except ValueError as error:
                    issues.append(f"{location}: invalid URI {url!r}: {error}")
            else:
                issues.append(f"{location}: unsupported or missing link action")
        if index == toc_start:
            in_toc = True
        if in_toc:
            if index != toc_start and not (page_targets & expected_toc):
                in_toc = False
            else:
                toc_targets.update(page_targets)
        page_links.append(observed_links)
    for anchor in sorted(expected_toc - toc_targets):
        issues.append(f"Table of contents: link to {anchor!r} is missing")
    if source_links:
        positions = {
            anchor: reader.get_destination_page_number(named[anchor])
            for anchor in expected_toc if anchor in named
        }
        for anchor, targets in source_links.items():
            start = positions.get(anchor)
            if start is None or start < 0:
                issues.append(f"Source {anchor!r}: destination page is unavailable")
                continue
            end = min((pos for pos in positions.values() if pos is not None and pos > start),
                      default=len(reader.pages))
            observed = set().union(*page_links[start:end])
            for kind, target in sorted(targets - observed):
                issues.append(f"Source {anchor!r}: {kind} link to {target!r} is missing")
    outline_count = _outline_findings(reader.outline, reader, issues)
    return issues, sources, len(named), internal_count, outline_count


def _external_worker(url, results):
    results.put(check_external(url))


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
        "# Paper: Link Integrity", "", f"**Outcome: {state}**", "",
        f"- Named destinations: {named_count}",
        f"- Internal link annotations: {internal_count}",
        f"- Bookmarks: {outline_count}",
        f"- Unique HTTP(S) destinations: {len(sources)}",
        f"- Internal findings: {len(issues)}",
        f"- Likely broken external destinations: {len(broken)}",
        f"- Uncertain external destinations: {len(uncertain)}", "",
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
        report, state = f"# Paper: Link Integrity\n\n**Outcome: incomplete**\n\n- Audit unavailable: {error}\n", "incomplete"
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    print(report, end="")
    return {"passed": 0, "failed": 1, "incomplete": 2}[state]


if __name__ == "__main__":
    raise SystemExit(main())
