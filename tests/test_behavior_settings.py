from __future__ import annotations

import re
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch

from texting_app import config, settings
from texting_app.db import connect, init_db
from texting_app.server import _bootstrap


ROOT = Path(__file__).resolve().parents[1]
APP_JS_PATH = ROOT / "static" / "app.js"

MARK_READ_KEY = "behavior.mark_read_on_open"
DESKTOP_ENTER_KEY = "behavior.enter_to_send_desktop"
MOBILE_ENTER_KEY = "behavior.enter_to_send_mobile"


class BehaviorSettingsTests(unittest.TestCase):
    sender = "+15550000001"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, "DATA_DIR", root))
        self.stack.enter_context(patch.object(config, "MEDIA_DIR", root / "media"))
        self.stack.enter_context(patch.object(config, "DB_PATH", root / "switchboard.sqlite"))
        self.stack.enter_context(patch.object(config, "PERSONAL_NUMBERS", [self.sender]))
        self.stack.enter_context(
            patch.object(config, "DEFAULT_IDENTITY_LABELS", {self.sender: "Primary"})
        )
        settings.invalidate_settings_cache()
        with closing(connect()) as conn:
            init_db(conn)

    def tearDown(self) -> None:
        settings.invalidate_settings_cache()
        self.stack.close()
        self.temp_dir.cleanup()

    def _bootstrap_payloads(self) -> tuple[dict, dict]:
        limited_principal = {
            "role": "limited",
            "user_id": 73,
            "username": "operator",
            "phone_number": self.sender,
            "theme_family": "switchboard",
            "theme_mode": "light",
        }
        with closing(connect()) as conn:
            return _bootstrap(conn), _bootstrap(conn, limited_principal)

    def test_behavior_defaults_are_registered_and_bootstrapped_for_every_role(self) -> None:
        expected_defaults = {
            MARK_READ_KEY: "1",
            DESKTOP_ENTER_KEY: "1",
            MOBILE_ENTER_KEY: "0",
        }
        for key, default in expected_defaults.items():
            with self.subTest(key=key):
                definition = settings.SETTINGS_BY_KEY[key]
                self.assertEqual(definition.section, "Behavior")
                self.assertEqual(definition.kind, "bool")
                self.assertEqual(definition.default, default)

        fields = {
            field["key"]: field
            for section in settings.configured_values()["sections"]
            for field in section["fields"]
        }
        self.assertEqual(fields[MARK_READ_KEY]["value"], "1")
        self.assertEqual(fields[DESKTOP_ENTER_KEY]["value"], "1")
        self.assertEqual(fields[MOBILE_ENTER_KEY]["value"], "0")

        admin, limited = self._bootstrap_payloads()
        for payload in (admin, limited):
            self.assertIs(payload["mark_read_on_open"], True)
            self.assertIs(payload["enter_to_send_desktop"], True)
            self.assertIs(payload["enter_to_send_mobile"], False)
        self.assertEqual(limited["settings"], {"sections": []})

    def test_enter_settings_persist_independently_for_admin_and_limited_bootstrap(self) -> None:
        settings.update_values(
            {
                "settings": {
                    DESKTOP_ENTER_KEY: False,
                    MOBILE_ENTER_KEY: True,
                }
            }
        )

        self.assertFalse(settings.get_bool(DESKTOP_ENTER_KEY, True))
        self.assertTrue(settings.get_bool(MOBILE_ENTER_KEY, False))
        with closing(connect()) as conn:
            stored = {
                row["key"]: row["value"]
                for row in conn.execute(
                    "SELECT key, value FROM app_settings WHERE key IN (?, ?)",
                    (DESKTOP_ENTER_KEY, MOBILE_ENTER_KEY),
                )
            }
        self.assertEqual(stored, {DESKTOP_ENTER_KEY: "0", MOBILE_ENTER_KEY: "1"})

        admin, limited = self._bootstrap_payloads()
        for payload in (admin, limited):
            self.assertIs(payload["enter_to_send_desktop"], False)
            self.assertIs(payload["enter_to_send_mobile"], True)

    def test_saved_mark_read_opt_out_survives_the_new_default(self) -> None:
        settings.update_values({"settings": {MARK_READ_KEY: False}})

        self.assertFalse(settings.get_bool(MARK_READ_KEY, True))
        admin, limited = self._bootstrap_payloads()
        self.assertIs(admin["mark_read_on_open"], False)
        self.assertIs(limited["mark_read_on_open"], False)
        with closing(connect()) as conn:
            saved = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (MARK_READ_KEY,),
            ).fetchone()
        self.assertIsNotNone(saved)
        self.assertEqual(saved["value"], "0")

        settings.update_values({"clear": [MARK_READ_KEY]})
        self.assertTrue(settings.get_bool(MARK_READ_KEY, False))

    def test_composer_enter_contract_is_device_specific_and_preserves_newlines(self) -> None:
        source = APP_JS_PATH.read_text()
        handler_match = re.search(
            r'els\.messageText\.addEventListener\("keydown", \(event\) => \{'
            r"(?P<body>.*?)"
            r"\n  \}\);",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(handler_match)
        handler = handler_match.group("body")

        prevent_index = handler.index("event.preventDefault()")
        send_index = handler.index("sendCurrentMessage()")
        self.assertLess(handler.index("event.key"), prevent_index)
        self.assertLess(handler.index("event.shiftKey"), prevent_index)
        self.assertLess(handler.index("event.isComposing"), prevent_index)
        self.assertLess(prevent_index, send_index)

        enter_helpers = []
        for function_match in re.finditer(
            r"function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{"
            r"(?P<body>.*?)"
            r"\n\}",
            source,
            re.DOTALL,
        ):
            body = function_match.group("body")
            if (
                "enter_to_send_desktop" in body
                and "enter_to_send_mobile" in body
                and "isMobileDevice()" in body
            ):
                enter_helpers.append(function_match.group("name"))

        self.assertTrue(
            enter_helpers,
            "Expected a current-device helper using both Enter-to-send bootstrap settings.",
        )
        helper_calls = [
            handler.find(f"{helper}(")
            for helper in enter_helpers
            if handler.find(f"{helper}(") >= 0
        ]
        self.assertTrue(helper_calls, "The composer keydown handler must consult the device helper.")
        self.assertLess(min(helper_calls), prevent_index)


if __name__ == "__main__":
    unittest.main()
