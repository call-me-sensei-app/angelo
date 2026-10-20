# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An empty chat that looks ready never has a dead click (1.0.2), without Blender.

Evidence: a 1.0.0/1.0.1 user's first launch showed the connected welcome ("What would you like to make?", the
example prompts, "Choose model") and nothing they clicked did anything; a restart fixed it. Two causes, each fixed as
a class and tested here per state:

* Input. The chat passed every event on while any modal operator other than its own ran in the window, and
  Angelo's own passive overlays (the welcome screen, notice cards, the AI Providers page) counted as one: with the
  first-launch welcome or a What's New card open over the 3D view, the drawn chat did nothing. Now only a real tool
  holds the chat's input (``input_policy.canvas_blocking``), and a click it holds gets a notice naming it
  (``chat_start_states.blocked_card``), even when the tool was running before the chat's handler started.
* Model. A chat never bound to a connection made elsewhere had no model list ("Choose model") and said nothing. It
  stays unbound (a connection added elsewhere binds no chat: ai_providers.rst), and a connection with exactly one
  model has it chosen; every no-model state shows a card with its one action: loading
  (none needed), failed (Retry, the provider's text under Details), empty (Retry), or no models of its own while
  connections offer some (Choose a model, which adopts the connection). Its own models and no default: Default.

``test_blender_chat_start_states`` proves the same in the running app, with screenshots.
"""

import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # test_native_welcome_card
import angelo_agent  # noqa: E402
from angelo_agent import chat_start_states as states, input_policy  # noqa: E402
from angelo_agent.chat_settings import selected_model  # noqa: E402


def operator(identity, name=""):
    return SimpleNamespace(bl_idname=identity, name=name)


def window(*operators):
    return SimpleNamespace(modal_operators=list(operators))


def model(identity, default=False):
    return {"id": identity, "name": identity.title(), "default": default, "efforts": [], "fast_tier": None,
            "images": True, "default_effort": "low"}


class InputTests(unittest.TestCase):
    """Which modal operators hold a canvas's input."""

    def test_angelo_overlays_leave_the_chat_usable(self):
        for identity in sorted(input_policy.PASSIVE_OVERLAYS):
            with self.subTest(identity=identity):
                shown = window(operator(identity, "Overlay"), operator("angelo.chat_input"))
                self.assertEqual(input_policy.canvas_blocking(shown), [], "the chat beside it takes its clicks")
                self.assertTrue(input_policy.interaction_active(shown),
                                "renders, captures and layout changes still wait for it")

    def test_a_real_tool_holds_the_chat_and_is_named(self):
        held = window(operator("angelo.intro", "Welcome"), operator("TRANSFORM_OT_translate", "Move"),
                      operator("SOME_OT_addon_modal", ""))
        self.assertEqual(input_policy.canvas_blocking(held), ["Move", "some.addon_modal"],
                         "named as the person sees it, else by its identity; the overlay is not named")

    def test_the_canvases_own_handlers_never_block(self):
        self.assertEqual(input_policy.canvas_blocking(window(*(operator(i) for i in input_policy.CANVAS_INPUTS))), [])


class ClassificationTests(unittest.TestCase):
    """Every Angelo modal operator is classified once, so a new overlay can never silently freeze the chat."""

    def test_every_angelo_modal_operator_is_classified_exactly_once(self):
        import ast
        found = {}
        for path in sorted(ADDON.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ClassDef) and any(isinstance(b, ast.FunctionDef) and b.name == "modal"
                                                         for b in node.body):
                    for item in node.body:
                        if isinstance(item, ast.Assign) and any(getattr(t, "id", "") == "bl_idname" for t in item.targets):
                            found[item.value.value] = path.name
        self.assertTrue(found)
        sets = (input_policy.CANVAS_INPUTS, input_policy.PASSIVE_OVERLAYS, input_policy.HOLDING_TOOLS)
        for identity, module in sorted(found.items()):
            with self.subTest(operator=identity, module=module):
                self.assertEqual(sum(identity in group for group in sets), 1,
                                 "add it to input_policy.CANVAS_INPUTS, PASSIVE_OVERLAYS or HOLDING_TOOLS")
        self.assertTrue(set().union(*sets) <= set(found), "no stale names")

    def test_an_approval_still_holds_the_chat(self):
        self.assertEqual(input_policy.canvas_blocking(window(operator("angelo.accept_native_preview", "Accept"))),
                         ["Accept"], "Return and Esc belong to the approval")

    def test_a_handler_that_ended_without_its_cancel_is_seen(self):
        self.assertTrue(input_policy.handler_running(window(operator("angelo.chat_input")), "angelo.chat_input"))
        self.assertFalse(input_policy.handler_running(window(operator("angelo.intro")), "angelo.chat_input"))
        self.assertFalse(input_policy.handler_running(SimpleNamespace(), "angelo.chat_input"))

    def test_offscreen_tools_never_wait_for_an_overlay(self):
        shown = window(operator("angelo.intro"), operator("angelo.card_host"))
        self.assertFalse(input_policy.tool_running(shown), "a welcome left open never makes a turn's tools refuse")
        self.assertTrue(input_policy.tool_running(window(operator("TRANSFORM_OT_translate", "Move"))))
        for name in ("view_capture.py", "view_render.py", "view_motion.py", "sculpt.py", "native_programs.py",
                     "marble_host.py"):
            with self.subTest(module=name):
                source = (ADDON / name).read_text()
                self.assertIn("tool_running(window)", source)
                self.assertNotIn("interaction_active", source)
        self.assertIn("interaction_active(shown)", (ADDON / "view_editor.py").read_text(),
                      "Blender's window screenshot would hold the overlay's pixels: it still waits")


class BlockedCardTests(unittest.TestCase):
    def test_names_the_tool_and_how_to_end_it_without_a_dead_button(self):
        card = states.blocked_card(["Move"])
        self.assertEqual(card["kind"], "warn")
        self.assertIn("“Move”", card["body"])
        self.assertIn("Esc", card["body"])
        self.assertIn("Add-ons", card["body"], "an add-on that keeps running does not end on Esc")
        self.assertEqual(card["actions"], (), "the chat's clicks are what the tool holds: a button there would be dead")

    def test_shown_for_a_while_after_the_click(self):
        self.assertEqual(states.blocked_shown((("Move",), 100.0), 100.5), ("Move",))
        self.assertEqual(states.blocked_shown((("Move",), 100.0), 100.0 + states.BLOCKED_SECONDS), ("Move",))
        self.assertIsNone(states.blocked_shown((("Move",), 100.0), 100.1 + states.BLOCKED_SECONDS))
        self.assertIsNone(states.blocked_shown(None, 5.0))


class ModelStateTests(unittest.TestCase):
    """Every way a connected chat can be without a model, and its card."""

    def test_only_when_the_model_button_would_say_choose_model(self):
        self.assertTrue(states.needs_model(None, "", False))
        self.assertFalse(states.needs_model(model("opus"), "", False))
        self.assertFalse(states.needs_model(None, "claude-opus-5-5", False), "a typed model ID is a choice")
        self.assertFalse(states.needs_model(None, "", True), "a team connection names itself")

    def test_each_state_has_its_one_action(self):
        cases = [  # ((own, choices, loading, error), (state, kind, action))
            (((), (), True, ""), (states.LOADING, "info", None)),
            (((), (), False, "Claude: initialize timed out"), (states.FAILED, "error", states.RETRY_MODELS)),
            (((), (), False, ""), (states.EMPTY, "warn", states.RETRY_MODELS)),
            (((), (model("a"), model("b")), False, ""), (states.CHOOSE, "info", states.CHOOSE_MODEL)),
            (((), (model("a"),), True, "elsewhere failed"), (states.CHOOSE, "info", states.CHOOSE_MODEL)),
        ]
        for (own, choices, loading, error), (expected, kind, action) in cases:
            with self.subTest(choices=len(choices), loading=loading, error=error):
                problem = states.model_problem(list(own), list(choices), loading, error)
                self.assertEqual(problem, expected)
                card = states.model_card(problem, error)
                self.assertEqual(card["kind"], kind)
                self.assertEqual([a[0] for a in card["actions"]], [action] if action else [])
                self.assertTrue(card["title"] and card["body"])
                for _, value, label, style in card["actions"]:
                    self.assertEqual((value, style), (None, "primary"))
                    self.assertTrue(label)

    def test_models_of_its_own_need_nothing_and_say_default(self):
        """Several models and none flagged default: the connection's default runs, as the model list's checked
        Default row says (chat_popovers.model_rows), so the button says Default and no card asks for a choice."""
        own = [model("a"), model("b")]
        self.assertIsNone(states.model_problem(own, own, False, ""))
        self.assertIsNone(states.model_problem(own, own, True, "another connection failed"))
        self.assertEqual(states.default_label(own), "Default")
        self.assertEqual(states.default_label([]), "Choose model")
        from angelo_agent.chat_popovers import model_rows
        self.assertTrue(model_rows(own, "")[0]["selected"], "the list's Default row is the one checked")

    def test_a_failure_keeps_the_providers_text_under_details(self):
        card = states.model_card(states.FAILED, "Claude: initialize timed out", expanded=True)
        self.assertEqual((card["details"], card["details_action"], card["expanded"]),
                         ("Claude: initialize timed out", states.DETAILS, True))
        self.assertNotIn("initialize", card["body"], "the sentence is plain language")
        self.assertIsNone(states.model_card(states.EMPTY)["details"])


class PreselectTests(unittest.TestCase):
    def chat(self, models, chosen=""):
        return SimpleNamespace(state={"options": {"model": chosen}}, settings={}, models=models,
                               selection_models=models, selection_settings={})

    def test_the_default_then_a_single_model(self):
        self.assertEqual(selected_model(self.chat([model("a"), model("b", True)]))["id"], "b")
        self.assertEqual(selected_model(self.chat([model("only")]))["id"], "only",
                         "a connection with exactly one model has it chosen")
        self.assertIsNone(selected_model(self.chat([model("a"), model("b")])), "several, no default: the person picks")
        self.assertIsNone(selected_model(self.chat([])))
        self.assertIsNone(selected_model(self.chat([model("only")], chosen="gone")), "an unknown choice is never guessed")


class NoBindingTests(unittest.TestCase):
    """A chat is never bound behind the person's back: a connection added elsewhere (AI Providers, a confirmed
    suggestion) binds no chat; an unbound chat adopts the store's active connection of its mode when it sends or
    opens the model list (doc/angelo/ai_providers.rst; test_blender_native_suggestions pins it in the app)."""

    def test_the_pump_binds_no_chat(self):
        pump = (ADDON / "quick_connect.py").read_text().split("\ndef pump(ui):", 1)[1]
        self.assertNotIn("choose_model_connection", pump.split("for mode in list(_flows):", 1)[0])
        self.assertNotIn("bind_fresh_chat", (ADDON / "quick_connect.py").read_text())
        self.assertNotIn("fresh_chat_connection", (ADDON / "native_suggestions.py").read_text())


class WiringTests(unittest.TestCase):
    """Where the pieces meet (Blender-only code, read as source)."""

    def read(self, name):
        return (ADDON / name).read_text()

    def test_the_chat_names_a_held_click_even_when_the_tool_came_first(self):
        source = self.read("chat_view.py")
        modal = source.split("class ANGELO_OT_chat_input", 1)[1].split("    def modal(self, context, event):", 1)[1]
        guard = modal.index("blocking = canvas_blocking(window)")
        self.assertLess(guard, modal.index("note_blocked_click(window, event, blocking)"))
        self.assertLess(modal.index("note_blocked_click("), modal.index("return {'PASS_THROUGH'}", guard))
        self.assertIn("clear_blocked_notice(window)", modal)
        ensure = source.split("\ndef ensure_input(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("interaction_active", ensure, "the handler starts beside a running tool, passing its events")
        self.assertNotIn("canvas_blocking", ensure)
        self.assertIn("_draw_blocked(state, width,", source)

    def test_the_chat_input_survives_a_failed_event_and_restarts_if_it_ended(self):
        source = self.read("chat_view.py")
        self.assertIn("    @_kept_alive\n    def modal(self, context, event):", source)
        kept = source.split("\ndef _kept_alive(", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("return modal(self, context, event)", kept)
        self.assertIn("except Exception:", kept)
        self.assertIn("return {'PASS_THROUGH'}", kept)
        ensure = source.split("\ndef ensure_input(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('handler_running(window, "angelo.chat_input")', ensure)
        studio = self.read("studio.py").split("\ndef ensure_input(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('not handler_running(window, "angelo.studio_input")', studio)

    def test_keys_over_the_welcome_or_a_card_are_theirs(self):
        source = self.read("chat_view.py")
        modal = source.split("    def modal(self, context, event):", 1)[1]
        self.assertLess(modal.index("over_overlay(window, event)"), modal.index("area = next("))
        helper = source.split("\ndef over_overlay(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('intro._open.get("area") == pointer', helper)
        self.assertIn("card_host.shown_on(pointer)", helper)

    def test_the_setup_cards_import_waits_for_a_running_turn(self):
        after = self.read("settings_import_ui.py").split("\ndef after_setup(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(after.index("if apply.agent_busy():"), after.index("_run_import("))

    def test_a_single_claude_model_reaches_the_runtime(self):
        session = self.read("claude_transport.py").split("\ndef claude_session(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('model = chosen["id"] if chosen and not chosen.get("runtime_default_alias") else ""', session)
        self.assertLess(session.index("selected_model(context, options)"), session.index('["--model", model]'))

    def test_a_failed_draw_closes_an_invisible_picker(self):
        failure = self.read("chat_view.py").split("    except Exception as error:\n        last_error = str(error)", 1)[1]
        self.assertIn('state.pop("popover", None)', failure.split("finally:", 1)[0])

    def test_the_model_pill_and_card_share_one_guarded_path(self):
        view, welcome = self.read("chat_view.py"), self.read("chat_welcome.py")
        self.assertIn("if chat_welcome.open_models(ui, controller):", view)
        opener = welcome.split("\ndef open_models(", 1)[1]
        self.assertIn("except (RuntimeError, ValueError, OSError) as error:", opener)
        self.assertIn('controller.emit("message", {"role": "error"', opener, "a failure is a notice, never nothing")
        dispatch = welcome.split("\ndef dispatch(", 1)[1]
        self.assertIn('action == "welcome_choose_model"', dispatch)
        self.assertIn('action == "welcome_retry_models"', dispatch)
        self.assertIn("bpy.ops.angelo.refresh_models()", dispatch)

    def test_the_welcome_draws_the_card_above_the_examples(self):
        draw = self.read("chat_welcome.py").split("\ndef draw_empty(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("card = model_card(ui, ui.get_controller(), state) if connected else None", draw)
        self.assertIn("card_height", draw.split("total = (", 1)[1].split("\n    y = ", 1)[0], "it counts toward the layout")
        self.assertLess(draw.index("_notice(view, card, 24, y, column, state, True)"), draw.index('kind == "example"'))

    def test_studio_shares_the_rule(self):
        studio = self.read("studio.py")
        self.assertIn("if canvas_blocking(window):", studio.split("def modal(self, context, event):", 1)[1])
        self.assertIn("if canvas_blocking(window):", studio.split("\ndef ensure_input(", 1)[1].split("\ndef ", 1)[0])


class WelcomePlacementTests(unittest.TestCase):
    """The card sits between the subtitle and the example prompts, and its button is a hit."""

    def test_card_then_examples_and_hits(self):
        welcome_tests = importlib.import_module("test_native_welcome_card")
        case = welcome_tests.WelcomeCardRowTests
        case.setUpClass()
        try:
            card = states.model_card(states.CHOOSE)
            fake = types.ModuleType("angelo_agent.ui_kit_status")

            def notice(view, x, top, width, scale, ui=None, draw=True, **params):
                hits = [((x + 40, top - 70, 120, 26), action, value) for action, value, _, _ in params["actions"]]
                if draw:
                    view.text(params["title"], x + 40, top - 20, 14)
                return 80, hits
            fake.notice = notice
            with mock.patch.dict(sys.modules, {"angelo_agent.ui_kit_status": fake}), \
                    mock.patch.object(angelo_agent, "ui_kit_status", fake, create=True):
                runner = case("test_examples_then_one_tip_that_links_to_the_docs")
                state, view = runner.draw(model_card=card)
        finally:
            case.tearDownClass()
        actions = [action for _, action, _ in state["hits"]]
        self.assertEqual(actions[0], states.CHOOSE_MODEL)
        self.assertEqual(state["drawn_model_card"], card["title"])
        button = state["hits"][0][0]
        first_example = next(rect for rect, action, _ in state["hits"] if action == "welcome_example")
        self.assertGreaterEqual(button[1], first_example[1] + first_example[3], "above the examples")
        self.assertTrue(all(rect[1] >= 120 for rect, _, _ in state["hits"]), "all above the composer")


if __name__ == "__main__":
    unittest.main()
