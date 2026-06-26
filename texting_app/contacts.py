from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

from . import config
from . import fastmail, google_contacts
from . import settings as app_settings
from .db import as_json, connect, init_db
from .phone import normalize_phone
from .timeutil import now_est


class ContactsError(RuntimeError):
    pass


def active_provider() -> str:
    provider = app_settings.get_value("contacts.provider", config.CONTACTS_PROVIDER).strip().lower()
    if provider in {"", "auto"}:
        providers = configured_providers()
        if providers["fastmail"]:
            return "fastmail"
        if providers["google"]:
            return "google"
        return "none"
    if provider in {"fastmail", "google", "none"}:
        return provider
    raise ContactsError("CONTACTS_PROVIDER must be auto, fastmail, google, or none.")


def configured_providers() -> dict[str, bool]:
    return {
        "fastmail": bool(
            app_settings.get_value("fastmail.api_token", config.FASTMAIL_API_TOKEN)
            or (
                app_settings.get_value("fastmail.username", config.FASTMAIL_USERNAME)
                and app_settings.get_value("fastmail.app_password", config.FASTMAIL_APP_PASSWORD)
            )
        ),
        "google": bool(
            app_settings.get_value("google.contacts_access_token", config.GOOGLE_CONTACTS_ACCESS_TOKEN)
            or (
                app_settings.get_value("google.client_id", config.GOOGLE_CLIENT_ID)
                and app_settings.get_value("google.client_secret", config.GOOGLE_CLIENT_SECRET)
                and app_settings.get_value("google.refresh_token", config.GOOGLE_REFRESH_TOKEN)
            )
        ),
    }


def sync_contacts() -> dict[str, int]:
    provider = active_provider()
    if provider == "google":
        return google_contacts.sync_contacts()
    if provider == "fastmail":
        return fastmail.sync_contacts()
    raise ContactsError("No contact sync provider is configured.")


def save_contact_name(phone_number: str, display_name: str) -> dict[str, Any]:
    return fastmail.save_contact_name(phone_number, display_name)


def _clean_phone_contact_name(value: Any) -> str:
    return " ".join(str(value or "").split())[:140]


def _phone_contact_phones(contact: dict[str, Any]) -> list[tuple[str, str]]:
    phones: list[tuple[str, str]] = []
    seen: set[str] = set()
    raw_phones = contact.get("phones") or contact.get("phone_numbers") or []
    if not isinstance(raw_phones, list):
        return phones
    for item in raw_phones:
        if isinstance(item, dict):
            number = normalize_phone(str(item.get("number") or item.get("phone_number") or ""))
            label = " ".join(str(item.get("label") or "mobile").split())[:40] or "mobile"
        else:
            number = normalize_phone(str(item or ""))
            label = "mobile"
        if not number or number in seen:
            continue
        seen.add(number)
        phones.append((number, label))
    return phones


def _phone_contact_external_id(contact: dict[str, Any], phones: list[tuple[str, str]]) -> str:
    raw = str(contact.get("lookup_key") or contact.get("contact_id") or contact.get("id") or "").strip()
    if not raw:
        raw = "|".join(number for number, _label in phones)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"phone:{digest}"


def _mergeable_phone_contact_id(conn, phones: list[tuple[str, str]]) -> int | None:
    numbers = [number for number, _label in phones]
    if not numbers:
        return None
    placeholders = ",".join("?" for _item in numbers)
    row = conn.execute(
        f"""
        SELECT c.id
        FROM contact_phones cp
        JOIN contacts c ON c.id = cp.contact_id
        WHERE cp.phone_number IN ({placeholders})
          AND c.source NOT IN ('fastmail', 'google')
        ORDER BY c.fastmail_id IS NOT NULL DESC, c.updated_at DESC, c.id DESC
        LIMIT 1
        """,
        numbers,
    ).fetchone()
    return int(row["id"]) if row else None


