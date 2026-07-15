from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from . import config
from . import settings as app_settings
from .db import add_attachment, connect, init_db
from .timeutil import EASTERN


DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_BATCH_SIZE = 8
DEFAULT_LOCK_TIMEOUT = 5 * 60
PROVIDER_ATTACHMENT_SOURCES = ("twilio", "telnyx", "telnyx-fax")


class AttachmentDownloadError(RuntimeError):
    pass


class AttachmentPermanentError(AttachmentDownloadError):
    pass


@dataclass(frozen=True)
class DownloadResult:
    local_path: str
    size: int
    sha256: str
    content_type: str
    filename: str = ""
    source_sha256: str = ""


_WORKER_LOCK = threading.Lock()
_WORKER_WAKE = threading.Event()
_WORKER_THREAD: threading.Thread | None = None
_WORKER_STOP = threading.Event()


def _timestamp() -> str:
    # Message refresh tokens use updated_at. Keep microseconds so a fast download
    # completed in the same second as its webhook still changes the token.
    return datetime.now(EASTERN).isoformat(timespec="microseconds")


def _clean_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    if value == "telnyx-fax":
        return "telnyx"
    if value not in {"telnyx", "twilio"}:
        raise ValueError(f"Unsupported attachment provider: {provider}")
    return value


