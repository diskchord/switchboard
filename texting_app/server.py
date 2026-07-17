from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import base64
from contextlib import closing
from datetime import datetime, timedelta
from io import BytesIO
from email.parser import BytesParser
from email.policy import default as email_policy
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import auth
from . import config
from .attachment_ingestion import start_attachment_worker
from .autoreply import (
    DEFAULT_AUTOREPLY_COOLDOWN_HOURS,
    DEFAULT_AUTOREPLY_MESSAGE,
    identity_autoreply_fields,
    update_autoreply_rule,
)
from .contacts import ContactsError, active_provider, configured_providers, import_phone_contacts
from .contacts import save_contact_name as save_synced_contact_name
from .contacts import start_autosync, sync_contacts
from .db import connect, conversation_key, ensure_conversation, from_json, init_db, self_numbers
from .fastmail import FastmailError
from .google_contacts import GoogleContactsError
from .http_utils import file_etag, maybe_gzip, parse_byte_range
from .messaging import MessagingError, configured_messaging_providers, provider_for_number
from .messaging import send_message as send_provider_message
from .phone import display_phone, normalize_phone
from .settings import (
    SettingsError,
    configured_values,
    get_bool,
    get_int,
    get_value,
    invalidate_settings_cache,
    update_values,
)
from .telnyx import TelnyxError
from .telnyx import handle_webhook as handle_telnyx_webhook
from .telnyx import send_fax as send_telnyx_fax
from .timeutil import EASTERN, now_est
from .twilio import TwilioError
from .twilio import handle_webhook as handle_twilio_webhook
from .voice import (
    VoiceError,
    parse_voice_callback,
    store_revai_callback,
    store_voicemail_callback,
    update_voice_rule,
    voice_rule_fields,
    voice_xml,
)


STATIC_DIR = config.ROOT / "static"
MESSAGE_PAGE_SIZE = 80
UPLOAD_CONTENT_PREFIXES = ("image/", "video/", "audio/")
UPLOAD_CONTENT_TYPES = {"application/pdf"}
DEFAULT_REQUEST_BODY_LIMIT = 16 * 1024 * 1024
UPLOAD_REQUEST_OVERHEAD = 1024 * 1024
SESSION_MAX_AGE_SECONDS = config.AUTH_SESSION_DAYS * 24 * 60 * 60
LOGIN_FAILURE_LIMIT = 8
LOGIN_FAILURE_WINDOW_SECONDS = 5 * 60
DEFAULT_IDENTITY_SETTING_KEY = "messaging.default_identity"
LOGIN_FAILURES: dict[str, list[float]] = {}
LOGIN_FAILURE_LOCK = threading.Lock()
BACKUP_CODE_METADATA_PREFIX = "auth.backup_code.used."
AUTH_USERNAME_METADATA_KEY = "auth.username"
AUTH_PASSWORD_HASH_METADATA_KEY = "auth.password_hash"
AUTH_SECRET_KEY_METADATA_KEY = "auth.secret_key"
AUTH_TOTP_METADATA_KEY = "auth.totp_secret"
AUTH_BACKUP_CODES_METADATA_KEY = "auth.backup_code_hashes"
AUTH_ACCOUNT_METADATA_KEYS = (
    AUTH_USERNAME_METADATA_KEY,
    AUTH_PASSWORD_HASH_METADATA_KEY,
    AUTH_SECRET_KEY_METADATA_KEY,
)
TWO_FACTOR_SETUP_TOKEN_SECONDS = 10 * 60

PUBLIC_GET_PATHS = {
    "/api/auth/session",
    "/api/health",
    "/api/telnyx/voice",
    "/api/twilio/voice",
    "/apple-touch-icon.png",
    "/favicon.ico",
    "/favicon.svg",
    "/login",
}
PUBLIC_POST_PATHS = {
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/setup",
    "/api/revai/webhook",
    "/api/telnyx/voice",
    "/api/telnyx/voice/recording",
    "/api/telnyx/voice/transcription",
    "/api/telnyx/webhook",
    "/api/twilio/voice",
    "/api/twilio/voice/recording",
    "/api/twilio/voice/transcription",
    "/api/twilio/webhook",
}


def _json_default(value):
    return str(value)


def _row_dict(row) -> dict:
    return dict(row) if row else {}


def _identity_dict(row) -> dict:
    identity = _row_dict(row)
    if identity:
        identity.update(identity_autoreply_fields(identity))
        identity.update(voice_rule_fields(identity))
    return identity


def _identity_with_autoreply(conn, identity_id: int) -> dict:
    row = conn.execute(
        """
        SELECT i.*,
          COALESCE(ar.enabled, 0) AS autoreply_enabled,
          COALESCE(ar.message, '') AS autoreply_message,
          COALESCE(ar.cooldown_hours, ?) AS autoreply_cooldown_hours,
          vr.phone_number AS voice_rule_phone_number,
          COALESCE(vr.forwarding_enabled, 0) AS voice_forwarding_enabled,
          COALESCE(vr.forward_to_number, '') AS voice_forward_to_number,
          COALESCE(vr.forward_timeout_seconds, 20) AS voice_forward_timeout_seconds,
          COALESCE(vr.voicemail_enabled, 1) AS voice_voicemail_enabled,
          COALESCE(vr.voicemail_greeting, '') AS voice_voicemail_greeting,
          COALESCE(vr.voicemail_greeting_media_url, '') AS voice_voicemail_greeting_media_url
        FROM identities i
        LEFT JOIN autoreply_rules ar ON ar.phone_number = i.phone_number
        LEFT JOIN voice_rules vr ON vr.phone_number = i.phone_number
        WHERE i.id = ?
        """,
        (DEFAULT_AUTOREPLY_COOLDOWN_HOURS, identity_id),
    ).fetchone()
    return _identity_dict(row)


def _default_identity_phone(identities: list[dict]) -> str:
    active_numbers = [identity["phone_number"] for identity in identities if identity.get("is_active")]
    if not active_numbers:
        return ""
    saved = normalize_phone(get_value(DEFAULT_IDENTITY_SETTING_KEY, ""))
    if saved in set(active_numbers):
        return saved
    return active_numbers[0]


def _apply_default_identity(identities: list[dict]) -> str:
    default_phone = _default_identity_phone(identities)
    for identity in identities:
        identity["is_default"] = identity.get("phone_number") == default_phone
    return default_phone


def _configured_upload_dir() -> Path:
    raw = get_value("uploads.public_directory", str(config.PUBLIC_UPLOAD_DIR)).strip()
    path = Path(raw).expanduser()
    return path if path.is_absolute() else config.ROOT / path


def _upload_base_url_from_request(request_url: str) -> str:
    parsed = urlparse(request_url or "")
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/uploads"


def _configured_upload_base_url(default_base_url: str = "") -> str:
    configured = get_value("uploads.public_base_url", config.PUBLIC_UPLOAD_BASE_URL).strip().rstrip("/")
    if configured:
        return configured
    return default_base_url.strip().rstrip("/")


def _upload_max_bytes() -> int:
    return max(get_int("uploads.max_file_mb", config.UPLOAD_MAX_FILE_MB), 1) * 1024 * 1024


def _upload_allowed(content_type: str) -> bool:
    content_type = (content_type or "").lower()
    return content_type in UPLOAD_CONTENT_TYPES or any(content_type.startswith(prefix) for prefix in UPLOAD_CONTENT_PREFIXES)


def _upload_extension(filename: str, content_type: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,12}", ext):
        return ext
    guessed = mimetypes.guess_extension(content_type or "") or ".bin"
    return guessed.lower()


def _parse_upload(content_type: str, body: bytes) -> tuple[str, str, bytes]:
    if "multipart/form-data" not in content_type:
        raise ValueError("Upload must use multipart/form-data.")
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=email_policy).parsebytes(header + body)
    if not message.is_multipart():
        raise ValueError("Upload did not include a file.")
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") != "file":
            continue
        data = part.get_payload(decode=True) or b""
        filename = part.get_filename() or "upload"
        part_type = part.get_content_type() or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return filename, part_type, data
    raise ValueError("Upload did not include a file.")


def save_uploaded_media(content_type: str, body: bytes, request_url: str = "") -> dict:
    base_url = _configured_upload_base_url(_upload_base_url_from_request(request_url))
    if not base_url:
        raise ValueError("Set Uploads > Public upload base URL or access Switchboard through its public URL before uploading media.")
    upload_dir = _configured_upload_dir()
    source_name, file_type, data = _parse_upload(content_type, body)
    if not data:
        raise ValueError("Uploaded file is empty.")
    if len(data) > _upload_max_bytes():
        raise ValueError(f"Uploaded file is larger than {get_int('uploads.max_file_mb', config.UPLOAD_MAX_FILE_MB)} MB.")
    if not _upload_allowed(file_type):
        raise ValueError("Upload must be an image, video, audio file, or PDF.")
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{secrets.token_urlsafe(16)}{_upload_extension(source_name, file_type)}"
    target = upload_dir / filename
    target.write_bytes(data)
    public_url = f"{base_url}/{quote(filename)}"
    return {
        "filename": filename,
        "original_filename": source_name,
        "content_type": file_type,
        "size": len(data),
        "local_path": str(target),
        "url": public_url,
        "local_url": f"/uploads/{quote(filename)}",
    }


