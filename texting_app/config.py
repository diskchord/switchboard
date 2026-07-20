from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _strip_env_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'} and (index == 0 or value[index - 1] != "\\"):
            quote = None if quote == char else char if quote is None else quote
        if char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.strip()


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        value = _strip_env_comment(value.strip())
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")

APP_NAME = "Switchboard"
APP_SLUG = "switchboard"
LEGACY_APP_SLUG = "texting-app"


def _default_data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    preferred = base / APP_SLUG
    legacy = base / LEGACY_APP_SLUG
    if not os.environ.get("TEXTING_DATA_DIR") and not os.environ.get("TEXTING_DB") and legacy.exists() and not preferred.exists():
        return legacy
    return preferred


DEFAULT_DATA_DIR = _default_data_dir()


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() not in {"", "0", "false", "no", "off"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _csv_env(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]


def _labels_env(name: str) -> dict[str, str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = {}
        for part in value.split(","):
            phone, separator, label = part.partition("=")
            if separator and phone.strip() and label.strip():
                parsed[phone.strip()] = label.strip()
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(val) for key, val in parsed.items() if str(key).strip() and str(val).strip()}


def _mapping_env(name: str) -> dict[str, str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = {}
        for part in value.split(","):
            key, separator, raw = part.partition("=")
            if separator and key.strip() and raw.strip():
                parsed[key.strip()] = raw.strip()
    if not isinstance(parsed, dict):
        return {}
    return {str(key).strip(): str(val).strip() for key, val in parsed.items() if str(key).strip() and str(val).strip()}


_DB_ENV = os.environ.get("TEXTING_DB", "")
DATA_DIR = Path(os.environ.get("TEXTING_DATA_DIR") or (Path(_DB_ENV).parent if _DB_ENV else DEFAULT_DATA_DIR))
MEDIA_DIR = Path(os.environ.get("TEXTING_MEDIA_DIR", DATA_DIR / "media"))
DB_PATH = Path(_DB_ENV or DATA_DIR / "texting.sqlite")
PUBLIC_UPLOAD_DIR = Path(os.environ.get("TEXTING_PUBLIC_UPLOAD_DIR", DATA_DIR / "public-uploads"))
PUBLIC_UPLOAD_BASE_URL = os.environ.get("TEXTING_PUBLIC_UPLOAD_BASE_URL", "")
UPLOAD_MAX_FILE_MB = _int_env("TEXTING_UPLOAD_MAX_FILE_MB", 25)

HOST = os.environ.get("TEXTING_HOST", "127.0.0.1")
PORT = int(os.environ.get("TEXTING_PORT", "8766"))

AUTH_USERNAME = os.environ.get("TEXTING_AUTH_USERNAME", "").strip()
AUTH_PASSWORD_HASH = os.environ.get("TEXTING_AUTH_PASSWORD_HASH", "").strip()
AUTH_SECRET_KEY = os.environ.get("TEXTING_AUTH_SECRET_KEY", "").strip()
AUTH_SESSION_DAYS = max(_int_env("TEXTING_AUTH_SESSION_DAYS", 14), 1)
AUTH_DISABLED = _bool_env("TEXTING_AUTH_DISABLED", False)
ALLOW_UNSIGNED_PROVIDER_WEBHOOKS = _bool_env("TEXTING_ALLOW_UNSIGNED_PROVIDER_WEBHOOKS", False)
AUTH_TOTP_SECRET = os.environ.get("TEXTING_AUTH_TOTP_SECRET", "").replace(" ", "").strip().upper()
AUTH_TOTP_ISSUER = os.environ.get("TEXTING_AUTH_TOTP_ISSUER", APP_NAME).strip() or APP_NAME
AUTH_BACKUP_CODE_HASHES = _csv_env("TEXTING_AUTH_BACKUP_CODE_HASHES")
# Separate credential for programs using the versioned messaging API. This is
# intentionally not interchangeable with a browser session.
API_TOKEN = os.environ.get("TEXTING_API_TOKEN", "").strip()

EST_OFFSET = "-04:00"
EST_TZ_NAME = "ET"

PERSONAL_NUMBERS = _csv_env("TEXTING_PERSONAL_NUMBERS")
DEFAULT_IDENTITY_LABELS = _labels_env("TEXTING_IDENTITY_LABELS")

IDENTITY_COLORS = [
    "#0f766e",
    "#b45309",
    "#2563eb",
    "#be123c",
    "#4d7c0f",
    "#7c3aed",
    "#0e7490",
    "#a16207",
    "#c2410c",
    "#4338ca",
    "#15803d",
    "#a21caf",
    "#0369a1",
]

TELNYX_API_KEY = os.environ.get("TELNYX_API_KEY", "")
TELNYX_PUBLIC_KEY = os.environ.get("TELNYX_PUBLIC_KEY", "")
TELNYX_API_BASE = os.environ.get("TELNYX_API_BASE", "https://api.telnyx.com/v2")
TELNYX_FAX_CONNECTION_ID = os.environ.get("TELNYX_FAX_CONNECTION_ID", "")

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_API_BASE = os.environ.get("TWILIO_API_BASE", "https://api.twilio.com")
TWILIO_WEBHOOK_URL = os.environ.get("TWILIO_WEBHOOK_URL", "")
TWILIO_STATUS_CALLBACK_URL = os.environ.get("TWILIO_STATUS_CALLBACK_URL", "")

REVAI_ACCESS_TOKEN = os.environ.get("REVAI_ACCESS_TOKEN", "")
REVAI_API_BASE = os.environ.get("REVAI_API_BASE", "https://api.rev.ai/speechtotext/v1")
VOICEMAIL_TRANSCRIPTION_PROVIDER = (
    os.environ.get("TEXTING_VOICEMAIL_TRANSCRIPTION_PROVIDER", "provider").strip().lower() or "provider"
)

MESSAGING_PROVIDER = os.environ.get("TEXTING_MESSAGING_PROVIDER", "telnyx").strip().lower() or "telnyx"
MESSAGING_PROVIDER_BY_NUMBER = _mapping_env("TEXTING_PROVIDER_BY_NUMBER")

UI_LANGUAGE = os.environ.get("TEXTING_UI_LANGUAGE", "auto").strip().lower() or "auto"
UI_THEME_FAMILY = os.environ.get("TEXTING_UI_THEME", "switchboard").strip().lower() or "switchboard"

NTFY_ENDPOINT = os.environ.get("NTFY_ENDPOINT", "")
NTFY_ENABLED = _bool_env("NTFY_ENABLED", bool(NTFY_ENDPOINT)) and bool(NTFY_ENDPOINT)
NATIVE_NOTIFICATIONS_ENABLED = _bool_env("TEXTING_NATIVE_NOTIFICATIONS_ENABLED", False)
NATIVE_NOTIFICATION_INTERVAL_MINUTES = _int_env("TEXTING_NATIVE_NOTIFICATION_INTERVAL_MINUTES", 15)
AUTO_REFRESH_SECONDS = _int_env("TEXTING_AUTO_REFRESH_SECONDS", 5)
SHOW_SUMMARY_STATS = _bool_env("TEXTING_SHOW_SUMMARY_STATS", True)
SHOW_COMPOSER_COUNTER = _bool_env("TEXTING_SHOW_COMPOSER_COUNTER", True)
MEDIA_CACHE_ATTACHMENTS = _bool_env("TEXTING_CACHE_ATTACHMENTS", True)
SEND_SOUND_ENABLED = _bool_env("TEXTING_SEND_SOUND_ENABLED", True)
SEND_SOUND_TONE = os.environ.get("TEXTING_SEND_SOUND_TONE", "ascending").strip().lower() or "ascending"
RECEIVE_SOUND_MODE = os.environ.get("TEXTING_RECEIVE_SOUND", "auto").strip().lower() or "auto"
RECEIVE_SOUND_TONE = os.environ.get("TEXTING_RECEIVE_SOUND_TONE", "chime").strip().lower() or "chime"
SOUND_VOLUME = _int_env("TEXTING_SOUND_VOLUME", 45)

FASTMAIL_API_TOKEN = os.environ.get("FASTMAIL_API_TOKEN", "")
FASTMAIL_USERNAME = os.environ.get("FASTMAIL_USERNAME", "") or os.environ.get("FASTMAIL_EMAIL", "")
FASTMAIL_APP_PASSWORD = os.environ.get("FASTMAIL_APP_PASSWORD", "") or os.environ.get("FASTMAIL_PASSWORD", "")
FASTMAIL_CARDDAV_URL = os.environ.get("FASTMAIL_CARDDAV_URL", "")
FASTMAIL_CARDDAV_USERNAME = os.environ.get("FASTMAIL_CARDDAV_USERNAME", "")
FASTMAIL_ACCOUNT_ID = os.environ.get("FASTMAIL_ACCOUNT_ID", "")
FASTMAIL_CONFIGURED = bool(FASTMAIL_API_TOKEN or (FASTMAIL_USERNAME and FASTMAIL_APP_PASSWORD))
FASTMAIL_AUTOSYNC = _bool_env("FASTMAIL_AUTOSYNC", True)
FASTMAIL_SYNC_INTERVAL_MINUTES = _int_env("FASTMAIL_SYNC_INTERVAL_MINUTES", 360)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
GOOGLE_CONTACTS_ACCESS_TOKEN = os.environ.get("GOOGLE_CONTACTS_ACCESS_TOKEN", "")
GOOGLE_TOKEN_URI = os.environ.get("GOOGLE_TOKEN_URI", "https://oauth2.googleapis.com/token")
GOOGLE_PEOPLE_API_BASE = os.environ.get("GOOGLE_PEOPLE_API_BASE", "https://people.googleapis.com/v1")
GOOGLE_CONTACTS_CONFIGURED = bool(
    GOOGLE_CONTACTS_ACCESS_TOKEN or (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN)
)

CONTACTS_PROVIDER = os.environ.get("CONTACTS_PROVIDER", "auto").strip().lower() or "auto"
CONTACTS_AUTOSYNC = _bool_env("CONTACTS_AUTOSYNC", _bool_env("FASTMAIL_AUTOSYNC", True))
CONTACTS_SYNC_INTERVAL_MINUTES = _int_env("CONTACTS_SYNC_INTERVAL_MINUTES", FASTMAIL_SYNC_INTERVAL_MINUTES)
