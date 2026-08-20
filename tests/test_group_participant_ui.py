from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "static" / "app.js"
STYLES_PATH = ROOT / "static" / "styles.css"
INDEX_PATH = ROOT / "static" / "index.html"


class GroupParticipantUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = SCRIPT_PATH.read_text()
        self.styles = STYLES_PATH.read_text()
        self.index = INDEX_PATH.read_text()

    def test_core_asset_revisions_advance_together_after_group_ui_changes(self) -> None:
        script_revision = re.search(r'<script\s+src="/static/app\.js\?v=([^"&]+)"', self.index)
        style_revision = re.search(r'<link\s+rel="stylesheet"\s+href="/static/styles\.css\?v=([^"&]+)"', self.index)

        self.assertIsNotNone(script_revision)
        self.assertIsNotNone(style_revision)
        self.assertTrue(script_revision.group(1))
        self.assertEqual(script_revision.group(1), style_revision.group(1))
        self.assertNotEqual(script_revision.group(1), "tablet-keyboard-inset-1")

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

    def test_current_participant_name_overrides_stale_cached_message_metadata(self) -> None:
        render_messages = self.script.split("function renderMessages(", 1)[1].split(
            "function watchMessageMediaForScrollMode",
            1,
        )[0]

        self.assertIn("const currentSender = participantByPhone(message.from_number)", render_messages)
        self.assertRegex(
            render_messages,
            r"const\s+senderDisplay\s*=\s*participantSavedName\(currentSender\)\s*\|\|\s*"
            r"message\.from_display",
        )
        self.assertIn("escapeHtml(senderDisplay", render_messages)

    def test_conversation_metadata_updates_are_merged_into_cached_threads(self) -> None:
        merge_loaded = self.script.split("function mergeConversationIntoLoadedState", 1)[1].split(
            "function markLoadedConversationRead",
            1,
        )[0]

        self.assertIn("state.threadCache.get(conversationId)", merge_loaded)
        self.assertIn("state.threadCache.set(conversationId", merge_loaded)
        self.assertRegex(
            merge_loaded,
            r"conversation:\s*\{\s*\.\.\.cached\.conversation,\s*\.\.\.update\s*\}",
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

    def test_member_list_opens_without_focusing_the_add_input(self) -> None:
        open_members = self.script.split("function openGroupMembersModal()", 1)[1].split(
            "async function createGroupMembershipBranch()",
            1,
        )[0]

        self.assertIn(
            "els.groupMembersModal?.focus({ preventScroll: true })",
            open_members,
        )
        self.assertIn("blurActiveElementWithin(document)", open_members)
        self.assertNotIn("els.groupMemberInput?.focus()", open_members)

    def test_closing_member_and_contact_dialogs_blurs_their_active_element(self) -> None:
        close_members = self.script.split("function closeGroupMembersModal", 1)[1].split(
            "function openGroupMembersModal()",
            1,
        )[0]
        close_contact = self.script.split("function closeContactNameModal", 1)[1].split(
            "function openContactNameModal",
            1,
        )[0]

        self.assertIn("function blurActiveElementWithin(container)", self.script)
        self.assertIn("blurActiveElementWithin(els.groupMembersModal)", close_members)
        self.assertIn("blurActiveElementWithin(els.contactNameModal)", close_contact)

    def test_member_rename_keeps_contact_name_input_focus_and_selection(self) -> None:
        open_contact = self.script.split("function openContactNameModal", 1)[1].split(
            "function valueIsTruthy",
            1,
        )[0]

        self.assertIn("els.contactNameModalInput.focus()", open_contact)
        self.assertIn("els.contactNameModalInput.select()", open_contact)


if __name__ == "__main__":
    unittest.main()
