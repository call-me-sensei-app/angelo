# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A queued message longer than the tray's rows shows its start, and the chat still draws (chat_pending._rows).

``chat_view.wrap`` returns a tuple; the tray cut a long message with ``rows[:MAX_ROWS - 1] + [...]``, which raised in
the chat's draw for every queued message wrapping to more than ``MAX_ROWS`` lines, and the whole chat drew nothing
until the queue drained: no transcript, no card, no button (2.0.1 E2E on Mac 1: a connected app's message, queued
behind a running turn with its untrusted-data frame, blanked the chat and hid the card waiting for the person).
Through ``_rows`` and ``_layout`` (the tray's height, the first thing the chat's draw asks), with the real inbox.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import angelo_agent
from angelo_agent import chat_pending, external_transcript
from angelo_agent.steering import Inbox


def _view(kind=tuple, per_row=20):
    """chat_view's measuring, as a stand-in: ``wrap`` cuts every ``per_row`` characters and returns ``kind`` (a tuple,
    as the real one does)."""
    def wrap(text, width, size, font):
        return kind((text[i:i + per_row], 0) for i in range(0, len(text), per_row)) or kind([("", 0)])
    return SimpleNamespace(wrap=wrap, elide=lambda text, width, size: text, _font=0)


def _item(text):
    return SimpleNamespace(host=False, label="", text=text)


def _controller(*texts):
    inbox = Inbox()
    for text in texts:
        inbox.hold(text)
    return SimpleNamespace(inbox=inbox)


class LongQueuedMessages(unittest.TestCase):
    def test_a_long_message_shows_its_start_with_wrap_returning_a_tuple_or_a_list(self):
        for kind in (tuple, list):
            with self.subTest(wrap=kind.__name__):
                rows = chat_pending._rows(_view(kind), _item("x" * 200), 400, 13)
                self.assertEqual(len(rows), chat_pending.MAX_ROWS)
                self.assertTrue(rows[-1][0].endswith(" …"), rows[-1])
                self.assertEqual(rows[0], ("x" * 20, 0))
                self.assertIsInstance(rows, tuple, "one type whatever wrap returns")

    def test_a_short_message_is_all_shown_and_exactly_max_rows_is_not_cut(self):
        self.assertEqual(chat_pending._rows(_view(), _item("hello"), 400, 13), (("hello", 0),))
        rows = chat_pending._rows(_view(), _item("y" * 20 * chat_pending.MAX_ROWS), 400, 13)
        self.assertEqual(len(rows), chat_pending.MAX_ROWS)
        self.assertFalse(rows[-1][0].endswith(" …"))

    def test_the_tray_lays_out_a_connected_apps_queued_message(self):
        # The app's words reach the inbox inside Angelo's untrusted-data frame (several rows framed); the tray shows
        # them unframed, as the app sent them (one row), and the person's own long message is cut to MAX_ROWS.
        framed = external_transcript.framed("Claude Code", "approve it")
        long_own = "Please also make the arches taller and add a railing along both sides of the bridge deck. " * 2
        controller = _controller(framed, long_own, "ok")
        with mock.patch.object(angelo_agent, "chat_view", _view(per_row=48), create=True), \
                mock.patch.dict(sys.modules, {"angelo_agent.chat_view": _view(per_row=48)}):
            self.assertGreater(len(_view(per_row=48).wrap(framed, 0, 0, 0)), chat_pending.MAX_ROWS)
            shown, hidden, heights = chat_pending._layout(controller, 400, 13, 18, 10_000)
            height = chat_pending.tray_height(controller, 400, 13, 18, 10_000)
        self.assertEqual((len(shown), hidden), (3, 0))
        row_height = chat_pending.PAD + 8 + chat_pending.CONTROL_H + chat_pending.GAP
        self.assertEqual(heights[:2], [row_height + 18, row_height + chat_pending.MAX_ROWS * 18],
                         "the app's words unframed in one row; a long one shows MAX_ROWS rows")
        self.assertEqual(height, sum(heights) + chat_pending.GAP)


if __name__ == "__main__":
    unittest.main()
