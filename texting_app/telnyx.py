from __future__ import annotations

import base64
import json
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import config
from . import settings as app_settings
from .attachment_ingestion import enqueue_remote_attachment
from .db import (
    add_attachment,
    add_provider_message_ref,
    as_json,
    connect,
    ensure_conversation,
    init_db,
    self_numbers,
    upsert_message,
)
from .phone import display_phone, normalize_phone
from .timeutil import normalize_iso_timestamp, now_est


class TelnyxError(RuntimeError):
    pass


OUTBOUND_STATUS_EVENTS = {
    "message.sent",
    "message.delivered",
    "message.finalized",
    "message.delivery_failed",
    "message.delivery_unconfirmed",
    "message.failed",
}

EVENT_STATUS_MAP = {
    "message.sent": "sent",
    "message.delivered": "delivered",
    "message.delivery_failed": "delivery_failed",
    "message.delivery_unconfirmed": "delivery_unconfirmed",
    "message.failed": "delivery_failed",
}


def _telnyx_error_text(error: Any) -> str:
    if isinstance(error, str):
        return error
    if not isinstance(error, dict):
        return str(error)
    code = error.get("code") or error.get("error_code")
    title = error.get("title") or error.get("error") or error.get("reason")
    detail = error.get("detail") or error.get("message") or error.get("description")
    parts = [str(part) for part in (code, title, detail) if part]
    return " - ".join(parts) if parts else json.dumps(error, ensure_ascii=False)


def _format_telnyx_error_response(status_code: int, detail: str) -> str:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        payload = None
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        return f"Telnyx rejected the message ({status_code}): " + "; ".join(_telnyx_error_text(error) for error in errors)
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error_description") or payload.get("detail")
        if message:
            return f"Telnyx rejected the message ({status_code}): {message}"
    return f"Telnyx API returned {status_code}: {detail}"


