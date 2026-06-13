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
from .timeutil import now_est


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


def _extension_for_content_type(content_type: str | None) -> str:
    if not content_type:
        return ""
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "application/pdf": ".pdf",
    }.get(content_type.split(";", 1)[0].strip().lower(), "")


def _download_media(url: str, content_type: str | None = None) -> tuple[str | None, int | None, str | None]:
    if not url:
        return None, None, None
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    parsed_name = Path(urlparse(url).path).name
    extension = Path(parsed_name).suffix or _extension_for_content_type(content_type)
    base = parsed_name or hashlib.sha256(url.encode("utf-8")).hexdigest()
    filename = base if base.endswith(extension) else f"{base}{extension}"
    target = config.MEDIA_DIR / filename
    sha = None
    if not target.exists():
        request = urllib.request.Request(url, headers={"User-Agent": "switchboard/0.1"})
        account_sid = _account_sid()
        auth_token = _auth_token()
        if account_sid and auth_token:
            request.add_header("Authorization", _basic_auth_value(account_sid, auth_token))
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = response.read()
        except Exception:
            return None, None, None
        target.write_bytes(payload)
        sha = hashlib.sha256(payload).hexdigest()
    size = target.stat().st_size if target.exists() else None
    if target.exists() and sha is None:
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        sha = digest.hexdigest()
    return f"media/{filename}" if target.exists() else None, size, sha


def _webhook_url(request_url: str) -> str:
    configured = app_settings.get_value("twilio.webhook_url", config.TWILIO_WEBHOOK_URL).strip()
    return configured or request_url


def _form_params(raw_body: bytes) -> dict[str, list[str]]:
    return parse_qs(raw_body.decode("utf-8", errors="replace"), keep_blank_values=True)


def _flat_params(params: dict[str, list[str]]) -> dict[str, str]:
    return {key: values[-1] if values else "" for key, values in params.items()}


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
    return hmac.compare_digest(expected, signature)


def verify_signature(raw_body: bytes, headers: dict[str, str], request_url: str) -> bool:
    auth_token = _auth_token()
    if not auth_token:
        return True
    signature = headers.get("x-twilio-signature", "")
    if not signature:
        return False
    content_type = headers.get("content-type", "").lower()
    if "application/json" in content_type:
        return _verify_json_signature(raw_body, signature, request_url, auth_token)
    return _verify_form_signature(raw_body, signature, request_url, auth_token)


def _message_sid(params: dict[str, str]) -> str:
    for key in ("MessageSid", "SmsSid", "SmsMessageSid", "MessageId"):
        value = str(params.get(key) or "").strip()
        if value:
            return value
    return ""


def _status(params: dict[str, str]) -> str:
    for key in OUTBOUND_STATUS_KEYS:
        value = str(params.get(key) or "").strip().lower()
        if value:
            return value
    return "received"


def _looks_like_inbound_message(params: dict[str, str]) -> bool:
    return bool(params.get("From") and params.get("To") and (params.get("Body") or params.get("NumMedia")))


def _media_items(params: dict[str, str]) -> list[dict[str, str]]:
    try:
        count = int(params.get("NumMedia") or "0")
    except ValueError:
        count = 0
    items = []
    for index in range(max(count, 0)):
        url = params.get(f"MediaUrl{index}") or ""
        if not url:
            continue
        items.append(
            {
                "url": url,
                "content_type": params.get(f"MediaContentType{index}") or "",
            }
        )
    return items


def _find_message_id(conn: sqlite3.Connection, sid: str) -> int | None:
    message_id = message_id_for_provider_ref(conn, "twilio", sid)
    if message_id:
        return message_id
    row = conn.execute("SELECT id FROM messages WHERE telnyx_id = ? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    return int(row["id"]) if row else None


def _update_status_callback(conn: sqlite3.Connection, params: dict[str, str]) -> int | None:
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


def _store_inbound_message(conn: sqlite3.Connection, params: dict[str, str]) -> int | None:
    sid = _message_sid(params)
    from_number = normalize_phone(params.get("From"))
    to_number = normalize_phone(params.get("To"))
    text = params.get("Body") or ""
    media_items = _media_items(params)
    if not from_number or not to_number or (not text and not media_items):
        return None

    existing_id = _find_message_id(conn, sid) if sid else None
    if existing_id:
        conn.execute(
            """
            UPDATE messages
            SET status = 'received', raw_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (as_json({"twilio": params}), now_est(), existing_id),
        )
        return existing_id

    known_self = self_numbers(conn)
    remote_numbers = [from_number] if from_number else []
    self_participants = [to_number] if to_number else []
    if known_self:
        participants = {from_number, to_number}
        remote_numbers = sorted(n for n in participants if n and n not in known_self)
        self_participants = sorted(n for n in participants if n in known_self)
    conversation_id = ensure_conversation(conn, remote_numbers, self_participants)
    message_id = upsert_message(
        conn,
        conversation_id=conversation_id,
        direction="inbound",
        from_number=from_number,
        to_numbers=[to_number],
        cc_numbers=[],
        text=text,
        occurred_at=now_est(),
        message_type="MMS" if media_items else "SMS",
        status="received",
        source="twilio",
        telnyx_id=sid or None,
        telnyx_event_id=None,
        raw_json={"twilio": params},
    )
    add_provider_message_ref(conn, provider="twilio", provider_message_id=sid, message_id=message_id)
    for media in media_items:
        local_path, size, sha = _download_media(media["url"], media.get("content_type"))
        filename = Path(local_path or urlparse(media["url"]).path).name
        add_attachment(
            conn,
            message_id,
            local_path=local_path,
            remote_url=media["url"],
            content_type=media.get("content_type"),
            size=size,
            sha256=sha,
            filename=filename,
            source="twilio",
        )
    conn.commit()
    _notify_incoming_message(
        from_number=from_number,
        sender_name=_contact_name_for_phone(conn, from_number),
        text=text,
        media_count=len(media_items),
    )
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


def handle_webhook(raw_body: bytes, headers: dict[str, str], request_url: str) -> dict[str, Any]:
    if not verify_signature(raw_body, headers, request_url):
        raise TwilioError("Twilio webhook signature verification failed.")
    content_type = headers.get("content-type", "").lower()
    if "application/json" in content_type:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
        params = {str(key): str(value) for key, value in payload.items()}
    else:
        params = _flat_params(_form_params(raw_body))

    conn = connect()
    init_db(conn)
    message_id = _update_status_callback(conn, params)
    if message_id is None:
        message_id = _store_inbound_message(conn, params)
    conn.commit()
    return {"provider": "twilio", "message_id": message_id, "message_sid": _message_sid(params), "status": _status(params)}
