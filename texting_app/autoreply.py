from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .db import connect, init_db
from .phone import normalize_phone
from .timeutil import now_est


DEFAULT_AUTOREPLY_MESSAGE = "Thanks for reaching out. I'm away right now and will reply when I can."
DEFAULT_AUTOREPLY_COOLDOWN_HOURS = 24


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _cooldown_active(last_sent_at: str | None, cooldown_hours: int) -> bool:
    last_sent = _parse_timestamp(last_sent_at)
    current = _parse_timestamp(now_est())
    if not last_sent or not current:
        return False
    return current - last_sent < timedelta(hours=max(cooldown_hours, 1))


def _clean_cooldown_hours(value: Any) -> int:
    try:
        return max(int(str(value).strip()), 1)
    except (TypeError, ValueError):
        return DEFAULT_AUTOREPLY_COOLDOWN_HOURS


def identity_autoreply_fields(row: dict[str, Any]) -> dict[str, Any]:
    message = str(row.get("autoreply_message") or "")
    cooldown = _clean_cooldown_hours(row.get("autoreply_cooldown_hours"))
    return {
        "autoreply_enabled": bool(row.get("autoreply_enabled")),
        "autoreply_message": message,
        "autoreply_cooldown_hours": cooldown,
    }


def update_autoreply_rule(
    conn,
    *,
    phone_number: str,
    enabled: bool,
    message: str,
    cooldown_hours: int,
) -> None:
    phone_number = normalize_phone(phone_number)
    message = str(message or "").strip()
    cooldown_hours = _clean_cooldown_hours(cooldown_hours)
    timestamp = now_est()
    conn.execute(
        """
        INSERT INTO autoreply_rules(phone_number, enabled, message, cooldown_hours, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(phone_number) DO UPDATE SET
          enabled = excluded.enabled,
          message = excluded.message,
          cooldown_hours = excluded.cooldown_hours,
          updated_at = excluded.updated_at
        """,
        (phone_number, 1 if enabled else 0, message, cooldown_hours, timestamp, timestamp),
    )


def _enabled_rule_for_candidates(conn, candidates: list[str]) -> dict[str, Any] | None:
    candidates = [normalize_phone(number) for number in candidates if normalize_phone(number)]
    if not candidates:
        return None
    rows = conn.execute(
        f"""
        SELECT phone_number, enabled, message, cooldown_hours
        FROM autoreply_rules
        WHERE phone_number IN ({",".join("?" for _ in candidates)})
        """,
        candidates,
    ).fetchall()
    rules = {row["phone_number"]: dict(row) for row in rows}
    for candidate in candidates:
        rule = rules.get(candidate)
        if rule and int(rule.get("enabled") or 0) and str(rule.get("message") or "").strip():
            return rule
    return None


def _delivery_recent(conn, phone_number: str, recipient_number: str, cooldown_hours: int) -> bool:
    row = conn.execute(
        """
        SELECT last_sent_at
        FROM autoreply_deliveries
        WHERE phone_number = ? AND recipient_number = ?
        """,
        (phone_number, recipient_number),
    ).fetchone()
    return bool(row and _cooldown_active(row["last_sent_at"], cooldown_hours))


def _record_delivery(conn, phone_number: str, recipient_number: str, message_id: int | None) -> None:
    timestamp = now_est()
    conn.execute(
        """
        INSERT INTO autoreply_deliveries(phone_number, recipient_number, last_sent_at, message_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(phone_number, recipient_number) DO UPDATE SET
          last_sent_at = excluded.last_sent_at,
          message_id = excluded.message_id,
          updated_at = excluded.updated_at
        """,
        (phone_number, recipient_number, timestamp, message_id, timestamp, timestamp),
    )


def maybe_send_autoreply(
    *,
    conversation_id: int,
    from_number: str,
    self_numbers: list[str],
    remote_numbers: list[str] | None = None,
    trigger_message_id: int | None = None,
) -> dict[str, Any]:
    recipient_number = normalize_phone(from_number)
    candidate_self_numbers = [normalize_phone(number) for number in self_numbers if normalize_phone(number)]
    thread_remote_numbers = sorted({normalize_phone(number) for number in (remote_numbers or [recipient_number]) if normalize_phone(number)})
    if not recipient_number or not candidate_self_numbers:
        return {"sent": False, "reason": "missing_numbers"}
    if recipient_number in set(candidate_self_numbers):
        return {"sent": False, "reason": "self_message"}
    if len(thread_remote_numbers) != 1 or thread_remote_numbers[0] != recipient_number:
        return {"sent": False, "reason": "group_thread"}

    conn = connect()
    init_db(conn)
    rule = _enabled_rule_for_candidates(conn, candidate_self_numbers)
    if not rule:
        return {"sent": False, "reason": "disabled"}

    phone_number = normalize_phone(rule["phone_number"])
    message = str(rule["message"] or "").strip()
    cooldown_hours = _clean_cooldown_hours(rule["cooldown_hours"])
    if _delivery_recent(conn, phone_number, recipient_number, cooldown_hours):
        return {"sent": False, "reason": "cooldown"}

    try:
        from .messaging import send_message

        result = send_message(
            from_number=phone_number,
            to_numbers=[recipient_number],
            text=message,
            media_urls=[],
            conversation_id=conversation_id,
        )
    except Exception as exc:
        print(f"autoreply failed from {phone_number} to {recipient_number}: {exc}", flush=True)
        return {"sent": False, "reason": "send_failed", "error": str(exc)}

    message_id = result.get("message_id")
    if message_id:
        conn.execute(
            "UPDATE messages SET source = 'autoreply', updated_at = ? WHERE id = ?",
            (now_est(), int(message_id)),
        )
    _record_delivery(conn, phone_number, recipient_number, int(message_id) if message_id else None)
    conn.commit()
    return {
        "sent": True,
        "message_id": message_id,
        "trigger_message_id": trigger_message_id,
        "from_number": phone_number,
        "to_number": recipient_number,
    }
