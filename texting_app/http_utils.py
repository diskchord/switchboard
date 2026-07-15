from __future__ import annotations

import gzip
import os


COMPRESSIBLE_CONTENT_PREFIXES = (
    "application/json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
    "text/",
)


def accepts_gzip(value: str | None) -> bool:
    """Return whether an Accept-Encoding value permits gzip."""
    explicit: bool | None = None
    wildcard: bool | None = None
    for raw_item in str(value or "").split(","):
        item = raw_item.strip()
        if not item:
            continue
        encoding, *parameters = (part.strip() for part in item.split(";"))
        quality = 1.0
        for parameter in parameters:
            key, separator, raw_quality = parameter.partition("=")
            if separator and key.lower() == "q":
                try:
                    quality = float(raw_quality)
                except ValueError:
                    quality = 0.0
        allowed = quality > 0
        if encoding.lower() == "gzip":
            explicit = allowed
        elif encoding == "*":
            wildcard = allowed
    return explicit if explicit is not None else bool(wildcard)


def maybe_gzip(
    body: bytes,
    content_type: str,
    accept_encoding: str | None,
    *,
    minimum_size: int = 1024,
) -> tuple[bytes, bool]:
    """Compress a response when the client accepts gzip and doing so is useful."""
    if len(body) < minimum_size or not content_type.startswith(COMPRESSIBLE_CONTENT_PREFIXES):
        return body, False
    if not accepts_gzip(accept_encoding):
        return body, False
    compressed = gzip.compress(body, compresslevel=5, mtime=0)
    if len(compressed) >= len(body):
        return body, False
    return compressed, True


def parse_byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    """Parse a single HTTP bytes range, returning an inclusive start/end pair."""
    if not value:
        return None
    if size < 0:
        raise ValueError("Invalid resource size")
    unit, separator, spec = value.strip().partition("=")
    if separator != "=" or unit.lower() != "bytes" or not spec or "," in spec:
        raise ValueError("Invalid byte range")
    raw_start, dash, raw_end = spec.strip().partition("-")
    if dash != "-":
        raise ValueError("Invalid byte range")
    if not raw_start:
        try:
            suffix = int(raw_end)
        except ValueError as exc:
            raise ValueError("Invalid byte range") from exc
        if suffix <= 0 or size == 0:
            raise ValueError("Unsatisfiable byte range")
        return max(0, size - suffix), size - 1
    try:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
    except ValueError as exc:
        raise ValueError("Invalid byte range") from exc
    if start < 0 or start >= size or end < start:
        raise ValueError("Unsatisfiable byte range")
    return start, min(end, size - 1)


def file_etag(stat: os.stat_result) -> str:
    """Build a cheap validator that changes with a file's size or mtime."""
    return f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
