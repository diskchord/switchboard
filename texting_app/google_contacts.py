from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import config
from . import settings as app_settings
from .fastmail import import_cards


CONTACTS_READONLY_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"
CONNECTION_PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations"


class GoogleContactsError(RuntimeError):
    pass


def _google_contacts_access_token() -> str:
    return app_settings.get_value("google.contacts_access_token", config.GOOGLE_CONTACTS_ACCESS_TOKEN)


def _google_client_id() -> str:
    return app_settings.get_value("google.client_id", config.GOOGLE_CLIENT_ID)


def _google_client_secret() -> str:
    return app_settings.get_value("google.client_secret", config.GOOGLE_CLIENT_SECRET)


def _google_refresh_token() -> str:
    return app_settings.get_value("google.refresh_token", config.GOOGLE_REFRESH_TOKEN)


def _request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GoogleContactsError(f"Google Contacts returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GoogleContactsError(f"Google Contacts request failed: {exc}") from exc


def _access_token() -> str:
    access_token = _google_contacts_access_token()
    if access_token:
        return access_token
    client_id = _google_client_id()
    client_secret = _google_client_secret()
    refresh_token = _google_refresh_token()
    if not (client_id and client_secret and refresh_token):
        raise GoogleContactsError(
            "Google Contacts is not configured. Set GOOGLE_CONTACTS_ACCESS_TOKEN, or "
            "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN."
        )
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    response = _request_json(
        config.GOOGLE_TOKEN_URI,
        method="POST",
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise GoogleContactsError("Google token refresh did not return an access token.")
    return token


def _label(item: dict[str, Any]) -> str:
    for key in ("formattedType", "type"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _display_name(person: dict[str, Any]) -> str:
    names = person.get("names")
    if isinstance(names, list):
        for name in names:
            if not isinstance(name, dict):
                continue
            display = name.get("displayName") or name.get("unstructuredName")
            if isinstance(display, str) and display.strip():
                return display.strip()
            parts = [name.get("givenName"), name.get("familyName")]
            joined = " ".join(str(part).strip() for part in parts if isinstance(part, str) and part.strip())
            if joined:
                return joined
    organizations = person.get("organizations")
    if isinstance(organizations, list):
        for organization in organizations:
            if isinstance(organization, dict) and isinstance(organization.get("name"), str) and organization["name"].strip():
                return organization["name"].strip()
    return "Unnamed Contact"


def _google_card(person: dict[str, Any]) -> dict[str, Any]:
    card: dict[str, Any] = {
        "id": person.get("resourceName") or person.get("etag") or "",
        "fullName": _display_name(person),
        "phones": [],
        "emails": [],
        "google": person,
    }
    phone_numbers = person.get("phoneNumbers")
    if isinstance(phone_numbers, list):
        for phone in phone_numbers:
            if not isinstance(phone, dict):
                continue
            value = phone.get("canonicalForm") or phone.get("value")
            if isinstance(value, str) and value.strip():
                card["phones"].append({"number": value.strip(), "label": _label(phone)})
    email_addresses = person.get("emailAddresses")
    if isinstance(email_addresses, list):
        for email in email_addresses:
            if not isinstance(email, dict):
                continue
            value = email.get("value")
            if isinstance(value, str) and value.strip():
                card["emails"].append({"email": value.strip(), "label": _label(email)})
    organizations = person.get("organizations")
    if isinstance(organizations, list) and organizations:
        card["organizations"] = {
            str(index): organization
            for index, organization in enumerate(organizations)
            if isinstance(organization, dict)
        }
    return card


def _request_connections() -> list[dict[str, Any]]:
    token = _access_token()
    cards: list[dict[str, Any]] = []
    page_token = ""
    while True:
        query = {
            "pageSize": "1000",
            "personFields": CONNECTION_PERSON_FIELDS,
        }
        if page_token:
            query["pageToken"] = page_token
        url = f"{config.GOOGLE_PEOPLE_API_BASE}/people/me/connections?{urllib.parse.urlencode(query)}"
        response = _request_json(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        for person in response.get("connections") or []:
            if isinstance(person, dict):
                cards.append(_google_card(person))
        page_token = response.get("nextPageToken") or ""
        if not page_token:
            return cards


def sync_contacts() -> dict[str, int]:
    return import_cards(_request_connections(), "google")
