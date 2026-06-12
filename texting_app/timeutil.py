from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")
EST = EASTERN


def now_est() -> str:
    return datetime.now(EASTERN).replace(microsecond=0).isoformat()


def parse_import_timestamp(raw: str) -> str:
    raw = raw.strip().replace(".", " ", 1)
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=EASTERN).isoformat()
        except ValueError:
            continue
    return now_est()


def normalize_iso_timestamp(value: str | None) -> str:
    if not value:
        return now_est()
    value = value.strip()
    try:
        if value.endswith("Z"):
            dt = datetime.fromisoformat(value[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(value)
        return dt.astimezone(EASTERN).replace(microsecond=0).isoformat()
    except ValueError:
        return now_est()
