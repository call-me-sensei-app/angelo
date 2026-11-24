# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A bake job's check pictures never hold the main thread for a render, and never end the person's work
(bake_check_host).

The dry cut's bake_maps_job stalled the main thread 0.6-0.66 s on the reference Mac (2026-11-20; v2.0.1 0.11 s): each
check picture was a Cycles render in place. A job's ``Pictures.step`` now starts each picture as Blender's render job
(``INVOKE_DEFAULT``, no render view) and reads it once that job has ended; ``check()`` (scripts) renders in place.
Starting a render job does what F12 does first (stops playback, ends every other job), so a picture starts only when
nothing would be ended, waiting a bounded time and saying so. A picture another render ended is rendered again once,
then named; the person's Render Result slot is put back only while no other render ran. Closing mid-render stops the
render and removes the check's scene on a guarded timer once the job has ended. The stand-in writes a picture when its
render starts: the gate on the job's end is ``progress``, which is what these tests hold.
On a stand-in ``bpy``; the GUI suites prove it in the app.
"""
import suite_isolation  # noqa: F401
from pathlib import Path
import sys
import tempfile
import types
from types import SimpleNamespace
import unittest
from unittest import mock

from addon_stand_in_import import import_against_stand_ins

ROOT = Path(__file__).resolve().parents[3]


class Vector(tuple):
    def __new__(cls, values=(0.0, 0.0, 0.0)):
        return super().__new__(cls, tuple(values))

    def __sub__(self, other):
        return Vector(a - b for a, b in zip(self, other))

    def to_track_quat(self, *_axes):
        return SimpleNamespace(to_euler=lambda: (0.0, 0.0, 0.0))


class _Images(list):
    def __init__(self, load):
        super().__init__()
        self.load = load

    def remove(self, image):
        pass


class _Blocks(dict):
    def __init__(self, items, removed):
        super().__init__(items)
        self.removed = removed

    def remove(self, block):
        self.removed.append(block)


def stand_in_bpy(world):
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    for name in ("Scene", "World", "Object", "Camera", "Light", "Operator", "Panel", "PropertyGroup", "Menu"):
        setattr(bpy.types, name, type(name, (), {}))
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    view = SimpleNamespace(render_display_type="WINDOW")
    preferences = SimpleNamespace(view=view, is_dirty=False, addons={},
                                  bl_rna=SimpleNamespace(properties={"is_dirty": SimpleNamespace(is_readonly=True)}))
    screen = SimpleNamespace(is_animation_playing=False, is_temporary=False)
    world["screen"] = screen
    bpy.context = SimpleNamespace(preferences=preferences, window_manager=SimpleNamespace(
        windows=[SimpleNamespace(screen=screen)]))

    def render(*args, **kwargs):
        if world.get("refuse"):
            return {"CANCELLED"}
        world["renders"].append({"invoke": "INVOKE_DEFAULT" in args, "display": view.render_display_type, **kwargs})
        for handler in list(bpy.app.handlers.render_init):
            handler(world["scene"])
        if not world.get("no_file"):
            Path(world["scene"].render.filepath).write_bytes(b"exr")   # The picture the job writes.
        if "INVOKE_DEFAULT" in args:
            world["running"] = True
            return {"RUNNING_MODAL"}
        return {"FINISHED"}

    def load(path, check_existing=False):
        return SimpleNamespace(size=(2, 2), pixels=SimpleNamespace(foreach_get=lambda array: array.fill(0.5)))
    timers = {}
    bpy.ops = SimpleNamespace(render=SimpleNamespace(render=render))
    bpy.data = SimpleNamespace(images=_Images(load))
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    handlers.render_init = []
    bpy.app = SimpleNamespace(background=False, handlers=handlers,
                              is_job_running=lambda kind: kind in world["jobs"],
                              timers=SimpleNamespace(
                                  register=lambda function, first_interval=0: timers.__setitem__(function, 0),
                                  is_registered=lambda function: function in timers,
                                  unregister=lambda function: timers.pop(function, None)),
                              translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text))
    world["timers"] = timers
    return bpy


class PicturesTests(unittest.TestCase):
    def setUp(self):
        self.world = {"renders": [], "running": False, "stops": 0, "jobs": set(), "work": []}
        self.bpy = stand_in_bpy(self.world)
        mathutils = types.ModuleType("mathutils")
        mathutils.Vector = Vector
        with mock.patch.dict(sys.modules, {"mathutils": mathutils}):
            (self.host,) = import_against_stand_ins(self.bpy, ["bake_check_host"])
        jobs = SimpleNamespace(progress=self._progress, stop=lambda scene, kind: self._stop(),
                               running_work=lambda: list(self.world["work"]))
        for name, value in (("_native", lambda: jobs),
                            ("ResultSlot", lambda: SimpleNamespace(enter=self._enter, leave=self._leave))):
            patcher = mock.patch.object(self.host, name, value, create=True)   # create: fail by assertion on base.
            patcher.start()
            self.addCleanup(patcher.stop)
        self.world["slot"] = "person"

    def _progress(self, scene, kind):
        if self.world.get("freed"):
            raise ReferenceError("StructRNA of type Scene has been removed")
        return 0.5 if self.world["running"] else None

    def _stop(self):
        self.world["stops"] += 1

    def _enter(self):
        self.world["slot"] = "angelo"

    def _leave(self):
        self.world["slot"] = "person"

    def pictures(self, blocking=False):
        pictures = self.host.Pictures.__new__(self.host.Pictures)
        folder = tempfile.mkdtemp()
        scene = SimpleNamespace(name="Angelo Bake Check", render=SimpleNamespace(filepath=""))
        self.world["scene"] = scene
        pictures.__dict__.update(
            target=None, baked=None, sources=(), size=2, samples=1, blocking=blocking, pending=None, slot=None,
            cancelled=False, retries=0, foreign_since=None, waiting_since=None, waited=0.0, gave_up=None,
            originals=[], bakeds=[], ids=[], folder=folder, scene=scene, had_result=True,
            camera=SimpleNamespace(location=None, rotation_euler=None, data=SimpleNamespace(lens=0)),
            copies=([SimpleNamespace(hide_render=False)], [SimpleNamespace(hide_render=False)]),
            views=[{"location": (1, 0, 0), "look_at": (0, 0, 0), "lens": 50}] * 2,
            scratch=SimpleNamespace(cleanup=lambda: None, name=folder))
        guarded = getattr(getattr(self.host, "edit_tool_hold", None), "guarded", None)
        pictures._close_timer = guarded(pictures._close_later) if guarded else getattr(pictures, "_close_later", None)
        return pictures

    def run_to_end(self, pictures, end_every=3):
        finished, polls = False, 0
        while not finished and polls < 80:
            finished = pictures.step()
            polls += 1
            if self.world["running"] and polls % end_every == 0:   # Blender's render job ends a few polls later.
                self.world["running"] = False
        return finished

    def test_a_job_never_renders_in_place(self):
        pictures = self.pictures()
        self.assertTrue(self.run_to_end(pictures))
        self.assertEqual(len(self.world["renders"]), 4)   # Two views, original and baked.
        for render in self.world["renders"]:
            self.assertTrue(render["invoke"], "a check picture rendered in place on the main thread")
            self.assertEqual(render["display"], "NONE")   # No render view opened over the person's layout.
            self.assertFalse(render.get("use_viewport", True))
        self.assertEqual(self.bpy.context.preferences.view.render_display_type, "WINDOW")   # Put back.
        self.assertEqual((len(pictures.originals), len(pictures.bakeds)), (2, 2))
        self.assertEqual(self.world["slot"], "person")

    def test_a_picture_is_read_only_once_its_render_job_has_ended(self):
        pictures = self.pictures()
        self.assertFalse(pictures.step())
        self.assertTrue(self.world["running"])
        for _ in range(3):
            self.assertFalse(pictures.step())   # Still rendering: nothing read, no second render.
        self.assertEqual((len(self.world["renders"]), len(pictures.originals)), (1, 0))
        self.world["running"] = False
        pictures.step()
        self.assertEqual(len(pictures.originals), 1)

    def test_no_picture_starts_while_it_would_end_the_persons_work(self):
        for blocker in ("playback", "job", "work"):
            with self.subTest(blocker=blocker):
                self.world["renders"].clear()
                self.world["screen"].is_animation_playing = blocker == "playback"
                self.world["jobs"] = {"SHADER_COMPILATION"} if blocker == "job" else set()
                self.world["work"] = [("EXPORT", "render")] if blocker == "work" else []
                pictures = self.pictures()
                for _ in range(5):
                    self.assertFalse(pictures.step())
                self.assertEqual(self.world["renders"], [], blocker)   # Nothing started, nothing ended.
                self.world["screen"].is_animation_playing, self.world["jobs"], self.world["work"] = False, set(), []
                self.assertTrue(self.run_to_end(pictures))
                self.assertEqual(len(self.world["renders"]), 4)

    def test_a_wait_past_its_bound_ends_the_check_saying_why(self):
        pictures = self.pictures()
        self.world["screen"].is_animation_playing = True
        clock = [100.0]
        with mock.patch.object(self.host.time, "monotonic", lambda: clock[0]):
            self.assertFalse(pictures.step())
            clock[0] += self.host.START_WAIT + 1
            self.assertTrue(pictures.step())
        facts = pictures.facts()
        self.assertEqual((facts["skipped"], facts["blocked_by"]), ("busy", ["playback"]))
        self.assertGreater(facts["waited_s"], self.host.START_WAIT)
        self.assertEqual(self.world["renders"], [])

    def test_a_picture_another_render_ended_is_tried_again_then_named(self):
        pictures = self.pictures()
        self.world["no_file"] = True
        pictures.step()
        self.host._count_render(SimpleNamespace(name="Scene"))   # The person's F12 ended it.
        self.world["running"] = False
        self.assertFalse(pictures.step())   # Read: no picture; tried again.
        self.assertEqual(self.world["slot"], "angelo")   # Their render stays in view: the slot is not moved back.
        self.assertIsNone(pictures.pending)
        pictures.step()
        self.host._count_render(SimpleNamespace(name="Scene"))
        self.world["running"] = False
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            pictures.step()
        self.assertEqual(len(self.world["renders"]), 2)   # Once again, then said.
        self.assertIsNone(pictures.pending)

    def test_a_refused_start_leaves_nothing_held(self):
        pictures = self.pictures()
        self.world["refuse"] = True
        with self.assertRaisesRegex(RuntimeError, "did not start"):
            pictures.step()
        self.assertIsNone(pictures.pending)
        self.assertEqual(self.world["slot"], "person")

    def test_a_freed_scene_is_never_busy(self):
        pictures = self.pictures()
        pictures.step()
        self.world["freed"] = True
        self.assertFalse(pictures.busy())

    def test_closing_mid_render_stops_it_and_removes_the_scene_on_a_guarded_timer(self):
        pictures = self.pictures()
        removed = []
        block = SimpleNamespace(name="block")
        pictures.ids = [block]
        self.bpy.data.blocks = _Blocks({"block": block}, removed)
        with mock.patch.object(self.host, "_collection", return_value="blocks"):
            pictures.step()
            pictures.close()
            self.assertEqual(self.world["stops"], 1)
            self.assertEqual(removed, [])   # Nothing freed under a running render job.
            registered = list(self.world["timers"])
            self.assertEqual(registered, [pictures._close_timer])
            self.assertIsNot(registered[0], pictures._close_later)   # The choke point's callable (edit_tool_hold).
            self.assertEqual(pictures._close_later(), self.host.CLOSE_POLL)   # Still running.
            pictures.close()
            self.assertEqual(self.world["stops"], 1)   # Stopped once.
            self.world["running"] = False
            self.assertIsNone(pictures._close_later())
        self.assertEqual(len(removed), 1)

    def test_scripts_render_in_place(self):
        pictures = self.pictures(blocking=True)
        self.world["screen"].is_animation_playing = True   # A script asked for it: no gate, no job.
        while not pictures.step():
            pass
        self.assertTrue(self.world["renders"])
        self.assertFalse(any(render["invoke"] for render in self.world["renders"]))

    def test_a_stopping_job_waits_for_the_running_picture_before_its_facts(self):
        advance = self.host.advance
        pictures = SimpleNamespace(busy=lambda: self.world["running"], cancel=lambda: self._stop())
        self.world["running"] = True
        self.assertIsNone(advance(pictures, True, lambda: self.fail("no step while stopping")))
        self.assertEqual(self.world["stops"], 1)
        self.world["running"] = False
        self.assertEqual(advance(pictures, True, None), {"skipped": "stopped"})
        broken = SimpleNamespace(busy=lambda: False, facts=lambda: {})
        self.assertIn("error", advance(broken, False, lambda: (_ for _ in ()).throw(RuntimeError("no picture"))))

    def test_the_job_polls_through_advance_and_stops_check_renders(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/job_bake_maps.py").read_text(encoding="utf-8")
        self.assertIn("bake_check_host.advance(pictures, self.stop_requested, run_step)", source)
        self.assertIn("temp_override(window=self._check_window())", source)
        self.assertNotIn("blocking=True", source)
        stop = source[source.index("    def stop(self):"):source.index("    def _release(self):")]
        self.assertIn("pictures.cancel()", stop)


if __name__ == "__main__":
    unittest.main()
