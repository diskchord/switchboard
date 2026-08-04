from __future__ import annotations

from contextlib import closing
from datetime import datetime
from typing import Any

from .db import connect, from_json
from .phone import display_phone, normalize_phone
from .timeutil import EASTERN, now_est


REVIEW_STATES = {"presented", "dismissed", "deferred", "resolved"}

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

UNREAD_MESSAGE_CLAUSE = """
COALESCE(um.source, '') != 'autoreply'
AND (
  (
    c.manual_unread_at IS NOT NULL
    AND um.occurred_at >= c.manual_unread_at
  )
  OR (
    c.manual_unread_at IS NULL
    AND um.direction = 'inbound'
    AND (c.dealt_with_at IS NULL OR um.occurred_at > c.dealt_with_at)
  )
)
"""


def _query_value(query: dict[str, list[str]], name: str, default: str) -> str:
    values = query.get(name) or [default]
    return str(values[0])


def _query_bool(query: dict[str, list[str]], name: str, default: bool) -> bool:
    raw = _query_value(query, name, "true" if default else "false").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _query_int(
    query: dict[str, list[str]],
    name: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int,
) -> int:
    try:
        value = int(_query_value(query, name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _preferred_contacts(conn, phones: list[str]) -> dict[str, dict]:
    unique_phones = list(dict.fromkeys(phone for phone in phones if phone))
    contacts: dict[str, dict] = {}
    if not unique_phones:
        return contacts
    for offset in range(0, len(unique_phones), 800):
        chunk = unique_phones[offset : offset + 800]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT cp.phone_number, c.id, c.display_name, c.source, c.updated_at
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
              c.updated_at DESC,
              c.id DESC
            """,
            chunk,
        ).fetchall()
        for row in rows:
            contacts.setdefault(
                row["phone_number"],
                {
                    "id": row["id"],
                    "display_name": row["display_name"],
                    "source": row["source"],
                },
            )
    return contacts


def _participant_rows(conn, conversation_ids: list[int]) -> dict[int, list[dict]]:
    result = {conversation_id: [] for conversation_id in conversation_ids}
    if not conversation_ids:
        return result
    placeholders = ",".join("?" for _ in conversation_ids)
    rows = conn.execute(
        f"""
        SELECT cp.conversation_id, cp.phone_number, cp.role, i.label AS identity_label
        FROM conversation_participants cp
        LEFT JOIN identities i ON i.phone_number = cp.phone_number
        WHERE cp.conversation_id IN ({placeholders})
        ORDER BY cp.conversation_id, cp.role DESC, cp.phone_number
        """,
        conversation_ids,
    ).fetchall()
    preferred = _preferred_contacts(conn, [row["phone_number"] for row in rows])
    for row in rows:
        contact = preferred.get(row["phone_number"])
        result[row["conversation_id"]].append(
            {
                "phone_number": row["phone_number"],
                "role": row["role"],
                "display_name": row["identity_label"]
                or (contact or {}).get("display_name")
                or display_phone(row["phone_number"]),
                "contact_id": (contact or {}).get("id"),
            }
        )
    return result


def _summary_contact(row, participants: list[dict]) -> tuple[str, str | None, list[str]]:
    remote = [participant for participant in participants if participant["role"] == "participant"]
    phone_numbers = [participant["phone_number"] for participant in remote]
    if row["title"]:
        contact_name = row["title"]
    elif remote:
        names = [participant["display_name"] for participant in remote]
        contact_name = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
    else:
        contact_name = "Unknown"
    phone_number = phone_numbers[0] if len(phone_numbers) == 1 else None
    return contact_name, phone_number, phone_numbers


def _review_dict(row) -> dict | None:
    if not row or row["review_id"] is None:
        return None
    return {
        "id": row["review_id"],
        "through_message_id": row["review_through_message_id"],
        "state": row["review_state"],
        "first_presented_at": row["first_presented_at"],
        "last_presented_at": row["last_presented_at"],
        "defer_until": row["defer_until"],
        "analysis_version": row["analysis_version"],
    }


def _unread_summaries(conn, query: dict[str, list[str]]) -> dict:
    limit = _query_int(query, "limit", 50, maximum=200)
    include_hidden = _query_bool(query, "include_hidden", False)
    only_new = _query_bool(query, "only_new_since_last_review", True)
    raw_personal_number = _query_value(query, "personal_number", "").strip()
    personal_number = normalize_phone(raw_personal_number)
    if raw_personal_number and not personal_number:
        raise ValueError("personal_number must be a valid phone number.")

    clauses = [UNREAD_CONVERSATION_CLAUSE]
    params: list[Any] = []
    if not include_hidden:
        clauses.append("COALESCE(c.is_archived, 0) = 0")
    if personal_number:
        clauses.append(
            """
            EXISTS (
              SELECT 1 FROM conversation_participants self_cp
              WHERE self_cp.conversation_id = c.id
                AND self_cp.role = 'self'
                AND self_cp.phone_number = ?
            )
            """
        )
        params.append(personal_number)
    if only_new:
        clauses.append(
            """
            (
              ar.id IS NULL
              OR EXISTS (
                SELECT 1 FROM messages reopened
                WHERE reopened.conversation_id = c.id
                  AND reopened.direction = 'inbound'
                  AND reopened.id > ar.through_message_id
              )
              OR (
                ar.review_state = 'deferred'
                AND ar.defer_until IS NOT NULL
                AND datetime(ar.defer_until) <= datetime(?)
              )
            )
            """
        )
        params.append(now_est())

    rows = conn.execute(
        f"""
        SELECT c.id AS conversation_id,
          c.title,
          c.kind,
          c.is_archived,
          trigger.id AS latest_unread_message_id,
          trigger.text AS latest_unread_text,
          trigger.occurred_at AS latest_message_at,
          (
            SELECT COUNT(*) FROM messages um
            WHERE um.conversation_id = c.id AND {UNREAD_MESSAGE_CLAUSE}
          ) AS unread_count,
          EXISTS (
            SELECT 1 FROM messages um
            JOIN attachments a ON a.message_id = um.id
            WHERE um.conversation_id = c.id AND {UNREAD_MESSAGE_CLAUSE}
          ) AS has_media,
          EXISTS (
            SELECT 1 FROM messages um
            WHERE um.conversation_id = c.id
              AND {UNREAD_MESSAGE_CLAUSE}
              AND lower(um.message_type) = 'voicemail'
          ) AS has_voicemail,
          EXISTS (
            SELECT 1 FROM scheduled_messages sm
            WHERE sm.conversation_id = c.id
              AND sm.status IN ('queued', 'sending')
          ) AS pending_scheduled_message,
          ar.id AS review_id,
          ar.through_message_id AS review_through_message_id,
          ar.first_presented_at,
          ar.last_presented_at,
          ar.review_state,
          ar.defer_until,
          ar.analysis_version
        FROM conversations c
        JOIN messages trigger ON trigger.id = (
          SELECT um.id FROM messages um
          WHERE um.conversation_id = c.id AND {UNREAD_MESSAGE_CLAUSE}
          ORDER BY um.occurred_at DESC, um.id DESC
          LIMIT 1
        )
        LEFT JOIN assistant_action_reviews ar ON ar.id = (
          SELECT candidate_review.id
          FROM assistant_action_reviews candidate_review
          WHERE candidate_review.conversation_id = c.id
          ORDER BY candidate_review.through_message_id DESC, candidate_review.id DESC
          LIMIT 1
        )
        WHERE {' AND '.join(clauses)}
        ORDER BY trigger.occurred_at DESC, trigger.id DESC
        LIMIT ?
        """,
        (*params, limit + 1),
    ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    participants_by_conversation = _participant_rows(
        conn, [int(row["conversation_id"]) for row in rows]
    )
    conversations = []
    for row in rows:
        participants = participants_by_conversation.get(row["conversation_id"], [])
        contact_name, phone_number, phone_numbers = _summary_contact(row, participants)
        conversations.append(
            {
                "conversation_id": row["conversation_id"],
                "contact_name": contact_name,
                "phone_number": phone_number,
                "phone_numbers": phone_numbers,
                "personal_numbers": [
                    participant["phone_number"]
                    for participant in participants
                    if participant["role"] == "self"
                ],
                "latest_unread_message_id": row["latest_unread_message_id"],
                "latest_unread_text": row["latest_unread_text"],
                "unread_count": int(row["unread_count"]),
                "latest_message_at": row["latest_message_at"],
                "has_media": bool(row["has_media"]),
                "has_voicemail": bool(row["has_voicemail"]),
                "pending_scheduled_message": bool(row["pending_scheduled_message"]),
                "is_hidden": bool(row["is_archived"]),
                "review": _review_dict(row),
            }
        )
    return {"conversations": conversations, "has_more": has_more}


def list_unread_conversations(query: dict[str, list[str]]) -> dict:
    with closing(connect()) as conn:
        return _unread_summaries(conn, query)


def _contact_details(conn, participants: list[dict]) -> list[dict]:
    contact_ids = list(
        dict.fromkeys(
            int(participant["contact_id"])
            for participant in participants
            if participant.get("contact_id") is not None
        )
    )
    phones_by_contact: dict[int, list[dict]] = {contact_id: [] for contact_id in contact_ids}
    emails_by_contact: dict[int, list[dict]] = {contact_id: [] for contact_id in contact_ids}
    if contact_ids:
        placeholders = ",".join("?" for _ in contact_ids)
        for row in conn.execute(
            f"""
            SELECT contact_id, phone_number, label FROM contact_phones
            WHERE contact_id IN ({placeholders})
            ORDER BY contact_id, id
            """,
            contact_ids,
        ).fetchall():
            phones_by_contact[row["contact_id"]].append(
                {"phone_number": row["phone_number"], "label": row["label"]}
            )
        for row in conn.execute(
            f"""
            SELECT contact_id, email, label FROM contact_emails
            WHERE contact_id IN ({placeholders})
            ORDER BY contact_id, id
            """,
            contact_ids,
        ).fetchall():
            emails_by_contact[row["contact_id"]].append(
                {"email": row["email"], "label": row["label"]}
            )

    detailed = []
    for participant in participants:
        contact_id = participant.get("contact_id")
        contact = None
        if contact_id is not None:
            preferred = _preferred_contacts(conn, [participant["phone_number"]]).get(
                participant["phone_number"]
            )
            contact = {
                "id": contact_id,
                "display_name": participant["display_name"],
                "source": (preferred or {}).get("source", "local"),
                "phone_numbers": phones_by_contact.get(contact_id, []),
                "emails": emails_by_contact.get(contact_id, []),
            }
        detailed.append(
            {
                "phone_number": participant["phone_number"],
                "role": participant["role"],
                "display_name": participant["display_name"],
                "contact": contact,
            }
        )
    return detailed


def get_conversation_context(conversation_id: int, query: dict[str, list[str]]) -> dict:
    if conversation_id <= 0:
        raise ValueError("conversation_id must be a positive integer.")
    message_limit = _query_int(query, "message_limit", 20, maximum=100)
    with closing(connect()) as conn:
        conversation = conn.execute(
            """
            SELECT id, title, kind, is_archived, dealt_with_at, manual_unread_at,
              created_at, updated_at, last_message_at
            FROM conversations WHERE id = ?
            """,
            (conversation_id,),
        ).fetchone()
        if not conversation:
            raise LookupError("Conversation not found.")

        participants = _participant_rows(conn, [conversation_id]).get(conversation_id, [])
        detailed_participants = _contact_details(conn, participants)
        message_rows = conn.execute(
            """
            SELECT id, conversation_id, direction, from_number, to_numbers, cc_numbers,
              text, message_type, status, occurred_at, source
            FROM messages
            WHERE conversation_id = ?
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
            """,
            (conversation_id, message_limit + 1),
        ).fetchall()
        has_more = len(message_rows) > message_limit
        message_rows = list(reversed(message_rows[:message_limit]))
        message_ids = [int(row["id"]) for row in message_rows]
        attachments: dict[int, list[dict]] = {message_id: [] for message_id in message_ids}
        if message_ids:
            placeholders = ",".join("?" for _ in message_ids)
            for row in conn.execute(
                f"""
                SELECT id, message_id, content_type, size, filename, source
                FROM attachments
                WHERE message_id IN ({placeholders})
                ORDER BY message_id, id
                """,
                message_ids,
            ).fetchall():
                attachments[row["message_id"]].append(
                    {
                        "id": row["id"],
                        "content_type": row["content_type"],
                        "size": row["size"],
                        "filename": row["filename"],
                        "source": row["source"],
                    }
                )
        messages = []
        for row in message_rows:
            message_attachments = attachments[row["id"]]
            messages.append(
                {
                    "id": row["id"],
                    "direction": row["direction"],
                    "from_number": row["from_number"],
                    "to_numbers": from_json(row["to_numbers"], []),
                    "cc_numbers": from_json(row["cc_numbers"], []),
                    "text": row["text"],
                    "message_type": row["message_type"],
                    "status": row["status"],
                    "occurred_at": row["occurred_at"],
                    "source": row["source"],
                    "has_media": bool(message_attachments),
                    "attachments": message_attachments,
                }
            )

        scheduled_rows = conn.execute(
            """
            SELECT id, from_number, to_numbers, text, media_urls, scheduled_for, status
            FROM scheduled_messages
            WHERE conversation_id = ? AND status IN ('queued', 'sending')
            ORDER BY scheduled_for, id
            """,
            (conversation_id,),
        ).fetchall()
        pending_scheduled_messages = []
        for row in scheduled_rows:
            media_urls = from_json(row["media_urls"], [])
            pending_scheduled_messages.append(
                {
                    "id": row["id"],
                    "from_number": row["from_number"],
                    "to_numbers": from_json(row["to_numbers"], []),
                    "text": row["text"],
                    "has_media": bool(media_urls),
                    "media_count": len(media_urls),
                    "scheduled_for": row["scheduled_for"],
                    "status": row["status"],
                }
            )

        contact_name, phone_number, phone_numbers = _summary_contact(conversation, participants)
        unread = conn.execute(
            f"SELECT {UNREAD_CONVERSATION_CLAUSE} FROM conversations c WHERE c.id = ?",
            (conversation_id,),
        ).fetchone()[0]
        return {
            "conversation": {
                "id": conversation["id"],
                "contact_name": contact_name,
                "phone_number": phone_number,
                "phone_numbers": phone_numbers,
                "kind": conversation["kind"],
                "is_hidden": bool(conversation["is_archived"]),
                "is_unread": bool(unread),
                "last_message_at": conversation["last_message_at"],
                "participants": detailed_participants,
            },
            "messages": messages,
            "has_more": has_more,
            "pending_scheduled_messages": pending_scheduled_messages,
        }


def _normalize_defer_until(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("defer_until is required when state is deferred.")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("defer_until must be an ISO 8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError("defer_until must include a timezone offset.")
    return parsed.astimezone(EASTERN).replace(microsecond=0).isoformat()


def _stored_review_dict(row) -> dict:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "through_message_id": row["through_message_id"],
        "first_presented_at": row["first_presented_at"],
        "last_presented_at": row["last_presented_at"],
        "state": row["review_state"],
        "defer_until": row["defer_until"],
        "analysis_version": row["analysis_version"],
    }


def record_action_review(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    try:
        conversation_id = int(payload.get("conversation_id"))
        through_message_id = int(payload.get("through_message_id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("conversation_id and through_message_id must be integers.") from exc
    if conversation_id <= 0 or through_message_id <= 0:
        raise ValueError("conversation_id and through_message_id must be positive integers.")
    state = str(payload.get("state") or "presented").strip().lower()
    if state not in REVIEW_STATES:
        raise ValueError("state must be presented, dismissed, deferred, or resolved.")
    defer_until = _normalize_defer_until(payload.get("defer_until")) if state == "deferred" else None
    analysis_version = str(payload.get("analysis_version") or "1").strip()
    if not analysis_version or len(analysis_version) > 100:
        raise ValueError("analysis_version must be between 1 and 100 characters.")

    with closing(connect()) as conn:
        message = conn.execute(
            "SELECT conversation_id FROM messages WHERE id = ?",
            (through_message_id,),
        ).fetchone()
        if not message or int(message["conversation_id"]) != conversation_id:
            raise ValueError("through_message_id does not belong to conversation_id.")
        timestamp = now_est()
        conn.execute(
            """
            INSERT INTO assistant_action_reviews(
              conversation_id, through_message_id, first_presented_at,
              last_presented_at, review_state, defer_until, analysis_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id, through_message_id) DO UPDATE SET
              last_presented_at = excluded.last_presented_at,
              review_state = excluded.review_state,
              defer_until = excluded.defer_until,
              analysis_version = excluded.analysis_version
            """,
            (
                conversation_id,
                through_message_id,
                timestamp,
                timestamp,
                state,
                defer_until,
                analysis_version,
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT * FROM assistant_action_reviews
            WHERE conversation_id = ? AND through_message_id = ?
            """,
            (conversation_id, through_message_id),
        ).fetchone()
        return {"action_review": _stored_review_dict(row)}


def list_unresolved_action_reviews(query: dict[str, list[str]]) -> dict:
    limit = _query_int(query, "limit", 100, maximum=200)
    current_time = now_est()
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT ar.*,
              c.title,
              c.kind,
              c.is_archived,
              trigger.text AS through_message_text,
              trigger.occurred_at AS through_message_at,
              EXISTS (
                SELECT 1 FROM messages newer
                WHERE newer.conversation_id = ar.conversation_id
                  AND newer.direction = 'inbound'
                  AND newer.id > ar.through_message_id
              ) AS has_new_messages,
              CASE
                WHEN ar.review_state = 'deferred'
                  AND ar.defer_until IS NOT NULL
                  AND datetime(ar.defer_until) <= datetime(?)
                THEN 1 ELSE 0
              END AS deferral_expired
            FROM assistant_action_reviews ar
            JOIN conversations c ON c.id = ar.conversation_id
            JOIN messages trigger ON trigger.id = ar.through_message_id
            WHERE ar.review_state IN ('presented', 'deferred')
              AND ar.id = (
                SELECT latest_review.id
                FROM assistant_action_reviews latest_review
                WHERE latest_review.conversation_id = ar.conversation_id
                ORDER BY latest_review.through_message_id DESC, latest_review.id DESC
                LIMIT 1
              )
            ORDER BY
              has_new_messages DESC,
              deferral_expired DESC,
              ar.last_presented_at DESC,
              ar.id DESC
            LIMIT ?
            """,
            (current_time, limit),
        ).fetchall()
        participants_by_conversation = _participant_rows(
            conn, [int(row["conversation_id"]) for row in rows]
        )
        action_reviews = []
        for row in rows:
            participants = participants_by_conversation.get(row["conversation_id"], [])
            contact_name, phone_number, phone_numbers = _summary_contact(row, participants)
            review = _stored_review_dict(row)
            review.update(
                {
                    "contact_name": contact_name,
                    "phone_number": phone_number,
                    "phone_numbers": phone_numbers,
                    "through_message_text": row["through_message_text"],
                    "through_message_at": row["through_message_at"],
                    "has_new_messages": bool(row["has_new_messages"]),
                    "deferral_expired": bool(row["deferral_expired"]),
                    "is_hidden": bool(row["is_archived"]),
                }
            )
            action_reviews.append(review)
        return {"action_reviews": action_reviews}
