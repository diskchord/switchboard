from __future__ import annotations

import json
from typing import Any

from . import config
from .phone import normalize_phone
from .settings import get_value
from .telnyx import send_message as send_telnyx_message
from .twilio import send_message as send_twilio_message


class MessagingError(RuntimeError):
    pass


PROVIDERS = {"telnyx", "twilio"}


def _parse_provider_map(value: str) -> dict[str, str]:
    value = (value or "").strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise MessagingError("Provider by number must be a JSON object.") from exc
    if not isinstance(parsed, dict):
        raise MessagingError("Provider by number must be a JSON object.")
    mapping: dict[str, str] = {}
    for raw_number, raw_provider in parsed.items():
        number = normalize_phone(str(raw_number))
        provider = str(raw_provider or "").strip().lower()
        if not number:
            continue
        if provider not in PROVIDERS:
            raise MessagingError(f"Unsupported provider for {number}: {provider}")
        mapping[number] = provider
    return mapping


def provider_for_number(from_number: str | None) -> str:
    default_provider = get_value("messaging.provider", config.MESSAGING_PROVIDER).strip().lower() or "telnyx"
    if default_provider not in PROVIDERS:
        raise MessagingError(f"Unsupported messaging provider: {default_provider}")
    raw_mapping = get_value(
        "messaging.provider_by_number",
        json.dumps(config.MESSAGING_PROVIDER_BY_NUMBER, separators=(",", ":")),
    )
    provider_map = _parse_provider_map(raw_mapping)
    number = normalize_phone(from_number)
    return provider_map.get(number, default_provider)


def configured_messaging_providers() -> dict[str, bool]:
    return {
        "telnyx": bool(get_value("telnyx.api_key", config.TELNYX_API_KEY)),
        "twilio": bool(
            get_value("twilio.account_sid", config.TWILIO_ACCOUNT_SID)
            and get_value("twilio.auth_token", config.TWILIO_AUTH_TOKEN)
        ),
    }


def send_message(
    *,
    from_number: str,
    to_numbers: list[str],
    text: str,
    media_urls: list[str] | None = None,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    provider = provider_for_number(from_number)
    if provider == "twilio":
        result = send_twilio_message(
            from_number=from_number,
            to_numbers=to_numbers,
            text=text,
            media_urls=media_urls,
            conversation_id=conversation_id,
        )
    else:
        result = send_telnyx_message(
            from_number=from_number,
            to_numbers=to_numbers,
            text=text,
            media_urls=media_urls,
            conversation_id=conversation_id,
        )
    result["provider"] = provider
    return result
