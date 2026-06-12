from __future__ import annotations

import threading
import time
from typing import Any

from . import config
from . import fastmail, google_contacts
from . import settings as app_settings


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
