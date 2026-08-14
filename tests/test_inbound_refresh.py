from __future__ import annotations

import re
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from texting_app import config
from texting_app.db import connect, ensure_conversation, init_db, upsert_message
from texting_app.server import _get_messages, _list_conversations, refresh_state


APP_JS_PATH = Path(__file__).resolve().parents[1] / "static" / "app.js"


class InboundRefreshTests(unittest.TestCase):
    sender = "+15550000001"
    remote = "+12075550101"

    def test_inbound_message_changes_refresh_tokens_and_is_immediately_queryable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "switchboard.sqlite"
            with patch.object(config, "DB_PATH", database_path), patch.object(
                config,
                "PERSONAL_NUMBERS",
                [self.sender],
            ):
                with closing(connect()) as conn:
                    init_db(conn)
                    conversation_id = ensure_conversation(conn, [self.remote], [self.sender])
                    conn.commit()

                query = {"conversation_id": [str(conversation_id)]}
                before = refresh_state(query)["tokens"]

                with closing(connect()) as conn:
                    upsert_message(
                        conn,
                        conversation_id=conversation_id,
                        direction="inbound",
                        from_number=self.remote,
                        to_numbers=[self.sender],
                        cc_numbers=[],
                        text="Arrived during polling",
                        occurred_at="2026-08-12T09:00:00-04:00",
                    )
                    conn.commit()

                after = refresh_state(query)["tokens"]
                with closing(connect()) as conn:
                    conversations = _list_conversations(conn, {})["conversations"]
                    thread = _get_messages(conn, conversation_id)

                self.assertNotEqual(after["list"], before["list"])
                self.assertNotEqual(after["conversation"], before["conversation"])
                self.assertEqual(conversations[0]["last_text"], "Arrived during polling")
                self.assertEqual(thread["messages"][-1]["text"], "Arrived during polling")

    def test_visible_client_polls_within_five_seconds_and_reconciles_without_priming(self) -> None:
        source = APP_JS_PATH.read_text()

        minimum = re.search(r"const MIN_AUTO_REFRESH_SECONDS = (\d+);", source)
        self.assertIsNotNone(minimum)
        self.assertLessEqual(int(minimum.group(1)), 5)
        self.assertIn("state.autoRefreshSeconds * 1000", source)
        self.assertNotIn("pollForChanges({ prime:", source)
        self.assertIn(
            "const listChanged = force || !hasPrevious || previous.list !== state.refreshTokens.list;",
            source,
        )
        self.assertIn("Number(previousConversationId) !== Number(conversationId)", source)
        self.assertIn(
            "state.refreshTokens = { ...previous, conversation: next.conversation };",
            source,
        )


if __name__ == "__main__":
    unittest.main()
