from __future__ import annotations

import re
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import Mock, patch

from texting_app import config
from texting_app.db import connect, ensure_conversation, init_db, upsert_message
from texting_app.server import (
    PARTICIPANT_COLOR_PALETTE,
    TextingHandler,
    _get_messages,
    _participants,
    _refresh_tokens,
    set_conversation_participant_color,
)


class ParticipantColorTests(unittest.TestCase):
    sender = "+15550000001"
    first_member = "+12075550101"
    second_member = "+12075550102"
    third_member = "+12075550103"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, "DATA_DIR", root))
        self.stack.enter_context(patch.object(config, "MEDIA_DIR", root / "media"))
        self.stack.enter_context(patch.object(config, "DB_PATH", root / "switchboard.sqlite"))
        self.stack.enter_context(patch.object(config, "PERSONAL_NUMBERS", [self.sender]))
        with closing(connect()) as conn:
            init_db(conn)
            self.group_id = ensure_conversation(
                conn,
                [self.first_member, self.second_member, self.third_member],
                [self.sender],
            )
            self.direct_id = ensure_conversation(
                conn,
                ["+12075550999"],
                [self.sender],
            )
            self.other_group_id = ensure_conversation(
                conn,
                ["+12075550801", "+12075550802"],
                ["+15550000999"],
            )
            self.first_message_id = upsert_message(
                conn,
                conversation_id=self.group_id,
                direction="inbound",
                from_number=self.first_member,
                to_numbers=[self.sender],
                cc_numbers=[self.second_member, self.third_member],
                text="First sender",
                occurred_at="2026-08-18T10:00:00-04:00",
            )
            self.second_message_id = upsert_message(
                conn,
                conversation_id=self.group_id,
                direction="inbound",
                from_number=self.second_member,
                to_numbers=[self.sender],
                cc_numbers=[self.first_member, self.third_member],
                text="Second sender",
                occurred_at="2026-08-18T10:01:00-04:00",
            )
            self.outbound_message_id = upsert_message(
                conn,
                conversation_id=self.group_id,
                direction="outbound",
                from_number=self.sender,
                to_numbers=[self.first_member, self.second_member, self.third_member],
                cc_numbers=[],
                text="Outbound",
                occurred_at="2026-08-18T10:02:00-04:00",
            )
            identity_id = conn.execute(
                "SELECT id FROM identities WHERE phone_number = ?",
                (self.sender,),
            ).fetchone()["id"]
            self.first_user_id = self._insert_limited_user(conn, identity_id, "first-operator")
            self.second_user_id = self._insert_limited_user(conn, identity_id, "second-operator")
            conn.commit()

    def tearDown(self) -> None:
        self.stack.close()
        self.temp_dir.cleanup()

    @staticmethod
    def _insert_limited_user(conn, identity_id: int, username: str) -> int:
        cursor = conn.execute(
            """
            INSERT INTO limited_users(username, password_hash, identity_id, created_at, updated_at)
            VALUES (?, 'unused', ?, '2026-08-18T10:00:00-04:00', '2026-08-18T10:00:00-04:00')
            """,
            (username, identity_id),
        )
        return int(cursor.lastrowid)

    def test_unset_remote_participants_receive_distinct_deterministic_defaults(self) -> None:
        with closing(connect()) as conn:
            first = _participants(conn, self.group_id)
            second = _participants(conn, self.group_id)

        first_colors = {
            participant["phone_number"]: participant["color"]
            for participant in first
            if participant["role"] == "participant"
        }
        second_colors = {
            participant["phone_number"]: participant["color"]
            for participant in second
            if participant["role"] == "participant"
        }
        self.assertEqual(first_colors, second_colors)
        self.assertEqual(len(set(first_colors.values())), 3)
        self.assertTrue(set(first_colors.values()).issubset(set(PARTICIPANT_COLOR_PALETTE)))
        for color in first_colors.values():
            self.assertRegex(color, re.compile(r"^#[0-9a-f]{6}$"))

    def test_defaults_remain_distinct_beyond_the_configured_palette(self) -> None:
        members = [
            f"+1212555{index:04d}"
            for index in range(len(PARTICIPANT_COLOR_PALETTE) + 7)
        ]
        with closing(connect()) as conn:
            conversation_id = ensure_conversation(conn, members, [self.sender])
            conn.commit()
            participants = _participants(conn, conversation_id)

        colors = [
            participant["color"]
            for participant in participants
            if participant["role"] == "participant"
        ]
        self.assertEqual(len(colors), len(members))
        self.assertEqual(len(set(colors)), len(members))

    def test_admin_color_is_conversation_specific_and_decorates_inbound_messages(self) -> None:
        with closing(connect()) as conn:
            before_refresh = _refresh_tokens(conn, self.group_id)
        result = set_conversation_participant_color(
            self.group_id,
            {"phone_number": self.first_member, "color": "#ABCDEF"},
        )
        participants = {
            participant["phone_number"]: participant
            for participant in result["conversation"]["participants"]
        }
        self.assertEqual(participants[self.first_member]["color"], "#abcdef")

        with closing(connect()) as conn:
            stored = conn.execute(
                """
                SELECT color
                FROM conversation_participants
                WHERE conversation_id = ? AND phone_number = ?
                """,
                (self.group_id, self.first_member),
            ).fetchone()["color"]
            direct_default = conn.execute(
                """
                SELECT color
                FROM conversation_participants
                WHERE conversation_id = ? AND phone_number = ?
                """,
                (self.direct_id, "+12075550999"),
            ).fetchone()["color"]
            payload = _get_messages(conn, self.group_id)
            after_refresh = _refresh_tokens(conn, self.group_id)

        self.assertEqual(stored, "#abcdef")
        self.assertIsNone(direct_default)
        self.assertNotEqual(before_refresh["list"], after_refresh["list"])
        self.assertNotEqual(before_refresh["conversation"], after_refresh["conversation"])
        messages = {message["id"]: message for message in payload["messages"]}
        self.assertEqual(messages[self.first_message_id]["sender_color"], "#abcdef")
        self.assertEqual(
            messages[self.second_message_id]["sender_color"],
            participants[self.second_member]["color"],
        )
        self.assertNotIn("sender_color", messages[self.outbound_message_id])

    def test_invalid_targets_and_colors_are_rejected(self) -> None:
        for invalid_color in ("", "abcdef", "#abc", "#1234567", "#gg0000"):
            with self.subTest(color=invalid_color):
                with self.assertRaisesRegex(ValueError, "#RRGGBB"):
                    set_conversation_participant_color(
                        self.group_id,
                        {"phone_number": self.first_member, "color": invalid_color},
                    )

        with self.assertRaisesRegex(ValueError, "only available for group"):
            set_conversation_participant_color(
                self.direct_id,
                {"phone_number": "+12075550999", "color": "#123456"},
            )
        for phone_number in (self.sender, "+12075550777"):
            with self.subTest(phone_number=phone_number):
                with self.assertRaisesRegex(LookupError, "Group participant not found"):
                    set_conversation_participant_color(
                        self.group_id,
                        {"phone_number": phone_number, "color": "#123456"},
                    )

    def test_limited_user_override_is_private_and_refreshable(self) -> None:
        set_conversation_participant_color(
            self.group_id,
            {"phone_number": self.first_member, "color": "#112233"},
        )
        with closing(connect()) as conn:
            before = _refresh_tokens(
                conn,
                self.group_id,
                self.sender,
                self.first_user_id,
            )

        limited_result = set_conversation_participant_color(
            self.group_id,
            {"phone_number": self.first_member, "color": "#FEDCBA"},
            self.sender,
            self.first_user_id,
        )

        with closing(connect()) as conn:
            after = _refresh_tokens(
                conn,
                self.group_id,
                self.sender,
                self.first_user_id,
            )
            admin_participants = _participants(conn, self.group_id)
            other_user_participants = _participants(
                conn,
                self.group_id,
                self.sender,
                self.second_user_id,
            )
            limited_messages = _get_messages(
                conn,
                self.group_id,
                assigned_phone=self.sender,
                limited_user_id=self.first_user_id,
            )["messages"]
            stored_global = conn.execute(
                """
                SELECT color
                FROM conversation_participants
                WHERE conversation_id = ? AND phone_number = ?
                """,
                (self.group_id, self.first_member),
            ).fetchone()["color"]
            stored_override = conn.execute(
                """
                SELECT color
                FROM limited_user_participant_colors
                WHERE limited_user_id = ? AND conversation_id = ? AND phone_number = ?
                """,
                (self.first_user_id, self.group_id, self.first_member),
            ).fetchone()["color"]

        limited_colors = {
            participant["phone_number"]: participant["color"]
            for participant in limited_result["conversation"]["participants"]
        }
        admin_colors = {participant["phone_number"]: participant["color"] for participant in admin_participants}
        other_colors = {
            participant["phone_number"]: participant["color"]
            for participant in other_user_participants
        }
        message = next(item for item in limited_messages if item["id"] == self.first_message_id)
        self.assertEqual(limited_colors[self.first_member], "#fedcba")
        self.assertEqual(message["sender_color"], "#fedcba")
        self.assertEqual(admin_colors[self.first_member], "#112233")
        self.assertEqual(other_colors[self.first_member], "#112233")
        self.assertEqual(stored_global, "#112233")
        self.assertEqual(stored_override, "#fedcba")
        self.assertNotEqual(before["list"], after["list"])
        self.assertNotEqual(before["conversation"], after["conversation"])

    def test_limited_user_cannot_color_an_inaccessible_group(self) -> None:
        with self.assertRaisesRegex(LookupError, "Conversation not found"):
            set_conversation_participant_color(
                self.other_group_id,
                {"phone_number": "+12075550801", "color": "#123456"},
                self.sender,
                self.first_user_id,
            )

    @patch("texting_app.server.set_conversation_participant_color")
    def test_post_route_dispatches_participant_color_update(self, update_color: Mock) -> None:
        update_color.return_value = {"conversation": {"id": self.group_id}}
        handler = TextingHandler.__new__(TextingHandler)
        handler.path = f"/api/conversations/{self.group_id}/participants/color"
        handler._begin_request = Mock(return_value=True)
        handler._require_auth = Mock(return_value=True)
        handler._read_json = Mock(return_value={"phone_number": self.first_member, "color": "#123456"})
        handler._assigned_phone = Mock(return_value=self.sender)
        handler._limited_user_id = Mock(return_value=self.first_user_id)
        handler._send_json = Mock()

        handler.do_POST()

        update_color.assert_called_once_with(
            self.group_id,
            {"phone_number": self.first_member, "color": "#123456"},
            self.sender,
            self.first_user_id,
        )
        handler._send_json.assert_called_once_with(update_color.return_value)


