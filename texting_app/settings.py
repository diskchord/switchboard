from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from . import config
from .db import connect, init_db
from .timeutil import now_est


@dataclass(frozen=True)
class SettingDef:
    key: str
    label: str
    section: str
    kind: str
    default: str
    secret: bool = False
    help: str = ""
    options: tuple[tuple[str, str], ...] = ()
    env_names: tuple[str, ...] = ()


SETTING_DEFS: tuple[SettingDef, ...] = (
    SettingDef(
        "behavior.mark_read_on_open",
        "Mark thread read when opened",
        "Behavior",
        "bool",
        "0",
        help="Automatically marks a conversation read after opening it.",
    ),
    SettingDef(
        "behavior.details_collapsed_default",
        "Hide side panel by default",
        "Behavior",
        "bool",
        "1",
        help="Applies when this browser has no saved panel preference.",
    ),
    SettingDef(
        "notifications.ntfy_enabled",
        "Send ntfy notifications",
        "Notifications",
        "bool",
        "1" if config.NTFY_ENABLED else "0",
        env_names=("NTFY_ENABLED", "NTFY_ENDPOINT"),
    ),
    SettingDef(
        "notifications.ntfy_endpoint",
        "ntfy endpoint",
        "Notifications",
        "url",
        config.NTFY_ENDPOINT,
        help="Example: https://ntfy.example.com/texts",
        env_names=("NTFY_ENDPOINT",),
    ),
    SettingDef(
        "contacts.provider",
        "Contact provider",
        "Contacts",
        "select",
        config.CONTACTS_PROVIDER,
        options=(("auto", "Auto"), ("fastmail", "Fastmail"), ("google", "Google"), ("none", "None")),
        env_names=("CONTACTS_PROVIDER",),
    ),
    SettingDef(
        "contacts.autosync",
        "Auto-sync contacts",
        "Contacts",
        "bool",
        "1" if config.CONTACTS_AUTOSYNC else "0",
        env_names=("CONTACTS_AUTOSYNC", "FASTMAIL_AUTOSYNC"),
    ),
    SettingDef(
        "contacts.sync_interval_minutes",
        "Sync interval minutes",
        "Contacts",
        "number",
        str(config.CONTACTS_SYNC_INTERVAL_MINUTES),
        env_names=("CONTACTS_SYNC_INTERVAL_MINUTES", "FASTMAIL_SYNC_INTERVAL_MINUTES"),
    ),
    SettingDef("telnyx.api_base", "Telnyx API base", "Telnyx", "url", config.TELNYX_API_BASE, env_names=("TELNYX_API_BASE",)),
    SettingDef("telnyx.api_key", "Telnyx API key", "Telnyx", "secret", config.TELNYX_API_KEY, secret=True, env_names=("TELNYX_API_KEY",)),
    SettingDef("telnyx.public_key", "Telnyx public key", "Telnyx", "secret", config.TELNYX_PUBLIC_KEY, secret=True, env_names=("TELNYX_PUBLIC_KEY",)),
    SettingDef("fastmail.username", "Fastmail username", "Fastmail", "text", config.FASTMAIL_USERNAME, env_names=("FASTMAIL_USERNAME", "FASTMAIL_EMAIL")),
    SettingDef(
        "fastmail.app_password",
        "Fastmail app password",
        "Fastmail",
        "secret",
        config.FASTMAIL_APP_PASSWORD,
        secret=True,
        env_names=("FASTMAIL_APP_PASSWORD", "FASTMAIL_PASSWORD"),
    ),
    SettingDef("fastmail.carddav_url", "Fastmail CardDAV URL", "Fastmail", "url", config.FASTMAIL_CARDDAV_URL, env_names=("FASTMAIL_CARDDAV_URL",)),
    SettingDef("fastmail.carddav_username", "Fastmail CardDAV username", "Fastmail", "text", config.FASTMAIL_CARDDAV_USERNAME, env_names=("FASTMAIL_CARDDAV_USERNAME",)),
    SettingDef("fastmail.api_token", "Fastmail JMAP API token", "Fastmail", "secret", config.FASTMAIL_API_TOKEN, secret=True, env_names=("FASTMAIL_API_TOKEN",)),
    SettingDef("fastmail.account_id", "Fastmail account ID", "Fastmail", "text", config.FASTMAIL_ACCOUNT_ID, env_names=("FASTMAIL_ACCOUNT_ID",)),
    SettingDef("google.client_id", "Google client ID", "Google", "text", config.GOOGLE_CLIENT_ID, env_names=("GOOGLE_CLIENT_ID",)),
    SettingDef("google.client_secret", "Google client secret", "Google", "secret", config.GOOGLE_CLIENT_SECRET, secret=True, env_names=("GOOGLE_CLIENT_SECRET",)),
    SettingDef("google.refresh_token", "Google refresh token", "Google", "secret", config.GOOGLE_REFRESH_TOKEN, secret=True, env_names=("GOOGLE_REFRESH_TOKEN",)),
    SettingDef(
        "google.contacts_access_token",
        "Google temporary access token",
        "Google",
        "secret",
        config.GOOGLE_CONTACTS_ACCESS_TOKEN,
        secret=True,
        help="Useful for testing; refresh-token credentials are better for normal use.",
        env_names=("GOOGLE_CONTACTS_ACCESS_TOKEN",),
    ),
)

