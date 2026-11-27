"""Shared safety and classification rules for external link checks."""

from __future__ import annotations

import ipaddress
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

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
