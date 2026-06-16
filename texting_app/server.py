from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import config
from .autoreply import (
    DEFAULT_AUTOREPLY_COOLDOWN_HOURS,
    DEFAULT_AUTOREPLY_MESSAGE,
    identity_autoreply_fields,
    update_autoreply_rule,
)
from .contacts import ContactsError, active_provider, configured_providers
from .contacts import save_contact_name as save_synced_contact_name
from .contacts import start_autosync, sync_contacts
from .db import connect, conversation_key, ensure_conversation, from_json, init_db, self_numbers
from .fastmail import FastmailError
from .google_contacts import GoogleContactsError
from .messaging import MessagingError, configured_messaging_providers
from .messaging import send_message as send_provider_message
from .phone import display_phone, normalize_phone
from .settings import SettingsError, configured_values, get_bool, get_int, get_value, update_values
from .telnyx import TelnyxError
from .telnyx import handle_webhook as handle_telnyx_webhook
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
CONVERTIBLE_VIDEO_EXTENSIONS = {".3gp", ".3gpp"}
CONVERTIBLE_VIDEO_TYPES = {"video/3gpp", "video/3gp"}


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


def _configured_upload_dir() -> Path:
    raw = get_value("uploads.public_directory", str(config.PUBLIC_UPLOAD_DIR)).strip()
    path = Path(raw).expanduser()
    return path if path.is_absolute() else config.ROOT / path


def _configured_upload_base_url() -> str:
    return get_value("uploads.public_base_url", config.PUBLIC_UPLOAD_BASE_URL).strip().rstrip("/")


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


def save_uploaded_media(content_type: str, body: bytes) -> dict:
    base_url = _configured_upload_base_url()
    if not base_url:
        raise ValueError("Set Uploads > Public upload base URL before uploading media.")
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


def upload_diagnostics() -> dict:
    upload_dir = _configured_upload_dir()
    base_url = _configured_upload_base_url()
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


def _attachment_local_path(attachment: dict) -> Path | None:
    raw = str(attachment.get("local_path") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path if path.is_file() else None
    candidate = config.MEDIA_DIR / path.name
    return candidate if candidate.is_file() else None


def _stored_local_path(path: Path) -> str:
    try:
        if path.resolve().parent == config.MEDIA_DIR.resolve():
            return f"media/{path.name}"
    except OSError:
        pass
    return str(path)


def _is_convertible_video_attachment(attachment: dict) -> bool:
    content_type = str(attachment.get("content_type") or "").split(";", 1)[0].strip().lower()
    names = [
        str(attachment.get("filename") or ""),
        str(attachment.get("local_path") or ""),
        str(attachment.get("remote_url") or ""),
    ]
    return content_type in CONVERTIBLE_VIDEO_TYPES or any(Path(unquote(urlparse(name).path)).suffix.lower() in CONVERTIBLE_VIDEO_EXTENSIONS for name in names)


def _convert_attachment_video(conn, attachment: dict) -> dict:
    if not _is_convertible_video_attachment(attachment):
        return attachment
    source = _attachment_local_path(attachment)
    ffmpeg = shutil.which("ffmpeg")
    if not source or not ffmpeg:
        return attachment
    target = source.with_suffix(".mp4")
    if not target.exists() or target.stat().st_size == 0:
        temp = target.with_suffix(".tmp.mp4")
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
                    str(temp),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
            )
            temp.replace(target)
        except (OSError, subprocess.SubprocessError):
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            return attachment

    stored_path = _stored_local_path(target)
    filename = target.name
    size = target.stat().st_size
    conn.execute(
        """
        UPDATE attachments
        SET local_path = ?,
            content_type = 'video/mp4',
            size = ?,
            filename = ?,
            source = ?
        WHERE id = ?
        """,
        (stored_path, size, filename, attachment.get("source") or "converted", attachment["id"]),
    )
    conn.commit()
    return {
        **attachment,
        "local_path": stored_path,
        "content_type": "video/mp4",
        "size": size,
        "filename": filename,
    }


