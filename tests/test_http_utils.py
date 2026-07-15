from __future__ import annotations

import os
import tempfile
import unittest

from texting_app.http_utils import accepts_gzip, file_etag, maybe_gzip, parse_byte_range


class HttpUtilityTests(unittest.TestCase):
    def test_gzip_negotiation_honors_quality_and_wildcards(self) -> None:
        self.assertTrue(accepts_gzip("br, gzip"))
        self.assertTrue(accepts_gzip("*;q=0.5"))
        self.assertFalse(accepts_gzip("gzip;q=0, *;q=1"))
        self.assertFalse(accepts_gzip(None))

    def test_maybe_gzip_only_compresses_eligible_responses(self) -> None:
        body = (b'{"message":"repeated value"}' * 200)
        compressed, used = maybe_gzip(body, "application/json; charset=utf-8", "gzip")
        self.assertTrue(used)
        self.assertLess(len(compressed), len(body))
        unchanged, used = maybe_gzip(body, "image/png", "gzip")
        self.assertFalse(used)
        self.assertEqual(unchanged, body)

    def test_parse_byte_range_supports_common_forms(self) -> None:
        self.assertEqual(parse_byte_range("bytes=2-5", 10), (2, 5))
        self.assertEqual(parse_byte_range("bytes=7-", 10), (7, 9))
        self.assertEqual(parse_byte_range("bytes=-3", 10), (7, 9))
        self.assertIsNone(parse_byte_range(None, 10))
        with self.assertRaises(ValueError):
            parse_byte_range("bytes=20-", 10)
        with self.assertRaises(ValueError):
            parse_byte_range("bytes=0-1,4-5", 10)

    def test_file_etag_changes_with_file_metadata(self) -> None:
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(b"abc")
            handle.flush()
            first = file_etag(os.stat(handle.name))
            handle.write(b"def")
            handle.flush()
            second = file_etag(os.stat(handle.name))
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
