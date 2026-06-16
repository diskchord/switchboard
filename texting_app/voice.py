from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import uuid
from html import escape
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib.parse import parse_qs, quote, urlparse, urlunparse
from urllib import request as urlrequest

from . import config
from . import settings as app_settings
from .db import (
    add_attachment,
    add_provider_message_ref,
    connect,
    ensure_conversation,
    init_db,
    self_numbers,
    upsert_message,
)
from .phone import normalize_phone
from .telnyx import _contact_name_for_phone, _notify_incoming_message
from .timeutil import now_est


DEFAULT_FORWARD_TIMEOUT_SECONDS = 20
DEFAULT_VOICEMAIL_GREETING = "Please leave a message after the beep."
MIN_VOICEMAIL_RECORDING_SECONDS = 2.0
MIN_VOICEMAIL_MAX_LENGTH_SECONDS = 180
VOICEMAIL_MAX_LENGTH_SECONDS = max(300, MIN_VOICEMAIL_MAX_LENGTH_SECONDS)
VOICEMAIL_SILENCE_TIMEOUT_SECONDS = 0
REVAI_SUCCESS_STATUSES = {"transcribed"}
REVAI_FAILURE_STATUSES = {"failed"}
SIP_MAX_LENGTH = 255


class VoiceError(RuntimeError):
    pass


def voice_rule_fields(row: dict[str, Any]) -> dict[str, Any]:
    if "voice_rule_phone_number" in row and not row.get("voice_rule_phone_number"):
        rule = _global_rule(row.get("phone_number") or "")
        return {
            "voice_forwarding_enabled": bool(rule["forwarding_enabled"]),
            "voice_forward_to_number": rule["forward_to_number"],
            "voice_forward_timeout_seconds": rule["forward_timeout_seconds"],
            "voice_voicemail_enabled": bool(rule["voicemail_enabled"]),
            "voice_voicemail_greeting": rule["voicemail_greeting"],
            "voice_voicemail_greeting_media_url": rule["voicemail_greeting_media_url"],
            "voice_uses_global": True,
        }
    return {
        "voice_forwarding_enabled": bool(row.get("voice_forwarding_enabled") or 0),
        "voice_forward_to_number": row.get("voice_forward_to_number") or "",
        "voice_forward_timeout_seconds": int(row.get("voice_forward_timeout_seconds") or DEFAULT_FORWARD_TIMEOUT_SECONDS),
        "voice_voicemail_enabled": bool(row.get("voice_voicemail_enabled") if row.get("voice_voicemail_enabled") is not None else 1),
        "voice_voicemail_greeting": row.get("voice_voicemail_greeting") or DEFAULT_VOICEMAIL_GREETING,
        "voice_voicemail_greeting_media_url": row.get("voice_voicemail_greeting_media_url") or "",
        "voice_uses_global": False,
    }


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_timeout(value: Any) -> int:
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        seconds = DEFAULT_FORWARD_TIMEOUT_SECONDS
    return min(max(seconds, 5), 120)


