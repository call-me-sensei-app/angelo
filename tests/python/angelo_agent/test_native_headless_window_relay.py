# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Relayed approvals of a hidden-window session (``headless_window_relay``), read-only: the card relayed in full so
the app's human knows what waits, and nothing the app can answer with (break-it review 2026-11-18, M1/M2). Contract:
doc/angelo/mcp_headless_window.rst, "Relayed approvals"."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import headless_window_relay as relay

SCRIPT = "import bpy\nbpy.data.objects['Box'].location.x = 1\n"


def chat(name="angelo_execute_python", always=False):
    call = SimpleNamespace(id="chat1/call-1", name=name, approval=True, invocation_digest="digest-1",
                           arguments={"code": SCRIPT} if name == "angelo_execute_python" else {"size": 2})
    controller = SimpleNamespace(state={"id": "chat1"}, pending={call.id: call},
                                 offers_always_allow=lambda c: always)
    return controller, call


class RelayTest(unittest.TestCase):
    def test_a_script_card_is_relayed_in_full(self):
        controller, call = chat()
        row = relay.relayed(controller, call)
        self.assertEqual(row["tool"], "angelo_execute_python")
        self.assertEqual(row["script"], SCRIPT, "the exact script, as its card's review shows it")
        self.assertEqual(row["choices"], ["approve once", "decline"])
        self.assertEqual(row["approved_by"], "the person, in Angelo")
        self.assertIn("angelo_show", row["how"])
        self.assertIn("you can never approve it", row["how"])

    def test_an_edit_card_carries_its_exact_arguments(self):
        controller, call = chat(name="angelo_create_cube")
        row = relay.relayed(controller, call)
        self.assertEqual(row["arguments"], {"size": 2})
        self.assertNotIn("script", row)

    def test_the_cards_standing_choice_is_shown_for_what_it_is(self):
        controller, call = chat(always=True)
        self.assertIn("always allow scripts", relay.relayed(controller, call)["choices"])

    def test_the_card_names_this_exact_call_and_changes_with_it(self):
        controller, call = chat()
        first = relay.relayed(controller, call)["card"]
        self.assertEqual(len(first), 16)
        self.assertEqual(relay.card_id(call), first)
        call.invocation_digest = "digest-2"   # Its exact arguments changed: another card.
        self.assertNotEqual(relay.card_id(call), first)

    def test_nothing_in_the_row_can_approve_the_card(self):
        controller, call = chat()
        text = json.dumps(relay.relayed(controller, call))
        self.assertNotIn("digest-1", text, "no digest or id the app's model could hand back")
        self.assertNotIn("call-1", text)
        self.assertNotIn('"id"', text)
        self.assertFalse(hasattr(relay, "answer") or hasattr(relay, "approve"), "no approve from the app")


if __name__ == "__main__":
    unittest.main()
