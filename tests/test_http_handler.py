from __future__ import annotations

import gzip
import http.client
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse

from texting_app.server import TextingHandler
from http.server import ThreadingHTTPServer


class _FixtureHandler(TextingHandler):
    def log_message(self, _format: str, *_args) -> None:
        pass

    def do_GET(self) -> None:
        if not self._begin_request(allow_body=False):
            return
        name = Path(unquote(urlparse(self.path).path)).name
        self._serve_file(
            Path(self.server.fixture_directory) / name,
            cache_control="public, max-age=3600",
            allow_ranges=name.endswith(".mp4"),
        )

    def do_POST(self) -> None:
        if not self._begin_request(allow_body=True):
            return
        if urlparse(self.path).path == "/consume":
            self._send_json({"received": self._read_json()})
            return
        self._send_json({"error": "Not found"}, 404)


class HttpHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "asset.js").write_bytes((b"const value = 'switchboard';\n" * 300))
        (root / "media.mp4").write_bytes(bytes(range(256)))
        (root / "fax.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        self.server.fixture_directory = root
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)

    def tearDown(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp_dir.cleanup()

    def request(self, path: str, headers: dict[str, str] | None = None):
        self.connection.request("GET", path, headers=headers or {})
        response = self.connection.getresponse()
        body = response.read()
        return response, body

    def test_compression_cache_validation_keepalive_and_ranges(self) -> None:
        response, body = self.request("/asset.js?v=test", {"Accept-Encoding": "gzip"})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.version, 11)
        self.assertEqual(response.getheader("Content-Encoding"), "gzip")
        self.assertIn("Accept-Encoding", response.getheader("Vary") or "")
        self.assertTrue((response.getheader("ETag") or "").startswith("W/"))
        self.assertIn("app;dur=", response.getheader("Server-Timing") or "")
        self.assertIn(b"switchboard", gzip.decompress(body))
        first_socket = self.connection.sock

        etag = response.getheader("ETag")
        response, body = self.request("/asset.js?v=test", {"If-None-Match": etag})
        self.assertEqual(response.status, 304)
        self.assertEqual(body, b"")
        self.assertIsNone(response.getheader("Content-Length"))
        self.assertIs(self.connection.sock, first_socket)

        response, body = self.request("/media.mp4", {"Range": "bytes=2-5"})
        self.assertEqual(response.status, 206)
        self.assertEqual(body, bytes(range(2, 6)))
        self.assertEqual(response.getheader("Content-Range"), "bytes 2-5/256")
        self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
        media_etag = response.getheader("ETag")
        self.assertFalse((media_etag or "").startswith("W/"))

        response, body = self.request(
            "/media.mp4",
            {"Range": "bytes=2-5", "If-Range": '"different-representation"'},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(len(body), 256)

        response, body = self.request("/media.mp4", {"Range": "bytes=2-5", "If-Range": media_etag})
        self.assertEqual(response.status, 206)
        self.assertEqual(body, bytes(range(2, 6)))

    def test_unread_or_ambiguous_request_body_closes_connection(self) -> None:
        embedded = b"GET /asset.js HTTP/1.1\r\nHost: localhost\r\n\r\n"
        request = (
            b"GET /asset.js HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            + f"Content-Length: {len(embedded)}\r\n\r\n".encode("ascii")
            + embedded
        )
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=5) as client:
            client.sendall(request)
            response = bytearray()
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response.extend(chunk)
        self.assertEqual(bytes(response).count(b"HTTP/1.1"), 1)
        self.assertTrue(response.startswith(b"HTTP/1.1 400"))
        self.assertIn(b"Connection: close", response)

        conflicting_length_request = (
            b"GET /asset.js HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: 0\r\n"
            b"Content-Length: 4\r\n\r\n"
            b"test"
        )
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=5) as client:
            client.sendall(conflicting_length_request)
            response = bytearray()
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response.extend(chunk)
        self.assertEqual(bytes(response).count(b"HTTP/1.1"), 1)
        self.assertTrue(response.startswith(b"HTTP/1.1 400"))
        self.assertIn(b"Connection: close", response)

        post_request = (
            b"POST /unknown HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            + f"Content-Length: {len(embedded)}\r\n\r\n".encode("ascii")
            + embedded
        )
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=5) as client:
            client.sendall(post_request)
            response = bytearray()
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response.extend(chunk)
        self.assertEqual(bytes(response).count(b"HTTP/1.1"), 1)
        self.assertTrue(response.startswith(b"HTTP/1.1 404"))
        self.assertIn(b"Connection: close", response)

        oversized_request = (
            b"POST /unknown HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: 16777217\r\n\r\n"
        )
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=5) as client:
            client.sendall(oversized_request)
            response = bytearray()
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response.extend(chunk)
        self.assertTrue(response.startswith(b"HTTP/1.1 413"))
        self.assertIn(b"Connection: close", response)

        chunked_request = (
            b"GET /asset.js HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
            b"0\r\n\r\n"
        )
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=5) as client:
            client.sendall(chunked_request)
            response = bytearray()
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response.extend(chunk)
        self.assertEqual(bytes(response).count(b"HTTP/1.1"), 1)
        self.assertTrue(response.startswith(b"HTTP/1.1 400"))
        self.assertIn(b"Connection: close", response)

    def test_local_pdf_can_render_in_same_origin_lazy_viewer(self) -> None:
        response, body = self.request("/fax.pdf")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"%PDF-1.4\n%%EOF\n")
        self.assertEqual(response.getheader("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(response.getheader("Content-Security-Policy"), "frame-ancestors 'self'")

    def test_consumed_post_body_preserves_keepalive(self) -> None:
        self.connection.request(
            "POST",
            "/consume",
            body=b'{"ok":true}',
            headers={"Content-Type": "application/json"},
        )
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b'"ok":true', response.read())
        first_socket = self.connection.sock

        response, body = self.request("/asset.js")
        self.assertEqual(response.status, 200)
        self.assertIn(b"switchboard", body)
        self.assertIs(self.connection.sock, first_socket)


if __name__ == "__main__":
    unittest.main()
