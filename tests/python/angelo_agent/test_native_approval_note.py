# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Each request tells the model the chat's approval mode where it changed (``approval_note``, a ``TurnBlocks`` block).

The 2.0.1 acceptance rerun (3b2b492bb4a): right after the person switched a conversation from Read only to Ask, the
agent's first reply still acted as Read only. The mode was enforced right (at each tool's dispatch), but nothing in
the request said it changed: the model had only its earlier refusals. Contract: doc/angelo/agent_integration.rst.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import approval_note, context_ledger  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402


class RequestContextTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.c = Controller(folder.name)
        self.addCleanup(self.c.close)
        self.c.set_document(("approval-note", 1))

    def message(self):
        return self.c._context_blocks(self.c.runtime_context(), "Make a cube", None, None, None)

    def test_the_request_after_the_person_s_switch_says_the_mode_now(self):
        """Failed on e528acda14d: no request said the approval mode."""
        self.c.set_options(approval="READ_ONLY")
        self.assertIn("Approval mode now: Read only", self.message())
        self.c.set_options(approval="ASK")   # The person's switch in Angelo.
        text = self.message()
        self.assertIn("Approval mode now: Ask before changes", text)
        self.assertIn("An earlier Read only no longer applies", text)
        self.assertNotIn("Approval mode now: Read only", text)


class DeltaTests(unittest.TestCase):
    def blocks(self, received=None):
        return context_ledger.TurnBlocks(received)

    def test_said_only_where_it_changed_for_this_context(self):
        first = self.blocks()
        self.assertIn("Read only", approval_note.block(first, "READ_ONLY"))
        received = dict(first.after, native_id="n1")
        self.assertEqual(approval_note.block(self.blocks(received), "READ_ONLY"), "", "unchanged: not repeated")
        self.assertIn("Ask before changes", approval_note.block(self.blocks(received), "ASK"), "changed: said")

    def test_a_fresh_context_in_the_default_mode_gets_no_line_but_a_later_change_does(self):
        fresh = self.blocks()
        self.assertEqual(approval_note.block(fresh, "AUTO_BOUNDED"), "", "the default needs no words")
        received = dict(fresh.after, native_id="n1")
        self.assertIn("Read only", approval_note.block(self.blocks(received), "READ_ONLY"))

    def test_the_ledger_keeps_what_the_context_received(self):
        blocks = self.blocks()
        approval_note.block(blocks, "ASK")
        kept = context_ledger.clean(dict(blocks.after, native_id="n1"))
        self.assertEqual(kept["approval"], blocks.after["approval"], "persisted with the context's ledger")


if __name__ == "__main__":
    unittest.main()
