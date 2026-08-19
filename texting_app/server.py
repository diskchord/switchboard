from __future__ import annotations

import colorsys
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
from .assistant_api import (
    get_conversation_context as get_assistant_conversation_context,
    list_unread_conversations as list_assistant_unread_conversations,
    list_unresolved_action_reviews,
    record_action_review,
)
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
MAX_CONVERSATION_TITLE_LENGTH = 120
PARTICIPANT_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
PARTICIPANT_COLOR_PALETTE = tuple(config.IDENTITY_COLORS)
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

LIMITED_USER_ADMIN_GET_PATHS = {
    "/api/auth/2fa",
    "/api/database/download",
    "/api/settings",
    "/api/stats",
    "/api/uploads/diagnostics",
    "/api/users",
}
LIMITED_USER_ADMIN_POST_PATHS = {
    "/api/auth/2fa/backup-codes",
    "/api/auth/2fa/disable",
    "/api/auth/2fa/enable",
    "/api/auth/2fa/setup",
    "/api/auth/account",
    "/api/contacts/phone",
    "/api/contacts/sync",
    "/api/identities",
    "/api/settings",
    "/api/users",
}
THEME_FAMILIES = {"switchboard", "console", "midnight", "papyrus", "unicorn"}
THEME_FAMILY_ALIASES = {"girly": "unicorn"}
THEME_MODES = {"light", "dark"}


def canonical_theme_family(value: object) -> str:
    theme_family = str(value or "").strip().lower()
    return THEME_FAMILY_ALIASES.get(theme_family, theme_family)


def _seed_limited_user_contacts(
    conn,
    limited_user_id: int,
    phone_number: str,
    *,
    reset: bool = False,
) -> None:
    if reset:
        conn.execute(
            "DELETE FROM limited_user_contacts WHERE limited_user_id = ?",
            (limited_user_id,),
        )
    participant_rows = conn.execute(
        """
        SELECT DISTINCT participant.phone_number
        FROM conversation_participants participant
        WHERE participant.role = 'participant'
          AND EXISTS (
            SELECT 1
            FROM conversation_participants self_cp
            WHERE self_cp.conversation_id = participant.conversation_id
              AND self_cp.role = 'self'
              AND self_cp.phone_number = ?
          )
        """,
        (phone_number,),
    ).fetchall()
    names = _contact_names(conn, (row["phone_number"] for row in participant_rows))
    timestamp = now_est()
    conn.executemany(
        """
        INSERT INTO limited_user_contacts(
          limited_user_id, phone_number, display_name, source, created_at, updated_at
        )
        VALUES (?, ?, ?, 'snapshot', ?, ?)
        ON CONFLICT(limited_user_id, phone_number) DO NOTHING
        """,
        [
            (limited_user_id, phone, display_name, timestamp, timestamp)
            for phone, display_name in names.items()
        ],
    )
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


def _limited_user_dict(row) -> dict:
    user = _row_dict(row)
    if not user:
        return {}
    user.pop("password_hash", None)
    user["is_active"] = bool(user.get("is_active"))
    user["theme_family"] = canonical_theme_family(user.get("theme_family"))
    user["role"] = "limited"
    return user


def _validate_limited_username(value: object) -> str:
    username = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", username):
        raise ValueError("Username must be 1-64 letters, numbers, or . _ @ - characters.")
    if config.AUTH_USERNAME and username.casefold() == config.AUTH_USERNAME.casefold():
        raise ValueError("That username is already used by the administrator.")
    return username


def _validate_limited_password(value: object, *, required: bool) -> str:
    password = str(value or "")
    if not password and not required:
        return ""
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    return password


