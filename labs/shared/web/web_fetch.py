from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


MAX_WEB_BYTES = 1_000_000
MAX_WEB_TEXT_CHARACTERS = 100_000
MAX_REDIRECTS = 3
ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class FetchedWebPage:
    url: str
    title: str
    text: str


_FROZEN_WEB_PAGES: ContextVar[dict[str, FetchedWebPage | Exception] | None] = ContextVar(
    "frozen_web_pages", default=None
)


@contextmanager
def frozen_web_pages(pages: dict[str, FetchedWebPage | Exception]) -> Iterator[None]:
    token = _FROZEN_WEB_PAGES.set(pages)
    try:
        yield
    finally:
        _FROZEN_WEB_PAGES.reset(token)


def normalize_web_url(value: str) -> str:
    raw = value.strip()
    if not raw or len(raw) > 2_048:
        raise ValueError("Enter one HTTP or HTTPS URL no longer than 2048 characters.")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Web sources must use a complete HTTP or HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError("Web source URLs cannot contain credentials.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Web source URL has an invalid port.") from exc
    if port not in {None, 80, 443}:
        raise ValueError("Web source URLs may use only ports 80 or 443.")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


def validate_public_web_url(
    value: str,
    *,
    resolver: Callable[..., list] = socket.getaddrinfo,
) -> str:
    url, _ = resolve_public_web_url(value, resolver=resolver)
    return url


def resolve_public_web_url(
    value: str,
    *,
    resolver: Callable[..., list] = socket.getaddrinfo,
) -> tuple[str, list[IPAddress]]:
    url = normalize_web_url(value)
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise ValueError("Local and private-network URLs are not allowed.")

    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            resolved = resolver(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("The web source hostname could not be resolved.") from exc
        addresses = []
        for result in resolved:
            address = ipaddress.ip_address(result[4][0])
            if address not in addresses:
                addresses.append(address)

    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Local and private-network URLs are not allowed.")
    return url, addresses


def pinned_request_url(url: str, address: IPAddress) -> tuple[str, str, str]:
    parsed = urlsplit(url)
    address_host = f"[{address}]" if address.version == 6 else str(address)
    netloc = address_host if parsed.port is None else f"{address_host}:{parsed.port}"
    request_url = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))
    return request_url, parsed.netloc, parsed.hostname or ""


def fetch_web_page(
    value: str,
    *,
    client: httpx.Client | None = None,
    resolver: Callable[..., list] = socket.getaddrinfo,
) -> FetchedWebPage:
    current_url = normalize_web_url(value)
    frozen = _FROZEN_WEB_PAGES.get()
    if frozen and current_url in frozen:
        cached = frozen[current_url]
        if isinstance(cached, Exception):
            raise cached
        return cached

    owned_client = client is None
    http_client = client or httpx.Client(
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=False,
        limits=httpx.Limits(max_keepalive_connections=0),
        trust_env=False,
        headers={"User-Agent": "HarnessEngineeringLab/1.0 (+local educational fetcher)"},
    )
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            current_url, addresses = resolve_public_web_url(current_url, resolver=resolver)
            request_url, host_header, sni_hostname = pinned_request_url(current_url, addresses[0])
            with http_client.stream(
                "GET",
                request_url,
                headers={"Host": host_header, "Connection": "close"},
                extensions={"sni_hostname": sni_hostname},
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location or redirect_count == MAX_REDIRECTS:
                        raise ValueError("The webpage redirected too many times.")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise ValueError("The URL must return an HTML or plain-text webpage.")
                declared_size = response.headers.get("content-length")
                if declared_size and declared_size.isdigit() and int(declared_size) > MAX_WEB_BYTES:
                    raise ValueError("The webpage exceeds the 1 MB research limit.")

                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_WEB_BYTES:
                        raise ValueError("The webpage exceeds the 1 MB research limit.")
                encoding = response.encoding or "utf-8"
                raw_text = bytes(body).decode(encoding, errors="replace")

            if content_type == "text/plain":
                title = urlsplit(current_url).hostname or "Web source"
                text = normalize_page_text(raw_text)
            else:
                parser = ReadableHTMLParser()
                parser.feed(raw_text)
                title = parser.title or urlsplit(current_url).hostname or "Web source"
                text = normalize_page_text("\n".join(parser.text_parts))
            if not text:
                raise ValueError("The webpage did not contain readable text.")
            return FetchedWebPage(
                url=current_url,
                title=title[:120],
                text=text[:MAX_WEB_TEXT_CHARACTERS],
            )
    finally:
        if owned_client:
            http_client.close()
    raise ValueError("The webpage could not be fetched.")


def normalize_page_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.replace("\x00", "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


class ReadableHTMLParser(HTMLParser):
    BLOCKED_TAGS = {"script", "style", "noscript", "svg"}
    BREAK_TAGS = {"article", "br", "div", "h1", "h2", "h3", "h4", "li", "main", "p", "section", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocked_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    @property
    def title(self) -> str:
        return normalize_page_text(" ".join(self.title_parts))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in self.BLOCKED_TAGS:
            self.blocked_depth += 1
        if lowered == "title":
            self.in_title = True
        if lowered in self.BREAK_TAGS and self.blocked_depth == 0:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self.in_title = False
        if lowered in self.BLOCKED_TAGS and self.blocked_depth:
            self.blocked_depth -= 1
        if lowered in self.BREAK_TAGS and self.blocked_depth == 0:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.blocked_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        self.text_parts.append(data)
