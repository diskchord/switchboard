from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STYLES_PATH = ROOT / "static" / "styles.css"


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


if __name__ == "__main__":
    unittest.main()