class ParticipantColorMigrationTests(unittest.TestCase):
    def test_existing_database_adds_participant_color_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database_path = root / "legacy.sqlite"
            with (
                patch.object(config, "DATA_DIR", root),
                patch.object(config, "MEDIA_DIR", root / "media"),
                patch.object(config, "PERSONAL_NUMBERS", []),
            ):
                with closing(connect(database_path)) as conn:
                    conn.execute(
                        """
                        CREATE TABLE conversation_participants (
                          conversation_id INTEGER NOT NULL,
                          phone_number TEXT NOT NULL,
                          role TEXT NOT NULL CHECK(role IN ('self', 'participant')),
                          contact_id INTEGER,
                          PRIMARY KEY(conversation_id, phone_number)
                        )
                        """
                    )
                    init_db(conn)
                    participant_columns = {
                        row["name"]
                        for row in conn.execute("PRAGMA table_info(conversation_participants)")
                    }
                    override_columns = {
                        row["name"]
                        for row in conn.execute("PRAGMA table_info(limited_user_participant_colors)")
                    }

            self.assertIn("color", participant_columns)
            self.assertEqual(
                override_columns,
                {
                    "limited_user_id",
                    "conversation_id",
                    "phone_number",
                    "color",
                    "created_at",
                    "updated_at",
                },
            )


if __name__ == "__main__":
    unittest.main()