def _json_request(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TelnyxError(_format_telnyx_error_response(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise TelnyxError(f"Telnyx API request failed: {exc}") from exc


def _notification_text(text: str, media_count: int) -> str:
    text = text.strip()
    if text and media_count:
        return f"{text}\n\n{media_count} attachment{'s' if media_count != 1 else ''}"
    if text:
        return text
    if media_count:
        return f"{media_count} attachment{'s' if media_count != 1 else ''}"
    return "New text message"


def _ntfy_url() -> str:
    endpoint = app_settings.get_value("notifications.ntfy_endpoint", config.NTFY_ENDPOINT).strip()
    if not endpoint:
        return ""
    if "://" not in endpoint:
        endpoint = f"https://{endpoint}"
    return endpoint


def _contact_name_for_phone(conn: sqlite3.Connection, phone: str) -> str:
    phone = normalize_phone(phone)
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
        return str(row["display_name"])
    return display_phone(phone)


def _notify_incoming_message(*, from_number: str, sender_name: str, text: str, media_count: int) -> None:
    if not app_settings.get_bool("notifications.ntfy_enabled", config.NTFY_ENABLED):
        return
    url = _ntfy_url()
    if not url:
        return
    body = _notification_text(text, media_count).encode("utf-8")
    sender = sender_name.strip() or display_phone(from_number)
    title = f"Text from {sender}"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Title": title.replace("\r", " ").replace("\n", " ")[:120],
            "Tags": "speech_balloon",
            "Priority": "default",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5):
            return
    except Exception as exc:
        print(f"ntfy notification failed for {url}: {exc}", flush=True)


def send_message(
    *,
    from_number: str,
    to_numbers: list[str],
    text: str,
    media_urls: list[str] | None = None,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    api_key = app_settings.get_value("telnyx.api_key", config.TELNYX_API_KEY)
    api_base = app_settings.get_value("telnyx.api_base", config.TELNYX_API_BASE).rstrip("/")
    if not api_key:
        raise TelnyxError("TELNYX_API_KEY is not configured.")
    from_number = normalize_phone(from_number)
    to_numbers = [normalize_phone(n) for n in to_numbers if normalize_phone(n)]
    media_urls = [u.strip() for u in (media_urls or []) if u.strip()]
    if not from_number or not to_numbers:
        raise TelnyxError("A sender and at least one recipient are required.")
    if len(to_numbers) > 8:
        raise TelnyxError("Telnyx group MMS supports up to 8 recipients.")
    if len(to_numbers) > 1:
        endpoint = f"{api_base}/messages/group_mms"
        payload: dict[str, Any] = {"from": from_number, "to": to_numbers}
    else:
        endpoint = f"{api_base}/messages"
        payload = {"from": from_number, "to": to_numbers[0]}
    if text:
        payload["text"] = text
    if media_urls:
        payload["media_urls"] = media_urls
    response = _json_request(endpoint, payload, api_key)

    conn = connect()
    init_db(conn)
    known_self = self_numbers(conn)
    remote_numbers = sorted(n for n in to_numbers if n not in known_self)
    conversation_id = conversation_id or ensure_conversation(conn, remote_numbers, [from_number])
    data = response.get("data", response)
    telnyx_id = data.get("id")
    message_id = upsert_message(
        conn,
        conversation_id=conversation_id,
        direction="outbound",
        from_number=from_number,
        to_numbers=to_numbers,
        cc_numbers=[],
        text=text,
        occurred_at=normalize_iso_timestamp(data.get("sent_at") or data.get("received_at")),
        message_type="MMS" if media_urls or len(to_numbers) > 1 else "SMS",
        status=data.get("to", [{}])[0].get("status", "queued") if isinstance(data.get("to"), list) else "queued",
        source="telnyx",
        telnyx_id=telnyx_id,
        raw_json=response,
    )
    for url in media_urls:
        add_attachment(conn, message_id, remote_url=url, filename=Path(url).name, source="telnyx")
    add_provider_message_ref(conn, provider="telnyx", provider_message_id=telnyx_id, message_id=message_id)
    conn.commit()
    return {"message_id": message_id, "telnyx": response}


def send_fax(
    *,
    from_number: str,
    to_number: str,
    media_url: str,
    filename: str = "",
    conversation_id: int | None = None,
) -> dict[str, Any]:
    api_key = app_settings.get_value("telnyx.api_key", config.TELNYX_API_KEY)
    api_base = app_settings.get_value("telnyx.api_base", config.TELNYX_API_BASE).rstrip("/")
    connection_id = app_settings.get_value("telnyx.fax_connection_id", config.TELNYX_FAX_CONNECTION_ID).strip()
    if not api_key:
        raise TelnyxError("TELNYX_API_KEY is not configured.")
    if not connection_id:
        raise TelnyxError("TELNYX_FAX_CONNECTION_ID is not configured.")
    from_number = normalize_phone(from_number)
    to_number = normalize_phone(to_number)
    media_url = str(media_url or "").strip()
    if not from_number or not to_number:
        raise TelnyxError("A sender and fax recipient are required.")
    if not media_url:
        raise TelnyxError("A fax document URL is required.")

    response = _json_request(
        f"{api_base}/faxes",
        {
            "connection_id": connection_id,
            "from": from_number,
            "to": to_number,
            "media_url": media_url,
            "store_media": True,
            "store_preview": True,
        },
        api_key,
    )

    conn = connect()
    init_db(conn)
    known_self = self_numbers(conn)
    remote_numbers = [] if to_number in known_self else [to_number]
    self_participants = [from_number] if from_number in known_self or from_number else []
    conversation_id = conversation_id or ensure_conversation(conn, remote_numbers, self_participants)
    data = response.get("data", response)
    fax_id = str(data.get("id") or "")
    telnyx_id = f"fax:{fax_id}" if fax_id else ""
    status = str(data.get("status") or "queued")
    display_name = filename or Path(urlparse(media_url).path).name or "fax"
    message_id = upsert_message(
        conn,
        conversation_id=conversation_id,
        direction="outbound",
        from_number=from_number,
        to_numbers=[to_number],
        cc_numbers=[],
        text=f"Fax queued: {display_name}",
        occurred_at=normalize_iso_timestamp(data.get("created_at") or data.get("updated_at")),
        message_type="Fax",
        status=status,
        source="telnyx-fax",
        telnyx_id=telnyx_id or None,
        raw_json=response,
    )
    add_attachment(
        conn,
        message_id,
        remote_url=media_url,
        content_type="application/pdf" if display_name.lower().endswith(".pdf") else "",
        filename=display_name,
        source="telnyx-fax",
    )
    add_provider_message_ref(conn, provider="telnyx", provider_message_id=telnyx_id, message_id=message_id)
    conn.commit()
    return {"message_id": message_id, "conversation_id": conversation_id, "telnyx": response}


def verify_signature(raw_body: bytes, signature: str | None, timestamp: str | None) -> bool:
    public_key_value = app_settings.get_value("telnyx.public_key", config.TELNYX_PUBLIC_KEY)
    if not public_key_value:
        return app_settings.get_bool(
            "webhooks.allow_unsigned_provider",
            config.ALLOW_UNSIGNED_PROVIDER_WEBHOOKS,
        )
    if not signature or not timestamp:
        return False
    try:
        signed_at = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - signed_at) > 300:
        return False
    try:
        key_bytes = bytes.fromhex(public_key_value)
    except ValueError:
        key_bytes = base64.b64decode(public_key_value)
    public_key = Ed25519PublicKey.from_public_bytes(key_bytes)
    message = timestamp.encode("utf-8") + b"|" + raw_body
    try:
        public_key.verify(base64.b64decode(signature), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def _payload_phone(value: Any) -> str:
    if isinstance(value, dict):
        return normalize_phone(value.get("phone_number", ""))
    return normalize_phone(str(value or ""))


def _payload_phone_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [n for n in (_payload_phone(v) for v in value) if n]
    number = _payload_phone(value)
    return [number] if number else []


def _payload_status(payload: dict[str, Any], event_type: str) -> str:
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    to_entries = payload.get("to") or []
    if isinstance(to_entries, list):
        for entry in to_entries:
            if isinstance(entry, dict) and isinstance(entry.get("status"), str) and entry["status"]:
                return entry["status"]
    return EVENT_STATUS_MAP.get(event_type, "finalized" if event_type == "message.finalized" else "sent")


def _message_lookup_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("id", "message_id", "group_message_id"):
        value = payload.get(key)
        if isinstance(value, str) and value and value not in ids:
            ids.append(value)
    return ids


def _update_outbound_status(conn, event: dict[str, Any]) -> int | None:
    data = event.get("data", {})
    payload = data.get("payload", {})
    event_type = data.get("event_type", "")
    message_ids = _message_lookup_ids(payload)
    if not message_ids:
        return None
    status = _payload_status(payload, event_type)
    row = conn.execute(
        f"""
        SELECT id
        FROM messages
        WHERE telnyx_id IN ({",".join("?" for _ in message_ids)})
        ORDER BY id DESC
        LIMIT 1
        """,
        message_ids,
    ).fetchone()
    if not row:
        return None
    conn.execute(
        """
        UPDATE messages
        SET status = ?, telnyx_event_id = ?, raw_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, data.get("id"), as_json(event), now_est(), row["id"]),
    )
    return int(row["id"])


def _humanize_token(value: Any) -> str:
    return str(value or "").replace("_", " ").strip()


def _fax_failed(payload: dict[str, Any], event_type: str) -> bool:
    status = str(payload.get("status") or "").lower()
    return event_type.endswith(".failed") or status == "failed" or bool(payload.get("failure_reason"))


def _fax_message_text(payload: dict[str, Any], event_type: str) -> str:
    if _fax_failed(payload, event_type):
        reason = _humanize_token(payload.get("failure_reason"))
        return f"Fax failed: {reason}" if reason else "Fax failed"
    page_count = payload.get("page_count")
    if isinstance(page_count, str) and page_count.isdigit():
        page_count = int(page_count)
    if isinstance(page_count, int) and page_count > 0:
        return f"Fax received ({page_count} page{'s' if page_count != 1 else ''})"
    return "Fax received"


def _fax_status(payload: dict[str, Any], event_type: str) -> str:
    raw_status = str(payload.get("status") or "").strip().lower().replace(".", "_")
    if _fax_failed(payload, event_type):
        return "failed"
    event_status = str(event_type or "").removeprefix("fax.").replace(".", "_")
    if event_status == "delivered":
        return "delivered"
    if event_status == "queued":
        return "queued"
    if event_status == "sending_started":
        return "sending"
    if raw_status:
        return raw_status
    return event_status or "queued"


def _looks_like_fax_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return any(payload.get(key) for key in ("fax_id", "connection_id", "media_url", "failure_reason"))


def _infer_fax_event_type(payload: dict[str, Any], explicit: Any = None) -> str:
    event_type = str(explicit or "").strip()
    if event_type.startswith("fax."):
        return event_type
    status = str(payload.get("status") or "").strip().lower()
    if _fax_failed(payload, event_type):
        return "fax.failed"
    if status in {"received", "delivered", "completed", "success", "succeeded"} or payload.get("media_url"):
        return "fax.received"
    return "fax.status"


def _fax_payload_timestamp(payload: dict[str, Any]) -> Any:
    return (
        payload.get("completed_at")
        or payload.get("failed_at")
        or payload.get("received_at")
        or payload.get("created_at")
    )


def _root_event_id(root: dict[str, Any], payload: dict[str, Any]) -> str | None:
    for key in ("event_id", "webhook_id"):
        value = root.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    root_id = root.get("id")
    fax_id = payload.get("fax_id")
    if isinstance(root_id, str) and root_id.strip() and root_id != fax_id:
        return root_id.strip()
    return None


def _normalize_webhook_event(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    if isinstance(data, dict) and isinstance(data.get("payload"), dict):
        payload = data["payload"]
        if data.get("event_type") or not _looks_like_fax_payload(payload):
            return event
        normalized_data = dict(data)
        normalized_data["id"] = normalized_data.get("id") or _root_event_id(event, payload)
        normalized_data["event_type"] = _infer_fax_event_type(payload, event.get("event_type") or event.get("type"))
        normalized_data["occurred_at"] = (
            normalized_data.get("occurred_at") or event.get("occurred_at") or _fax_payload_timestamp(payload)
        )
        return {**event, "data": normalized_data}

    if isinstance(data, dict) and _looks_like_fax_payload(data):
        return {
            "data": {
                "id": _root_event_id(event, data),
                "event_type": _infer_fax_event_type(data, event.get("event_type") or data.get("event_type")),
                "occurred_at": event.get("occurred_at") or data.get("occurred_at") or _fax_payload_timestamp(data),
                "payload": data,
            }
        }

    payload = event.get("payload")
    if isinstance(payload, dict) and _looks_like_fax_payload(payload):
        return {
            "data": {
                "id": _root_event_id(event, payload),
                "event_type": _infer_fax_event_type(payload, event.get("event_type") or event.get("type")),
                "occurred_at": event.get("occurred_at") or _fax_payload_timestamp(payload),
                "payload": payload,
            }
        }

    if _looks_like_fax_payload(event):
        return {
            "data": {
                "id": _root_event_id(event, event),
                "event_type": _infer_fax_event_type(event, event.get("event_type") or event.get("type")),
                "occurred_at": event.get("occurred_at") or _fax_payload_timestamp(event),
                "payload": event,
            }
        }

    return event


def _enqueue_webhook_media(
    conn: sqlite3.Connection,
    message_id: int,
    media_items: Any,
    *,
    key_prefix: str,
    source: str = "telnyx",
) -> int:
    count = 0
    for index, media in enumerate(media_items if isinstance(media_items, list) else []):
        if not isinstance(media, dict):
            continue
        remote_url = str(media.get("url") or "").strip()
        if not remote_url:
            continue
        enqueue_remote_attachment(
            conn,
            message_id,
            provider="telnyx",
            remote_url=remote_url,
            content_type=media.get("content_type"),
            size=media.get("size"),
            sha256=media.get("sha256"),
            filename=Path(urlparse(remote_url).path).name,
            source=source,
            dedupe_key=f"telnyx:{key_prefix}:{index}",
        )
        count += 1
    return count


def _store_fax_event(conn, event: dict[str, Any], raw_event: dict[str, Any] | None = None) -> int | None:
    data = event.get("data", {})
    payload = data.get("payload", {})
    event_type = str(data.get("event_type") or "")
    stored_event = raw_event if raw_event is not None else event
    if not event_type.startswith("fax."):
        return None

    fax_id = str(payload.get("fax_id") or payload.get("id") or data.get("id") or "")
    telnyx_id = f"fax:{fax_id}" if fax_id else str(data.get("id") or "")
    if payload.get("direction") and str(payload.get("direction")).lower() != "inbound":
        if not telnyx_id:
            return None
        row = conn.execute("SELECT id FROM messages WHERE telnyx_id = ? ORDER BY id LIMIT 1", (telnyx_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            """
            UPDATE messages
            SET status = ?, telnyx_event_id = ?, raw_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (_fax_status(payload, event_type), data.get("id"), as_json(stored_event), now_est(), row["id"]),
        )
        add_provider_message_ref(conn, provider="telnyx", provider_message_id=telnyx_id, message_id=int(row["id"]))
        conn.commit()
        return int(row["id"])
    if event_type != "fax.received" and not _fax_failed(payload, event_type):
        return None

    occurred_at = normalize_iso_timestamp(payload.get("completed_at") or payload.get("failed_at") or data.get("occurred_at"))
    from_number = _payload_phone(payload.get("from") or payload.get("caller_id"))
    to_numbers = _payload_phone_list(payload.get("to"))
    known_self = self_numbers(conn)
    participants = set([from_number] + to_numbers)
    remote_numbers = sorted(n for n in participants if n and n not in known_self)
    self_participants = sorted(n for n in participants if n in known_self)
    conversation_id = ensure_conversation(conn, remote_numbers, self_participants)
    status = str(payload.get("status") or ("failed" if _fax_failed(payload, event_type) else "received"))
    text = _fax_message_text(payload, event_type)
    media_url = str(payload.get("media_url") or "").strip()
    fax_media = (
        [
            {
                "url": media_url,
                "content_type": "application/pdf",
                "size": payload.get("media_size") or payload.get("file_size"),
                "sha256": payload.get("sha256"),
            }
        ]
        if media_url
        else []
    )

    if telnyx_id:
        row = conn.execute("SELECT id FROM messages WHERE telnyx_id = ? ORDER BY id LIMIT 1", (telnyx_id,)).fetchone()
        if row:
            conn.execute(
                """
                UPDATE messages
                SET text = ?, status = ?, telnyx_event_id = ?, raw_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (text, status, data.get("id"), as_json(stored_event), now_est(), row["id"]),
            )
            existing_id = int(row["id"])
            add_provider_message_ref(conn, provider="telnyx", provider_message_id=telnyx_id, message_id=existing_id)
            _enqueue_webhook_media(
                conn,
                existing_id,
                fax_media,
                key_prefix=f"fax:{fax_id or telnyx_id}",
                source="telnyx-fax",
            )
            return existing_id

    message_id = upsert_message(
        conn,
        conversation_id=conversation_id,
        direction="inbound",
        from_number=from_number,
        to_numbers=to_numbers,
        cc_numbers=[],
        text=text,
        occurred_at=occurred_at,
        message_type="MMS",
        status=status,
        source="telnyx-fax",
        telnyx_id=telnyx_id or None,
        telnyx_event_id=data.get("id"),
        raw_json=stored_event,
    )
    add_provider_message_ref(conn, provider="telnyx", provider_message_id=telnyx_id, message_id=message_id)

    attachment_count = _enqueue_webhook_media(
        conn,
        message_id,
        fax_media,
        key_prefix=f"fax:{fax_id or telnyx_id or message_id}",
        source="telnyx-fax",
    )

    # Commit the message, remote metadata, and ingestion job before notification
    # delivery. PDF download/page rendering now happens solely in the worker.
    conn.commit()
    _notify_incoming_message(
        from_number=from_number,
        sender_name=_contact_name_for_phone(conn, from_number),
        text=text,
        media_count=attachment_count,
    )
    return message_id


def _store_webhook_message(conn, event: dict[str, Any], raw_event: dict[str, Any] | None = None) -> int | None:
    data = event.get("data", {})
    payload = data.get("payload", {})
    event_type = data.get("event_type", "")
    stored_event = raw_event if raw_event is not None else event
    telnyx_id = payload.get("id")
    occurred_at = normalize_iso_timestamp(
        payload.get("received_at")
        or payload.get("sent_at")
        or payload.get("completed_at")
        or data.get("occurred_at")
    )
    if event_type in OUTBOUND_STATUS_EVENTS:
        updated_id = _update_outbound_status(conn, event)
        if updated_id:
            return updated_id
    if str(event_type).startswith("fax."):
        return _store_fax_event(conn, event, raw_event=stored_event)
    if event_type not in {"message.received", "message.sent"}:
        return None

    direction = "inbound" if payload.get("direction") == "inbound" or event_type == "message.received" else "outbound"
    from_number = _payload_phone(payload.get("from"))
    to_numbers = _payload_phone_list(payload.get("to"))
    cc_numbers = _payload_phone_list(payload.get("cc"))
    known_self = self_numbers(conn)
    participants = set([from_number] + to_numbers + cc_numbers)
    remote_numbers = sorted(n for n in participants if n and n not in known_self)
    self_participants = sorted(n for n in participants if n in known_self)
    conversation_id = ensure_conversation(conn, remote_numbers, self_participants)
    status = "received" if direction == "inbound" else _payload_status(payload, event_type)
    media_items = payload.get("media") or []
    if telnyx_id:
        row = conn.execute("SELECT id FROM messages WHERE telnyx_id = ? ORDER BY id LIMIT 1", (telnyx_id,)).fetchone()
        if row:
            conn.execute(
                """
                UPDATE messages
                SET status = ?, telnyx_event_id = ?, raw_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, data.get("id"), as_json(stored_event), now_est(), row["id"]),
            )
            existing_id = int(row["id"])
            _enqueue_webhook_media(
                conn,
                existing_id,
                media_items,
                key_prefix=str(telnyx_id or existing_id),
            )
            add_provider_message_ref(conn, provider="telnyx", provider_message_id=telnyx_id, message_id=existing_id)
            return existing_id
    message_id = upsert_message(
        conn,
        conversation_id=conversation_id,
        direction=direction,
        from_number=from_number,
        to_numbers=to_numbers,
        cc_numbers=cc_numbers,
        text=payload.get("text") or "",
        occurred_at=occurred_at,
        message_type=payload.get("type") or ("MMS" if payload.get("media") else "SMS"),
        status=status,
        source="telnyx",
        telnyx_id=telnyx_id,
        telnyx_event_id=data.get("id"),
        raw_json=stored_event,
    )
    media_count = _enqueue_webhook_media(
        conn,
        message_id,
        media_items,
        key_prefix=str(telnyx_id or data.get("id") or message_id),
    )
    add_provider_message_ref(conn, provider="telnyx", provider_message_id=telnyx_id, message_id=message_id)
    if direction == "inbound":
        # The worker cannot claim these jobs until this prompt commit succeeds.
        conn.commit()
        _notify_incoming_message(
            from_number=from_number,
            sender_name=_contact_name_for_phone(conn, from_number),
            text=payload.get("text") or "",
            media_count=media_count,
        )
        try:
            from .autoreply import maybe_send_autoreply

            maybe_send_autoreply(
                conversation_id=conversation_id,
                from_number=from_number,
                self_numbers=self_participants or to_numbers,
                remote_numbers=remote_numbers,
                trigger_message_id=message_id,
            )
        except Exception as exc:
            print(f"autoreply processing failed for inbound Telnyx message {message_id}: {exc}", flush=True)
    return message_id


def handle_webhook(raw_body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    signature = headers.get("telnyx-signature-ed25519")
    timestamp = headers.get("telnyx-timestamp")
    if not verify_signature(raw_body, signature, timestamp):
        public_key_value = app_settings.get_value("telnyx.public_key", config.TELNYX_PUBLIC_KEY).strip()
        if not public_key_value and not app_settings.get_bool(
            "webhooks.allow_unsigned_provider",
            config.ALLOW_UNSIGNED_PROVIDER_WEBHOOKS,
        ):
            raise TelnyxError(
                "Telnyx webhook verification requires TELNYX_PUBLIC_KEY. "
                "For isolated local testing only, set TEXTING_ALLOW_UNSIGNED_PROVIDER_WEBHOOKS=1."
            )
        raise TelnyxError("Telnyx webhook signature verification failed.")
    raw_event = json.loads(raw_body.decode("utf-8"))
    event = _normalize_webhook_event(raw_event)
    data = event.get("data", {})
    event_id = data.get("id")
    event_type = data.get("event_type", "unknown")
    occurred_at = normalize_iso_timestamp(data.get("occurred_at"))

    conn = connect()
    init_db(conn)
    if event_id:
        try:
            conn.execute(
                """
                INSERT INTO telnyx_events(event_id, event_type, occurred_at, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, event_type, occurred_at, as_json(raw_event), now_est()),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return {"duplicate": True, "event_id": event_id}
    message_id = _store_webhook_message(conn, event, raw_event=raw_event)
    conn.commit()
    return {"event_id": event_id, "event_type": event_type, "message_id": message_id}
