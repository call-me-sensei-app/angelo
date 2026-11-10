# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hidden approvals stay discoverable; opening the chat never supplies an approval."""
import suite_isolation  # noqa: F401
import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import approval_wait


def chat(identity, **flags):
    call = NS(id=identity + "/call", approval=True, **flags)
    return NS(state={"id": identity}, pending={call.id: call})


class HiddenWait(unittest.TestCase):
    def test_reopening_shields_the_current_draws_larger_viewport(self):
        path = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/chat_view.py"
        draw = next(node for node in ast.parse(path.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "_draw_chat")
        statements = []
        for node in ast.walk(draw):
            if not isinstance(node, ast.Assign):
                continue
            targets = [ast.unparse(target) for target in node.targets]
            viewport = "message_viewport" in targets or "state['message_viewport']" in targets
            place = isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == "approval_wait.place"
            if viewport or place:
                statements.append(node)
        statements.sort(key=lambda node: node.lineno)
        self.assertEqual(len(statements), 3)
        controller = chat("a")
        approval_wait.request_reveal("a", next(iter(controller.pending.values())), shield=True, controller=controller)
        state = {"message_viewport": (0, 100, 300, 200)}
        namespace = {"approval_wait": approval_wait, "state": state, "controller": controller,
                     "message_bottom": 10, "width": 300, "height": 500, "bar": 0,
                     "blocks": [], "scroll": 0, "visible": 490}
        exec(compile(ast.Module(body=statements, type_ignores=[]), str(path), "exec"), namespace)
        self.assertEqual(state["approval_shield"], ((0, 10, 300, 490), None))
        self.assertFalse(approval_wait.pending_shield("a"))

    def tearDown(self):
        approval_wait._reveal.clear()
        getattr(approval_wait, "_shield_pending", {}).clear()

    def test_only_shown_active_chat_is_seen(self):
        a, b = chat("a"), chat("b")
        self.assertEqual(approval_wait.unseen([a, b], "a", False), [a, b])
        self.assertEqual(approval_wait.unseen([a, b], "a", True), [b])

    def test_only_unanswered_cards_are_unseen(self):
        for flag in ("authorized", "executing", "approved", "automatic", "revoked", "consent_approved"):
            with self.subTest(flag=flag):
                self.assertEqual(approval_wait.unseen([chat("a", **{flag: True})], None, False), [])
        self.assertEqual(approval_wait.unseen([chat("a", needs_person=False)], None, False), [])

    def test_reopening_shields_before_first_draw_without_authorizing(self):
        controller = chat("a")
        approval_wait.request_reveal("a", approval_wait.awaiting(controller), shield=True)
        self.assertTrue(approval_wait.pending_shield("a"))
        self.assertFalse(approval_wait.pending_shield("b"))
        state = {"message_viewport": (0, 10, 300, 400)}
        approval_wait._place(state, controller, [("approval", None, [], 100, "a/call", None)], 0, 400)
        self.assertEqual(state["approval_shield"], ((0, 10, 300, 400), None))
        self.assertFalse(approval_wait.pending_shield("a"))
        self.assertFalse(any(getattr(c, "authorized", False) for c in controller.pending.values()))

    def test_pending_reveal_rejects_pointer_and_keyboard_hits(self):
        from angelo_agent import canvas_controls
        controller = chat("a")
        approval_wait.request_reveal("a", approval_wait.awaiting(controller), shield=True)
        hit = ((0, 0, 100, 30), "approve", "a/call")
        state = {"session": "a", "hits": [hit], "focus": ("approve", "a/call")}
        canvas_controls.press(lambda: None, state, hit)
        self.assertEqual(state["hits"], [])
        self.assertNotIn("pressed", state)
        self.assertFalse(canvas_controls.key(NS(type="RET", value="PRESS"), state, lambda: None))


    def test_every_shielded_route_invalidates_the_previous_chats_canvas(self):
        from angelo_agent.chat_hit_testing import release_active
        old_hit = ((0, 0, 100, 30), "approve", "a/call")
        state = {"session": "a", "hits": [old_hit], "focus": ("approve", "a/call"), "focused": True}
        module = NS(_views={7: state})
        controller = chat("b")
        with patch.dict(sys.modules, {"angelo_agent.chat_view": module}):
            approval_wait.request_reveal("b", approval_wait.awaiting(controller), shield=True)
        self.assertEqual(state["hits"], [])
        self.assertNotIn("focus", state)
        self.assertFalse(state["focused"])
        self.assertTrue(state["blur_request"])
        self.assertFalse(release_active(old_hit, state["hits"], 10, 10))


    def test_completed_card_clears_pending_shield(self):
        controller = chat("a")
        approval_wait.request_reveal("a", approval_wait.awaiting(controller), shield=True)
        controller.pending.clear()
        approval_wait._place({}, controller, [], 0, 400)
        self.assertTrue(approval_wait.pending_shield("a"), "no viewport has drawn to own the shield yet")
        state = {"message_viewport": (0, 0, 300, 400)}
        approval_wait._place(state, controller, [], 0, 400)
        self.assertEqual(state["approval_shield"], ((0, 0, 300, 400), None))
        self.assertFalse(approval_wait.pending_shield("a"))

    def test_old_reveal_never_scrolls_to_a_replacement_card(self):
        controller = chat("a")
        approval_wait.request_reveal("a", approval_wait.awaiting(controller), shield=True)
        controller.pending = {"new": NS(id="new", approval=True)}
        state = {"message_viewport": (0, 0, 300, 400)}
        self.assertEqual(approval_wait._place(state, controller, [("approval", None, [], 100, "new", None)], 17, 400), 17)
        self.assertFalse(approval_wait.pending_shield("a"))
        self.assertEqual(state["approval_shield"], ((0, 0, 300, 400), None))


    def test_current_request_still_reveals_its_card(self):
        from angelo_agent.pending_delivery import ToolCall
        controller = chat("a")
        controller._epoch, controller._request_generation, controller._worker_request = 3, 7, (3, 7)
        call = ToolCall("a/call", "angelo_test", {}, None, approval=True,
                        epoch=3, generation=7, delivery_owner=object())
        controller.pending = {call.id: call}
        approval_wait.request_reveal("a", call, controller=controller)
        self.assertEqual(approval_wait._place({}, controller,
                         [("approval", None, [], 100, call.id, None)], 17, 400), 0)

    def test_same_id_replacement_never_inherits_reveal(self):
        controller = chat("a")
        approval_wait.request_reveal("a", approval_wait.awaiting(controller), shield=True)
        controller.pending = {"a/call": NS(id="a/call", approval=True)}
        state = {"message_viewport": (0, 0, 300, 400)}
        self.assertEqual(approval_wait._place(state, controller,
                         [("approval", None, [], 100, "a/call", None)], 17, 400), 17)
        self.assertIn("approval_shield", state)

    def test_changed_request_fence_never_inherits_reveal(self):
        for owner, field in (("call", "epoch"), ("call", "generation"), ("call", "delivery_owner"),
                             ("controller", "_epoch"), ("controller", "_request_generation"),
                             ("controller", "_worker_request")):
            with self.subTest(field=field):
                controller = chat("a")
                call = approval_wait.awaiting(controller)
                approval_wait.request_reveal("a", call, controller=controller)
                setattr(call if owner == "call" else controller, field, object())
                self.assertEqual(approval_wait._place({}, controller,
                                 [("approval", None, [], 100, call.id, None)], 17, 400), 17)


class HiddenSurfaces(unittest.TestCase):
    def test_external_subset_includes_the_hidden_active_chat(self):
        from angelo_agent.external_apps import waiting
        a, b = chat("a"), chat("b")
        a.external_client = "external"
        self.assertEqual(waiting([a, b], "a", chat_shown=False), [("external", "a")])
        self.assertEqual(waiting([a, b], "a", chat_shown=True), [])

    def test_launcher_click_rereads_the_card_and_only_selects_and_reveals(self):
        import angelo_agent
        from angelo_agent import launcher_waiting
        a = chat("a")
        ui = NS(_controllers={"a": a}, _sessions=NS(active="a"), show_session=Mock())
        toggle = NS(chat_shown=lambda: False)
        with patch.object(angelo_agent, "ui", ui, create=True), patch.object(
                angelo_agent, "chat_toggle", toggle, create=True), patch.object(
                approval_wait, "label", return_value="waiting"):
            launcher_waiting.refresh()
            self.assertEqual(launcher_waiting.first(), ("a", "a/call", "waiting"))
            self.assertTrue(launcher_waiting.select())
            ui.show_session.assert_called_once_with("a")
            self.assertTrue(approval_wait.pending_shield("a"))
            a.pending.clear()
            self.assertFalse(launcher_waiting.select(), "a stale drawn label cannot reveal or approve another call")
        launcher_waiting._items = ()
        approval_wait._reveal.clear()
        approval_wait._shield_pending.clear()


    def test_drawn_pill_matches_its_hit_and_reuses_its_measured_label(self):
        from angelo_agent import launcher_waiting
        view = NS(shape=Mock(), text=Mock())
        measure = Mock(return_value=100)
        modules = {"angelo_agent.card_host": NS(_View=lambda: view),
                   "angelo_agent.chat_view": NS(measure=measure, elide=lambda text, room, size: text)}
        launcher_waiting._items = (("a", "call", "waiting"),)
        launcher_waiting._size = None
        window = NS(as_pointer=lambda: 7)
        region = NS(x=20, y=30, width=1000)
        with patch.dict(sys.modules, modules):
            launcher_waiting.draw(window, region, 940, 100, 30, 1.0)
            launcher_waiting.draw(window, region, 940, 100, 30, 1.0)
        self.assertEqual(measure.call_count, 1)
        self.assertTrue(launcher_waiting.hit(window, 800, 130))
        self.assertFalse(launcher_waiting.hit(window, 100, 130))
        launcher_waiting._items = ()
        self.assertFalse(launcher_waiting.hit(window, 800, 130), "a disappeared card leaves no live pill hit")
        launcher_waiting._rects.clear()
        launcher_waiting._size = None


if __name__ == "__main__":
    unittest.main()