def _limited_identity(conn, identity_id: object):
    try:
        parsed_id = int(identity_id or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose a sender number.") from exc
    row = conn.execute(
        "SELECT id, phone_number, label FROM identities WHERE id = ? AND is_self = 1 AND is_active = 1",
        (parsed_id,),
    ).fetchone()
    if not row:
        raise ValueError("Choose an active sender number.")
    return row


def list_limited_users() -> dict:
    with closing(connect()) as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.identity_id, u.is_active, u.theme_family, u.theme_mode,
              u.session_version, u.last_login_at, u.created_at, u.updated_at,
              i.phone_number, i.label AS identity_label
            FROM limited_users u
            JOIN identities i ON i.id = u.identity_id
            ORDER BY lower(u.username), u.id
            """
        ).fetchall()
        return {"users": [_limited_user_dict(row) for row in rows]}


def create_limited_user(payload: dict) -> dict:
    username = _validate_limited_username(payload.get("username"))
    password = _validate_limited_password(payload.get("password"), required=True)
    with closing(connect()) as conn:
        init_db(conn)
        identity = _limited_identity(conn, payload.get("identity_id"))
        timestamp = now_est()
        try:
            cursor = conn.execute(
                """
                INSERT INTO limited_users(
                  username, password_hash, identity_id, is_active,
                  theme_family, theme_mode, session_version, created_at, updated_at
                )
                VALUES (?, ?, ?, 1, 'switchboard', 'light', 1, ?, ?)
                """,
                (username, auth.hash_password(password), identity["id"], timestamp, timestamp),
            )
            _seed_limited_user_contacts(
                conn,
                int(cursor.lastrowid),
                str(identity["phone_number"]),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("That username already exists.") from exc
        conn.commit()
        row = conn.execute(
            """
            SELECT u.id, u.username, u.identity_id, u.is_active, u.theme_family, u.theme_mode,
              u.session_version, u.last_login_at, u.created_at, u.updated_at,
              i.phone_number, i.label AS identity_label
            FROM limited_users u JOIN identities i ON i.id = u.identity_id
            WHERE u.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        return {"user": _limited_user_dict(row)}


def update_limited_user(user_id: int, payload: dict) -> dict:
    with closing(connect()) as conn:
        init_db(conn)
        existing = conn.execute("SELECT * FROM limited_users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise LookupError("Limited user not found.")
        username = _validate_limited_username(payload.get("username", existing["username"]))
        password = _validate_limited_password(payload.get("password"), required=False)
        identity = _limited_identity(conn, payload.get("identity_id", existing["identity_id"]))
        is_active = 1 if payload.get("is_active", bool(existing["is_active"])) else 0
        session_version = int(existing["session_version"]) + (
            1
            if password
            or username != existing["username"]
            or int(identity["id"]) != int(existing["identity_id"])
            or is_active != int(existing["is_active"])
            else 0
        )
        assignments: list[object] = [username, identity["id"], is_active, session_version, now_est()]
        password_sql = ""
        if password:
            password_sql = ", password_hash = ?"
            assignments.append(auth.hash_password(password))
        assignments.append(user_id)
        try:
            conn.execute(
                f"""
                UPDATE limited_users
                SET username = ?, identity_id = ?, is_active = ?, session_version = ?, updated_at = ?
                  {password_sql}
                WHERE id = ?
                """,
                assignments,
            )
            if int(identity["id"]) != int(existing["identity_id"]):
                conn.execute(
                    "DELETE FROM limited_user_conversation_states WHERE limited_user_id = ?",
                    (user_id,),
                )
                _seed_limited_user_contacts(
                    conn,
                    user_id,
                    str(identity["phone_number"]),
                    reset=True,
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("That username already exists.") from exc
        conn.commit()
        row = conn.execute(
            """
            SELECT u.id, u.username, u.identity_id, u.is_active, u.theme_family, u.theme_mode,
              u.session_version, u.last_login_at, u.created_at, u.updated_at,
              i.phone_number, i.label AS identity_label
            FROM limited_users u JOIN identities i ON i.id = u.identity_id
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
        return {"user": _limited_user_dict(row)}


def delete_limited_user(user_id: int) -> dict:
    with closing(connect()) as conn:
        init_db(conn)
        row = conn.execute("SELECT username FROM limited_users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise LookupError("Limited user not found.")
        conn.execute("DELETE FROM limited_users WHERE id = ?", (user_id,))
        conn.commit()
        return {"deleted": True, "id": user_id, "username": row["username"]}


def limited_user_for_login(username: str):
    with closing(connect()) as conn:
        init_db(conn)
        return conn.execute(
            """
            SELECT u.*, i.phone_number, i.label AS identity_label
            FROM limited_users u
            JOIN identities i ON i.id = u.identity_id
            WHERE u.username = ? COLLATE NOCASE AND u.is_active = 1 AND i.is_active = 1
            """,
            (str(username or "").strip(),),
        ).fetchone()


def principal_from_session(payload: dict | None) -> dict | None:
    if not payload:
        return None
    if payload.get("r") != "limited":
        return {
            "username": str(payload.get("u") or config.AUTH_USERNAME or "local"),
            "role": "admin",
            "user_id": None,
            "identity_id": None,
            "phone_number": None,
        }
    with closing(connect()) as conn:
        row = conn.execute(
            """
            SELECT u.*, i.phone_number, i.label AS identity_label
            FROM limited_users u
            JOIN identities i ON i.id = u.identity_id
            WHERE u.id = ? AND u.is_active = 1 AND i.is_active = 1
            """,
            (int(payload.get("uid") or 0),),
        ).fetchone()
    if (
        not row
        or row["username"] != payload.get("u")
        or int(row["session_version"]) != int(payload.get("sv") or 0)
    ):
        return None
    return {
        "username": row["username"],
        "role": "limited",
        "user_id": int(row["id"]),
        "identity_id": int(row["identity_id"]),
        "phone_number": row["phone_number"],
        "identity_label": row["identity_label"],
        "theme_family": canonical_theme_family(row["theme_family"]),
        "theme_mode": row["theme_mode"],
    }


def limited_user_preferences(user_id: int) -> dict:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT theme_family, theme_mode FROM limited_users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
        if not row:
            raise LookupError("Limited user not found.")
        return {
            "theme_family": canonical_theme_family(row["theme_family"]),
            "theme_mode": row["theme_mode"],
        }


def update_limited_user_preferences(user_id: int, payload: dict) -> dict:
    theme_family = canonical_theme_family(payload.get("theme_family"))
    theme_mode = str(payload.get("theme_mode") or "").strip().lower()
    if theme_family not in THEME_FAMILIES:
        raise ValueError("Choose a valid theme.")
    if theme_mode not in THEME_MODES:
        raise ValueError("Choose light or dark mode.")
    with closing(connect()) as conn:
        result = conn.execute(
            """
            UPDATE limited_users
            SET theme_family = ?, theme_mode = ?, updated_at = ?
            WHERE id = ? AND is_active = 1
            """,
            (theme_family, theme_mode, now_est(), user_id),
        )
        if not result.rowcount:
            raise LookupError("Limited user not found.")
        conn.commit()
    return {"theme_family": theme_family, "theme_mode": theme_mode}


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


def _media_accessible_to_phone(filename: str, phone_number: str | None) -> bool:
    if not phone_number:
        return True
    with closing(connect()) as conn:
        rows = conn.execute(
            f"""
            SELECT a.local_path
            FROM attachments a
            JOIN messages m ON m.id = a.message_id
            WHERE {_message_access_sql('m')}
            """,
            (phone_number, phone_number),
        ).fetchall()
    return any(Path(str(row["local_path"] or "")).name == filename for row in rows)


def _message_access_sql(alias: str) -> str:
    return f"""
    (
      {alias}.from_number = ?
      OR EXISTS (SELECT 1 FROM json_each({alias}.to_numbers) access_to WHERE access_to.value = ?)
    )
    """


def _conversation_accessible(conn, conversation_id: int, phone_number: str | None) -> bool:
    if not phone_number:
        return True
    return bool(
        conn.execute(
            """
            SELECT 1
            FROM conversation_participants
            WHERE conversation_id = ? AND role = 'self' AND phone_number = ?
            """,
            (conversation_id, phone_number),
        ).fetchone()
    )


def _require_conversation_access(conn, conversation_id: int, phone_number: str | None) -> None:
    if not _conversation_accessible(conn, conversation_id, phone_number):
        raise LookupError("Conversation not found.")


def _scheduled_messages_for_conversation(
    conn,
    conversation_id: int,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> list[dict]:
    access_sql = " AND from_number = ?" if assigned_phone else ""
    params: list[object] = [conversation_id]
    if assigned_phone:
        params.append(assigned_phone)
    rows = conn.execute(
        f"""
        SELECT *
        FROM scheduled_messages
        WHERE conversation_id = ?
          AND status IN ('queued', 'sending', 'failed')
          {access_sql}
        ORDER BY scheduled_for, id
        """,
        params,
    ).fetchall()
    contact_names = _contact_names(
        conn,
        (row["from_number"] for row in rows),
        limited_user_id,
    )
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
    last_inbound_occurred_at: str | None,
    dealt_with_at: str | None,
    manual_unread_at: str | None = None,
) -> int:
    if manual_unread_at:
        return 1
    return int(
        bool(last_inbound_occurred_at)
        and (not dealt_with_at or last_inbound_occurred_at > dealt_with_at)
    )


UNREAD_CONVERSATION_CLAUSE = """
(
  c.manual_unread_at IS NOT NULL
  OR EXISTS (
    SELECT 1
    FROM messages latest
    WHERE latest.conversation_id = c.id
      AND latest.direction = 'inbound'
      AND COALESCE(latest.source, '') != 'autoreply'
      AND latest.occurred_at > COALESCE(c.dealt_with_at, '')
  )
)
"""


def _unread_conversation_clause(
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> tuple[str, list[object]]:
    if not assigned_phone:
        return UNREAD_CONVERSATION_CLAUSE, []
    if not limited_user_id:
        raise PermissionError("Limited user account required.")
    return (
        f"""
        (
          COALESCE((
            SELECT state.manual_unread_at
            FROM limited_user_conversation_states state
            WHERE state.conversation_id = c.id AND state.limited_user_id = ?
          ), '') <> ''
          OR EXISTS (
            SELECT 1
            FROM messages latest
            WHERE latest.conversation_id = c.id
              AND latest.direction = 'inbound'
              AND COALESCE(latest.source, '') != 'autoreply'
              AND EXISTS (
                SELECT 1 FROM json_each(latest.to_numbers) latest_to
                WHERE latest_to.value = ?
              )
              AND latest.occurred_at > COALESCE((
                SELECT state.dealt_with_at
                FROM limited_user_conversation_states state
                WHERE state.conversation_id = c.id AND state.limited_user_id = ?
              ), '')
          )
        )
        """,
        [
            limited_user_id,
            assigned_phone,
            limited_user_id,
        ],
    )


def _conversation_user_states(
    conn,
    conversation_ids,
    limited_user_id: int,
) -> dict[int, dict]:
    ids = list(dict.fromkeys(int(conversation_id) for conversation_id in conversation_ids))
    if not ids:
        return {}
    states: dict[int, dict] = {}
    for offset in range(0, len(ids), 800):
        chunk = ids[offset : offset + 800]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT conversation_id, dealt_with_at, manual_unread_at, updated_at
            FROM limited_user_conversation_states
            WHERE limited_user_id = ? AND conversation_id IN ({placeholders})
            """,
            (limited_user_id, *chunk),
        ).fetchall()
        for row in rows:
            states[int(row["conversation_id"])] = _row_dict(row)
    return states


def _conversation_user_state(conn, conversation_id: int, limited_user_id: int) -> dict:
    return _conversation_user_states(conn, [conversation_id], limited_user_id).get(
        int(conversation_id),
        {"dealt_with_at": None, "manual_unread_at": None, "updated_at": ""},
    )

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


def _contact_names(conn, phones, limited_user_id: int | None = None) -> dict[str, str]:
    unique_phones = list(dict.fromkeys(str(phone) for phone in phones if phone))
    names: dict[str, str] = {}
    for offset in range(0, len(unique_phones), 800):
        chunk = unique_phones[offset : offset + 800]
        placeholders = ",".join("?" for _ in chunk)
        if limited_user_id:
            rows = conn.execute(
                f"""
                SELECT phone_number, display_name
                FROM limited_user_contacts
                WHERE limited_user_id = ?
                  AND phone_number IN ({placeholders})
                ORDER BY updated_at DESC
                """,
                (limited_user_id, *chunk),
            ).fetchall()
            for row in rows:
                phone = row["phone_number"]
                display_name = row["display_name"]
                if phone not in names and display_name and display_name != phone:
                    names[phone] = display_name
            continue
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


def _contact_name(conn, phone: str, limited_user_id: int | None = None) -> str:
    return _contact_names(conn, [phone], limited_user_id).get(phone) or display_phone(phone)


def _limited_participant_colors(
    conn,
    conversation_ids,
    limited_user_id: int | None,
) -> dict[tuple[int, str], str]:
    ids = list(dict.fromkeys(int(conversation_id) for conversation_id in conversation_ids))
    if not limited_user_id or not ids:
        return {}
    colors: dict[tuple[int, str], str] = {}
    for offset in range(0, len(ids), 800):
        chunk = ids[offset : offset + 800]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT conversation_id, phone_number, color
            FROM limited_user_participant_colors
            WHERE limited_user_id = ?
              AND conversation_id IN ({placeholders})
            """,
            (limited_user_id, *chunk),
        ).fetchall()
        colors.update(
            {
                (int(row["conversation_id"]), str(row["phone_number"])): str(row["color"])
                for row in rows
            }
        )
    return colors


def _assign_default_participant_colors(
    conversation_id: int,
    participants: list[dict],
) -> None:
    remote_participants = sorted(
        (participant for participant in participants if participant["role"] == "participant"),
        key=lambda participant: str(participant["phone_number"]),
    )
    if not any(not participant.get("color") for participant in remote_participants):
        return
    palette = PARTICIPANT_COLOR_PALETTE
    if not palette:
        return
    offset = int(conversation_id) % len(palette)
    rotated = palette[offset:] + palette[:offset]
    used = {
        str(participant.get("color") or "").lower()
        for participant in remote_participants
        if participant.get("color")
    }
    for index, participant in enumerate(remote_participants):
        if participant.get("color"):
            continue
        if index < len(rotated):
            color = rotated[index]
        else:
            color = _generated_participant_color(conversation_id, index - len(rotated))
        collision_index = 0
        while color.lower() in used:
            collision_index += 1
            color = _generated_participant_color(
                conversation_id,
                index + collision_index * max(1, len(remote_participants)),
            )
        participant["color"] = color
        used.add(color.lower())


def _generated_participant_color(conversation_id: int, index: int) -> str:
    seed = abs(int(conversation_id)) * 97 + max(0, int(index))
    hue = ((seed * 137.50776405003785) % 360) / 360
    saturation = 0.62 + (seed % 3) * 0.06
    lightness = 0.40 + ((seed // 3) % 3) * 0.05
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def _participants_for_conversations(
    conn,
    conversation_ids,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict[int, list[dict]]:
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
                  i.color AS identity_color,
                  cp.color AS participant_color
                FROM conversation_participants cp
                LEFT JOIN identities i ON i.phone_number = cp.phone_number
                WHERE cp.conversation_id IN ({placeholders})
                """,
                chunk,
            ).fetchall()
        )
    contact_names = _contact_names(
        conn,
        (row["phone_number"] for row in rows),
        limited_user_id,
    )
    limited_colors = _limited_participant_colors(conn, ids, limited_user_id)
    for row in rows:
        phone = row["phone_number"]
        if assigned_phone and row["role"] == "self" and phone != assigned_phone:
            continue
        conversation_id = int(row["conversation_id"])
        color = row["identity_color"] if row["role"] == "self" else row["participant_color"]
        if row["role"] == "participant":
            color = limited_colors.get((conversation_id, phone), color)
        participants[row["conversation_id"]].append(
            {
                "phone_number": phone,
                "display": row["identity_label"] or contact_names.get(phone) or display_phone(phone),
                "role": row["role"],
                "color": color,
            }
        )
    for conversation_id, conversation_participants in participants.items():
        _assign_default_participant_colors(conversation_id, conversation_participants)
        conversation_participants.sort(
            key=lambda participant: (
                0 if participant["role"] == "self" else 1,
                str(participant["display"]).casefold(),
            )
        )
    return participants


def _participants(
    conn,
    conversation_id: int,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> list[dict]:
    return _participants_for_conversations(
        conn,
        [conversation_id],
        assigned_phone,
        limited_user_id,
    ).get(conversation_id, [])


def _conversation_title_from_participants(participants: list[dict], fallback: str | None = None) -> str:
    names = [participant["display"] for participant in participants if participant["role"] == "participant"]
    if names:
        return ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
    return fallback or "Unknown"


def _conversation_title(conn, conversation_id: int, fallback: str | None = None) -> str:
    return _conversation_title_from_participants(_participants(conn, conversation_id), fallback)


def _limited_conversation_titles(
    conn,
    conversation_ids,
    limited_user_id: int | None,
) -> dict[int, str]:
    ids = sorted({int(value) for value in conversation_ids if int(value) > 0})
    if not limited_user_id or not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT conversation_id, title
        FROM limited_user_conversation_titles
        WHERE limited_user_id = ? AND conversation_id IN ({placeholders})
        """,
        (limited_user_id, *ids),
    ).fetchall()
    return {int(row["conversation_id"]): str(row["title"] or "") for row in rows}


def _stored_conversation_title(
    conn,
    conversation_id: int,
    limited_user_id: int | None = None,
) -> str:
    if limited_user_id:
        row = conn.execute(
            """
            SELECT title
            FROM limited_user_conversation_titles
            WHERE limited_user_id = ? AND conversation_id = ?
            """,
            (limited_user_id, conversation_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT title FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return str(row["title"] or "") if row else ""


def _conversation_reference(
    conn,
    conversation_id: object,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict | None:
    try:
        reference_id = int(conversation_id or 0)
    except (TypeError, ValueError):
        return None
    if reference_id <= 0 or not _conversation_accessible(conn, reference_id, assigned_phone):
        return None
    row = conn.execute(
        "SELECT id FROM conversations WHERE id = ?",
        (reference_id,),
    ).fetchone()
    if not row:
        return None
    participants = _participants(
        conn,
        reference_id,
        assigned_phone,
        limited_user_id,
    )
    title = _stored_conversation_title(conn, reference_id, limited_user_id)
    return {
        "id": reference_id,
        "title": title or _conversation_title_from_participants(participants),
    }


def _search_terms(value: str) -> list[str]:
    return [part for part in re.split(r"\s+", value.strip().lower()) if part]


def _conversation_direct_search_expr(
    terms: list[str],
    limited_user_id: int | None = None,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    for term in terms:
        like = f"%{term}%"
        contact_join = "LEFT JOIN contacts direct_co ON direct_co.id = direct_cp.contact_id"
        contact_name = "direct_co.display_name"
        title_match = "lower(COALESCE(c.title, '')) LIKE ?"
        if limited_user_id:
            contact_join = """
                LEFT JOIN limited_user_contacts direct_luc
                  ON direct_luc.phone_number = direct_cp.phone_number
                 AND direct_luc.limited_user_id = ?
            """
            contact_name = "direct_luc.display_name"
            title_match = """
                EXISTS (
                  SELECT 1
                  FROM limited_user_conversation_titles direct_luct
                  WHERE direct_luct.conversation_id = c.id
                    AND direct_luct.limited_user_id = ?
                    AND lower(direct_luct.title) LIKE ?
                )
            """
        clauses.append(
            f"""
            (
              {title_match}
              OR EXISTS (
                SELECT 1
                FROM conversation_participants direct_cp
                {contact_join}
                WHERE direct_cp.conversation_id = c.id
                  AND direct_cp.role = 'participant'
                  AND (
                    lower(COALESCE({contact_name}, '')) LIKE ?
                    OR lower(direct_cp.phone_number) LIKE ?
                  )
              )
            )
            """
        )
        if limited_user_id:
            params.extend([limited_user_id, like, limited_user_id, like, like])
        else:
            params.extend([like, like, like])
    return " AND ".join(clauses) if clauses else "0", params


def _conversation_text_search_expr(
    table: str,
    alias: str,
    terms: list[str],
    assigned_phone: str | None = None,
) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        clauses.append(f"lower(COALESCE({alias}.text, '')) LIKE ?")
        params.append(f"%{term}%")
    where_sql = " AND ".join(clauses) if clauses else "0"
    access_sql = ""
    access_params: list[str] = []
    if assigned_phone:
        if table == "messages":
            access_sql = f"AND {_message_access_sql(alias)}"
            access_params = [assigned_phone, assigned_phone]
        else:
            access_sql = f"AND {alias}.from_number = ?"
            access_params = [assigned_phone]
    return (
        f"""
        EXISTS (
          SELECT 1
          FROM {table} {alias}
          WHERE {alias}.conversation_id = c.id
            {access_sql}
            AND {where_sql}
        )
        """,
        [*access_params, *params],
    )


def _conversation_search_clause(
    terms: list[str],
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> tuple[str, list[object]]:
    direct_sql, direct_params = _conversation_direct_search_expr(terms, limited_user_id)
    message_sql, message_params = _conversation_text_search_expr("messages", "search_m", terms, assigned_phone)
    scheduled_sql, scheduled_params = _conversation_text_search_expr(
        "scheduled_messages", "search_sm", terms, assigned_phone
    )
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


def _conversation_message_search_match(
    conn,
    conversation_id: int,
    terms: list[str],
    assigned_phone: str | None = None,
) -> dict | None:
    if not terms:
        return None
    where_sql = " AND ".join(["lower(COALESCE(text, '')) LIKE ?"] * len(terms))
    params = [f"%{term}%" for term in terms]
    access_sql = f"AND {_message_access_sql('messages')}" if assigned_phone else ""
    access_params = [assigned_phone, assigned_phone] if assigned_phone else []
    row = conn.execute(
        f"""
        SELECT id, text, occurred_at
        FROM messages
        WHERE conversation_id = ?
          {access_sql}
          AND {where_sql}
        ORDER BY occurred_at DESC, id DESC
        LIMIT 1
        """,
        (conversation_id, *access_params, *params),
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


def _decorate_conversation_summary(
    row,
    participants: list[dict],
    read_state: dict | None = None,
    custom_title: str | None = None,
) -> dict:
    item = _row_dict(row)
    item.pop("search_name_rank", None)
    dealt_with_at = read_state.get("dealt_with_at") if read_state is not None else row["dealt_with_at"]
    manual_unread_at = read_state.get("manual_unread_at") if read_state is not None else row["manual_unread_at"]
    if read_state is not None:
        item["dealt_with_at"] = dealt_with_at
        item["manual_unread_at"] = manual_unread_at
    needs_attention = _needs_attention(
        row["last_inbound_occurred_at"],
        dealt_with_at,
        manual_unread_at,
    )
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
        item["last_from_number"] = row["scheduled_from_number"]
        item["last_to_numbers"] = scheduled_to_numbers
        item["last_status"] = "failed" if scheduled_failed else "scheduled"
        item["last_occurred_at"] = row["scheduled_for"]
        item["last_raw_json"] = None
    else:
        item["last_to_numbers"] = from_json(row["last_to_numbers"], [])
    stored_title = row["title"] if read_state is None else custom_title
    item["custom_title"] = str(stored_title or "")
    item["title"] = stored_title or _conversation_title_from_participants(participants)
    item["participants"] = participants
    item["sort_at"] = row["list_sort_at"] or row["last_message_at"] or row["updated_at"]
    item["needs_attention"] = needs_attention
    item["last_status_label"] = _status_label(item.get("last_status"))
    item["last_status_kind"] = _status_kind(item.get("last_status"))
    if use_scheduled:
        item["last_status_detail"] = (
            row["scheduled_failure"] if row["scheduled_status"] == "failed" else f"Scheduled for {row['scheduled_for']}"
        )
    else:
        item["last_status_detail"] = _message_status_detail(row["last_status"], row["last_raw_json"])
    return item


def _list_conversations(
    conn,
    query: dict[str, list[str]],
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
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
    if assigned_phone:
        clauses.append(
            """
            EXISTS (
              SELECT 1 FROM conversation_participants access_cp
              WHERE access_cp.conversation_id = c.id
                AND access_cp.role = 'self'
                AND access_cp.phone_number = ?
            )
            """
        )
        params.append(assigned_phone)
    search_select_params: list[object] = []
    search_rank_select = "0 AS search_name_rank"
    if unread:
        unread_clause, unread_params = _unread_conversation_clause(
            assigned_phone,
            limited_user_id,
        )
        clauses.append(unread_clause)
        params.extend(unread_params)
    if search_terms:
        direct_search_sql, direct_search_params = _conversation_direct_search_expr(
            search_terms,
            limited_user_id,
        )
        search_rank_select = f"CASE WHEN {direct_search_sql} THEN 1 ELSE 0 END AS search_name_rank"
        search_select_params.extend(direct_search_params)
        search_clause, search_params = _conversation_search_clause(
            search_terms,
            assigned_phone,
            limited_user_id,
        )
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
    message_access_sql = f"AND {_message_access_sql('messages')}" if assigned_phone else ""
    inbound_access_sql = f"AND {_message_access_sql('latest_inbound')}" if assigned_phone else ""
    scheduled_access_sql = "AND from_number = ?" if assigned_phone else ""
    inbound_select_params: list[object] = []
    access_select_params: list[object] = []
    if assigned_phone:
        inbound_select_params.extend([assigned_phone, assigned_phone])
        access_select_params.extend([assigned_phone, assigned_phone, assigned_phone])
    rows = conn.execute(
        f"""
        SELECT c.*,
          m.text AS last_text,
          m.id AS last_message_id,
          m.message_type AS last_message_type,
          m.direction AS last_direction,
          m.from_number AS last_from_number,
          m.to_numbers AS last_to_numbers,
          m.status AS last_status,
          m.occurred_at AS last_occurred_at,
          m.raw_json AS last_raw_json,
          sm.id AS scheduled_id,
          sm.text AS scheduled_text,
          sm.from_number AS scheduled_from_number,
          sm.to_numbers AS scheduled_to_numbers,
          sm.media_urls AS scheduled_media_urls,
          sm.scheduled_for AS scheduled_for,
          sm.status AS scheduled_status,
          sm.failure AS scheduled_failure,
          {CONVERSATION_SORT_EXPR} AS list_sort_at,
          {search_rank_select},
          (
            SELECT MAX(latest_inbound.occurred_at)
            FROM messages latest_inbound
            WHERE latest_inbound.conversation_id = c.id
              AND latest_inbound.direction = 'inbound'
              AND COALESCE(latest_inbound.source, '') != 'autoreply'
              {inbound_access_sql}
          ) AS last_inbound_occurred_at
        FROM conversations c
        LEFT JOIN messages m ON m.id = (
          SELECT id FROM messages
          WHERE conversation_id = c.id
            AND COALESCE(source, '') != 'autoreply'
            {message_access_sql}
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
            {scheduled_access_sql}
          ORDER BY scheduled_for DESC, id DESC
          LIMIT 1
        )
        {where}
        ORDER BY search_name_rank DESC, list_sort_at DESC, c.id DESC
        LIMIT ?
        """,
        (
            *search_select_params,
            *inbound_select_params,
            *access_select_params,
            *params,
            limit + 1,
        ),
    ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    participants_by_conversation = _participants_for_conversations(
        conn,
        (row["id"] for row in rows),
        assigned_phone,
        limited_user_id,
    )
    limited_titles = _limited_conversation_titles(
        conn,
        (row["id"] for row in rows),
        limited_user_id,
    )
    read_states = (
        _conversation_user_states(conn, (row["id"] for row in rows), limited_user_id)
        if assigned_phone and limited_user_id
        else {}
    )
    conversations = []
    for row in rows:
        direct_search_match = bool(row["search_name_rank"]) if search_terms else False
        item = _decorate_conversation_summary(
            row,
            participants_by_conversation.get(row["id"], []),
            read_states.get(
                int(row["id"]),
                {"dealt_with_at": None, "manual_unread_at": None},
            )
            if assigned_phone
            else None,
            limited_titles.get(int(row["id"])),
        )
        if search_terms and not direct_search_match:
            item["search_match"] = _conversation_message_search_match(
                conn, row["id"], search_terms, assigned_phone
            )
        conversations.append(item)
    return {"conversations": conversations, "has_more": has_more}


def list_conversations(
    query: dict[str, list[str]],
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    with closing(connect()) as conn:
        return _list_conversations(conn, query, assigned_phone, limited_user_id)


def _notification_key(row) -> str:
    if not row:
        return ""
    return f"{row['occurred_at']}|{row['id']}"


def _parse_notification_key(value: str) -> tuple[str, int]:
    occurred_at, separator, message_id = str(value or "").rpartition("|")
    if not separator or not message_id.isdigit():
        return "", 0
    return occurred_at, int(message_id)


def _mobile_notifications(
    conn,
    query: dict[str, list[str]],
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    enabled = get_bool("notifications.native_enabled", config.NATIVE_NOTIFICATIONS_ENABLED)
    interval_minutes = max(get_int("notifications.native_interval_minutes", config.NATIVE_NOTIFICATION_INTERVAL_MINUTES), 15)
    limit = min(int((query.get("limit") or ["20"])[0]), 50)
    since_at, since_id = _parse_notification_key((query.get("since") or [""])[0])
    rows = []
    access_sql = f"AND {_message_access_sql('m')}" if assigned_phone else ""
    access_params = [assigned_phone, assigned_phone] if assigned_phone else []
    if enabled and since_at:
        state_join = ""
        state_params: list[str] = []
        read_clause = """
                c.manual_unread_at IS NOT NULL
                OR c.dealt_with_at IS NULL
                OR m.occurred_at > c.dealt_with_at
        """
        if assigned_phone:
            state_join = """
            LEFT JOIN limited_user_conversation_states read_state
              ON read_state.conversation_id = c.id
             AND read_state.limited_user_id = ?
            """
            state_params = [limited_user_id or 0]
            read_clause = """
                read_state.manual_unread_at IS NOT NULL
                OR read_state.dealt_with_at IS NULL
                OR m.occurred_at > read_state.dealt_with_at
            """
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
            {state_join}
            WHERE m.direction = 'inbound'
              {access_sql}
              AND COALESCE(c.is_archived, 0) = 0
              AND (m.occurred_at > ? OR (m.occurred_at = ? AND m.id > ?))
              AND (
                {read_clause}
              )
            ORDER BY m.occurred_at ASC, m.id ASC
            LIMIT ?
            """,
            (*state_params, *access_params, since_at, since_at, since_id, limit),
        ).fetchall()
    latest = rows[-1] if rows else conn.execute(
        f"""
        SELECT m.id, m.occurred_at
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.direction = 'inbound'
          {access_sql}
          AND COALESCE(c.is_archived, 0) = 0
        ORDER BY m.occurred_at DESC, m.id DESC
        LIMIT 1
        """,
        access_params,
    ).fetchone()
    notifications = []
    for row in rows:
        text = str(row["text"] or "").strip()
        attachment_count = int(row["attachment_count"] or 0)
        if not text and attachment_count:
            text = f"{attachment_count} attachment{'s' if attachment_count != 1 else ''}"
        elif not text:
            text = "New text message"
        conversation_participants = _participants(
            conn,
            row["conversation_id"],
            assigned_phone,
            limited_user_id,
        )
        participant_title = _conversation_title_from_participants(conversation_participants)
        sender_color = next(
            (
                participant.get("color")
                for participant in conversation_participants
                if participant["phone_number"] == row["from_number"]
            ),
            None,
        )
        title = _stored_conversation_title(
            conn,
            int(row["conversation_id"]),
            limited_user_id,
        ) or participant_title
        notifications.append(
            {
                "notification_key": _notification_key(row),
                "message_id": row["id"],
                "conversation_id": row["conversation_id"],
                "title": title,
                "from_number": row["from_number"],
                "from_display": _contact_name(conn, row["from_number"], limited_user_id),
                "sender_color": sender_color,
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


def mobile_notifications(
    query: dict[str, list[str]],
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    with closing(connect()) as conn:
        return _mobile_notifications(conn, query, assigned_phone, limited_user_id)


def _refresh_tokens(
    conn,
    conversation_id: int | None = None,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict[str, str]:
    def token_part(row, key: str) -> str:
        value = row[key]
        return "" if value is None else str(value)

    if assigned_phone:
        unread_clause, unread_params = _unread_conversation_clause(
            assigned_phone,
            limited_user_id,
        )
        list_row = conn.execute(
            f"""
            WITH accessible_conversations AS (
              SELECT DISTINCT conversation_id AS id
              FROM conversation_participants
              WHERE role = 'self' AND phone_number = ?
            ),
            accessible_messages AS (
              SELECT m.* FROM messages m
              JOIN accessible_conversations ac ON ac.id = m.conversation_id
              WHERE {_message_access_sql('m')}
            ),
            accessible_scheduled AS (
              SELECT sm.* FROM scheduled_messages sm
              JOIN accessible_conversations ac ON ac.id = sm.conversation_id
              WHERE sm.from_number = ?
            )
            SELECT
              (SELECT COUNT(*) FROM accessible_conversations) AS conversation_count,
              (SELECT COUNT(*) FROM conversations c JOIN accessible_conversations ac ON ac.id = c.id WHERE COALESCE(c.is_archived, 0) = 1) AS hidden_count,
              (SELECT COUNT(*) FROM conversations c JOIN accessible_conversations ac ON ac.id = c.id WHERE COALESCE(c.is_archived, 0) = 0 AND {unread_clause}) AS unread_count,
              (SELECT COALESCE(MAX(c.updated_at), '') FROM conversations c JOIN accessible_conversations ac ON ac.id = c.id) AS conversations_updated_at,
              (SELECT COUNT(*) FROM accessible_messages) AS message_count,
              (SELECT COALESCE(MAX(updated_at), '') FROM accessible_messages) AS messages_updated_at,
              (SELECT COUNT(*) FROM accessible_scheduled) AS scheduled_count,
              (SELECT COALESCE(MAX(updated_at), '') FROM accessible_scheduled) AS scheduled_updated_at,
              1 AS identity_count,
              (SELECT COALESCE(updated_at, '') FROM identities WHERE phone_number = ?) AS identities_updated_at,
              (
                SELECT COUNT(*)
                FROM limited_user_contacts luc
                WHERE luc.limited_user_id = ?
              ) AS contact_count,
              (
                SELECT COALESCE(MAX(luc.updated_at), '')
                FROM limited_user_contacts luc
                WHERE luc.limited_user_id = ?
              ) AS contacts_updated_at,
              (
                SELECT COALESCE(MAX(read_state.updated_at), '')
                FROM limited_user_conversation_states read_state
                WHERE read_state.limited_user_id = ?
              ) AS conversation_states_updated_at,
              (
                SELECT COUNT(*)
                FROM limited_user_conversation_titles luct
                WHERE luct.limited_user_id = ?
              ) AS limited_title_count,
              (
                SELECT COALESCE(MAX(luct.updated_at), '')
                FROM limited_user_conversation_titles luct
                WHERE luct.limited_user_id = ?
              ) AS limited_titles_updated_at,
              (
                SELECT COUNT(*)
                FROM limited_user_participant_colors lupc
                WHERE lupc.limited_user_id = ?
              ) AS limited_participant_color_count,
              (
                SELECT COALESCE(MAX(lupc.updated_at), '')
                FROM limited_user_participant_colors lupc
                WHERE lupc.limited_user_id = ?
              ) AS limited_participant_colors_updated_at
            """,
            (
                assigned_phone,
                assigned_phone,
                assigned_phone,
                assigned_phone,
                *unread_params,
                assigned_phone,
                limited_user_id or 0,
                limited_user_id or 0,
                limited_user_id or 0,
                limited_user_id or 0,
                limited_user_id or 0,
                limited_user_id or 0,
                limited_user_id or 0,
            ),
        ).fetchone()
    else:
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
          (SELECT COALESCE(MAX(updated_at), '') FROM contacts) AS contacts_updated_at,
          '' AS conversation_states_updated_at,
          0 AS limited_title_count,
          '' AS limited_titles_updated_at,
          0 AS limited_participant_color_count,
          '' AS limited_participant_colors_updated_at
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
                "conversation_states_updated_at",
                "limited_title_count",
                "limited_titles_updated_at",
                "limited_participant_color_count",
                "limited_participant_colors_updated_at",
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
        _require_conversation_access(conn, conversation_id, assigned_phone)
        if assigned_phone:
            row = conn.execute(
                f"""
                SELECT
                  c.updated_at AS conversation_updated_at,
                  (
                    SELECT COALESCE(luct.updated_at, '')
                    FROM limited_user_conversation_titles luct
                    WHERE luct.limited_user_id = ? AND luct.conversation_id = c.id
                  ) AS title_updated_at,
                  (
                    SELECT COALESCE(MAX(lupc.updated_at), '')
                    FROM limited_user_participant_colors lupc
                    WHERE lupc.limited_user_id = ? AND lupc.conversation_id = c.id
                  ) AS participant_colors_updated_at,
                  COALESCE(c.dealt_with_at, '') AS dealt_with_at,
                  COALESCE(c.manual_unread_at, '') AS manual_unread_at,
                  COALESCE(c.last_message_at, '') AS last_message_at,
                  (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id AND {_message_access_sql('m')}) AS message_count,
                  (SELECT COALESCE(MAX(m.updated_at), '') FROM messages m WHERE m.conversation_id = c.id AND {_message_access_sql('m')}) AS messages_updated_at,
                  (SELECT COALESCE(MAX(m.occurred_at), '') FROM messages m WHERE m.conversation_id = c.id AND {_message_access_sql('m')}) AS messages_occurred_at,
                  (SELECT COUNT(*) FROM scheduled_messages sm WHERE sm.conversation_id = c.id AND sm.from_number = ?) AS scheduled_count,
                  (SELECT COALESCE(MAX(sm.updated_at), '') FROM scheduled_messages sm WHERE sm.conversation_id = c.id AND sm.from_number = ?) AS scheduled_updated_at,
                  (SELECT COALESCE(MAX(sm.scheduled_for), '') FROM scheduled_messages sm WHERE sm.conversation_id = c.id AND sm.from_number = ? AND sm.status IN ('queued', 'sending', 'failed')) AS scheduled_for
                FROM conversations c
                WHERE c.id = ?
                """,
                (
                    limited_user_id or 0,
                    limited_user_id or 0,
                    assigned_phone, assigned_phone,
                    assigned_phone, assigned_phone,
                    assigned_phone, assigned_phone,
                    assigned_phone, assigned_phone, assigned_phone,
                    conversation_id,
                ),
            ).fetchone()
        else:
            row = conn.execute(
                """
            SELECT
              c.updated_at AS conversation_updated_at,
              '' AS title_updated_at,
              '' AS participant_colors_updated_at,
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
            read_state = (
                _conversation_user_state(conn, conversation_id, limited_user_id)
                if assigned_phone and limited_user_id
                else None
            )
            tokens["conversation"] = "|".join(
                (
                    str(read_state.get(key) or "")
                    if read_state is not None and key in {"dealt_with_at", "manual_unread_at"}
                    else token_part(row, key)
                )
                for key in (
                    "conversation_updated_at",
                    "title_updated_at",
                    "participant_colors_updated_at",
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


def refresh_state(
    query: dict[str, list[str]],
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    conversation_id_raw = (query.get("conversation_id") or ["0"])[0]
    conversation_id = int(conversation_id_raw) if conversation_id_raw.isdigit() else None
    with closing(connect()) as conn:
        return {
            "server_time": now_est(),
            "tokens": _refresh_tokens(
                conn,
                conversation_id,
                assigned_phone,
                limited_user_id,
            ),
        }


def _get_messages(
    conn,
    conversation_id: int,
    query: dict[str, list[str]] | None = None,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    query = query or {}
    _require_conversation_access(conn, conversation_id, assigned_phone)
    limit = min(int((query.get("limit") or [str(MESSAGE_PAGE_SIZE)])[0]), 250)
    before = (query.get("before") or [""])[0]
    before_id_raw = (query.get("before_id") or ["0"])[0]
    before_id = int(before_id_raw) if before_id_raw.isdigit() else 0
    where = "WHERE m.conversation_id = ?"
    params: list = [conversation_id]
    if assigned_phone:
        where += f" AND {_message_access_sql('m')}"
        params.extend([assigned_phone, assigned_phone])
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
        limited_user_id,
    )
    participants = _participants(
        conn,
        conversation_id,
        assigned_phone,
        limited_user_id,
    )
    participant_colors = {
        participant["phone_number"]: participant.get("color")
        for participant in participants
    }
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
        if row["direction"] == "inbound":
            message["sender_color"] = participant_colors.get(row["from_number"])
        message["attachments"] = attachments_by_message[row["id"]]
        messages.append(_decorate_message_status(message))
    scheduled_messages = []
    if not before and not before_id:
        scheduled_messages = _scheduled_messages_for_conversation(
            conn,
            conversation_id,
            assigned_phone,
            limited_user_id,
        )
        messages.extend(scheduled_messages)
        messages.sort(key=lambda item: (item.get("occurred_at") or "", str(item.get("id") or "")))
    older_count = 0
    if rows:
        oldest = rows[0]
        older_access_sql = f"AND {_message_access_sql('messages')}" if assigned_phone else ""
        older_access_params = [assigned_phone, assigned_phone] if assigned_phone else []
        older_count = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM messages
            WHERE conversation_id = ?
              {older_access_sql}
              AND (occurred_at < ? OR (occurred_at = ? AND id < ?))
            """,
            (
                conversation_id,
                *older_access_params,
                oldest["occurred_at"],
                oldest["occurred_at"],
                oldest["id"],
            ),
        ).fetchone()["count"]
    conversation = _row_dict(conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone())
    if assigned_phone:
        read_state = _conversation_user_state(conn, conversation_id, limited_user_id)
        conversation["dealt_with_at"] = read_state.get("dealt_with_at")
        conversation["manual_unread_at"] = read_state.get("manual_unread_at")
    last_access_sql = f"AND {_message_access_sql('messages')}" if assigned_phone else ""
    last_access_params = [assigned_phone, assigned_phone] if assigned_phone else []
    last_message = conn.execute(
        f"""
        SELECT direction, occurred_at
        FROM messages
        WHERE conversation_id = ?
          AND COALESCE(source, '') != 'autoreply'
          {last_access_sql}
        ORDER BY occurred_at DESC, id DESC
        LIMIT 1
        """,
        (conversation_id, *last_access_params),
    ).fetchone()
    if last_message:
        conversation["last_direction"] = last_message["direction"]
        conversation["last_occurred_at"] = last_message["occurred_at"]
    last_inbound = conn.execute(
        f"""
        SELECT occurred_at
        FROM messages
        WHERE conversation_id = ?
          AND direction = 'inbound'
          AND COALESCE(source, '') != 'autoreply'
          {last_access_sql}
        ORDER BY occurred_at DESC, id DESC
        LIMIT 1
        """,
        (conversation_id, *last_access_params),
    ).fetchone()
    conversation["last_inbound_occurred_at"] = (
        last_inbound["occurred_at"] if last_inbound else None
    )
    conversation["needs_attention"] = _needs_attention(
        conversation.get("last_inbound_occurred_at"),
        conversation.get("dealt_with_at"),
        conversation.get("manual_unread_at"),
    )
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
    custom_title = _stored_conversation_title(conn, conversation_id, limited_user_id)
    conversation["custom_title"] = custom_title
    conversation["title"] = custom_title or _conversation_title_from_participants(participants)
    conversation["participants"] = participants
    conversation["branched_from"] = _conversation_reference(
        conn,
        conversation.get("branched_from_conversation_id"),
        assigned_phone,
        limited_user_id,
    )
    return {
        "conversation": conversation,
        "messages": messages,
        "has_more": older_count > 0,
        "older_count": older_count,
    }


def get_messages(
    conversation_id: int,
    query: dict[str, list[str]] | None = None,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    with closing(connect()) as conn:
        return _get_messages(
            conn,
            conversation_id,
            query,
            assigned_phone,
            limited_user_id,
        )


def _bootstrap(conn, principal: dict | None = None) -> dict:
    server_time = now_est()
    assigned_phone = str((principal or {}).get("phone_number") or "") or None
    limited_user_id = int((principal or {}).get("user_id") or 0) or None
    identity_where = "WHERE i.phone_number = ?" if assigned_phone else ""
    identity_params: list[object] = [DEFAULT_AUTOREPLY_COOLDOWN_HOURS]
    if assigned_phone:
        identity_params.append(assigned_phone)
    identities = [
        _identity_dict(row)
        for row in conn.execute(
            f"""
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
            {identity_where}
            ORDER BY i.id
            """,
            identity_params,
        ).fetchall()
    ]
    default_identity = _apply_default_identity(identities)
    if assigned_phone:
        unread_clause, unread_params = _unread_conversation_clause(
            assigned_phone,
            limited_user_id,
        )
        stats = _row_dict(
            conn.execute(
                f"""
                WITH accessible_conversations AS (
                  SELECT DISTINCT conversation_id AS id
                  FROM conversation_participants
                  WHERE role = 'self' AND phone_number = ?
                ),
                accessible_messages AS (
                  SELECT m.id
                  FROM messages m
                  JOIN accessible_conversations ac ON ac.id = m.conversation_id
                  WHERE {_message_access_sql('m')}
                )
                SELECT
                  (SELECT COUNT(*) FROM accessible_conversations) AS conversations,
                  (SELECT COUNT(*) FROM conversations c JOIN accessible_conversations ac ON ac.id = c.id WHERE COALESCE(c.is_archived, 0) = 0) AS inbox_conversations,
                  (SELECT COUNT(*) FROM conversations c JOIN accessible_conversations ac ON ac.id = c.id WHERE COALESCE(c.is_archived, 0) = 1) AS hidden_conversations,
                  (SELECT COUNT(*) FROM conversations c JOIN accessible_conversations ac ON ac.id = c.id WHERE COALESCE(c.is_archived, 0) = 0 AND {unread_clause}) AS unread_conversations,
                  (SELECT COUNT(*) FROM accessible_messages) AS messages,
                  (SELECT COUNT(*) FROM attachments a JOIN accessible_messages am ON am.id = a.message_id) AS attachments,
                  (
                    SELECT COUNT(*)
                    FROM limited_user_contacts luc
                    WHERE luc.limited_user_id = ?
                  ) AS contacts
                """,
                (
                    assigned_phone,
                    assigned_phone,
                    assigned_phone,
                    *unread_params,
                    limited_user_id or 0,
                ),
            ).fetchone()
        )
    else:
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
    limited_assignments = []
    if not assigned_phone:
        limited_assignments = [
            {
                "user_id": row["id"],
                "username": row["username"],
                "identity_id": row["identity_id"],
                "phone_number": row["phone_number"],
                "is_active": bool(row["is_active"]),
            }
            for row in conn.execute(
                """
                SELECT u.id, u.username, u.identity_id, u.is_active, i.phone_number
                FROM limited_users u
                JOIN identities i ON i.id = u.identity_id
                ORDER BY lower(u.username), u.id
                """
            ).fetchall()
        ]
    return {
        "identities": identities,
        "limited_assignments": limited_assignments,
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
        "settings": configured_values() if not assigned_phone else {"sections": []},
        "mark_read_on_open": get_bool("behavior.mark_read_on_open", True),
        "enter_to_send_desktop": get_bool("behavior.enter_to_send_desktop", True),
        "enter_to_send_mobile": get_bool("behavior.enter_to_send_mobile", False),
        "details_collapsed_default": get_bool("behavior.details_collapsed_default", True),
        "default_identity": default_identity,
        "access": {
            "role": (principal or {}).get("role") or "admin",
            "username": (principal or {}).get("username") or config.AUTH_USERNAME or "local",
            "limited": bool(assigned_phone),
            "phone_number": assigned_phone or "",
        },
        "preferences": (
            {
                "theme_family": canonical_theme_family(
                    (principal or {}).get("theme_family") or "switchboard"
                ),
                "theme_mode": (principal or {}).get("theme_mode") or "light",
            }
            if assigned_phone
            else {}
        ),
    }


def bootstrap(principal: dict | None = None) -> dict:
    with closing(connect()) as conn:
        return _bootstrap(conn, principal)


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


def _search_contacts(
    conn,
    query: dict[str, list[str]],
    limited_user_id: int | None = None,
) -> dict:
    terms = _search_terms((query.get("q") or [""])[0])
    if limited_user_id:
        clauses = ["limited_user_id = ?"]
        params: list[object] = [limited_user_id]
        for term in terms:
            clauses.append("(lower(display_name) LIKE ? OR phone_number LIKE ?)")
            like = f"%{term}%"
            params.extend([like, like])
        rows = conn.execute(
            f"""
            SELECT phone_number AS id, display_name, source, phone_number, 'mobile' AS label
            FROM limited_user_contacts
            WHERE {' AND '.join(clauses)}
            ORDER BY CASE source WHEN 'user' THEN 2 ELSE 1 END DESC, updated_at DESC
            LIMIT 50
            """,
            params,
        ).fetchall()
    elif not terms:
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


def search_contacts(
    query: dict[str, list[str]],
    limited_user_id: int | None = None,
) -> dict:
    with closing(connect()) as conn:
        return _search_contacts(conn, query, limited_user_id)


def match_conversation(
    query: dict[str, list[str]],
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    raw_recipients: list[str] = []
    for value in (query.get("recipient") or []) + (query.get("recipients") or []):
        raw_recipients.extend(part.strip() for part in value.split(",") if part.strip())
    recipients = sorted({normalize_phone(value) for value in raw_recipients if normalize_phone(value)})
    if not recipients:
        return {"conversation": None}
    key = conversation_key(recipients)
    with closing(connect()) as conn:
        row = conn.execute("SELECT id FROM conversations WHERE conversation_key = ?", (key,)).fetchone()
        accessible = bool(row and _conversation_accessible(conn, int(row["id"]), assigned_phone))
    if not row or not accessible:
        return {"conversation": None}
    return {
        "conversation_id": int(row["id"]),
        "conversation": get_messages(
            int(row["id"]),
            assigned_phone=assigned_phone,
            limited_user_id=limited_user_id,
        )["conversation"],
    }


def save_contact_name(
    payload: dict,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    phone = payload.get("phone_number") or payload.get("phone") or ""
    display_name = str(payload.get("display_name") or payload.get("name") or "").strip()
    if assigned_phone and limited_user_id:
        phone = normalize_phone(phone)
        display_name = re.sub(r"\s+", " ", display_name).strip()
        if not phone:
            raise ValueError("A valid phone number is required.")
        if not display_name:
            raise ValueError("Contact name is required.")
        if len(display_name) > 140:
            raise ValueError("Contact name is too long.")
        with closing(connect()) as conn:
            accessible = conn.execute(
                """
                SELECT 1
                FROM conversation_participants participant
                WHERE participant.role = 'participant'
                  AND participant.phone_number = ?
                  AND EXISTS (
                    SELECT 1
                    FROM conversation_participants self_cp
                    WHERE self_cp.conversation_id = participant.conversation_id
                      AND self_cp.role = 'self'
                      AND self_cp.phone_number = ?
                  )
                LIMIT 1
                """,
                (phone, assigned_phone),
            ).fetchone()
            if not accessible:
                raise LookupError("Contact not found.")
            timestamp = now_est()
            conn.execute(
                """
                INSERT INTO limited_user_contacts(
                  limited_user_id, phone_number, display_name, source, created_at, updated_at
                )
                VALUES (?, ?, ?, 'user', ?, ?)
                ON CONFLICT(limited_user_id, phone_number) DO UPDATE SET
                  display_name = excluded.display_name,
                  source = 'user',
                  updated_at = excluded.updated_at
                """,
                (limited_user_id, phone, display_name, timestamp, timestamp),
            )
            conn.commit()
        result = {
            "contact": {
                "id": phone,
                "display_name": display_name,
                "phone_number": phone,
                "source": "user",
            },
            "synced": False,
            "participants": 0,
        }
    else:
        result = save_synced_contact_name(phone, display_name)
    conversation_id = payload.get("conversation_id")
    if conversation_id:
        result["conversation"] = get_messages(
            int(conversation_id),
            assigned_phone=assigned_phone,
            limited_user_id=limited_user_id,
        )["conversation"]
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


def set_conversation_archived(
    conversation_id: int,
    archived: bool,
    assigned_phone: str | None = None,
) -> dict:
    conn = connect()
    init_db(conn)
    _require_conversation_access(conn, conversation_id, assigned_phone)
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


def set_conversation_title(
    conversation_id: int,
    title: object,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    custom_title = str(title or "").strip()
    if len(custom_title) > MAX_CONVERSATION_TITLE_LENGTH:
        raise ValueError(
            f"Group names must be {MAX_CONVERSATION_TITLE_LENGTH} characters or fewer."
        )
    with closing(connect()) as conn:
        init_db(conn)
        _require_conversation_access(conn, conversation_id, assigned_phone)
        conversation = conn.execute(
            "SELECT kind FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if not conversation:
            raise LookupError("Conversation not found")
        if conversation["kind"] != "group":
            raise ValueError("Only group conversations can be named.")
        timestamp = now_est()
        if assigned_phone:
            if not limited_user_id:
                raise PermissionError("Limited user account required.")
            if custom_title:
                conn.execute(
                    """
                    INSERT INTO limited_user_conversation_titles(
                      limited_user_id, conversation_id, title, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(limited_user_id, conversation_id) DO UPDATE SET
                      title = excluded.title,
                      updated_at = excluded.updated_at
                    """,
                    (limited_user_id, conversation_id, custom_title, timestamp, timestamp),
                )
            else:
                conn.execute(
                    """
                    DELETE FROM limited_user_conversation_titles
                    WHERE limited_user_id = ? AND conversation_id = ?
                    """,
                    (limited_user_id, conversation_id),
                )
        else:
            conn.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (custom_title or None, timestamp, conversation_id),
            )
        conn.commit()
        return {
            "conversation": _conversation_summary(
                conn,
                conversation_id,
                assigned_phone,
                limited_user_id,
            )
        }


def set_conversation_participant_color(
    conversation_id: int,
    payload: dict,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    phone_number = normalize_phone(str(payload.get("phone_number") or payload.get("phone") or ""))
    color = str(payload.get("color") or "").strip().lower()
    if not phone_number:
        raise ValueError("A valid participant phone number is required.")
    if not PARTICIPANT_COLOR_PATTERN.fullmatch(color):
        raise ValueError("Participant color must use #RRGGBB format.")
    with closing(connect()) as conn:
        init_db(conn)
        _require_conversation_access(conn, conversation_id, assigned_phone)
        conversation = conn.execute(
            "SELECT kind FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if not conversation:
            raise LookupError("Conversation not found.")
        if conversation["kind"] != "group":
            raise ValueError("Participant colors are only available for group conversations.")
        participant = conn.execute(
            """
            SELECT role
            FROM conversation_participants
            WHERE conversation_id = ? AND phone_number = ?
            """,
            (conversation_id, phone_number),
        ).fetchone()
        if not participant or participant["role"] != "participant":
            raise LookupError("Group participant not found.")
        # Refresh tokens use updated_at values, so retain sub-second precision for
        # repeated color changes that can happen within one UI interaction burst.
        timestamp = datetime.now(EASTERN).isoformat(timespec="microseconds")
        if assigned_phone:
            if not limited_user_id:
                raise PermissionError("Limited user account required.")
            conn.execute(
                """
                INSERT INTO limited_user_participant_colors(
                  limited_user_id, conversation_id, phone_number, color, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(limited_user_id, conversation_id, phone_number) DO UPDATE SET
                  color = excluded.color,
                  updated_at = excluded.updated_at
                """,
                (
                    limited_user_id,
                    conversation_id,
                    phone_number,
                    color,
                    timestamp,
                    timestamp,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE conversation_participants
                SET color = ?
                WHERE conversation_id = ? AND phone_number = ? AND role = 'participant'
                """,
                (color, conversation_id, phone_number),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (timestamp, conversation_id),
            )
        conn.commit()
        return {
            "conversation": _conversation_summary(
                conn,
                conversation_id,
                assigned_phone,
                limited_user_id,
            )
        }


def _conversation_summary(
    conn,
    conversation_id: int,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    message_access_sql = f"AND {_message_access_sql('messages')}" if assigned_phone else ""
    inbound_access_sql = f"AND {_message_access_sql('latest_inbound')}" if assigned_phone else ""
    scheduled_access_sql = "AND from_number = ?" if assigned_phone else ""
    inbound_select_params: list[object] = []
    params: list[object] = []
    if assigned_phone:
        inbound_select_params.extend([assigned_phone, assigned_phone])
        params.extend([assigned_phone, assigned_phone, assigned_phone])
    params.append(conversation_id)
    row = conn.execute(
        f"""
        SELECT c.*,
          m.text AS last_text,
          m.id AS last_message_id,
          m.message_type AS last_message_type,
          m.direction AS last_direction,
          m.from_number AS last_from_number,
          m.to_numbers AS last_to_numbers,
          m.status AS last_status,
          m.occurred_at AS last_occurred_at,
          m.raw_json AS last_raw_json,
          sm.id AS scheduled_id,
          sm.text AS scheduled_text,
          sm.from_number AS scheduled_from_number,
          sm.to_numbers AS scheduled_to_numbers,
          sm.media_urls AS scheduled_media_urls,
          sm.scheduled_for AS scheduled_for,
          sm.status AS scheduled_status,
          sm.failure AS scheduled_failure,
          {CONVERSATION_SORT_EXPR} AS list_sort_at,
          (
            SELECT MAX(latest_inbound.occurred_at)
            FROM messages latest_inbound
            WHERE latest_inbound.conversation_id = c.id
              AND latest_inbound.direction = 'inbound'
              AND COALESCE(latest_inbound.source, '') != 'autoreply'
              {inbound_access_sql}
          ) AS last_inbound_occurred_at
        FROM conversations c
        LEFT JOIN messages m ON m.id = (
          SELECT id FROM messages
          WHERE conversation_id = c.id
            AND COALESCE(source, '') != 'autoreply'
            {message_access_sql}
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
            {scheduled_access_sql}
          ORDER BY scheduled_for DESC, id DESC
          LIMIT 1
        )
        WHERE c.id = ?
        """,
        (*inbound_select_params, *params),
    ).fetchone()
    if not row:
        return {}
    read_state = (
        _conversation_user_state(conn, conversation_id, limited_user_id)
        if assigned_phone and limited_user_id
        else None
    )
    conversation = _decorate_conversation_summary(
        row,
        _participants(conn, conversation_id, assigned_phone, limited_user_id),
        read_state,
        _stored_conversation_title(conn, conversation_id, limited_user_id)
        if limited_user_id
        else None,
    )
    conversation["branched_from"] = _conversation_reference(
        conn,
        conversation.get("branched_from_conversation_id"),
        assigned_phone,
        limited_user_id,
    )
    return conversation


_READ_THROUGH_UNSET = object()


def set_conversation_dealt(
    conversation_id: int,
    dealt: bool = True,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
    read_through_message_id: int | None | object = _READ_THROUGH_UNSET,
) -> dict:
    guard_read_through = dealt and read_through_message_id is not _READ_THROUGH_UNSET
    if guard_read_through and read_through_message_id is not None:
        if isinstance(read_through_message_id, bool) or not isinstance(read_through_message_id, int):
            raise ValueError("Read-through message ID must be a positive integer or null.")
        if read_through_message_id <= 0:
            raise ValueError("Read-through message ID must be a positive integer or null.")
    with closing(connect()) as conn:
        if guard_read_through:
            # Hold the writer lock from the comparison through the read-state
            # update so an inbound message cannot slip between those steps.
            conn.execute("BEGIN IMMEDIATE")
        _require_conversation_access(conn, conversation_id, assigned_phone)
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not row:
            raise ValueError("Conversation not found.")
        if guard_read_through:
            access_sql = f"AND {_message_access_sql('visible_inbound')}" if assigned_phone else ""
            access_params = [assigned_phone, assigned_phone] if assigned_phone else []
            latest_inbound = conn.execute(
                f"""
                SELECT visible_inbound.id
                FROM messages visible_inbound
                WHERE visible_inbound.conversation_id = ?
                  AND visible_inbound.direction = 'inbound'
                  {access_sql}
                ORDER BY visible_inbound.occurred_at DESC, visible_inbound.id DESC
                LIMIT 1
                """,
                (conversation_id, *access_params),
            ).fetchone()
            latest_inbound_id = int(latest_inbound["id"]) if latest_inbound else None
            if latest_inbound_id != read_through_message_id:
                conn.rollback()
                conversation = _conversation_summary(
                    conn,
                    conversation_id,
                    assigned_phone,
                    limited_user_id,
                )
                return {
                    "conversation": conversation,
                    "unread_count": _unread_conversation_count(
                        conn,
                        assigned_phone,
                        limited_user_id,
                    ),
                    "read_applied": False,
                }
        timestamp = now_est()
        if assigned_phone:
            if not limited_user_id:
                raise PermissionError("Limited user account required.")
            latest = conn.execute(
                f"""
                SELECT occurred_at
                FROM messages scoped_message
                WHERE scoped_message.conversation_id = ?
                  AND {_message_access_sql('scoped_message')}
                ORDER BY occurred_at DESC, id DESC
                LIMIT 1
                """,
                (conversation_id, assigned_phone, assigned_phone),
            ).fetchone()
            marker = (latest["occurred_at"] if latest else None) or timestamp
            dealt_with_at = marker if dealt else None
            manual_unread_at = None if dealt else marker
            conn.execute(
                """
                INSERT INTO limited_user_conversation_states(
                  limited_user_id, conversation_id, dealt_with_at, manual_unread_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(limited_user_id, conversation_id) DO UPDATE SET
                  dealt_with_at = excluded.dealt_with_at,
                  manual_unread_at = excluded.manual_unread_at,
                  updated_at = excluded.updated_at
                """,
                (limited_user_id, conversation_id, dealt_with_at, manual_unread_at, timestamp),
            )
        else:
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
        conversation = _conversation_summary(
            conn,
            conversation_id,
            assigned_phone,
            limited_user_id,
        )
        return {
            "conversation": conversation,
            "unread_count": _unread_conversation_count(
                conn,
                assigned_phone,
                limited_user_id,
            ),
            "read_applied": True,
        }


def bulk_update_conversations(
    payload: dict,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
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
    if assigned_phone:
        if not limited_user_id:
            conn.close()
            raise PermissionError("Limited user account required.")
        inaccessible = [
            conversation_id
            for conversation_id in ids
            if not _conversation_accessible(conn, conversation_id, assigned_phone)
        ]
        if inaccessible:
            conn.close()
            raise LookupError("One or more conversations were not found.")
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
            if assigned_phone:
                latest = conn.execute(
                    f"""
                    SELECT occurred_at
                    FROM messages scoped_message
                    WHERE scoped_message.conversation_id = ?
                      AND {_message_access_sql('scoped_message')}
                    ORDER BY occurred_at DESC, id DESC
                    LIMIT 1
                    """,
                    (row["id"], assigned_phone, assigned_phone),
                ).fetchone()
                marker = (latest["occurred_at"] if latest else None) or timestamp
                conn.execute(
                    """
                    INSERT INTO limited_user_conversation_states(
                      limited_user_id, conversation_id, dealt_with_at, manual_unread_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(limited_user_id, conversation_id) DO UPDATE SET
                      dealt_with_at = excluded.dealt_with_at,
                      manual_unread_at = excluded.manual_unread_at,
                      updated_at = excluded.updated_at
                    """,
                    (
                        limited_user_id,
                        row["id"],
                        marker if dealt else None,
                        None if dealt else marker,
                        timestamp,
                    ),
                )
            else:
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


def create_conversation(
    payload: dict,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    recipients = [normalize_phone(x) for x in payload.get("recipients", []) if normalize_phone(x)]
    if not recipients:
        raise ValueError("At least one recipient is required.")
    from_number = normalize_phone(payload.get("from_number") or "")
    if assigned_phone:
        if from_number and from_number != assigned_phone:
            raise ValueError("You can only send from your assigned number.")
        from_number = assigned_phone
    branched_from_raw = payload.get("branched_from_conversation_id")
    branched_from_id = None
    if branched_from_raw not in (None, ""):
        try:
            branched_from_id = int(branched_from_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Original conversation reference is invalid.") from exc
        if branched_from_id <= 0:
            raise ValueError("Original conversation reference is invalid.")
    with closing(connect()) as conn:
        init_db(conn)
        if branched_from_id:
            source = conn.execute(
                "SELECT id FROM conversations WHERE id = ?",
                (branched_from_id,),
            ).fetchone()
            if not source or not _conversation_accessible(conn, branched_from_id, assigned_phone):
                raise LookupError("Original conversation not found.")
        existing = conn.execute(
            "SELECT id FROM conversations WHERE conversation_key = ?",
            (conversation_key(recipients),),
        ).fetchone()
        conversation_id = ensure_conversation(
            conn,
            recipients,
            [from_number] if from_number else [],
            payload.get("title"),
        )
        if existing is None and branched_from_id:
            conn.execute(
                """
                UPDATE conversations
                SET branched_from_conversation_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (branched_from_id, now_est(), conversation_id),
            )
        conn.commit()
    return {
        "conversation_id": conversation_id,
        "created": existing is None,
        **get_messages(
            conversation_id,
            assigned_phone=assigned_phone,
            limited_user_id=limited_user_id,
        ),
    }


def _unread_conversation_count(
    conn,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> int:
    unread_clause, unread_params = _unread_conversation_clause(
        assigned_phone,
        limited_user_id,
    )
    access_clause = ""
    access_params: list[str] = []
    if assigned_phone:
        access_clause = """
              AND EXISTS (
                SELECT 1 FROM conversation_participants access_cp
                WHERE access_cp.conversation_id = c.id
                  AND access_cp.role = 'self'
                  AND access_cp.phone_number = ?
              )
        """
        access_params = [assigned_phone]
    return int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM conversations c
            WHERE COALESCE(c.is_archived, 0) = 0
              {access_clause}
              AND {unread_clause}
            """,
            (*access_params, *unread_params),
        ).fetchone()[0]
    )


def _mark_reply_message_read(
    message_id: int,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT conversation_id, occurred_at, from_number FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if not row:
            return None
        conversation_id = int(row["conversation_id"])
        marker = row["occurred_at"] or now_est()
        timestamp = now_est()
        if assigned_phone:
            if not limited_user_id:
                raise PermissionError("Limited user account required.")
            conn.execute(
                """
                INSERT INTO limited_user_conversation_states(
                  limited_user_id, conversation_id, dealt_with_at, manual_unread_at, updated_at
                )
                VALUES (?, ?, ?, NULL, ?)
                ON CONFLICT(limited_user_id, conversation_id) DO UPDATE SET
                  dealt_with_at = excluded.dealt_with_at,
                  manual_unread_at = NULL,
                  updated_at = excluded.updated_at
                """,
                (limited_user_id, conversation_id, marker, timestamp),
            )
        else:
            conn.execute(
                """
                UPDATE conversations
                SET dealt_with_at = ?,
                    manual_unread_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (marker, timestamp, conversation_id),
            )
        conn.commit()
        return {
            "conversation": _conversation_summary(
                conn,
                conversation_id,
                assigned_phone,
                limited_user_id,
            ),
            "unread_count": _unread_conversation_count(
                conn,
                assigned_phone,
                limited_user_id,
            ),
        }


def send_api_message(
    payload: dict,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    conversation_id = payload.get("conversation_id")
    to_numbers = [normalize_phone(x) for x in payload.get("to_numbers", []) if normalize_phone(x)]
    text = str(payload.get("text") or "")
    media_urls = [str(x).strip() for x in payload.get("media_urls", []) if str(x).strip()]
    if not text and not media_urls:
        raise ValueError("Message text or media URL is required.")
    from_number = normalize_phone(payload.get("from_number") or "")
    if assigned_phone:
        if from_number and from_number != assigned_phone:
            raise ValueError("You can only send from your assigned number.")
        from_number = assigned_phone
        if conversation_id:
            with closing(connect()) as conn:
                _require_conversation_access(conn, int(conversation_id), assigned_phone)
    result = send_provider_message(
        from_number=from_number or payload.get("from_number"),
        to_numbers=to_numbers,
        text=text,
        media_urls=media_urls,
        conversation_id=int(conversation_id) if conversation_id else None,
    )
    if result.get("message_id"):
        message_id = int(result["message_id"])
        _mark_uploaded_attachments_local(message_id, media_urls)
        read_state = _mark_reply_message_read(
            message_id,
            assigned_phone,
            limited_user_id,
        )
        if read_state:
            conversation = read_state["conversation"]
            result["conversation_id"] = conversation["id"]
            result["conversation"] = conversation
            result["unread_count"] = read_state["unread_count"]
    return result


def api_message_receipt(message_id: int) -> dict:
    """Return the stable, non-UI representation of an outbound message."""
    with closing(connect()) as conn:
        init_db(conn)
        row = conn.execute(
            """
            SELECT m.*, pmr.provider, pmr.provider_message_id
            FROM messages m
            LEFT JOIN provider_message_refs pmr ON pmr.message_id = m.id
            WHERE m.id = ? AND m.direction = 'outbound'
            ORDER BY pmr.provider
            LIMIT 1
            """,
            (message_id,),
        ).fetchone()
        if not row:
            raise LookupError("Message not found.")
        status = str(row["status"] or "")
        return {
            "id": int(row["id"]),
            "conversation_id": int(row["conversation_id"]),
            "direction": row["direction"],
            "from_number": row["from_number"],
            "to_numbers": from_json(row["to_numbers"], []),
            "text": row["text"],
            "message_type": row["message_type"],
            "status": status,
            "status_label": _status_label(status),
            "status_kind": _status_kind(status),
            "accepted": status not in FAILURE_STATUSES,
            "delivered": status == "delivered",
            "provider": row["provider"] or row["source"],
            "provider_message_id": row["provider_message_id"] or row["telnyx_id"],
            "occurred_at": row["occurred_at"],
            "updated_at": row["updated_at"],
        }


def send_external_api_message(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    for field in ("to_numbers", "media_urls"):
        if field in payload and not isinstance(payload[field], list):
            raise ValueError(f"{field} must be a JSON array.")
    result = send_api_message(payload)
    message_id = result.get("message_id")
    if not message_id:
        raise MessagingError("The provider did not return a message confirmation.")
    return api_message_receipt(int(message_id))


def send_api_fax(
    payload: dict,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    conversation_id = payload.get("conversation_id")
    media_url = str(payload.get("media_url") or "").strip()
    to_number = normalize_phone(payload.get("to_number"))
    if not media_url:
        raise ValueError("Choose a fax document.")
    if not to_number:
        raise ValueError("Enter a fax recipient.")
    from_number = normalize_phone(payload.get("from_number") or "")
    if assigned_phone:
        if from_number and from_number != assigned_phone:
            raise ValueError("You can only send from your assigned number.")
        from_number = assigned_phone
        if conversation_id:
            with closing(connect()) as conn:
                _require_conversation_access(conn, int(conversation_id), assigned_phone)
    result = send_telnyx_fax(
        from_number=from_number or payload.get("from_number"),
        to_number=to_number,
        media_url=media_url,
        filename=str(payload.get("filename") or ""),
        conversation_id=int(conversation_id) if conversation_id else None,
    )
    if result.get("message_id"):
        message_id = int(result["message_id"])
        _mark_uploaded_attachments_local(message_id, [media_url])
        read_state = _mark_reply_message_read(
            message_id,
            assigned_phone,
            limited_user_id,
        )
        if read_state:
            conversation = read_state["conversation"]
            result["conversation_id"] = conversation["id"]
            result["conversation"] = conversation
            result["unread_count"] = read_state["unread_count"]
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


def schedule_api_message(
    payload: dict,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
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
    if assigned_phone:
        if not limited_user_id:
            raise PermissionError("Limited user account required.")
        if from_number and from_number != assigned_phone:
            raise ValueError("You can only send from your assigned number.")
        from_number = assigned_phone
    if conversation_id:
        conversation_id = int(conversation_id)
        _require_conversation_access(conn, conversation_id, assigned_phone)
    else:
        known_self = self_numbers(conn)
        remote_numbers = sorted(n for n in to_numbers if n not in known_self)
        conversation_id = ensure_conversation(conn, remote_numbers or to_numbers, [from_number] if from_number else [])
    cur = conn.execute(
        """
        INSERT INTO scheduled_messages(
          conversation_id, limited_user_id, from_number, to_numbers, text, media_urls,
          scheduled_for, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
        """,
        (
            conversation_id,
            limited_user_id,
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


def cancel_scheduled_message(scheduled_id: int, assigned_phone: str | None = None) -> dict:
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM scheduled_messages WHERE id = ?", (scheduled_id,)).fetchone()
    if not row:
        raise ValueError("Scheduled message not found.")
    if assigned_phone and row["from_number"] != assigned_phone:
        raise LookupError("Scheduled message not found.")
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


def send_scheduled_message_now(
    scheduled_id: int,
    assigned_phone: str | None = None,
    limited_user_id: int | None = None,
) -> dict:
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT * FROM scheduled_messages WHERE id = ?", (scheduled_id,)).fetchone()
    if not row:
        raise ValueError("Scheduled message not found.")
    if assigned_phone and row["from_number"] != assigned_phone:
        raise LookupError("Scheduled message not found.")
    status = str(row["status"] or "")
    if status != "queued":
        raise ValueError("Only queued scheduled messages can be sent now.")
    conversation_id = int(row["conversation_id"]) if row["conversation_id"] else None
    _send_scheduled_row(conn, row, limited_user_id)
    updated = conn.execute("SELECT * FROM scheduled_messages WHERE id = ?", (scheduled_id,)).fetchone()
    return {
        "conversation_id": conversation_id,
        "scheduled_message": _scheduled_message_dict(updated),
        "sent": str(updated["status"] or "") == "sent",
    }


def _send_scheduled_row(conn, row, acting_limited_user_id: int | None = None) -> None:
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
            owner_user_id = int(scheduled.get("limited_user_id") or acting_limited_user_id or 0) or None
            _mark_reply_message_read(
                message_id,
                str(scheduled.get("from_number") or "") if owner_user_id else None,
                owner_user_id,
            )
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


def _ensure_admin_username_available(username: str) -> None:
    with closing(connect()) as conn:
        init_db(conn)
        if conn.execute(
            "SELECT 1 FROM limited_users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone():
            raise ValueError("That username is already used by a limited user.")


def setup_auth_account(payload: dict) -> dict:
    if auth.auth_configured() and not auth.auth_disabled():
        raise ValueError("Sign-in is already configured.")
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    confirm = str(payload.get("confirm_password") or payload.get("confirm") or "")
    if not username:
        raise ValueError("Enter a username.")
    _ensure_admin_username_available(username)
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
    _ensure_admin_username_available(username)
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
        self._principal_token = object()
        self._principal = None

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
        principal = self._current_principal()
        return str(principal.get("username") or "") if principal else None

    def _current_principal(self) -> dict | None:
        token = self._session_token()
        cached_token = getattr(self, "_principal_token", object())
        if cached_token == token:
            return getattr(self, "_principal", None)
        principal = principal_from_session(auth.verify_session_payload(token))
        self._principal_token = token
        self._principal = principal
        return principal

    def _assigned_phone(self) -> str | None:
        principal = self._current_principal()
        phone = str((principal or {}).get("phone_number") or "")
        return phone or None

    def _limited_user_id(self) -> int | None:
        principal = self._current_principal() or {}
        if principal.get("role") != "limited":
            return None
        return int(principal.get("user_id") or 0) or None

    def _limited_request_forbidden(self, method: str, path: str) -> bool:
        principal = self._current_principal()
        if not principal or principal.get("role") != "limited":
            return False
        if method == "GET":
            return path in LIMITED_USER_ADMIN_GET_PATHS
        if method == "POST":
            return path in LIMITED_USER_ADMIN_POST_PATHS or bool(
                re.fullmatch(r"/api/users/\d+/delete", path)
            )
        if method == "PUT":
            return bool(re.fullmatch(r"/api/(?:identities|users)/\d+", path))
        if method == "DELETE":
            return True
        return False

    def _has_api_token(self) -> bool:
        return self._has_bearer_token(config.API_TOKEN)

    def _has_assistant_api_token(self) -> bool:
        return self._has_bearer_token(config.ASSISTANT_API_TOKEN)

    def _has_bearer_token(self, expected: str) -> bool:
        authorization = (self.headers.get("Authorization") or "").strip()
        scheme, separator, supplied = authorization.partition(" ")
        return bool(
            expected
            and separator
            and scheme.lower() == "bearer"
            and secrets.compare_digest(supplied.strip(), expected)
        )

    def _api_tokens_are_distinct(self) -> bool:
        return not (
            config.API_TOKEN
            and config.ASSISTANT_API_TOKEN
            and secrets.compare_digest(config.API_TOKEN, config.ASSISTANT_API_TOKEN)
        )

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
        if path.startswith("/api/assistant/v1/"):
            if not config.ASSISTANT_API_TOKEN:
                self._send_json(
                    {
                        "error": "The assistant API is not configured. "
                        "Set SWITCHBOARD_ASSISTANT_API_TOKEN."
                    },
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return False
            if not self._api_tokens_are_distinct():
                self._send_json(
                    {
                        "error": "SWITCHBOARD_ASSISTANT_API_TOKEN must differ "
                        "from TEXTING_API_TOKEN."
                    },
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return False
            if not self._has_assistant_api_token():
                self._send_json(
                    {"error": "A valid assistant Bearer token is required."},
                    HTTPStatus.UNAUTHORIZED,
                    headers={"WWW-Authenticate": 'Bearer realm="Switchboard Assistant API"'},
                )
                return False
            return True
        if path.startswith("/api/v1/"):
            if not config.API_TOKEN:
                self._send_json(
                    {"error": "The programmatic API is not configured. Set TEXTING_API_TOKEN."},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return False
            if not self._api_tokens_are_distinct():
                self._send_json(
                    {
                        "error": "TEXTING_API_TOKEN must differ from "
                        "SWITCHBOARD_ASSISTANT_API_TOKEN."
                    },
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return False
            if not self._has_api_token():
                self._send_json(
                    {"error": "A valid Bearer token is required."},
                    HTTPStatus.UNAUTHORIZED,
                    headers={"WWW-Authenticate": 'Bearer realm="Switchboard API"'},
                )
                return False
            return True
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
        if self._limited_request_forbidden(method, path):
            self._send_json(
                {"error": "Administrator access is required."},
                HTTPStatus.FORBIDDEN,
            )
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
        admin_valid = auth.auth_disabled() or (
            secrets.compare_digest(username, config.AUTH_USERNAME)
            and auth.verify_password(password, config.AUTH_PASSWORD_HASH)
        )
        limited_user = None if admin_valid or auth.auth_disabled() else limited_user_for_login(username)
        limited_valid = bool(
            limited_user
            and auth.verify_password(password, str(limited_user["password_hash"] or ""))
        )
        if not admin_valid and not limited_valid:
            record_login_failure(client_key)
            if wants_json:
                self._send_json({"error": "Invalid username or password."}, HTTPStatus.UNAUTHORIZED)
            else:
                self._send_redirect(f"/login?error=1&next={login_next}", HTTPStatus.SEE_OTHER)
            return
        material = two_factor_material()
        if admin_valid and not auth.auth_disabled() and material["enabled"]:
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
        if limited_valid:
            canonical_username = str(limited_user["username"])
            token = auth.create_session_token(
                canonical_username,
                SESSION_MAX_AGE_SECONDS,
                role="limited",
                user_id=int(limited_user["id"]),
                session_version=int(limited_user["session_version"]),
            )
            with closing(connect()) as conn:
                conn.execute(
                    "UPDATE limited_users SET last_login_at = ? WHERE id = ?",
                    (now_est(), int(limited_user["id"])),
                )
                conn.commit()
            signed_in_user = canonical_username
        else:
            signed_in_user = config.AUTH_USERNAME or username or "local"
            token = auth.create_session_token(signed_in_user, SESSION_MAX_AGE_SECONDS)
        cookie = auth.session_cookie(token, self._request_is_secure(), SESSION_MAX_AGE_SECONDS)
        if wants_json:
            self._send_json({"ok": True, "user": signed_in_user}, headers={"Set-Cookie": cookie})
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
                principal = self._current_principal()
                self._send_json(
                    {
                        "authenticated": bool(principal),
                        "auth": auth_status_payload(),
                        "access": (
                            {
                                "role": principal["role"],
                                "username": principal["username"],
                                "limited": principal["role"] == "limited",
                                "phone_number": principal.get("phone_number") or "",
                            }
                            if principal
                            else None
                        ),
                    }
                )
            elif path == "/api/auth/2fa":
                self._send_json(two_factor_status_payload())
            elif path == "/api/bootstrap":
                self._send_json(bootstrap(self._current_principal()))
            elif path == "/api/preferences":
                principal = self._current_principal() or {}
                if principal.get("role") != "limited":
                    self._send_json({"theme_family": "", "theme_mode": ""})
                else:
                    self._send_json(limited_user_preferences(int(principal["user_id"])))
            elif path == "/api/users":
                self._send_json(list_limited_users())
            elif path == "/api/settings":
                self._send_json(configured_values())
            elif path == "/api/stats":
                self._send_json(message_stats(query))
            elif path == "/api/mobile/notifications":
                self._send_json(
                    mobile_notifications(
                        query,
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif path == "/api/refresh":
                self._send_json(
                    refresh_state(
                        query,
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif path == "/api/uploads/diagnostics":
                self._send_json(upload_diagnostics(self._request_url()))
            elif path == "/api/conversations":
                self._send_json(
                    list_conversations(
                        query,
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif path == "/api/assistant/v1/unread-conversations":
                self._send_json(list_assistant_unread_conversations(query))
            elif match := re.fullmatch(r"/api/assistant/v1/conversations/(\d+)/context", path):
                self._send_json(
                    get_assistant_conversation_context(int(match.group(1)), query)
                )
            elif path == "/api/assistant/v1/action-reviews/unresolved":
                self._send_json(list_unresolved_action_reviews(query))
            elif match := re.fullmatch(r"/api/v1/messages/(\d+)", path):
                self._send_json({"message": api_message_receipt(int(match.group(1)))})
            elif path == "/api/conversations/match":
                self._send_json(
                    match_conversation(
                        query,
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif match := re.fullmatch(r"/api/conversations/(\d+)/messages", path):
                self._send_json(
                    get_messages(
                        int(match.group(1)),
                        query,
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif path == "/api/contacts":
                self._send_json(search_contacts(query, self._limited_user_id()))
            elif path == "/api/database/download":
                self._send_database_download()
            elif path in {"/api/twilio/voice", "/api/telnyx/voice"}:
                provider = "twilio" if "twilio" in path else "telnyx"
                params = {key: values[-1] if values else "" for key, values in query.items()}
                self._send_xml(voice_xml(provider, params, self._request_url()))
            elif path.startswith("/media/"):
                name = Path(unquote(path.removeprefix("/media/"))).name
                if not _media_accessible_to_phone(name, self._assigned_phone()):
                    self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
                else:
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
            elif path.startswith("/api/"):
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._serve_file(STATIC_DIR / "index.html", cache_control="no-store")
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except LookupError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
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
            elif path == "/api/users":
                self._send_json(create_limited_user(self._read_json()), HTTPStatus.CREATED)
            elif path == "/api/preferences":
                principal = self._current_principal() or {}
                if principal.get("role") != "limited":
                    self._send_json(
                        {"error": "Preferences are stored locally for the administrator."},
                        HTTPStatus.BAD_REQUEST,
                    )
                else:
                    self._send_json(
                        update_limited_user_preferences(
                            int(principal["user_id"]), self._read_json()
                        )
                    )
            elif match := re.fullmatch(r"/api/users/(\d+)/delete", path):
                self._send_json(delete_limited_user(int(match.group(1))))
            elif path == "/api/messages":
                self._send_json(
                    send_api_message(
                        self._read_json(),
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif path == "/api/v1/messages":
                receipt = send_external_api_message(self._read_json())
                self._send_json(
                    {"message": receipt, "status_url": f"/api/v1/messages/{receipt['id']}"},
                    HTTPStatus.CREATED,
                )
            elif path == "/api/assistant/v1/action-reviews":
                self._send_json(record_action_review(self._read_json()))
            elif path == "/api/fax/send":
                self._send_json(
                    send_api_fax(
                        self._read_json(),
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif path == "/api/messages/schedule":
                self._send_json(
                    schedule_api_message(
                        self._read_json(),
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif match := re.fullmatch(r"/api/messages/schedule/(\d+)/cancel", path):
                self._send_json(
                    cancel_scheduled_message(int(match.group(1)), self._assigned_phone())
                )
            elif match := re.fullmatch(r"/api/messages/schedule/(\d+)/send-now", path):
                self._send_json(
                    send_scheduled_message_now(
                        int(match.group(1)),
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif path == "/api/conversations":
                self._send_json(
                    create_conversation(
                        self._read_json(),
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif match := re.fullmatch(r"/api/conversations/(\d+)/archive", path):
                payload = self._read_json()
                archived = bool(payload.get("archived", True))
                self._send_json(
                    set_conversation_archived(
                        int(match.group(1)), archived, self._assigned_phone()
                    )
                )
            elif match := re.fullmatch(r"/api/conversations/(\d+)/title", path):
                payload = self._read_json()
                self._send_json(
                    set_conversation_title(
                        int(match.group(1)),
                        payload.get("title"),
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif match := re.fullmatch(r"/api/conversations/(\d+)/participants/color", path):
                self._send_json(
                    set_conversation_participant_color(
                        int(match.group(1)),
                        self._read_json(),
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif match := re.fullmatch(r"/api/conversations/(\d+)/dealt", path):
                payload = self._read_json()
                dealt = bool(payload.get("dealt", True))
                read_through_message_id = (
                    payload.get("read_through_message_id")
                    if "read_through_message_id" in payload
                    else _READ_THROUGH_UNSET
                )
                self._send_json(
                    set_conversation_dealt(
                        int(match.group(1)),
                        dealt,
                        self._assigned_phone(),
                        self._limited_user_id(),
                        read_through_message_id,
                    )
                )
            elif path == "/api/conversations/bulk":
                self._send_json(
                    bulk_update_conversations(
                        self._read_json(),
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
            elif path == "/api/contacts/sync":
                self._send_json({"synced": sync_contacts()})
            elif path == "/api/contacts/phone":
                self._send_json({"synced": import_phone_contacts(self._read_json())})
            elif path == "/api/contacts/name":
                self._send_json(
                    save_contact_name(
                        self._read_json(),
                        self._assigned_phone(),
                        self._limited_user_id(),
                    )
                )
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
            json.JSONDecodeError,
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
        except LookupError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
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
            elif match := re.fullmatch(r"/api/users/(\d+)", path):
                self._send_json(update_limited_user(int(match.group(1)), self._read_json()))
            else:
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
        except LookupError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
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
