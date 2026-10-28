# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A script's read-back reads its rows as one pass (revision_pass, script_after.named): the same revision
object_revision gives each object, a budget of each object's own over one shared-read memo, the scene listed once,
refusals per object, and the rows, Booleans and motion resolved from one name index of the scene. Host fakes only;
test_blender_world.py and the concurrent-edit fixture read real revisions after scripts."""

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_scene_queries import FakeObject  # noqa: E402  (first: it stands in for bpy)
from test_native_turn_context import Clock, TurnContextFixture, object_id  # noqa: E402
from angelo_agent import scene_queries, scene_settings, script_after, script_booleans  # noqa: E402
from angelo_agent.revision_pass import RevisionPass  # noqa: E402
from angelo_agent.scene_read_values import SceneQueryError  # noqa: E402


class RevisionPassTests(TurnContextFixture):
    def test_each_revision_is_the_one_object_revision_gives(self):
        read = RevisionPass(self.window)
        for obj in self.objects[:6]:
            self.assertEqual(read(obj), scene_queries.object_revision(self.window, obj))

    def test_each_object_has_its_own_budget_over_one_shared_memo(self):
        seen, original = [], scene_queries._fingerprints.revision

        def revision(window, obj, work):
            seen.append(work)
            return original(window, obj, work)
        read = RevisionPass(self.window)
        with patch.object(scene_queries._fingerprints, "revision", side_effect=revision):
            read(self.objects[0])
            read(self.objects[1])
        self.assertIsNot(seen[0], seen[1])
        self.assertIs(seen[0].shared, seen[1].shared)
        self.assertIsNot(RevisionPass(self.window)._shared, read._shared)   # A new pass shares nothing.

    def test_the_scene_is_listed_once_and_a_stranger_is_refused(self):
        listings = []
        objects = self.window.scene.objects
        original = type(objects).__iter__
        with patch.object(type(objects), "__iter__", lambda self: listings.append(1) or original(self)):
            read = RevisionPass(self.window)
            self.assertEqual(listings, [])   # Nothing read, nothing listed.
            for obj in self.objects[:5]:
                read(obj)
        self.assertEqual(len(listings), 1)
        with self.assertRaisesRegex(SceneQueryError, "must belong to the bound scene"):
            read(object_id(FakeObject(999, "Elsewhere", mesh=True)))

    def test_a_window_gone_since_the_pass_began_gives_no_revision(self):
        read = RevisionPass(self.window)
        read.__dict__["_live__window"] = lambda: None   # live_target: the held window is closed or replaced.
        with self.assertRaisesRegex(SceneQueryError, "window is gone"):
            read(self.objects[0])

    def test_off_the_main_thread_each_read_refuses(self):
        read, caught = RevisionPass(self.window), []

        def worker():
            try:
                read(self.objects[0])
            except Exception as error:  # noqa: BLE001 - the test reads which one (fixtures may reload its module).
                caught.append((type(error).__name__, str(error)))
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(caught, [("SceneQueryError", "Native scene observations require Blender's main thread")])


class NameIndexTests(TurnContextFixture):
    def test_the_first_object_of_a_name_as_get_finds_it(self):
        twin = object_id(FakeObject(990, "Prop 03", mesh=True))
        scene = SimpleNamespace(objects=list(self.objects) + [twin])
        index = script_after.named(scene)
        self.assertIs(index["Prop 03"], self.objects[3])
        self.assertEqual(len(index), len(self.objects))

    def test_the_read_back_looks_names_up_in_one_index(self):
        objects = self.window.scene.objects
        changes = {"added": [{"name": "Prop 03"}], "modified": [{"name": "Prop 07", "fields": ["matrix"]}],
                   "removed": []}
        with patch.object(type(objects), "get", side_effect=AssertionError("looked up one by one")), \
                patch.object(scene_settings, "settings_revision", return_value="7" * 64), \
                patch.object(script_booleans, "live", wraps=script_booleans.live) as live, \
                patch("angelo_agent.motion_facts.after_edit", return_value=None) as motion:
            result = script_after.attach(self.window, {"success": True, "result": None, "changes": changes},
                                         clock=Clock(0.))
        self.assertEqual([row["name"] for row in result["after"]["objects"]], ["Prop 03", "Prop 07"])
        self.assertIs(live.call_args.kwargs["named"]["Prop 07"], self.objects[7])
        self.assertEqual(motion.call_args.kwargs["objects"], [self.objects[3], self.objects[7]])


if __name__ == "__main__":
    unittest.main()
