#!/usr/bin/env python3
"""Check generated HTML titles, canonical URLs, and indexing directives."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from .check_links import page_url, parse_config
else:
    from check_links import page_url, parse_config


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_count = 0
        self.title_parts: list[str] = []
        self.in_title = False
        self.canonicals: list[str | None] = []
        self.robots: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "title":
            self.title_count += 1
            self.in_title = True
        elif tag == "link" and "canonical" in (attributes.get("rel") or "").lower().split():
            self.canonicals.append(attributes.get("href"))
        elif tag == "meta" and (attributes.get("name") or "").lower() in {
            "robots",
            "googlebot",
        }:
            self.robots.append(
                ((attributes.get("name") or "").lower(), attributes.get("content") or "")
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)


def audit_site(site_dir: Path, config_path: Path) -> tuple[int, list[str]]:
    if not site_dir.is_dir():
        raise ValueError(f"Generated site directory does not exist: {site_dir}")

    site_url, baseurl, _ = parse_config(config_path)
    site_parts = urlsplit(site_url)
    if site_parts.scheme not in {"http", "https"} or not site_parts.netloc:
        raise ValueError(f"{config_path} must define an absolute HTTP(S) site url")

    pages = sorted((*site_dir.rglob("*.html"), *site_dir.rglob("*.htm")))
    if not pages:
        raise ValueError(f"No generated HTML pages found in {site_dir}")

    findings = []
    for path in pages:
        route = page_url(path, site_dir, baseurl)
        source = path.relative_to(site_dir).as_posix()
        parser = MetadataParser()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))

        title = "".join(parser.title_parts).strip()
        if parser.title_count != 1 or not title:
            findings.append(
                f"ERROR: {route} ({source}): expected one nonempty <title>, "
                f"found {parser.title_count}; add a page title or site title."
            )

        if len(parser.canonicals) != 1:
            findings.append(
                f"ERROR: {route} ({source}): expected exactly one canonical link, "
                f"found {len(parser.canonicals)}; check canonical_url in "
                "_layouts/default.html."
            )
        else:
            expected = f"{site_url.rstrip('/')}{route}"
            canonical = parser.canonicals[0]
            if canonical != expected:
                findings.append(
                    f"ERROR: {route} ({source}): canonical URL is {canonical!r}; "
                    f"expected {expected!r}. Check canonical_url in _layouts/default.html "
                    "against site.url, baseurl, and the page URL."
                )

        for name, content in parser.robots:
            directives = {directive.strip().lower() for directive in content.split(",")}
            prohibited = sorted(directives & {"noindex", "none"})
            if prohibited:
                findings.append(
                    f"ERROR: {route} ({source}): meta name={name!r} contains "
                    f"{', '.join(prohibited)}; remove an unintended "
                    "noindex/none directive or review the indexing intent."
                )

    return len(pages), findings


def format_summary(page_count: int, findings: list[str]) -> str:
    result = "passed" if not findings else "failed"
    finding_count = len(findings)
    noun = "finding" if finding_count == 1 else "findings"
    lines = [
        f"- Generated HTML pages audited: {page_count}",
        f"- Page titles, canonical URLs, and indexability: **{result}** ({finding_count} {noun}).",
    ]
    if findings:
        lines.extend(("", "## SEO findings", "", *(f"- {finding}" for finding in findings)))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=Path("_site"))
    parser.add_argument("--config", type=Path, default=Path("_config.yml"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    try:
        page_count, findings = audit_site(args.site_dir, args.config)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    if args.report:
        args.report.write_text(format_summary(page_count, findings), encoding="utf-8")

    if findings:
        print("\n".join(findings))
        return 1

    print(
        f"SEO audit passed: {page_count} generated HTML pages checked "
        "(nonempty titles, expected canonicals, no noindex directives)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
