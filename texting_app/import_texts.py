from __future__ import annotations

import argparse
import mimetypes
import os
import re
import shutil
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import config
from .db import add_attachment, connect, ensure_conversation, init_db, self_numbers, upsert_message
from .phone import find_phone_numbers, normalize_phone
from .timeutil import EST, parse_import_timestamp


MEDIA_MARKER = "/texts/media/"


@dataclass
class ParsedBlock:
    css_class: str
    descript: str = ""
    body_parts: list[str] = field(default_factory=list)
    media_urls: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        text = "".join(self.body_parts)
        text = unescape(text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


class TextArchiveHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[ParsedBlock] = []
        self.current: ParsedBlock | None = None
        self.div_depth = 0
        self.in_descript = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag == "div":
            classes = set(attr.get("class", "").split())
            if self.current is None and {"thisguy", "theother", "vacnote"} & classes:
                css_class = next(c for c in ("thisguy", "theother", "vacnote") if c in classes)
                self.current = ParsedBlock(css_class=css_class)
                self.div_depth = 1
                return
            if self.current is not None:
                self.div_depth += 1
                if "descript" in classes:
                    self.in_descript = True
                return
        if self.current is None:
            return
        if tag == "br":
            self.current.body_parts.append("\n")
        if tag == "a":
            self._remember_media(attr.get("href", ""))
        if tag in {"img", "video", "audio", "source"}:
            self._remember_media(attr.get("src", ""))

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if tag == "div":
            if self.in_descript:
                self.in_descript = False
            self.div_depth -= 1
            if self.div_depth <= 0:
                self.blocks.append(self.current)
                self.current = None
                self.div_depth = 0

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        if self.in_descript:
            self.current.descript += data
        else:
            self.current.body_parts.append(data)

    def _remember_media(self, url: str) -> None:
        if not url:
            return
        if MEDIA_MARKER in url or url.startswith("media/") or url.startswith("/phone/texts/media/"):
            if url not in self.current.media_urls:
                self.current.media_urls.append(url)


def parse_html(content: str) -> list[ParsedBlock]:
    parser = TextArchiveHTMLParser()
    parser.feed(content)
    parser.close()
    return parser.blocks


def media_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if MEDIA_MARKER in path:
        return path.split(MEDIA_MARKER, 1)[1].split("/", 1)[0]
    if "/phone/texts/media/" in path:
        return path.split("/phone/texts/media/", 1)[1].split("/", 1)[0]
    if path.startswith("media/"):
        return path.split("/", 1)[1]
    return Path(path).name


def sniff_content_type(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            data = fh.read(32)
    except OSError:
        return "application/octet-stream"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if b"ftyp" in data[:16]:
        return "video/mp4"
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"BEGIN:VCARD"):
        return "text/vcard"
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed:
        return guessed
    return "application/octet-stream"


DATE_RE = re.compile(r"^\s*(\d{2}-\d{2}-\d{4}[ .]\d{2}:\d{2}(?::\d{2})?)")


def parse_descript(descript: str) -> tuple[str | None, str, list[str]]:
    date_match = DATE_RE.match(descript or "")
    if not date_match:
        return None, "", []
    timestamp_raw = date_match.group(1)
    if "," not in descript:
        return parse_import_timestamp(timestamp_raw), "", []
    timestamp_raw, route = descript.split(",", 1)
    occurred_at = parse_import_timestamp(timestamp_raw)
    if ">" in route:
        left, right = route.split(">", 1)
        from_numbers = find_phone_numbers(left)
        to_numbers = find_phone_numbers(right)
        return occurred_at, from_numbers[0] if from_numbers else "", to_numbers
    numbers = find_phone_numbers(route)
    return occurred_at, numbers[0] if numbers else "", []


def fill_block_timestamps(blocks: list[ParsedBlock], member_mtime: int) -> list[str]:
    parsed = [parse_descript(block.descript)[0] for block in blocks]
    if not any(parsed):
        fallback = datetime.fromtimestamp(member_mtime, EST).replace(microsecond=0).isoformat()
        return [fallback for _block in blocks]

    timestamps = parsed[:]
    for idx, timestamp in enumerate(timestamps):
        if timestamp:
            continue
        next_idx = next((i for i in range(idx + 1, len(timestamps)) if timestamps[i]), None)
        if next_idx is not None:
            next_dt = datetime.fromisoformat(timestamps[next_idx])
            timestamps[idx] = (next_dt - timedelta(seconds=next_idx - idx)).isoformat()
            continue
        prev_idx = next((i for i in range(idx - 1, -1, -1) if timestamps[i]), None)
        if prev_idx is not None:
            prev_dt = datetime.fromisoformat(timestamps[prev_idx])
            timestamps[idx] = (prev_dt + timedelta(seconds=idx - prev_idx)).isoformat()
    return [timestamp or datetime.fromtimestamp(member_mtime, EST).replace(microsecond=0).isoformat() for timestamp in timestamps]


def infer_message(
    block: ParsedBlock,
    filename_remote: str,
    known_self_numbers: set[str],
    occurred_at: str,
) -> tuple[str, str, list[str], list[str], str]:
    _parsed_at, parsed_from, parsed_to = parse_descript(block.descript)
    remote = normalize_phone(filename_remote)
    if not known_self_numbers:
        raise RuntimeError("Set TEXTING_PERSONAL_NUMBERS before importing texts.")
    default_self = sorted(known_self_numbers)[0]

    if block.css_class == "thisguy" or parsed_from in known_self_numbers:
        direction = "outbound"
        from_number = parsed_from if parsed_from in known_self_numbers else default_self
        to_numbers = parsed_to or ([remote] if remote and remote not in known_self_numbers else [])
    else:
        direction = "inbound"
        from_number = parsed_from or remote
        to_numbers = parsed_to or [default_self]

    to_numbers = [n for n in (normalize_phone(x) for x in to_numbers) if n]
    cc_numbers: list[str] = []
    return occurred_at, direction, normalize_phone(from_number), to_numbers, cc_numbers


def copy_archive_media(archive_path: Path, media_dir: Path) -> dict[str, Path]:
    media_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, Path] = {}
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.startswith("texts/media/"):
                continue
            name = Path(member.name).name
            if not name:
                continue
            target = media_dir / name
            if not target.exists() or target.stat().st_size != member.size:
                source = tar.extractfile(member)
                if source is None:
                    continue
                with target.open("wb") as out:
                    shutil.copyfileobj(source, out)
            copied[name] = target
    return copied


