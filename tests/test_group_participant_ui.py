from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "static" / "app.js"
STYLES_PATH = ROOT / "static" / "styles.css"


class GroupParticipantUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = SCRIPT_PATH.read_text()
        self.styles = STYLES_PATH.read_text()

    def test_member_rows_offer_rename_and_color_controls(self) -> None:
        self.assertIn('data-rename-group-member=', self.script)
        self.assertIn('data-group-member-color=', self.script)
        self.assertIn("function openGroupMemberContactRename(phone)", self.script)
        self.assertIn("async function saveGroupMemberColor(phone, rawColor)", self.script)
        self.assertIn(
            "`/api/conversations/${source.id}/participants/color`",
            self.script,
        )
        self.assertIn("groupMemberContactEditingPhone", self.script)

    def test_inbound_group_messages_use_the_sender_color(self) -> None:
        self.assertIn('message.sender_color', self.script)
        self.assertIn('--message-participant:', self.script)
        self.assertRegex(
            self.styles,
            r"\.message-row\.inbound\.participant-colored\s+\.message-bubble\s*\{"
            r"[^}]*var\(--message-participant\)",
        )

    def test_large_groups_are_summarized_and_width_contained(self) -> None:
        self.assertIn("const GROUP_HEADER_MEMBER_LIMIT = 3", self.script)
        self.assertIn("groupParticipantOverflowHtml", self.script)
        self.assertIn('data-group-members-open', self.script)

        header = re.search(r"\.thread-header\s*\{([^}]*)\}", self.styles, re.DOTALL)
        title_wrap = re.search(r"\.thread-title-wrap\s*\{([^}]*)\}", self.styles, re.DOTALL)
        participant_row = re.search(r"\.participant-row\s*\{([^}]*)\}", self.styles, re.DOTALL)
        self.assertIsNotNone(header)
        self.assertIsNotNone(title_wrap)
        self.assertIsNotNone(participant_row)
        self.assertIn("min-width: 0", header.group(1))
        self.assertIn("overflow: hidden", header.group(1))
        self.assertIn("min-width: 0", title_wrap.group(1))
        self.assertIn("overflow: hidden", participant_row.group(1))
        self.assertGreaterEqual(
            self.styles.count("grid-template-columns: minmax(0, 1fr)"),
            2,
        )


if __name__ == "__main__":
    unittest.main()
