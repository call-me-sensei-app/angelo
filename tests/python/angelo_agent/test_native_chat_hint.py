# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one-time show/hide chat hint (``chat_hint``) and the labelled toggle, without Blender.

Evidence: after 1.0.2's bare-icon toggle, people still could not find the chat in Manual mode. Covered here: when each hint is offered (first time in Manual mode, first time the chat disappears there
by any means, never in Director, never twice), what dismissing remembers, the shortcut named from the keymap as bound
(changed, platform spelling, removed), the card's layout on a fake view (it fits every region, sits top right clear of
the navigation gizmo, its button says what a click does), and the watch sequence: a waiting hint withdrawn when it no
longer applies, a visible one turned into *The chat is hidden*, closed when the chat comes back or Director starts.
``test_blender_chat_hint`` proves it in the running app.
"""

import ast
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import angelo_agent  # noqa: E402
from angelo_agent import card_kit as kit, chat_hint as hint  # noqa: E402
from test_native_notice_cards import VIEW, controls, labels  # noqa: E402

MAC = {"ctrl": True, "alt": True, "type": "N", "name": "N"}


def texts(plan):
    return [op[1] for op in plan.ops if op[0] == "text"]


class RuleTests(unittest.TestCase):
    def test_seen_reads_only_true_flags(self):
        self.assertEqual(hint.seen(None), frozenset())
        self.assertEqual(hint.seen(["manual"]), frozenset())
        self.assertEqual(hint.seen({"manual": True, "hidden": "yes", "other": True}), {"manual"})
        self.assertEqual(hint.seen({"manual": True, "hidden": True}), {"manual", "hidden"})

    def test_never_in_director(self):
        for state in ("docked", "popped", "hidden"):
            self.assertIsNone(hint.offer(frozenset(), False, state, "docked"))

    def test_first_time_in_manual_mode(self):
        for state in ("docked", "popped", "hidden"):   # Started in Manual with the chat closed too (a 1.0.2 user).
            self.assertEqual(hint.offer(frozenset(), True, state, None), "manual")
        self.assertIsNone(hint.offer({"manual"}, True, "docked", "docked"))

    def test_first_time_the_chat_disappears_by_any_means(self):
        for previous in ("docked", "popped"):
            self.assertEqual(hint.offer({"manual"}, True, "hidden", previous), "hidden")
            self.assertEqual(hint.offer(frozenset(), True, "hidden", previous), "hidden")   # The more useful one.
        self.assertIsNone(hint.offer({"manual", "hidden"}, True, "hidden", "docked"))
        self.assertIsNone(hint.offer({"manual"}, True, "hidden", "hidden"))   # Hidden already: nothing disappeared.
        self.assertIsNone(hint.offer({"manual"}, True, "hidden", None))       # Started hidden: nothing disappeared.

    def test_dismissing_the_hidden_hint_covers_both(self):
        self.assertEqual(hint.dismissed("manual"), {"manual"})
        self.assertEqual(hint.dismissed("hidden"), {"manual", "hidden"})

    def test_key_caps_follow_the_platform_and_the_binding(self):
        self.assertEqual(hint.key_caps(MAC, True), ("Ctrl", "Option", "N"))
        self.assertEqual(hint.key_caps(MAC, False), ("Ctrl", "Alt", "N"))
        self.assertEqual(hint.key_caps({"shift": True, "oskey": True, "type": "F5", "name": "F5"}, True), ("Shift", "Cmd", "F5"))
        self.assertEqual(hint.key_caps({"oskey": True, "type": "SPACE", "name": "Spacebar"}, False), ("Win", "Spacebar"))
        self.assertEqual(hint.key_caps(None, True), ())


class CardTests(unittest.TestCase):
    def card(self, kind="manual", state="docked", caps=("Ctrl", "Option", "N")):
        return hint.ChatHint(kind, lambda: state, lambda: caps)

    def plan(self, card, w=1400, h=900, scale=1.0):
        return card.layout(VIEW, w, h, scale, kit.UiState())

    def test_fits_and_sits_top_right_beside_the_gizmo(self):
        for w, h, scale in ((1400, 900, 1.0), (2800, 1800, 2.0), (520, 420, 1.0), (300, 260, 1.0), (1000, 700, 1.5)):
            plan = self.plan(self.card(), w, h, scale)
            x, y, cw, ch = plan.card
            self.assertGreaterEqual(x, 0, (w, h))
            self.assertLessEqual(x + cw, w + 0.5, (w, h))
            self.assertGreaterEqual(y, -0.5, (w, h))
            self.assertLessEqual(y + ch, h + 0.5, (w, h))
            for op in controls(plan):
                rx, ry, rw, rh = op[1]
                self.assertTrue(x <= rx and rx + rw <= x + cw + 0.5 and y <= ry and ry + rh <= y + ch + 0.5, op)
        x, y, cw, ch = self.plan(self.card()).card
        self.assertLessEqual(x + cw, 1400 - 96)        # Clear of the navigation gizmo.
        self.assertGreater(x, 1400 / 2)                # On the right, under the top bar's Chat button.
        self.assertGreater(y + ch, 900 - 100)          # At the top, under the 3D view's header.

    def test_the_button_says_what_a_click_does(self):
        self.assertEqual(labels(self.plan(self.card(state="docked"))), ["Got it", "Hide chat"])
        self.assertEqual(labels(self.plan(self.card(state="popped"))), ["Got it", "Hide chat"])
        self.assertEqual(labels(self.plan(self.card(state="hidden"))), ["Got it", "Show chat"])
        self.assertTrue([op for op in self.plan(self.card()).ops if op[0] == "closebtn"])

    def test_titles_body_and_shortcut(self):
        manual = " ".join(texts(self.plan(self.card())))
        self.assertIn("Show or hide the chat", manual)
        self.assertIn("Ctrl+Option+N", manual)
        hidden = " ".join(texts(self.plan(self.card("hidden", "hidden", ("Ctrl", "Alt", "N")))))
        self.assertIn("The chat is hidden", hidden)
        self.assertIn("Ctrl+Alt+N", hidden)
        removed = " ".join(texts(self.plan(self.card(caps=()))))
        self.assertNotIn("{keys}", removed)
        self.assertNotIn("press", removed)   # No shortcut bound: none is quoted.
        for template in [t for pair in hint.BODY.values() for t in pair]:
            self.assertEqual(template.count("{keys}"), 1 if "press" in template else 0, template)

    def test_draws_the_top_bar_button_and_key_caps(self):
        ops = [op for op in self.plan(self.card()).ops if op[0] == "kit" and op[1] == "chat_hint_keys"]
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0][3]["label"], "Chat")
        self.assertEqual(ops[0][3]["caps"], ("Ctrl", "Option", "N"))
        self.assertTrue(ops[0][3]["shown"])
        self.assertFalse([op for op in self.plan(self.card(state="hidden")).ops if op[0] == "kit"][0][3]["shown"])
        self.assertIn("chat_hint_keys", hint.ui_kit._registry)

    def test_version_follows_the_chat_and_the_shortcut(self):
        state = ["docked"]
        card = hint.ChatHint("manual", lambda: state[0], lambda: ())
        before = card.version()
        state[0] = "hidden"
        self.assertNotEqual(card.version(), before)

    def test_dismissing_remembers_and_a_lost_card_does_not(self):
        card = self.card("hidden")
        host = SimpleNamespace(close=mock.Mock())
        card.act("dismiss", None, host)
        host.close.assert_called_once_with("dismissed")
        with mock.patch.object(hint, "remember") as remember:
            card.closed("lost")
            card.closed("replaced")
            remember.assert_not_called()
            card.closed("dismissed")
            remember.assert_called_once_with({"manual", "hidden"})

    def test_non_modal_and_behind_every_other_notice(self):
        card = self.card()
        self.assertFalse(card.modal)
        self.assertTrue(card.retry_when_lost)
        for other in ("update_card.py", "whatsnew_card.py", "crash_card.py"):
            source = (ADDON / other).read_text()
            priorities = [int(n) for n in __import__("re").findall(r"priority[^=\n]*=\s*(?:[^,\n]*,\s*)*?(\d+)", source)]
            self.assertTrue(priorities and all(p > card.priority for p in priorities), (other, priorities))


class FakeHost:
    """``card_host`` as ``observe`` uses it."""

    def __init__(self):
        self.waiting, self.visible, self.events = [], None, []

    def request(self, card):
        self.waiting = [card]

    def queued(self, kind=None):
        return list(self.waiting)

    def pump(self, ready):
        if self.waiting and self.visible is None:
            self.visible = self.waiting.pop()

    def is_active(self, kind=None):
        return self.visible is not None

    def close(self, how):
        card, self.visible = self.visible, None
        self.events.append(("close", how))
        card.closed(how)

    def withdraw(self, kind):
        self.waiting = []
        self.events.append(("withdraw", kind))

    def refresh(self, kind):
        self.events.append(("refresh", kind))

    def check_area(self):
        return False


class WatchTests(unittest.TestCase):
    """``observe`` twice a second, over a scripted mode and chat state."""

    def setUp(self):
        hint.reset()
        self.host, self.done, self.state, self.director = FakeHost(), set(), ["docked"], [False]
        window = SimpleNamespace(screen=SimpleNamespace(is_temporary=False), as_pointer=lambda: 1)
        bpy = types.ModuleType("bpy")
        bpy.context = SimpleNamespace(window_manager=SimpleNamespace(windows=[window]))
        bpy.app = SimpleNamespace(background=False)
        toggle = SimpleNamespace(chat_state=lambda w, ws: (self.state[0], None, None))
        director = SimpleNamespace(is_director=lambda w: self.director[0], _timer=None)
        notices = SimpleNamespace(ready=lambda: True)
        fakes = {"card_host": self.host, "chat_toggle": toggle, "director": director, "app_notices": notices}
        patches = [mock.patch.dict(sys.modules, {"bpy": bpy, **{"angelo_agent." + k: v for k, v in fakes.items()}})]
        patches += [mock.patch.object(angelo_agent, k, v, create=True) for k, v in fakes.items()]
        patches += [mock.patch.object(hint, "disabled", lambda: False),
                    mock.patch.object(hint, "load", lambda: frozenset(self.done)),
                    mock.patch.object(hint, "remember", lambda kinds: self.done.update(kinds)),
                    mock.patch.object(hint, "shortcut", lambda: ("Ctrl", "Option", "N"))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(hint.reset)

    def tick(self, state=None, director=None):
        if state is not None:
            self.state[0] = state
        if director is not None:
            self.director[0] = director
        hint.observe()

    def test_director_offers_nothing(self):
        self.director[0] = True
        self.tick()
        self.tick("hidden")
        self.assertIsNone(self.host.visible)
        self.assertEqual(self.host.waiting, [])

    def test_manual_hint_once_then_hidden_hint_once(self):
        self.tick()
        self.assertEqual(self.host.visible.hint, "manual")
        self.host.visible.act("dismiss", None, self.host)
        self.assertEqual(self.done, {"manual"})
        self.tick()
        self.assertIsNone(self.host.visible)            # Never twice.
        self.tick("hidden")                             # Close Area, the toggle, the shortcut: any means.
        self.assertEqual(self.host.visible.hint, "hidden")
        self.tick("docked")                             # Brought back: learnt, closed, remembered.
        self.assertEqual(self.host.events[-1], ("close", "done"))
        self.assertEqual(self.done, {"manual", "hidden"})
        self.tick("hidden")
        self.tick("docked")
        self.assertIsNone(self.host.visible)

    def test_hiding_while_the_first_hint_shows_turns_it_into_the_second(self):
        self.tick()
        card = self.host.visible
        self.tick("hidden")
        self.assertIs(self.host.visible, card)
        self.assertEqual(card.hint, "hidden")
        self.assertIn(("refresh", "chat_hint"), self.host.events)
        card.act("dismiss", None, self.host)
        self.assertEqual(self.done, {"manual", "hidden"})

    def test_director_closes_a_visible_hint_and_withdraws_a_waiting_one(self):
        self.tick()
        self.tick(director=True)
        self.assertEqual(self.host.events[-1], ("close", "mode"))
        self.assertEqual(self.done, {"manual"})        # It was on screen: seen.
        self.done.clear()
        hint.reset()
        self.director[0] = False
        self.host.pump = lambda ready: None              # Not ready yet (a mode change, What's New first).
        self.tick()
        self.assertEqual(len(self.host.waiting), 1)
        self.tick(director=True)
        self.assertEqual(self.host.events[-1], ("withdraw", "chat_hint"))
        self.assertEqual(self.done, set())              # Never shown: not remembered.

    def test_a_waiting_hidden_hint_is_withdrawn_when_the_chat_comes_back(self):
        self.done.add("manual")
        self.host.pump = lambda ready: None
        self.tick()
        self.tick("hidden")
        self.assertEqual(self.host.waiting[0].hint, "hidden")
        self.tick("docked")
        self.assertEqual(self.host.waiting, [])
        self.assertEqual(self.done, {"manual"})


class AnchorTests(unittest.TestCase):
    """The card stands next to the top bar's Chat button with a small arrow at it, and its text says where the button
    really is."""

    def toggle(self):
        sys.modules.pop("angelo_agent.chat_toggle", None)
        bpy = types.ModuleType("bpy")
        bpy.types = SimpleNamespace(Operator=object)
        with mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.types": bpy.types}):
            import angelo_agent.chat_toggle as module
        return module

    def test_the_buttons_rect_follows_from_its_region(self):
        toggle = self.toggle()
        unit = 20
        self.assertEqual(toggle.button_units(40, unit), 3.75)    # icon + padding (1.75) + 2 units of text
        self.assertEqual(toggle.button_units(41, unit), 4.0)     # rounded up, never clipped
        self.assertEqual(toggle.anchor_rect(1700, 1380, 1.0, 3.75, unit), (1704, 1779.0, 1380))
        self.assertEqual(toggle.anchor_rect(3400, 2760, 2.0, 3.75, 40), (3408, 3558.0, 2760))   # Retina: doubled

    def card(self, anchor=None):
        return hint.ChatHint("manual", lambda: "docked", lambda: ("Ctrl", "Option", "N"), lambda: anchor)

    def test_points_its_arrow_at_the_button_and_stays_on_screen(self):
        for w, h, point in ((600, 1200, 300), (600, 1200, 590), (600, 1200, 12), (2000, 1200, 1500), (420, 900, 200)):
            card = self.card()
            b = card._build(VIEW, 1.0, w, kit.UiState())
            plan = card._place(b, w, h, 0, 0, point)
            x, y, cw, ch = plan.card
            self.assertTrue(0 <= x and x + cw <= w + 0.5 and y + ch <= h + 0.5, (w, point))
            arrow = [op for op in plan.ops if op[0] == "kit" and op[1] == "chat_hint_arrow"]
            self.assertEqual(len(arrow), 1, (w, point))
            ax, ay, aw, ah = arrow[0][2]
            self.assertAlmostEqual(ay, y + ch, delta=0.5, msg="its base on the card's top edge")
            self.assertLessEqual(ay + ah, h, "its tip inside the region, under the top bar")
            self.assertTrue(x <= ax and ax + aw <= x + cw, "the arrow leaves from the card")
            if 30 <= point <= w - 30:
                self.assertAlmostEqual(ax + aw / 2, point, delta=0.5, msg="it points at the button")

    def test_without_a_button_over_the_region_it_keeps_the_corner(self):
        card = self.card()
        plan = card._place(card._build(VIEW, 1.0, 1400, kit.UiState()), 1400, 900, 0, 0, None)
        self.assertFalse([op for op in plan.ops if op[0] == "kit" and op[1] == "chat_hint_arrow"])
        self.assertLessEqual(plan.card[0] + plan.card[2], 1400 - 96)

    def test_shows_in_the_area_right_under_the_button(self):
        def area(name, x, y, w, h):
            return SimpleNamespace(name=name, regions=[SimpleNamespace(type='WINDOW', x=x, y=y, width=w, height=h)])
        chat = area("chat", 1700, 0, 594, 1350)
        props = area("properties", 1290, 0, 405, 1100)
        outliner = area("outliner", 1290, 1103, 405, 250)
        view = area("view", 0, 120, 1286, 1230)
        window = SimpleNamespace(screen=SimpleNamespace(areas=[view, props, outliner, chat]))
        bpy = SimpleNamespace(context=SimpleNamespace(preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1.0))))
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            self.assertIs(self.card((1780, 1840, 1380)).target_area(window), chat)
            self.assertIs(self.card((1300, 1380, 1380)).target_area(window), outliner, "the topmost one under it")
            self.assertIsNone(self.card(None).target_area(window), "no button drawn: the host's 3D view")
            narrow = SimpleNamespace(screen=SimpleNamespace(areas=[area("narrow", 1700, 0, 200, 1350)]))
            self.assertIsNone(self.card((1780, 1840, 1380)).target_area(narrow), "too narrow: the 3D view instead")

    def test_wording_places_the_button_by_what_is_beside_it(self):
        for template in [t for pair in hint.BODY.values() for t in pair]:
            self.assertNotIn("top right", template)
            self.assertIn("in the top bar, next to {mode}", template)
        text = " ".join(t for op in self.card().layout(VIEW, 1400, 900, 1.0, kit.UiState()).ops if op[0] == "text"
                        for t in [op[1]])
        self.assertIn("next to Manual", " ".join(text.split()))


class LayoutTests(unittest.TestCase):
    """A corner card never makes docking or a mode change wait; modal cards, real tools and screenshots still do.

    Evidence: the first GUI run of the hint's own suite, Show chat on the card refused with "Finish or cancel the
    active Blender tool before moving Coding Agent": the card's host operator counted as a tool."""

    def window(self, *names):
        return SimpleNamespace(modal_operators=[SimpleNamespace(bl_idname=n, name=n) for n in names])

    def host(self, modal):
        active = None if modal is None else SimpleNamespace(card=SimpleNamespace(modal=modal))
        return mock.patch.dict(sys.modules, {"angelo_agent.card_host": SimpleNamespace(_active=active)})

    def test_corner_card_leaves_layout_free(self):
        from angelo_agent import input_policy
        shown = self.window("ANGELO_OT_card_host")
        with self.host(False):
            self.assertFalse(input_policy.layout_blocking(shown))
            self.assertTrue(input_policy.interaction_active(shown), "a window screenshot would hold its pixels")
        with self.host(None):   # Just closed: its operator ends at the next event.
            self.assertFalse(input_policy.layout_blocking(shown))

    def test_modal_card_and_real_tools_still_hold_layout(self):
        from angelo_agent import input_policy
        with self.host(True):
            self.assertTrue(input_policy.layout_blocking(self.window("ANGELO_OT_card_host")))
        with self.host(False):
            self.assertTrue(input_policy.layout_blocking(self.window("ANGELO_OT_card_host", "TRANSFORM_OT_translate")))
            self.assertTrue(input_policy.layout_blocking(self.window("ANGELO_OT_intro")))
            self.assertFalse(input_policy.layout_blocking(self.window("ANGELO_OT_chat_input")))

    def test_dock_and_mode_changes_use_it(self):
        ui = (ADDON / "ui.py").read_text()
        dock = ui.split("def request_chat_dock(", 1)[1].split("\ndef ", 1)[0]
        self.assertEqual(dock.count("layout_blocking("), 2)
        self.assertNotIn("interaction_active", dock)
        director = (ADDON / "director.py").read_text()
        self.assertIn("if layout_blocking(target):", director)
        self.assertNotIn("interaction_active", director)
        self.assertIn("interaction_active(shown)", (ADDON / "view_editor.py").read_text())


