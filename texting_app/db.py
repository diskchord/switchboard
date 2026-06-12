from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import config
from .phone import normalize_phone
from .timeutil import now_est


DEALT_WITH_CUTOFF_EST = "2026-06-11T00:00:00-04:00"
DEALT_WITH_CUTOFF_KEY = "dealt_with_cutoff_2026_06_11"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS app_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone_number TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  color TEXT NOT NULL DEFAULT '#2563eb',
  is_self INTEGER NOT NULL DEFAULT 1,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  display_name TEXT NOT NULL,
  fastmail_id TEXT UNIQUE,
  source TEXT NOT NULL DEFAULT 'local',
  raw_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_phones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  phone_number TEXT NOT NULL,
  label TEXT,
  UNIQUE(contact_id, phone_number)
);

CREATE TABLE IF NOT EXISTS contact_emails (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  label TEXT,
  UNIQUE(contact_id, email)
);

CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_key TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK(kind IN ('direct', 'group')),
  title TEXT,
  is_archived INTEGER NOT NULL DEFAULT 0,
  archived_at TEXT,
  dealt_with_at TEXT,
  manual_unread_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_message_at TEXT
);

CREATE TABLE IF NOT EXISTS conversation_participants (
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  phone_number TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('self', 'participant')),
  contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  PRIMARY KEY(conversation_id, phone_number)
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  telnyx_id TEXT,
  telnyx_event_id TEXT,
  import_source_id TEXT UNIQUE,
  direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
  from_number TEXT NOT NULL,
  to_numbers TEXT NOT NULL DEFAULT '[]',
  cc_numbers TEXT NOT NULL DEFAULT '[]',
  text TEXT NOT NULL DEFAULT '',
  message_type TEXT NOT NULL DEFAULT 'SMS',
  status TEXT NOT NULL DEFAULT 'received',
  occurred_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'local',
  raw_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  local_path TEXT,
  remote_url TEXT,
  content_type TEXT,
  size INTEGER,
  sha256 TEXT,
  filename TEXT,
  source TEXT NOT NULL DEFAULT 'local'
);

