from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from texting_app import config
from texting_app.assistant_api import (
    get_conversation_context,
    list_unread_conversations,
    list_unresolved_action_reviews,
    record_action_review,
)
from texting_app.db import add_attachment, connect, ensure_conversation, init_db, upsert_message


SELF_NUMBER = "+15551230001"
JANE_NUMBER = "+12075551234"
ROBERT_NUMBER = "+12075559876"


class AssistantApiDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, "DATA_DIR", root))
        self.stack.enter_context(patch.object(config, "MEDIA_DIR", root / "media"))
        self.stack.enter_context(patch.object(config, "DB_PATH", root / "switchboard.sqlite"))
        self.stack.enter_context(patch.object(config, "PERSONAL_NUMBERS", [SELF_NUMBER]))

        conn = connect()
        init_db(conn)
        self.jane_conversation_id = ensure_conversation(
            conn, [JANE_NUMBER], [SELF_NUMBER]
        )
        jane_contact_id = conn.execute(
            """
            SELECT contact_id FROM conversation_participants
            WHERE conversation_id = ? AND role = 'participant'
            """,
            (self.jane_conversation_id,),
        ).fetchone()["contact_id"]
        conn.execute(
            "UPDATE contacts SET display_name = 'Jane Smith' WHERE id = ?",
            (jane_contact_id,),
        )
        conn.execute(
            "INSERT INTO contact_emails(contact_id, email, label) VALUES (?, ?, ?)",
            (jane_contact_id, "jane@example.com", "home"),
        )
        self.jane_message_1 = upsert_message(
            conn,
            conversation_id=self.jane_conversation_id,
            direction="inbound",
            from_number=JANE_NUMBER,
            to_numbers=[SELF_NUMBER],
            cc_numbers=[],
            text="Hello, this is Jane.",
            occurred_at="2026-07-31T10:00:00-04:00",
        )
        self.jane_message_2 = upsert_message(
            conn,
            conversation_id=self.jane_conversation_id,
            direction="inbound",
            from_number=JANE_NUMBER,
            to_numbers=[SELF_NUMBER],
            cc_numbers=[],
            text="Could we move next Monday's appointment?",
            occurred_at="2026-07-31T10:01:00-04:00",
        )
        add_attachment(
            conn,
            self.jane_message_2,
            content_type="image/jpeg",
            size=42,
            filename="piano.jpg",
        )
        conn.execute(
            """
            INSERT INTO scheduled_messages(
              conversation_id, from_number, to_numbers, text, media_urls,
              scheduled_for, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '[]', ?, 'queued', ?, ?)
            """,
            (
                self.jane_conversation_id,
                SELF_NUMBER,
                f'["{JANE_NUMBER}"]',
                "Existing queued reply",
                "2026-07-31T12:00:00-04:00",
                "2026-07-31T10:02:00-04:00",
                "2026-07-31T10:02:00-04:00",
            ),
        )

        self.robert_conversation_id = ensure_conversation(
            conn, [ROBERT_NUMBER], [SELF_NUMBER]
        )
        robert_contact_id = conn.execute(
            """
            SELECT contact_id FROM conversation_participants
            WHERE conversation_id = ? AND role = 'participant'
            """,
            (self.robert_conversation_id,),
        ).fetchone()["contact_id"]
        conn.execute(
            "UPDATE contacts SET display_name = 'Robert Jones' WHERE id = ?",
            (robert_contact_id,),
        )
        self.robert_message = upsert_message(
            conn,
            conversation_id=self.robert_conversation_id,
            direction="inbound",
            from_number=ROBERT_NUMBER,
            to_numbers=[SELF_NUMBER],
            cc_numbers=[],
            text="No transcript available.",
            occurred_at="2026-07-31T09:00:00-04:00",
            message_type="Voicemail",
        )
        conn.execute(
            "UPDATE conversations SET is_archived = 1 WHERE id = ?",
            (self.robert_conversation_id,),
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.stack.close()
        self.temp_dir.cleanup()

    def test_unread_summaries_are_minimal_filterable_and_do_not_change_unread_state(self) -> None:
        before = self._conversation_markers(self.jane_conversation_id)

        result = list_unread_conversations({})

        self.assertFalse(result["has_more"])
        self.assertEqual(len(result["conversations"]), 1)
        summary = result["conversations"][0]
        self.assertEqual(summary["conversation_id"], self.jane_conversation_id)
        self.assertEqual(summary["contact_name"], "Jane Smith")
        self.assertEqual(summary["phone_number"], JANE_NUMBER)
        self.assertEqual(summary["personal_numbers"], [SELF_NUMBER])
        self.assertEqual(summary["latest_unread_message_id"], self.jane_message_2)
        self.assertEqual(
            summary["latest_unread_text"],
            "Could we move next Monday's appointment?",
        )
        self.assertEqual(summary["unread_count"], 2)
        self.assertTrue(summary["has_media"])
        self.assertFalse(summary["has_voicemail"])
        self.assertTrue(summary["pending_scheduled_message"])
        self.assertIsNone(summary["review"])
        self.assertEqual(self._conversation_markers(self.jane_conversation_id), before)

        hidden = list_unread_conversations({"include_hidden": ["true"]})
        self.assertEqual(len(hidden["conversations"]), 2)
        voicemail = next(
            item
            for item in hidden["conversations"]
            if item["conversation_id"] == self.robert_conversation_id
        )
        self.assertTrue(voicemail["has_voicemail"])
        self.assertTrue(voicemail["is_hidden"])

        no_match = list_unread_conversations(
            {"personal_number": ["+15559999999"]}
        )
        self.assertEqual(no_match["conversations"], [])

    def test_later_outbound_does_not_mask_an_earlier_unread_inbound(self) -> None:
        conn = connect()
        upsert_message(
            conn,
            conversation_id=self.jane_conversation_id,
            direction="outbound",
            from_number=SELF_NUMBER,
            to_numbers=[JANE_NUMBER],
            cc_numbers=[],
            text="A reply sent by another viewer",
            occurred_at="2026-07-31T10:02:00-04:00",
            status="sent",
        )
        conn.commit()
        conn.close()

        result = list_unread_conversations({})
        context = get_conversation_context(self.jane_conversation_id, {})

        self.assertEqual(
            [item["conversation_id"] for item in result["conversations"]],
            [self.jane_conversation_id],
        )
        summary = result["conversations"][0]
        self.assertEqual(summary["latest_unread_message_id"], self.jane_message_2)
        self.assertEqual(summary["unread_count"], 2)
        self.assertTrue(context["conversation"]["is_unread"])

    def test_context_is_bounded_and_includes_contacts_media_and_queued_messages(self) -> None:
        result = get_conversation_context(
            self.jane_conversation_id, {"message_limit": ["1"]}
        )

        self.assertTrue(result["has_more"])
        self.assertEqual([message["id"] for message in result["messages"]], [self.jane_message_2])
        self.assertTrue(result["messages"][0]["has_media"])
        self.assertEqual(result["messages"][0]["attachments"][0]["filename"], "piano.jpg")
        participant = next(
            item
            for item in result["conversation"]["participants"]
            if item["role"] == "participant"
        )
        self.assertEqual(participant["contact"]["display_name"], "Jane Smith")
        self.assertEqual(
            participant["contact"]["emails"],
            [{"email": "jane@example.com", "label": "home"}],
        )
        self.assertTrue(result["conversation"]["is_unread"])
        self.assertEqual(
            result["pending_scheduled_messages"][0]["text"],
            "Existing queued reply",
        )

    def test_reviews_suppress_repeats_reopen_on_inbound_and_preserve_unread_state(self) -> None:
        before = self._conversation_markers(self.jane_conversation_id)
        created = record_action_review(
            {
                "conversation_id": self.jane_conversation_id,
                "through_message_id": self.jane_message_2,
                "state": "presented",
                "analysis_version": "triage-v1",
            }
        )["action_review"]
        self.assertEqual(created["state"], "presented")
        self.assertEqual(created["analysis_version"], "triage-v1")
        self.assertEqual(self._conversation_markers(self.jane_conversation_id), before)

        suppressed = list_unread_conversations({})
        self.assertEqual(suppressed["conversations"], [])
        all_unread = list_unread_conversations(
            {"only_new_since_last_review": ["false"]}
        )
        self.assertEqual(all_unread["conversations"][0]["review"]["state"], "presented")

        conn = connect()
        new_message_id = upsert_message(
            conn,
            conversation_id=self.jane_conversation_id,
            direction="inbound",
            from_number=JANE_NUMBER,
            to_numbers=[SELF_NUMBER],
            cc_numbers=[],
            text="Wednesday or Friday morning might work.",
            occurred_at="2026-07-31T10:03:00-04:00",
        )
        conn.commit()
        conn.close()

        reopened = list_unread_conversations({})
        self.assertEqual(reopened["conversations"][0]["latest_unread_message_id"], new_message_id)
        unresolved = list_unresolved_action_reviews({})["action_reviews"]
        self.assertEqual(len(unresolved), 1)
        self.assertTrue(unresolved[0]["has_new_messages"])

    def test_deferred_review_returns_after_expiry_and_upsert_keeps_first_timestamp(self) -> None:
        first = record_action_review(
            {
                "conversation_id": self.jane_conversation_id,
                "through_message_id": self.jane_message_2,
                "state": "deferred",
                "defer_until": "2099-08-01T09:00:00-04:00",
            }
        )["action_review"]
        self.assertEqual(list_unread_conversations({})["conversations"], [])

        second = record_action_review(
            {
                "conversation_id": self.jane_conversation_id,
                "through_message_id": self.jane_message_2,
                "state": "deferred",
                "defer_until": "2020-08-01T09:00:00-04:00",
            }
        )["action_review"]
        self.assertEqual(second["first_presented_at"], first["first_presented_at"])
        due = list_unread_conversations({})["conversations"]
        self.assertEqual(len(due), 1)
        unresolved = list_unresolved_action_reviews({})["action_reviews"]
        self.assertTrue(unresolved[0]["deferral_expired"])

    def test_review_validation_rejects_a_message_from_another_conversation(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not belong"):
            record_action_review(
                {
                    "conversation_id": self.jane_conversation_id,
                    "through_message_id": self.robert_message,
                    "state": "resolved",
                }
            )

    def _conversation_markers(self, conversation_id: int) -> tuple[str | None, str | None, int]:
        conn = connect()
        row = conn.execute(
            """
            SELECT dealt_with_at, manual_unread_at, is_archived
            FROM conversations WHERE id = ?
            """,
            (conversation_id,),
        ).fetchone()
        conn.close()
        return row["dealt_with_at"], row["manual_unread_at"], row["is_archived"]


if __name__ == "__main__":
    unittest.main()
