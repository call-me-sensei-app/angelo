# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An app's views, window picture, state and usage over MCP, end to end without Blender.

The real launcher (``external_mcp.Server``), listener, ``ExternalHost`` and ``Controller`` of
``test_native_external_agent``'s fixture, with an environment whose ``look`` records the ``angelo_view`` call it
would make (``view_spec.validate_view`` checks it) and writes a picture per view; the window's own reads are
``test_native_external_state``'s and the figures ``test_native_external_usage``'s. Contract:
doc/angelo/external_mcp.rst, "Looks", "State" and "Usage".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import test_native_external_agent as base  # noqa: E402
from angelo_agent import edit_tool_hold, external_result, external_visibility, view_spec  # noqa: E402

HANDLE = "scn:" + "b" * 32 + ":7"


class LookEnvironment(base.AppEnvironment):
    """``AppEnvironment`` plus the window's side of a look (``external_visibility.VisibilityUi``) without bpy."""

    def __init__(self, state):
        super().__init__(state)
        self.looks, self.windows, self.result, self.held_reason = [], [], None, None

    def held(self):
        return self.held_reason

    def look(self, controller, arguments):
        view_spec.validate_view(view_spec.TOOL_NAME, arguments)   # The agent's own contract.
        self.looks.append(arguments)
        if self.result is not None:   # What a test makes angelo_view answer, through the real outcome reading.
            return external_visibility.look_outcome(self.result())
        return [self.picture(controller) for _view in arguments["views"]], {"scene_changed": False,
                                                                           "uncertainty": []}

    def window(self, controller):
        self.windows.append(controller.state["id"])
        from angelo_agent import external_window
        return self.picture(controller), {"size": [800, 600], "editors": [
            {"type": "VIEW_3D", "box": [0, 0, 600, 600], "shown": False, "why": external_window.VIEWPORT},
            {"type": "PROPERTIES", "box": [600, 0, 200, 600], "shown": False, "why": external_window.NOT_A_VIEWPORT}]}

    def look_facts(self, checked):
        found = {"Cube": HANDLE}
        return {"handles": {name: found[name] for name in checked["objects"] if name in found},
                "known": ["Cube", "Shot"], "bounds": ((0.0, 0.0, 1.0), 1.5), "aspect": 1.0,
                "cameras": {"Shot": {"location": [0.0, -8.0, 1.0], "look_at": [0.0, 0.0, 1.0], "lens": 50.0}}}

    def window_state(self):
        return {"mode": {"mode": "OBJECT", "active_object": "Cube"},
                "file": {"saved": True, "unsaved_changes": False},
                "note": str(Path.home() / "Projects" / "secret.blend")}   # A path a read could carry: withheld.


