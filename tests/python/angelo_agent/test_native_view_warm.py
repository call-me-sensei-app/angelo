# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No agent view compiles material shaders on Blender's main loop (``view_warm``).

An EEVEE view (MATERIAL, LIT) is an image render, in which EEVEE compiles every shader it needs on the main thread:
the parallel benchmark's lead stalled 446-652 ms on its views after merges, 3.7-4.0 s on a cold launch (Mac 1 probes).
Without Blender, against a stand-in of the native shader state (materials drawn, missing, queued): the queue draw
runs only while a material is missing, on a private offscreen freed at once; an agent's view waits pending only on
its own scene's missing or queued shaders, queues again while the engine is cold, gives up on a material never asked
for and after ``WAIT_SECONDS``, answers for the scene it was asked for, waits out the person's tool, and keeps the
recovery barrier's evidence; the wiring is opt-in for the agent's tool only. The GUI probe measures the main loop.
"""

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))

BPY = ModuleType("bpy")
BPY.app = SimpleNamespace(background=False, timers=SimpleNamespace(register=lambda function, first_interval=0.0: None))
BPY.context = SimpleNamespace(temp_override=lambda **kwargs: _Override())
ADDED = "bpy" not in sys.modules
sys.modules.setdefault("bpy", BPY)
from angelo_agent import studio_agent, view_warm  # noqa: E402
if ADDED:
    del sys.modules["bpy"]


class _Override:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Offscreen:
    made = []

    def __init__(self, width, height):
        self.size, self.freed = (width, height), False
        Offscreen.made.append(self)

    def free(self):
        self.freed = True


def window():
    region = SimpleNamespace(type='WINDOW', data=SimpleNamespace(view_matrix="V", window_matrix="P"))
    area = SimpleNamespace(type='VIEW_3D', regions=[region], spaces=SimpleNamespace(active="space"))
    layer = SimpleNamespace(depsgraph="depsgraph")
    return SimpleNamespace(scene="Scene", view_layer=layer, screen=SimpleNamespace(areas=[area])), area


class Fixture(unittest.TestCase):
    def setUp(self):
        self.shaders, self.draws, self.busy, self.timers, self.job = (4, 2, 0), [], False, [], False
        self.live = SimpleNamespace(shader_state=lambda depsgraph, *view: self.shaders,
                                    draw=lambda *args, **kwargs: self.draws.append((args, kwargs)))
        self.window, self.area = window()
        self.clock = [100.0]
        fake = SimpleNamespace(app=SimpleNamespace(background=False, is_job_running=lambda kind: self.job,
                                                   timers=SimpleNamespace(
                                   register=lambda function, first_interval=0.0: self.timers.append(function))),
                               context=BPY.context)
        patches = [patch.object(view_warm, "bpy", fake),
                   patch.dict(sys.modules, {"_bpy": SimpleNamespace(_angelo_live=self.live),
                                            "gpu": SimpleNamespace(types=SimpleNamespace(GPUOffScreen=Offscreen))}),
                   patch.object(view_warm, "_busy", lambda window: self.busy),
                   patch.object(view_warm.time, "monotonic", lambda: self.clock[0]),
                   patch.object(view_warm, "_used", [False]), patch.dict(view_warm._soon, clear=True),
                   patch.dict(view_warm._denied, clear=True)]
        for each in patches:
            each.start()
            self.addCleanup(each.stop)
        Offscreen.made.clear()


class QueueTests(Fixture):
    def test_only_while_a_material_is_missing(self):
        self.shaders = (4, 0, 3)
        self.assertFalse(view_warm.queue(self.window, self.area), "queued ones are not drawn for again")
        self.assertEqual(self.draws, [])
        self.shaders = (4, 2, 0)
        self.assertTrue(view_warm.queue(self.window, self.area))
        (args, kwargs), = self.draws
        self.assertTrue(kwargs["compile_ahead"])
        self.assertIs(args[1], Offscreen.made[0])
        self.assertEqual(args[2], "space", "the artist's view is only read")
        self.assertTrue(Offscreen.made[0].freed, "its EEVEE instance is not kept")

    def test_never_in_the_background_and_never_raises(self):
        view_warm.bpy.app.background = True
        self.assertFalse(view_warm.queue(self.window, self.area))
        view_warm.bpy.app.background = False

        def older(*args, **kwargs):
            raise TypeError("draw() got an unexpected keyword argument 'compile_ahead'")
        self.live.draw = older
        self.assertFalse(view_warm.queue(self.window, self.area))
        self.assertTrue(Offscreen.made[-1].freed)
        self.live.shader_state = None   # A build without the reader: nothing to wait on.
        self.assertIsNone(view_warm.state(self.window))


class WaitTests(Fixture):
    def view(self):
        return {"views": [1], "timing": {}}

    def test_nothing_missing_or_queued_draws_at_once(self):
        self.shaders = (4, 0, 0)
        self.assertIsNone(view_warm.wait(self.window, self.area, lambda: 1 / 0))
        self.assertEqual(self.draws, [])

    def test_pending_on_its_own_scenes_shaders_then_the_view(self):
        self.shaders = (4, 0, 2)
        pending = view_warm.wait(self.window, self.area, self.view)
        self.assertIsInstance(pending, studio_agent.StudioPending)
        self.assertEqual(pending.status_text, view_warm.STATUS)
        self.assertIsNone(pending.poll())
        self.shaders = (4, 0, 0)
        result = pending.poll()
        self.assertEqual(result["views"], [1])
        self.assertIn("shaders_waited_ms", result["timing"])

    def test_a_cold_engine_is_queued_again_until_it_asks(self):
        self.shaders = (4, 4, 0)
        pending = view_warm.wait(self.window, self.area, self.view)
        self.assertEqual(len(self.draws), 1)
        self.clock[0] += view_warm.REQUEUE_SECONDS
        self.assertIsNone(pending.poll())
        self.assertEqual(len(self.draws), 2, "queued again")
        self.shaders = (4, 0, 4)   # Now asked for: compiling.
        self.clock[0] += 10
        self.assertIsNone(pending.poll(), "queued ones are waited for")
        self.shaders = (4, 0, 0)
        self.assertEqual(pending.poll()["views"], [1])

    def test_a_material_never_asked_for_is_waited_for_to_the_deadline_then_not_drawn(self):
        """VW8-H-2: a missing one would be compiled on the main loop by the draw: never drawn on that."""
        self.shaders = (4, 1, 0)
        drawn = []
        pending = view_warm.wait(self.window, self.area, lambda: drawn.append(1) or self.view())
        self.clock[0] += 10
        self.assertIsNone(pending.poll())
        self.clock[0] += view_warm.WAIT_SECONDS
        self.assertEqual(pending.poll(), view_warm.LATE)
        self.assertEqual(drawn, [])

    def test_never_past_the_bound(self):
        self.shaders = (4, 0, 3)
        pending = view_warm.wait(self.window, self.area, self.view)
        self.clock[0] += view_warm.WAIT_SECONDS
        self.assertEqual(pending.poll(), view_warm.LATE, "still queued: not drawn")
        self.assertGreater(pending.timeout, view_warm.WAIT_SECONDS)

    def test_a_freed_or_remade_scene_answers_as_reloaded(self):
        self.shaders = (4, 0, 1)
        pending = view_warm.wait(self.window, self.area, self.view)

        class Freed:
            def __eq__(self, other):
                raise ReferenceError("StructRNA of type Scene has been removed")
            __ne__ = __eq__
        self.window.scene = Freed()
        self.assertEqual(pending.poll()["error"], view_warm.RELOADED, "a file load or an undo meanwhile")
        scene = type("Scene", (), {})   # Compared by identity, as data-blocks are.
        self.window.scene, self.window.view_layer.name = scene(), "Layer"
        self.window.scene.name_full = "Shot"
        pending = view_warm.wait(self.window, self.area, self.view)
        self.window.scene = scene()   # An undo re-made it: same name, a new data-block.
        self.window.scene.name_full = "Shot"
        self.assertEqual(pending.poll()["error"], view_warm.RELOADED)
        pending = view_warm.wait(self.window, self.area, self.view)
        self.window.scene = scene()
        self.window.scene.name_full = "Other"
        self.assertEqual(pending.poll()["error"], view_warm.MOVED, "another scene shown: as before")

    def test_the_persons_tool_is_waited_out_and_the_scene_asked_for_is_kept(self):
        self.shaders = (4, 0, 1)
        pending = view_warm.wait(self.window, self.area, self.view)
        self.shaders, self.busy = (4, 0, 0), True
        self.assertIsNone(pending.poll())
        self.busy = False
        self.window.scene = "Other scene"
        self.assertEqual(pending.poll(), {"error": view_warm.MOVED, "read_only": True, "scene_changed": False})

    def test_the_views_refusal_and_the_recovery_barrier(self):
        self.shaders = (4, 0, 1)
        read = []
        barrier = SimpleNamespace(read=lambda name, records, result, credit: read.append((name, credit)) or result)
        controller = SimpleNamespace(recovery_barrier=barrier, executing_call=SimpleNamespace(parent_call=None))
        pending = view_warm.wait(self.window, self.area, self.view, controller)
        self.shaders = (4, 0, 0)
        pending.poll()
        self.assertEqual(read, [("angelo_view", True)], "its reads count as a direct view's do")
        self.shaders = (4, 0, 1)
        failing = view_warm.wait(self.window, self.area, lambda: (_ for _ in ()).throw(RuntimeError("Finish it")))
        self.shaders = (4, 0, 0)
        self.assertEqual(failing.poll(), {"error": "Finish it", "read_only": True, "scene_changed": False})


class BreakitTests(Fixture):
    """BREAKIT-viewwarm (pa-e2e's Sol review of 06ca45eaeb1): VW-H-3, VW-H-1, VW-M-1, VW-M-2."""

    def view(self):
        return {"views": [1], "timing": {}}

    def test_still_queued_at_the_deadline_is_never_drawn(self):
        """Finalizing a queued compile waits on the compiler without a bound (gpu_shader.cc): no draw then."""
        self.shaders = (4, 0, 1, ["Slow"], [])
        drawn = []
        pending = view_warm.wait(self.window, self.area, lambda: drawn.append(1) or self.view())
        self.clock[0] += view_warm.WAIT_SECONDS
        self.assertEqual(pending.poll(), view_warm.LATE)
        self.assertEqual(drawn, [], "never drawn")

    def test_unknown_state_or_a_failing_preparation_never_draws(self):
        """VW8-H-2: a state that cannot be read, or preparation that raised, is no reason to draw."""
        self.shaders = (4, 0, 1, ["A"], [])
        drawn = []
        pending = view_warm.wait(self.window, self.area, lambda: drawn.append(1) or self.view())
        self.live.shader_state = lambda depsgraph, *view: None   # Not evaluated, say.
        self.assertIsNone(pending.poll())
        self.clock[0] += view_warm.WAIT_SECONDS
        self.assertEqual(pending.poll(), view_warm.LATE)
        self.shaders = (4, 0, 1, ["A"], [])
        self.live.shader_state = lambda depsgraph, *view: self.shaders
        pending = view_warm.wait(self.window, self.area, lambda: drawn.append(1) or self.view())

        def broken(window, area=None):
            raise KeyError("prepared state")
        with patch.object(view_warm, "state", broken):
            result = pending.poll()
        self.assertEqual(result["error"], view_warm.BROKE.format(KeyError("prepared state")))
        self.assertEqual(drawn, [])
        del self.live.shader_state   # An older build that cannot tell: drawn at once, as before.
        self.assertIsNone(view_warm.wait(self.window, self.area, self.view))

    def test_the_look_asked_for_is_the_one_warmed(self):
        """VW-M-1: MATERIAL warming with the scene's world compiled a world the view never shows."""
        view_warm.wait(self.window, self.area, self.view, None, False)
        self.assertEqual(self.draws[-1][1].get("lit"), False, "MATERIAL: no scene lights or world")
        view_warm.wait(self.window, self.area, self.view, None, True)
        self.assertNotIn("lit", self.draws[-1][1], "LIT: the draw's own default, with both")

    def test_an_override_draws_every_object(self):
        """VW-M-2: the view layer's material override is what EEVEE draws every object with."""
        slots = [SimpleNamespace(material=SimpleNamespace(name="Own"))]
        thing = SimpleNamespace(name="Thing", type="MESH", visible_get=lambda view_layer=None: True,
                                evaluated_get=lambda depsgraph: SimpleNamespace(material_slots=slots),
                                instance_type="NONE", modifiers=[])
        self.window.view_layer.objects = [thing]
        self.window.view_layer.material_override = SimpleNamespace(name="Override")
        self.assertEqual([obj for obj, _keys in view_warm._first_draw_objects(self.window, {"Override"})], [thing])
        self.window.view_layer.material_override = None
        self.assertEqual(view_warm._first_draw_objects(self.window, {"Override"}), [])

    def test_a_failed_compile_is_named_in_the_view(self):
        """VW-H-1: EEVEE draws a failed material with its error shading; the view says which."""
        self.shaders = (4, 0, 0, [], [], ["Broken"])
        notes = view_warm.notes(self.window, "MATERIAL")
        self.assertEqual([note["code"] for note in notes], ["shaders_failed"])
        self.assertIn("Broken", notes[0]["detail"])
        self.assertEqual(view_warm.notes(self.window, "SOLID"), [], "Workbench draws no EEVEE material")
        self.shaders = (4, 0, 0, [], [])
        self.assertEqual(view_warm.notes(self.window, "LIT"), [])

    def test_a_failed_world_is_named_in_lit_only(self):
        """VW8-H-1: EEVEE draws its default world for a world whose compile failed; MATERIAL draws no scene world."""
        self.shaders = (4, 0, 0, [], [], [], ["Sky"])
        self.assertIn("Sky", view_warm.notes(self.window, "LIT")[0]["detail"])
        self.assertEqual(view_warm.notes(self.window, "MATERIAL"), [])

    def test_a_zero_slot_mesh_under_an_override_is_drawn(self):
        """VW8-M-1: EEVEE draws a mesh of no slots with its fallback slot, which the override replaces."""
        bare = SimpleNamespace(name="Bare", type="MESH", data=None, visible_get=lambda view_layer=None: True,
                               evaluated_get=lambda depsgraph: SimpleNamespace(material_slots=[]),
                               instance_type="NONE", modifiers=[])
        self.window.view_layer.objects = [bare]
        self.window.view_layer.material_override = SimpleNamespace(name="Override")
        self.assertEqual([obj for obj, _keys in view_warm._first_draw_objects(self.window, {"Override"})], [bare])

    def test_the_strip_names_each_frames_failures(self):
        motion = (ADDON / "view_motion.py").read_text(encoding="utf-8")
        loop = motion[motion.index("for index, frame in enumerate(frames):"):motion.index("    finally:")]
        self.assertIn("failed |= view_warm.failed(window, shading)", loop)
        self.assertIn("[view_warm.note(failed)] if failed else []", motion)

    def test_the_view_reports_them_and_asks_its_look(self):
        capture = (ADDON / "view_capture.py").read_text(encoding="utf-8")
        self.assertIn('uncertainty.extend(view_warm.notes(window, checked["shading"]))', capture)
        call = capture[capture.index("view_warm.view("):]
        self.assertIn('checked["shading"] == "LIT")', call[:call.index("\n")])
        warm = (ADDON / "view_warm.py").read_text(encoding="utf-8")
        helper = warm[warm.index("def view("):warm.index("def _reloaded(")]
        self.assertIn('lambda: again("SOLID")', helper, "the Solid fallback")
        self.assertIn("live_target.require(held,", helper, "the window held by identity (C2's Windows crash)")


class WatchedTests(Fixture):
    def test_materials_that_arrive_meanwhile_are_waited_for_too(self):
        """VW8-H-2: a merge's material queued during the wait would be finalized on the main loop by the draw."""
        self.shaders = (4, 0, 1, ["Asked"], [])
        drawn = []
        pending = view_warm.wait(self.window, self.area, lambda: drawn.append(1) or {"views": [1], "timing": {}})
        self.shaders = (5, 0, 1, ["Arrived later"], [])
        self.assertIsNone(pending.poll(), "the asked one is done, the later one is still queued")
        self.shaders = (5, 0, 0, [], [])
        self.assertEqual(pending.poll()["views"], [1])

    def test_first_draws_cover_what_arrived_meanwhile(self):
        thing = SimpleNamespace(name="Later", type="MESH", visible_get=lambda view_layer=None: True, data=None,
                                evaluated_get=lambda depsgraph: SimpleNamespace(material_slots=[
                                    SimpleNamespace(material=SimpleNamespace(name="Arrived later"))]),
                                instance_type="NONE", modifiers=[])
        self.window.view_layer.objects = [thing]
        only = []
        self.live.draw = lambda *args, **kwargs: only.append(kwargs.get("only"))
        self.shaders = (4, 0, 1, ["Asked"], [])
        pending = view_warm.wait(self.window, self.area, lambda: {"views": [1], "timing": {}})
        self.shaders = (5, 0, 1, ["Arrived later"], [])
        pending.poll()
        self.shaders = (5, 0, 0, [], [])
        pending.poll()
        self.assertIn([thing], only, "drawn first once compiled")

class FirstDrawTests(Fixture):
    """After the compile, every object that draws a newly compiled material, then every instancer, is drawn (Metal
    builds a pipeline at its first draw, one per shader and vertex format): a draw holds one object bringing keys not
    drawn yet and others whose keys all are; one alone after a slow draw; each tick until ``TICK_SECONDS`` of it are
    spent; then the view on a later tick."""

    def setUp(self):
        super().setUp()
        self.step, self.slow, self.cost = [1.0], set(), [0.0]   # Clock per reading; objects whose draw builds.
        self.now = [0.0]

        def perf_counter():
            self.now[0] += self.step[0]
            return self.now[0]
        timer = patch.object(view_warm.time, "perf_counter", perf_counter)
        timer.start()
        self.addCleanup(timer.stop)

        def draw(*args, **kwargs):
            only = kwargs.get("only")
            self.draws.append(only)
            self.now[0] += self.cost[0] + (0.1 if only and {obj.name for obj in only} & self.slow else 0.0)
        self.live.draw = draw

    def thing(self, name, materials, instancer=False, shown=True, kind="MESH"):
        slots = [SimpleNamespace(material=SimpleNamespace(name=m)) for m in materials]
        evaluated = SimpleNamespace(material_slots=slots)
        return SimpleNamespace(name=name, type=kind, visible_get=lambda view_layer=None: shown,
                               evaluated_get=lambda depsgraph: evaluated,
                               instance_type="COLLECTION" if instancer else "NONE", modifiers=[])

    def waiting(self, objects, names):
        self.window.view_layer.objects = objects
        self.shaders = (4, 0, 1, names)
        pending = view_warm.wait(self.window, self.area, lambda: {"views": [1], "timing": {}})
        self.shaders = (4, 0, 0, [])
        return pending

    def drawn(self):
        return [[obj.name for obj in only] for only in self.draws if only]

    def finish(self, pending):
        for _ in range(500):
            result = pending.poll()
            if result is not None:
                return result
        self.fail("never drawn")

    def test_one_draw_a_tick_then_the_view(self):
        objects = [self.thing("New", ["Fresh"]), self.thing("Old", ["Known"]), self.thing("Rig", [], True),
                   self.thing("Hidden", ["Fresh"], shown=False)]
        pending = self.waiting(objects, ["Fresh"])
        self.assertIsNone(pending.poll())
        self.assertIsNone(pending.poll())
        self.assertEqual(pending.poll()["views"], [1])
        self.assertEqual(self.drawn(), [["New"], ["Rig"]],
                         "the new material's object and the instancer; not the known or hidden one")
        self.assertTrue(all(o.freed for o in Offscreen.made), "each draw's own offscreen, freed at once")
        self.assertEqual(len(Offscreen.made), 2, "a new one per draw: a kept EEVEE instance stops sampling")

    def test_every_object_is_drawn_and_shared_pipelines_collapse(self):
        """REVIEW-VIEW-SPREAD H2 and -2 HB, MA: no object is assumed to share another's pipeline (separated parts,
        other vertex formats), yet 300 that do take a few draws."""
        self.step[0] = 0.0001
        objects = [self.thing("Cube %d" % i, ["Shared"]) for i in range(300)]
        objects.append(self.thing("Hair", ["Shared"], kind="CURVES"))
        result = self.finish(self.waiting(objects, ["Shared"]))
        self.assertEqual(result["views"], [1])
        drawn = self.drawn()
        self.assertEqual(sorted(sum(drawn, [])), sorted(obj.name for obj in objects), "each once")
        self.assertLessEqual(len(drawn), 300 // view_warm.CHUNK_MAX + 2)
        self.assertLessEqual(max(map(len, drawn)), view_warm.CHUNK_MAX)
        self.assertEqual(drawn[0], ["Hair"], "its own keys (another type): a draw of its own")

    def test_no_draw_brings_two_objects_new_keys(self):
        """REVIEW-VIEW-SPREAD-3 H2: a chunk sized by cost built 16 objects' pipelines in one 1.3 s draw."""
        self.step[0] = 0.0001
        objects = [self.thing("A%d" % i, ["F%d" % i]) for i in range(10)]
        objects += [self.thing("S%d" % i, ["Shared"]) for i in range(10)]
        self.finish(self.waiting(objects, ["F%d" % i for i in range(10)] + ["Shared"]))
        drawn = self.drawn()
        self.assertTrue(all(len(only) == 1 for only in drawn if only[0].startswith("A")), drawn)
        self.assertEqual(sorted(sum(drawn, [])), sorted(obj.name for obj in objects))

    def test_after_a_slow_draw_the_next_is_one_object_alone(self):
        self.step[0] = 0.0001
        self.slow = {"S0"}
        objects = [self.thing("S%d" % i, ["Shared"]) for i in range(10)]
        self.finish(self.waiting(objects, ["Shared"]))
        self.assertEqual([len(only) for only in self.drawn()], [view_warm.CHUNK_MAX, 1, 1])

    def test_instancers_share_draws_while_quick(self):
        self.step[0] = 0.0001
        rigs = [self.thing("Rig %d" % i, [], True) for i in range(20)]
        self.finish(self.waiting(rigs, ["Fresh"]))
        drawn = self.drawn()
        self.assertEqual(sorted(sum(drawn, [])), sorted(r.name for r in rigs), "each once")
        self.assertLessEqual(max(map(len, drawn)), view_warm.GROUP_MAX)
        self.assertLess(len(drawn), 20)

    def test_a_tick_ends_once_its_budget_is_spent(self):
        """REVIEW-VIEW-SPREAD H1: draws that each came back quickly ran the whole list in one tick."""
        self.step[0], self.cost[0] = 0.0001, view_warm.TICK_SECONDS / 4
        objects = [self.thing("A%d" % i, ["F%d" % i]) for i in range(100)]
        pending = self.waiting(objects, ["F%d" % i for i in range(100)])
        self.assertIsNone(pending.poll())
        self.assertEqual(len(self.drawn()), 1, "new pipelines only as a tick's first draw (REVIEW-VIEW-SPREAD-4 L1)")
        self.finish(pending)
        self.assertEqual(len(sum(self.drawn(), [])), 100, "every one drawn before the view")
        self.draws.clear()
        shared = [self.thing("S%d" % i, ["Shared"]) for i in range(100)]
        pending = self.waiting(shared, ["Shared"])
        self.assertIsNone(pending.poll())
        self.assertTrue(1 < len(self.drawn()) < 10, ("drawn ones go on until the budget", self.drawn()))

    def test_a_removed_object_is_skipped_and_a_failed_draw_is_never_done(self):
        """REVIEW-VIEW-SPREAD M1: a removed object is skipped, never the rest. VW9-M-1: a draw that raised is not a
        draw done: it answers BROKE and the object stays owed."""
        class Removable(SimpleNamespace):
            removed = False

            def __getattribute__(self, name):
                if name == "name" and object.__getattribute__(self, "removed"):
                    raise ReferenceError("StructRNA of type Object has been removed")
                return object.__getattribute__(self, name)
        gone = Removable(**vars(self.thing("Gone", ["F2"])))
        stale = self.thing("Stale", ["F3"])
        objects = [self.thing("First", ["F1"]), gone, stale, self.thing("Kept", ["F4"])]
        pending = self.waiting(objects, ["F1", "F2", "F3", "F4"])

        def draw(*args, **kwargs):
            if [obj.name for obj in kwargs.get("only") or []] == ["Stale"]:
                raise SystemError("value.as_pointer() failed")
            self.draws.append(kwargs.get("only"))
        self.live.draw = draw
        self.assertIsNone(pending.poll())
        gone.removed = True
        result = pending.poll()
        self.assertEqual(result["error"], view_warm.BROKE.format(SystemError("value.as_pointer() failed")))
        self.assertEqual(self.drawn(), [["First"]], "the removed one skipped; nothing drawn past the failure")

    def test_denied_twice_with_nothing_changed_answers_solid_labelled(self):
        """VW9-H-1c (the coordinator, 2026-10-25), VW10-M-2: a material that never compiles must not deny every image.
        Denied once, the agent asks again; a change in what is not ready (another UID, missing to queued) is progress
        and denied again; denied with nothing changed, or MAX_DENIALS times in a row, the view is Solid, labelled."""
        solid = lambda: {"views": ["solid"], "timing": {}, "uncertainty": []}   # noqa: E731

        def ask(shaders):
            self.shaders = shaders
            pending = view_warm.wait(self.window, self.area, lambda: 1 / 0, None, True, solid)
            self.clock[0] += view_warm.WAIT_SECONDS
            return pending.poll()
        self.assertEqual(ask((4, 1, 0, ["uid-1"], ["uid-1"], [], [], ["Stuck"])), view_warm.LATE)
        self.assertEqual(ask((4, 0, 1, ["uid-1"], [], [], [], ["Stuck"])), view_warm.LATE, "missing to queued: progress")
        result = ask((4, 0, 1, ["uid-1"], [], [], [], ["Stuck"]))
        self.assertEqual(result["views"], ["solid"], "nothing changed: Solid")
        self.assertTrue(result["uncertain"])
        note, = result["uncertainty"]
        self.assertEqual(note["code"], "shaders_not_ready")
        self.assertIn("Stuck", note["detail"])
        self.assertIn("compiling", note["detail"])
        self.assertIn("material display colours under studio light", note["detail"], "what Solid shows (VW10-M-1)")
        view_warm._denied.clear()
        for index in range(view_warm.MAX_DENIALS - 1):   # Alternating states: bounded all the same.
            self.assertEqual(ask((4, 0, 1, ["uid-%d" % index], [], [], [], ["A"])), view_warm.LATE)
        self.assertEqual(ask((4, 0, 1, ["uid-x"], [], [], [], ["A"]))["views"], ["solid"])

    def scene(self, name):
        self.window.scene = SimpleNamespace(name_full=name)
        self.window.view_layer.name = "Layer"

    def test_the_view_layer_is_held_by_identity_not_name(self):
        """BREAKIT-lt-pa LT-M-1: by name, renaming the layer during the wait answered MOVED, and another layer given
        the saved name and shown was drawn as if it were the one asked for."""
        self.scene("A")
        self.shaders = (4, 0, 1, ["uid-a"], [], [], [], ["A material"])
        pending = view_warm.wait(self.window, self.area, lambda: {"views": [1], "timing": {}})
        self.window.view_layer.name = "Renamed"   # The same layer, renamed.
        self.shaders = (4, 0, 0, [], [], [], [], [])
        first = pending.poll()
        result = first if first is not None else pending.poll()   # A first-draw tick may come first.
        self.assertEqual(result["views"], [1], "a rename is the same layer")
        self.shaders = (4, 0, 1, ["uid-a"], [], [], [], ["A material"])
        pending = view_warm.wait(self.window, self.area, lambda: {"views": [1], "timing": {}})
        asked = self.window.view_layer
        other = type("ViewLayer", (), {})()   # Another layer (compared by identity, as Blender's are), same name.
        other.name, other.depsgraph = asked.name, asked.depsgraph
        self.window.view_layer = other
        self.assertIn((pending.poll() or {}).get("error"), (view_warm.MOVED, view_warm.RELOADED),
                      "never drawn as the other")

    def test_owed_work_belongs_to_its_scene(self):
        """VW11-H-1: a window that switched scenes adopted the other scene's owed objects and drew them on this
        depsgraph, where they draw nothing, counting them done; MOVED dropped what was owed."""
        self.scene("A")
        self.shaders = (4, 0, 1, ["uid-a"], [], [], [], ["A material"])
        pending = view_warm.wait(self.window, self.area, lambda: 1 / 0)
        owner_a = view_warm._key(self.window)
        self.scene("B")
        self.assertEqual(pending.poll()["error"], view_warm.MOVED)
        self.assertIn(owner_a, view_warm._soon, "kept for A")
        self.shaders = (4, 0, 0, [], [], [], [], [])
        self.assertIsNone(view_warm.wait(self.window, self.area, lambda: {"views": [1], "timing": {}}),
                          "B owes nothing: A's work is not B's")
        self.assertIn(owner_a, view_warm._soon, "still A's")

    def test_an_emptied_list_is_listed_again_when_the_scene_changed(self):
        """VW11-H-2: an empty list of owed objects discarded what joined after it drained."""
        self.scene("A")
        owner = view_warm._key(self.window)
        view_warm._soon[owner] = {"names": {"uid-1"}, "objects": [], "pace": {"lit": True}, "run": None,
                                  "deadline": 0.0, "generation": 1}
        self.shaders = (4, 0, 0, [], [], [], [], [])
        with patch.object(view_warm, "_generation", lambda window: 2):
            pending = view_warm.wait(self.window, self.area, lambda: {"views": [1], "timing": {}})
        self.assertIsNotNone(pending, "evaluated since it drained: listed again before the view")

    def test_the_solid_note_says_which_limit_and_a_capture_resets_it(self):
        """VW11-M-2: one text for no progress, another for too many tries; an immediate capture resets the count."""
        solid = lambda: {"views": ["solid"], "timing": {}, "uncertainty": []}   # noqa: E731

        def ask(shaders):
            self.shaders = shaders
            pending = view_warm.wait(self.window, self.area, lambda: 1 / 0, None, True, solid)
            if pending is None:
                return None
            self.clock[0] += view_warm.WAIT_SECONDS
            return pending.poll()
        for index in range(view_warm.MAX_DENIALS - 1):
            ask((4, 0, 1, ["uid-%d" % index], [], [], [], ["A"]))
        bounded = ask((4, 0, 1, ["uid-x"], [], [], [], ["A"]))
        self.assertIn("tries in a row", bounded["uncertainty"][0]["detail"])
        self.assertNotIn("no progress", bounded["uncertainty"][0]["detail"])
        self.assertIsNone(ask((4, 0, 0, [], [], [], [], [])), "drawn at once")
        self.assertEqual(view_warm._denied, {}, "a capture of the look asked for resets the count")

    def test_owed_work_outlives_a_denial(self):
        """VW10-H-1: what was owed lived only in the pending call; the next request saw nothing missing and drew it
        all in one burst."""
        self.window.view_layer.objects = [self.thing("Drawer", ["uid-1"])]
        self.shaders = (4, 0, 1, ["uid-1"], [], [], [], ["A"])
        pending = view_warm.wait(self.window, self.area, lambda: 1 / 0)
        self.clock[0] += view_warm.WAIT_SECONDS
        self.assertEqual(pending.poll(), view_warm.LATE)
        self.assertIn(view_warm._key(self.window), view_warm._soon, "kept for the next request")
        self.shaders = (4, 0, 0, [], [], [], [], [])   # Compiled meanwhile.
        again = view_warm.wait(self.window, self.area, lambda: {"views": [1], "timing": {}})
        self.assertIsNotNone(again, "its first draws are still owed: never drawn at once")

    def test_an_edit_after_its_first_draw_draws_it_again(self):
        """VW10-H-2: an in-place edit recompiles a material under the same UID."""
        pace = {"books": {"LIT": ({(1, frozenset({("uid-1", ("MESH",))}))}, {("uid-1", ("MESH",))})}}
        view_warm._redo(pace, {"uid-1"})
        self.assertEqual(pace["books"]["LIT"], (set(), set()))

    def test_first_draws_are_kept_per_look(self):
        """A merge warmed for MATERIAL (the last view's look) left the LIT view to build every LIT pipeline in its one
        draw (506 ms, Mac 1 probe v3, round 11): drawn for one look is not drawn for the other."""
        thing = SimpleNamespace(name="Thing", type="MESH", visible_get=lambda view_layer=None: True,
                                evaluated_get=lambda depsgraph: None, instance_type="NONE", modifiers=[])
        self.live.draw = lambda *args, **kwargs: self.draws.append(kwargs.get("only"))
        entry = (thing, frozenset({("uid-1", ("MESH",))}))
        pace = {"lit": False}
        view_warm.first_draws(self.window, [entry], self.area, pace)
        pace["lit"] = True
        view_warm.first_draws(self.window, [entry], self.area, pace)
        self.assertEqual(len([only for only in self.draws if only]), 2, "drawn again for LIT")
        view_warm.first_draws(self.window, [entry], self.area, pace)
        self.assertEqual(len([only for only in self.draws if only]), 2, "not a third time")

    def test_a_failed_world_override_is_named_in_material_too(self):
        """VW10-H-3: Material Preview draws the view layer's world override."""
        self.shaders = (4, 0, 0, [], [], [], ["Override Sky"], [], True)
        self.assertIn("Override Sky", view_warm.notes(self.window, "MATERIAL")[0]["detail"])
        self.shaders = (4, 0, 0, [], [], [], ["Scene Sky"], [], False)
        self.assertEqual(view_warm.notes(self.window, "MATERIAL"), [], "MATERIAL draws a studio light instead")


class SoonTests(Fixture):
    def test_only_where_the_agent_drew_an_eevee_view_and_once_idle(self):
        view_warm.soon(self.window)
        self.assertEqual(self.timers, [], "never compiles what this session's agent does not look at")
        view_warm._used[0] = True
        view_warm.soon(self.window)
        run, = self.timers
        self.busy = True
        self.assertEqual(run(), 0.25, "waits for the person's tool or playback")
        self.busy = False
        self.assertIsNone(run())
        self.assertEqual(len(self.draws), 1)

    def merged(self):
        """A merge's queue of a material named "Part", the object that draws it, and the spreading timer."""
        view_warm._used[0] = True
        part = FirstDrawTests.thing(self, "Part", ["Part"])
        self.window.view_layer.objects = [part]
        self.live.draw = lambda *args, **kwargs: self.draws.append(kwargs.get("only"))
        self.shaders = (4, 1, 0, ["Part"])
        view_warm.soon(self.window)
        run, = self.timers
        self.assertIsNone(run())
        spread = self.timers[-1]
        self.assertIsNot(spread, run, "the first draws follow once compiled")
        return spread

    def test_a_merges_materials_get_their_first_draws_once_compiled(self):
        """REVIEW-VIEW-SPREAD M3: a view asked after the compile built every pipeline inside itself."""
        spread = self.merged()
        self.shaders = (4, 0, 1, ["Part"])
        self.assertEqual(spread(), 0.1, "still compiling")
        self.shaders = (4, 0, 0, [])
        self.assertIsNone(spread())
        self.assertEqual([[o.name for o in only] for only in self.draws if only], [["Part"]])
        self.assertEqual(view_warm._soon, {})

    def test_a_view_asked_before_they_are_done_finishes_them_first(self):
        spread = self.merged()
        self.shaders = (4, 0, 0, [])   # Compiled; its first draw not yet run.
        pending = view_warm.wait(self.window, self.area, lambda: {"views": [1], "timing": {}})
        self.assertIsNotNone(pending, "nothing pending, yet the merge's pipelines are not built")
        self.assertIsNone(spread(), "the view took them over")
        self.assertIsNone(pending.poll())
        self.assertEqual(pending.poll()["views"], [1])
        self.assertEqual([[o.name for o in only] for only in self.draws if only], [["Part"]], "drawn once")

    def test_first_draws_owed_past_the_deadline_go_to_the_next_view(self):
        """REVIEW-VIEW-SPREAD-4 M1: dropped at the deadline (the person busy, playing back), the next view's image
        render built all of them at once."""
        spread = self.merged()
        self.shaders = (4, 0, 0, [])
        self.clock[0] += view_warm.WAIT_SECONDS + 1
        self.assertIsNone(spread())
        self.assertIn(view_warm._key(self.window), view_warm._soon, "kept, owed")
        pending = view_warm.wait(self.window, self.area, lambda: {"views": [1], "timing": {}})
        self.assertIsNotNone(pending, "the view takes them over")

    def test_a_merge_warmed_for_one_look_owes_the_other_its_first_draws(self):
        spread = self.merged()   # Warmed for LIT (the last view's look).
        self.shaders = (4, 0, 0, [])
        self.assertIsNone(spread())
        view_warm._soon[view_warm._key(self.window)] = {"names": {"Part"}, "objects": [], "run": None,
                                                         "deadline": 0.0, "pace": {"lit": True}}
        pending = view_warm.wait(self.window, self.area, lambda: {"views": [1], "timing": {}}, None, False)
        self.assertIsNotNone(pending, "MATERIAL: the LIT first draws do not count")

    def test_an_edit_during_spreading_is_drawn_again(self):
        """VW11-H-3: merge spreading forgot nothing after an in-place edit, so the view built the new shaders."""
        spread = self.merged()
        entry = view_warm._soon[view_warm._key(self.window)]
        entry["pace"]["books"] = {"LIT": ({(1, frozenset({("Part", ("MESH",))}))}, {("Part", ("MESH",))})}
        self.shaders = (4, 0, 1, ["Part"])   # Edited: compiling again.
        self.assertEqual(spread(), 0.1)
        self.assertEqual(entry["pace"]["books"]["LIT"], (set(), set()), "done work forgotten for the edit")

    def test_a_later_merge_keeps_an_earlier_ones(self):
        """REVIEW-VIEW-SPREAD-2 MB: a second merge's queue dropped the first one's first draws."""
        self.merged()
        self.timers.clear()
        self.shaders = (4, 1, 0, ["Second"])
        view_warm.soon(self.window)
        self.timers[0]()
        self.assertEqual(view_warm._soon[view_warm._key(self.window)]["names"], {"Part", "Second"})


class WiringTests(unittest.TestCase):
    def source(self, name):
        return (ADDON / name).read_text(encoding="utf-8")

    def test_the_agents_view_waits_and_other_callers_draw_at_once(self):
        capture = self.source("view_capture.py")
        execute = capture[capture.index("def execute("):]
        self.assertIn("wait_shaders=False)", execute[:execute.index("\n")])
        self.assertLess(execute.index("view_warm.view("), execute.index('if "frames" in checked'))
        self.assertIn("view_capture.execute(self.window,arguments,self.observation_store,self.studio_context,True)",
                      self.source("blender_tools.py"))
        for name in ("flow_capture.py", "team_library_save.py"):
            call = self.source(name)
            call = call[call.index("view_capture.execute("):]
            self.assertNotIn("True", call[:call.index(")")], name + " reads its result at once")

    def test_a_refused_view_never_waits_or_draws_first(self):
        """The approval mode's refusal comes first (T4 C3 audit): a view Read-only, Ask or a sculpt session refuses is
        answered at once by the capture (a cache hit, else the refusal), never after a wait and its first draws."""
        import angelo_agent
        waits, area = [], SimpleNamespace(spaces=SimpleNamespace(active="space"))
        capture = SimpleNamespace(_view3d_areas=lambda window: [area], _draw_area=lambda areas: areas[0],
                                  execute=lambda *args: "drawn")
        target = SimpleNamespace(window_ref=lambda window: window, require=lambda held, why: held)
        for refused in (True, False):
            asked = []

            def admit(space, shading, controller, refused=refused):
                asked.append((space, shading, controller))
                if refused:
                    raise RuntimeError("Read-only: the view's display settings would run add-on Python")
            policy = SimpleNamespace(admit_display=admit)
            modules = {"angelo_agent.view_capture": capture, "angelo_agent.view_policy": policy,
                       "angelo_agent.live_target": target}
            with patch.dict(sys.modules, modules), patch.multiple(angelo_agent, create=True, view_capture=capture,
                                                                   view_policy=policy, live_target=target), \
                    patch.object(view_warm, "wait", lambda *args: waits.append(args) or "pending"):
                answer = view_warm.view("window", {"shading": "MATERIAL"}, "refs", "chat", False)
            self.assertEqual(asked, [("space", "MATERIAL", "chat")])
            self.assertEqual(answer, None if refused else "pending")
        self.assertEqual(len(waits), 1, "only the admitted view waits")

    def test_a_strip_refused_by_its_frame_policy_never_prepares_shaders(self):
        """Display changes can be safe while a strip's frame changes, playback or job state refuse the request."""
        import angelo_agent
        area = SimpleNamespace(spaces=SimpleNamespace(active="space"))
        capture = SimpleNamespace(_view3d_areas=lambda window: [area], _draw_area=lambda areas: areas[0],
                                  display_keys=lambda shading: (shading,), execute=lambda *args: "drawn")
        target = SimpleNamespace(window_ref=lambda window: window, require=lambda held, why: held)
        policy = SimpleNamespace(admit_display=lambda *args: None, mode_of=lambda controller: "ASK")
        for lit in (False, True):
            for refusal in ("frame handler", "playback", "active job", None):
                with self.subTest(lit=lit, refusal=refusal):
                    asked, waits = [], []
                    motion = SimpleNamespace(_refusal=lambda *args: asked.append(args) or refusal)
                    modules = {"view_capture": capture, "view_policy": policy, "live_target": target,
                               "view_motion": motion}
                    with patch.dict(sys.modules, {"angelo_agent." + k: v for k, v in modules.items()}), \
                            patch.multiple(angelo_agent, create=True, **modules), \
                            patch.object(view_warm, "wait", lambda *args: waits.append(args) or "pending"):
                        answer = view_warm.view("window", {"frames": [1, 3]}, "refs", "chat", lit)
                    self.assertEqual(asked, [("window", "ASK", ("LIT" if lit else "MATERIAL",))])
                    self.assertEqual(answer, None if refusal else "pending")
                    self.assertEqual(len(waits), 0 if refusal else 1)

    def test_a_merge_queues_its_parts_shaders(self):
        after = self.source("merge_framing.py")
        after = after[after.index("def after_merge"):]
        self.assertLess(after.index("view_warm.soon(window)"), after.index("return"))


if __name__ == "__main__":
    unittest.main()
