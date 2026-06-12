from __future__ import annotations

import threading
import time
from typing import Any

from . import config
from . import fastmail, google_contacts


class ContactsError(RuntimeError):
    pass


def active_provider() -> str:
    provider = config.CONTACTS_PROVIDER
    if provider in {"", "auto"}:
        if config.FASTMAIL_CONFIGURED:
            return "fastmail"
        if config.GOOGLE_CONTACTS_CONFIGURED:
            return "google"
        return "none"
    if provider in {"fastmail", "google", "none"}:
        return provider
    raise ContactsError("CONTACTS_PROVIDER must be auto, fastmail, google, or none.")


def configured_providers() -> dict[str, bool]:
    return {
        "fastmail": config.FASTMAIL_CONFIGURED,
        "google": config.GOOGLE_CONTACTS_CONFIGURED,
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
    if not config.CONTACTS_AUTOSYNC or active_provider() == "none":
        return

    def worker() -> None:
        while True:
            try:
                sync_contacts()
            except Exception as exc:
                print(f"Contact sync failed: {exc}", flush=True)
            time.sleep(max(config.CONTACTS_SYNC_INTERVAL_MINUTES, 5) * 60)

    provider = active_provider()
    thread = threading.Thread(target=worker, name=f"{provider}-contacts-sync", daemon=True)
    thread.start()
