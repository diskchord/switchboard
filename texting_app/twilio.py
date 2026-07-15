from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

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
    message_id_for_provider_ref,
    self_numbers,
    upsert_message,
)
from .phone import normalize_phone
from .telnyx import _contact_name_for_phone, _notify_incoming_message
from .timeutil import normalize_iso_timestamp, now_est


class TwilioError(RuntimeError):
    pass


OUTBOUND_STATUS_KEYS = ("MessageStatus", "SmsStatus")


def _twilio_error_response(status_code: int, detail: str) -> str:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("detail") or payload.get("error_description")
        code = payload.get("code")
        if message and code:
            return f"Twilio rejected the message ({status_code}, {code}): {message}"
        if message:
            return f"Twilio rejected the message ({status_code}): {message}"
    return f"Twilio API returned {status_code}: {detail}"


def _account_sid() -> str:
    return app_settings.get_value("twilio.account_sid", config.TWILIO_ACCOUNT_SID).strip()


def _auth_token() -> str:
    return app_settings.get_value("twilio.auth_token", config.TWILIO_AUTH_TOKEN).strip()


def _api_base() -> str:
    return app_settings.get_value("twilio.api_base", config.TWILIO_API_BASE).strip().rstrip("/")


def _basic_auth_value(account_sid: str, auth_token: str) -> str:
    raw = f"{account_sid}:{auth_token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _form_request(url: str, params: list[tuple[str, str]], account_sid: str, auth_token: str) -> dict[str, Any]:
    body = urlencode(params, doseq=True).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": _basic_auth_value(account_sid, auth_token),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TwilioError(_twilio_error_response(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise TwilioError(f"Twilio API request failed: {exc}") from exc


def _webhook_url(request_url: str) -> str:
    configured = app_settings.get_value("twilio.webhook_url", config.TWILIO_WEBHOOK_URL).strip()
    if not configured:
        return request_url
    request_query = urlparse(request_url).query
    if request_query and not urlparse(configured).query:
        return f"{configured}?{request_query}"
    return configured


def _form_params(raw_body: bytes) -> dict[str, list[str]]:
    return parse_qs(raw_body.decode("utf-8", errors="replace"), keep_blank_values=True)


def _flat_params(params: dict[str, list[str]]) -> dict[str, Any]:
    return {key: values[-1] if len(values) == 1 else values for key, values in params.items()}


