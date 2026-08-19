from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STYLES_PATH = ROOT / "static" / "styles.css"
ACTIVITY_PATH = (
    ROOT
    / "mobile"
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "example"
    / "texting"
    / "MainActivity.java"
)


class KeyboardLayoutTests(unittest.TestCase):
    def test_wide_layout_uses_keyboard_adjusted_viewport(self) -> None:
        styles = STYLES_PATH.read_text()
        wide_keyboard_rules = re.search(
            r"@media\s*\(min-width:\s*761px\)\s*\{"
            r"(?P<rules>.*?)"
            r"\n\}",
            styles,
            re.DOTALL,
        )

        self.assertIsNotNone(wide_keyboard_rules)
        rules = wide_keyboard_rules.group("rules")
        self.assertRegex(
            rules,
            r"body\.keyboard-open\s+\.app-shell\s*\{[^}]*"
            r"height:\s*calc\(var\(--visual-viewport-height,\s*100dvh\)\s*-\s*"
            r"var\(--layout-keyboard-inset,\s*0px\)\)",
        )
        self.assertRegex(
            rules,
            r"body\.keyboard-open\s+\.thread-pane\s*\{[^}]*"
            r"height:\s*100%;[^}]*max-height:\s*100%",
        )

    def test_contact_name_dialog_uses_keyboard_adjusted_viewport(self) -> None:
        styles = STYLES_PATH.read_text()

        self.assertRegex(
            styles,
            r"body\.keyboard-open\s+\.contact-name-modal\s*\{[^}]*"
            r"top:\s*var\(--visual-viewport-offset-top,\s*0px\);[^}]*"
            r"bottom:\s*auto;[^}]*"
            r"height:\s*calc\(var\(--visual-viewport-height,\s*100dvh\)\s*-\s*"
            r"var\(--layout-keyboard-inset,\s*0px\)\)",
        )

    def test_android_back_handler_recognizes_group_members_dialog(self) -> None:
        activity = ACTIVITY_PATH.read_text()

        self.assertIn("document.querySelector('#groupMembersModal')", activity)
        self.assertIn(
            "!document.querySelector('#groupMembersModal').classList.contains('hidden')",
            activity,
        )


if __name__ == "__main__":
    unittest.main()
