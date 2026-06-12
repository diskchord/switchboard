from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote, urljoin, urlparse
from xml.etree import ElementTree

from . import config
from . import settings as app_settings
from .db import as_json, connect, init_db
from .phone import normalize_phone
from .timeutil import now_est


CONTACTS_CAPABILITY = "urn:ietf:params:jmap:contacts"
CORE_CAPABILITY = "urn:ietf:params:jmap:core"
CARDDAV_NS = "{urn:ietf:params:xml:ns:carddav}"
DAV_NS = "{DAV:}"


class FastmailError(RuntimeError):
    pass


def _fastmail_api_token() -> str:
    return app_settings.get_value("fastmail.api_token", config.FASTMAIL_API_TOKEN)


def _fastmail_username_value() -> str:
    return app_settings.get_value("fastmail.username", config.FASTMAIL_USERNAME)


def _fastmail_app_password() -> str:
    return app_settings.get_value("fastmail.app_password", config.FASTMAIL_APP_PASSWORD)


def _fastmail_carddav_url() -> str:
    return app_settings.get_value("fastmail.carddav_url", config.FASTMAIL_CARDDAV_URL)


def _fastmail_carddav_username_value() -> str:
    return app_settings.get_value("fastmail.carddav_username", config.FASTMAIL_CARDDAV_USERNAME)


def _fastmail_account_id() -> str:
    return app_settings.get_value("fastmail.account_id", config.FASTMAIL_ACCOUNT_ID)