def _normalize_sip_address(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if any(char.isspace() for char in raw):
        return ""
    lower = raw.lower()
    if not lower.startswith(("sip:", "sips:")):
        if "@" not in raw or "://" in raw:
            return ""
        raw = f"sip:{raw}"
        lower = raw.lower()
    if len(raw) > SIP_MAX_LENGTH:
        return ""
    address = raw.split(":", 1)[1]
    user, separator, host_and_params = address.partition("@")
    host = host_and_params.split(";", 1)[0].split("?", 1)[0]
    if not user or not separator or not host:
        return ""
    return f"{lower.split(':', 1)[0]}:{address}"


def _forward_destination(value: Any) -> str:
    raw = str(value or "").strip()
    return _normalize_sip_address(raw) or normalize_phone(raw)


def _is_sip_destination(value: Any) -> bool:
    return bool(_normalize_sip_address(value))


def _global_rule(phone_number: str = "") -> dict[str, Any]:
    greeting = app_settings.get_value("voice.voicemail_greeting", DEFAULT_VOICEMAIL_GREETING).strip()
    return {
        "phone_number": normalize_phone(phone_number),
        "forwarding_enabled": 1 if app_settings.get_bool("voice.forwarding_enabled", False) else 0,
        "forward_to_number": _forward_destination(app_settings.get_value("voice.forward_to_number", "")),
        "forward_timeout_seconds": _coerce_timeout(app_settings.get_value("voice.forward_timeout_seconds", str(DEFAULT_FORWARD_TIMEOUT_SECONDS))),
        "voicemail_enabled": 1 if app_settings.get_bool("voice.voicemail_enabled", True) else 0,
        "voicemail_greeting": greeting or DEFAULT_VOICEMAIL_GREETING,
        "voicemail_greeting_media_url": app_settings.get_value("voice.voicemail_greeting_media_url", "").strip(),
    }


def update_voice_rule(
    conn,
    *,
    phone_number: str,
    forwarding_enabled: Any,
    forward_to_number: Any,
    forward_timeout_seconds: Any,
    voicemail_enabled: Any,
    voicemail_greeting: Any,
    voicemail_greeting_media_url: Any,
) -> None:
    phone_number = normalize_phone(phone_number)
    if not phone_number:
        raise ValueError("Voice rule needs a sender number.")
    enabled = _coerce_bool(forwarding_enabled)
    forward_to = _forward_destination(forward_to_number)
    timeout = _coerce_timeout(forward_timeout_seconds)
    voicemail = _coerce_bool(voicemail_enabled, True)
    greeting = str(voicemail_greeting or DEFAULT_VOICEMAIL_GREETING).strip() or DEFAULT_VOICEMAIL_GREETING
    greeting_media_url = str(voicemail_greeting_media_url or "").strip()
    if enabled and not forward_to:
        raise ValueError("Forwarding needs a phone number or SIP address.")
    timestamp = now_est()
    conn.execute(
        """
        INSERT INTO voice_rules(
          phone_number, forwarding_enabled, forward_to_number, forward_timeout_seconds,
          voicemail_enabled, voicemail_greeting, voicemail_greeting_media_url, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(phone_number) DO UPDATE SET
          forwarding_enabled = excluded.forwarding_enabled,
          forward_to_number = excluded.forward_to_number,
          forward_timeout_seconds = excluded.forward_timeout_seconds,
          voicemail_enabled = excluded.voicemail_enabled,
          voicemail_greeting = excluded.voicemail_greeting,
          voicemail_greeting_media_url = excluded.voicemail_greeting_media_url,
          updated_at = excluded.updated_at
        """,
        (
            phone_number,
            1 if enabled else 0,
            forward_to,
            timeout,
            1 if voicemail else 0,
            greeting,
            greeting_media_url,
            timestamp,
            timestamp,
        ),
    )


def _default_rule(phone_number: str) -> dict[str, Any]:
    return _global_rule(phone_number)


def _voice_rule(conn, phone_number: str) -> dict[str, Any]:
    phone_number = normalize_phone(phone_number)
    row = conn.execute("SELECT * FROM voice_rules WHERE phone_number = ?", (phone_number,)).fetchone()
    return dict(row) if row else _default_rule(phone_number)


def _request_base(request_url: str) -> str:
    parsed = urlparse(request_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def _callback_url(request_url: str, path: str) -> str:
    base = _request_base(request_url)
    return f"{base}{path}" if base else path


def _voicemail_transcription_provider() -> str:
    return (
        app_settings.get_value("transcription.voicemail_provider", config.VOICEMAIL_TRANSCRIPTION_PROVIDER).strip().lower()
        or "provider"
    )


def _revai_access_token() -> str:
    return app_settings.get_value("revai.access_token", config.REVAI_ACCESS_TOKEN).strip()


def _revai_api_base() -> str:
    return app_settings.get_value("revai.api_base", config.REVAI_API_BASE).strip().rstrip("/")


def _revai_selected() -> bool:
    return _voicemail_transcription_provider() == "revai"


def _revai_enabled() -> bool:
    return _revai_selected() and bool(_revai_access_token())


def _provider_transcription_enabled() -> bool:
    return not _revai_selected()


def _safe_error_text(error: BaseException) -> str:
    text = _redact_sensitive_text(str(error))
    return text[:500] if len(text) > 500 else text


def _redact_sensitive_text(text: str) -> str:
    text = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
    text = re.sub(r"\bBasic\s+[A-Za-z0-9+/=]+", "Basic [redacted]", text)
    text = re.sub(
        r"((?:X-Amz-Signature|X-Amz-Credential|Signature|AccessKeyId)=)[^&\s<]+",
        r"\1[redacted]",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _http_error_detail(error: urlerror.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace").strip()
    except OSError:
        body = ""
    body = _redact_sensitive_text(body)
    detail = f"HTTP {error.code}"
    if body:
        detail = f"{detail}: {body[:500]}"
    return detail


def _revai_request(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, accept: str = "application/json") -> Any:
    token = _revai_access_token()
    if not token:
        raise VoiceError("Rev.ai access token is not configured.")
    base = _revai_api_base()
    if not base:
        raise VoiceError("Rev.ai API base is not configured.")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urlrequest.Request(f"{base}{path}", data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(request, timeout=20) as response:
            body = response.read()
    except urlerror.HTTPError as exc:
        raise VoiceError(f"Rev.ai request failed: {_http_error_detail(exc)}") from exc
    except urlerror.URLError as exc:
        raise VoiceError(f"Rev.ai request failed: {_safe_error_text(exc)}") from exc
    if accept == "text/plain":
        return body.decode("utf-8", errors="replace")
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def _revai_upload_file(path: Path, *, content_type: str, options: dict[str, Any]) -> Any:
    token = _revai_access_token()
    if not token:
        raise VoiceError("Rev.ai access token is not configured.")
    base = _revai_api_base()
    if not base:
        raise VoiceError("Rev.ai API base is not configured.")
    if not path.is_file():
        raise VoiceError("Local voicemail recording is missing.")
    boundary = f"switchboard-{uuid.uuid4().hex}"
    line = b"\r\n"
    options_json = json.dumps(options, separators=(",", ":")).encode("utf-8")
    media = path.read_bytes()
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("ascii"),
            b'Content-Disposition: form-data; name="options"\r\n',
            b"Content-Type: application/json\r\n\r\n",
            options_json,
            line,
            f"--{boundary}\r\n".encode("ascii"),
            f'Content-Disposition: form-data; name="media"; filename="{path.name}"\r\n'.encode("utf-8"),
            f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode("ascii"),
            media,
            line,
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    request = urlrequest.Request(
        f"{base}/jobs",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=90) as response:
            response_body = response.read()
    except urlerror.HTTPError as exc:
        raise VoiceError(f"Rev.ai upload failed: {_http_error_detail(exc)}") from exc
    except urlerror.URLError as exc:
        raise VoiceError(f"Rev.ai upload failed: {_safe_error_text(exc)}") from exc
    if not response_body:
        return {}
    return json.loads(response_body.decode("utf-8"))


def _url_host(url: str) -> str:
    return urlparse(url).hostname.lower() if urlparse(url).hostname else ""


def _signed_download_url(url: str) -> bool:
    keys = {key.lower() for key in parse_qs(urlparse(url).query).keys()}
    if "x-amz-algorithm" in keys or "x-amz-signature" in keys:
        return True
    return "signature" in keys and bool(keys & {"expires", "credential", "x-amz-credential", "x-goog-credential"})


def _same_domain_or_subdomain(host: str, domain: str) -> bool:
    return bool(host and domain and (host == domain or host.endswith(f".{domain}")))


def _telnyx_api_host() -> str:
    base = app_settings.get_value("telnyx.api_base", config.TELNYX_API_BASE).strip()
    return _url_host(base)


def _recording_source_auth_headers(provider: str, download_url: str = "") -> dict[str, str]:
    host = _url_host(download_url) if download_url else ""
    if download_url and _signed_download_url(download_url):
        return {}
    if provider == "telnyx":
        telnyx_host = _telnyx_api_host()
        if host and not (_same_domain_or_subdomain(host, "telnyx.com") or host == telnyx_host):
            return {}
        api_key = app_settings.get_value("telnyx.api_key", config.TELNYX_API_KEY).strip()
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if provider == "twilio":
        if host and not _same_domain_or_subdomain(host, "twilio.com"):
            return {}
        account_sid = app_settings.get_value("twilio.account_sid", config.TWILIO_ACCOUNT_SID).strip()
        auth_token = app_settings.get_value("twilio.auth_token", config.TWILIO_AUTH_TOKEN).strip()
        if account_sid and auth_token:
            token = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {token}"}
    return {}


def _telnyx_recording_download_url(recording_id: str) -> str:
    api_key = app_settings.get_value("telnyx.api_key", config.TELNYX_API_KEY).strip()
    if not recording_id or not api_key:
        return ""
    base = app_settings.get_value("telnyx.api_base", config.TELNYX_API_BASE).strip().rstrip("/")
    if not base:
        return ""
    request = urlrequest.Request(
        f"{base}/recordings/{quote(recording_id, safe='')}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urlrequest.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        raise VoiceError(f"Telnyx recording lookup failed: {_http_error_detail(exc)}") from exc
    except (urlerror.URLError, json.JSONDecodeError) as exc:
        raise VoiceError(f"Telnyx recording lookup failed: {_safe_error_text(exc)}") from exc
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return ""
    download_urls = data.get("download_urls") if isinstance(data.get("download_urls"), dict) else {}
    for value in (
        download_urls.get("mp3"),
        download_urls.get("wav"),
        data.get("media_url"),
        data.get("download_url"),
    ):
        url = str(value or "").strip()
        if url.startswith(("http://", "https://")):
            return url
    return ""


def _recording_download_url(provider: str, recording_id: str, recording_url: str) -> str:
    if provider == "telnyx":
        try:
            download_url = _telnyx_recording_download_url(recording_id)
        except VoiceError as exc:
            print(f"Telnyx recording lookup failed for {recording_id}: {_safe_error_text(exc)}", flush=True)
        else:
            if download_url:
                return download_url
    return recording_url


def _recording_media_file(local_path: str | None) -> Path | None:
    raw = str(local_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path if path.is_file() else None
    candidate = config.MEDIA_DIR / path.name
    return candidate if candidate.is_file() else None


def _media_local_path(path: Path) -> str:
    return f"media/{path.name}"


def _recording_extension(content_type: str, url: str) -> str:
    normalized = content_type.split(";", 1)[0].strip().lower()
    mapped = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/wave": ".wav",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
    }.get(normalized)
    if mapped:
        return mapped
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".mp3", ".wav", ".m4a", ".mp4", ".ogg", ".oga", ".opus"}:
        return suffix
    guessed = mimetypes.guess_extension(normalized) if normalized else ""
    return guessed or ".mp3"


def _recording_stem(provider: str, recording_id: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{provider}-{recording_id}").strip("._-")
    return (stem or f"{provider}-voicemail")[:160]


def _download_recording_to_media(provider: str, recording_id: str, recording_url: str) -> dict[str, Any]:
    download_url = _recording_download_url(provider, recording_id, recording_url)
    if not download_url:
        raise VoiceError("Recording callback did not include a recording URL.")
    headers = {"User-Agent": "switchboard/0.1"}
    if download_url == recording_url:
        headers.update(_recording_source_auth_headers(provider, download_url))
    request = urlrequest.Request(download_url, headers=headers)
    try:
        with urlrequest.urlopen(request, timeout=60) as response:
            payload = response.read()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    except urlerror.HTTPError as exc:
        raise VoiceError(f"Voicemail recording download failed: {_http_error_detail(exc)}") from exc
    except urlerror.URLError as exc:
        raise VoiceError(f"Voicemail recording download failed: {_safe_error_text(exc)}") from exc
    if not payload:
        raise VoiceError("Downloaded voicemail recording was empty.")
    digest = hashlib.sha256(payload).hexdigest()
    extension = _recording_extension(content_type, download_url)
    filename = f"{_recording_stem(provider, recording_id)}-{digest[:12]}{extension}"
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    target = config.MEDIA_DIR / filename
    if not target.exists():
        target.write_bytes(payload)
    stored_content_type = content_type
    if not stored_content_type or stored_content_type == "application/octet-stream":
        stored_content_type = mimetypes.guess_type(filename)[0] or _voicemail_attachment_type(filename)
    return {
        "local_path": _media_local_path(target),
        "path": target,
        "content_type": stored_content_type,
        "size": target.stat().st_size,
        "sha256": digest,
        "filename": filename,
    }


def _local_recording_from_recording(recording: dict[str, Any] | None) -> dict[str, Any]:
    recording = recording or {}
    existing = _recording_media_file(recording.get("local_path"))
    if not existing:
        return {}
    content_type = str(recording.get("content_type") or mimetypes.guess_type(existing.name)[0] or _voicemail_attachment_type(existing.name))
    return {
        "local_path": _media_local_path(existing),
        "path": existing,
        "content_type": content_type,
        "size": recording.get("size") or existing.stat().st_size,
        "sha256": str(recording.get("sha256") or ""),
        "filename": existing.name,
    }


def _ensure_local_recording(
    conn,
    *,
    provider: str,
    recording_id: str,
    recording_url: str,
    recording: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recording = recording or {}
    existing = _local_recording_from_recording(recording)
    if existing:
        return existing
    local = _download_recording_to_media(provider, recording_id, recording_url)
    conn.execute(
        """
        UPDATE voicemail_recordings
        SET local_path = ?,
            content_type = ?,
            size = ?,
            sha256 = ?,
            updated_at = ?
        WHERE provider = ? AND recording_id = ?
        """,
        (
            local["local_path"],
            local["content_type"],
            local["size"],
            local["sha256"],
            now_est(),
            provider,
            recording_id,
        ),
    )
    return local


def _clean_transcript_text(text: str) -> str:
    text = text.strip()
    return re.sub(r"^\s*Channel\s+\d+\s*:\s*", "", text, flags=re.IGNORECASE).strip()


def _submit_revai_job(
    conn,
    *,
    provider: str,
    recording_id: str,
    local_recording: dict[str, Any],
    request_url: str,
) -> dict[str, Any] | None:
    callback_url = _callback_url(request_url, "/api/revai/webhook")
    options = {
        "notification_config": {"url": callback_url},
        "metadata": f"voicemail:{provider}:{recording_id}",
    }
    timestamp = now_est()
    try:
        job = _revai_upload_file(
            Path(local_recording["path"]),
            content_type=str(local_recording.get("content_type") or "application/octet-stream"),
            options=options,
        )
    except VoiceError as exc:
        conn.execute(
            """
            UPDATE voicemail_recordings
            SET transcription_provider = 'revai',
                transcription_status = 'submission_failed',
                failure = ?,
                updated_at = ?
            WHERE provider = ? AND recording_id = ?
            """,
            (_safe_error_text(exc), timestamp, provider, recording_id),
        )
        print(f"Rev.ai voicemail submission failed for {provider}:{recording_id}: {_safe_error_text(exc)}", flush=True)
        return None
    job_id = str(job.get("id") or "")
    status = str(job.get("status") or "submitted").strip().lower() or "submitted"
    conn.execute(
        """
        UPDATE voicemail_recordings
        SET transcription_provider = 'revai',
            transcription_status = ?,
            failure = '',
            revai_job_id = ?,
            updated_at = ?
        WHERE provider = ? AND recording_id = ?
        """,
        (status, job_id, timestamp, provider, recording_id),
    )
    print(
        f"Rev.ai voicemail submission queued for {provider}:{recording_id}: "
        f"job_id={job_id or '(missing)'} status={status}",
        flush=True,
    )
    return job


def _revai_transcript_text(job_id: str) -> str:
    text = _revai_request(f"/jobs/{job_id}/transcript", accept="text/plain")
    return _clean_transcript_text(str(text or ""))


def _voice_params(value: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(val[-1] if isinstance(val, list) else val or "") for key, val in value.items()}


def _parse_body(raw_body: bytes, content_type: str = "") -> dict[str, str]:
    if "application/json" in content_type.lower():
        payload = json.loads(raw_body.decode("utf-8") or "{}")
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("payload"), dict):
                payload = data["payload"]
            return _voice_params(payload)
        return {}
    return _voice_params(parse_qs(raw_body.decode("utf-8", errors="replace"), keep_blank_values=True))


def verify_twilio_voice_request(raw_body: bytes, headers: dict[str, str], request_url: str) -> None:
    from .twilio import verify_signature

    if not verify_signature(raw_body, headers, request_url):
        raise VoiceError("Twilio voice webhook signature verification failed.")


def verify_telnyx_voice_request(raw_body: bytes, headers: dict[str, str]) -> None:
    from .telnyx import verify_signature

    if not verify_signature(raw_body, headers.get("telnyx-signature-ed25519"), headers.get("telnyx-timestamp")):
        raise VoiceError("Telnyx voice webhook signature verification failed.")


def parse_voice_callback(provider: str, raw_body: bytes, headers: dict[str, str], request_url: str) -> dict[str, str]:
    if provider == "twilio":
        verify_twilio_voice_request(raw_body, headers, request_url)
    elif provider == "telnyx":
        verify_telnyx_voice_request(raw_body, headers)
    return _parse_body(raw_body, headers.get("content-type", ""))


def _param(params: dict[str, str], *keys: str) -> str:
    lower = {key.lower(): value for key, value in params.items()}
    for key in keys:
        if key in params and params[key]:
            return str(params[key])
        value = lower.get(key.lower())
        if value:
            return str(value)
    return ""


def _caller_number(params: dict[str, str]) -> str:
    return normalize_phone(_param(params, "From", "Caller", "from", "caller_id_number", "caller_id"))


def _called_number(params: dict[str, str]) -> str:
    return normalize_phone(_param(params, "To", "Called", "to", "called_number"))


def _successful_dial(params: dict[str, str]) -> bool:
    status = _param(params, "DialCallStatus", "dial_call_status").strip().lower()
    return status in {"completed", "answered"}


def _has_dial_result(params: dict[str, str]) -> bool:
    return bool(_param(params, "DialCallStatus", "dial_call_status"))


def _xml(parts: list[str]) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(parts) + "</Response>"


def _say(greeting: str) -> str:
    greeting = greeting.strip() or DEFAULT_VOICEMAIL_GREETING
    return f"<Say>{escape(greeting)}</Say>"


def _voicemail_greeting(rule: dict[str, Any]) -> str:
    media_url = str(rule.get("voicemail_greeting_media_url") or "").strip()
    if media_url:
        return f"<Play>{escape(media_url)}</Play>"
    return _say(str(rule.get("voicemail_greeting") or DEFAULT_VOICEMAIL_GREETING))


def _twilio_record(rule: dict[str, Any], request_url: str) -> str:
    recording_url = _callback_url(request_url, "/api/twilio/voice/recording")
    transcription_url = _callback_url(request_url, "/api/twilio/voice/transcription")
    transcription_attrs = (
        f'transcribe="true" transcribeCallback="{escape(transcription_url)}" '
        if _provider_transcription_enabled()
        else ""
    )
    return _xml(
        [
            _voicemail_greeting(rule),
            (
                f'<Record maxLength="{VOICEMAIL_MAX_LENGTH_SECONDS}" timeout="{VOICEMAIL_SILENCE_TIMEOUT_SECONDS}" '
                f'finishOnKey="#" playBeep="true" '
                f"{transcription_attrs}"
                f'recordingStatusCallback="{escape(recording_url)}" recordingStatusCallbackMethod="POST" '
                f'action="{escape(recording_url)}" method="POST" />'
            ),
            "<Say>Goodbye.</Say>",
            "<Hangup />",
        ]
    )


def _telnyx_record(rule: dict[str, Any], request_url: str) -> str:
    recording_url = _callback_url(request_url, "/api/telnyx/voice/recording")
    transcription_url = _callback_url(request_url, "/api/telnyx/voice/transcription")
    transcription_attrs = (
        f'transcription="true" transcriptionCallback="{escape(transcription_url)}" '
        if _provider_transcription_enabled()
        else ""
    )
    return _xml(
        [
            _voicemail_greeting(rule),
            (
                f'<Record maxLength="{VOICEMAIL_MAX_LENGTH_SECONDS}" timeout="{VOICEMAIL_SILENCE_TIMEOUT_SECONDS}" '
                f'finishOnKey="#" playBeep="true" format="mp3" '
                f"{transcription_attrs}"
                f'recordingStatusCallback="{escape(recording_url)}" recordingStatusCallbackMethod="POST" '
                f'action="{escape(recording_url)}" method="POST" />'
            ),
            "<Say>Goodbye.</Say>",
            "<Hangup />",
        ]
    )


def _dial_xml(provider: str, rule: dict[str, Any], to_number: str, request_url: str) -> str:
    action = _callback_url(request_url, f"/api/{provider}/voice")
    timeout = _coerce_timeout(rule.get("forward_timeout_seconds"))
    forward_to = _forward_destination(rule.get("forward_to_number"))
    caller_id_attr = f' callerId="{escape(to_number)}"' if to_number else ""
    dial_target = (
        f"<Sip>{escape(forward_to)}</Sip>"
        if _is_sip_destination(forward_to)
        else f"<Number>{escape(forward_to)}</Number>"
    )
    return _xml(
        [
            (
                f'<Dial timeout="{timeout}" action="{escape(action)}" method="POST"{caller_id_attr}>'
                f"{dial_target}"
                "</Dial>"
            )
        ]
    )


def voice_xml(provider: str, params: dict[str, str], request_url: str) -> str:
    to_number = _called_number(params)
    conn = connect()
    init_db(conn)
    rule = _voice_rule(conn, to_number)
    if _successful_dial(params):
        return _xml(["<Hangup />"])
    if not _has_dial_result(params) and rule.get("forwarding_enabled") and _forward_destination(rule.get("forward_to_number")):
        return _dial_xml(provider, rule, to_number, request_url)
    if not rule.get("voicemail_enabled", 1):
        return _xml(["<Reject />"])
    if provider == "telnyx":
        return _telnyx_record(rule, request_url)
    return _twilio_record(rule, request_url)


def _recording_url(provider: str, params: dict[str, str]) -> str:
    url = _param(params, "RecordingUrl", "recording_url", "recordingUrl")
    if provider == "twilio" and url and not url.lower().endswith((".mp3", ".wav")):
        return f"{url}.mp3"
    return url


def _recording_duration_seconds(params: dict[str, str]) -> float | None:
    for key in (
        "RecordingDuration",
        "recording_duration",
        "recordingDuration",
        "Duration",
        "duration",
        "duration_seconds",
    ):
        raw = _param(params, key).strip()
        if not raw:
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    for key in ("RecordingDurationMillis", "duration_millis"):
        raw = _param(params, key).strip()
        if not raw:
            continue
        try:
            return float(raw) / 1000
        except ValueError:
            continue
    return None


def _has_transcription_result(params: dict[str, str]) -> bool:
    return bool(
        _param(
            params,
            "TranscriptionSid",
            "transcription_sid",
            "TranscriptionStatus",
            "transcription_status",
            "TranscriptionText",
            "transcription_text",
            "transcript",
            "text",
        )
    )


def _recording_id(provider: str, params: dict[str, str]) -> str:
    return (
        _param(params, "RecordingSid", "recording_sid", "recording_id", "RecordingId")
        or _param(params, "CallSid", "call_sid", "call_control_id")
        or _param(params, "TranscriptionSid", "transcription_sid")
        or f"{provider}:{now_est()}"
    )


def _transcription_text(params: dict[str, str]) -> str:
    return _clean_transcript_text(_param(params, "TranscriptionText", "transcription_text", "text", "transcript"))


def _transcription_status(params: dict[str, str]) -> str:
    return _param(params, "TranscriptionStatus", "transcription_status", "status").strip().lower()


def _voicemail_text(params: dict[str, str]) -> str:
    transcription = _transcription_text(params).strip()
    status = _transcription_status(params)
    if transcription:
        return transcription
    if status == "failed":
        return "No transcript available."
    return "Transcription pending."


def _upsert_voicemail_recording(conn, provider: str, recording_id: str, params: dict[str, str]) -> dict[str, Any]:
    timestamp = now_est()
    duration = _recording_duration_seconds(params)
    conn.execute(
        """
        INSERT INTO voicemail_recordings(
          provider, recording_id, from_number, to_number, recording_url,
          duration_seconds, transcription_status, transcript_text, raw_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, recording_id) DO UPDATE SET
          from_number = COALESCE(NULLIF(excluded.from_number, ''), voicemail_recordings.from_number),
          to_number = COALESCE(NULLIF(excluded.to_number, ''), voicemail_recordings.to_number),
          recording_url = COALESCE(NULLIF(excluded.recording_url, ''), voicemail_recordings.recording_url),
          duration_seconds = COALESCE(excluded.duration_seconds, voicemail_recordings.duration_seconds),
          transcription_status = COALESCE(NULLIF(excluded.transcription_status, ''), voicemail_recordings.transcription_status),
          transcript_text = COALESCE(NULLIF(excluded.transcript_text, ''), voicemail_recordings.transcript_text),
          raw_json = excluded.raw_json,
          updated_at = excluded.updated_at
        """,
        (
            provider,
            recording_id,
            _caller_number(params),
            _called_number(params),
            _recording_url(provider, params),
            duration,
            _transcription_status(params),
            _transcription_text(params),
            json.dumps({provider: params}, ensure_ascii=False),
            timestamp,
            timestamp,
        ),
    )
    row = conn.execute(
        "SELECT * FROM voicemail_recordings WHERE provider = ? AND recording_id = ?",
        (provider, recording_id),
    ).fetchone()
    return dict(row) if row else {}


def _recording_is_meaningful(duration: float | None) -> bool:
    return duration is not None and duration >= MIN_VOICEMAIL_RECORDING_SECONDS


def _recording_may_be_meaningful(duration: float | None) -> bool:
    return duration is None or _recording_is_meaningful(duration)


def _recording_can_be_downloaded(provider: str, recording_id: str, recording_url: str) -> bool:
    return bool(recording_url or (provider == "telnyx" and recording_id))


def _voicemail_attachment_type(recording_url: str) -> str:
    url = recording_url.lower()
    if url.endswith(".wav"):
        return "audio/wav"
    if url.endswith(".m4a") or url.endswith(".mp4"):
        return "audio/mp4"
    if url.endswith(".ogg") or url.endswith(".oga") or url.endswith(".opus"):
        return "audio/ogg"
    return "audio/mpeg"


def _store_voicemail_message(
    conn,
    *,
    provider: str,
    recording_id: str,
    from_number: str,
    to_number: str,
    text: str,
    recording_url: str,
    local_recording: dict[str, Any] | None = None,
    raw_json: dict[str, Any],
    event_id: str | None = None,
) -> dict[str, Any]:
    text = text.strip()
    if not text or not from_number or not to_number:
        return {"provider": provider, "message_id": None, "recording_id": recording_id, "stored": False}

    import_source_id = f"voicemail:{provider}:{recording_id}"
    known_self = self_numbers(conn)
    participants = {from_number, to_number}
    remote_numbers = sorted(n for n in participants if n and n not in known_self)
    self_participants = sorted(n for n in participants if n in known_self) or [to_number]
    conversation_id = ensure_conversation(conn, remote_numbers or [from_number], self_participants)
    existing = conn.execute("SELECT text FROM messages WHERE import_source_id = ?", (import_source_id,)).fetchone()
    should_notify = not existing or str(existing["text"] or "") != text
    message_id = upsert_message(
        conn,
        conversation_id=conversation_id,
        direction="inbound",
        from_number=from_number,
        to_numbers=[to_number],
        cc_numbers=[],
        text=text,
        occurred_at=now_est(),
        message_type="Voicemail",
        status="received",
        source=f"{provider}-voicemail",
        telnyx_id=import_source_id,
        telnyx_event_id=event_id,
        import_source_id=import_source_id,
        raw_json=raw_json,
    )
    add_provider_message_ref(conn, provider=f"{provider}-voice", provider_message_id=recording_id, message_id=message_id)
    local_recording = local_recording or {}
    local_path = str(local_recording.get("local_path") or "")
    has_recording = bool(local_path or recording_url)
    if has_recording:
        content_type = str(
            local_recording.get("content_type")
            or _voicemail_attachment_type(local_path or recording_url)
        )
        filename = str(local_recording.get("filename") or "")
        if not filename:
            suffix = Path(local_path or recording_url).suffix or ".mp3"
            filename = f"{recording_id}{suffix}"
        lookup_column = "local_path" if local_path else "remote_url"
        lookup_value = local_path or recording_url
        exists = conn.execute(
            f"SELECT 1 FROM attachments WHERE message_id = ? AND {lookup_column} = ?",
            (message_id, lookup_value),
        ).fetchone()
        if not exists:
            add_attachment(
                conn,
                message_id,
                local_path=local_path or None,
                remote_url=recording_url if not local_path else None,
                content_type=content_type,
                size=local_recording.get("size"),
                sha256=str(local_recording.get("sha256") or "") or None,
                filename=filename,
                source=f"{provider}-voicemail",
            )
    return {
        "provider": provider,
        "message_id": message_id,
        "recording_id": recording_id,
        "stored": True,
        "should_notify": should_notify,
        "notify_text": text,
        "notify_media_count": 1 if has_recording else 0,
    }


def _notify_voicemail(conn, result: dict[str, Any], from_number: str) -> None:
    if not result.get("should_notify"):
        return
    _notify_incoming_message(
        from_number=from_number,
        sender_name=_contact_name_for_phone(conn, from_number),
        text=str(result.get("notify_text") or ""),
        media_count=int(result.get("notify_media_count") or 0),
    )


def store_voicemail_callback(
    provider: str,
    params: dict[str, str],
    callback_kind: str = "",
    request_url: str = "",
) -> dict[str, Any]:
    revai_selected = _revai_selected()
    from_number = _caller_number(params)
    to_number = _called_number(params)
    recording_id = _recording_id(provider, params)

    conn = connect()
    init_db(conn)
    recording = _upsert_voicemail_recording(conn, provider, recording_id, params)
    duration = _recording_duration_seconds(params)
    if duration is None:
        duration = recording.get("duration_seconds")
    recording_url = _recording_url(provider, params) or str(recording.get("recording_url") or "")
    can_download_recording = _recording_can_be_downloaded(provider, recording_id, recording_url)
    local_recording: dict[str, Any] = {}
    if can_download_recording and callback_kind != "transcription" and _recording_may_be_meaningful(duration):
        try:
            local_recording = _ensure_local_recording(
                conn,
                provider=provider,
                recording_id=recording_id,
                recording_url=recording_url,
                recording=recording,
            )
            recording.update(local_recording)
        except VoiceError as exc:
            failure = _safe_error_text(exc)
            conn.execute(
                """
                UPDATE voicemail_recordings
                SET transcription_provider = ?,
                    transcription_status = 'recording_download_failed',
                    failure = ?,
                    updated_at = ?
                WHERE provider = ? AND recording_id = ?
                """,
                ("revai" if revai_selected else "", failure, now_est(), provider, recording_id),
            )
            print(f"Voicemail recording download failed for {provider}:{recording_id}: {failure}", flush=True)

    if (
        revai_selected
        and callback_kind != "transcription"
        and not _has_transcription_result(params)
    ):
        if can_download_recording and local_recording and _recording_may_be_meaningful(duration):
            conn.commit()
            job = _submit_revai_job(
                conn,
                provider=provider,
                recording_id=recording_id,
                local_recording=local_recording,
                request_url=request_url,
            )
            conn.commit()
            if not job:
                from_number = from_number or normalize_phone(recording.get("from_number"))
                to_number = to_number or normalize_phone(recording.get("to_number"))
                if from_number and to_number and _recording_is_meaningful(duration):
                    result = _store_voicemail_message(
                        conn,
                        provider=provider,
                        recording_id=recording_id,
                        from_number=from_number,
                        to_number=to_number,
                        text="No transcript available.",
                        recording_url=recording_url,
                        local_recording=local_recording,
                        raw_json={provider: params},
                        event_id=_param(params, "RecordingSid", "recording_id") or None,
                    )
                    conn.commit()
                    _notify_voicemail(conn, result, from_number)
                    result.update({"transcription_provider": "revai", "revai_submission_failed": True})
                    return {
                        key: value
                        for key, value in result.items()
                        if not key.startswith("notify_") and key != "should_notify"
                    }
            return {
                "provider": provider,
                "message_id": None,
                "recording_id": recording_id,
                "stored": False,
                "pending": True,
                "transcription_provider": "revai",
                "revai_job_id": str((job or {}).get("id") or ""),
                "submission_failed": not bool(job),
            }
        skipped_empty = can_download_recording and not _recording_may_be_meaningful(duration)
        if can_download_recording:
            failure = (
                "Recording was too short for voicemail transcription."
                if skipped_empty
                else "Recording could not be downloaded for voicemail transcription."
            )
        else:
            failure = "Recording callback did not include a recording URL."
        print(f"Rev.ai voicemail submission skipped for {provider}:{recording_id}: {failure}", flush=True)
        conn.execute(
            """
            UPDATE voicemail_recordings
            SET transcription_provider = 'revai',
                transcription_status = 'submission_skipped',
                failure = ?,
                updated_at = ?
            WHERE provider = ? AND recording_id = ?
            """,
            (failure, now_est(), provider, recording_id),
        )
        conn.commit()
        return {
            "provider": provider,
            "message_id": None,
            "recording_id": recording_id,
            "stored": False,
            "empty": skipped_empty,
            "transcription_provider": "revai",
            "failure": failure,
        }

    if callback_kind == "transcription" and (recording.get("revai_job_id") or revai_selected):
        conn.commit()
        return {
            "provider": provider,
            "message_id": None,
            "recording_id": recording_id,
            "stored": False,
            "pending": True,
            "transcription_provider": "revai",
        }

    if not _has_transcription_result(params) and callback_kind != "transcription":
        conn.commit()
        return {"provider": provider, "message_id": None, "recording_id": recording_id, "stored": False, "pending": True}

    from_number = from_number or normalize_phone(recording.get("from_number"))
    to_number = to_number or normalize_phone(recording.get("to_number"))
    transcription = _transcription_text(params).strip()
    status = _transcription_status(params)
    if not transcription and status != "failed":
        conn.commit()
        return {"provider": provider, "message_id": None, "recording_id": recording_id, "stored": False, "empty": True}
    if status == "failed" and not _recording_is_meaningful(duration):
        conn.commit()
        return {"provider": provider, "message_id": None, "recording_id": recording_id, "stored": False, "empty": True}
    if not from_number or not to_number:
        conn.commit()
        return {"provider": provider, "message_id": None, "recording_id": recording_id, "stored": False}

    text = _voicemail_text(params)
    message_local_recording = local_recording or _local_recording_from_recording(recording)
    if not message_local_recording and recording_url:
        try:
            message_local_recording = _ensure_local_recording(
                conn,
                provider=provider,
                recording_id=recording_id,
                recording_url=recording_url,
                recording=recording,
            )
        except VoiceError as exc:
            print(f"Voicemail recording download failed for {provider}:{recording_id}: {_safe_error_text(exc)}", flush=True)
    result = _store_voicemail_message(
        conn,
        provider=provider,
        recording_id=recording_id,
        from_number=from_number,
        to_number=to_number,
        text=text,
        recording_url=recording_url,
        local_recording=message_local_recording,
        raw_json={provider: params},
        event_id=_param(params, "TranscriptionSid", "RecordingSid", "recording_id") or None,
    )
    conn.commit()
    _notify_voicemail(conn, result, from_number)
    return {key: value for key, value in result.items() if not key.startswith("notify_") and key != "should_notify"}


def _revai_job_payload(raw_body: bytes) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise VoiceError("Rev.ai webhook payload must be JSON.") from exc
    if not isinstance(payload, dict):
        raise VoiceError("Rev.ai webhook payload must be an object.")
    job = payload.get("job") if isinstance(payload.get("job"), dict) else payload
    job_id = str(job.get("id") or payload.get("job_id") or "").strip()
    status = str(job.get("status") or payload.get("status") or "").strip().lower()
    return payload, job, job_id, status


def store_revai_callback(raw_body: bytes) -> dict[str, Any]:
    payload, job, job_id, status = _revai_job_payload(raw_body)
    if not job_id:
        return {"provider": "revai", "stored": False, "unknown_job": True}

    conn = connect()
    init_db(conn)
    row = conn.execute(
        """
        SELECT *
        FROM voicemail_recordings
        WHERE revai_job_id = ?
        LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    if not row:
        conn.commit()
        return {"provider": "revai", "stored": False, "unknown_job": True, "revai_job_id": job_id}

    recording = dict(row)
    provider = str(recording.get("provider") or "")
    recording_id = str(recording.get("recording_id") or "")
    from_number = normalize_phone(recording.get("from_number") or "")
    to_number = normalize_phone(recording.get("to_number") or "")
    recording_url = str(recording.get("recording_url") or "")
    duration = recording.get("duration_seconds")
    timestamp = now_est()
    local_recording = _local_recording_from_recording(recording)
    if not local_recording and recording_url and _recording_may_be_meaningful(duration):
        try:
            local_recording = _ensure_local_recording(
                conn,
                provider=provider,
                recording_id=recording_id,
                recording_url=recording_url,
                recording=recording,
            )
        except VoiceError as exc:
            print(f"Voicemail recording download failed for {provider}:{recording_id}: {_safe_error_text(exc)}", flush=True)

    if status in REVAI_SUCCESS_STATUSES:
        try:
            transcript = _revai_transcript_text(job_id)
        except VoiceError as exc:
            conn.execute(
                """
                UPDATE voicemail_recordings
                SET transcription_provider = 'revai',
                    transcription_status = 'transcript_fetch_failed',
                    failure = ?,
                    updated_at = ?
                WHERE revai_job_id = ?
                """,
                (_safe_error_text(exc), timestamp, job_id),
            )
            conn.commit()
            print(f"Rev.ai transcript fetch failed for job {job_id}: {_safe_error_text(exc)}", flush=True)
            return {"provider": "revai", "stored": False, "pending": True, "revai_job_id": job_id}
        conn.execute(
            """
            UPDATE voicemail_recordings
            SET transcription_provider = 'revai',
                transcription_status = ?,
                transcript_text = ?,
                failure = '',
                updated_at = ?
            WHERE revai_job_id = ?
            """,
            (status, transcript, timestamp, job_id),
        )
        if not transcript:
            if not _recording_is_meaningful(duration):
                conn.commit()
                return {"provider": "revai", "stored": False, "empty": True, "revai_job_id": job_id}
            result = _store_voicemail_message(
                conn,
                provider=provider,
                recording_id=recording_id,
                from_number=from_number,
                to_number=to_number,
                text="No transcript available.",
                recording_url=recording_url,
                local_recording=local_recording,
                raw_json={"revai": payload, provider: json.loads(recording.get("raw_json") or "{}")},
                event_id=f"revai:{job_id}",
            )
            conn.commit()
            _notify_voicemail(conn, result, from_number)
            result.update({"transcription_provider": "revai", "revai_job_id": job_id, "empty_transcript": True})
            return {key: value for key, value in result.items() if not key.startswith("notify_") and key != "should_notify"}
        result = _store_voicemail_message(
            conn,
            provider=provider,
            recording_id=recording_id,
            from_number=from_number,
            to_number=to_number,
            text=transcript,
            recording_url=recording_url,
            local_recording=local_recording,
            raw_json={"revai": payload, provider: json.loads(recording.get("raw_json") or "{}")},
            event_id=f"revai:{job_id}",
        )
        conn.commit()
        _notify_voicemail(conn, result, from_number)
        result.update({"transcription_provider": "revai", "revai_job_id": job_id})
        return {key: value for key, value in result.items() if not key.startswith("notify_") and key != "should_notify"}

    failure = str(job.get("failure") or job.get("failure_detail") or payload.get("failure") or "")
    conn.execute(
        """
        UPDATE voicemail_recordings
        SET transcription_provider = 'revai',
            transcription_status = ?,
            failure = ?,
            updated_at = ?
        WHERE revai_job_id = ?
        """,
        (status or "unknown", failure[:500], timestamp, job_id),
    )
    if status in REVAI_FAILURE_STATUSES:
        if not _recording_is_meaningful(duration):
            conn.commit()
            return {"provider": "revai", "stored": False, "empty": True, "revai_job_id": job_id}
        result = _store_voicemail_message(
            conn,
            provider=provider,
            recording_id=recording_id,
            from_number=from_number,
            to_number=to_number,
            text="No transcript available.",
            recording_url=recording_url,
            local_recording=local_recording,
            raw_json={"revai": payload, provider: json.loads(recording.get("raw_json") or "{}")},
            event_id=f"revai:{job_id}",
        )
        conn.commit()
        _notify_voicemail(conn, result, from_number)
        result.update({"transcription_provider": "revai", "revai_job_id": job_id})
        return {key: value for key, value in result.items() if not key.startswith("notify_") and key != "should_notify"}

    conn.commit()
    return {"provider": "revai", "stored": False, "pending": True, "revai_job_id": job_id, "status": status}
