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
from texting_app.db import connect, ensure_conversation, init_db, upsert_message
from texting_app.server import (
    _bootstrap,
    _get_messages,
    _list_conversations,
    create_limited_user,
    limited_user_preferences,
    list_limited_users,
    principal_from_session,
    refresh_state,
    send_api_message,
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

    def test_reads_are_scoped_to_assigned_number_even_in_a_shared_thread(self) -> None:
        conversation_id, first_message = self._seed_shared_conversation()

        conversations = _list_conversations(
            self.conn, {}, assigned_phone=self.first_number
        )["conversations"]
        thread = _get_messages(
            self.conn, conversation_id, assigned_phone=self.first_number
        )

        self.assertEqual([item["id"] for item in conversations], [conversation_id])
        self.assertEqual([item["id"] for item in thread["messages"]], [first_message])
        self.assertEqual(thread["messages"][0]["text"], "Message for first line")
        self.assertNotIn(
            self.second_number,
            [participant["phone_number"] for participant in thread["conversation"]["participants"]],
        )

    def test_bootstrap_and_refresh_are_scoped_and_unrelated_threads_are_denied(self) -> None:
        conversation_id, _ = self._seed_shared_conversation()
        principal = {
            "role": "limited",
            "username": "operator",
            "phone_number": self.first_number,
            "theme_family": "console",
            "theme_mode": "dark",
        }

        payload = _bootstrap(self.conn, principal)
        refresh = refresh_state(
            {"conversation_id": [str(conversation_id)]}, self.first_number
        )

        self.assertEqual([item["phone_number"] for item in payload["identities"]], [self.first_number])
        self.assertEqual(payload["stats"]["messages"], 1)
        self.assertEqual(payload["preferences"], {"theme_family": "console", "theme_mode": "dark"})
        self.assertTrue(refresh["tokens"]["conversation"])
        with self.assertRaisesRegex(LookupError, "Conversation not found"):
            _get_messages(self.conn, conversation_id + 1, assigned_phone=self.first_number)

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
        conn.commit()
        conn.close()
        self.other_conversation = other_conversation
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

        status, bootstrap_payload, _ = self.request("GET", "/api/bootstrap", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(bootstrap_payload["access"]["limited"])
        self.assertEqual(
            [identity["phone_number"] for identity in bootstrap_payload["identities"]],
            [self.first_number],
        )

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
