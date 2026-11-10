# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine link's request card with the agent's requests (doc/angelo/engine_link.rst, "The agent"), on the real
``engine_link``, ``card_host`` and ``engine_link_ui.RequestCard``; only drawing modules are stand-ins.

Covered (break-it review 2, findings 1 and 7): when an engine's request replaces the agent's, the agent's card leaves
the screen before the engine's is shown, and an answer on a card that is no longer the pending request's changes
nothing (a Not now meant for the agent never refuses the engine); the card says who asked: the engine, Angelo's
agent, or the connected app by its name.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_engine_link_tool as base  # noqa: E402  (its stand-in bpy and fixtures)

engine_link, host, EDIT, BPY = base.engine_link, base.host, base.EDIT, base.BPY


def ui_stubs():
    """card_host's and engine_link_ui's drawing-only imports as stand-ins; update_card.NoticeCard as it is built."""
    BPY.props = mock.MagicMock()
    BPY.utils = mock.MagicMock()
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    stubs = {"gpu": mock.MagicMock(), "bpy.app.handlers": handlers, "bpy.types": BPY.types}
    for name in ("canvas_rtl", "card_draw", "card_kit", "font_watch", "input_policy", "scroll_input", "card_view",
                 "splash_setup", "app_notices"):
        stubs["angelo_agent." + name] = mock.MagicMock()

    class NoticeCard:
        kind, modal, priority, cancel_action = "notice", False, 30, "close"

        def __init__(self, icon, title, body, buttons=(), on_action=None, lifetime=0):
            self.icon, self.title, self.body, self.buttons = icon, title, body, tuple(buttons)
            self.on_action, self.lifetime = on_action, lifetime

        def closed(self, how):
            pass

    update_card = types.ModuleType("angelo_agent.update_card")
    update_card.NoticeCard = NoticeCard
    stubs["angelo_agent.update_card"] = update_card
    return stubs


class RequestCardTests(unittest.TestCase):
    setUp = base.ToolTests.setUp

    def ui(self):
        self.enterContext(mock.patch.dict(sys.modules, ui_stubs()))
        for name in ("angelo_agent.card_host", "angelo_agent.engine_link_ui"):
            sys.modules.pop(name, None)
        card_host = importlib.import_module("angelo_agent.card_host")
        ui = importlib.import_module("angelo_agent.engine_link_ui")
        card_host._queue[:] = []
        card_host._active = None
        hooks = dict(engine_link.hooks)
        self.addCleanup(engine_link.hooks.update, hooks)
        engine_link.hooks.update(request=ui._show_request, withdraw=ui._withdraw_request)
        return card_host, ui

    def shown(self, card_host, ui):
        """Put the queued request card on screen, as the pump does."""
        card = card_host.queued(ui.RequestCard.kind)[0]
        card_host._queue[:] = []
        card_host._active = SimpleNamespace(card=card, window=0, area=0)
        return card

    def test_an_engines_request_takes_the_agents_card_off_the_screen(self):
        card_host, ui = self.ui()
        self.server.editor(1)
        host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.blend"})
        agents = self.shown(card_host, ui)
        engine_link._handle(1, {"type": "open", "id": "7", "project": str(self.project), "asset": "res://crate.glb",
                                "project_name": "Game"})
        self.assertIsNot(card_host.active_card(), agents, "the agent's card left the screen")
        self.assertEqual([card.request["asset"].ident for card in card_host.queued(ui.RequestCard.kind)],
                         ["res://crate.glb"])
        agents._answer("later", SimpleNamespace(close=lambda how: None))   # A click that landed on the old card.
        self.assertEqual(engine_link.pending()["client"], 1, "the engine's request still waits")
        self.assertFalse([header for client, header in self.server.sent
                          if client == 1 and header["type"] == "refused"], "the engine heard no refusal")
        with self.assertRaises(base.RefusalError):   # The agent's request was replaced: it may not ask at once.
            engine_link._state["pending"] = None
            host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.blend"})

    def test_the_card_says_who_asked(self):
        card_host, ui = self.ui()
        self.server.editor(1)
        engine_link._handle(1, {"type": "open", "id": "7", "project": str(self.project), "asset": "res://crate.glb",
                                "project_name": "Game"})
        self.assertEqual(card_host.queued(ui.RequestCard.kind)[-1].title, "Godot wants to edit crate.glb")
        engine_link.decline()
        host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.blend"})
        self.assertEqual(card_host.queued(ui.RequestCard.kind)[-1].title,
                         "Angelo's agent wants to open crate.blend from Godot")
        engine_link._withdraw("opened")   # As if opened: nothing held against the next.
        app = SimpleNamespace(external_client="claude-code", _request_generation=1)
        host.execute(None, EDIT, {"action": "OPEN", "asset": "res://crate.glb"}, app)
        self.assertEqual(card_host.queued(ui.RequestCard.kind)[-1].title,
                         "claude-code wants to open crate.glb from Godot")


if __name__ == "__main__":
    unittest.main()