SETTINGS_BY_KEY = {definition.key: definition for definition in SETTING_DEFS}
SECRET_KEYS = {definition.key for definition in SETTING_DEFS if definition.secret}


class SettingsError(ValueError):
    pass


def _stored_values() -> dict[str, str]:
    conn = connect()
    init_db(conn)
    return {
        row["key"]: row["value"]
        for row in conn.execute("SELECT key, value FROM app_settings").fetchall()
    }


def _coerce_value(definition: SettingDef, value: Any) -> str:
    if definition.kind == "bool":
        if isinstance(value, bool):
            return "1" if value else "0"
        return "1" if str(value).strip().lower() in {"1", "true", "yes", "on"} else "0"
    if definition.kind == "number":
        try:
            number = int(str(value).strip())
        except ValueError as exc:
            raise SettingsError(f"{definition.label} must be a number.") from exc
        if number < 0:
            raise SettingsError(f"{definition.label} must be zero or greater.")
        return str(number)
    if definition.kind == "select":
        allowed = {option[0] for option in definition.options}
        value = str(value).strip().lower()
        if value not in allowed:
            raise SettingsError(f"{definition.label} must be one of: {', '.join(sorted(allowed))}.")
        return value
    return str(value or "").strip()


def get_value(key: str, default: str | None = None) -> str:
    definition = SETTINGS_BY_KEY.get(key)
    fallback = definition.default if definition else (default or "")
    conn = connect()
    init_db(conn)
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row:
        return str(row["value"])
    return fallback


def get_bool(key: str, default: bool = False) -> bool:
    value = get_value(key, "1" if default else "0")
    return value.lower() in {"1", "true", "yes", "on"}


def get_int(key: str, default: int) -> int:
    try:
        return int(get_value(key, str(default)))
    except ValueError:
        return default


def has_value(key: str) -> bool:
    return bool(get_value(key, ""))


def configured_values() -> dict[str, Any]:
    stored = _stored_values()
    sections: dict[str, list[dict[str, Any]]] = {}
    for definition in SETTING_DEFS:
        value = stored.get(definition.key, definition.default)
        source = "saved" if definition.key in stored else (
            "env" if any(os.environ.get(name) is not None for name in definition.env_names) else "default"
        )
        field = {
            "key": definition.key,
            "label": definition.label,
            "type": definition.kind,
            "secret": definition.secret,
            "value": None if definition.secret else value,
            "has_value": bool(value),
            "source": source,
            "help": definition.help,
            "options": [{"value": value, "label": label} for value, label in definition.options],
        }
        sections.setdefault(definition.section, []).append(field)
    return {"sections": [{"name": name, "fields": fields} for name, fields in sections.items()]}


def update_values(payload: dict[str, Any]) -> dict[str, Any]:
    updates = payload.get("settings") or {}
    clear = set(payload.get("clear") or [])
    if not isinstance(updates, dict):
        raise SettingsError("Settings payload must be an object.")
    timestamp = now_est()
    conn = connect()
    init_db(conn)
    for key in clear:
        if key not in SETTINGS_BY_KEY:
            raise SettingsError(f"Unknown setting: {key}")
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    for key, raw_value in updates.items():
        definition = SETTINGS_BY_KEY.get(key)
        if not definition:
            raise SettingsError(f"Unknown setting: {key}")
        if definition.secret and not str(raw_value or "").strip():
            continue
        value = _coerce_value(definition, raw_value)
        conn.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, timestamp),
        )
    conn.commit()
    return configured_values()
