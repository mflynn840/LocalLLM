import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from bs4 import BeautifulSoup

MAX_RESPONSE_BYTES = 1_000_000
MAX_PAGE_CHARS = 20_000
USER_AGENT = "Local-AI-Web-Reader/1.0"


class PublicOnlyResolver(AbstractResolver):
    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict[str, object]]:
        loop = asyncio.get_running_loop()
        try:
            results = await loop.getaddrinfo(
                host,
                port,
                family=family,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise OSError(f"Could not resolve public host: {host}") from exc

        addresses = {
            ipaddress.ip_address(result[4][0].split("%", 1)[0])
            for result in results
        }
        if not addresses or any(not address.is_global for address in addresses):
            raise OSError("URLs resolving to non-public IP addresses are not allowed.")
        return [
            {
                "hostname": host,
                "host": result[4][0],
                "port": result[4][1],
                "family": result[0],
                "proto": result[2],
                "flags": 0,
            }
            for result in results
        ]

    async def close(self) -> None:
        return None


def _validate_public_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP or HTTPS URLs are allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing user information are not allowed.")
    allowed_port = 443 if parsed.scheme == "https" else 80
    if parsed.port is not None and parsed.port != allowed_port:
        raise ValueError("Only standard HTTP and HTTPS ports are allowed.")

    host = parsed.hostname.rstrip(".").lower()
    if (
        host in {"localhost", "localhost.localdomain"}
        or "." not in host
        or host.endswith((".localhost", ".local", ".internal", ".test"))
    ):
        raise ValueError("Local and private network hosts are not allowed.")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return url
    if not address.is_global:
        raise ValueError("URLs resolving to non-public IP addresses are not allowed.")
    return url


async def fetch_page(url: str) -> dict[str, str]:
    current_url = url
    for redirect_count in range(4):
        _validate_public_url(current_url)
        connector = aiohttp.TCPConnector(
            resolver=PublicOnlyResolver(),
            use_dns_cache=False,
            limit_per_host=2,
        )
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"},
            trust_env=False,
        ) as client:
            async with client.get(current_url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirect_count == 3:
                        raise ValueError("The page redirected too many times.")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if not any(
                    kind in content_type
                    for kind in ("text/html", "application/xhtml+xml", "text/plain")
                ):
                    raise ValueError(f"Unsupported page content type: {content_type or 'unknown'}.")
                chunks = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    chunks.extend(chunk)
                    if len(chunks) > MAX_RESPONSE_BYTES:
                        raise ValueError("Page response exceeded the 1 MB size limit.")
                raw = bytes(chunks)
                text = raw.decode(response.charset or "utf-8", errors="replace")
                break
    else:
        raise ValueError("The page redirected too many times.")

    if "text/html" in content_type or "application/xhtml+xml" in content_type:
        soup = BeautifulSoup(text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else current_url
        for node in soup(
            ["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]
        ):
            node.decompose()
        text = soup.get_text(" ", strip=True)
    else:
        title = current_url

    cleaned = " ".join(text.split())
    return {
        "url": current_url,
        "title": title[:300],
        "text": cleaned[:MAX_PAGE_CHARS],
    }
