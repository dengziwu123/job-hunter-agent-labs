from __future__ import annotations

import socket

import httpx
import pytest

from labs.shared.web.web_fetch import fetch_web_page, normalize_web_url, validate_public_web_url


def public_resolver(host: str, port: int, *, type: int) -> list:
    assert type == socket.SOCK_STREAM
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]


def test_url_validation_accepts_public_http_and_rejects_unsafe_targets() -> None:
    assert normalize_web_url("https://example.com/jobs/42#apply") == "https://example.com/jobs/42"
    assert validate_public_web_url("https://example.com/jobs/42", resolver=public_resolver).startswith("https://")

    for value in (
        "file:///etc/passwd",
        "https://user:secret@example.com/",
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "https://example.com:8443/jobs",
    ):
        with pytest.raises(ValueError):
            validate_public_web_url(value, resolver=public_resolver)


def test_fetch_web_page_extracts_readable_html_without_script_content() -> None:
    html = b"""
        <html><head><title>AI Tools Engineer</title><style>.hidden{}</style></head>
        <body><main><h1>AI Tools Engineer</h1><p>Python API experience required.</p>
        <script>stealSecrets()</script><p>Build developer workflows.</p></main></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://93.184.216.34/jobs/42"
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=html)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        page = fetch_web_page(
            "https://example.com/jobs/42",
            client=client,
            resolver=public_resolver,
        )

    assert page.title == "AI Tools Engineer"
    assert page.url == "https://example.com/jobs/42"
    assert "Python API experience required." in page.text
    assert "Build developer workflows." in page.text
    assert "stealSecrets" not in page.text
    assert ".hidden" not in page.text


def test_fetch_web_page_pins_the_validated_address_against_dns_rebinding() -> None:
    resolver_calls = 0

    def rebinding_resolver(host: str, port: int, *, type: int) -> list:
        nonlocal resolver_calls
        resolver_calls += 1
        address = "93.184.216.34" if resolver_calls == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="Public job page")

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        page = fetch_web_page("https://example.com/jobs", client=client, resolver=rebinding_resolver)

    assert page.text == "Public job page"
    assert resolver_calls == 1


def test_fetch_web_page_revalidates_redirect_and_enforces_size_limit() -> None:
    def redirect_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    with httpx.Client(transport=httpx.MockTransport(redirect_handler), follow_redirects=False) as client:
        with pytest.raises(ValueError, match="private-network"):
            fetch_web_page("https://example.com/jobs", client=client, resolver=public_resolver)

    def oversized_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "1000001"},
            content=b"<p>too large</p>",
        )

    with httpx.Client(transport=httpx.MockTransport(oversized_handler), follow_redirects=False) as client:
        with pytest.raises(ValueError, match="1 MB"):
            fetch_web_page("https://example.com/jobs", client=client, resolver=public_resolver)