CREATE TABLE IF NOT EXISTS telnyx_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_time ON messages(conversation_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_messages_telnyx_id ON messages(telnyx_id);
CREATE INDEX IF NOT EXISTS idx_contact_phones_phone ON contact_phones(phone_number);
CREATE INDEX IF NOT EXISTS idx_conversations_last ON conversations(last_message_at DESC);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate_schema(conn)
    seed_identities(conn)
    conn.commit()


def migrate_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    if "is_archived" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0")
    if "archived_at" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN archived_at TEXT")
    if "dealt_with_at" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN dealt_with_at TEXT")
    if "manual_unread_at" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN manual_unread_at TEXT")
    apply_dealt_with_cutoff(conn)


def apply_dealt_with_cutoff(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM app_metadata WHERE key = ?", (DEALT_WITH_CUTOFF_KEY,)).fetchone():
        return
    count = conn.execute("SELECT COUNT(*) AS count FROM conversations").fetchone()["count"]
    if not count:
        return
    timestamp = now_est()
    conn.execute(
        """
        UPDATE conversations
        SET dealt_with_at = COALESCE(dealt_with_at, ?)
        WHERE dealt_with_at IS NULL
        """,
        (DEALT_WITH_CUTOFF_EST,),
    )
    conn.execute(
        "INSERT INTO app_metadata(key, value, updated_at) VALUES (?, ?, ?)",
        (DEALT_WITH_CUTOFF_KEY, DEALT_WITH_CUTOFF_EST, timestamp),
    )


def seed_identities(conn: sqlite3.Connection) -> None:
    timestamp = now_est()
    for idx, raw in enumerate(config.PERSONAL_NUMBERS):
        phone = normalize_phone(raw)
        label = config.DEFAULT_IDENTITY_LABELS.get(phone) or config.DEFAULT_IDENTITY_LABELS.get(raw) or phone
        color = config.IDENTITY_COLORS[idx % len(config.IDENTITY_COLORS)]
        conn.execute(
            """
            INSERT INTO identities(phone_number, label, color, is_self, is_active, created_at, updated_at)
            VALUES (?, ?, ?, 1, 1, ?, ?)
            ON CONFLICT(phone_number) DO NOTHING
            """,
            (phone, label, color, timestamp, timestamp),
        )


def self_numbers(conn: sqlite3.Connection) -> set[str]:
    return {
        row["phone_number"]
        for row in conn.execute("SELECT phone_number FROM identities WHERE is_self = 1")
    }


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def from_json(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def contact_for_phone(conn: sqlite3.Connection, phone: str) -> int | None:
    row = conn.execute(
        """
        SELECT cp.contact_id
        FROM contact_phones cp
        JOIN contacts c ON c.id = cp.contact_id
        WHERE cp.phone_number = ?
        ORDER BY c.source IN ('fastmail', 'google') DESC, c.updated_at DESC
        LIMIT 1
        """,
        (phone,),
    ).fetchone()
    return int(row["contact_id"]) if row else None


def ensure_contact_for_phone(conn: sqlite3.Connection, phone: str) -> int:
    phone = normalize_phone(phone)
    existing = contact_for_phone(conn, phone)
    if existing:
        return existing
    timestamp = now_est()
    cur = conn.execute(
        """
        INSERT INTO contacts(display_name, source, created_at, updated_at)
        VALUES (?, 'phone', ?, ?)
        """,
        (phone, timestamp, timestamp),
    )
    contact_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO contact_phones(contact_id, phone_number, label) VALUES (?, ?, 'mobile')",
        (contact_id, phone),
    )
    return contact_id


def conversation_key(remote_numbers: list[str]) -> str:
    cleaned = sorted({normalize_phone(n) for n in remote_numbers if normalize_phone(n)})
    return "group:" + ",".join(cleaned) if len(cleaned) > 1 else "direct:" + (cleaned[0] if cleaned else "unknown")


def ensure_conversation(
    conn: sqlite3.Connection,
    remote_numbers: list[str],
    self_participants: list[str] | None = None,
    title: str | None = None,
) -> int:
    remote_numbers = sorted({normalize_phone(n) for n in remote_numbers if normalize_phone(n)})
    self_participants = sorted({normalize_phone(n) for n in (self_participants or []) if normalize_phone(n)})
    key = conversation_key(remote_numbers)
    timestamp = now_est()
    kind = "group" if len(remote_numbers) > 1 else "direct"
    row = conn.execute("SELECT id FROM conversations WHERE conversation_key = ?", (key,)).fetchone()
    if row:
        conversation_id = int(row["id"])
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (timestamp, conversation_id))
    else:
        cur = conn.execute(
            """
            INSERT INTO conversations(conversation_key, kind, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, kind, title, timestamp, timestamp),
        )
        conversation_id = int(cur.lastrowid)

    for phone in remote_numbers:
        contact_id = ensure_contact_for_phone(conn, phone)
        conn.execute(
            """
            INSERT INTO conversation_participants(conversation_id, phone_number, role, contact_id)
            VALUES (?, ?, 'participant', ?)
            ON CONFLICT(conversation_id, phone_number) DO UPDATE SET contact_id = excluded.contact_id
            """,
            (conversation_id, phone, contact_id),
        )
    for phone in self_participants:
        conn.execute(
            """
            INSERT INTO conversation_participants(conversation_id, phone_number, role)
            VALUES (?, ?, 'self')
            ON CONFLICT(conversation_id, phone_number) DO NOTHING
            """,
            (conversation_id, phone),
        )
    return conversation_id


def upsert_message(
    conn: sqlite3.Connection,
    *,
    conversation_id: int,
    direction: str,
    from_number: str,
    to_numbers: list[str],
    cc_numbers: list[str] | None,
    text: str,
    occurred_at: str,
    message_type: str = "SMS",
    status: str = "received",
    source: str = "local",
    telnyx_id: str | None = None,
    telnyx_event_id: str | None = None,
    import_source_id: str | None = None,
    raw_json: Any = None,
) -> int:
    timestamp = now_est()
    values = (
        conversation_id,
        telnyx_id,
        telnyx_event_id,
        import_source_id,
        direction,
        normalize_phone(from_number),
        as_json([normalize_phone(n) for n in to_numbers if normalize_phone(n)]),
        as_json([normalize_phone(n) for n in (cc_numbers or []) if normalize_phone(n)]),
        text or "",
        message_type,
        status,
        occurred_at,
        source,
        as_json(raw_json) if raw_json is not None else None,
        timestamp,
        timestamp,
    )
    cur = conn.execute(
        """
        INSERT INTO messages(
          conversation_id, telnyx_id, telnyx_event_id, import_source_id, direction,
          from_number, to_numbers, cc_numbers, text, message_type, status, occurred_at,
          source, raw_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(import_source_id) DO UPDATE SET
          text = excluded.text,
          updated_at = excluded.updated_at
        """,
        values,
    )
    if import_source_id:
        row = conn.execute("SELECT id FROM messages WHERE import_source_id = ?", (import_source_id,)).fetchone()
        message_id = int(row["id"])
    else:
        message_id = int(cur.lastrowid)
    conn.execute(
        """
        UPDATE conversations
        SET last_message_at = CASE
            WHEN last_message_at IS NULL OR last_message_at < ? THEN ?
            ELSE last_message_at
          END,
          updated_at = ?
        WHERE id = ?
        """,
        (occurred_at, occurred_at, timestamp, conversation_id),
    )
    return message_id


def add_attachment(
    conn: sqlite3.Connection,
    message_id: int,
    *,
    local_path: str | None = None,
    remote_url: str | None = None,
    content_type: str | None = None,
    size: int | None = None,
    sha256: str | None = None,
    filename: str | None = None,
    source: str = "local",
) -> None:
    conn.execute(
        """
        INSERT INTO attachments(message_id, local_path, remote_url, content_type, size, sha256, filename, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (message_id, local_path, remote_url, content_type, size, sha256, filename, source),
    )
