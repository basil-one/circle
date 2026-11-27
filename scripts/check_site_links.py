#!/usr/bin/env python3
"""Check links in a generated Jekyll site without failing on external responses."""

from __future__ import annotations

import argparse
import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urldefrag, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

LINK_ATTRIBUTES = ("href", "src", "poster", "data")
UNCERTAIN_STATUSES = {401, 403, 407, 408, 425, 429}


class UnsafeRedirect(Exception):
    pass


class PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        destination = urljoin(request.full_url, new_url)
        is_public, reason = public_destination(destination)
        if not is_public:
            raise UnsafeRedirect(f"redirect to {destination} blocked: {reason}")
        return super().redirect_request(request, file, code, message, headers, destination)


@dataclass
class Page:
    path: Path
    url: str
    links: list[str] = field(default_factory=list)
    identifiers: set[str] = field(default_factory=set)
    base_href: str | None = None


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.identifiers: set[str] = set()
        self.base_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        for name in ("id", "name"):
            if attributes.get(name):
                self.identifiers.add(attributes[name] or "")

        if tag == "base" and self.base_href is None:
            self.base_href = attributes.get("href")

        for name in LINK_ATTRIBUTES:
            value = attributes.get(name)
            if value:
                self.links.append(value.strip())

        srcset = attributes.get("srcset")
        if srcset:
            self.links.extend(srcset_urls(srcset))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def srcset_urls(value: str) -> list[str]:
    urls = []
    position = 0
    while position < len(value):
        while position < len(value) and (value[position].isspace() or value[position] == ","):
            position += 1
        start = position
        while position < len(value) and not value[position].isspace():
            position += 1
        candidate = value[start:position].rstrip(",")
        if candidate:
            urls.append(candidate)
        while position < len(value) and value[position] != ",":
            position += 1
        position += position < len(value)
    return urls


def parse_config(config_path: Path) -> tuple[str, str, dict[str, tuple[str, str]]]:
    text = config_path.read_text(encoding="utf-8")

    def config_value(key: str, default: str) -> str:
        match = re.search(
            rf"^{re.escape(key)}:\s*(?:\"([^\"]*)\"|'([^']*)'|([^#\s]+))",
            text,
            re.MULTILINE,
        )
        if not match:
            return default
        return next((value for value in match.groups() if value is not None), default)

    site_url = config_value("url", "")
    baseurl = config_value("baseurl", "").rstrip("/")
    action_links: dict[str, tuple[str, str]] = {}
    section = re.search(r"^action_links:\s*$([\s\S]*?)(?=^\S|\Z)", text, re.MULTILINE)
    if section:
        for match in re.finditer(
            r"^\s+([a-zA-Z0-9_]+):\s*[\"'](https?://[^\"']+)[\"']\s*$",
            section.group(1),
            re.MULTILINE,
        ):
            key, value = match.groups()
            action_links[normalize_url(value)] = (key, value)
    return site_url, baseurl, action_links


def normalize_url(url: str) -> str:
    parts = urlsplit(urldefrag(url).url)
    return parts._replace(path=parts.path.rstrip("/") or "/").geturl()


def page_url(path: Path, root: Path, baseurl: str) -> str:
    relative = path.relative_to(root).as_posix()
    if relative == "index.html":
        route = "/"
    elif relative.endswith("/index.html"):
        route = "/" + relative[: -len("index.html")]
    else:
        route = "/" + relative
    return baseurl + route


def load_pages(root: Path, baseurl: str) -> list[Page]:
    pages = []
    for path in sorted((*root.rglob("*.html"), *root.rglob("*.htm"))):
        parser = PageParser()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        pages.append(
            Page(
                path=path,
                url=page_url(path, root, baseurl),
                links=parser.links,
                identifiers=parser.identifiers,
                base_href=parser.base_href,
            )
        )
    return pages


def same_site(url: str, site_url: str) -> bool:
    target, site = urlsplit(url), urlsplit(site_url)
    if not site.hostname or target.hostname != site.hostname:
        return False
    try:
        target_port = target.port or (443 if target.scheme == "https" else 80)
        site_port = site.port or (443 if site.scheme == "https" else 80)
    except ValueError:
        return False
    return target_port == site_port


def local_target(url: str, baseurl: str, root: Path) -> tuple[Path | None, str]:
    parts = urlsplit(url)
    path = unquote(parts.path)
    if baseurl and (path == baseurl or path.startswith(baseurl + "/")):
        path = path[len(baseurl) :] or "/"
    relative = path.lstrip("/")
    try:
        requested = (root / relative).resolve()
        root_resolved = root.resolve()
        if not requested.is_relative_to(root_resolved):
            return None, "path resolves outside the generated site"

        candidates = [requested]
        if path.endswith("/"):
            candidates = [requested / "index.html"]
        elif not requested.suffix:
            candidates.extend((Path(str(requested) + ".html"), requested / "index.html"))
        for candidate in candidates:
            if candidate.resolve().is_relative_to(root_resolved) and candidate.is_file():
                return candidate.resolve(), ""
    except (OSError, ValueError):
        pass
    return None, "target does not exist in the generated site"


