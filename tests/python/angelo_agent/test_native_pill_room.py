# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The transcript ends above every pill drawn over the composer (chat_card_draw.pills_room).

The standing script consent's line (Allow for this session's "Turn off") and the changes pill are stacked from 8
points above the composer. The transcript reserved room for the changes pill only, so the consent line lay over the
transcript's last rows: in a narrow chat a user bubble's Restore sat under Turn off, and the first hit took the press
(break-it review of ca6f5ff137d, S4). The room is now reserved for every pill shown.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

from angelo_agent import chat_card_draw

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


class PillRoom(unittest.TestCase):
    def test_every_shown_pill_lies_below_the_transcript(self):
        for changes in (False, True):
            for consent in (None, ("Scripts run without asking in this chat", "session")):
                with self.subTest(changes=changes, consent=bool(consent)):
                    shown = int(changes) + int(consent is not None)
                    bottom = chat_card_draw.PILL_TOP + chat_card_draw.PILL_STEP * max(0, shown - 1) + \
                        chat_card_draw.PILL_H if shown else 0
                    room = chat_card_draw.pills_room(changes, consent)
                    self.assertGreaterEqual(room - bottom, 20, "the last pill ends 20 points or more below it")

    def test_the_room_without_the_consent_line_is_as_before(self):
        self.assertEqual(chat_card_draw.pills_room(False, None), 28)
        self.assertEqual(chat_card_draw.pills_room(True, None), 66)

    def test_the_chat_reserves_the_room_for_the_pills_it_draws(self):
        source = (ADDON / "chat_view.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        self.assertIn("chat_card_draw.pills_room", calls)
        self.assertNotIn("(66 if changed or changed_files else 28)", source, "one rule for the room")


if __name__ == "__main__":
    unittest.main()