def _attachment_dict(conn, row) -> dict:
    attachment = _row_dict(row)
    if not attachment.get("local_path"):
        local_path = _local_upload_path_for_remote_url(attachment.get("remote_url"))
        if local_path:
            attachment["local_path"] = str(local_path)
            attachment["source"] = "upload"
    attachment = _convert_attachment_video(conn, attachment)
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
                "from_display": _contact_name(conn, row["from_number"]),
                "attachments": attachments,
            }
        )
    return scheduled


FAILURE_STATUSES = {"delivery_failed", "failed", "undelivered", "rejected", "expired"}
WARNING_STATUSES = {"delivery_unconfirmed", "unknown", "unconfirmed"}
SUCCESS_STATUSES = {"delivered", "received", "imported"}
PENDING_STATUSES = {"queued", "scheduled", "sending", "sent", "accepted", "finalized"}


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


def _contact_name(conn, phone: str) -> str:
    row = conn.execute(
        """
        SELECT c.display_name
        FROM contact_phones cp
        JOIN contacts c ON c.id = cp.contact_id
        WHERE cp.phone_number = ?
        ORDER BY c.source IN ('fastmail', 'google') DESC, c.updated_at DESC
        LIMIT 1
        """,
        (phone,),
    ).fetchone()
    if row and row["display_name"] and row["display_name"] != phone:
        return row["display_name"]
    return display_phone(phone)