def _relink_participants_to_best_contacts(conn) -> int:
    rows = conn.execute(
        """
        WITH best AS (
          SELECT cp.conversation_id,
            cp.phone_number,
            cp.contact_id,
            (
              SELECT cph.contact_id
              FROM contact_phones cph
              JOIN contacts c ON c.id = cph.contact_id
              WHERE cph.phone_number = cp.phone_number
              ORDER BY
                CASE c.source
                  WHEN 'fastmail' THEN 3
                  WHEN 'google' THEN 3
                  WHEN 'phone' THEN 2
                  ELSE 1
                END DESC,
                c.updated_at DESC,
                c.id DESC
              LIMIT 1
            ) AS best_contact_id
          FROM conversation_participants cp
          WHERE cp.role = 'participant'
        )
        SELECT conversation_id, phone_number, best_contact_id AS contact_id
        FROM best
        WHERE best_contact_id IS NOT NULL
          AND COALESCE(contact_id, 0) != best_contact_id
        """
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            UPDATE conversation_participants
            SET contact_id = ?
            WHERE conversation_id = ? AND phone_number = ?
            """,
            (row["contact_id"], row["conversation_id"], row["phone_number"]),
        )
    return len(rows)


def import_phone_contacts(payload: dict[str, Any] | list[dict[str, Any]]) -> dict[str, int]:
    contacts = payload.get("contacts") if isinstance(payload, dict) else payload
    if not isinstance(contacts, list):
        raise ValueError("Phone contact import requires a contacts list.")
    conn = connect()
    init_db(conn)
    timestamp = now_est()
    imported = 0
    phone_count = 0
    skipped = 0
    for contact in contacts:
        if not isinstance(contact, dict):
            skipped += 1
            continue
        phones = _phone_contact_phones(contact)
        display_name = _clean_phone_contact_name(contact.get("display_name") or contact.get("name"))
        if not phones or not display_name:
            skipped += 1
            continue
        external_id = _phone_contact_external_id(contact, phones)
        raw = as_json(
            {
                "provider": "android",
                "external_id": str(contact.get("lookup_key") or contact.get("contact_id") or contact.get("id") or ""),
                "display_name": display_name,
                "phones": [{"number": number, "label": label} for number, label in phones],
            }
        )
        row = conn.execute("SELECT id FROM contacts WHERE fastmail_id = ?", (external_id,)).fetchone()
        contact_id = int(row["id"]) if row else (_mergeable_phone_contact_id(conn, phones) or 0)
        if contact_id:
            conn.execute(
                """
                UPDATE contacts
                SET display_name = ?, fastmail_id = ?, source = 'phone', raw_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (display_name, external_id, raw, timestamp, contact_id),
            )
            conn.execute("DELETE FROM contact_phones WHERE contact_id = ?", (contact_id,))
            conn.execute("DELETE FROM contact_emails WHERE contact_id = ?", (contact_id,))
        else:
            cur = conn.execute(
                """
                INSERT INTO contacts(display_name, fastmail_id, source, raw_json, created_at, updated_at)
                VALUES (?, ?, 'phone', ?, ?, ?)
                """,
                (display_name, external_id, raw, timestamp, timestamp),
            )
            contact_id = int(cur.lastrowid)
        for number, label in phones:
            conn.execute(
                "INSERT OR IGNORE INTO contact_phones(contact_id, phone_number, label) VALUES (?, ?, ?)",
                (contact_id, number, label),
            )
            phone_count += 1
        imported += 1
    relinked = _relink_participants_to_best_contacts(conn)
    conn.commit()
    return {"contacts": imported, "phones": phone_count, "participants": relinked, "skipped": skipped}


def start_autosync() -> None:
    if not app_settings.get_bool("contacts.autosync", config.CONTACTS_AUTOSYNC) or active_provider() == "none":
        return

    def worker() -> None:
        while True:
            try:
                sync_contacts()
            except Exception as exc:
                print(f"Contact sync failed: {exc}", flush=True)
            time.sleep(max(app_settings.get_int("contacts.sync_interval_minutes", config.CONTACTS_SYNC_INTERVAL_MINUTES), 5) * 60)

    provider = active_provider()
    thread = threading.Thread(target=worker, name=f"{provider}-contacts-sync", daemon=True)
    thread.start()