def broken_internal_links(
    root: Path, pages: list[Page], site_url: str, baseurl: str
) -> tuple[list[tuple[str, str, str]], int]:
    issues: list[tuple[str, str, str]] = []
    checked = 0
    page_by_path = {page.path.resolve(): page for page in pages}
    origin = urlsplit(site_url)

    for page in pages:
        base = urljoin(f"{origin.scheme}://{origin.netloc}{page.url}", page.base_href or "")
        for raw_url in page.links:
            try:
                absolute = urljoin(base, raw_url)
                parts = urlsplit(absolute)
            except ValueError as error:
                checked += 1
                issues.append((page.url, raw_url, f"invalid URL: {error}"))
                continue
            if parts.scheme not in {"http", "https"} or not same_site(absolute, site_url):
                continue
            checked += 1
            target, error = local_target(absolute, baseurl, root)
            if target is None:
                issues.append((page.url, raw_url, error))
                continue
            fragment = unquote(parts.fragment)
            target_page = page_by_path.get(target)
            if fragment and target_page and fragment not in target_page.identifiers:
                issues.append((page.url, raw_url, f"fragment '#{fragment}' not found in target"))

    return issues, checked


def external_category(status: int) -> str:
    if 200 <= status < 400:
        return "healthy"
    if status in UNCERTAIN_STATUSES or status >= 500:
        return "uncertain"
    return "likely broken"


def public_destination(url: str) -> tuple[bool, str]:
    parts = urlsplit(url)
    if not parts.hostname or parts.username or parts.password:
        return False, "invalid destination authority; request skipped"
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = {
            ipaddress.ip_address(result[4][0])
            for result in socket.getaddrinfo(parts.hostname, port)
        }
    except OSError:
        return False, "DNS lookup failed; request skipped"
    except ValueError:
        return False, "invalid destination authority; request skipped"
    if addresses and any(not address.is_global for address in addresses):
        return False, "destination resolves to a non-public address; request skipped"
    return True, ""


def check_external(url: str, timeout: float = 8.0, retries: int = 1) -> tuple[str, str]:
    request_url = urldefrag(url).url
    is_public, reason = public_destination(request_url)
    if not is_public:
        return "uncertain", reason

    request = Request(
        request_url,
        headers={
            "Range": "bytes=0-0",
            "User-Agent": "Circle site link audit (GitHub Actions)",
        },
    )
    opener = build_opener(PublicRedirectHandler())
    for attempt in range(retries + 1):
        try:
            with opener.open(request, timeout=timeout) as response:
                status = response.status
                final_url = response.geturl()
                detail = f"HTTP {status}"
                if final_url != request_url:
                    detail += f" after redirect to {final_url}"
                if status >= 500 and attempt < retries:
                    time.sleep(0.5)
                    continue
                return external_category(status), detail
        except HTTPError as error:
            if error.code >= 500 and attempt < retries:
                time.sleep(0.5)
                continue
            return external_category(error.code), f"HTTP {error.code} {error.reason}"
        except UnsafeRedirect as error:
            return "uncertain", str(error)
        except (URLError, TimeoutError, socket.timeout, OSError) as error:
            if attempt < retries:
                time.sleep(0.5)
                continue
            reason = getattr(error, "reason", error)
            return "uncertain", f"request error: {reason}"
    return "uncertain", "request failed after retries"


def _collect_external_sources(pages: list[Page], site_url: str) -> dict[str, set[str]]:
    external_sources: dict[str, set[str]] = {}
    for page in pages:
        origin = urlsplit(site_url)
        base = urljoin(f"{origin.scheme}://{origin.netloc}{page.url}", page.base_href or "")
        for raw_url in page.links:
            try:
                absolute = urljoin(base, raw_url)
            except ValueError:
                continue
            try:
                parts = urlsplit(absolute)
            except ValueError:
                continue
            if parts.scheme in {"http", "https"} and not same_site(absolute, site_url):
                external_sources.setdefault(urldefrag(absolute).url, set()).add(page.url)
    return external_sources


def _check_external_destinations(
    external_sources: dict[str, set[str]], check_externals: bool, external_delay: float
) -> dict[str, tuple[str, str]]:
    external_results: dict[str, tuple[str, str]] = {}
    if check_externals:
        for index, url in enumerate(sorted(external_sources)):
            if index:
                time.sleep(external_delay)
            external_results[url] = check_external(url)
    return external_results


