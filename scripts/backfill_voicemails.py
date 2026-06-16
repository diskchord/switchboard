#!/usr/bin/env python3
from __future__ import annotations

import json

from texting_app.db import connect, init_db
from texting_app.voice import (
    VoiceError,
    _ensure_local_recording,
    _local_recording_from_recording,
    _recording_is_meaningful,
    _revai_transcript_text,
    _safe_error_text,
    _store_voicemail_message,
)


def main() -> None:
    conn = connect()
    init_db(conn)
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM voicemail_recordings
            ORDER BY created_at
            """
        ).fetchall()
    ]
    downloaded = 0
    restored = 0
    skipped = 0
    failed = 0
    for recording in rows:
        provider = str(recording.get("provider") or "")
        recording_id = str(recording.get("recording_id") or "")
        recording_url = str(recording.get("recording_url") or "")
        local = _local_recording_from_recording(recording)
        if not local:
            try:
                local = _ensure_local_recording(
                    conn,
                    provider=provider,
                    recording_id=recording_id,
                    recording_url=recording_url,
                    recording=recording,
                )
                downloaded += 1
            except VoiceError as exc:
                failed += 1
                print(f"download failed for {provider}:{recording_id}: {_safe_error_text(exc)}")
                continue

        transcript = str(recording.get("transcript_text") or "").strip()
        if not transcript and recording.get("revai_job_id") and recording.get("transcription_status") == "transcribed":
            try:
                transcript = _revai_transcript_text(str(recording["revai_job_id"]))
            except VoiceError as exc:
                print(f"transcript fetch failed for {provider}:{recording_id}: {_safe_error_text(exc)}")
        if not transcript:
            if not _recording_is_meaningful(recording.get("duration_seconds")):
                skipped += 1
                continue
            transcript = "No transcript available."

        from_number = str(recording.get("from_number") or "")
        to_number = str(recording.get("to_number") or "")
        if not from_number or not to_number:
            skipped += 1
            continue

        result = _store_voicemail_message(
            conn,
            provider=provider,
            recording_id=recording_id,
            from_number=from_number,
            to_number=to_number,
            text=transcript,
            recording_url=recording_url,
            local_recording=local,
            raw_json={provider: json.loads(recording.get("raw_json") or "{}")},
            event_id=f"backfill:{provider}:{recording_id}",
        )
        if result.get("stored"):
            restored += 1
        else:
            skipped += 1
    conn.commit()
    print(
        "voicemail backfill complete: "
        f"downloaded={downloaded} restored={restored} skipped={skipped} failed={failed}"
    )


if __name__ == "__main__":
    main()