def import_archive(archive: Path, db_path: Path | None = None, copy_media: bool = True) -> dict[str, int]:
    archive = archive.expanduser().resolve()
    conn = connect(db_path)
    init_db(conn)
    known_self = self_numbers(conn)
    media_files = copy_archive_media(archive, config.MEDIA_DIR) if copy_media else {}
    imported = 0
    skipped = 0
    attachments = 0

    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            if not member.name.startswith("texts/") or member.name.startswith("texts/media/"):
                continue
            if member.name.endswith((".autoresponse", ".autotextresponse")):
                continue
            filename_remote = Path(member.name).name
            if not filename_remote.startswith("+") or filename_remote == "+":
                skipped += 1
                continue
            fileobj = tar.extractfile(member)
            if fileobj is None:
                continue
            content = fileobj.read().decode("utf-8", errors="replace")
            blocks = parse_html(content)
            if not blocks:
                skipped += 1
                continue
            timestamps = fill_block_timestamps(blocks, member.mtime)
            last_conversation_id: int | None = None
            for idx, block in enumerate(blocks):
                occurred_at, direction, from_number, to_numbers, cc_numbers = infer_message(
                    block, filename_remote, known_self, timestamps[idx]
                )
                participants = set(to_numbers + cc_numbers + [from_number])
                remote_numbers = sorted(n for n in participants if n not in known_self)
                self_participants = sorted(n for n in participants if n in known_self)
                if not remote_numbers:
                    remote = normalize_phone(filename_remote)
                    if remote and remote not in known_self:
                        remote_numbers = [remote]
                conversation_id = ensure_conversation(conn, remote_numbers, self_participants)
                last_conversation_id = conversation_id
                media_urls = block.media_urls
                message_type = "MMS" if media_urls else "SMS"
                message_id = upsert_message(
                    conn,
                    conversation_id=conversation_id,
                    direction=direction,
                    from_number=from_number,
                    to_numbers=to_numbers,
                    cc_numbers=cc_numbers,
                    text=block.text,
                    occurred_at=occurred_at,
                    message_type=message_type,
                    status="imported",
                    source="import",
                    import_source_id=f"{member.name}:{idx}",
                    raw_json={"descript": block.descript, "class": block.css_class},
                )
                for url in media_urls:
                    name = media_name_from_url(url)
                    local = media_files.get(name) or (config.MEDIA_DIR / name)
                    local_path = f"media/{name}" if local.exists() else None
                    size = local.stat().st_size if local.exists() else None
                    add_attachment(
                        conn,
                        message_id,
                        local_path=local_path,
                        remote_url=url,
                        content_type=sniff_content_type(local) if local.exists() else None,
                        size=size,
                        filename=name,
                        source="import",
                    )
                    attachments += 1
                imported += 1
            if last_conversation_id is not None:
                conn.execute(
                    """
                    UPDATE conversations
                    SET last_message_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (timestamps[-1], timestamps[-1], last_conversation_id),
                )
            if imported and imported % 1000 == 0:
                conn.commit()
    conn.commit()
    return {"messages": imported, "attachments": attachments, "skipped_files": skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import old text-message HTML archives into Switchboard.")
    parser.add_argument("archive")
    parser.add_argument("--db", default=str(config.DB_PATH))
    parser.add_argument("--no-media", action="store_true")
    args = parser.parse_args()
    stats = import_archive(Path(args.archive), Path(args.db), copy_media=not args.no_media)
    for key, value in stats.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
