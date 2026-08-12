from __future__ import annotations

import tempfile
import http.client
import json
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from texting_app import auth, config
from texting_app.db import (
    LIMITED_USER_SCOPES_KEY,
    backfill_limited_user_scopes,
    connect,
    ensure_contact_for_phone,
    ensure_conversation,
    init_db,
    upsert_message,
)
from texting_app.server import (
    _bootstrap,
    _contact_names,
    _get_messages,
    _list_conversations,
    _mark_reply_message_read,
    _search_contacts,
    _unread_conversation_count,
    create_limited_user,
    limited_user_preferences,
    list_limited_users,
    principal_from_session,
    refresh_state,
    save_contact_name,
    send_api_message,
    set_conversation_dealt,
    set_conversation_title,
    update_limited_user,
    update_limited_user_preferences,
    TextingHandler,
)


class LimitedUserTests(unittest.TestCase):
    first_number = "+15550000001"
    second_number = "+15550000002"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, "DATA_DIR", root))
        self.stack.enter_context(patch.object(config, "MEDIA_DIR", root / "media"))
        self.stack.enter_context(patch.object(config, "DB_PATH", root / "switchboard.sqlite"))
        self.stack.enter_context(
            patch.object(config, "PERSONAL_NUMBERS", [self.first_number, self.second_number])
        )
        self.stack.enter_context(
            patch.object(
                config,
                "DEFAULT_IDENTITY_LABELS",
                {self.first_number: "First line", self.second_number: "Second line"},
            )
        )
        self.stack.enter_context(patch.object(config, "AUTH_USERNAME", "admin"))
        self.stack.enter_context(patch.object(config, "AUTH_PASSWORD_HASH", "admin-hash"))
        self.stack.enter_context(patch.object(config, "AUTH_SECRET_KEY", "test-session-secret"))
        self.stack.enter_context(patch.object(config, "AUTH_DISABLED", False))
        original_hash_password = auth.hash_password
        self.stack.enter_context(
            patch(
                "texting_app.server.auth.hash_password",
                lambda value: original_hash_password(value, 1_000),
            )
        )
        self.conn = connect()
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.stack.close()
        self.temp_dir.cleanup()

    def _identity_id(self, number: str) -> int:
        return int(
            self.conn.execute(
                "SELECT id FROM identities WHERE phone_number = ?", (number,)
            ).fetchone()[0]
        )

    def _seed_shared_conversation(self) -> tuple[int, int]:
        remote = "+12075551234"
        conversation_id = ensure_conversation(self.conn, [remote], [self.first_number])
        ensure_conversation(self.conn, [remote], [self.second_number])
        first_message = upsert_message(
            self.conn,
            conversation_id=conversation_id,
            direction="inbound",
            from_number=remote,
            to_numbers=[self.first_number],
            cc_numbers=[],
            text="Message for first line",
            occurred_at="2026-08-01T10:00:00-04:00",
        )
        upsert_message(
            self.conn,
            conversation_id=conversation_id,
            direction="inbound",
            from_number=remote,
            to_numbers=[self.second_number],
            cc_numbers=[],
            text="Private second-line message",
            occurred_at="2026-08-01T10:01:00-04:00",
        )
        other_id = ensure_conversation(
            self.conn, ["+12075559876"], [self.second_number]
        )
        upsert_message(
            self.conn,
            conversation_id=other_id,
            direction="inbound",
            from_number="+12075559876",
            to_numbers=[self.second_number],
            cc_numbers=[],
            text="Second line only",
            occurred_at="2026-08-01T10:02:00-04:00",
        )
        self.conn.commit()
        return conversation_id, first_message

    def _name_contact(self, phone_number: str, display_name: str) -> None:
        contact_id = ensure_contact_for_phone(self.conn, phone_number)
        self.conn.execute(
            "UPDATE contacts SET display_name = ?, updated_at = ? WHERE id = ?",
            (display_name, "2026-08-01T09:00:00-04:00", contact_id),
        )
        self.conn.commit()

    def test_reads_are_scoped_to_assigned_number_even_in_a_shared_thread(self) -> None:
        conversation_id, first_message = self._seed_shared_conversation()

        conversations = _list_conversations(
            self.conn, {}, assigned_phone=self.first_number
        )["conversations"]
        thread = _get_messages(
            self.conn, conversation_id, assigned_phone=self.first_number
        )

        self.assertEqual([item["id"] for item in conversations], [conversation_id])
        self.assertEqual(conversations[0]["last_from_number"], "+12075551234")
        self.assertEqual(conversations[0]["last_to_numbers"], [self.first_number])
        self.assertEqual([item["id"] for item in thread["messages"]], [first_message])
        self.assertEqual(thread["messages"][0]["text"], "Message for first line")
        self.assertNotIn(
            self.second_number,
            [participant["phone_number"] for participant in thread["conversation"]["participants"]],
        )

    def test_group_names_are_editable_and_private_for_limited_users(self) -> None:
        group_id = ensure_conversation(
            self.conn,
            ["+12075551111", "+12075552222"],
            [self.first_number],
        )
        direct_id = ensure_conversation(
            self.conn,
            ["+12075553333"],
            [self.first_number],
        )
        self.conn.commit()
        first_user = create_limited_user(
            {
                "username": "group-namer",
                "password": "correct-horse",
                "identity_id": self._identity_id(self.first_number),
            }
        )["user"]
        second_user = create_limited_user(
            {
                "username": "other-group-namer",
                "password": "correct-horse",
                "identity_id": self._identity_id(self.first_number),
            }
        )["user"]

        admin_result = set_conversation_title(group_id, "Family updates")
        limited_result = set_conversation_title(
            group_id,
            "My shift group",
            self.first_number,
            first_user["id"],
        )

        self.assertEqual(admin_result["conversation"]["custom_title"], "Family updates")
        self.assertEqual(limited_result["conversation"]["custom_title"], "My shift group")
        self.assertEqual(
            _get_messages(
                self.conn,
                group_id,
                assigned_phone=self.first_number,
                limited_user_id=first_user["id"],
            )["conversation"]["title"],
            "My shift group",
        )
        other_list = _list_conversations(
            self.conn,
            {},
            assigned_phone=self.first_number,
            limited_user_id=second_user["id"],
        )["conversations"]
        other_group = next(item for item in other_list if item["id"] == group_id)
        self.assertEqual(other_group["custom_title"], "")
        self.assertNotEqual(other_group["title"], "Family updates")
        self.assertEqual(
            [
                item["id"]
                for item in _list_conversations(
                    self.conn,
                    {"search": ["My shift group"]},
                    assigned_phone=self.first_number,
                    limited_user_id=first_user["id"],
                )["conversations"]
            ],
            [group_id],
        )
        self.assertEqual(
            _list_conversations(
                self.conn,
                {"search": ["My shift group"]},
                assigned_phone=self.first_number,
                limited_user_id=second_user["id"],
            )["conversations"],
            [],
        )

        cleared = set_conversation_title(
            group_id,
            "",
            self.first_number,
            first_user["id"],
        )["conversation"]
        self.assertEqual(cleared["custom_title"], "")
        self.assertNotEqual(cleared["title"], "My shift group")
        with self.assertRaisesRegex(ValueError, "Only group"):
            set_conversation_title(direct_id, "Not allowed")
        with self.assertRaisesRegex(ValueError, "120 characters"):
            set_conversation_title(group_id, "x" * 121)

    def test_unread_state_is_isolated_per_user_and_from_primary(self) -> None:
        conversation_id, first_message = self._seed_shared_conversation()
        first_user = create_limited_user(
            {
                "username": "first-reader",
                "password": "correct-horse",
                "identity_id": self._identity_id(self.first_number),
            }
        )["user"]
        second_user = create_limited_user(
            {
                "username": "second-reader",
                "password": "correct-horse",
                "identity_id": self._identity_id(self.first_number),
            }
        )["user"]
        first_principal = {
            "role": "limited",
            "user_id": first_user["id"],
            "phone_number": self.first_number,
        }
        second_principal = {
            "role": "limited",
            "user_id": second_user["id"],
            "phone_number": self.first_number,
        }

        _mark_reply_message_read(first_message)
        limited_state_count = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM limited_user_conversation_states
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()[0]
        self.assertEqual(limited_state_count, 0)
        self.assertEqual(_bootstrap(self.conn, first_principal)["stats"]["unread_conversations"], 1)
        self.assertEqual(_bootstrap(self.conn, second_principal)["stats"]["unread_conversations"], 1)

        set_conversation_dealt(
            conversation_id,
            True,
            self.first_number,
            first_user["id"],
        )
        first_unread = _list_conversations(
            self.conn,
            {"unread": ["true"]},
            assigned_phone=self.first_number,
            limited_user_id=first_user["id"],
        )["conversations"]
        second_unread = _list_conversations(
            self.conn,
            {"unread": ["true"]},
            assigned_phone=self.first_number,
            limited_user_id=second_user["id"],
        )["conversations"]
        first_thread = _get_messages(
            self.conn,
            conversation_id,
            assigned_phone=self.first_number,
            limited_user_id=first_user["id"],
        )
        global_state = self.conn.execute(
            "SELECT dealt_with_at, manual_unread_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()

        self.assertEqual(first_unread, [])
        self.assertEqual([item["id"] for item in second_unread], [conversation_id])
        self.assertEqual(
            _unread_conversation_count(self.conn, self.first_number, first_user["id"]),
            0,
        )
        self.assertEqual(
            _unread_conversation_count(self.conn, self.first_number, second_user["id"]),
            1,
        )
        self.assertFalse(first_thread["conversation"]["needs_attention"])
        self.assertIsNotNone(global_state["dealt_with_at"])
        self.assertIsNone(global_state["manual_unread_at"])
        self.assertNotEqual(
            refresh_state({}, self.first_number, first_user["id"])["tokens"]["list"],
            refresh_state({}, self.first_number, second_user["id"])["tokens"]["list"],
        )

        set_conversation_dealt(conversation_id, False)
        first_unread = _list_conversations(
            self.conn,
            {"unread": ["true"]},
            assigned_phone=self.first_number,
            limited_user_id=first_user["id"],
        )["conversations"]
        self.assertEqual(first_unread, [])

    def test_global_manual_unread_does_not_leak_into_new_limited_scope(self) -> None:
        conversation_id, _ = self._seed_shared_conversation()
        self.conn.execute(
            """
            UPDATE conversations
            SET dealt_with_at = ?, manual_unread_at = ?
            WHERE id = ?
            """,
            (
                "2026-08-01T10:05:00-04:00",
                "2026-08-01T10:05:00-04:00",
                conversation_id,
            ),
        )
        self.conn.commit()
        user = create_limited_user(
            {
                "username": "manual-unread-reader",
                "password": "correct-horse",
                "identity_id": self._identity_id(self.first_number),
            }
        )["user"]

        unread = _list_conversations(
            self.conn,
            {"unread": ["true"]},
            assigned_phone=self.first_number,
            limited_user_id=user["id"],
        )["conversations"]
        thread = _get_messages(
            self.conn,
            conversation_id,
            assigned_phone=self.first_number,
            limited_user_id=user["id"],
        )
        self.assertEqual([item["id"] for item in unread], [conversation_id])
        self.assertIsNone(thread["conversation"]["manual_unread_at"])

    def test_limited_contacts_are_snapshotted_and_then_owned_by_each_user(self) -> None:
        conversation_id, _ = self._seed_shared_conversation()
        shared_remote = "+12075551234"
        second_only_remote = "+12075559876"
        self._name_contact(shared_remote, "Original Shared Name")
        self._name_contact(second_only_remote, "Second Line Secret")

        first_user = create_limited_user(
            {
                "username": "first-operator",
                "password": "correct-horse",
                "identity_id": self._identity_id(self.first_number),
            }
        )["user"]
        self._name_contact(shared_remote, "Later Admin Rename")

        late_remote = "+12075557777"
        late_conversation = ensure_conversation(
            self.conn,
            [late_remote],
            [self.first_number],
        )
        upsert_message(
            self.conn,
            conversation_id=late_conversation,
            direction="inbound",
            from_number=late_remote,
            to_numbers=[self.first_number],
            cc_numbers=[],
            text="Created after the account",
            occurred_at="2026-08-01T11:00:00-04:00",
        )
        self.conn.commit()
        self._name_contact(late_remote, "Late Admin Contact")

        contacts = _search_contacts(self.conn, {}, first_user["id"])["contacts"]
        scoped_thread = _get_messages(
            self.conn,
            conversation_id,
            assigned_phone=self.first_number,
            limited_user_id=first_user["id"],
        )

        self.assertEqual(
            [(item["phone_number"], item["display_name"]) for item in contacts],
            [(shared_remote, "Original Shared Name")],
        )
        self.assertEqual(
            [
                participant["display"]
                for participant in scoped_thread["conversation"]["participants"]
                if participant["role"] == "participant"
            ],
            ["Original Shared Name"],
        )
        self.assertNotIn(second_only_remote, [item["phone_number"] for item in contacts])
        self.assertNotIn(late_remote, [item["phone_number"] for item in contacts])
        self.assertEqual(
            [
                item["id"]
                for item in _list_conversations(
                    self.conn,
                    {"search": ["Original Shared"]},
                    assigned_phone=self.first_number,
                    limited_user_id=first_user["id"],
                )["conversations"]
            ],
            [conversation_id],
        )
        self.assertEqual(
            _list_conversations(
                self.conn,
                {"search": ["Later Admin Rename"]},
                assigned_phone=self.first_number,
                limited_user_id=first_user["id"],
            )["conversations"],
            [],
        )

        save_contact_name(
            {
                "phone_number": shared_remote,
                "display_name": "My Shared Name",
                "conversation_id": conversation_id,
            },
            self.first_number,
            first_user["id"],
        )
        save_contact_name(
            {
                "phone_number": late_remote,
                "display_name": "My New Contact",
                "conversation_id": late_conversation,
            },
            self.first_number,
            first_user["id"],
        )

        renamed_contacts = _search_contacts(self.conn, {}, first_user["id"])["contacts"]
        self.assertEqual(
            {
                item["phone_number"]: item["display_name"]
                for item in renamed_contacts
            },
            {
                shared_remote: "My Shared Name",
                late_remote: "My New Contact",
            },
        )
        self.assertEqual(
            _contact_names(self.conn, [shared_remote])[shared_remote],
            "Later Admin Rename",
        )
        with self.assertRaisesRegex(LookupError, "Contact not found"):
            save_contact_name(
                {
                    "phone_number": second_only_remote,
                    "display_name": "Leaked Contact",
                },
                self.first_number,
                first_user["id"],
            )

        later_user = create_limited_user(
            {
                "username": "later-operator",
                "password": "correct-horse",
                "identity_id": self._identity_id(self.first_number),
            }
        )["user"]
        later_contacts = _search_contacts(self.conn, {}, later_user["id"])["contacts"]
        self.assertEqual(
            {
                item["phone_number"]: item["display_name"]
                for item in later_contacts
            },
            {
                shared_remote: "Later Admin Rename",
                late_remote: "Late Admin Contact",
            },
        )

        update_limited_user(
            first_user["id"],
            {
                "username": "first-operator",
                "identity_id": self._identity_id(self.second_number),
                "is_active": True,
            },
        )
        reassigned_contacts = _search_contacts(
            self.conn,
            {},
            first_user["id"],
        )["contacts"]
        self.assertEqual(
            {
                item["phone_number"]: item["display_name"]
                for item in reassigned_contacts
            },
            {
                shared_remote: "Later Admin Rename",
                second_only_remote: "Second Line Secret",
            },
        )

    def test_existing_limited_users_receive_one_time_scope_backfill(self) -> None:
        conversation_id, _ = self._seed_shared_conversation()
        shared_remote = "+12075551234"
        self._name_contact(shared_remote, "Migration Snapshot")
        user = create_limited_user(
            {
                "username": "existing-operator",
                "password": "correct-horse",
                "identity_id": self._identity_id(self.first_number),
            }
        )["user"]
        self.conn.execute(
            "DELETE FROM limited_user_contacts WHERE limited_user_id = ?",
            (user["id"],),
        )
        self.conn.execute(
            "DELETE FROM app_metadata WHERE key = ?",
            (LIMITED_USER_SCOPES_KEY,),
        )
        self.conn.commit()

        backfill_limited_user_scopes(self.conn)
        contacts = _search_contacts(self.conn, {}, user["id"])["contacts"]
        read_state = self.conn.execute(
            """
            SELECT 1
            FROM limited_user_conversation_states
            WHERE conversation_id = ? AND limited_user_id = ?
            """,
            (conversation_id, user["id"]),
        ).fetchone()

        self.assertEqual(
            [(item["phone_number"], item["display_name"]) for item in contacts],
            [(shared_remote, "Migration Snapshot")],
        )
        self.assertIsNone(read_state)

        self._name_contact(shared_remote, "Changed After Migration")
        backfill_limited_user_scopes(self.conn)
        contacts = _search_contacts(self.conn, {}, user["id"])["contacts"]
        self.assertEqual(contacts[0]["display_name"], "Migration Snapshot")

    def test_bootstrap_and_refresh_are_scoped_and_unrelated_threads_are_denied(self) -> None:
        conversation_id, _ = self._seed_shared_conversation()
        user = create_limited_user(
            {
                "username": "bootstrap-operator",
                "password": "correct-horse",
                "identity_id": self._identity_id(self.first_number),
            }
        )["user"]
        principal = {
            "role": "limited",
            "user_id": user["id"],
            "username": "operator",
            "phone_number": self.first_number,
            "theme_family": "console",
            "theme_mode": "dark",
        }

        payload = _bootstrap(self.conn, principal)
        refresh = refresh_state(
            {"conversation_id": [str(conversation_id)]},
            self.first_number,
            user["id"],
        )

        self.assertEqual([item["phone_number"] for item in payload["identities"]], [self.first_number])
        self.assertEqual(payload["stats"]["messages"], 1)
        self.assertEqual(payload["preferences"], {"theme_family": "console", "theme_mode": "dark"})
        self.assertTrue(refresh["tokens"]["conversation"])
        with self.assertRaisesRegex(LookupError, "Conversation not found"):
            _get_messages(
                self.conn,
                conversation_id + 1,
                assigned_phone=self.first_number,
                limited_user_id=user["id"],
            )

    def test_user_management_sessions_and_preferences(self) -> None:
        identity_id = self._identity_id(self.first_number)
        created = create_limited_user(
            {"username": "operator", "password": "correct-horse", "identity_id": identity_id}
        )["user"]
        users = list_limited_users()["users"]

        self.assertEqual(len(users), 1)
        self.assertNotIn("password_hash", users[0])
        self.assertEqual(users[0]["phone_number"], self.first_number)

        token = auth.create_session_token(
            created["username"],
            600,
            role="limited",
            user_id=created["id"],
            session_version=created["session_version"],
        )
        principal = principal_from_session(auth.verify_session_payload(token))
        self.assertEqual(principal["phone_number"], self.first_number)

        admin_bootstrap = _bootstrap(self.conn)
        limited_bootstrap = _bootstrap(self.conn, principal)
        self.assertEqual(
            admin_bootstrap["limited_assignments"],
            [
                {
                    "user_id": created["id"],
                    "username": "operator",
                    "identity_id": identity_id,
                    "phone_number": self.first_number,
                    "is_active": True,
                }
            ],
        )
        self.assertEqual(limited_bootstrap["limited_assignments"], [])

        preferences = update_limited_user_preferences(
            created["id"], {"theme_family": "unicorn", "theme_mode": "dark"}
        )
        self.assertEqual(preferences, {"theme_family": "unicorn", "theme_mode": "dark"})
        self.assertEqual(preferences, limited_user_preferences(created["id"]))
        legacy_preferences = update_limited_user_preferences(
            created["id"], {"theme_family": "girly", "theme_mode": "light"}
        )
        self.assertEqual(legacy_preferences, {"theme_family": "unicorn", "theme_mode": "light"})

        updated = update_limited_user(
            created["id"],
            {
                "username": "operator",
                "identity_id": self._identity_id(self.second_number),
                "is_active": True,
            },
        )["user"]
        self.assertGreater(updated["session_version"], created["session_version"])
        self.assertIsNone(principal_from_session(auth.verify_session_payload(token)))

    def test_limited_send_rejects_a_different_sender_before_provider_call(self) -> None:
        with patch("texting_app.server.send_provider_message") as send:
            with self.assertRaisesRegex(ValueError, "assigned number"):
                send_api_message(
                    {
                        "from_number": self.second_number,
                        "to_numbers": ["+12075551234"],
                        "text": "Not allowed",
                    },
                    assigned_phone=self.first_number,
                )
        send.assert_not_called()


class _LimitedUserApiHandler(TextingHandler):
    def log_message(self, _format: str, *_args) -> None:
        pass


class LimitedUserHttpTests(unittest.TestCase):
    first_number = "+15550000001"
    second_number = "+15550000002"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.stack = ExitStack()
        original_hash_password = auth.hash_password
        self.stack.enter_context(patch.object(config, "DATA_DIR", root))
        self.stack.enter_context(patch.object(config, "MEDIA_DIR", root / "media"))
        self.stack.enter_context(patch.object(config, "DB_PATH", root / "switchboard.sqlite"))
        self.stack.enter_context(
            patch.object(config, "PERSONAL_NUMBERS", [self.first_number, self.second_number])
        )
        self.stack.enter_context(patch.object(config, "DEFAULT_IDENTITY_LABELS", {}))
        self.stack.enter_context(patch.object(config, "AUTH_USERNAME", "admin"))
        self.stack.enter_context(
            patch.object(config, "AUTH_PASSWORD_HASH", original_hash_password("admin-pass", 1_000))
        )
        self.stack.enter_context(patch.object(config, "AUTH_SECRET_KEY", "http-session-secret"))
        self.stack.enter_context(patch.object(config, "AUTH_DISABLED", False))
        self.stack.enter_context(patch.object(config, "AUTH_TOTP_SECRET", ""))
        self.stack.enter_context(patch.object(config, "AUTH_BACKUP_CODE_HASHES", []))
        self.stack.enter_context(
            patch(
                "texting_app.server.auth.hash_password",
                lambda value: original_hash_password(value, 1_000),
            )
        )
        conn = connect()
        init_db(conn)
        first_id = int(
            conn.execute(
                "SELECT id FROM identities WHERE phone_number = ?", (self.first_number,)
            ).fetchone()[0]
        )
        other_conversation = ensure_conversation(
            conn, ["+12075559876"], [self.second_number]
        )
        group_conversation = ensure_conversation(
            conn,
            ["+12075551111", "+12075552222"],
            [self.first_number],
        )
        upsert_message(
            conn,
            conversation_id=other_conversation,
            direction="inbound",
            from_number="+12075559876",
            to_numbers=[self.second_number],
            cc_numbers=[],
            text="Other line",
            occurred_at="2026-08-01T10:00:00-04:00",
        )
        other_contact_id = ensure_contact_for_phone(conn, "+12075559876")
        conn.execute(
            "UPDATE contacts SET display_name = ?, updated_at = ? WHERE id = ?",
            ("Other Line Secret", "2026-08-01T10:00:00-04:00", other_contact_id),
        )
        conn.commit()
        conn.close()
        self.other_conversation = other_conversation
        self.group_conversation = group_conversation
        create_limited_user(
            {"username": "operator", "password": "operator-pass", "identity_id": first_id}
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _LimitedUserApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.stack.close()
        self.temp_dir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        cookie: str = "",
        body: dict | None = None,
    ) -> tuple[int, dict, str]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        raw = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=raw, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read() or b"{}")
        set_cookie = response.getheader("Set-Cookie") or ""
        status = response.status
        connection.close()
        return status, payload, set_cookie

    def test_limited_login_routes_and_preferences_are_enforced(self) -> None:
        status, payload, set_cookie = self.request(
            "POST",
            "/api/auth/login",
            body={"username": "operator", "password": "operator-pass"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        cookie = set_cookie.split(";", 1)[0]

        status, payload, _ = self.request(
            "POST",
            f"/api/conversations/{self.group_conversation}/title",
            cookie=cookie,
            body={"title": "Operator group"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["conversation"]["custom_title"], "Operator group")

        status, bootstrap_payload, _ = self.request("GET", "/api/bootstrap", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(bootstrap_payload["access"]["limited"])
        self.assertEqual(
            [identity["phone_number"] for identity in bootstrap_payload["identities"]],
            [self.first_number],
        )

        status, contacts_payload, _ = self.request("GET", "/api/contacts", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(contacts_payload["contacts"], [])

        status, contact_payload, _ = self.request(
            "POST",
            "/api/contacts/name",
            cookie=cookie,
            body={
                "phone_number": "+12075559876",
                "display_name": "Should Not Be Visible",
            },
        )
        self.assertEqual(status, 404)
        self.assertIn("Contact not found", contact_payload["error"])

        status, settings_payload, _ = self.request("GET", "/api/settings", cookie=cookie)
        self.assertEqual(status, 403)
        self.assertIn("Administrator", settings_payload["error"])

        status, _, _ = self.request(
            "GET",
            f"/api/conversations/{self.other_conversation}/messages",
            cookie=cookie,
        )
        self.assertEqual(status, 404)

        status, preferences, _ = self.request(
            "POST",
            "/api/preferences",
            cookie=cookie,
            body={"theme_family": "midnight", "theme_mode": "dark"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            preferences, {"theme_family": "midnight", "theme_mode": "dark"}
        )

        status, send_payload, _ = self.request(
            "POST",
            "/api/messages",
            cookie=cookie,
            body={
                "from_number": self.second_number,
                "to_numbers": ["+12075551234"],
                "text": "Blocked sender",
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("assigned number", send_payload["error"])


if __name__ == "__main__":
    unittest.main()
