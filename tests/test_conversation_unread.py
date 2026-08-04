from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from texting_app import config
from texting_app.db import connect, ensure_conversation, init_db, upsert_message
from texting_app.server import _get_messages, _list_conversations, _mark_reply_message_read


class QueuedMessageUnreadStateTests(unittest.TestCase):
    def test_queued_preview_does_not_clear_unread_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect(Path(temp_dir) / "switchboard.sqlite")
            init_db(conn)
            conversation_id = ensure_conversation(
                conn,
                ["+12075551234"],
                ["+15551230001"],
            )
            upsert_message(
                conn,
                conversation_id=conversation_id,
                direction="inbound",
                from_number="+12075551234",
                to_numbers=["+15551230001"],
                cc_numbers=[],
                text="Could we move the appointment?",
                occurred_at="2026-07-31T10:00:00-04:00",
            )
            conn.execute(
                """
                INSERT INTO scheduled_messages(
                  conversation_id, from_number, to_numbers, text, media_urls,
                  scheduled_for, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '[]', ?, 'queued', ?, ?)
                """,
                (
                    conversation_id,
                    "+15551230001",
                    '["+12075551234"]',
                    "Queued reply",
                    "2026-07-31T11:00:00-04:00",
                    "2026-07-31T10:01:00-04:00",
                    "2026-07-31T10:01:00-04:00",
                ),
            )
            conn.commit()

            inbox = _list_conversations(conn, {})["conversations"]
            unread = _list_conversations(conn, {"unread": ["true"]})["conversations"]
            thread = _get_messages(conn, conversation_id)

            self.assertEqual(len(inbox), 1)
            self.assertEqual(len(unread), 1)
            self.assertEqual(inbox[0]["last_status"], "scheduled")
            self.assertEqual(inbox[0]["last_direction"], "outbound")
            self.assertEqual(inbox[0]["needs_attention"], 1)
            self.assertEqual(thread["conversation"]["last_direction"], "outbound")
            self.assertEqual(thread["conversation"]["needs_attention"], 1)
            conn.close()

    def test_reply_read_state_includes_authoritative_unread_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "switchboard.sqlite"
            with patch.object(config, "DB_PATH", database_path), patch.object(config, "PERSONAL_NUMBERS", []):
                conn = connect()
                init_db(conn)
                replied_conversation_id = ensure_conversation(
                    conn,
                    ["+12075551234"],
                    ["+15551230001"],
                )
                other_conversation_id = ensure_conversation(
                    conn,
                    ["+12075559876"],
                    ["+15551230001"],
                )
                upsert_message(
                    conn,
                    conversation_id=replied_conversation_id,
                    direction="inbound",
                    from_number="+12075551234",
                    to_numbers=["+15551230001"],
                    cc_numbers=[],
                    text="First unread conversation",
                    occurred_at="2026-07-31T10:00:00-04:00",
                )
                upsert_message(
                    conn,
                    conversation_id=other_conversation_id,
                    direction="inbound",
                    from_number="+12075559876",
                    to_numbers=["+15551230001"],
                    cc_numbers=[],
                    text="Second unread conversation",
                    occurred_at="2026-07-31T10:01:00-04:00",
                )
                reply_id = upsert_message(
                    conn,
                    conversation_id=replied_conversation_id,
                    direction="outbound",
                    from_number="+15551230001",
                    to_numbers=["+12075551234"],
                    cc_numbers=[],
                    text="Reply",
                    occurred_at="2026-07-31T10:02:00-04:00",
                )
                conn.commit()
                conn.close()

                result = _mark_reply_message_read(reply_id)

                self.assertIsNotNone(result)
                self.assertEqual(result["conversation"]["id"], replied_conversation_id)
                self.assertEqual(result["conversation"]["needs_attention"], 0)
                self.assertEqual(result["unread_count"], 1)


if __name__ == "__main__":
    unittest.main()