def _request_json(url: str, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if payload is not None else "GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FastmailError(f"Fastmail returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise FastmailError(f"Fastmail request failed: {exc}") from exc


def _basic_auth(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _carddav_collection_url() -> str:
    carddav_url = _fastmail_carddav_url()
    if carddav_url:
        return carddav_url.rstrip("/")
    username = _fastmail_username_value()
    if not username:
        raise FastmailError("FASTMAIL_USERNAME is not configured.")
    return f"https://carddav.fastmail.com/dav/addressbooks/user/{quote(username, safe='@')}/Default"


def _carddav_username() -> str:
    carddav_username = _fastmail_carddav_username_value()
    if carddav_username:
        return carddav_username
    username = _fastmail_username_value()
    if "@" not in username:
        return username
    local, domain = username.split("@", 1)
    return f"{local}+Default@{domain}"


def _request_carddav_cards() -> list[dict[str, Any]]:
    app_password = _fastmail_app_password()
    if not _fastmail_username_value() or not app_password:
        raise FastmailError("FASTMAIL_USERNAME and FASTMAIL_APP_PASSWORD are required for Fastmail CardDAV sync.")
    body = b"""<?xml version="1.0" encoding="utf-8"?>
<C:addressbook-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">
  <D:prop>
    <D:getetag/>
    <C:address-data/>
  </D:prop>
</C:addressbook-query>
"""
    request = urllib.request.Request(
        _carddav_collection_url(),
        data=body,
        method="REPORT",
        headers={
            "Authorization": _basic_auth(_carddav_username(), app_password),
            "Content-Type": "application/xml; charset=utf-8",
            "Accept": "application/xml,text/xml",
            "Depth": "1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            xml_body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FastmailError(f"Fastmail CardDAV returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise FastmailError(f"Fastmail CardDAV request failed: {exc}") from exc

    root = ElementTree.fromstring(xml_body)
    cards: list[dict[str, Any]] = []
    responses = list(root.iter(f"{DAV_NS}response"))
    if responses:
        for response in responses:
            href = response.findtext(f"{DAV_NS}href") or ""
            etag = response.findtext(f".//{DAV_NS}getetag") or ""
            for element in response.iter(f"{CARDDAV_NS}address-data"):
                if element.text:
                    card = _parse_vcard(element.text)
                    if card:
                        card["href"] = href
                        card["etag"] = etag
                        cards.append(card)
    else:
        for element in root.iter(f"{CARDDAV_NS}address-data"):
            if element.text:
                card = _parse_vcard(element.text)
                if card:
                    cards.append(card)
    return cards


def _unfold_vcard_lines(vcard: str) -> list[str]:
    lines: list[str] = []
    for raw in vcard.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _vcard_unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def _vcard_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _name_components(display_name: str) -> tuple[str, str]:
    parts = display_name.split()
    if len(parts) <= 1:
        return parts[0] if parts else display_name, ""
    return parts[-1], " ".join(parts[:-1])


def _n_value(display_name: str) -> str:
    family, given = _name_components(display_name)
    return f"{_vcard_escape(family)};{_vcard_escape(given)};;;"


def _vcard_label(params: list[str]) -> str:
    labels: list[str] = []
    for param in params:
        key, _, value = param.partition("=")
        if key.upper() == "TYPE":
            labels.extend(part.strip().lower() for part in value.split(",") if part.strip())
        elif not value and param.strip():
            labels.append(param.strip().lower())
    return ", ".join(dict.fromkeys(labels))


def _parse_vcard(vcard: str) -> dict[str, Any] | None:
    card: dict[str, Any] = {"phones": [], "emails": []}
    for line in _unfold_vcard_lines(vcard):
        if ":" not in line:
            continue
        name_params, value = line.split(":", 1)
        parts = name_params.split(";")
        name = parts[0].upper()
        params = parts[1:]
        value = _vcard_unescape(value)
        if name == "UID":
            card["id"] = value
        elif name == "FN" and value:
            card["fullName"] = value
        elif name == "N" and value and not card.get("fullName"):
            names = [_vcard_unescape(part) for part in value.split(";") if part]
            if names:
                card["fullName"] = " ".join(reversed(names[:2])).strip()
        elif name == "ORG" and value and not card.get("fullName"):
            card["organizations"] = {"primary": {"name": value.replace(";", " ")}}
        elif name == "TEL" and value:
            card["phones"].append({"number": value.removeprefix("tel:"), "label": _vcard_label(params)})
        elif name == "EMAIL" and value:
            card["emails"].append({"email": value, "label": _vcard_label(params)})
    if not card.get("id"):
        card["id"] = hashlib.sha256(vcard.encode("utf-8")).hexdigest()
    return card if card.get("fullName") or card["phones"] or card["emails"] else None


def _choose_account(session: dict[str, Any]) -> str:
    account_id = _fastmail_account_id()
    if account_id:
        return account_id
    for account_id, account in (session.get("accounts") or {}).items():
        if CONTACTS_CAPABILITY in (account.get("accountCapabilities") or {}):
            return account_id
    raise FastmailError("No Fastmail account with JMAP contacts capability was found.")


def _label_from_contexts(item: dict[str, Any]) -> str:
    label = item.get("label")
    if isinstance(label, str) and label:
        return label
    contexts = item.get("contexts")
    if isinstance(contexts, dict) and contexts:
        return ", ".join(contexts.keys())
    if isinstance(contexts, list) and contexts:
        return ", ".join(str(x) for x in contexts)
    return ""


def _extract_name(card: dict[str, Any]) -> str:
    for key in ("fullName", "name"):
        value = card.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    name = card.get("name")
    if isinstance(name, dict):
        for key in ("fullName", "name"):
            value = name.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        components = name.get("components")
        if isinstance(components, list):
            parts = []
            for component in components:
                if isinstance(component, dict):
                    value = component.get("value") or component.get("name")
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
            if parts:
                return " ".join(parts)
    orgs = card.get("organizations")
    if isinstance(orgs, dict):
        for org in orgs.values():
            if isinstance(org, dict) and isinstance(org.get("name"), str) and org["name"].strip():
                return org["name"].strip()
    return "Unnamed Contact"


def _iter_contact_values(card: dict[str, Any], *keys: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for key in keys:
        value = card.get(key)
        values = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
        for item in values:
            if isinstance(item, str):
                results.append((item, ""))
            elif isinstance(item, dict):
                label = _label_from_contexts(item)
                for field in ("number", "phone", "uri", "email", "address", "value"):
                    raw = item.get(field)
                    if isinstance(raw, str) and raw.strip():
                        results.append((raw.strip(), label))
    return results


def _phones_from_card(card: dict[str, Any]) -> list[tuple[str, str]]:
    phones: list[tuple[str, str]] = []
    for raw, label in _iter_contact_values(card, "phones", "phoneNumbers", "telephoneNumbers"):
        number = normalize_phone(raw)
        if number:
            phones.append((number, label))
    raw_json = json.dumps(card)
    for tel in re.findall(r"tel:[^\"\\s,}]+", raw_json):
        number = normalize_phone(tel)
        if number:
            phones.append((number, ""))
    dedup: dict[str, str] = {}
    for number, label in phones:
        dedup.setdefault(number, label)
    return list(dedup.items())


def _emails_from_card(card: dict[str, Any]) -> list[tuple[str, str]]:
    emails: list[tuple[str, str]] = []
    for raw, label in _iter_contact_values(card, "emails", "emailAddresses"):
        if "@" in raw:
            emails.append((raw.lower(), label))
    dedup: dict[str, str] = {}
    for email, label in emails:
        dedup.setdefault(email, label)
    return list(dedup.items())


def _carddav_url_for_href(href: str) -> str:
    if href.startswith(("http://", "https://")):
        return href
    collection_url = _carddav_collection_url()
    if href.startswith("/"):
        parsed = urlparse(collection_url)
        return urljoin(f"{parsed.scheme}://{parsed.netloc}", href)
    return urljoin(collection_url.rstrip("/") + "/", href)


def _carddav_href_for_uid(uid: str) -> str:
    return f"{quote(uid, safe='')}.vcf"


def _carddav_headers(content_type: str | None = None) -> dict[str, str]:
    app_password = _fastmail_app_password()
    if not _fastmail_username_value() or not app_password:
        raise FastmailError("FASTMAIL_USERNAME and FASTMAIL_APP_PASSWORD are required to save Fastmail contacts.")
    headers = {
        "Authorization": _basic_auth(_carddav_username(), app_password),
        "Accept": "text/vcard,text/plain,*/*",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _get_carddav_vcard(href: str) -> str:
    request = urllib.request.Request(
        _carddav_url_for_href(href),
        method="GET",
        headers=_carddav_headers(),
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FastmailError(f"Fastmail CardDAV returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise FastmailError(f"Fastmail CardDAV request failed: {exc}") from exc


def _put_carddav_vcard(vcard: str, *, href: str | None = None, uid: str) -> str:
    target_href = href or _carddav_href_for_uid(uid)
    request = urllib.request.Request(
        _carddav_url_for_href(target_href),
        data=vcard.encode("utf-8"),
        method="PUT",
        headers=_carddav_headers("text/vcard; charset=utf-8"),
    )
    try:
        with urllib.request.urlopen(request, timeout=45):
            return target_href
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FastmailError(f"Fastmail CardDAV returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise FastmailError(f"Fastmail CardDAV request failed: {exc}") from exc


def _uid_for_phone(phone: str) -> str:
    digest = hashlib.sha256(phone.encode("utf-8")).hexdigest()[:24]
    return f"texting-app-{digest}"


def _build_vcard(uid: str, display_name: str, phones: list[tuple[str, str]], emails: list[tuple[str, str]] | None = None) -> str:
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        "PRODID:-//Texting App//Contacts//EN",
        f"UID:{_vcard_escape(uid)}",
        f"FN:{_vcard_escape(display_name)}",
        f"N:{_n_value(display_name)}",
    ]
    seen_phones: set[str] = set()
    for phone, _label in phones:
        normalized = normalize_phone(phone)
        if normalized and normalized not in seen_phones:
            lines.append(f"TEL;TYPE=CELL:{_vcard_escape(normalized)}")
            seen_phones.add(normalized)
    seen_emails: set[str] = set()
    for email, _label in emails or []:
        email = email.strip().lower()
        if "@" in email and email not in seen_emails:
            lines.append(f"EMAIL;TYPE=INTERNET:{_vcard_escape(email)}")
            seen_emails.add(email)
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def _rename_vcard(vcard: str, *, uid: str, display_name: str, phone: str) -> str:
    parsed = _parse_vcard(vcard) or {}
    known_phones = {number for number, _label in _phones_from_card(parsed)}
    lines = _unfold_vcard_lines(vcard)
    if not lines:
        return _build_vcard(uid, display_name, [(phone, "mobile")])

    next_lines: list[str] = []
    saw_uid = False
    saw_fn = False
    saw_n = False
    saw_end = False
    for line in lines:
        if ":" not in line:
            next_lines.append(line)
            continue
        name = line.split(":", 1)[0].split(";", 1)[0].upper()
        if name == "UID":
            if not saw_uid:
                next_lines.append(f"UID:{_vcard_escape(uid)}")
                saw_uid = True
            continue
        if name == "FN":
            if not saw_fn:
                next_lines.append(f"FN:{_vcard_escape(display_name)}")
                saw_fn = True
            continue
        if name == "N":
            if not saw_n:
                next_lines.append(f"N:{_n_value(display_name)}")
                saw_n = True
            continue
        if name == "END":
            if not saw_uid:
                next_lines.append(f"UID:{_vcard_escape(uid)}")
            if not saw_fn:
                next_lines.append(f"FN:{_vcard_escape(display_name)}")
            if not saw_n:
                next_lines.append(f"N:{_n_value(display_name)}")
            if phone not in known_phones:
                next_lines.append(f"TEL;TYPE=CELL:{_vcard_escape(phone)}")
            next_lines.append("END:VCARD")
            saw_end = True
            continue
        next_lines.append(line)

    if not saw_end:
        if not saw_uid:
            next_lines.append(f"UID:{_vcard_escape(uid)}")
        if not saw_fn:
            next_lines.append(f"FN:{_vcard_escape(display_name)}")
        if not saw_n:
            next_lines.append(f"N:{_n_value(display_name)}")
        if phone not in known_phones:
            next_lines.append(f"TEL;TYPE=CELL:{_vcard_escape(phone)}")
        next_lines.append("END:VCARD")
    return "\r\n".join(next_lines) + "\r\n"


def _json_card(raw_json: str | None) -> dict[str, Any]:
    if not raw_json:
        return {}
    try:
        card = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    return card if isinstance(card, dict) else {}


def _contact_row_for_phone(conn, phone: str):
    return conn.execute(
        """
        SELECT c.*
        FROM contact_phones cp
        JOIN contacts c ON c.id = cp.contact_id
        WHERE cp.phone_number = ?
        ORDER BY c.source IN ('fastmail', 'google') DESC, c.updated_at DESC, c.id DESC
        LIMIT 1
        """,
        (phone,),
    ).fetchone()


def _find_carddav_card(phone: str, uid: str | None = None) -> dict[str, Any] | None:
    cards = _request_carddav_cards()
    if uid:
        for card in cards:
            if str(card.get("id") or card.get("uid") or "") == uid:
                return card
    for card in cards:
        if phone in {number for number, _label in _phones_from_card(card)}:
            return card
    return None


def _upsert_named_contact(conn, *, phone: str, display_name: str, card: dict[str, Any], synced: bool) -> int:
    timestamp = now_est()
    row = _contact_row_for_phone(conn, phone)
    source = "fastmail" if synced else (row["source"] if row else "phone")
    fastmail_id = str(card.get("id") or card.get("uid") or "") if synced else (row["fastmail_id"] if row else None)
    fastmail_row = (
        conn.execute("SELECT id FROM contacts WHERE fastmail_id = ?", (fastmail_id,)).fetchone()
        if fastmail_id
        else None
    )
    if fastmail_row:
        contact_id = int(fastmail_row["id"])
    elif row:
        contact_id = int(row["id"])
    else:
        cur = conn.execute(
            """
            INSERT INTO contacts(display_name, fastmail_id, source, raw_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (display_name, fastmail_id, source, as_json(card), timestamp, timestamp),
        )
        contact_id = int(cur.lastrowid)

    conn.execute(
        """
        UPDATE contacts
        SET display_name = ?, fastmail_id = ?, source = ?, raw_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (display_name, fastmail_id, source, as_json(card), timestamp, contact_id),
    )
    conn.execute("DELETE FROM contact_phones WHERE contact_id = ?", (contact_id,))
    conn.execute("DELETE FROM contact_emails WHERE contact_id = ?", (contact_id,))

    phones = _phones_from_card(card)
    if phone not in {number for number, _label in phones}:
        phones.append((phone, "mobile"))
    for number, label in phones:
        conn.execute(
            "INSERT OR IGNORE INTO contact_phones(contact_id, phone_number, label) VALUES (?, ?, ?)",
            (contact_id, number, label or "mobile"),
        )
    for email, label in _emails_from_card(card):
        conn.execute(
            "INSERT OR IGNORE INTO contact_emails(contact_id, email, label) VALUES (?, ?, ?)",
            (contact_id, email, label or "email"),
        )

    if row and int(row["id"]) != contact_id and row["source"] != "fastmail":
        conn.execute("DELETE FROM contacts WHERE id = ?", (int(row["id"]),))
    conn.execute(
        """
        UPDATE conversation_participants
        SET contact_id = ?
        WHERE phone_number = ? AND role = 'participant'
        """,
        (contact_id, phone),
    )
    return contact_id


def save_contact_name(phone_number: str, display_name: str) -> dict[str, Any]:
    phone = normalize_phone(phone_number)
    display_name = re.sub(r"\s+", " ", str(display_name or "")).strip()
    if not phone:
        raise ValueError("A valid phone number is required.")
    if not display_name:
        raise ValueError("Contact name is required.")
    if len(display_name) > 140:
        raise ValueError("Contact name is too long.")

    conn = connect()
    init_db(conn)
    row = _contact_row_for_phone(conn, phone)
    card = _json_card(row["raw_json"] if row else None)
    uid = str(card.get("id") or card.get("uid") or (row["fastmail_id"] if row else "") or _uid_for_phone(phone))
    href = str(card.get("href") or "")
    synced = False

    if _fastmail_username_value() and _fastmail_app_password():
        if row and row["source"] == "fastmail" and not href:
            matched = _find_carddav_card(phone, uid)
            if matched:
                card = matched
                uid = str(card.get("id") or card.get("uid") or uid)
                href = str(card.get("href") or "")
        source_vcard = _get_carddav_vcard(href) if href else ""
        if source_vcard:
            vcard = _rename_vcard(source_vcard, uid=uid, display_name=display_name, phone=phone)
        else:
            phones = _phones_from_card(card)
            if phone not in {number for number, _label in phones}:
                phones.append((phone, "mobile"))
            vcard = _build_vcard(uid, display_name, phones, _emails_from_card(card))
        href = _put_carddav_vcard(vcard, href=href or None, uid=uid)
        card = _parse_vcard(vcard) or {"phones": [{"number": phone, "label": "mobile"}], "emails": []}
        card["id"] = uid
        card["href"] = href
        synced = True
    else:
        card["id"] = uid
        card["fullName"] = display_name
        phones = card.get("phones")
        if not isinstance(phones, list):
            card["phones"] = []
        if phone not in {number for number, _label in _phones_from_card(card)}:
            card["phones"].append({"number": phone, "label": "mobile"})

    contact_id = _upsert_named_contact(conn, phone=phone, display_name=display_name, card=card, synced=synced)
    relinked = _relink_participants_to_synced_contacts(conn) if synced else 0
    conn.commit()
    contact = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    return {
        "contact": {
            "id": contact_id,
            "display_name": contact["display_name"] if contact else display_name,
            "phone_number": phone,
            "source": contact["source"] if contact else ("fastmail" if synced else "phone"),
        },
        "synced": synced,
        "participants": relinked,
    }


def sync_contacts() -> dict[str, int]:
    if _fastmail_username_value() and _fastmail_app_password():
        cards = _request_carddav_cards()
    else:
        cards = _request_jmap_cards()
    return import_cards(cards, "fastmail")


def _request_jmap_cards() -> list[dict[str, Any]]:
    api_token = _fastmail_api_token()
    if not api_token:
        raise FastmailError("Fastmail is not configured. Set FASTMAIL_USERNAME and FASTMAIL_APP_PASSWORD, or FASTMAIL_API_TOKEN.")
    session = _request_json("https://api.fastmail.com/jmap/session", api_token)
    account_id = _choose_account(session)
    api_url = session.get("apiUrl")
    if not api_url:
        raise FastmailError("Fastmail JMAP session did not include apiUrl.")
    payload = {
        "using": [CORE_CAPABILITY, CONTACTS_CAPABILITY],
        "methodCalls": [
            ["ContactCard/get", {"accountId": account_id}, "contacts"],
        ],
    }
    response = _request_json(api_url, api_token, payload)
    cards: list[dict[str, Any]] = []
    for method_name, args, _tag in response.get("methodResponses", []):
        if method_name == "ContactCard/get":
            cards = args.get("list") or []
    return cards


def import_cards(cards: list[dict[str, Any]], source: str) -> dict[str, int]:
    return _import_cards(cards, source)


def _import_cards(cards: list[dict[str, Any]], source: str) -> dict[str, int]:
    conn = connect()
    init_db(conn)
    imported = 0
    phone_count = 0
    email_count = 0
    timestamp = now_est()
    for card in cards:
        fastmail_id = str(card.get("id") or card.get("uid") or "")
        display_name = _extract_name(card)
        raw = as_json(card)
        row = conn.execute("SELECT id FROM contacts WHERE fastmail_id = ?", (fastmail_id,)).fetchone()
        if row:
            contact_id = int(row["id"])
            conn.execute(
                "UPDATE contacts SET display_name = ?, raw_json = ?, source = ?, updated_at = ? WHERE id = ?",
                (display_name, raw, source, timestamp, contact_id),
            )
            conn.execute("DELETE FROM contact_phones WHERE contact_id = ?", (contact_id,))
            conn.execute("DELETE FROM contact_emails WHERE contact_id = ?", (contact_id,))
        else:
            cur = conn.execute(
                """
                INSERT INTO contacts(display_name, fastmail_id, source, raw_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (display_name, fastmail_id, source, raw, timestamp, timestamp),
            )
            contact_id = int(cur.lastrowid)
        for number, label in _phones_from_card(card):
            conn.execute(
                "INSERT OR IGNORE INTO contact_phones(contact_id, phone_number, label) VALUES (?, ?, ?)",
                (contact_id, number, label or "mobile"),
            )
            phone_count += 1
        for email, label in _emails_from_card(card):
            conn.execute(
                "INSERT OR IGNORE INTO contact_emails(contact_id, email, label) VALUES (?, ?, ?)",
                (contact_id, email, label or "email"),
            )
            email_count += 1
        imported += 1
    relinked = _relink_participants_to_synced_contacts(conn)
    conn.commit()
    return {"contacts": imported, "phones": phone_count, "emails": email_count, "participants": relinked}


def _relink_participants_to_synced_contacts(conn) -> int:
    rows = conn.execute(
        """
        WITH best AS (
          SELECT cp.conversation_id,
            cp.phone_number,
            cp.contact_id,
            (
              SELECT cph.contact_id
              FROM contact_phones cph
              JOIN contacts c ON c.id = cph.contact_id
              WHERE cph.phone_number = cp.phone_number
                AND c.source IN ('fastmail', 'google')
              ORDER BY c.updated_at DESC, c.id DESC
              LIMIT 1
            ) AS best_contact_id
          FROM conversation_participants cp
          WHERE cp.role = 'participant'
        )
        SELECT conversation_id, phone_number, best_contact_id AS contact_id
        FROM best
        WHERE best_contact_id IS NOT NULL
          AND COALESCE(contact_id, 0) != best_contact_id
        """
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            UPDATE conversation_participants
            SET contact_id = ?
            WHERE conversation_id = ? AND phone_number = ?
            """,
            (row["contact_id"], row["conversation_id"], row["phone_number"]),
        )
    return len(rows)


def start_autosync() -> None:
    configured = bool(_fastmail_api_token() or (_fastmail_username_value() and _fastmail_app_password()))
    if not configured or not app_settings.get_bool("contacts.autosync", config.CONTACTS_AUTOSYNC):
        return

    def worker() -> None:
        while True:
            try:
                sync_contacts()
            except Exception as exc:
                print(f"Fastmail sync failed: {exc}", flush=True)
            interval = app_settings.get_int("contacts.sync_interval_minutes", config.CONTACTS_SYNC_INTERVAL_MINUTES)
            time.sleep(max(interval, 5) * 60)

    thread = threading.Thread(target=worker, name="fastmail-sync", daemon=True)
    thread.start()
