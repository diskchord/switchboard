from __future__ import annotations

import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import config
from .contacts import ContactsError, active_provider, configured_providers
from .contacts import save_contact_name as save_synced_contact_name
from .contacts import start_autosync, sync_contacts
from .db import connect, conversation_key, ensure_conversation, from_json, init_db
from .fastmail import FastmailError
from .google_contacts import GoogleContactsError
from .phone import display_phone, normalize_phone
from .settings import SettingsError, configured_values, get_bool, get_value, update_values
from .telnyx import TelnyxError, handle_webhook, send_message
from .timeutil import now_est


STATIC_DIR = config.ROOT / "static"
MESSAGE_PAGE_SIZE = 80


def _json_default(value):
    return str(value)


def _row_dict(row) -> dict:
    return dict(row) if row else {}


FAILURE_STATUSES = {"delivery_failed", "failed", "undelivered", "rejected", "expired"}
WARNING_STATUSES = {"delivery_unconfirmed", "unknown", "unconfirmed"}
SUCCESS_STATUSES = {"delivered", "received", "imported"}
PENDING_STATUSES = {"queued", "sending", "sent", "accepted", "finalized"}


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
        ORDER BY candidate.occurred_at DESC, candidate.id DESC
        LIMIT 1
      )
  )
)
"""


def _status_label(status: str | None) -> str:
    labels = {
        "delivery_failed": "Failed",
        "delivery_unconfirmed": "Unconfirmed",
        "queued": "Queued",
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
    message_payload = payload.get("data", {}).get("payload", payload.get("data", payload))
    errors = message_payload.get("errors") if isinstance(message_payload, dict) else None
    if errors:
        return "; ".join(_error_text(error) for error in errors)
    if status == "delivery_unconfirmed":
        return "Telnyx did not receive carrier confirmation for this message."
    if status:
        return f"Telnyx reported {status.replace('_', ' ')}."
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
              )
            )
            """
        )
        params.extend([like, like, like])
    if before and before_id:
        clauses.append(
            """
            (
              COALESCE(c.last_message_at, c.updated_at) < ?
              OR (COALESCE(c.last_message_at, c.updated_at) = ? AND c.id < ?)
            )
            """
        )
        params.extend([before, before, before_id])
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        f"""
        SELECT c.*,
          m.text AS last_text,
          m.direction AS last_direction,
          m.status AS last_status,
          m.occurred_at AS last_occurred_at,
          m.raw_json AS last_raw_json,
          (SELECT COUNT(*) FROM messages mm WHERE mm.conversation_id = c.id) AS message_count
        FROM conversations c
        LEFT JOIN messages m ON m.id = (
          SELECT id FROM messages
          WHERE conversation_id = c.id
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
        {where}
        ORDER BY COALESCE(c.last_message_at, c.updated_at) DESC, c.id DESC
        LIMIT ?
        """,
        (*params, limit + 1),
    ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    conversations = []
    for row in rows:
        item = _row_dict(row)
        item["title"] = row["title"] or _conversation_title(conn, row["id"])
        item["participants"] = _participants(conn, row["id"])
        item["sort_at"] = row["last_message_at"] or row["updated_at"]
        item["needs_attention"] = _needs_attention(
            row["last_direction"],
            row["last_occurred_at"],
            row["dealt_with_at"],
            row["manual_unread_at"],
        )
        item["last_status_label"] = _status_label(row["last_status"])
        item["last_status_kind"] = _status_kind(row["last_status"])
        item["last_status_detail"] = _message_status_detail(row["last_status"], row["last_raw_json"])
        conversations.append(item)
    return {"conversations": conversations, "has_more": has_more}


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
            _row_dict(a)
            for a in conn.execute("SELECT * FROM attachments WHERE message_id = ?", (row["id"],)).fetchall()
        ]
        messages.append(_decorate_message_status(message))
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
        _row_dict(row)
        for row in conn.execute("SELECT * FROM identities ORDER BY id").fetchall()
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
    return {
        "identities": identities,
        "stats": stats,
        "server_time_et": server_time,
        "server_time_est": server_time,
        "telnyx_configured": bool(get_value("telnyx.api_key", config.TELNYX_API_KEY)),
        "fastmail_configured": providers.get("fastmail", False),
        "google_contacts_configured": providers.get("google", False),
        "contacts_provider": active_provider(),
        "contact_providers": providers,
        "settings": configured_values(),
        "mark_read_on_open": get_bool("behavior.mark_read_on_open", False),
        "details_collapsed_default": get_bool("behavior.details_collapsed_default", True),
        "default_identity": identities[0]["phone_number"] if identities else "",
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
    conn.execute(
        "UPDATE identities SET label = ?, color = ?, is_active = ?, updated_at = ? WHERE id = ?",
        (label, color, active, now_est(), identity_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM identities WHERE id = ?", (identity_id,)).fetchone()
    return {"identity": _row_dict(row)}


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
    return send_message(
        from_number=payload.get("from_number"),
        to_numbers=to_numbers,
        text=text,
        media_urls=media_urls,
        conversation_id=int(conversation_id) if conversation_id else None,
    )


class TextingHandler(BaseHTTPRequestHandler):
    server_version = "TextingApp/0.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def _send_headers(self, status: int, content_type: str, length: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        no_store_types = ("application/json", "text/html", "text/css", "application/javascript", "text/javascript")
        cache_control = "no-store" if content_type.startswith(no_store_types) else "public, max-age=3600"
        self.send_header("Cache-Control", cache_control)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, default=_json_default).encode("utf-8")
        self._send_headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _read_raw(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

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
            elif path == "/api/conversations":
                self._send_json(list_conversations(query))
            elif path == "/api/conversations/match":
                self._send_json(match_conversation(query))
            elif match := re.fullmatch(r"/api/conversations/(\d+)/messages", path):
                self._send_json(get_messages(int(match.group(1)), query))
            elif path == "/api/contacts":
                self._send_json(search_contacts(query))
            elif path.startswith("/media/"):
                name = Path(unquote(path.removeprefix("/media/"))).name
                self._serve_file(config.MEDIA_DIR / name)
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
            elif path == "/api/contacts/sync":
                self._send_json({"synced": sync_contacts()})
            elif path == "/api/contacts/name":
                self._send_json(save_contact_name(self._read_json()))
            elif path == "/api/settings":
                self._send_json(update_values(self._read_json()))
            elif path == "/api/telnyx/webhook":
                raw = self._read_raw()
                headers = {key.lower(): value for key, value in self.headers.items()}
                self._send_json(handle_webhook(raw, headers))
            else:
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, SettingsError, TelnyxError, FastmailError, GoogleContactsError, ContactsError) as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:
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
    httpd = ThreadingHTTPServer((host, port), TextingHandler)
    print(f"Texting app running at http://{host}:{port}", flush=True)
    httpd.serve_forever()