class WiringTests(unittest.TestCase):
    def test_the_toggle_is_labelled_and_the_watch_observes(self):
        source = (ADDON / "chat_toggle.py").read_text()
        draw = next(node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef) and node.name == "draw")
        call = next(node for node in ast.walk(draw) if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "operator")
        text = next(k.value for k in call.keywords if k.arg == "text")
        self.assertEqual(ast.unparse(text), "label")
        self.assertIn("label = iface_(LABEL)", ast.unparse(draw))
        self.assertIn("row.ui_units_x = units", ast.unparse(draw), "a measured width, so anchor_rect is exact")
        self.assertIn("anchors[context.window.as_pointer()] = anchor_rect(", ast.unparse(draw))
        self.assertIn('LABEL = n_("Chat")', source)
        self.assertIn("chat_hint.observe()", source)
        self.assertIn("chat_hint.reset()", source)

    def test_a_show_that_fails_says_so_on_a_card(self):
        source = (ADDON / "chat_toggle.py").read_text()
        failure = source.split("def not_shown(error):", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("card_host.request(update_card.NoticeCard(", failure, "the hidden chat can't show its own error")
        self.assertIn("card_host.pump(", failure)
        self.assertIn("iface_(NOT_SHOWN)", failure)

    def test_overlap_insets_without_blender(self):
        self.assertEqual(hint.overlap_insets(), (0, 0))
        card = hint.ChatHint("manual", lambda: "docked", lambda: ())
        plan = card._place(card._build(VIEW, 1.0, 400, kit.UiState()), 500, 800, left=80, right=0)
        self.assertGreaterEqual(plan.card[0], 80, "never under the toolbar")

    def test_classified_for_translation(self):
        from scripts_i18n_sources import UI_MODULES
        self.assertIn("chat_hint", UI_MODULES)

    def test_hints_are_off_in_automation_unless_their_suite_asks(self):
        source = (ADDON / "chat_hint.py").read_text()
        self.assertIn('app_notices.automation_off() and not product_env.flag("ENABLE_CHAT_HINTS")', source)


def _sources_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("scripts_i18n_sources", ROOT / "scripts/angelo/i18n/sources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules["scripts_i18n_sources"] = module


_sources_module()


if __name__ == "__main__":
    unittest.main()