def upload_diagnostics(request_url: str = "") -> dict:
    upload_dir = _configured_upload_dir()
    auto_base_url = _upload_base_url_from_request(request_url)
    base_url = _configured_upload_base_url(auto_base_url)
    recent_files = []
    if upload_dir.is_dir():
        found = []
        for item in upload_dir.iterdir():
            try:
                stat = item.stat()
            except OSError:
                continue
            if item.is_file():
                found.append(
                    {
                        "name": item.name,
                        "path": str(item),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                )
        recent_files = sorted(found, key=lambda item: item["mtime"], reverse=True)[:20]
    return {
        "directory": str(upload_dir),
        "resolved_directory": str(upload_dir.resolve(strict=False)),
        "directory_exists": upload_dir.exists(),
        "directory_is_dir": upload_dir.is_dir(),
        "directory_writable": os.access(upload_dir, os.W_OK) if upload_dir.exists() else os.access(upload_dir.parent, os.W_OK),
        "base_url": base_url,
        "auto_base_url": auto_base_url,
        "base_url_source": "configured" if get_value("uploads.public_base_url", config.PUBLIC_UPLOAD_BASE_URL).strip() else "request",
        "max_file_mb": get_int("uploads.max_file_mb", config.UPLOAD_MAX_FILE_MB),
        "cwd": os.getcwd(),
        "process_uid": os.getuid(),
        "process_gid": os.getgid(),
        "recent_files": recent_files,
    }


def _local_upload_path_for_remote_url(remote_url: str | None) -> Path | None:
    if not remote_url:
        return None
    filename = Path(unquote(urlparse(remote_url).path)).name
    if not filename:
        return None
    candidate = _configured_upload_dir() / filename
    return candidate if candidate.is_file() else None


def _attachment_dict(row) -> dict:
    attachment = _row_dict(row)
    if not attachment.get("local_path"):
        local_path = _local_upload_path_for_remote_url(attachment.get("remote_url"))
        if local_path:
            attachment["local_path"] = str(local_path)
            attachment["source"] = "upload"
    return attachment


def _mark_uploaded_attachments_local(message_id: int, media_urls: list[str]) -> None:
    if not media_urls:
        return
    conn = connect()
    init_db(conn)
    try:
        for url in media_urls:
            local_path = _local_upload_path_for_remote_url(url)
            if not local_path:
                continue
            content_type = mimetypes.guess_type(local_path.name)[0]
            size = local_path.stat().st_size
            conn.execute(
                """
                UPDATE attachments
                SET local_path = ?,
                    content_type = COALESCE(content_type, ?),
                    size = COALESCE(size, ?),
                    source = 'upload'
                WHERE message_id = ?
                  AND remote_url = ?
                """,
                (str(local_path), content_type, size, message_id, url),
            )
        conn.commit()
    finally:
        conn.close()


def _scheduled_messages_for_conversation(conn, conversation_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM scheduled_messages
        WHERE conversation_id = ?
          AND status IN ('queued', 'sending', 'failed')
        ORDER BY scheduled_for, id
        """,
        (conversation_id,),
    ).fetchall()
    contact_names = _contact_names(conn, (row["from_number"] for row in rows))
    scheduled = []
    for row in rows:
        to_numbers = from_json(row["to_numbers"], [])
        media_urls = from_json(row["media_urls"], [])
        failed = row["status"] == "failed"
        attachments = [
            {
                "id": f"scheduled-{row['id']}-{index}",
                "local_path": "",
                "remote_url": url,
                "content_type": mimetypes.guess_type(url)[0] or "",
                "size": None,
                "sha256": "",
                "filename": Path(unquote(urlparse(url).path)).name,
                "source": "scheduled",
            }
            for index, url in enumerate(media_urls)
        ]
        scheduled.append(
            {
                "id": f"scheduled-{row['id']}",
                "scheduled_message_id": row["id"],
                "conversation_id": conversation_id,
                "direction": "outbound",
                "from_number": row["from_number"],
                "to_numbers": to_numbers,
                "cc_numbers": [],
                "text": row["text"],
                "message_type": "MMS" if media_urls or len(to_numbers) > 1 else "SMS",
                "status": "failed" if failed else "scheduled",
                "status_label": "Failed" if failed else "Scheduled",
                "status_kind": "failed" if failed else "pending",
                "status_detail": row["failure"] if failed else f"Scheduled for {row['scheduled_for']}",
                "occurred_at": row["scheduled_for"],
                "source": "scheduled",
                "raw_json": None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "from_display": contact_names.get(row["from_number"]) or display_phone(row["from_number"]),
                "attachments": attachments,
            }
        )
    return scheduled


FAILURE_STATUSES = {"delivery_failed", "failed", "undelivered", "rejected", "expired"}
WARNING_STATUSES = {"delivery_unconfirmed", "unknown", "unconfirmed"}
SUCCESS_STATUSES = {"delivered", "received", "imported", "completed"}
PENDING_STATUSES = {"queued", "scheduled", "sending", "sent", "accepted", "finalized", "media_processed"}


def _needs_attention(
    last_direction: str | None,
    last_occurred_at: str | None,
    dealt_with_at: str | None,
    manual_unread_at: str | None = None,
) -> int:
    if manual_unread_at:
        return 1
    return int(last_direction == "inbound" and bool(last_occurred_at) and (not dealt_with_at or last_occurred_at > dealt_with_at))


UNREAD_CONVERSATION_CLAUSE = """
(
  c.manual_unread_at IS NOT NULL
  OR EXISTS (
    SELECT 1
    FROM messages latest
    WHERE latest.conversation_id = c.id
      AND latest.direction = 'inbound'
      AND (c.dealt_with_at IS NULL OR latest.occurred_at > c.dealt_with_at)
      AND latest.id = (
        SELECT candidate.id
        FROM messages candidate
        WHERE candidate.conversation_id = c.id
          AND COALESCE(candidate.source, '') != 'autoreply'
        ORDER BY candidate.occurred_at DESC, candidate.id DESC
        LIMIT 1
      )
  )
)
"""

CONVERSATION_SORT_EXPR = """
CASE
  WHEN sm.scheduled_for IS NOT NULL
    AND (c.last_message_at IS NULL OR sm.scheduled_for > c.last_message_at)
    THEN sm.scheduled_for
  ELSE COALESCE(c.last_message_at, c.updated_at)
END
"""


def _status_label(status: str | None) -> str:
    labels = {
        "delivery_failed": "Failed",
        "delivery_unconfirmed": "Unconfirmed",
        "queued": "Queued",
        "scheduled": "Scheduled",
        "sending": "Sending",
        "sent": "Sent",
        "delivered": "Delivered",
        "received": "Received",
        "imported": "Imported",
        "media_processed": "Media processed",
        "completed": "Completed",
    }
    if not status:
        return ""
    return labels.get(status, status.replace("_", " ").title())


def _status_kind(status: str | None) -> str:
    status = status or ""
    if status in FAILURE_STATUSES:
        return "failed"
    if status in WARNING_STATUSES:
        return "warning"
    if status in SUCCESS_STATUSES:
        return "success"
    if status in PENDING_STATUSES:
        return "pending"
    return "neutral"


def _as_payload(raw_json: str | None) -> dict:
    if not raw_json:
        return {}
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_text(error) -> str:
    if isinstance(error, str):
        return error
    if not isinstance(error, dict):
        return str(error)
    code = error.get("code") or error.get("error_code")
    title = error.get("title") or error.get("error") or error.get("reason")
    detail = error.get("detail") or error.get("message") or error.get("description")
    parts = [str(part) for part in (code, title, detail) if part]
    return " - ".join(parts) if parts else json.dumps(error, ensure_ascii=False)


def _message_status_detail(status: str | None, raw_json: str | None) -> str:
    if _status_kind(status) not in {"failed", "warning"}:
        return ""
    payload = _as_payload(raw_json)
    provider = "Twilio" if "twilio" in payload or "twilio_status" in payload else "Telnyx"
    twilio_status = payload.get("twilio_status")
    if isinstance(twilio_status, dict):
        twilio_error = twilio_status.get("ErrorMessage") or twilio_status.get("ErrorCode")
        if twilio_error:
            return f"Twilio reported {status.replace('_', ' ')}: {twilio_error}."
    message_payload = payload.get("data", {}).get("payload", payload.get("data", payload))
    if isinstance(message_payload, dict):
        failure_reason = message_payload.get("failure_reason")
        if failure_reason:
            reason = str(failure_reason).replace("_", " ")
            return f"{provider} reported {status.replace('_', ' ')}: {reason}."
    errors = message_payload.get("errors") if isinstance(message_payload, dict) else None
    if errors:
        return "; ".join(_error_text(error) for error in errors)
    if status == "delivery_unconfirmed":
        return "Telnyx did not receive carrier confirmation for this message."
    if status:
        return f"{provider} reported {status.replace('_', ' ')}."
    return ""


def _decorate_message_status(message: dict) -> dict:
    status = message.get("status")
    message["status_label"] = _status_label(status)
    message["status_kind"] = _status_kind(status)
    message["status_detail"] = _message_status_detail(status, message.get("raw_json"))
    return message


def _contact_names(conn, phones) -> dict[str, str]:
    unique_phones = list(dict.fromkeys(str(phone) for phone in phones if phone))
    names: dict[str, str] = {}
    for offset in range(0, len(unique_phones), 800):
        chunk = unique_phones[offset : offset + 800]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT cp.phone_number, c.display_name
            FROM contact_phones cp
            JOIN contacts c ON c.id = cp.contact_id
            WHERE cp.phone_number IN ({placeholders})
            ORDER BY cp.phone_number,
              CASE c.source
                WHEN 'fastmail' THEN 3
                WHEN 'google' THEN 3
                WHEN 'phone' THEN 2
                ELSE 1
              END DESC,
              c.updated_at DESC
            """,
            chunk,
        ).fetchall()
        for row in rows:
            phone = row["phone_number"]
            display_name = row["display_name"]
            if phone not in names and display_name and display_name != phone:
                names[phone] = display_name
    return names


def _contact_name(conn, phone: str) -> str:
    return _contact_names(conn, [phone]).get(phone) or display_phone(phone)


def _participants_for_conversations(conn, conversation_ids) -> dict[int, list[dict]]:
    ids = list(dict.fromkeys(int(conversation_id) for conversation_id in conversation_ids))
    participants: dict[int, list[dict]] = {conversation_id: [] for conversation_id in ids}
    if not ids:
        return participants
    rows = []
    for offset in range(0, len(ids), 800):
        chunk = ids[offset : offset + 800]
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            conn.execute(
                f"""
                SELECT cp.conversation_id,
                  cp.phone_number,
                  cp.role,
                  i.label AS identity_label,
                  i.color
                FROM conversation_participants cp
                LEFT JOIN identities i ON i.phone_number = cp.phone_number
                WHERE cp.conversation_id IN ({placeholders})
                """,
                chunk,
            ).fetchall()
        )
    contact_names = _contact_names(conn, (row["phone_number"] for row in rows))
    for row in rows:
        phone = row["phone_number"]
        participants[row["conversation_id"]].append(
            {
                "phone_number": phone,
                "display": row["identity_label"] or contact_names.get(phone) or display_phone(phone),
                "role": row["role"],
                "color": row["color"],
            }
        )
    for conversation_participants in participants.values():
        conversation_participants.sort(
            key=lambda participant: (
                0 if participant["role"] == "self" else 1,
                str(participant["display"]).casefold(),
            )
        )
    return participants


def _participants(conn, conversation_id: int) -> list[dict]:
    return _participants_for_conversations(conn, [conversation_id]).get(conversation_id, [])


def _conversation_title_from_participants(participants: list[dict], fallback: str | None = None) -> str:
    names = [participant["display"] for participant in participants if participant["role"] == "participant"]
    if names:
        return ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
    return fallback or "Unknown"


def _conversation_title(conn, conversation_id: int, fallback: str | None = None) -> str:
    return _conversation_title_from_participants(_participants(conn, conversation_id), fallback)


def _search_terms(value: str) -> list[str]:
    return [part for part in re.split(r"\s+", value.strip().lower()) if part]


def _conversation_direct_search_expr(terms: list[str]) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        like = f"%{term}%"
        clauses.append(
            """
            (
              lower(COALESCE(c.title, '')) LIKE ?
              OR EXISTS (
                SELECT 1
                FROM conversation_participants direct_cp
                LEFT JOIN contacts direct_co ON direct_co.id = direct_cp.contact_id
                WHERE direct_cp.conversation_id = c.id
                  AND direct_cp.role = 'participant'
                  AND (
                    lower(COALESCE(direct_co.display_name, '')) LIKE ?
                    OR lower(direct_cp.phone_number) LIKE ?
                  )
              )
            )
            """
        )
        params.extend([like, like, like])
    return " AND ".join(clauses) if clauses else "0", params


def _conversation_text_search_expr(table: str, alias: str, terms: list[str]) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        clauses.append(f"lower(COALESCE({alias}.text, '')) LIKE ?")
        params.append(f"%{term}%")
    where_sql = " AND ".join(clauses) if clauses else "0"
    return (
        f"""
        EXISTS (
          SELECT 1
          FROM {table} {alias}
          WHERE {alias}.conversation_id = c.id
            AND {where_sql}
        )
        """,
        params,
    )


def _conversation_search_clause(terms: list[str]) -> tuple[str, list[str]]:
    direct_sql, direct_params = _conversation_direct_search_expr(terms)
    message_sql, message_params = _conversation_text_search_expr("messages", "search_m", terms)
    scheduled_sql, scheduled_params = _conversation_text_search_expr("scheduled_messages", "search_sm", terms)
    return (
        f"({direct_sql}) OR ({message_sql}) OR ({scheduled_sql})",
        [*direct_params, *message_params, *scheduled_params],
    )


