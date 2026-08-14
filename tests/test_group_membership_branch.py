from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from texting_app import config
from texting_app.db import connect, ensure_conversation, init_db
from texting_app.server import create_conversation


ROOT = Path(__file__).resolve().parents[1]


class GroupMembershipBranchTests(unittest.TestCase):
    sender = "+15550000001"
    first_member = "+12075550101"
    second_member = "+12075550102"
    replacement_member = "+12075550103"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "switchboard.sqlite"
        self.db_patch = patch.object(config, "DB_PATH", self.database_path)
        self.numbers_patch = patch.object(config, "PERSONAL_NUMBERS", [self.sender])
        self.db_patch.start()
        self.numbers_patch.start()
        with closing(connect()) as conn:
            init_db(conn)
            self.original_id = ensure_conversation(
                conn,
                [self.first_member, self.second_member],
                [self.sender],
                "Original group",
            )
            conn.commit()

    def tearDown(self) -> None:
        self.numbers_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _participant_phones(self, conversation_id: int) -> list[str]:
        with closing(connect()) as conn:
            return [
                str(row["phone_number"])
                for row in conn.execute(
                    """
                    SELECT phone_number
                    FROM conversation_participants
                    WHERE conversation_id = ? AND role = 'participant'
                    ORDER BY phone_number
                    """,
                    (conversation_id,),
                )
            ]

    def test_existing_database_adds_the_branch_reference_column(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite"
        with closing(connect(legacy_path)) as conn:
            conn.execute(
                """
                CREATE TABLE conversations (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  conversation_key TEXT NOT NULL UNIQUE,
                  kind TEXT NOT NULL CHECK(kind IN ('direct', 'group')),
                  title TEXT,
                  is_archived INTEGER NOT NULL DEFAULT 0,
                  archived_at TEXT,
                  dealt_with_at TEXT,
                  manual_unread_at TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  last_message_at TEXT
                )
                """
            )
            init_db(conn)
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(conversations)")
            }
            indexes = {
                row["name"] for row in conn.execute("PRAGMA index_list(conversations)")
            }

        self.assertIn("branched_from_conversation_id", columns)
        self.assertIn("idx_conversations_branched_from", indexes)

    def test_changed_membership_creates_a_new_conversation_without_mutating_original(self) -> None:
        result = create_conversation(
            {
                "recipients": [self.first_member, self.replacement_member],
                "from_number": self.sender,
                "branched_from_conversation_id": self.original_id,
            }
        )

        self.assertTrue(result["created"])
        self.assertNotEqual(result["conversation_id"], self.original_id)
        self.assertEqual(
            self._participant_phones(self.original_id),
            [self.first_member, self.second_member],
        )
        self.assertEqual(
            self._participant_phones(result["conversation_id"]),
            [self.first_member, self.replacement_member],
        )
        self.assertEqual(
            result["conversation"]["branched_from"],
            {"id": self.original_id, "title": "Original group"},
        )

    def test_existing_membership_is_opened_instead_of_duplicated(self) -> None:
        payload = {
            "recipients": [self.first_member, self.replacement_member],
            "from_number": self.sender,
            "branched_from_conversation_id": self.original_id,
        }
        created = create_conversation(payload)
        reopened = create_conversation(payload)

        self.assertTrue(created["created"])
        self.assertFalse(reopened["created"])
        self.assertEqual(reopened["conversation_id"], created["conversation_id"])
        self.assertEqual(reopened["conversation"]["branched_from"]["id"], self.original_id)

    def test_removing_from_a_two_recipient_group_can_branch_to_a_direct_thread(self) -> None:
        result = create_conversation(
            {
                "recipients": [self.first_member],
                "from_number": self.sender,
                "branched_from_conversation_id": self.original_id,
            }
        )

        self.assertTrue(result["created"])
        self.assertEqual(result["conversation"]["kind"], "direct")
        self.assertEqual(self._participant_phones(result["conversation_id"]), [self.first_member])
        self.assertEqual(
            self._participant_phones(self.original_id),
            [self.first_member, self.second_member],
        )
        self.assertEqual(result["conversation"]["branched_from"]["id"], self.original_id)

    def test_deleting_the_original_preserves_the_branch_and_clears_its_reference(self) -> None:
        result = create_conversation(
            {
                "recipients": [self.first_member, self.replacement_member],
                "from_number": self.sender,
                "branched_from_conversation_id": self.original_id,
            }
        )

        with closing(connect()) as conn:
            conn.execute("DELETE FROM conversations WHERE id = ?", (self.original_id,))
            conn.commit()
            branch = conn.execute(
                "SELECT branched_from_conversation_id FROM conversations WHERE id = ?",
                (result["conversation_id"],),
            ).fetchone()

        self.assertIsNotNone(branch)
        self.assertIsNone(branch["branched_from_conversation_id"])

    def test_limited_user_cannot_reference_a_conversation_outside_their_number(self) -> None:
        with closing(connect()) as conn:
            inaccessible_id = ensure_conversation(
                conn,
                ["+12075550991", "+12075550992"],
                ["+15550000999"],
            )
            before = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            conn.commit()

        with self.assertRaisesRegex(LookupError, "Original conversation not found"):
            create_conversation(
                {
                    "recipients": [self.first_member, self.replacement_member],
                    "from_number": self.sender,
                    "branched_from_conversation_id": inaccessible_id,
                },
                assigned_phone=self.sender,
            )

        with closing(connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], before)

    def test_limited_user_sees_their_private_name_for_the_original(self) -> None:
        with closing(connect()) as conn:
            identity_id = conn.execute(
                "SELECT id FROM identities WHERE phone_number = ?",
                (self.sender,),
            ).fetchone()[0]
            cursor = conn.execute(
                """
                INSERT INTO limited_users(username, password_hash, identity_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("branch-viewer", "unused", identity_id, "2026-08-13T10:00:00-04:00", "2026-08-13T10:00:00-04:00"),
            )
            limited_user_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO limited_user_conversation_titles(
                  limited_user_id, conversation_id, title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    limited_user_id,
                    self.original_id,
                    "My private group name",
                    "2026-08-13T10:00:00-04:00",
                    "2026-08-13T10:00:00-04:00",
                ),
            )
            conn.commit()

        result = create_conversation(
            {
                "recipients": [self.first_member, self.replacement_member],
                "from_number": self.sender,
                "branched_from_conversation_id": self.original_id,
            },
            assigned_phone=self.sender,
            limited_user_id=limited_user_id,
        )

        self.assertEqual(
            result["conversation"]["branched_from"],
            {"id": self.original_id, "title": "My private group name"},
        )

    def test_members_dialog_owns_add_remove_and_branch_controls(self) -> None:
        html = (ROOT / "static" / "index.html").read_text()
        script = (ROOT / "static" / "app.js").read_text()
        modal_start = html.index('id="groupMembersModal"')
        modal_end = html.index('id="scheduleModal"')
        modal = html[modal_start:modal_end]

        self.assertIn('id="groupMemberAddForm"', modal)
        self.assertIn('id="groupMemberInput"', modal)
        self.assertIn('id="groupMembersCreate"', modal)
        self.assertIn('data-i18n="group.branch_note"', modal)
        self.assertIn('id="branchReference"', html)
        self.assertIn('id="branchReferenceLink"', html)
        self.assertIn('data-remove-group-member=', script)
        self.assertIn("async function createGroupMembershipBranch()", script)
        self.assertIn('method: "POST"', script[script.index("async function createGroupMembershipBranch()"):])
        self.assertIn('api("/api/conversations"', script[script.index("async function createGroupMembershipBranch()"):])
        self.assertIn("branched_from_conversation_id: source.id", script)
        self.assertIn("function renderBranchReference(conversation)", script)


if __name__ == "__main__":
    unittest.main()