@unittest.skipIf(sys.platform == "win32", base.LISTENER)
class VisibilityTests(base.AgentFixture):
    def setUp(self):
        with mock.patch.object(base, "AppEnvironment", LookEnvironment):
            super().setUp()
        self.env.provider = base.TurnProvider(self.release, script=False)
        self.call("angelo_send", {"message": "hello"})
        self.release.set()
        self.controller = self.conversation()
        self.until(lambda: not self.controller.turn_running())
        self.rows = len(self.controller.state["messages"])
        external_visibility_clock = {"now": 1000.0}

        def advance():   # Each look reads a later clock than the throttle's window, unless a test holds it.
            external_visibility_clock["now"] += 5.0
            return external_visibility_clock["now"]
        self.clock = mock.patch.object(external_visibility, "clock", side_effect=advance)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        # The stepping clock makes every draw look 5 s long; the budget's cool-down is its own tests' (fixed clocks).
        cool_down = mock.patch.object(external_visibility, "COOL_DOWN", 0.0)
        cool_down.start()
        self.addCleanup(cool_down.stop)

    def assert_no_trace(self):
        self.assertEqual(len(self.controller.state["messages"]), self.rows, "a look adds nothing to the conversation")
        self.assertFalse(self.controller.turn_running(), "and starts no request")

    def test_a_look_from_two_angles_framed_on_an_object_returns_both_pictures(self):
        result = self.call("angelo_look", {"views": ["front", {"orbit": [90, 10]}], "objects": ["Cube"],
                                           "resolution": 384, "shading": "lit"})
        self.assertFalse(result["isError"], result)
        images = [item for item in result["content"] if item["type"] == "image"]
        self.assertEqual(len(images), 2)
        value = result["structuredContent"]
        self.assertEqual(len(value["views"]), 2)
        self.assertEqual(value["sent"], value["views"])
        arguments = self.env.looks[-1]
        self.assertEqual(arguments["targets"], [HANDLE])
        self.assertEqual((arguments["views"][0], arguments["resolution"], arguments["shading"]), ("FRONT", 384, "LIT"))
        self.assertEqual(arguments["views"][1]["look_at"], [0.0, 0.0, 1.0])
        for identity in value["views"]:   # Each is a picture of the conversation, readable by id.
            self.assertEqual(self.call("angelo_image", {"id": identity})["content"][0]["type"], "image")
        self.assert_no_trace()

    def test_a_scene_camera_by_name_and_a_free_camera(self):
        result = self.call("angelo_look", {"views": [{"camera": "Shot"},
                                                     {"location": [5, 5, 5], "look_at": [0, 0, 0]}]})
        self.assertFalse(result["isError"], result)
        self.assertEqual(self.env.looks[-1]["views"][0]["location"], [0.0, -8.0, 1.0])

    def test_a_refused_look_says_why_and_draws_nothing(self):
        for arguments, words in (({"shading": "RENDERED", "views": ["SCENE_CAMERA"]}, "Render Result"),
                                 ({"objects": ["Sphere"]}, "Cube"), ({"views": [{"camera": "Nope"}]}, "Shot")):
            with self.subTest(arguments=arguments):
                result = self.call("angelo_look", arguments)
                self.assertTrue(result["isError"])
                self.assertEqual(result["structuredContent"]["code"], "invalid")
                self.assertIn(words, result["structuredContent"]["message"])
        self.assertEqual(self.env.looks, [])
        self.assert_no_trace()

    def test_looks_are_throttled_per_app(self):
        self.clock.stop()
        with mock.patch.object(external_visibility, "clock", return_value=2000.0):
            self.assertFalse(self.call("angelo_look")["isError"])
            again = self.call("angelo_look")
        self.clock.start()
        self.assertEqual(again["structuredContent"]["code"], "busy")
        self.assertEqual(len(self.env.looks), 1)

    def test_pictures_join_the_apps_viewport_pictures_and_old_ones_are_deleted(self):
        from angelo_agent.external_look import MAX_VIEWPORT_PICTURES
        first = self.call("angelo_look")["structuredContent"]["views"][0]
        for _ in range(MAX_VIEWPORT_PICTURES):
            self.call("angelo_look")
        self.assertTrue(self.call("angelo_image", {"id": first})["isError"], "the oldest picture was deleted")

    def test_the_window_is_its_layout_and_waits_out_their_tool(self):
        result = self.call("angelo_window")
        self.assertFalse(result["isError"], result)
        self.assertEqual(self.env.windows, [self.controller.state["id"]])
        self.assertEqual([item["type"] for item in result["content"]], ["image", "text"])
        layout = result["structuredContent"]["layout"]
        self.assertEqual([e["shown"] for e in layout["editors"]], [False, False])
        self.assertIn("angelo_look", layout["editors"][0]["why"], "a viewport says where its pixels come from")
        with mock.patch.object(edit_tool_hold, "running", return_value=True):
            self.assertEqual(self.call("angelo_window")["structuredContent"]["code"], "busy")
            self.assertEqual(self.call("angelo_look")["structuredContent"]["code"], "busy")
            self.assertFalse(self.call("angelo_state")["isError"], "state reads nothing under the person's tool")
        self.assert_no_trace()

    def test_state_has_the_window_the_chat_and_angelo_and_withholds_paths(self):
        value = self.call("angelo_state")["structuredContent"]
        self.assertEqual(value["window"]["mode"]["mode"], "OBJECT")
        for key in ("turn", "model", "effort", "efforts", "approval", "consents", "connection"):
            self.assertIn(key, value["chat"])
        self.assertIn("addon_version", value["angelo"])
        text = json.dumps(value)
        self.assertNotIn(str(Path.home()), text, "a path under the home folder is withheld")
        self.assert_no_trace()

    def test_usage_has_every_figure_for_the_apps_task(self):
        value = self.call("angelo_usage")["structuredContent"]
        for key in ("billing", "conversation", "turns", "tasks", "goal", "plan", "jobs", "turn"):
            self.assertIn(key, value)
        self.assertEqual(len(value["tasks"]), 1, "the send in setUp is this app's task")
        self.assertIn("usd", value["tasks"][0]["usage"])
        self.assert_no_trace()

    def test_a_finished_tasks_status_says_what_changed_and_what_it_cost(self):
        result = self.call("angelo_status")["structuredContent"]["result"]
        self.assertEqual(result["summary"], "Made the cube.")
        self.assertIn("cost", result)
        self.assertIn("usd", result["cost"])
        self.assertFalse(result["verified"], "the stand-in turn ran no certified edit")
        self.assertEqual(result["changes_note"], external_result.UNVERIFIED)

    def test_a_read_that_raises_answers_that_call_and_the_listener_serves_every_app_after(self):
        """Review MUST 1: no read-only handler's exception stops the listener (external_host.drain re-raises)."""
        def broken():
            raise AttributeError("a result Angelo did not expect")
        self.env.result = broken
        failed = self.call("angelo_look")
        self.assertEqual(failed["structuredContent"]["code"], "not_applied", failed)
        self.assertIn("AttributeError", failed["structuredContent"]["message"])
        with mock.patch.object(self.env, "scene", side_effect=KeyError("x")):
            self.assertEqual(self.call("angelo_scene")["structuredContent"]["code"], "not_applied")
        self.env.result = None
        other = base.AppRegistry(self.state).connect("codex")
        self.assertFalse(self.call("angelo_state", name=other)["isError"], "another app is still served")
        self.assertFalse(self.call("angelo_look")["isError"], "and this one")

    def test_a_look_whose_shaders_compile_answers_busy_and_cancels_its_pending(self):
        from angelo_agent.studio_agent import StudioPending
        cancelled = []
        self.env.result = lambda: StudioPending(lambda: None, lambda: cancelled.append(True))
        answer = self.call("angelo_look", {"shading": "MATERIAL"})["structuredContent"]
        self.assertEqual(answer["code"], "busy")
        self.assertIn("compiling shaders", answer["message"])
        self.assertEqual(cancelled, [True])
        self.assert_no_trace()

    def test_a_look_returns_angelo_views_disclosures(self):
        value = self.call("angelo_look")["structuredContent"]
        self.assertEqual((value["scene_changed"], value["uncertainty"]), (False, []))

    def test_captures_share_one_budget_across_apps(self):
        self.clock.stop()
        other = base.AppRegistry(self.state).connect("codex")
        with mock.patch.object(external_visibility, "clock", return_value=5000.0):
            self.assertFalse(self.call("angelo_look")["isError"])
            held = self.call("angelo_look", name=other)["structuredContent"]
        self.clock.start()
        self.assertEqual(held["code"], "busy", "another app's capture waits for the shared budget")
        self.assertIn("drawing another picture", held["message"])

    def test_a_refused_or_throttled_call_takes_no_other_apps_turn(self):
        """Re-review SHOULD 1: the budget is checked only at a draw, after validation and the app's own throttle."""
        self.clock.stop()
        other = base.AppRegistry(self.state).connect("codex")
        with mock.patch.object(external_visibility, "clock", return_value=7000.0):
            for _ in range(3):
                refused = self.call("angelo_look", {"shading": "RENDERED", "views": ["SCENE_CAMERA"]}, name=other)
                self.assertEqual(refused["structuredContent"]["code"], "invalid")
            drawn = self.call("angelo_look")
        self.clock.start()
        self.assertFalse(drawn["isError"], drawn)

    def test_playback_or_the_persons_tool_answers_busy_spending_no_slot_or_budget(self):
        """Fourth review NIT: the person's hold is answered before the app's throttle and the shared budget."""
        self.clock.stop()
        self.env.held_reason = external_visibility.PLAYBACK
        with mock.patch.object(external_visibility, "clock", return_value=11000.0):
            held = self.call("angelo_look")["structuredContent"]
            self.env.held_reason = None
            drawn = self.call("angelo_look")
        self.clock.start()
        self.assertEqual((held["code"], held["message"]), ("busy", external_visibility.PLAYBACK))
        self.assertFalse(drawn["isError"], "the same app looks at once after: no slot or budget was spent")
        self.assertEqual(len(self.env.looks), 1)

    def test_a_budget_refusal_gives_the_app_its_throttle_slot_back(self):
        """Third review NIT: a look the shared budget refused still used the app's own once-a-second slot."""
        self.clock.stop()
        other = base.AppRegistry(self.state).connect("codex")
        with mock.patch.object(external_visibility, "clock", return_value=9000.0):
            self.assertFalse(self.call("angelo_look", name=other)["isError"])   # Another app's draw: the budget.
            held = self.call("angelo_look")["structuredContent"]
        self.assertEqual(held["code"], "busy")
        with mock.patch.object(external_visibility, "clock", return_value=9000.0 + external_visibility.MIN_GAP):
            again = self.call("angelo_look")
        self.clock.start()
        self.assertFalse(again["isError"], "asked again as soon as the budget allows, the app is not throttled")

    def test_state_and_usage_before_a_first_message_create_no_conversation(self):
        other = base.AppRegistry(self.state).connect("codex")
        before = set(self.env._controllers)
        state = self.call("angelo_state", name=other)["structuredContent"]
        usage = self.call("angelo_usage", name=other)["structuredContent"]
        self.assertEqual(set(self.env._controllers), before, "a read made no conversation")
        self.assertIsNone(state["chat"]["conversation"])
        self.assertEqual((state["chat"]["model"], state["chat"]["note"]), (None, external_visibility.NO_CONVERSATION),
                         "the state says plainly that no conversation, and so no model, exists yet")
        self.assertEqual(state["window"]["mode"]["mode"], "OBJECT", "the window is still read")
        self.assertEqual((usage["conversation"], usage["turns"]), (None, []))

    def test_the_visibility_modules_reach_no_approval_consent_or_edit(self):
        root = Path(external_visibility.__file__).parent
        for module in ("external_visibility", "external_look_args", "external_state", "external_usage",
                       "external_result", "external_window",
                       "external_mcp_visibility"):
            source = (root / (module + ".py")).read_text(encoding="utf-8")
            for never in ("approve_tool", "decline_tool", "set_trusted_scripts", "always_allow", "grant_authority",
                          "review_script", "set_enabled", "execute_tool", "ops.", "undo_push", ".new(", ".remove("):
                self.assertNotIn(never, source, "%s: %s" % (module, never))


class NoConversationNoteTests(unittest.TestCase):
    def test_the_note_names_every_call_that_makes_a_conversation(self):
        """Fourth review: the note said only angelo_send made one, so an app might pay a turn to see the models."""
        import ast
        import re
        from angelo_agent import external_agent
        source = Path(external_agent.__file__).read_text(encoding="utf-8")
        found = re.search(r"if controller is None and action in (\{[^}]*\})", source)
        creating = ast.literal_eval(found.group(1)) | {"send"}
        for action in creating:
            self.assertIn("angelo_" + action, external_visibility.NO_CONVERSATION, action)
        self.assertIn("angelo_settings lists", external_visibility.NO_CONVERSATION)


if __name__ == "__main__":
    unittest.main()
