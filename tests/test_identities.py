from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from texting_app import config
from texting_app import settings
from texting_app.db import connect, init_db
from texting_app.messaging import provider_for_number
from texting_app.server import create_identity


class IdentityCreationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, "DATA_DIR", root))
        self.stack.enter_context(patch.object(config, "MEDIA_DIR", root / "media"))
        self.stack.enter_context(patch.object(config, "DB_PATH", root / "switchboard.sqlite"))
        self.stack.enter_context(patch.object(config, "PERSONAL_NUMBERS", ["+15550000001"]))
        self.stack.enter_context(patch.object(config, "DEFAULT_IDENTITY_LABELS", {"+15550000001": "Legacy"}))
        self.stack.enter_context(patch.object(config, "MESSAGING_PROVIDER", "telnyx"))
        self.stack.enter_context(
            patch.object(config, "MESSAGING_PROVIDER_BY_NUMBER", {"+15550000001": "twilio"})
        )
        setting_defs = dict(settings.SETTINGS_BY_KEY)
        setting_defs["messaging.provider"] = replace(
            setting_defs["messaging.provider"], default="telnyx"
        )
        setting_defs["messaging.provider_by_number"] = replace(
            setting_defs["messaging.provider_by_number"],
            default=json.dumps({"+15550000001": "twilio"}, separators=(",", ":")),
        )
        self.stack.enter_context(patch.object(settings, "SETTINGS_BY_KEY", setting_defs))

    def tearDown(self) -> None:
        self.stack.close()
        self.temp_dir.cleanup()

    def test_database_identity_coexists_with_legacy_env_seed_and_provider_map(self) -> None:
        result = create_identity(
            {"phone_number": "+1-603-509-9032", "label": "New Hampshire", "provider": "telnyx"}
        )

        self.assertEqual(result["identity"]["phone_number"], "+16035099032")
        self.assertEqual(result["identity"]["label"], "New Hampshire")
        self.assertEqual(provider_for_number("+15550000001"), "twilio")
        self.assertEqual(provider_for_number("+16035099032"), "telnyx")

        conn = connect()
        init_db(conn)
        rows = conn.execute("SELECT phone_number, label FROM identities ORDER BY id").fetchall()
        self.assertEqual(
            [(row["phone_number"], row["label"]) for row in rows],
            [("+15550000001", "Legacy"), ("+16035099032", "New Hampshire")],
        )
        saved_map = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'messaging.provider_by_number'"
        ).fetchone()
        self.assertEqual(
            json.loads(saved_map["value"]),
            {"+15550000001": "twilio", "+16035099032": "telnyx"},
        )
        conn.close()

    def test_duplicate_normalized_number_is_rejected(self) -> None:
        create_identity({"phone_number": "+1 (603) 509-9032", "provider": "twilio"})

        with self.assertRaisesRegex(ValueError, "already exists"):
            create_identity({"phone_number": "6035099032", "provider": "twilio"})

    def test_invalid_number_does_not_create_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid phone number"):
            create_identity({"phone_number": "123"})

        conn = connect()
        init_db(conn)
        count = conn.execute("SELECT COUNT(*) FROM identities").fetchone()[0]
        self.assertEqual(count, 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
