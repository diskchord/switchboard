from __future__ import annotations

import re


PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def normalize_phone(value: str | None, default_country: str = "1") -> str:
    if not value:
        return ""
    value = value.strip()
    if value.startswith("tel:"):
        value = value[4:]
    value = value.split(";")[0]
    digits = re.sub(r"\D", "", value)
    if not digits:
        return ""
    if len(digits) == 10:
        digits = default_country + digits
    if len(digits) == 11 and digits.startswith(default_country):
        return f"+{digits}"
    if value.strip().startswith("+"):
        return f"+{digits}"
    return f"+{digits}"


def display_phone(value: str | None) -> str:
    normalized = normalize_phone(value)
    digits = re.sub(r"\D", "", normalized)
    if len(digits) == 11 and digits.startswith("1"):
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    if normalized:
        return normalized
    return ""


def find_phone_numbers(text: str | None) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in PHONE_RE.findall(text):
        number = normalize_phone(match)
        if number and number not in seen:
            seen.add(number)
            found.append(number)
    return found