def _search_snippet(text: str, terms: list[str], max_length: int = 160, context: int = 52) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if not compact:
        return ""
    lower = compact.lower()
    positions = [lower.find(term) for term in terms if term]
    positions = [position for position in positions if position >= 0]
    first = min(positions) if positions else 0
    start = max(0, first - context)
    end = min(len(compact), start + max_length)
    if end - start < max_length:
        start = max(0, end - max_length)
    snippet = compact[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(compact):
        snippet = f"{snippet}..."
    return snippet


def _conversation_message_search_match(conn, conversation_id: int, terms: list[str]) -> dict | None:
    if not terms:
        return None
    where_sql = " AND ".join(["lower(COALESCE(text, '')) LIKE ?"] * len(terms))
    params = [f"%{term}%" for term in terms]
    row = conn.execute(
        f"""
        SELECT id, text, occurred_at
        FROM messages
        WHERE conversation_id = ?
          AND {where_sql}
        ORDER BY occurred_at DESC, id DESC
        LIMIT 1
        """,
        (conversation_id, *params),
    ).fetchone()
    if not row:
        return None
    return {
        "type": "message",
        "message_id": row["id"],
        "occurred_at": row["occurred_at"],
        "snippet": _search_snippet(row["text"], terms),
        "terms": terms,
    }


def _decorate_conversation_summary(row, participants: list[dict]) -> dict:
    item = _row_dict(row)
    item.pop("search_name_rank", None)
    use_scheduled = bool(row["scheduled_id"]) and (
        not row["last_occurred_at"] or row["scheduled_for"] >= row["last_occurred_at"]
    )
    if use_scheduled:
        scheduled_to_numbers = from_json(row["scheduled_to_numbers"], [])
        scheduled_media_urls = from_json(row["scheduled_media_urls"], [])
        scheduled_failed = row["scheduled_status"] == "failed"
        item["last_text"] = row["scheduled_text"]
        item["last_message_type"] = "MMS" if scheduled_media_urls or len(scheduled_to_numbers) > 1 else "SMS"
        item["last_direction"] = "outbound"
        item["last_status"] = "failed" if scheduled_failed else "scheduled"
        item["last_occurred_at"] = row["scheduled_for"]
        item["last_raw_json"] = None
    item["title"] = row["title"] or _conversation_title_from_participants(participants)
    item["participants"] = participants
    item["sort_at"] = row["list_sort_at"] or row["last_message_at"] or row["updated_at"]
    item["needs_attention"] = _needs_attention(
        item.get("last_direction"),
        item.get("last_occurred_at"),
        row["dealt_with_at"],
        row["manual_unread_at"],
    )
    item["last_status_label"] = _status_label(item.get("last_status"))
    item["last_status_kind"] = _status_kind(item.get("last_status"))
    if use_scheduled:
        item["last_status_detail"] = (
            row["scheduled_failure"] if row["scheduled_status"] == "failed" else f"Scheduled for {row['scheduled_for']}"
        )
    else:
        item["last_status_detail"] = _message_status_detail(row["last_status"], row["last_raw_json"])
    return item


def _list_conversations(conn, query: dict[str, list[str]]) -> dict:
    search = (query.get("search") or [""])[0].strip()
    search_terms = _search_terms(search)
    hidden = (query.get("hidden") or ["0"])[0].lower() in {"1", "true", "yes"}
    unread = (query.get("unread") or ["0"])[0].lower() in {"1", "true", "yes"}
    limit = min(int((query.get("limit") or ["80"])[0]), 200)
    before = (query.get("before") or [""])[0]
    before_id_raw = (query.get("before_id") or ["0"])[0]
    before_id = int(before_id_raw) if before_id_raw.isdigit() else 0
    clauses: list[str] = []
    clauses.append("COALESCE(c.is_archived, 0) = ?")
    params: list = [1 if hidden else 0]
    search_select_params: list[str] = []
    search_rank_select = "0 AS search_name_rank"
    if unread:
        clauses.append(UNREAD_CONVERSATION_CLAUSE)
    if search_terms:
        direct_search_sql, direct_search_params = _conversation_direct_search_expr(search_terms)
        search_rank_select = f"CASE WHEN {direct_search_sql} THEN 1 ELSE 0 END AS search_name_rank"
        search_select_params.extend(direct_search_params)
        search_clause, search_params = _conversation_search_clause(search_terms)
        if search_clause:
            clauses.append(f"({search_clause})")
            params.extend(search_params)
    if before and before_id:
        clauses.append(
            f"""
            (
              {CONVERSATION_SORT_EXPR} < ?
              OR ({CONVERSATION_SORT_EXPR} = ? AND c.id < ?)
            )
            """
        )
        params.extend([before, before, before_id])
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        f"""
        SELECT c.*,
          m.text AS last_text,
          m.id AS last_message_id,
          m.message_type AS last_message_type,
          m.direction AS last_direction,
          m.status AS last_status,
          m.occurred_at AS last_occurred_at,
          m.raw_json AS last_raw_json,
          sm.id AS scheduled_id,
          sm.text AS scheduled_text,
          sm.to_numbers AS scheduled_to_numbers,
          sm.media_urls AS scheduled_media_urls,
          sm.scheduled_for AS scheduled_for,
          sm.status AS scheduled_status,
          sm.failure AS scheduled_failure,
          {CONVERSATION_SORT_EXPR} AS list_sort_at,
          {search_rank_select}
        FROM conversations c
        LEFT JOIN messages m ON m.id = (
          SELECT id FROM messages
          WHERE conversation_id = c.id
            AND COALESCE(source, '') != 'autoreply'
            AND (
              c.last_message_at IS NULL
              OR occurred_at <= c.last_message_at
              OR NOT EXISTS (
                SELECT 1 FROM messages newer_bound
                WHERE newer_bound.conversation_id = c.id
                  AND newer_bound.occurred_at <= c.last_message_at
              )
            )
          ORDER BY occurred_at DESC, id DESC
          LIMIT 1
        )
        LEFT JOIN scheduled_messages sm ON sm.id = (
          SELECT id FROM scheduled_messages
          WHERE conversation_id = c.id
            AND status IN ('queued', 'sending', 'failed')
          ORDER BY scheduled_for DESC, id DESC
          LIMIT 1
        )
        {where}
        ORDER BY search_name_rank DESC, list_sort_at DESC, c.id DESC
        LIMIT ?
        """,
        (*search_select_params, *params, limit + 1),
    ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    participants_by_conversation = _participants_for_conversations(conn, (row["id"] for row in rows))
    conversations = []
    for row in rows:
        direct_search_match = bool(row["search_name_rank"]) if search_terms else False
        item = _decorate_conversation_summary(
            row,
            participants_by_conversation.get(row["id"], []),
        )
        if search_terms and not direct_search_match:
            item["search_match"] = _conversation_message_search_match(conn, row["id"], search_terms)
        conversations.append(item)
    return {"conversations": conversations, "has_more": has_more}


def list_conversations(query: dict[str, list[str]]) -> dict:
    with closing(connect()) as conn:
        return _list_conversations(conn, query)


def _notification_key(row) -> str:
    if not row:
        return ""
    return f"{row['occurred_at']}|{row['id']}"


def _parse_notification_key(value: str) -> tuple[str, int]:
    occurred_at, separator, message_id = str(value or "").rpartition("|")
    if not separator or not message_id.isdigit():
        return "", 0
    return occurred_at, int(message_id)


def _mobile_notifications(conn, query: dict[str, list[str]]) -> dict:
    enabled = get_bool("notifications.native_enabled", config.NATIVE_NOTIFICATIONS_ENABLED)
    interval_minutes = max(get_int("notifications.native_interval_minutes", config.NATIVE_NOTIFICATION_INTERVAL_MINUTES), 15)
    limit = min(int((query.get("limit") or ["20"])[0]), 50)
    since_at, since_id = _parse_notification_key((query.get("since") or [""])[0])
    rows = []
    if enabled and since_at:
        rows = conn.execute(
            f"""
            SELECT m.id,
              m.conversation_id,
              m.text,
              m.message_type,
              m.occurred_at,
              m.from_number,
              c.dealt_with_at,
              c.manual_unread_at,
              (
                SELECT COUNT(*)
                FROM attachments a
                WHERE a.message_id = m.id
              ) AS attachment_count
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.direction = 'inbound'
              AND COALESCE(c.is_archived, 0) = 0
              AND (m.occurred_at > ? OR (m.occurred_at = ? AND m.id > ?))
              AND (
                c.manual_unread_at IS NOT NULL
                OR c.dealt_with_at IS NULL
                OR m.occurred_at > c.dealt_with_at
              )
            ORDER BY m.occurred_at ASC, m.id ASC
            LIMIT ?
            """,
            (since_at, since_at, since_id, limit),
        ).fetchall()
    latest = rows[-1] if rows else conn.execute(
        """
        SELECT m.id, m.occurred_at
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.direction = 'inbound'
          AND COALESCE(c.is_archived, 0) = 0
        ORDER BY m.occurred_at DESC, m.id DESC
        LIMIT 1
        """
    ).fetchone()
    notifications = []
    for row in rows:
        text = str(row["text"] or "").strip()
        attachment_count = int(row["attachment_count"] or 0)
        if not text and attachment_count:
            text = f"{attachment_count} attachment{'s' if attachment_count != 1 else ''}"
        elif not text:
            text = "New text message"
        title = _conversation_title(conn, row["conversation_id"])
        notifications.append(
            {
                "notification_key": _notification_key(row),
                "message_id": row["id"],
                "conversation_id": row["conversation_id"],
                "title": title,
                "from_number": row["from_number"],
                "from_display": _contact_name(conn, row["from_number"]),
                "text": text,
                "attachment_count": attachment_count,
                "occurred_at": row["occurred_at"],
            }
        )
    return {
        "enabled": enabled,
        "poll_interval_minutes": interval_minutes,
        "server_time": now_est(),
        "latest_key": _notification_key(latest),
        "notifications": notifications,
    }


def mobile_notifications(query: dict[str, list[str]]) -> dict:
    with closing(connect()) as conn:
        return _mobile_notifications(conn, query)


def _refresh_tokens(conn, conversation_id: int | None = None) -> dict[str, str]:
    def token_part(row, key: str) -> str:
        value = row[key]
        return "" if value is None else str(value)

    list_row = conn.execute(
        f"""
        SELECT
          (SELECT COUNT(*) FROM conversations) AS conversation_count,
          (SELECT COUNT(*) FROM conversations WHERE COALESCE(is_archived, 0) = 1) AS hidden_count,
          (SELECT COUNT(*) FROM conversations c WHERE COALESCE(c.is_archived, 0) = 0 AND {UNREAD_CONVERSATION_CLAUSE}) AS unread_count,
          (SELECT COALESCE(MAX(updated_at), '') FROM conversations) AS conversations_updated_at,
          (SELECT COUNT(*) FROM messages) AS message_count,
          (SELECT COALESCE(MAX(updated_at), '') FROM messages) AS messages_updated_at,
          (SELECT COUNT(*) FROM scheduled_messages) AS scheduled_count,
          (SELECT COALESCE(MAX(updated_at), '') FROM scheduled_messages) AS scheduled_updated_at,
          (SELECT COUNT(*) FROM identities) AS identity_count,
          (SELECT COALESCE(MAX(updated_at), '') FROM identities) AS identities_updated_at,
          (SELECT COUNT(*) FROM contacts) AS contact_count,
          (SELECT COALESCE(MAX(updated_at), '') FROM contacts) AS contacts_updated_at
        """
    ).fetchone()
    tokens = {
        "list": "|".join(
            token_part(list_row, key)
            for key in (
                "conversation_count",
                "hidden_count",
                "unread_count",
                "conversations_updated_at",
                "message_count",
                "messages_updated_at",
                "scheduled_count",
                "scheduled_updated_at",
            )
        ),
        "bootstrap": "|".join(
            token_part(list_row, key)
            for key in (
                "identity_count",
                "identities_updated_at",
                "contact_count",
                "contacts_updated_at",
                "hidden_count",
                "unread_count",
            )
        ),
        "conversation": "",
    }
    if conversation_id:
        row = conn.execute(
            """
            SELECT
              c.updated_at AS conversation_updated_at,
              COALESCE(c.dealt_with_at, '') AS dealt_with_at,
              COALESCE(c.manual_unread_at, '') AS manual_unread_at,
              COALESCE(c.last_message_at, '') AS last_message_at,
              COUNT(m.id) AS message_count,
              COALESCE(MAX(m.updated_at), '') AS messages_updated_at,
              COALESCE(MAX(m.occurred_at), '') AS messages_occurred_at,
              (SELECT COUNT(*) FROM scheduled_messages sm WHERE sm.conversation_id = c.id) AS scheduled_count,
              (SELECT COALESCE(MAX(sm.updated_at), '') FROM scheduled_messages sm WHERE sm.conversation_id = c.id) AS scheduled_updated_at,
              (SELECT COALESCE(MAX(sm.scheduled_for), '') FROM scheduled_messages sm WHERE sm.conversation_id = c.id AND sm.status IN ('queued', 'sending', 'failed')) AS scheduled_for
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.id = ?
            GROUP BY c.id
            """,
            (conversation_id,),
        ).fetchone()
        if row:
            tokens["conversation"] = "|".join(
                token_part(row, key)
                for key in (
                    "conversation_updated_at",
                    "dealt_with_at",
                    "manual_unread_at",
                    "last_message_at",
                    "message_count",
                    "messages_updated_at",
                    "messages_occurred_at",
                    "scheduled_count",
                    "scheduled_updated_at",
                    "scheduled_for",
                )
            )
    return tokens


def refresh_state(query: dict[str, list[str]]) -> dict:
    conversation_id_raw = (query.get("conversation_id") or ["0"])[0]
    conversation_id = int(conversation_id_raw) if conversation_id_raw.isdigit() else None
    with closing(connect()) as conn:
        return {
            "server_time": now_est(),
            "tokens": _refresh_tokens(conn, conversation_id),
        }


def _get_messages(conn, conversation_id: int, query: dict[str, list[str]] | None = None) -> dict:
    query = query or {}
    limit = min(int((query.get("limit") or [str(MESSAGE_PAGE_SIZE)])[0]), 250)
    before = (query.get("before") or [""])[0]
    before_id_raw = (query.get("before_id") or ["0"])[0]
    before_id = int(before_id_raw) if before_id_raw.isdigit() else 0
    where = "WHERE m.conversation_id = ?"
    params: list = [conversation_id]
    if before and before_id:
        where += " AND (m.occurred_at < ? OR (m.occurred_at = ? AND m.id < ?))"
        params.extend([before, before, before_id])
    rows_desc = conn.execute(
        f"""
        SELECT m.*, i.label AS identity_label, i.color AS identity_color
        FROM messages m
        LEFT JOIN identities i ON i.phone_number = m.from_number
        {where}
        ORDER BY m.occurred_at DESC, m.id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    rows = list(reversed(rows_desc))
    contact_names = _contact_names(
        conn,
        (row["from_number"] for row in rows if not row["identity_label"]),
    )
    attachments_by_message: dict[int, list[dict]] = {row["id"]: [] for row in rows}
    message_ids = list(attachments_by_message)
    if message_ids:
        placeholders = ",".join("?" for _ in message_ids)
        attachment_rows = conn.execute(
            f"SELECT * FROM attachments WHERE message_id IN ({placeholders}) ORDER BY message_id, id",
            message_ids,
        ).fetchall()
        for attachment_row in attachment_rows:
            attachments_by_message[attachment_row["message_id"]].append(_attachment_dict(attachment_row))
    messages = []
    for row in rows:
        message = _row_dict(row)
        message["to_numbers"] = from_json(row["to_numbers"], [])
        message["cc_numbers"] = from_json(row["cc_numbers"], [])
        message["from_display"] = (
            row["identity_label"] or contact_names.get(row["from_number"]) or display_phone(row["from_number"])
        )
        message["attachments"] = attachments_by_message[row["id"]]
        messages.append(_decorate_message_status(message))
    scheduled_messages = []
    if not before and not before_id:
        scheduled_messages = _scheduled_messages_for_conversation(conn, conversation_id)
        messages.extend(scheduled_messages)
        messages.sort(key=lambda item: (item.get("occurred_at") or "", str(item.get("id") or "")))
    older_count = 0
    if rows:
        oldest = rows[0]
        older_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM messages
            WHERE conversation_id = ?
              AND (occurred_at < ? OR (occurred_at = ? AND id < ?))
            """,
            (conversation_id, oldest["occurred_at"], oldest["occurred_at"], oldest["id"]),
        ).fetchone()["count"]
    conversation = _row_dict(conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone())
    last_message = conn.execute(
        """
        SELECT direction, occurred_at
        FROM messages
        WHERE conversation_id = ?
          AND COALESCE(source, '') != 'autoreply'
        ORDER BY occurred_at DESC, id DESC
        LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()
    if last_message:
        conversation["last_direction"] = last_message["direction"]
        conversation["last_occurred_at"] = last_message["occurred_at"]
    if scheduled_messages:
        latest_scheduled = max(
            scheduled_messages,
            key=lambda item: (item.get("occurred_at") or "", str(item.get("id") or "")),
        )
        if (
            not conversation.get("last_occurred_at")
            or latest_scheduled["occurred_at"] >= conversation["last_occurred_at"]
        ):
            conversation["last_direction"] = "outbound"
            conversation["last_occurred_at"] = latest_scheduled["occurred_at"]
    conversation["needs_attention"] = _needs_attention(
        conversation.get("last_direction"),
        conversation.get("last_occurred_at"),
        conversation.get("dealt_with_at"),
        conversation.get("manual_unread_at"),
    )
    participants = _participants(conn, conversation_id)
    conversation["title"] = _conversation_title_from_participants(participants)
    conversation["participants"] = participants
    return {
        "conversation": conversation,
        "messages": messages,
        "has_more": older_count > 0,
        "older_count": older_count,
    }


def get_messages(conversation_id: int, query: dict[str, list[str]] | None = None) -> dict:
    with closing(connect()) as conn:
        return _get_messages(conn, conversation_id, query)


def _bootstrap(conn) -> dict:
    server_time = now_est()
    identities = [
        _identity_dict(row)
        for row in conn.execute(
            """
            SELECT i.*,
              COALESCE(ar.enabled, 0) AS autoreply_enabled,
              COALESCE(ar.message, '') AS autoreply_message,
              COALESCE(ar.cooldown_hours, ?) AS autoreply_cooldown_hours,
              vr.phone_number AS voice_rule_phone_number,
              COALESCE(vr.forwarding_enabled, 0) AS voice_forwarding_enabled,
              COALESCE(vr.forward_to_number, '') AS voice_forward_to_number,
              COALESCE(vr.forward_timeout_seconds, 20) AS voice_forward_timeout_seconds,
              COALESCE(vr.voicemail_enabled, 1) AS voice_voicemail_enabled,
              COALESCE(vr.voicemail_greeting, '') AS voice_voicemail_greeting,
              COALESCE(vr.voicemail_greeting_media_url, '') AS voice_voicemail_greeting_media_url
            FROM identities i
            LEFT JOIN autoreply_rules ar ON ar.phone_number = i.phone_number
            LEFT JOIN voice_rules vr ON vr.phone_number = i.phone_number
            ORDER BY i.id
            """,
            (DEFAULT_AUTOREPLY_COOLDOWN_HOURS,),
        ).fetchall()
    ]
    default_identity = _apply_default_identity(identities)
    stats = _row_dict(
        conn.execute(
            f"""
            SELECT
              (SELECT COUNT(*) FROM conversations) AS conversations,
              (SELECT COUNT(*) FROM conversations WHERE COALESCE(is_archived, 0) = 0) AS inbox_conversations,
              (SELECT COUNT(*) FROM conversations WHERE COALESCE(is_archived, 0) = 1) AS hidden_conversations,
              (SELECT COUNT(*) FROM conversations c WHERE COALESCE(c.is_archived, 0) = 0 AND {UNREAD_CONVERSATION_CLAUSE}) AS unread_conversations,
              (SELECT COUNT(*) FROM messages) AS messages,
              (SELECT COUNT(*) FROM attachments) AS attachments,
              (SELECT COUNT(*) FROM contacts) AS contacts
            """
        ).fetchone()
    )
    providers = configured_providers()
    messaging_providers = configured_messaging_providers()
    return {
        "identities": identities,
        "stats": stats,
        "server_time_et": server_time,
        "server_time_est": server_time,
        "telnyx_configured": bool(get_value("telnyx.api_key", config.TELNYX_API_KEY)),
        "twilio_configured": messaging_providers.get("twilio", False),
        "messaging_provider": get_value("messaging.provider", config.MESSAGING_PROVIDER),
        "messaging_providers": messaging_providers,
        "fastmail_configured": providers.get("fastmail", False),
        "google_contacts_configured": providers.get("google", False),
        "contacts_provider": active_provider(),
        "contact_providers": providers,
        "settings": configured_values(),
        "mark_read_on_open": get_bool("behavior.mark_read_on_open", False),
        "details_collapsed_default": get_bool("behavior.details_collapsed_default", True),
        "default_identity": default_identity,
    }


def bootstrap() -> dict:
    with closing(connect()) as conn:
        return _bootstrap(conn)


STATS_PERIOD_KEYS = {
    "all",
    "today",
    "7d",
    "last_week",
    "30d",
    "this_month",
    "last_month",
    "ytd",
    "this_year",
    "last_year",
}


def _stats_period(query: dict[str, list[str]] | None) -> dict[str, str | None]:
    requested = ((query or {}).get("period") or ["all"])[0].strip().lower()
    key = requested if requested in STATS_PERIOD_KEYS else "all"
    if key == "this_year":
        key = "ytd"

    now = datetime.now(EASTERN).replace(microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0)
    tomorrow_start = today_start + timedelta(days=1)
    this_month_start = today_start.replace(day=1)
    this_year_start = today_start.replace(month=1, day=1)
    current_week_start = today_start - timedelta(days=(today_start.weekday() + 1) % 7)
    start: datetime | None = None
    end: datetime | None = None

    if key == "today":
        start = today_start
        end = tomorrow_start
    elif key == "7d":
        start = today_start - timedelta(days=6)
        end = tomorrow_start
    elif key == "last_week":
        start = current_week_start - timedelta(days=7)
        end = current_week_start
    elif key == "30d":
        start = today_start - timedelta(days=29)
        end = tomorrow_start
    elif key == "this_month":
        start = this_month_start
        end = tomorrow_start
    elif key == "last_month":
        end = this_month_start
        if this_month_start.month == 1:
            start = this_month_start.replace(year=this_month_start.year - 1, month=12)
        else:
            start = this_month_start.replace(month=this_month_start.month - 1)
    elif key == "ytd":
        start = this_year_start
        end = tomorrow_start
    elif key == "last_year":
        start = this_year_start.replace(year=this_year_start.year - 1)
        end = this_year_start

    return {
        "key": key,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
    }


def _stats_message_where(period: dict[str, str | None], alias: str = "") -> tuple[str, list[str]]:
    column = f"{alias}.occurred_at" if alias else "occurred_at"
    clauses: list[str] = []
    params: list[str] = []
    if period.get("start"):
        clauses.append(f"{column} >= ?")
        params.append(period["start"] or "")
    if period.get("end"):
        clauses.append(f"{column} < ?")
        params.append(period["end"] or "")
    return (f"WHERE {' AND '.join(clauses)}" if clauses else "", params)


def _append_where(where_sql: str, condition: str) -> str:
    return f"{where_sql} AND {condition}" if where_sql else f"WHERE {condition}"


def _count(conn, sql: str, params: list[str] | tuple[str, ...] = ()) -> int:
    row = conn.execute(sql, tuple(params)).fetchone()
    return int(row[0] or 0) if row else 0


def _parse_stats_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(EASTERN).replace(microsecond=0)


def _stats_month_start(value: datetime) -> datetime:
    return value.astimezone(EASTERN).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_stats_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return value.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _stats_bucket_start(value: str, bucket: str) -> datetime | None:
    try:
        if bucket == "hour":
            return datetime.fromisoformat(f"{value}:00:00").replace(tzinfo=EASTERN)
        if bucket == "day":
            return datetime.fromisoformat(f"{value}T00:00:00").replace(tzinfo=EASTERN)
        if bucket == "month":
            return datetime.fromisoformat(f"{value}-01T00:00:00").replace(tzinfo=EASTERN)
    except ValueError:
        return None
    return None


def _stats_bucket_key(value: datetime, bucket: str) -> str:
    local = value.astimezone(EASTERN)
    if bucket == "hour":
        return local.strftime("%Y-%m-%dT%H")
    if bucket == "month":
        return local.strftime("%Y-%m")
    return local.strftime("%Y-%m-%d")


def _align_stats_bucket_start(value: datetime, bucket: str) -> datetime:
    local = value.astimezone(EASTERN).replace(microsecond=0)
    if bucket == "hour":
        return local.replace(minute=0, second=0)
    if bucket == "month":
        return _stats_month_start(local)
    return local.replace(hour=0, minute=0, second=0)


def _advance_stats_bucket(value: datetime, bucket: str) -> datetime:
    if bucket == "hour":
        return value + timedelta(hours=1)
    if bucket == "month":
        return _add_stats_months(value, 1)
    return value + timedelta(days=1)


def _align_stats_bucket_end(value: datetime, bucket: str) -> datetime:
    start = _align_stats_bucket_start(value, bucket)
    return start if start == value else _advance_stats_bucket(start, bucket)


def _stats_timeline_bucket(period_key: str) -> str:
    if period_key == "today":
        return "hour"
    if period_key in {"ytd", "last_year", "all"}:
        return "month"
    return "day"


def _stats_timeline(conn, period: dict[str, str | None], where_sql: str, params: list[str]) -> dict:
    bucket = _stats_timeline_bucket(period.get("key") or "all")
    bucket_expr = {
        "hour": "substr(occurred_at, 1, 13)",
        "day": "substr(occurred_at, 1, 10)",
        "month": "substr(occurred_at, 1, 7)",
    }[bucket]
    rows = [
        _row_dict(row)
        for row in conn.execute(
            f"""
            SELECT {bucket_expr} AS bucket,
              COUNT(*) AS count,
              SUM(direction = 'inbound') AS inbound,
              SUM(direction = 'outbound') AS outbound
            FROM messages
            {where_sql}
            GROUP BY bucket
            ORDER BY bucket
            """,
            params,
        ).fetchall()
        if row["bucket"]
    ]
    counts = {str(row["bucket"]): row for row in rows}
    start = _parse_stats_datetime(period.get("start"))
    end = _parse_stats_datetime(period.get("end"))
    now = datetime.now(EASTERN).replace(microsecond=0)

    if start is None and rows:
        start = _stats_bucket_start(str(rows[0]["bucket"]), bucket)
    if end is None and rows:
        last_start = _stats_bucket_start(str(rows[-1]["bucket"]), bucket)
        end = _advance_stats_bucket(last_start, bucket) if last_start else None
    if start is None:
        return {"bucket": bucket, "points": []}

    start = _align_stats_bucket_start(start, bucket)
    if period.get("key") == "today":
        end = _advance_stats_bucket(_align_stats_bucket_start(now, bucket), bucket)
    elif end is None:
        end = _advance_stats_bucket(_align_stats_bucket_start(now, bucket), bucket)
    else:
        end = _align_stats_bucket_end(end, bucket)
    if end <= start:
        end = _advance_stats_bucket(start, bucket)

    points = []
    cursor = start
    while cursor < end:
        key = _stats_bucket_key(cursor, bucket)
        row = counts.get(key, {})
        points.append(
            {
                "bucket": key,
                "count": int(row.get("count") or 0),
                "inbound": int(row.get("inbound") or 0),
                "outbound": int(row.get("outbound") or 0),
            }
        )
        cursor = _advance_stats_bucket(cursor, bucket)
    return {"bucket": bucket, "points": points}


def _message_stats(conn, query: dict[str, list[str]] | None = None) -> dict:
    period = _stats_period(query)
    where_sql, params = _stats_message_where(period)
    where_m_sql, params_m = _stats_message_where(period, "m")
    has_period_filter = bool(period.get("start") or period.get("end"))
    inbound_where = _append_where(where_sql, "direction = 'inbound'")
    outbound_where = _append_where(where_sql, "direction = 'outbound'")
    voicemail_where = _append_where(where_sql, "message_type = 'Voicemail'")
    failed_where = _append_where(where_sql, "status IN ('delivery_failed', 'failed', 'undelivered', 'rejected', 'expired')")
    pending_where = _append_where(where_sql, "status IN ('queued', 'sending', 'sent', 'accepted', 'finalized')")

    totals = {
        "messages": _count(conn, f"SELECT COUNT(*) FROM messages {where_sql}", params),
        "inbound_messages": _count(conn, f"SELECT COUNT(*) FROM messages {inbound_where}", params),
        "outbound_messages": _count(conn, f"SELECT COUNT(*) FROM messages {outbound_where}", params),
        "voicemails": _count(conn, f"SELECT COUNT(*) FROM messages {voicemail_where}", params),
        "failed_messages": _count(
            conn,
            f"""
            SELECT COUNT(*)
            FROM messages
            {failed_where}
            """,
            params,
        ),
        "pending_messages": _count(
            conn,
            f"""
            SELECT COUNT(*)
            FROM messages
            {pending_where}
            """,
            params,
        ),
        "attachments": _count(
            conn,
            f"""
            SELECT COUNT(*)
            FROM attachments a
            JOIN messages m ON m.id = a.message_id
            {where_m_sql}
            """,
            params_m,
        ),
    }
    if has_period_filter:
        totals.update(
            {
                "conversations": _count(
                    conn,
                    f"SELECT COUNT(DISTINCT m.conversation_id) FROM messages m {where_m_sql}",
                    params_m,
                ),
                "inbox_conversations": _count(
                    conn,
                    f"""
                    SELECT COUNT(DISTINCT m.conversation_id)
                    FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    {_append_where(where_m_sql, "COALESCE(c.is_archived, 0) = 0")}
                    """,
                    params_m,
                ),
                "hidden_conversations": _count(
                    conn,
                    f"""
                    SELECT COUNT(DISTINCT m.conversation_id)
                    FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    {_append_where(where_m_sql, "COALESCE(c.is_archived, 0) = 1")}
                    """,
                    params_m,
                ),
                "unread_conversations": _count(
                    conn,
                    f"""
                    SELECT COUNT(DISTINCT m.conversation_id)
                    FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    {_append_where(_append_where(where_m_sql, "COALESCE(c.is_archived, 0) = 0"), UNREAD_CONVERSATION_CLAUSE)}
                    """,
                    params_m,
                ),
                "contacts": _count(
                    conn,
                    f"""
                    SELECT COUNT(DISTINCT cp.phone_number)
                    FROM messages m
                    JOIN conversation_participants cp ON cp.conversation_id = m.conversation_id
                    {_append_where(where_m_sql, "cp.role = 'participant' AND cp.phone_number <> ''")}
                    """,
                    params_m,
                ),
            }
        )
    else:
        totals.update(
            {
                "conversations": _count(conn, "SELECT COUNT(*) FROM conversations"),
                "inbox_conversations": _count(conn, "SELECT COUNT(*) FROM conversations WHERE COALESCE(is_archived, 0) = 0"),
                "hidden_conversations": _count(conn, "SELECT COUNT(*) FROM conversations WHERE COALESCE(is_archived, 0) = 1"),
                "unread_conversations": _count(
                    conn,
                    f"SELECT COUNT(*) FROM conversations c WHERE COALESCE(c.is_archived, 0) = 0 AND {UNREAD_CONVERSATION_CLAUSE}",
                ),
                "contacts": _count(conn, "SELECT COUNT(*) FROM contacts"),
            }
        )

    by_status = [
        _row_dict(row)
        for row in conn.execute(
            f"""
            SELECT status, COUNT(*) AS count
            FROM messages
            {where_sql}
            GROUP BY status
            ORDER BY count DESC, status
            """,
            params,
        ).fetchall()
    ]
    by_source = [
        _row_dict(row)
        for row in conn.execute(
            f"""
            SELECT source, COUNT(*) AS count
            FROM messages
            {where_sql}
            GROUP BY source
            ORDER BY count DESC, source
            """,
            params,
        ).fetchall()
    ]
    by_type = [
        _row_dict(row)
        for row in conn.execute(
            f"""
            SELECT message_type, COUNT(*) AS count
            FROM messages
            {where_sql}
            GROUP BY message_type
            ORDER BY count DESC, message_type
            """,
            params,
        ).fetchall()
    ]
    by_direction = [
        _row_dict(row)
        for row in conn.execute(
            f"""
            SELECT direction, COUNT(*) AS count
            FROM messages
            {where_sql}
            GROUP BY direction
            ORDER BY count DESC, direction
            """,
            params,
        ).fetchall()
    ]
    timeline = _stats_timeline(conn, period, where_sql, params)
    return {
        "period": period,
        "totals": totals,
        "by_status": by_status,
        "by_source": by_source,
        "by_type": by_type,
        "by_direction": by_direction,
        "timeline": timeline,
        "server_time": now_est(),
    }


def message_stats(query: dict[str, list[str]] | None = None) -> dict:
    with closing(connect()) as conn:
        return _message_stats(conn, query)


def _search_contacts(conn, query: dict[str, list[str]]) -> dict:
    terms = _search_terms((query.get("q") or [""])[0])
    if not terms:
        rows = conn.execute(
            """
            SELECT c.id, c.display_name, c.source, cp.phone_number, cp.label
            FROM contacts c
            JOIN contact_phones cp ON cp.contact_id = c.id
            ORDER BY
              CASE c.source
                WHEN 'fastmail' THEN 3
                WHEN 'google' THEN 3
                WHEN 'phone' THEN 2
                ELSE 1
              END DESC,
              c.updated_at DESC
            LIMIT 50
            """
        ).fetchall()
    else:
        where_sql = " AND ".join(["(lower(c.display_name) LIKE ? OR cp.phone_number LIKE ?)"] * len(terms))
        params = []
        for term in terms:
            like = f"%{term}%"
            params.extend([like, like])
        rows = conn.execute(
            f"""
            SELECT c.id, c.display_name, c.source, cp.phone_number, cp.label
            FROM contacts c
            JOIN contact_phones cp ON cp.contact_id = c.id
            WHERE {where_sql}
            ORDER BY
              CASE c.source
                WHEN 'fastmail' THEN 3
                WHEN 'google' THEN 3
                WHEN 'phone' THEN 2
                ELSE 1
              END DESC,
              c.display_name
            LIMIT 50
            """,
            params,
        ).fetchall()
    return {
        "contacts": [
            {
                "id": row["id"],
                "display_name": row["display_name"],
	                "phone_number": row["phone_number"],
	                "phone_display": display_phone(row["phone_number"]),
	                "label": row["label"],
	                "source": row["source"],
	            }
            for row in rows
        ]
    }


def search_contacts(query: dict[str, list[str]]) -> dict:
    with closing(connect()) as conn:
        return _search_contacts(conn, query)


def match_conversation(query: dict[str, list[str]]) -> dict:
    raw_recipients: list[str] = []
    for value in (query.get("recipient") or []) + (query.get("recipients") or []):
        raw_recipients.extend(part.strip() for part in value.split(",") if part.strip())
    recipients = sorted({normalize_phone(value) for value in raw_recipients if normalize_phone(value)})
    if not recipients:
        return {"conversation": None}
    key = conversation_key(recipients)
    with closing(connect()) as conn:
        row = conn.execute("SELECT id FROM conversations WHERE conversation_key = ?", (key,)).fetchone()
    if not row:
        return {"conversation": None}
    return {"conversation_id": int(row["id"]), "conversation": get_messages(int(row["id"]))["conversation"]}


def save_contact_name(payload: dict) -> dict:
    phone = payload.get("phone_number") or payload.get("phone") or ""
    display_name = str(payload.get("display_name") or payload.get("name") or "").strip()
    result = save_synced_contact_name(phone, display_name)
    conversation_id = payload.get("conversation_id")
    if conversation_id:
        result["conversation"] = get_messages(int(conversation_id))["conversation"]
    return result


def create_identity(payload: dict) -> dict:
    raw_phone = str(payload.get("phone_number") or payload.get("phone") or "").strip()
    phone_number = normalize_phone(raw_phone)
    digits = re.sub(r"\D", "", phone_number)
    if not phone_number or not phone_number.startswith("+") or not 7 <= len(digits) <= 15:
        raise ValueError("Enter a valid phone number in E.164 format, such as +15551234567.")

    label = str(payload.get("label") or "").strip() or display_phone(phone_number) or phone_number
    provider = str(payload.get("provider") or "").strip().lower()
    if provider and provider not in {"telnyx", "twilio"}:
        raise ValueError("Provider must be Telnyx or Twilio.")

    provider_mapping: dict[str, str] | None = None
    if provider:
        raw_mapping = get_value(
            "messaging.provider_by_number",
            json.dumps(config.MESSAGING_PROVIDER_BY_NUMBER, separators=(",", ":")),
        )
        try:
            parsed_mapping = json.loads(raw_mapping or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Provider by sender number must contain valid JSON before adding a number.") from exc
        if not isinstance(parsed_mapping, dict):
            raise ValueError("Provider by sender number must be a JSON object before adding a number.")
        provider_mapping = {
            normalize_phone(str(number)): str(mapped_provider).strip().lower()
            for number, mapped_provider in parsed_mapping.items()
            if normalize_phone(str(number))
        }
        provider_mapping[phone_number] = provider

    conn = connect()
    init_db(conn)
    if conn.execute("SELECT 1 FROM identities WHERE phone_number = ?", (phone_number,)).fetchone():
        conn.close()
        raise ValueError("That sender number already exists.")

    timestamp = now_est()
    identity_count = int(conn.execute("SELECT COUNT(*) FROM identities").fetchone()[0])
    color = config.IDENTITY_COLORS[identity_count % len(config.IDENTITY_COLORS)]
    cursor = conn.execute(
        """
        INSERT INTO identities(phone_number, label, color, is_self, is_active, created_at, updated_at)
        VALUES (?, ?, ?, 1, 1, ?, ?)
        """,
        (phone_number, label, color, timestamp, timestamp),
    )

    if provider_mapping is not None:
        conn.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            ("messaging.provider_by_number", json.dumps(provider_mapping, separators=(",", ":")), timestamp),
        )

    conn.commit()
    if provider_mapping is not None:
        invalidate_settings_cache()
    identity = _identity_with_autoreply(conn, int(cursor.lastrowid))
    default_identity = _default_identity_phone(
        [_row_dict(row) for row in conn.execute("SELECT phone_number, is_active FROM identities ORDER BY id").fetchall()]
    )
    identity["is_default"] = identity.get("phone_number") == default_identity
    conn.close()
    identity["provider"] = provider_for_number(phone_number)
    return {"identity": identity, "default_identity": default_identity}


def update_identity(identity_id: int, payload: dict) -> dict:
    label = str(payload.get("label") or "").strip()
    color = str(payload.get("color") or "").strip()
    active = 1 if payload.get("is_active", True) else 0
    if not label:
        raise ValueError("Identity label is required.")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("Identity color must be a hex color.")
    conn = connect()
    init_db(conn)
    existing = _identity_with_autoreply(conn, identity_id)
    if not existing:
        raise ValueError("Identity not found.")
    autoreply_enabled = payload.get("autoreply_enabled", existing.get("autoreply_enabled", False))
    if not isinstance(autoreply_enabled, bool):
        autoreply_enabled = str(autoreply_enabled).strip().lower() in {"1", "true", "yes", "on"}
    autoreply_message = str(
        payload.get(
            "autoreply_message",
            existing.get("autoreply_message") or DEFAULT_AUTOREPLY_MESSAGE,
        )
        or ""
    ).strip()
    try:
        autoreply_cooldown_hours = int(
            str(
                payload.get(
                    "autoreply_cooldown_hours",
                    existing.get("autoreply_cooldown_hours") or DEFAULT_AUTOREPLY_COOLDOWN_HOURS,
                )
            ).strip()
        )
    except ValueError as exc:
        raise ValueError("Auto-reply cooldown must be a number.") from exc
    if autoreply_cooldown_hours < 1:
        raise ValueError("Auto-reply cooldown must be at least 1 hour.")
    if autoreply_enabled and not autoreply_message:
        raise ValueError("Auto-reply message is required when auto-reply is enabled.")
    make_default = payload.get("is_default") is True or str(payload.get("is_default") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    conn.execute(
        "UPDATE identities SET label = ?, color = ?, is_active = ?, updated_at = ? WHERE id = ?",
        (label, color, active, now_est(), identity_id),
    )
    if make_default and active:
        conn.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (DEFAULT_IDENTITY_SETTING_KEY, existing["phone_number"], now_est()),
        )
    update_autoreply_rule(
        conn,
        phone_number=existing["phone_number"],
        enabled=autoreply_enabled,
        message=autoreply_message,
        cooldown_hours=autoreply_cooldown_hours,
    )
    update_voice_rule(
        conn,
        phone_number=existing["phone_number"],
        forwarding_enabled=payload.get("voice_forwarding_enabled", existing.get("voice_forwarding_enabled", False)),
        forward_to_number=payload.get("voice_forward_to_number", existing.get("voice_forward_to_number", "")),
        forward_timeout_seconds=payload.get(
            "voice_forward_timeout_seconds",
            existing.get("voice_forward_timeout_seconds") or 20,
        ),
        voicemail_enabled=payload.get("voice_voicemail_enabled", existing.get("voice_voicemail_enabled", True)),
        voicemail_greeting=payload.get(
            "voice_voicemail_greeting",
            existing.get("voice_voicemail_greeting") or "Please leave a message after the beep.",
        ),
        voicemail_greeting_media_url=payload.get(
            "voice_voicemail_greeting_media_url",
            existing.get("voice_voicemail_greeting_media_url") or "",
        ),
    )
    conn.commit()
    if make_default and active:
        invalidate_settings_cache()
    identity = _identity_with_autoreply(conn, identity_id)
    default_identity = _default_identity_phone(
        [_row_dict(row) for row in conn.execute("SELECT phone_number, is_active FROM identities ORDER BY id").fetchall()]
    )
    identity["is_default"] = identity.get("phone_number") == default_identity
    conn.close()
    return {"identity": identity}


def set_conversation_archived(conversation_id: int, archived: bool) -> dict:
    conn = connect()
    init_db(conn)
    timestamp = now_est()
    conn.execute(
        """
        UPDATE conversations
        SET is_archived = ?, archived_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (1 if archived else 0, timestamp if archived else None, timestamp, conversation_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if not row:
        raise ValueError("Conversation not found.")
    return {"conversation": _row_dict(row)}


def _conversation_summary(conn, conversation_id: int) -> dict:
    row = conn.execute(
        f"""
        SELECT c.*,
          m.text AS last_text,
          m.id AS last_message_id,
          m.message_type AS last_message_type,
          m.direction AS last_direction,
          m.status AS last_status,
          m.occurred_at AS last_occurred_at,
          m.raw_json AS last_raw_json,
          sm.id AS scheduled_id,
          sm.text AS scheduled_text,
          sm.to_numbers AS scheduled_to_numbers,
          sm.media_urls AS scheduled_media_urls,
          sm.scheduled_for AS scheduled_for,
          sm.status AS scheduled_status,
          sm.failure AS scheduled_failure,
          {CONVERSATION_SORT_EXPR} AS list_sort_at
        FROM conversations c
        LEFT JOIN messages m ON m.id = (
          SELECT id FROM messages
          WHERE conversation_id = c.id
            AND COALESCE(source, '') != 'autoreply'
            AND (
              c.last_message_at IS NULL
              OR occurred_at <= c.last_message_at
              OR NOT EXISTS (
                SELECT 1 FROM messages newer_bound
                WHERE newer_bound.conversation_id = c.id
                  AND newer_bound.occurred_at <= c.last_message_at
              )
            )
          ORDER BY occurred_at DESC, id DESC
          LIMIT 1
        )
        LEFT JOIN scheduled_messages sm ON sm.id = (
          SELECT id FROM scheduled_messages
          WHERE conversation_id = c.id
            AND status IN ('queued', 'sending', 'failed')
          ORDER BY scheduled_for DESC, id DESC
          LIMIT 1
        )
        WHERE c.id = ?
        """,
        (conversation_id,),
    ).fetchone()
    if not row:
        return {}
    return _decorate_conversation_summary(row, _participants(conn, conversation_id))


def set_conversation_dealt(conversation_id: int, dealt: bool = True) -> dict:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not row:
            raise ValueError("Conversation not found.")
        timestamp = now_est()
        dealt_with_at = (row["last_message_at"] or timestamp) if dealt else None
        manual_unread_at = None if dealt else (row["last_message_at"] or timestamp)
        conn.execute(
            """
            UPDATE conversations
            SET dealt_with_at = ?, manual_unread_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (dealt_with_at, manual_unread_at, timestamp, conversation_id),
        )
        conn.commit()
        conversation = _conversation_summary(conn, conversation_id)
        unread_count = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM conversations c
            WHERE COALESCE(c.is_archived, 0) = 0
              AND {UNREAD_CONVERSATION_CLAUSE}
            """
        ).fetchone()[0]
        return {"conversation": conversation, "unread_count": int(unread_count)}


def bulk_update_conversations(payload: dict) -> dict:
    ids = []
    for value in payload.get("conversation_ids") or payload.get("ids") or []:
        try:
            conversation_id = int(value)
        except (TypeError, ValueError):
            continue
        if conversation_id > 0 and conversation_id not in ids:
            ids.append(conversation_id)
    if not ids:
        raise ValueError("Select at least one conversation.")
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"read", "unread", "hide", "unhide"}:
        raise ValueError("Bulk action must be read, unread, hide, or unhide.")
    conn = connect()
    init_db(conn)
    timestamp = now_est()
    placeholders = ",".join("?" for _ in ids)
    if action in {"hide", "unhide"}:
        archived = action == "hide"
        conn.execute(
            f"""
            UPDATE conversations
            SET is_archived = ?, archived_at = ?, updated_at = ?
            WHERE id IN ({placeholders})
            """,
            (1 if archived else 0, timestamp if archived else None, timestamp, *ids),
        )
    else:
        dealt = action == "read"
        rows = conn.execute(
            f"""
            SELECT id, last_message_at
            FROM conversations
            WHERE id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        for row in rows:
            marker = row["last_message_at"] or timestamp
            conn.execute(
                """
                UPDATE conversations
                SET dealt_with_at = ?, manual_unread_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (marker if dealt else None, None if dealt else marker, timestamp, row["id"]),
            )
    conn.commit()
    return {"updated": len(ids), "action": action, "conversation_ids": ids}


def create_conversation(payload: dict) -> dict:
    recipients = [normalize_phone(x) for x in payload.get("recipients", []) if normalize_phone(x)]
    if not recipients:
        raise ValueError("At least one recipient is required.")
    from_number = normalize_phone(payload.get("from_number") or "")
    conn = connect()
    init_db(conn)
    conversation_id = ensure_conversation(
        conn,
        recipients,
        [from_number] if from_number else [],
        payload.get("title"),
    )
    conn.commit()
    return {"conversation_id": conversation_id, **get_messages(conversation_id)}


def _mark_reply_message_read(message_id: int) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT conversation_id, occurred_at FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if not row:
            return None
        conversation_id = int(row["conversation_id"])
        marker = row["occurred_at"] or now_est()
        conn.execute(
            """
            UPDATE conversations
            SET dealt_with_at = ?,
                manual_unread_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (marker, now_est(), conversation_id),
        )
        conn.commit()
        return _conversation_summary(conn, conversation_id)


def send_api_message(payload: dict) -> dict:
    conversation_id = payload.get("conversation_id")
    to_numbers = [normalize_phone(x) for x in payload.get("to_numbers", []) if normalize_phone(x)]
    text = str(payload.get("text") or "")
    media_urls = [str(x).strip() for x in payload.get("media_urls", []) if str(x).strip()]
    if not text and not media_urls:
        raise ValueError("Message text or media URL is required.")
    result = send_provider_message(
        from_number=payload.get("from_number"),
        to_numbers=to_numbers,
        text=text,
        media_urls=media_urls,
        conversation_id=int(conversation_id) if conversation_id else None,
    )
    if result.get("message_id"):
        message_id = int(result["message_id"])
        _mark_uploaded_attachments_local(message_id, media_urls)
        conversation = _mark_reply_message_read(message_id)
        if conversation:
            result["conversation_id"] = conversation["id"]
            result["conversation"] = conversation
    return result


def send_api_fax(payload: dict) -> dict:
    conversation_id = payload.get("conversation_id")
    media_url = str(payload.get("media_url") or "").strip()
    to_number = normalize_phone(payload.get("to_number"))
    if not media_url:
        raise ValueError("Choose a fax document.")
    if not to_number:
        raise ValueError("Enter a fax recipient.")
    result = send_telnyx_fax(
        from_number=payload.get("from_number"),
        to_number=to_number,
        media_url=media_url,
        filename=str(payload.get("filename") or ""),
        conversation_id=int(conversation_id) if conversation_id else None,
    )
    if result.get("message_id"):
        message_id = int(result["message_id"])
        _mark_uploaded_attachments_local(message_id, [media_url])
        conversation = _mark_reply_message_read(message_id)
        if conversation:
            result["conversation_id"] = conversation["id"]
            result["conversation"] = conversation
    return result


def _parse_schedule_time(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("Choose a send time.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Schedule time must be a valid date and time.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    scheduled_for = parsed.astimezone(EASTERN).replace(microsecond=0)
    now = datetime.now(EASTERN).replace(microsecond=0)
    if scheduled_for <= now:
        raise ValueError("Schedule time must be in the future.")
    return scheduled_for.isoformat()


def _scheduled_message_dict(row) -> dict:
    message = _row_dict(row)
    message["to_numbers"] = from_json(message.get("to_numbers"), [])
    message["media_urls"] = from_json(message.get("media_urls"), [])
    return message


def schedule_api_message(payload: dict) -> dict:
    conversation_id = payload.get("conversation_id")
    to_numbers = [normalize_phone(x) for x in payload.get("to_numbers", []) if normalize_phone(x)]
    text = str(payload.get("text") or "")
    media_urls = [str(x).strip() for x in payload.get("media_urls", []) if str(x).strip()]
    if not to_numbers:
        raise ValueError("At least one recipient is required.")
    if not text and not media_urls:
        raise ValueError("Message text or media URL is required.")
    scheduled_for = _parse_schedule_time(str(payload.get("scheduled_for") or ""))
    timestamp = now_est()
    conn = connect()
    init_db(conn)
    from_number = normalize_phone(payload.get("from_number") or "")
    if conversation_id:
        conversation_id = int(conversation_id)
    else:
        known_self = self_numbers(conn)
        remote_numbers = sorted(n for n in to_numbers if n not in known_self)
        conversation_id = ensure_conversation(conn, remote_numbers or to_numbers, [from_number] if from_number else [])
    cur = conn.execute(
        """
        INSERT INTO scheduled_messages(
          conversation_id, from_number, to_numbers, text, media_urls,
          scheduled_for, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
        """,
        (
            conversation_id,
            from_number,
            json.dumps(to_numbers, separators=(",", ":")),
            text,
            json.dumps(media_urls, separators=(",", ":")),
            scheduled_for,
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM scheduled_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {"conversation_id": conversation_id, "scheduled_message": _scheduled_message_dict(row)}


def cancel_scheduled_message(scheduled_id: int) -> dict:
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM scheduled_messages WHERE id = ?", (scheduled_id,)).fetchone()
    if not row:
        raise ValueError("Scheduled message not found.")
    status = str(row["status"] or "")
    if status != "queued":
        raise ValueError("Only queued scheduled messages can be canceled.")
    timestamp = now_est()
    updated = conn.execute(
        """
        UPDATE scheduled_messages
        SET status = 'canceled',
            failure = '',
            updated_at = ?
        WHERE id = ? AND status = 'queued'
        """,
        (timestamp, scheduled_id),
    ).rowcount
    if not updated:
        raise ValueError("Only queued scheduled messages can be canceled.")
    conn.commit()
    canceled = conn.execute("SELECT * FROM scheduled_messages WHERE id = ?", (scheduled_id,)).fetchone()
    return {
        "conversation_id": canceled["conversation_id"],
        "scheduled_message": _scheduled_message_dict(canceled),
        "canceled": True,
    }


def send_scheduled_message_now(scheduled_id: int) -> dict:
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM scheduled_messages WHERE id = ?", (scheduled_id,)).fetchone()
    if not row:
        raise ValueError("Scheduled message not found.")
    status = str(row["status"] or "")
    if status != "queued":
        raise ValueError("Only queued scheduled messages can be sent now.")
    conversation_id = int(row["conversation_id"]) if row["conversation_id"] else None
    _send_scheduled_row(conn, row)
    updated = conn.execute("SELECT * FROM scheduled_messages WHERE id = ?", (scheduled_id,)).fetchone()
    return {
        "conversation_id": conversation_id,
        "scheduled_message": _scheduled_message_dict(updated),
        "sent": str(updated["status"] or "") == "sent",
    }


def _send_scheduled_row(conn, row) -> None:
    scheduled = _scheduled_message_dict(row)
    scheduled_id = int(scheduled["id"])
    timestamp = now_est()
    updated = conn.execute(
        """
        UPDATE scheduled_messages
        SET status = 'sending', updated_at = ?
        WHERE id = ? AND status = 'queued'
        """,
        (timestamp, scheduled_id),
    ).rowcount
    conn.commit()
    if not updated:
        return
    try:
        result = send_provider_message(
            from_number=scheduled.get("from_number"),
            to_numbers=scheduled["to_numbers"],
            text=scheduled.get("text") or "",
            media_urls=scheduled["media_urls"],
            conversation_id=int(scheduled["conversation_id"]) if scheduled.get("conversation_id") else None,
        )
        message_id = int(result["message_id"]) if result.get("message_id") else None
        if message_id:
            _mark_uploaded_attachments_local(message_id, scheduled["media_urls"])
            _mark_reply_message_read(message_id)
        conn.execute(
            """
            UPDATE scheduled_messages
            SET status = 'sent',
                provider = ?,
                message_id = ?,
                failure = '',
                sent_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (str(result.get("provider") or ""), message_id, now_est(), now_est(), scheduled_id),
        )
    except Exception as exc:
        conn.execute(
            """
            UPDATE scheduled_messages
            SET status = 'failed',
                failure = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (str(exc)[:500], now_est(), scheduled_id),
        )
        print(f"Scheduled message {scheduled_id} failed: {exc}", flush=True)
    conn.commit()


def process_due_scheduled_messages(limit: int = 10) -> int:
    conn = connect()
    init_db(conn)
    rows = conn.execute(
        """
        SELECT *
        FROM scheduled_messages
        WHERE status = 'queued'
          AND scheduled_for <= ?
        ORDER BY scheduled_for, id
        LIMIT ?
        """,
        (now_est(), limit),
    ).fetchall()
    for row in rows:
        _send_scheduled_row(conn, row)
    conn.close()
    return len(rows)


_scheduled_sender_started = False


def recover_sending_scheduled_messages() -> None:
    conn = connect()
    init_db(conn)
    conn.execute(
        """
        UPDATE scheduled_messages
        SET status = 'queued',
            updated_at = ?
        WHERE status = 'sending'
        """,
        (now_est(),),
    )
    conn.commit()
    conn.close()


def start_scheduled_sender() -> None:
    global _scheduled_sender_started
    if _scheduled_sender_started:
        return
    _scheduled_sender_started = True
    recover_sending_scheduled_messages()

    def worker() -> None:
        while True:
            try:
                process_due_scheduled_messages()
            except Exception as exc:
                print(f"Scheduled message worker failed: {exc}", flush=True)
            time.sleep(10)

    thread = threading.Thread(target=worker, name="scheduled-message-sender", daemon=True)
    thread.start()


def login_limited(key: str) -> bool:
    now = time.time()
    with LOGIN_FAILURE_LOCK:
        failures = [stamp for stamp in LOGIN_FAILURES.get(key, []) if now - stamp < LOGIN_FAILURE_WINDOW_SECONDS]
        LOGIN_FAILURES[key] = failures
        return len(failures) >= LOGIN_FAILURE_LIMIT


def record_login_failure(key: str) -> None:
    now = time.time()
    with LOGIN_FAILURE_LOCK:
        failures = [stamp for stamp in LOGIN_FAILURES.get(key, []) if now - stamp < LOGIN_FAILURE_WINDOW_SECONDS]
        failures.append(now)
        LOGIN_FAILURES[key] = failures


def clear_login_failures(key: str) -> None:
    with LOGIN_FAILURE_LOCK:
        LOGIN_FAILURES.pop(key, None)


def database_backup_bytes() -> tuple[str, bytes]:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"switchboard-{datetime.now(EASTERN):%Y%m%d-%H%M%S}.sqlite"
    temp_path: Path | None = None
    source = None
    destination = None
    try:
        with tempfile.NamedTemporaryFile(prefix="switchboard-backup-", suffix=".sqlite", dir=config.DATA_DIR, delete=False) as handle:
            temp_path = Path(handle.name)
        source = connect()
        destination = sqlite3.connect(temp_path)
        source.backup(destination)
        destination.close()
        source.close()
        destination = None
        source = None
        return filename, temp_path.read_bytes()
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _metadata_values(keys: tuple[str, ...]) -> dict[str, str]:
    conn = connect()
    init_db(conn)
    try:
        placeholders = ",".join("?" for _ in keys)
        return {
            row["key"]: row["value"]
            for row in conn.execute(f"SELECT key, value FROM app_metadata WHERE key IN ({placeholders})", keys).fetchall()
        }
    finally:
        conn.close()


def _write_metadata_values(values: dict[str, str]) -> None:
    if not values:
        return
    timestamp = now_est()
    conn = connect()
    init_db(conn)
    try:
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO app_metadata(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, timestamp),
            )
        conn.commit()
    finally:
        conn.close()


def _split_backup_hashes(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").replace("\n", ",").split(",") if part.strip()]


def load_app_auth_config() -> None:
    values = _metadata_values(AUTH_ACCOUNT_METADATA_KEYS)
    username = values.get(AUTH_USERNAME_METADATA_KEY, "").strip()
    password_hash = values.get(AUTH_PASSWORD_HASH_METADATA_KEY, "").strip()
    if not username or not password_hash:
        return
    secret_key = values.get(AUTH_SECRET_KEY_METADATA_KEY, "").strip() or config.AUTH_SECRET_KEY
    _apply_auth_config(username, password_hash, secret_key)


def two_factor_material() -> dict:
    values = _metadata_values((AUTH_TOTP_METADATA_KEY, AUTH_BACKUP_CODES_METADATA_KEY))
    saved_secret = values.get(AUTH_TOTP_METADATA_KEY, "").strip()
    saved_hashes_raw = values.get(AUTH_BACKUP_CODES_METADATA_KEY, "").strip()
    saved_hashes = _split_backup_hashes(saved_hashes_raw)
    secret = saved_secret or config.AUTH_TOTP_SECRET
    backup_hashes = saved_hashes if saved_hashes_raw else list(config.AUTH_BACKUP_CODE_HASHES)
    app_managed = bool(saved_secret or saved_hashes_raw)
    env_enabled = auth.two_factor_enabled(config.AUTH_TOTP_SECRET, config.AUTH_BACKUP_CODE_HASHES)
    return {
        "secret": secret,
        "backup_hashes": backup_hashes,
        "enabled": auth.two_factor_enabled(secret, backup_hashes),
        "app_managed": app_managed,
        "env_enabled": env_enabled,
        "source": "settings" if app_managed else "env" if env_enabled else "none",
    }


def auth_status_payload() -> dict:
    status = auth.auth_status()
    material = two_factor_material()
    status.update(
        {
            "two_factor_enabled": bool(auth.auth_configured() and material["enabled"]),
            "two_factor_source": material["source"],
            "two_factor_app_managed": material["app_managed"],
            "two_factor_env_managed": bool(material["env_enabled"] and not material["app_managed"]),
            "backup_codes_configured": len(material["backup_hashes"]) if auth.auth_configured() else 0,
        }
    )
    return status


def two_factor_status_payload() -> dict:
    material = two_factor_material()
    available = bool(auth.auth_configured() and not auth.auth_disabled())
    enabled = bool(available and material["enabled"])
    return {
        "available": available,
        "configured": auth.auth_configured(),
        "auth_disabled": auth.auth_disabled(),
        "enabled": enabled,
        "source": material["source"] if enabled else "none",
        "app_managed": bool(enabled and material["app_managed"]),
        "env_managed": bool(enabled and material["env_enabled"] and not material["app_managed"]),
        "can_disable": bool(enabled and material["app_managed"]),
        "backup_codes_configured": len(material["backup_hashes"]) if enabled else 0,
        "username": config.AUTH_USERNAME if auth.auth_configured() else "",
    }


def _require_auth_password(payload: dict) -> None:
    if auth.auth_disabled():
        raise ValueError("Sign-in is disabled, so two-factor authentication cannot be managed.")
    if not auth.auth_configured():
        raise ValueError("Set TEXTING_AUTH_USERNAME and TEXTING_AUTH_PASSWORD_HASH before enabling 2FA.")
    if not auth.verify_password(str(payload.get("password") or ""), config.AUTH_PASSWORD_HASH):
        raise ValueError("Current password is incorrect.")


def _ensure_auth_secret_key(metadata_updates: dict[str, str]) -> None:
    if config.AUTH_SECRET_KEY:
        return
    secret_key = secrets.token_urlsafe(48)
    metadata_updates[AUTH_SECRET_KEY_METADATA_KEY] = secret_key
    config.AUTH_SECRET_KEY = secret_key


def _apply_auth_config(username: str, password_hash: str, secret_key: str | None = None) -> None:
    config.AUTH_USERNAME = username
    config.AUTH_PASSWORD_HASH = password_hash
    if secret_key is not None:
        config.AUTH_SECRET_KEY = secret_key


def _validate_account_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")


def setup_auth_account(payload: dict) -> dict:
    if auth.auth_configured() and not auth.auth_disabled():
        raise ValueError("Sign-in is already configured.")
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    confirm = str(payload.get("confirm_password") or payload.get("confirm") or "")
    if not username:
        raise ValueError("Enter a username.")
    _validate_account_password(password)
    if password != confirm:
        raise ValueError("Passwords do not match.")
    password_hash = auth.hash_password(password)
    metadata_updates = {
        AUTH_USERNAME_METADATA_KEY: username,
        AUTH_PASSWORD_HASH_METADATA_KEY: password_hash,
    }
    _ensure_auth_secret_key(metadata_updates)
    _write_metadata_values(metadata_updates)
    _apply_auth_config(username, password_hash, metadata_updates.get(AUTH_SECRET_KEY_METADATA_KEY))
    config.AUTH_DISABLED = False
    return auth_status_payload()


def update_auth_account(payload: dict) -> dict:
    _require_auth_password(payload)
    username = str(payload.get("username") or config.AUTH_USERNAME or "").strip()
    new_password = str(payload.get("new_password") or "")
    confirm = str(payload.get("confirm_password") or "")
    if not username:
        raise ValueError("Enter a username.")
    password_hash = config.AUTH_PASSWORD_HASH
    if new_password:
        _validate_account_password(new_password)
        if new_password != confirm:
            raise ValueError("New passwords do not match.")
        password_hash = auth.hash_password(new_password)
    metadata_updates = {
        AUTH_USERNAME_METADATA_KEY: username,
        AUTH_PASSWORD_HASH_METADATA_KEY: password_hash,
    }
    _ensure_auth_secret_key(metadata_updates)
    _write_metadata_values(metadata_updates)
    _apply_auth_config(username, password_hash, metadata_updates.get(AUTH_SECRET_KEY_METADATA_KEY))
    return auth_status_payload()


def qr_svg_data_uri(value: str) -> str:
    try:
        import qrcode
        import qrcode.image.svg
    except Exception:
        return ""
    image = qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    output = BytesIO()
    image.save(output)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _store_two_factor_material(secret: str, backup_hashes: list[str]) -> dict:
    timestamp = now_est()
    conn = connect()
    init_db(conn)
    try:
        conn.execute(
            """
            INSERT INTO app_metadata(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (AUTH_TOTP_METADATA_KEY, auth.normalize_totp_secret(secret), timestamp),
        )
        conn.execute(
            """
            INSERT INTO app_metadata(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (AUTH_BACKUP_CODES_METADATA_KEY, ",".join(backup_hashes), timestamp),
        )
        conn.execute("DELETE FROM app_metadata WHERE key LIKE ?", (BACKUP_CODE_METADATA_PREFIX + "%",))
        conn.commit()
    finally:
        conn.close()
    return two_factor_status_payload()


def _store_backup_hashes(backup_hashes: list[str]) -> dict:
    timestamp = now_est()
    conn = connect()
    init_db(conn)
    try:
        conn.execute(
            """
            INSERT INTO app_metadata(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (AUTH_BACKUP_CODES_METADATA_KEY, ",".join(backup_hashes), timestamp),
        )
        conn.execute("DELETE FROM app_metadata WHERE key LIKE ?", (BACKUP_CODE_METADATA_PREFIX + "%",))
        conn.commit()
    finally:
        conn.close()
    return two_factor_status_payload()


def _clear_app_two_factor_material() -> dict:
    material = two_factor_material()
    if material["env_enabled"] and not material["app_managed"]:
        raise ValueError("2FA is configured in .env. Remove the 2FA env values to disable it.")
    conn = connect()
    init_db(conn)
    try:
        conn.execute("DELETE FROM app_metadata WHERE key IN (?, ?)", (AUTH_TOTP_METADATA_KEY, AUTH_BACKUP_CODES_METADATA_KEY))
        conn.execute("DELETE FROM app_metadata WHERE key LIKE ?", (BACKUP_CODE_METADATA_PREFIX + "%",))
        conn.commit()
    finally:
        conn.close()
    return two_factor_status_payload()


def start_two_factor_setup(payload: dict) -> dict:
    _require_auth_password(payload)
    secret = auth.generate_totp_secret()
    backup_codes = auth.generate_backup_codes(10)
    backup_hashes = [auth.backup_code_hash(code) for code in backup_codes]
    setup_token = auth.create_signed_payload(
        {"secret": secret, "backup_hashes": backup_hashes},
        "2fa-setup",
        TWO_FACTOR_SETUP_TOKEN_SECONDS,
    )
    uri = auth.totp_uri(config.AUTH_USERNAME, secret, config.AUTH_TOTP_ISSUER)
    return {
        "setup": {
            "secret": secret,
            "uri": uri,
            "qr_svg": qr_svg_data_uri(uri),
            "backup_codes": backup_codes,
            "setup_token": setup_token,
            "expires_seconds": TWO_FACTOR_SETUP_TOKEN_SECONDS,
        },
        "status": two_factor_status_payload(),
    }


def enable_two_factor(payload: dict) -> dict:
    setup = auth.verify_signed_payload(str(payload.get("setup_token") or ""), "2fa-setup")
    if not setup:
        raise ValueError("2FA setup expired. Start setup again.")
    secret = auth.normalize_totp_secret(str(setup.get("secret") or ""))
    backup_hashes = [str(value) for value in setup.get("backup_hashes") or [] if str(value).strip()]
    if not auth.verify_totp(str(payload.get("code") or ""), secret):
        raise ValueError("Authenticator code is incorrect.")
    return {"status": _store_two_factor_material(secret, backup_hashes)}


def regenerate_backup_codes(payload: dict) -> dict:
    _require_auth_password(payload)
    material = two_factor_material()
    if not material["enabled"]:
        raise ValueError("Enable 2FA before generating backup codes.")
    backup_codes = auth.generate_backup_codes(10)
    backup_hashes = [auth.backup_code_hash(code) for code in backup_codes]
    return {"backup_codes": backup_codes, "status": _store_backup_hashes(backup_hashes)}


def disable_two_factor(payload: dict) -> dict:
    _require_auth_password(payload)
    material = two_factor_material()
    if not material["enabled"]:
        return {"status": two_factor_status_payload()}
    second_factor = str(payload.get("second_factor") or payload.get("code") or "")
    factor = auth.verify_second_factor(second_factor, material["secret"], material["backup_hashes"])
    if not factor:
        raise ValueError("Two-factor code is incorrect.")
    factor_type, backup_hash = factor
    if factor_type == "backup" and backup_hash and not claim_backup_code(backup_hash):
        raise ValueError("That backup code has already been used.")
    return {"status": _clear_app_two_factor_material()}


def claim_backup_code(encoded: str) -> bool:
    fingerprint = auth.backup_code_fingerprint(encoded)
    conn = connect()
    init_db(conn)
    try:
        conn.execute(
            "INSERT INTO app_metadata(key, value, updated_at) VALUES (?, ?, ?)",
            (BACKUP_CODE_METADATA_PREFIX + fingerprint, "1", now_est()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


class TextingHandler(BaseHTTPRequestHandler):
    server_version = "Switchboard/0.1"
    protocol_version = "HTTP/1.1"
    connection_timeout_seconds = 30

    def setup(self) -> None:
        self.request.settimeout(self.connection_timeout_seconds)
        super().setup()

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def log_upload(self, fmt: str, *args) -> None:
        self.log_message("upload " + fmt, *args)

    def _begin_request(self, *, allow_body: bool) -> bool:
        """Validate request framing before an HTTP/1.1 connection is reused."""

        self._request_started_at = time.perf_counter()
        self._request_body_length = 0
        self._request_body_bytes_read = 0
        self._request_body_framing_invalid = False

        transfer_encoding = (self.headers.get("Transfer-Encoding") or "").strip()
        content_lengths = self.headers.get_all("Content-Length", [])
        error = ""
        error_status = HTTPStatus.BAD_REQUEST
        if transfer_encoding:
            error = "Transfer-Encoding request bodies are not supported."
        elif len(content_lengths) > 1:
            error = "Multiple Content-Length headers are not allowed."
        elif content_lengths:
            raw_content_length = str(content_lengths[0]).strip()
            if not re.fullmatch(r"[0-9]+", raw_content_length):
                error = "Invalid Content-Length header."
            else:
                self._request_body_length = int(raw_content_length)
        if not error and not allow_body and self._request_body_length:
            error = "This request method does not accept a body."
        if not error and allow_body:
            path = urlparse(self.path).path
            body_limit = (
                _upload_max_bytes() + UPLOAD_REQUEST_OVERHEAD
                if path == "/api/uploads"
                else DEFAULT_REQUEST_BODY_LIMIT
            )
            if self._request_body_length > body_limit:
                error = f"Request body exceeds the {body_limit // (1024 * 1024)} MB limit."
                error_status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        if not error:
            return True

        self._request_body_framing_invalid = True
        self.close_connection = True
        self._send_json(
            {"error": error},
            error_status,
            headers={"Connection": "close"},
        )
        return False

    def _request_has_unread_body(self) -> bool:
        return bool(
            getattr(self, "_request_body_framing_invalid", False)
            or getattr(self, "_request_body_bytes_read", 0) < getattr(self, "_request_body_length", 0)
        )

    def _send_headers(
        self,
        status: int,
        content_type: str,
        length: int | None = None,
        headers: dict[str, str] | None = None,
        cache_control: str | None = None,
    ) -> None:
        response_headers = dict(headers or {})
        if self._request_has_unread_body():
            self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        frame_options = next(
            (value for key, value in response_headers.items() if key.lower() == "x-frame-options"),
            "DENY",
        )
        self.send_header("X-Frame-Options", frame_options)
        no_store_types = (
            "application/json",
            "text/html",
            "text/css",
            "text/xml",
            "application/javascript",
            "text/javascript",
        )
        cache_control = cache_control or ("no-store" if content_type.startswith(no_store_types) else "public, max-age=3600")
        self.send_header("Cache-Control", cache_control)
        if length is not None:
            self.send_header("Content-Length", str(length))
        if "Server-Timing" not in response_headers and getattr(self, "_request_started_at", None) is not None:
            duration_ms = max(0.0, (time.perf_counter() - self._request_started_at) * 1000)
            self.send_header("Server-Timing", f"app;dur={duration_ms:.2f}")
        if self.close_connection and not any(key.lower() == "connection" for key in response_headers):
            self.send_header("Connection", "close")
        for key, value in response_headers.items():
            if key.lower() == "x-frame-options":
                continue
            self.send_header(key, value)
        self.end_headers()

    def _send_json(self, payload: dict, status: int = 200, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(
            payload,
            default=_json_default,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self._send_bytes(
            body,
            "application/json; charset=utf-8",
            status,
            headers=headers,
            cache_control="no-store",
        )

    def _send_xml(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self._send_bytes(data, "text/xml; charset=utf-8", status)

    def _send_html(self, body: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        data = body.encode("utf-8")
        self._send_bytes(data, "text/html; charset=utf-8", status, headers=headers, cache_control="no-store")

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: int = 200,
        headers: dict[str, str] | None = None,
        cache_control: str | None = None,
    ) -> None:
        response_headers = dict(headers or {})
        encoded_body, compressed = maybe_gzip(body, content_type, self.headers.get("Accept-Encoding"))
        if compressed:
            response_headers["Content-Encoding"] = "gzip"
            vary = [part.strip() for part in response_headers.get("Vary", "").split(",") if part.strip()]
            if "Accept-Encoding" not in vary:
                vary.append("Accept-Encoding")
            response_headers["Vary"] = ", ".join(vary)
        self._send_headers(status, content_type, len(encoded_body), headers=response_headers, cache_control=cache_control)
        self.wfile.write(encoded_body)

    def _send_redirect(self, location: str, status: int = HTTPStatus.FOUND, headers: dict[str, str] | None = None) -> None:
        merged = {"Location": location, **(headers or {})}
        self._send_headers(status, "text/plain; charset=utf-8", 0, headers=merged, cache_control="no-store")

    def _read_json(self) -> dict:
        raw = self._read_raw() or b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _read_raw(self) -> bytes:
        length = getattr(self, "_request_body_length", 0)
        remaining = max(0, length - getattr(self, "_request_body_bytes_read", 0))
        raw = self.rfile.read(remaining) if remaining else b""
        self._request_body_bytes_read = getattr(self, "_request_body_bytes_read", 0) + len(raw)
        if self._request_body_bytes_read < length:
            self.close_connection = True
        return raw

    def _request_url(self) -> str:
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip()
        if not proto:
            proto = "https" if self.headers.get("X-Forwarded-Ssl", "").lower() == "on" else "http"
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",", 1)[0].strip()
        return f"{proto}://{host}{self.path}" if host else self.path

    def _request_is_secure(self) -> bool:
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
        return proto == "https" or self.headers.get("X-Forwarded-Ssl", "").lower() == "on"

    def _request_host(self) -> str:
        return (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",", 1)[0].strip().lower()

    def _client_key(self) -> str:
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
        return forwarded or self.client_address[0]

    def _session_token(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        cookies = SimpleCookie()
        try:
            cookies.load(raw)
        except Exception:
            return None
        morsel = cookies.get(auth.SESSION_COOKIE_NAME)
        return morsel.value if morsel else None

    def _current_user(self) -> str | None:
        return auth.verify_session_token(self._session_token())

    def _is_public_request(self, method: str, path: str) -> bool:
        if path.startswith("/static/") or path.startswith("/uploads/"):
            return True
        if method == "GET":
            return path in PUBLIC_GET_PATHS
        if method == "POST":
            return path in PUBLIC_POST_PATHS
        return False

    def _same_origin_request(self) -> bool:
        host = self._request_host()
        if not host:
            return True
        for header in ("Origin", "Referer"):
            value = self.headers.get(header, "").strip()
            if not value:
                continue
            parsed = urlparse(value)
            if parsed.netloc and parsed.netloc.lower() != host:
                return False
        return True

    def _redirect_to_login(self) -> None:
        next_path = self.path if self.path.startswith("/") else "/"
        self._send_redirect(f"/login?next={quote(next_path, safe='')}")

    def _require_auth(self, method: str, path: str) -> bool:
        if auth.auth_disabled() or self._is_public_request(method, path):
            return True
        if not auth.auth_configured():
            if method == "GET" and not path.startswith("/api/"):
                self._send_redirect("/login?setup=1")
            else:
                self._send_json(
                    {"error": "Switchboard sign-in is not configured. Complete setup or set TEXTING_AUTH_USERNAME and TEXTING_AUTH_PASSWORD_HASH."},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            return False
        if not self._current_user():
            if method == "GET" and not path.startswith("/api/") and not path.startswith("/media/"):
                self._redirect_to_login()
            else:
                self._send_json({"error": "Login required."}, HTTPStatus.UNAUTHORIZED)
            return False
        if method in {"POST", "PUT", "DELETE"} and not self._same_origin_request():
            self._send_json({"error": "Cross-origin request blocked."}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def _serve_file(
        self,
        path: Path,
        *,
        cache_control: str | None = None,
        allow_ranges: bool = False,
    ) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        stat = path.stat()
        compressible_file = content_type.startswith(
            ("application/javascript", "application/json", "image/svg+xml", "text/")
        )
        etag = file_etag(stat)
        if compressible_file:
            etag = f"W/{etag}"
        headers = {"ETag": etag}
        if content_type == "application/pdf":
            headers["X-Frame-Options"] = "SAMEORIGIN"
            headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        if compressible_file:
            headers["Vary"] = "Accept-Encoding"
        if_none_match = {part.strip() for part in self.headers.get("If-None-Match", "").split(",")}
        if "*" in if_none_match or etag in if_none_match:
            self._send_headers(
                HTTPStatus.NOT_MODIFIED,
                content_type,
                None,
                headers=headers,
                cache_control=cache_control,
            )
            return

        byte_range = None
        if allow_ranges:
            headers["Accept-Ranges"] = "bytes"
            range_header = self.headers.get("Range")
            if_range = self.headers.get("If-Range")
            if if_range and (etag.startswith("W/") or if_range.strip() != etag):
                range_header = None
            try:
                byte_range = parse_byte_range(range_header, stat.st_size)
            except ValueError:
                headers["Content-Range"] = f"bytes */{stat.st_size}"
                self._send_headers(
                    HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                    content_type,
                    0,
                    headers=headers,
                    cache_control=cache_control,
                )
                return

        if byte_range is None and compressible_file:
            self._send_bytes(
                path.read_bytes(),
                content_type,
                headers=headers,
                cache_control=cache_control,
            )
            return

        start, end = byte_range or (0, stat.st_size - 1)
        length = max(0, end - start + 1)
        status = HTTPStatus.PARTIAL_CONTENT if byte_range is not None else HTTPStatus.OK
        if byte_range is not None:
            headers["Content-Range"] = f"bytes {start}-{end}/{stat.st_size}"
        self._send_headers(status, content_type, length, headers=headers, cache_control=cache_control)
        if not length:
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True
                    return
                remaining -= len(chunk)

    def _serve_login(self) -> None:
        self._serve_file(STATIC_DIR / "login.html", cache_control="no-store")

    def _read_login_payload(self) -> tuple[str, str, str, str, bool]:
        content_type = (self.headers.get("Content-Type") or "").lower()
        raw = self._read_raw()
        if "application/json" in content_type:
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            second_factor = payload.get("second_factor") or payload.get("two_factor") or payload.get("code") or payload.get("otp")
            return (
                str(payload.get("username") or ""),
                str(payload.get("password") or ""),
                str(second_factor or ""),
                str(payload.get("next") or "/"),
                True,
            )
        parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        second_factor = (
            parsed.get("second_factor")
            or parsed.get("two_factor")
            or parsed.get("code")
            or parsed.get("otp")
            or [""]
        )[0]
        return (
            (parsed.get("username") or [""])[0],
            (parsed.get("password") or [""])[0],
            second_factor,
            (parsed.get("next") or ["/"])[0],
            False,
        )

    def _handle_login(self) -> None:
        if not auth.auth_configured() and not auth.auth_disabled():
            self._send_json(
                {"error": "Switchboard sign-in is not configured. Complete setup or set TEXTING_AUTH_USERNAME and TEXTING_AUTH_PASSWORD_HASH."},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        username, password, second_factor, next_path, wants_json = self._read_login_payload()
        next_path = next_path if next_path.startswith("/") else "/"
        login_next = quote(next_path, safe="")
        client_key = self._client_key()
        if login_limited(client_key):
            self._send_json({"error": "Too many sign-in attempts. Try again in a few minutes."}, HTTPStatus.TOO_MANY_REQUESTS)
            return
        valid = auth.auth_disabled() or (
            secrets.compare_digest(username, config.AUTH_USERNAME) and auth.verify_password(password, config.AUTH_PASSWORD_HASH)
        )
        if not valid:
            record_login_failure(client_key)
            if wants_json:
                self._send_json({"error": "Invalid username or password."}, HTTPStatus.UNAUTHORIZED)
            else:
                self._send_redirect(f"/login?error=1&next={login_next}", HTTPStatus.SEE_OTHER)
            return
        material = two_factor_material()
        if not auth.auth_disabled() and material["enabled"]:
            if not second_factor.strip():
                if wants_json:
                    self._send_json(
                        {"two_factor_required": True, "error": "Two-factor code required."},
                        HTTPStatus.ACCEPTED,
                    )
                else:
                    self._send_redirect(f"/login?2fa=1&next={login_next}", HTTPStatus.SEE_OTHER)
                return
            factor = auth.verify_second_factor(second_factor, material["secret"], material["backup_hashes"])
            if not factor:
                record_login_failure(client_key)
                if wants_json:
                    self._send_json({"error": "Invalid two-factor code."}, HTTPStatus.UNAUTHORIZED)
                else:
                    self._send_redirect(f"/login?2fa=1&error=1&next={login_next}", HTTPStatus.SEE_OTHER)
                return
            factor_type, backup_hash = factor
            if factor_type == "backup" and backup_hash and not claim_backup_code(backup_hash):
                record_login_failure(client_key)
                if wants_json:
                    self._send_json({"error": "That backup code has already been used."}, HTTPStatus.UNAUTHORIZED)
                else:
                    self._send_redirect(f"/login?2fa=1&error=1&next={login_next}", HTTPStatus.SEE_OTHER)
                return
        clear_login_failures(client_key)
        token = auth.create_session_token(config.AUTH_USERNAME or username or "local", SESSION_MAX_AGE_SECONDS)
        cookie = auth.session_cookie(token, self._request_is_secure(), SESSION_MAX_AGE_SECONDS)
        if wants_json:
            self._send_json({"ok": True, "user": config.AUTH_USERNAME or username}, headers={"Set-Cookie": cookie})
        else:
            self._send_redirect(next_path, HTTPStatus.SEE_OTHER, headers={"Set-Cookie": cookie})

    def _handle_logout(self) -> None:
        cookie = auth.clear_session_cookie(self._request_is_secure())
        self._send_json({"ok": True}, headers={"Set-Cookie": cookie})

    def _handle_account_setup(self) -> None:
        status = setup_auth_account(self._read_json())
        token = auth.create_session_token(config.AUTH_USERNAME, SESSION_MAX_AGE_SECONDS)
        cookie = auth.session_cookie(token, self._request_is_secure(), SESSION_MAX_AGE_SECONDS)
        self._send_json({"ok": True, "auth": status}, headers={"Set-Cookie": cookie})

    def _send_database_download(self) -> None:
        filename, data = database_backup_bytes()
        self._send_bytes(
            data,
            "application/vnd.sqlite3",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            cache_control="no-store",
        )

    def do_GET(self) -> None:
        if not self._begin_request(allow_body=False):
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        versioned_static_cache = (
            "public, max-age=31536000, immutable"
            if query.get("v")
            else "public, max-age=300, must-revalidate"
        )
        try:
            if not self._require_auth("GET", path):
                return
            if path == "/api/health":
                self._send_json({"ok": True, "app": config.APP_SLUG})
            elif path == "/api/auth/session":
                self._send_json({"authenticated": bool(self._current_user()), "auth": auth_status_payload()})
            elif path == "/api/auth/2fa":
                self._send_json(two_factor_status_payload())
            elif path == "/api/bootstrap":
                self._send_json(bootstrap())
            elif path == "/api/settings":
                self._send_json(configured_values())
            elif path == "/api/stats":
                self._send_json(message_stats(query))
            elif path == "/api/mobile/notifications":
                self._send_json(mobile_notifications(query))
            elif path == "/api/refresh":
                self._send_json(refresh_state(query))
            elif path == "/api/uploads/diagnostics":
                self._send_json(upload_diagnostics(self._request_url()))
            elif path == "/api/conversations":
                self._send_json(list_conversations(query))
            elif path == "/api/conversations/match":
                self._send_json(match_conversation(query))
            elif match := re.fullmatch(r"/api/conversations/(\d+)/messages", path):
                self._send_json(get_messages(int(match.group(1)), query))
            elif path == "/api/contacts":
                self._send_json(search_contacts(query))
            elif path == "/api/database/download":
                self._send_database_download()
            elif path in {"/api/twilio/voice", "/api/telnyx/voice"}:
                provider = "twilio" if "twilio" in path else "telnyx"
                params = {key: values[-1] if values else "" for key, values in query.items()}
                self._send_xml(voice_xml(provider, params, self._request_url()))
            elif path.startswith("/media/"):
                name = Path(unquote(path.removeprefix("/media/"))).name
                self._serve_file(
                    config.MEDIA_DIR / name,
                    cache_control="private, max-age=3600",
                    allow_ranges=True,
                )
            elif path.startswith("/uploads/"):
                name = Path(unquote(path.removeprefix("/uploads/"))).name
                self._serve_file(
                    _configured_upload_dir() / name,
                    cache_control="public, max-age=3600",
                    allow_ranges=True,
                )
            elif path in {"/favicon.ico", "/favicon.svg", "/apple-touch-icon.png"}:
                self._serve_file(
                    STATIC_DIR / path.removeprefix("/"),
                    cache_control=versioned_static_cache,
                )
            elif path.startswith("/static/"):
                rel = Path(unquote(path.removeprefix("/static/")))
                self._serve_file(STATIC_DIR / rel.name, cache_control=versioned_static_cache)
            elif path == "/login":
                if self._current_user() and auth.auth_configured():
                    self._send_redirect("/")
                else:
                    self._serve_login()
            elif path in {"/", "/index.html"}:
                self._serve_file(STATIC_DIR / "index.html", cache_control="no-store")
            else:
                self._serve_file(STATIC_DIR / "index.html", cache_control="no-store")
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        if not self._begin_request(allow_body=True):
            return
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if not self._require_auth("POST", path):
                return
            if path == "/api/auth/login":
                self._handle_login()
            elif path == "/api/auth/setup":
                self._handle_account_setup()
            elif path == "/api/auth/logout":
                self._handle_logout()
            elif path == "/api/auth/account":
                status = update_auth_account(self._read_json())
                token = auth.create_session_token(config.AUTH_USERNAME, SESSION_MAX_AGE_SECONDS)
                cookie = auth.session_cookie(token, self._request_is_secure(), SESSION_MAX_AGE_SECONDS)
                self._send_json({"auth": status}, headers={"Set-Cookie": cookie})
            elif path == "/api/auth/2fa/setup":
                self._send_json(start_two_factor_setup(self._read_json()))
            elif path == "/api/auth/2fa/enable":
                self._send_json(enable_two_factor(self._read_json()))
            elif path == "/api/auth/2fa/backup-codes":
                self._send_json(regenerate_backup_codes(self._read_json()))
            elif path == "/api/auth/2fa/disable":
                self._send_json(disable_two_factor(self._read_json()))
            elif path == "/api/messages":
                self._send_json(send_api_message(self._read_json()))
            elif path == "/api/fax/send":
                self._send_json(send_api_fax(self._read_json()))
            elif path == "/api/messages/schedule":
                self._send_json(schedule_api_message(self._read_json()))
            elif match := re.fullmatch(r"/api/messages/schedule/(\d+)/cancel", path):
                self._send_json(cancel_scheduled_message(int(match.group(1))))
            elif match := re.fullmatch(r"/api/messages/schedule/(\d+)/send-now", path):
                self._send_json(send_scheduled_message_now(int(match.group(1))))
            elif path == "/api/conversations":
                self._send_json(create_conversation(self._read_json()))
            elif match := re.fullmatch(r"/api/conversations/(\d+)/archive", path):
                payload = self._read_json()
                archived = bool(payload.get("archived", True))
                self._send_json(set_conversation_archived(int(match.group(1)), archived))
            elif match := re.fullmatch(r"/api/conversations/(\d+)/dealt", path):
                payload = self._read_json()
                dealt = bool(payload.get("dealt", True))
                self._send_json(set_conversation_dealt(int(match.group(1)), dealt))
            elif path == "/api/conversations/bulk":
                self._send_json(bulk_update_conversations(self._read_json()))
            elif path == "/api/contacts/sync":
                self._send_json({"synced": sync_contacts()})
            elif path == "/api/contacts/phone":
                self._send_json({"synced": import_phone_contacts(self._read_json())})
            elif path == "/api/contacts/name":
                self._send_json(save_contact_name(self._read_json()))
            elif path == "/api/identities":
                self._send_json(create_identity(self._read_json()), HTTPStatus.CREATED)
            elif path == "/api/settings":
                self._send_json(update_values(self._read_json()))
            elif path == "/api/uploads":
                diagnostics = upload_diagnostics(self._request_url())
                self.log_upload(
                    "attempt directory=%s exists=%s base_url=%s",
                    diagnostics["directory"],
                    diagnostics["directory_exists"],
                    diagnostics["base_url"] or "(blank)",
                )
                payload = save_uploaded_media(self.headers.get("Content-Type", ""), self._read_raw(), self._request_url())
                self.log_upload(
                    "saved original=%s filename=%s directory=%s url=%s size=%s",
                    payload["original_filename"],
                    payload["filename"],
                    diagnostics["directory"],
                    payload["url"],
                    payload["size"],
                )
                self._send_json(payload)
            elif path == "/api/telnyx/webhook":
                raw = self._read_raw()
                headers = {key.lower(): value for key, value in self.headers.items()}
                self._send_json(handle_telnyx_webhook(raw, headers))
            elif path == "/api/twilio/webhook":
                raw = self._read_raw()
                headers = {key.lower(): value for key, value in self.headers.items()}
                handle_twilio_webhook(raw, headers, self._request_url())
                self._send_xml('<?xml version="1.0" encoding="UTF-8"?><Response></Response>')
            elif path == "/api/revai/webhook":
                self._send_json(store_revai_callback(self._read_raw()))
            elif path in {"/api/twilio/voice", "/api/telnyx/voice"}:
                raw = self._read_raw()
                headers = {key.lower(): value for key, value in self.headers.items()}
                provider = "twilio" if "twilio" in path else "telnyx"
                params = parse_voice_callback(provider, raw, headers, self._request_url())
                self._send_xml(voice_xml(provider, params, self._request_url()))
            elif path in {
                "/api/twilio/voice/recording",
                "/api/twilio/voice/transcription",
                "/api/telnyx/voice/recording",
                "/api/telnyx/voice/transcription",
            }:
                raw = self._read_raw()
                headers = {key.lower(): value for key, value in self.headers.items()}
                provider = "twilio" if "twilio" in path else "telnyx"
                params = parse_voice_callback(provider, raw, headers, self._request_url())
                callback_kind = "transcription" if path.endswith("/transcription") else "recording"
                result = store_voicemail_callback(provider, params, callback_kind=callback_kind, request_url=self._request_url())
                print(f"{provider} voice {callback_kind} callback: {result}", flush=True)
                self._send_xml('<?xml version="1.0" encoding="UTF-8"?><Response><Hangup /></Response>')
            else:
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (
            ValueError,
            SettingsError,
            MessagingError,
            TelnyxError,
            TwilioError,
            VoiceError,
            FastmailError,
            GoogleContactsError,
            ContactsError,
        ) as exc:
            if path == "/api/uploads":
                diagnostics = upload_diagnostics(self._request_url())
                self.log_upload(
                    "failed directory=%s exists=%s base_url=%s error=%s",
                    diagnostics["directory"],
                    diagnostics["directory_exists"],
                    diagnostics["base_url"] or "(blank)",
                    exc,
                )
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:
            if path == "/api/uploads":
                diagnostics = upload_diagnostics(self._request_url())
                self.log_upload(
                    "error directory=%s exists=%s base_url=%s error=%s",
                    diagnostics["directory"],
                    diagnostics["directory_exists"],
                    diagnostics["base_url"] or "(blank)",
                    exc,
                )
            self._send_json({"error": str(exc)}, 500)

    def do_PUT(self) -> None:
        if not self._begin_request(allow_body=True):
            return
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if not self._require_auth("PUT", path):
                return
            if match := re.fullmatch(r"/api/identities/(\d+)", path):
                self._send_json(update_identity(int(match.group(1)), self._read_json()))
            else:
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)


def run(host: str | None = None, port: int | None = None) -> None:
    host = host or config.HOST
    port = port or config.PORT
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    init_db(conn)
    conn.close()
    start_attachment_worker()
    load_app_auth_config()
    start_autosync()
    start_scheduled_sender()
    httpd = ThreadingHTTPServer((host, port), TextingHandler)
    print(f"Switchboard running at http://{host}:{port}", flush=True)
    httpd.serve_forever()