def _participants(conn, conversation_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT cp.phone_number,
          cp.role,
          (
            SELECT best.display_name
            FROM contact_phones best_phone
            JOIN contacts best ON best.id = best_phone.contact_id
            WHERE best_phone.phone_number = cp.phone_number
            ORDER BY best.source IN ('fastmail', 'google') DESC, best.updated_at DESC
            LIMIT 1
          ) AS contact_display,
          i.label AS identity_label,
          i.color
        FROM conversation_participants cp
        LEFT JOIN identities i ON i.phone_number = cp.phone_number
        WHERE cp.conversation_id = ?
        ORDER BY cp.role DESC, COALESCE(contact_display, i.label, cp.phone_number)
        """,
        (conversation_id,),
    ).fetchall()
    return [
        {
            "phone_number": row["phone_number"],
            "display": row["identity_label"]
            or (row["contact_display"] if row["contact_display"] and row["contact_display"] != row["phone_number"] else None)
            or display_phone(row["phone_number"]),
            "role": row["role"],
            "color": row["color"],
        }
        for row in rows
    ]


def _conversation_title(conn, conversation_id: int, fallback: str | None = None) -> str:
    names = [
        p["display"]
        for p in _participants(conn, conversation_id)
        if p["role"] == "participant"
    ]
    if names:
        return ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
    return fallback or "Unknown"


def list_conversations(query: dict[str, list[str]]) -> dict:
    conn = connect()
    init_db(conn)
    search = (query.get("search") or [""])[0].strip()
    hidden = (query.get("hidden") or ["0"])[0].lower() in {"1", "true", "yes"}
    unread = (query.get("unread") or ["0"])[0].lower() in {"1", "true", "yes"}
    limit = min(int((query.get("limit") or ["80"])[0]), 200)
    before = (query.get("before") or [""])[0]
    before_id_raw = (query.get("before_id") or ["0"])[0]
    before_id = int(before_id_raw) if before_id_raw.isdigit() else 0
    clauses: list[str] = []
    clauses.append("COALESCE(c.is_archived, 0) = ?")
    params: list = [1 if hidden else 0]
    if unread:
        clauses.append(UNREAD_CONVERSATION_CLAUSE)
    if search:
        like = f"%{search.lower()}%"
        clauses.append(
            """
            (
              c.id IN (
          SELECT cp.conversation_id
          FROM conversation_participants cp
          LEFT JOIN contacts co ON co.id = cp.contact_id
          WHERE lower(cp.phone_number) LIKE ? OR lower(COALESCE(co.display_name, '')) LIKE ?
              ) OR c.id IN (
                SELECT conversation_id FROM messages WHERE lower(text) LIKE ?
              ) OR c.id IN (
                SELECT conversation_id FROM scheduled_messages WHERE lower(text) LIKE ?
              )
            )
            """
        )
        params.extend([like, like, like, like])
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
          (SELECT COUNT(*) FROM messages mm WHERE mm.conversation_id = c.id) AS message_count
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
        ORDER BY list_sort_at DESC, c.id DESC
        LIMIT ?
        """,
        (*params, limit + 1),
    ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    conversations = []
    for row in rows:
        item = _row_dict(row)
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
        item["title"] = row["title"] or _conversation_title(conn, row["id"])
        item["participants"] = _participants(conn, row["id"])
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
            item["last_status_detail"] = row["scheduled_failure"] if row["scheduled_status"] == "failed" else f"Scheduled for {row['scheduled_for']}"
        else:
            item["last_status_detail"] = _message_status_detail(row["last_status"], row["last_raw_json"])
        conversations.append(item)
    return {"conversations": conversations, "has_more": has_more}


def _notification_key(row) -> str:
    if not row:
        return ""
    return f"{row['occurred_at']}|{row['id']}"


def _parse_notification_key(value: str) -> tuple[str, int]:
    occurred_at, separator, message_id = str(value or "").rpartition("|")
    if not separator or not message_id.isdigit():
        return "", 0
    return occurred_at, int(message_id)


def mobile_notifications(query: dict[str, list[str]]) -> dict:
    conn = connect()
    init_db(conn)
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
    conn = connect()
    init_db(conn)
    return {
        "server_time": now_est(),
        "tokens": _refresh_tokens(conn, conversation_id),
    }


def get_messages(conversation_id: int, query: dict[str, list[str]] | None = None) -> dict:
    conn = connect()
    init_db(conn)
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
    messages = []
    for row in rows:
        message = _row_dict(row)
        message["to_numbers"] = from_json(row["to_numbers"], [])
        message["cc_numbers"] = from_json(row["cc_numbers"], [])
        message["from_display"] = row["identity_label"] or _contact_name(conn, row["from_number"])
        message["attachments"] = [
            _attachment_dict(conn, a)
            for a in conn.execute("SELECT * FROM attachments WHERE message_id = ?", (row["id"],)).fetchall()
        ]
        messages.append(_decorate_message_status(message))
    if not before and not before_id:
        messages.extend(_scheduled_messages_for_conversation(conn, conversation_id))
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
    conversation["needs_attention"] = _needs_attention(
        conversation.get("last_direction"),
        conversation.get("last_occurred_at"),
        conversation.get("dealt_with_at"),
        conversation.get("manual_unread_at"),
    )
    conversation["title"] = _conversation_title(conn, conversation_id)
    conversation["participants"] = _participants(conn, conversation_id)
    return {
        "conversation": conversation,
        "messages": messages,
        "has_more": older_count > 0,
        "older_count": older_count,
    }


def bootstrap() -> dict:
    conn = connect()
    init_db(conn)
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
        "default_identity": identities[0]["phone_number"] if identities else "",
    }