def _param_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _first_param(params: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key not in params:
            continue
        values = _param_values(params.get(key))
        for value in reversed(values):
            if value is not None:
                return str(value)
    return ""


def _decode_phone_values(value: Any) -> list[str]:
    phones: list[str] = []
    for item in _param_values(value):
        if item is None:
            continue
        if isinstance(item, dict):
            for key in ("phone_number", "phoneNumber", "address", "number", "value"):
                phones.extend(_decode_phone_values(item.get(key)))
            continue
        if not isinstance(item, str):
            item = str(item)
        item = item.strip()
        if not item:
            continue
        if item.startswith("["):
            try:
                phones.extend(_decode_phone_values(json.loads(item)))
                continue
            except json.JSONDecodeError:
                pass
        if "," in item:
            phones.extend(part.strip() for part in item.split(",") if part.strip())
        else:
            phones.append(item)
    return phones


def _phone_list(*values: Any) -> list[str]:
    seen: set[str] = set()
    phones: list[str] = []
    for value in values:
        for phone in _decode_phone_values(value):
            normalized = normalize_phone(phone)
            if normalized and normalized not in seen:
                seen.add(normalized)
                phones.append(normalized)
    return phones


def _verify_form_signature(raw_body: bytes, signature: str, request_url: str, auth_token: str) -> bool:
    signed = _webhook_url(request_url)
    params = _form_params(raw_body)
    for key in sorted(params):
        for value in params[key]:
            signed += key + value
    digest = hmac.new(auth_token.encode("utf-8"), signed.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


def _verify_json_signature(raw_body: bytes, signature: str, request_url: str, auth_token: str) -> bool:
    signed = _webhook_url(request_url)
    query = parse_qs(urlparse(signed).query)
    body_hash = (query.get("bodySHA256") or [""])[-1]
    if body_hash:
        actual = hashlib.sha256(raw_body).hexdigest()
        if not hmac.compare_digest(actual, body_hash):
            return False
    digest = hmac.new(auth_token.encode("utf-8"), signed.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("ascii")
    if hmac.compare_digest(expected, signature):
        return True

    # Twilio Event Streams webhooks validate against the raw body rather than
    # form fields, and older sink examples may not include a bodySHA256 query.
    body_text = raw_body.decode("utf-8", errors="replace")
    body_digest = hmac.new(auth_token.encode("utf-8"), (signed + body_text).encode("utf-8"), hashlib.sha1).digest()
    body_expected = base64.b64encode(body_digest).decode("ascii")
    return hmac.compare_digest(body_expected, signature)


def verify_signature(raw_body: bytes, headers: dict[str, str], request_url: str) -> bool:
    auth_token = _auth_token()
    if not auth_token:
        return app_settings.get_bool(
            "webhooks.allow_unsigned_provider",
            config.ALLOW_UNSIGNED_PROVIDER_WEBHOOKS,
        )
    signature = headers.get("x-twilio-signature", "")
    if not signature:
        return False
    content_type = headers.get("content-type", "").lower()
    if "application/json" in content_type:
        return _verify_json_signature(raw_body, signature, request_url, auth_token)
    return _verify_form_signature(raw_body, signature, request_url, auth_token)


def _message_sid(params: dict[str, Any]) -> str:
    for key in ("MessageSid", "SmsSid", "SmsMessageSid", "MessageId", "messageSid", "sid"):
        value = _first_param(params, key).strip()
        if value:
            return value
    return ""


def _status(params: dict[str, Any]) -> str:
    for key in (*OUTBOUND_STATUS_KEYS, "status"):
        value = _first_param(params, key).strip().lower()
        if value:
            return value
    return "received"


def _recipient_numbers(params: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    exact_keys = {"Recipients", "recipients", "Recipients[]", "recipients[]", "Recipient", "recipient"}
    for key in exact_keys:
        if key in params:
            values.append(params[key])
    for key, value in params.items():
        lowered = key.lower().rstrip("[]0123456789")
        if lowered in {"recipient", "recipients"} and key not in exact_keys:
            values.append(value)
    return _phone_list(*values)


def _to_numbers(params: dict[str, Any]) -> list[str]:
    to_number = normalize_phone(_first_param(params, "To", "to"))
    recipients = _recipient_numbers(params)
    return _phone_list([to_number] if to_number else [], recipients)


def _media_count(params: dict[str, Any]) -> int:
    try:
        return int(_first_param(params, "NumMedia", "numMedia") or "0")
    except ValueError:
        return 0


def _looks_like_inbound_message(params: dict[str, Any]) -> bool:
    return bool(
        _first_param(params, "From", "from")
        and _to_numbers(params)
        and (_first_param(params, "Body", "body") or _media_count(params))
    )


def _media_items(params: dict[str, Any]) -> list[dict[str, str]]:
    count = _media_count(params)
    items = []
    for index in range(max(count, 0)):
        url = _first_param(params, f"MediaUrl{index}", f"mediaUrl{index}")
        if not url:
            continue
        items.append(
            {
                "url": url,
                "content_type": _first_param(params, f"MediaContentType{index}", f"mediaContentType{index}"),
            }
        )
    return items


def _find_message_id(conn: sqlite3.Connection, sid: str) -> int | None:
    message_id = message_id_for_provider_ref(conn, "twilio", sid)
    if message_id:
        return message_id
    row = conn.execute("SELECT id FROM messages WHERE telnyx_id = ? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    return int(row["id"]) if row else None


def _update_status_callback(conn: sqlite3.Connection, params: dict[str, Any]) -> int | None:
    sid = _message_sid(params)
    status = _status(params)
    if not sid or not status:
        return None
    if status == "received" and _looks_like_inbound_message(params):
        return None
    message_id = _find_message_id(conn, sid)
    if not message_id:
        return None
    conn.execute(
        """
        UPDATE messages
        SET status = ?, raw_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, as_json({"twilio_status": params}), now_est(), message_id),
    )
    add_provider_message_ref(conn, provider="twilio", provider_message_id=sid, message_id=message_id)
    return message_id


def _message_timestamp(params: dict[str, Any]) -> str:
    raw = _first_param(params, "Timestamp", "timestamp", "DateCreated", "dateCreated")
    return normalize_iso_timestamp(raw) if raw else now_est()


def _store_inbound_message(conn: sqlite3.Connection, params: dict[str, Any]) -> int | None:
    sid = _message_sid(params)
    from_number = normalize_phone(_first_param(params, "From", "from"))
    to_numbers = _to_numbers(params)
    primary_to = normalize_phone(_first_param(params, "To", "to")) or (to_numbers[0] if to_numbers else "")
    text = _first_param(params, "Body", "body")
    media_items = _media_items(params)
    if not from_number or not to_numbers or (not text and not media_items):
        return None

    existing_id = _find_message_id(conn, sid) if sid else None
    is_new_message = existing_id is None
    if existing_id:
        conn.execute(
            """
            UPDATE messages
            SET status = 'received', raw_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (as_json({"twilio": params}), now_est(), existing_id),
        )
        message_id = existing_id
        conversation_row = conn.execute("SELECT conversation_id FROM messages WHERE id = ?", (message_id,)).fetchone()
        conversation_id = int(conversation_row["conversation_id"])
        remote_numbers = []
        self_participants = []
    else:
        known_self = self_numbers(conn)
        participants = {from_number, *to_numbers}
        remote_numbers = sorted(n for n in participants if n and n != primary_to) or ([from_number] if from_number else [])
        self_participants = [primary_to] if primary_to else []
        if known_self:
            remote_numbers = sorted(n for n in participants if n and n not in known_self)
            self_participants = sorted(n for n in participants if n in known_self)
        conversation_id = ensure_conversation(conn, remote_numbers, self_participants)
        message_id = upsert_message(
            conn,
            conversation_id=conversation_id,
            direction="inbound",
            from_number=from_number,
            to_numbers=to_numbers,
            cc_numbers=[],
            text=text,
            occurred_at=_message_timestamp(params),
            message_type="MMS" if media_items or len(to_numbers) > 1 else "SMS",
            status="received",
            source="twilio",
            telnyx_id=sid or None,
            telnyx_event_id=None,
            raw_json={"twilio": params},
        )
    add_provider_message_ref(conn, provider="twilio", provider_message_id=sid, message_id=message_id)
    for index, media in enumerate(media_items):
        enqueue_remote_attachment(
            conn,
            message_id,
            provider="twilio",
            remote_url=media["url"],
            content_type=media.get("content_type"),
            filename=Path(urlparse(media["url"]).path).name,
            source="twilio",
            dedupe_key=f"twilio:{sid or message_id}:{index}",
        )
    if not is_new_message:
        return message_id

    # Commit the message, remote metadata, and jobs before optional notification
    # and autoreply I/O. The daemon worker can only claim jobs after this commit.
    conn.commit()
    _notify_incoming_message(
        from_number=from_number,
        sender_name=_contact_name_for_phone(conn, from_number),
        text=text,
        media_count=len(media_items),
    )
    try:
        from .autoreply import maybe_send_autoreply

        maybe_send_autoreply(
            conversation_id=conversation_id,
            from_number=from_number,
            self_numbers=self_participants or ([primary_to] if primary_to else to_numbers),
            remote_numbers=remote_numbers,
            trigger_message_id=message_id,
        )
    except Exception as exc:
        print(f"autoreply processing failed for inbound Twilio message {message_id}: {exc}", flush=True)
    return message_id


def send_message(
    *,
    from_number: str,
    to_numbers: list[str],
    text: str,
    media_urls: list[str] | None = None,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    account_sid = _account_sid()
    auth_token = _auth_token()
    api_base = _api_base()
    if not account_sid or not auth_token:
        raise TwilioError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are not configured.")
    from_number = normalize_phone(from_number)
    to_numbers = [normalize_phone(n) for n in to_numbers if normalize_phone(n)]
    media_urls = [u.strip() for u in (media_urls or []) if u.strip()]
    if not from_number or not to_numbers:
        raise TwilioError("A sender and at least one recipient are required.")
    if len(media_urls) > 10:
        raise TwilioError("Twilio supports up to 10 media URLs per message.")
    endpoint = f"{api_base}/2010-04-01/Accounts/{account_sid}/Messages.json"
    status_callback = app_settings.get_value("twilio.status_callback_url", config.TWILIO_STATUS_CALLBACK_URL).strip()
    responses = []
    for to_number in to_numbers:
        params = [("From", from_number), ("To", to_number)]
        if text:
            params.append(("Body", text))
        for url in media_urls:
            params.append(("MediaUrl", url))
        if status_callback:
            params.append(("StatusCallback", status_callback))
        responses.append(_form_request(endpoint, params, account_sid, auth_token))

    conn = connect()
    init_db(conn)
    known_self = self_numbers(conn)
    remote_numbers = sorted(n for n in to_numbers if n not in known_self)
    conversation_id = conversation_id or ensure_conversation(conn, remote_numbers, [from_number])
    first = responses[0] if responses else {}
    first_sid = first.get("sid")
    message_id = upsert_message(
        conn,
        conversation_id=conversation_id,
        direction="outbound",
        from_number=from_number,
        to_numbers=to_numbers,
        cc_numbers=[],
        text=text,
        occurred_at=now_est(),
        message_type="MMS" if media_urls else "SMS",
        status=str(first.get("status") or "queued"),
        source="twilio",
        telnyx_id=first_sid,
        raw_json={"twilio": responses},
    )
    for response in responses:
        add_provider_message_ref(conn, provider="twilio", provider_message_id=response.get("sid"), message_id=message_id)
    for url in media_urls:
        add_attachment(conn, message_id, remote_url=url, filename=Path(urlparse(url).path).name, source="twilio")
    conn.commit()
    return {"message_id": message_id, "twilio": responses}


def _json_event_data(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {}
    data = event.get("data")
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            data = parsed if isinstance(parsed, dict) else data
        except json.JSONDecodeError:
            pass
    if isinstance(data, dict) and (
        str(event.get("type") or "").startswith("com.twilio.messaging.")
        or data.get("messageSid")
        or data.get("MessageSid")
    ):
        return data
    return event


def _params_from_json_event(event: Any) -> dict[str, Any]:
    data = _json_event_data(event)
    params = dict(data)
    aliases = {
        "messageSid": "MessageSid",
        "accountSid": "AccountSid",
        "messagingServiceSid": "MessagingServiceSid",
        "from": "From",
        "to": "To",
        "body": "Body",
        "numMedia": "NumMedia",
        "numSegments": "NumSegments",
        "timestamp": "Timestamp",
        "dateCreated": "DateCreated",
    }
    for source, target in aliases.items():
        if source in data and target not in params:
            params[target] = data[source]
    if "recipients" in data and "Recipients" not in params:
        params["Recipients"] = data["recipients"]
    return params


def _params_from_json_body(raw_body: bytes) -> list[dict[str, Any]]:
    payload = json.loads(raw_body.decode("utf-8") or "{}")
    events = payload if isinstance(payload, list) else [payload]
    return [_params_from_json_event(event) for event in events]


def handle_webhook(raw_body: bytes, headers: dict[str, str], request_url: str) -> dict[str, Any]:
    if not verify_signature(raw_body, headers, request_url):
        if not _auth_token() and not app_settings.get_bool(
            "webhooks.allow_unsigned_provider",
            config.ALLOW_UNSIGNED_PROVIDER_WEBHOOKS,
        ):
            raise TwilioError(
                "Twilio webhook verification requires TWILIO_AUTH_TOKEN. "
                "For isolated local testing only, set TEXTING_ALLOW_UNSIGNED_PROVIDER_WEBHOOKS=1."
            )
        raise TwilioError("Twilio webhook signature verification failed.")
    content_type = headers.get("content-type", "").lower()
    if "application/json" in content_type:
        params_list = _params_from_json_body(raw_body)
    else:
        params_list = [_flat_params(_form_params(raw_body))]

    conn = connect()
    init_db(conn)
    message_ids: list[int] = []
    last_params: dict[str, Any] = {}
    for params in params_list:
        if not params:
            continue
        last_params = params
        message_id = _update_status_callback(conn, params)
        if message_id is None:
            message_id = _store_inbound_message(conn, params)
        if message_id is not None:
            message_ids.append(message_id)
    conn.commit()
    return {
        "provider": "twilio",
        "message_id": message_ids[-1] if message_ids else None,
        "message_ids": message_ids,
        "message_sid": _message_sid(last_params),
        "status": _status(last_params),
    }