def enqueue_remote_attachment(
    conn: sqlite3.Connection,
    message_id: int,
    *,
    provider: str,
    remote_url: str,
    content_type: str | None = None,
    size: int | None = None,
    sha256: str | None = None,
    filename: str | None = None,
    source: str | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    """Persist remote attachment metadata and an idempotent ingestion job.

    The caller owns the transaction. Keeping the attachment and job in the same
    transaction means a worker cannot observe a job before its parent message is
    committed, and a crash cannot leave committed metadata without its job.
    """

    provider = _clean_provider(provider)
    remote_url = str(remote_url or "").strip()
    if not remote_url:
        raise ValueError("Remote attachment URL is required.")
    source = str(source or provider).strip().lower() or provider
    dedupe_key = str(dedupe_key or "").strip() or None
    timestamp = _timestamp()
    now = time.time()

    existing = None
    if dedupe_key:
        existing = conn.execute(
            """
            SELECT a.id, a.local_path, j.status AS job_status
            FROM attachment_ingestion_jobs j
            JOIN attachments a ON a.id = j.attachment_id
            WHERE j.dedupe_key = ?
            """,
            (dedupe_key,),
        ).fetchone()
    if existing is None:
        existing = conn.execute(
            """
            SELECT a.id, a.local_path, j.status AS job_status
            FROM attachments a
            LEFT JOIN attachment_ingestion_jobs j ON j.attachment_id = a.id
            WHERE a.message_id = ? AND a.remote_url = ? AND a.source = ?
              AND (? IS NULL OR j.id IS NULL)
            ORDER BY a.id
            LIMIT 1
            """,
            (message_id, remote_url, source, dedupe_key),
        ).fetchone()

    if existing:
        attachment_id = int(existing["id"])
        has_local_file = bool(str(existing["local_path"] or "").strip())
        conn.execute(
            """
            UPDATE attachments
            SET remote_url = ?,
                content_type = COALESCE(NULLIF(?, ''), content_type),
                size = COALESCE(?, size),
                sha256 = COALESCE(NULLIF(?, ''), sha256),
                filename = COALESCE(NULLIF(?, ''), filename),
                ingestion_status = CASE WHEN ? THEN 'ready' ELSE 'pending' END,
                ingestion_error = CASE WHEN ? THEN ingestion_error ELSE '' END,
                ingestion_updated_at = ?
            WHERE id = ?
            """,
            (
                remote_url,
                content_type,
                size,
                sha256,
                filename,
                has_local_file,
                has_local_file,
                timestamp,
                attachment_id,
            ),
        )
    else:
        attachment_id = add_attachment(
            conn,
            message_id,
            remote_url=remote_url,
            content_type=content_type,
            size=size,
            sha256=sha256,
            filename=filename,
            source=source,
            ingestion_status="pending",
            ingestion_updated_at=timestamp,
        )
        has_local_file = False

    if not has_local_file:
        job = conn.execute(
            "SELECT id, status FROM attachment_ingestion_jobs WHERE attachment_id = ?",
            (attachment_id,),
        ).fetchone()
        if job:
            # Completed-without-a-local-path is inconsistent (for example, the
            # media directory was restored separately). Make it recoverable.
            if job["status"] == "completed":
                conn.execute(
                    """
                    UPDATE attachment_ingestion_jobs
                    SET provider = ?, status = 'queued', attempts = 0,
                        max_attempts = ?, available_at = ?, locked_at = NULL,
                        worker_id = '', last_error = '', completed_at = NULL,
                        updated_at = ?, dedupe_key = COALESCE(dedupe_key, ?)
                    WHERE id = ?
                    """,
                    (provider, max(1, int(max_attempts)), now, timestamp, dedupe_key, job["id"]),
                )
            elif job["status"] == "failed":
                conn.execute(
                    """
                    UPDATE attachments
                    SET ingestion_status = 'failed',
                        ingestion_error = COALESCE(
                          (SELECT last_error FROM attachment_ingestion_jobs WHERE id = ?),
                          ingestion_error
                        ),
                        ingestion_updated_at = ?
                    WHERE id = ?
                    """,
                    (job["id"], timestamp, attachment_id),
                )
            elif job["status"] == "processing":
                conn.execute(
                    "UPDATE attachments SET ingestion_status = 'processing', ingestion_updated_at = ? WHERE id = ?",
                    (timestamp, attachment_id),
                )
            elif job["status"] == "retry":
                conn.execute(
                    "UPDATE attachments SET ingestion_status = 'retrying', ingestion_updated_at = ? WHERE id = ?",
                    (timestamp, attachment_id),
                )
        else:
            conn.execute(
                """
                INSERT INTO attachment_ingestion_jobs(
                  attachment_id, provider, dedupe_key, status, attempts,
                  max_attempts, available_at, created_at, updated_at
                )
                VALUES (?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                """,
                (attachment_id, provider, dedupe_key, max(1, int(max_attempts)), now, timestamp, timestamp),
            )
        _WORKER_WAKE.set()
    return attachment_id


def backfill_attachment_jobs() -> int:
    """Enqueue legacy/crash-recovered provider attachments missing local media."""

    conn = connect()
    init_db(conn)
    timestamp = _timestamp()
    now = time.time()
    changed = 0
    placeholders = ",".join("?" for _ in PROVIDER_ATTACHMENT_SOURCES)
    rows = conn.execute(
        f"""
        SELECT a.id, a.source, a.local_path, j.id AS job_id, j.status AS job_status
        FROM attachments a
        LEFT JOIN attachment_ingestion_jobs j ON j.attachment_id = a.id
        WHERE a.source IN ({placeholders})
          AND COALESCE(a.remote_url, '') <> ''
        ORDER BY a.id
        """,
        PROVIDER_ATTACHMENT_SOURCES,
    ).fetchall()
    for row in rows:
        local_path = str(row["local_path"] or "").strip()
        if local_path:
            path = Path(local_path)
            local_file = path if path.is_absolute() else config.MEDIA_DIR / path.name
            if local_file.is_file():
                continue
            # A database restore or manual media cleanup can leave a stale
            # local_path behind. Clear it before making the job recoverable.
            conn.execute("UPDATE attachments SET local_path = NULL WHERE id = ?", (row["id"],))
        provider = "twilio" if row["source"] == "twilio" else "telnyx"
        if row["job_id"] is None:
            conn.execute(
                """
                INSERT INTO attachment_ingestion_jobs(
                  attachment_id, provider, dedupe_key, status, attempts,
                  max_attempts, available_at, created_at, updated_at
                )
                VALUES (?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    provider,
                    f"backfill:{row['id']}",
                    DEFAULT_MAX_ATTEMPTS,
                    now,
                    timestamp,
                    timestamp,
                ),
            )
            changed += 1
        elif row["job_status"] in {"completed", "failed"}:
            conn.execute(
                """
                UPDATE attachment_ingestion_jobs
                SET status = 'queued', attempts = 0, available_at = ?,
                    locked_at = NULL, worker_id = '', last_error = '',
                    completed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, timestamp, row["job_id"]),
            )
            changed += 1
        conn.execute(
            """
            UPDATE attachments
            SET ingestion_status = 'pending', ingestion_error = '', ingestion_updated_at = ?
            WHERE id = ?
            """,
            (timestamp, row["id"]),
        )
    conn.commit()
    conn.close()
    if changed:
        _WORKER_WAKE.set()
    return changed