def message_stats() -> dict:
    conn = connect()
    init_db(conn)
    totals = _row_dict(
        conn.execute(
            f"""
            SELECT
              (SELECT COUNT(*) FROM conversations) AS conversations,
              (SELECT COUNT(*) FROM conversations WHERE COALESCE(is_archived, 0) = 0) AS inbox_conversations,
              (SELECT COUNT(*) FROM conversations WHERE COALESCE(is_archived, 0) = 1) AS hidden_conversations,
              (SELECT COUNT(*) FROM conversations c WHERE COALESCE(c.is_archived, 0) = 0 AND {UNREAD_CONVERSATION_CLAUSE}) AS unread_conversations,
              (SELECT COUNT(*) FROM messages) AS messages,
              (SELECT COUNT(*) FROM messages WHERE direction = 'inbound') AS inbound_messages,
              (SELECT COUNT(*) FROM messages WHERE direction = 'outbound') AS outbound_messages,
              (SELECT COUNT(*) FROM messages WHERE message_type = 'Voicemail') AS voicemails,
              (SELECT COUNT(*) FROM messages WHERE status IN ('delivery_failed', 'failed', 'undelivered', 'rejected', 'expired')) AS failed_messages,
              (SELECT COUNT(*) FROM messages WHERE status IN ('queued', 'sending', 'sent', 'accepted', 'finalized')) AS pending_messages,
              (SELECT COUNT(*) FROM attachments) AS attachments,
              (SELECT COUNT(*) FROM contacts) AS contacts
            """
        ).fetchone()
    )
    by_status = [
        _row_dict(row)
        for row in conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM messages
            GROUP BY status
            ORDER BY count DESC, status
            """
        ).fetchall()
    ]
    by_source = [
        _row_dict(row)
        for row in conn.execute(
            """
            SELECT source, COUNT(*) AS count
            FROM messages
            GROUP BY source
            ORDER BY count DESC, source
            """
        ).fetchall()
    ]
    recent_days = [
        _row_dict(row)
        for row in conn.execute(
            """
            SELECT substr(occurred_at, 1, 10) AS day,
              COUNT(*) AS count,
              SUM(direction = 'inbound') AS inbound,
              SUM(direction = 'outbound') AS outbound
            FROM messages
            GROUP BY day
            ORDER BY day DESC
            LIMIT 14
            """
        ).fetchall()
    ]
    return {
        "totals": totals,
        "by_status": by_status,
        "by_source": by_source,
        "recent_days": recent_days,
        "server_time": now_est(),
    }


def search_contacts(query: dict[str, list[str]]) -> dict:
    conn = connect()
    init_db(conn)
    term = (query.get("q") or [""])[0].strip().lower()
    if not term:
        rows = conn.execute(
            """
            SELECT c.id, c.display_name, cp.phone_number, cp.label
            FROM contacts c
            JOIN contact_phones cp ON cp.contact_id = c.id
            ORDER BY c.updated_at DESC
            LIMIT 50
            """
        ).fetchall()
    else:
        like = f"%{term}%"
        rows = conn.execute(
            """
            SELECT c.id, c.display_name, cp.phone_number, cp.label
            FROM contacts c
            JOIN contact_phones cp ON cp.contact_id = c.id
            WHERE lower(c.display_name) LIKE ? OR cp.phone_number LIKE ?
            ORDER BY c.display_name
            LIMIT 50
            """,
            (like, like),
        ).fetchall()
    return {
        "contacts": [
            {
                "id": row["id"],
                "display_name": row["display_name"],
                "phone_number": row["phone_number"],
                "phone_display": display_phone(row["phone_number"]),
                "label": row["label"],
            }
            for row in rows
        ]
    }


def match_conversation(query: dict[str, list[str]]) -> dict:
    raw_recipients: list[str] = []
    for value in (query.get("recipient") or []) + (query.get("recipients") or []):
        raw_recipients.extend(part.strip() for part in value.split(",") if part.strip())
    recipients = sorted({normalize_phone(value) for value in raw_recipients if normalize_phone(value)})
    if not recipients:
        return {"conversation": None}
    key = conversation_key(recipients)
    conn = connect()
    init_db(conn)
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
    conn.execute(
        "UPDATE identities SET label = ?, color = ?, is_active = ?, updated_at = ? WHERE id = ?",
        (label, color, active, now_est(), identity_id),
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
    return {"identity": _identity_with_autoreply(conn, identity_id)}


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


def set_conversation_dealt(conversation_id: int, dealt: bool = True) -> dict:
    conn = connect()
    init_db(conn)
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
    return {"conversation": get_messages(conversation_id)["conversation"]}


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
        _mark_uploaded_attachments_local(int(result["message_id"]), media_urls)
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


class TextingHandler(BaseHTTPRequestHandler):
    server_version = "Switchboard/0.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def log_upload(self, fmt: str, *args) -> None:
        self.log_message("upload " + fmt, *args)

    def _send_headers(self, status: int, content_type: str, length: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        no_store_types = (
            "application/json",
            "text/html",
            "text/css",
            "text/xml",
            "application/javascript",
            "text/javascript",
        )
        cache_control = "no-store" if content_type.startswith(no_store_types) else "public, max-age=3600"
        self.send_header("Cache-Control", cache_control)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, default=_json_default).encode("utf-8")
        self._send_headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _send_xml(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self._send_headers(status, "text/xml; charset=utf-8", len(data))
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _read_raw(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _request_url(self) -> str:
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip()
        if not proto:
            proto = "https" if self.headers.get("X-Forwarded-Ssl", "").lower() == "on" else "http"
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",", 1)[0].strip()
        return f"{proto}://{host}{self.path}" if host else self.path

    def _serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self._send_headers(200, content_type, len(data))
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/bootstrap":
                self._send_json(bootstrap())
            elif path == "/api/settings":
                self._send_json(configured_values())
            elif path == "/api/stats":
                self._send_json(message_stats())
            elif path == "/api/mobile/notifications":
                self._send_json(mobile_notifications(query))
            elif path == "/api/refresh":
                self._send_json(refresh_state(query))
            elif path == "/api/uploads/diagnostics":
                self._send_json(upload_diagnostics())
            elif path == "/api/conversations":
                self._send_json(list_conversations(query))
            elif path == "/api/conversations/match":
                self._send_json(match_conversation(query))
            elif match := re.fullmatch(r"/api/conversations/(\d+)/messages", path):
                self._send_json(get_messages(int(match.group(1)), query))
            elif path == "/api/contacts":
                self._send_json(search_contacts(query))
            elif path in {"/api/twilio/voice", "/api/telnyx/voice"}:
                provider = "twilio" if "twilio" in path else "telnyx"
                params = {key: values[-1] if values else "" for key, values in query.items()}
                self._send_xml(voice_xml(provider, params, self._request_url()))
            elif path.startswith("/media/"):
                name = Path(unquote(path.removeprefix("/media/"))).name
                self._serve_file(config.MEDIA_DIR / name)
            elif path.startswith("/uploads/"):
                name = Path(unquote(path.removeprefix("/uploads/"))).name
                self._serve_file(_configured_upload_dir() / name)
            elif path in {"/favicon.ico", "/favicon.svg", "/apple-touch-icon.png"}:
                self._serve_file(STATIC_DIR / path.removeprefix("/"))
            elif path.startswith("/static/"):
                rel = Path(unquote(path.removeprefix("/static/")))
                self._serve_file(STATIC_DIR / rel.name)
            elif path in {"/", "/index.html"}:
                self._serve_file(STATIC_DIR / "index.html")
            else:
                self._serve_file(STATIC_DIR / "index.html")
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/messages":
                self._send_json(send_api_message(self._read_json()))
            elif path == "/api/messages/schedule":
                self._send_json(schedule_api_message(self._read_json()))
            elif match := re.fullmatch(r"/api/messages/schedule/(\d+)/cancel", path):
                self._send_json(cancel_scheduled_message(int(match.group(1))))
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
            elif path == "/api/contacts/name":
                self._send_json(save_contact_name(self._read_json()))
            elif path == "/api/settings":
                self._send_json(update_values(self._read_json()))
            elif path == "/api/uploads":
                diagnostics = upload_diagnostics()
                self.log_upload(
                    "attempt directory=%s exists=%s base_url=%s",
                    diagnostics["directory"],
                    diagnostics["directory_exists"],
                    diagnostics["base_url"] or "(blank)",
                )
                payload = save_uploaded_media(self.headers.get("Content-Type", ""), self._read_raw())
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
                diagnostics = upload_diagnostics()
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
                diagnostics = upload_diagnostics()
                self.log_upload(
                    "error directory=%s exists=%s base_url=%s error=%s",
                    diagnostics["directory"],
                    diagnostics["directory_exists"],
                    diagnostics["base_url"] or "(blank)",
                    exc,
                )
            self._send_json({"error": str(exc)}, 500)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
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
    start_autosync()
    start_scheduled_sender()
    httpd = ThreadingHTTPServer((host, port), TextingHandler)
    print(f"Switchboard running at http://{host}:{port}", flush=True)
    httpd.serve_forever()