def _external_findings(
    external_sources: dict[str, set[str]],
    external_results: dict[str, tuple[str, str]],
    action_links: dict[str, tuple[str, str]],
) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {"likely broken": [], "uncertain": []}
    for url, (category, detail) in external_results.items():
        if category == "healthy":
            continue
        action = action_links.get(normalize_url(url))
        label = f" **ACTION: `{action[0]}`**" if action else ""
        for source in sorted(external_sources[url]):
            findings[category].append(
                f"- **Source:** `{source}` → **Destination:** `{url}` — {detail}{label}"
            )
    return findings


def _append_action_destinations(
    lines: list[str],
    action_links: dict[str, tuple[str, str]],
    external_sources: dict[str, set[str]],
    external_results: dict[str, tuple[str, str]],
    check_externals: bool,
) -> None:
    if not action_links:
        lines.append("None configured.")
    for action_url, (action, configured_url) in sorted(
        action_links.items(), key=lambda item: item[1][0]
    ):
        destination = next(
            (url for url in external_sources if normalize_url(url) == action_url),
            None,
        )
        if destination is None:
            status = "not found in generated HTML"
            sources = ""
        elif not check_externals:
            status = "not checked"
            sources = ""
        else:
            category, detail = external_results[destination]
            status = f"{category}: {detail}"
            sources = f" — source pages: {', '.join(sorted(external_sources[destination]))}"
        lines.append(f"- **{action}**: `{configured_url}` — {status}{sources}")


def _append_external_findings(lines: list[str], findings: dict[str, list[str]]) -> None:
    for category, title in (
        ("likely broken", "Likely broken external links"),
        ("uncertain", "Uncertain external responses"),
    ):
        lines.extend(("", f"## {title}", ""))
        lines.extend(findings[category] or ["None."])


def report_for_site(
    root: Path,
    config_path: Path,
    check_externals: bool = True,
    external_delay: float = 0.25,
) -> tuple[str, int, int, int]:
    site_url, baseurl, action_links = parse_config(config_path)
    if not site_url:
        raise ValueError(f"{config_path} must define the site's url")

    pages = load_pages(root, baseurl)
    if not pages:
        raise ValueError(f"No generated HTML pages found in {root}")

    internal, internal_count = broken_internal_links(root, pages, site_url, baseurl)
    external_sources = _collect_external_sources(pages, site_url)
    external_results = _check_external_destinations(
        external_sources, check_externals, external_delay
    )
    findings = _external_findings(external_sources, external_results, action_links)

    lines = [
        "# Generated-site link audit",
        "",
        f"- Generated HTML pages scanned: {len(pages)}",
        f"- Internal links/assets checked: {internal_count}",
        f"- Broken internal links/assets: {len(internal)}",
        f"- Unique external destinations: {len(external_sources)}",
        f"- External destinations checked: {len(external_results)}",
        "",
        "Uncertain external responses never fail. Likely broken external links fail only "
        "with `--fail-on-likely-external`; internal failures always fail.",
        "",
        "## Broken internal links and assets",
        "",
    ]
    if internal:
        lines.extend(
            f"- **Source:** `{source}` → **Destination:** `{destination}` — {error}"
            for source, destination, error in internal
        )
    else:
        lines.append("None.")

    lines.extend(("", "## Key visitor destinations", ""))
    _append_action_destinations(
        lines, action_links, external_sources, external_results, check_externals
    )
    _append_external_findings(lines, findings)

    likely_broken_count = sum(
        category == "likely broken" for category, _ in external_results.values()
    )
    uncertain_count = sum(
        category == "uncertain" for category, _ in external_results.values()
    )
    return "\n".join(lines) + "\n", len(internal), likely_broken_count, uncertain_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=Path("_site"))
    parser.add_argument("--config", type=Path, default=Path("_config.yml"))
    parser.add_argument("--report", type=Path, default=Path("link-audit-report.md"))
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--skip-external", action="store_true")
    parser.add_argument("--fail-on-likely-external", action="store_true")
    args = parser.parse_args()

    try:
        report, internal_errors, likely_external_errors, uncertain_external_errors = report_for_site(
            args.site_dir, args.config, check_externals=not args.skip_external
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))

    args.report.write_text(report, encoding="utf-8")
    if args.status_file:
        status = (
            "failure" if internal_errors or likely_external_errors
            else "advisory" if uncertain_external_errors
            else "success"
        )
        args.status_file.write_text(f"{status}\n", encoding="utf-8")
    print(report, end="")
    return 1 if internal_errors or (args.fail_on_likely_external and likely_external_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