def _recover_stale_jobs(conn: sqlite3.Connection, now: float, lock_timeout: float) -> int:
    rows = conn.execute(
        """
        SELECT j.id, j.attachment_id, j.attempts, j.max_attempts,
               a.message_id, m.conversation_id
        FROM attachment_ingestion_jobs j
        JOIN attachments a ON a.id = j.attachment_id
        JOIN messages m ON m.id = a.message_id
        WHERE j.status = 'processing' AND COALESCE(j.locked_at, 0) <= ?
        """,
        (now - max(1.0, lock_timeout),),
    ).fetchall()
    timestamp = _timestamp()
    for row in rows:
        exhausted = int(row["attempts"]) >= int(row["max_attempts"])
        status = "failed" if exhausted else "retry"
        attachment_status = "failed" if exhausted else "retrying"
        error = "Attachment worker stopped before completing this attempt."
        conn.execute(
            """
            UPDATE attachment_ingestion_jobs
            SET status = ?, available_at = ?, locked_at = NULL, worker_id = '',
                last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now, error, timestamp, row["id"]),
        )
        conn.execute(
            """
            UPDATE attachments
            SET ingestion_status = ?, ingestion_error = ?, ingestion_updated_at = ?
            WHERE id = ?
            """,
            (attachment_status, error, timestamp, row["attachment_id"]),
        )
        conn.execute("UPDATE messages SET updated_at = ? WHERE id = ?", (timestamp, row["message_id"]))
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (timestamp, row["conversation_id"]),
        )
    return len(rows)


def _claim_next_job(
    conn: sqlite3.Connection,
    *,
    now: float,
    worker_id: str,
    lock_timeout: float,
) -> dict[str, Any] | None:
    # Avoid taking a write reservation on every idle poll. The transactional
    # query below remains the authority when work exists or multiple workers race.
    candidate = conn.execute(
        """
        SELECT 1
        FROM attachment_ingestion_jobs
        WHERE (status IN ('queued', 'retry') AND available_at <= ?)
           OR (status = 'processing' AND COALESCE(locked_at, 0) <= ?)
        LIMIT 1
        """,
        (now, now - max(1.0, lock_timeout)),
    ).fetchone()
    if candidate is None:
        return None
    conn.execute("BEGIN IMMEDIATE")
    _recover_stale_jobs(conn, now, lock_timeout)
    row = conn.execute(
        """
        SELECT j.*, a.message_id, a.remote_url, a.local_path, a.content_type,
               a.size, a.sha256, a.filename, a.source, m.conversation_id
        FROM attachment_ingestion_jobs j
        JOIN attachments a ON a.id = j.attachment_id
        JOIN messages m ON m.id = a.message_id
        WHERE j.status IN ('queued', 'retry') AND j.available_at <= ?
        ORDER BY j.available_at, j.id
        LIMIT 1
        """,
        (now,),
    ).fetchone()
    if row is None:
        conn.commit()
        return None
    timestamp = _timestamp()
    conn.execute(
        """
        UPDATE attachment_ingestion_jobs
        SET status = 'processing', attempts = attempts + 1, locked_at = ?,
            worker_id = ?, last_error = '', updated_at = ?
        WHERE id = ?
        """,
        (now, worker_id, timestamp, row["id"]),
    )
    conn.execute(
        """
        UPDATE attachments
        SET ingestion_status = 'processing', ingestion_error = '', ingestion_updated_at = ?
        WHERE id = ?
        """,
        (timestamp, row["attachment_id"]),
    )
    conn.commit()
    claimed = dict(row)
    claimed["attempts"] = int(row["attempts"]) + 1
    claimed["worker_id"] = worker_id
    return claimed


def _valid_expected_sha(value: Any) -> str:
    expected = str(value or "").strip().lower()
    return expected if re.fullmatch(r"[0-9a-f]{64}", expected) else ""


def _file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _extension_for_content_type(content_type: str | None) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "video/3gp": ".3gp",
        "video/3gpp": ".3gp",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "application/pdf": ".pdf",
    }.get(str(content_type or "").split(";", 1)[0].strip().lower(), "")


def _provider_host_allowed(url: str, provider: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or not hostname:
        return False
    provider_suffix = ".twilio.com" if provider == "twilio" else ".telnyx.com"
    if hostname == provider_suffix[1:] or hostname.endswith(provider_suffix):
        return True
    configured_base = (
        app_settings.get_value("twilio.api_base", config.TWILIO_API_BASE)
        if provider == "twilio"
        else app_settings.get_value("telnyx.api_base", config.TELNYX_API_BASE)
    )
    return hostname == (urlparse(configured_base).hostname or "").lower()


def _provider_headers(url: str, provider: str) -> dict[str, str]:
    headers = {"User-Agent": "switchboard/0.1", "Accept": "*/*"}
    if not _provider_host_allowed(url, provider):
        return headers
    if provider == "twilio":
        account_sid = app_settings.get_value("twilio.account_sid", config.TWILIO_ACCOUNT_SID).strip()
        auth_token = app_settings.get_value("twilio.auth_token", config.TWILIO_AUTH_TOKEN).strip()
        if account_sid and auth_token:
            raw = f"{account_sid}:{auth_token}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    elif provider == "telnyx":
        api_key = app_settings.get_value("telnyx.api_key", config.TELNYX_API_KEY).strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    return headers


class _ProviderRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep provider credentials only on redirects to trusted provider hosts."""

    def __init__(self, provider: str) -> None:
        super().__init__()
        self.provider = _clean_provider(provider)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            _validate_remote_url(newurl)
            if not _provider_host_allowed(newurl, self.provider):
                redirected.remove_header("Authorization")
        return redirected


def _validate_remote_url(url: str) -> None:
    """Reject media URLs that can reach local or otherwise non-public services."""

    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise AttachmentPermanentError("Attachment URL must use public HTTPS.")
    if parsed.username or parsed.password:
        raise AttachmentPermanentError("Attachment URL must not contain credentials.")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise AttachmentPermanentError("Attachment URL has an invalid port.") from exc
    hostname = parsed.hostname.rstrip(".").lower()
    try:
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise AttachmentDownloadError(f"Attachment host could not be resolved: {exc}") from exc
    if not addresses:
        raise AttachmentDownloadError("Attachment host could not be resolved.")
    for address in addresses:
        raw_address = str(address[4][0]).split("%", 1)[0]
        try:
            parsed_address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise AttachmentPermanentError("Attachment host resolved to an invalid address.") from exc
        if parsed_address.version == 6 and parsed_address.ipv4_mapped:
            parsed_address = parsed_address.ipv4_mapped
        if not parsed_address.is_global or parsed_address.is_multicast:
            raise AttachmentPermanentError("Attachment host resolved to a non-public address.")


def _download_attachment(job: Mapping[str, Any]) -> DownloadResult:
    url = str(job.get("remote_url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AttachmentPermanentError("Attachment URL must use HTTP or HTTPS.")
    provider = _clean_provider(str(job.get("provider") or ""))
    attachment_id = int(job["attachment_id"])
    expected_sha = _valid_expected_sha(job.get("sha256"))
    declared_type = str(job.get("content_type") or "").split(";", 1)[0].strip().lower()
    max_size = max(app_settings.get_int("uploads.max_file_mb", config.UPLOAD_MAX_FILE_MB), 1) * 1024 * 1024
    try:
        expected_size = int(job.get("size") or 0)
    except (TypeError, ValueError):
        expected_size = 0
    if expected_size > max_size:
        raise AttachmentPermanentError(f"Attachment exceeds the {max_size // (1024 * 1024)} MB download limit.")
    remote_suffix = Path(parsed.path).suffix
    extension = remote_suffix or _extension_for_content_type(declared_type)
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    existing_files = sorted(
        path
        for path in config.MEDIA_DIR.glob(f"attachment-{attachment_id}.*")
        if not path.name.endswith(".part")
    )
    for existing in existing_files:
        size, sha256 = _file_digest(existing)
        if not expected_sha or sha256 == expected_sha:
            return DownloadResult(
                local_path=f"media/{existing.name}",
                size=size,
                sha256=sha256,
                content_type=declared_type,
            )
        existing.unlink(missing_ok=True)

    _validate_remote_url(url)

    request = urllib.request.Request(url, headers=_provider_headers(url, provider))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _ProviderRedirectHandler(provider),
    )
    temp_path = config.MEDIA_DIR / f".attachment-{attachment_id}-{uuid.uuid4().hex}.part"
    try:
        try:
            response = opener.open(request, timeout=45)
        except urllib.error.HTTPError as exc:
            raise AttachmentDownloadError(f"Attachment server returned HTTP {exc.code}.") from exc
        except urllib.error.URLError as exc:
            raise AttachmentDownloadError(f"Attachment request failed: {exc.reason}") from exc
        with response:
            response_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            try:
                response_size = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                response_size = 0
            if response_size > max_size:
                raise AttachmentPermanentError(
                    f"Attachment exceeds the {max_size // (1024 * 1024)} MB download limit."
                )
            content_type = declared_type or response_type
            extension = extension or _extension_for_content_type(content_type) or ".bin"
            target = config.MEDIA_DIR / f"attachment-{attachment_id}{extension.lower()}"
            digest = hashlib.sha256()
            size = 0
            with temp_path.open("xb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    if size + len(chunk) > max_size:
                        raise AttachmentPermanentError(
                            f"Attachment exceeds the {max_size // (1024 * 1024)} MB download limit."
                        )
                    handle.write(chunk)
                    size += len(chunk)
                    digest.update(chunk)
        sha256 = digest.hexdigest()
        if expected_sha and sha256 != expected_sha:
            raise AttachmentPermanentError("Downloaded attachment did not match its expected SHA-256 digest.")
        os.replace(temp_path, target)
        return DownloadResult(
            local_path=f"media/{target.name}",
            size=size,
            sha256=sha256,
            content_type=content_type,
        )
    except AttachmentDownloadError:
        raise
    except OSError as exc:
        raise AttachmentDownloadError(f"Could not store attachment: {exc}") from exc
    except Exception as exc:
        raise AttachmentDownloadError(f"Attachment download failed: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _is_convertible_video(job: Mapping[str, Any], result: DownloadResult) -> bool:
    content_types = {
        str(job.get("content_type") or "").split(";", 1)[0].strip().lower(),
        str(result.content_type or "").split(";", 1)[0].strip().lower(),
    }
    if content_types & {"video/3gp", "video/3gpp"}:
        return True
    names = (
        job.get("filename"),
        job.get("remote_url"),
        result.local_path,
        result.filename,
    )
    return any(
        Path(urlparse(str(name or "")).path).suffix.lower() in {".3gp", ".3gpp"}
        for name in names
    )


def _transcode_video(job: Mapping[str, Any], result: DownloadResult) -> DownloadResult:
    """Convert provider 3GP media for browsers; failure leaves the original usable."""

    if not _is_convertible_video(job, result):
        return result
    source = config.MEDIA_DIR / Path(result.local_path).name
    ffmpeg = shutil.which("ffmpeg")
    if not source.is_file() or not ffmpeg:
        return result
    target = source.with_suffix(".mp4")
    if not target.is_file() or target.stat().st_size == 0:
        temp_path = config.MEDIA_DIR / f".{target.stem}-{uuid.uuid4().hex}.tmp.mp4"
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0?",
                    "-map",
                    "0:a:0?",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-preset",
                    "veryfast",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    str(temp_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
            )
            os.replace(temp_path, target)
        except (OSError, subprocess.SubprocessError):
            temp_path.unlink(missing_ok=True)
            return result
        finally:
            temp_path.unlink(missing_ok=True)
    size, sha256 = _file_digest(target)
    return DownloadResult(
        local_path=f"media/{target.name}",
        size=size,
        sha256=sha256,
        content_type="video/mp4",
        filename=target.name,
        # Preserve the provider payload digest for missing-file recovery. The
        # MP4 is derived and can be rebuilt from the original download.
        source_sha256=result.source_sha256 or result.sha256,
    )


def _complete_job(conn: sqlite3.Connection, job: Mapping[str, Any], result: DownloadResult) -> bool:
    timestamp = _timestamp()
    conn.execute("BEGIN IMMEDIATE")
    owned = conn.execute(
        """
        SELECT 1 FROM attachment_ingestion_jobs
        WHERE id = ? AND status = 'processing' AND worker_id = ?
        """,
        (job["id"], job["worker_id"]),
    ).fetchone()
    if not owned:
        conn.rollback()
        return False
    conn.execute(
        """
        UPDATE attachments
        SET local_path = ?, size = ?, sha256 = ?,
            content_type = COALESCE(NULLIF(?, ''), content_type),
            filename = COALESCE(NULLIF(?, ''), filename),
            ingestion_status = 'ready', ingestion_error = '', ingestion_updated_at = ?
        WHERE id = ?
        """,
        (
            result.local_path,
            result.size,
            result.source_sha256 or result.sha256,
            result.content_type,
            result.filename,
            timestamp,
            job["attachment_id"],
        ),
    )
    conn.execute("UPDATE messages SET updated_at = ? WHERE id = ?", (timestamp, job["message_id"]))
    conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (timestamp, job["conversation_id"]))
    conn.execute(
        """
        UPDATE attachment_ingestion_jobs
        SET status = 'completed', locked_at = NULL, worker_id = '',
            last_error = '', completed_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (timestamp, timestamp, job["id"]),
    )
    conn.commit()
    return True


def _fail_job(conn: sqlite3.Connection, job: Mapping[str, Any], error: Exception, now: float) -> None:
    attempts = int(job["attempts"])
    exhausted = isinstance(error, AttachmentPermanentError) or attempts >= int(job["max_attempts"])
    job_status = "failed" if exhausted else "retry"
    attachment_status = "failed" if exhausted else "retrying"
    delay = 0.0 if exhausted else min(60 * 60, 5.0 * (2 ** max(0, attempts - 1)))
    detail = str(error or "Attachment ingestion failed.").strip()[:2000]
    timestamp = _timestamp()
    conn.execute("BEGIN IMMEDIATE")
    owned = conn.execute(
        """
        SELECT 1 FROM attachment_ingestion_jobs
        WHERE id = ? AND status = 'processing' AND worker_id = ?
        """,
        (job["id"], job["worker_id"]),
    ).fetchone()
    if not owned:
        conn.rollback()
        return
    conn.execute(
        """
        UPDATE attachment_ingestion_jobs
        SET status = ?, available_at = ?, locked_at = NULL, worker_id = '',
            last_error = ?, updated_at = ?
        WHERE id = ?
        """,
        (job_status, now + delay, detail, timestamp, job["id"]),
    )
    conn.execute(
        """
        UPDATE attachments
        SET ingestion_status = ?, ingestion_error = ?, ingestion_updated_at = ?
        WHERE id = ?
        """,
        (attachment_status, detail, timestamp, job["attachment_id"]),
    )
    conn.execute("UPDATE messages SET updated_at = ? WHERE id = ?", (timestamp, job["message_id"]))
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (timestamp, job["conversation_id"]),
    )
    conn.commit()


def process_attachment_jobs(
    *,
    limit: int = DEFAULT_BATCH_SIZE,
    now: float | None = None,
    downloader: Callable[[Mapping[str, Any]], DownloadResult] | None = None,
    worker_id: str | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> int:
    """Claim and process up to ``limit`` jobs; useful for workers and tests."""

    if limit <= 0:
        return 0
    fixed_time = None if now is None else float(now)
    download = downloader or _download_attachment
    worker_id = worker_id or f"{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex[:8]}"
    conn = connect()
    processed = 0
    try:
        while processed < limit:
            claim_time = time.time() if fixed_time is None else fixed_time
            job = _claim_next_job(conn, now=claim_time, worker_id=worker_id, lock_timeout=lock_timeout)
            if job is None:
                break
            processed += 1
            try:
                result = download(job)
                if not result.local_path:
                    raise AttachmentDownloadError("Attachment downloader returned no local path.")
                result = _transcode_video(job, result)
                _complete_job(conn, job, result)
            except Exception as exc:
                # A completion-time database error may leave its transaction
                # open. Roll it back before persisting the retry state.
                conn.rollback()
                failure_time = time.time() if fixed_time is None else fixed_time
                _fail_job(conn, job, exc, failure_time)
    finally:
        conn.close()
    return processed


def start_attachment_worker(
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Backfill outstanding media and start the idempotent daemon worker."""

    global _WORKER_THREAD
    with _WORKER_LOCK:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return _WORKER_THREAD
        backfill_attachment_jobs()
        stop = stop_event or _WORKER_STOP
        if stop_event is None:
            _WORKER_STOP.clear()

        def worker() -> None:
            while not stop.is_set():
                try:
                    processed = process_attachment_jobs(limit=max(1, int(batch_size)))
                except Exception as exc:
                    print(f"attachment ingestion worker failed: {exc}", flush=True)
                    processed = 0
                if processed >= max(1, int(batch_size)):
                    continue
                _WORKER_WAKE.wait(max(0.05, float(poll_interval)))
                _WORKER_WAKE.clear()

        _WORKER_THREAD = threading.Thread(target=worker, name="attachment-ingestion", daemon=True)
        _WORKER_THREAD.start()
        return _WORKER_THREAD
