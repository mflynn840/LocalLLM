import socket
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.web import PublicOnlyResolver, _validate_public_url


class ValidatePublicUrlTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_non_http_schemes(self):
        with self.assertRaises(ValueError):
            _validate_public_url("file:///etc/passwd")

    def test_rejects_localhost_without_dns(self):
        with self.assertRaises(ValueError):
            _validate_public_url("http://localhost/page")

    def test_rejects_private_ip(self):
        with self.assertRaises(ValueError):
            _validate_public_url("http://127.0.0.1/page")

    def test_rejects_nonstandard_port(self):
        with self.assertRaises(ValueError):
            _validate_public_url("https://example.com:8080/page")

    @patch("app.web.asyncio.get_running_loop")
    async def test_resolver_accepts_public_address(self, get_running_loop):
        loop = Mock()
        loop.getaddrinfo = AsyncMock(
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        )
        get_running_loop.return_value = loop
        result = await PublicOnlyResolver().resolve("example.com", 443)
        self.assertEqual(result[0]["host"], "93.184.216.34")

    @patch("app.web.asyncio.get_running_loop")
    async def test_resolver_rejects_mixed_public_and_private_addresses(self, get_running_loop):
        loop = Mock()
        loop.getaddrinfo = AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.5", 443)),
            ]
        )
        get_running_loop.return_value = loop
        with self.assertRaises(OSError):
            await PublicOnlyResolver().resolve("example.com", 443)

    def test_accepts_public_host_syntax(self):
        self.assertEqual(
            _validate_public_url("https://example.com/article"),
            "https://example.com/article",
        )


if __name__ == "__main__":
    unittest.main()
