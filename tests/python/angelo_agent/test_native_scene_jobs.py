# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every kind of Blender job is classified for "does it evaluate the document's scene on its thread?" (scene_jobs).

The parallel sessions' live view holds its reads and pictures, and a binding does not settle its window, while a
job's thread evaluates the document's scene or writes its data. ``_angelo_jobs.running_scene_jobs`` answers from a
switch over every ``eWM_JobType`` that does not build while a type is missing (read here as text too, so a new type
fails before any build); a build without it keeps each caller's closed list. The real job, in a real Blender, is
``test_blender_work_guard_jobs``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import test_native_work_guard as guard_tests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import parallel_sessions, program_context, scene_jobs  # noqa: E402

# Jobs whose thread neither evaluates the document's scene nor writes its data: the only ones that hold nothing.
LEAVE_THE_SCENE = {"ANY", "LOAD_PREVIEW", "FILESEL_READDIR", "ASSET_LIBRARY_LOAD", "ASSET_LIBRARY_GLOBAL_LISTING_LOAD",
                   "CLIP_BUILD_PROXY", "CLIP_PREFETCH", "SEQ_BUILD_PROXY", "SEQ_BUILD_PREVIEW", "SHADER_COMPILATION",
                   "STUDIOLIGHT", "FSMENU_BOOKMARK_VALIDATE", "SEQ_DRAW_THUMBNAIL", "SEQ_DRAG_DROP_PREVIEW",
                   "GENERATE_TEXTURE_CACHE", "COMPOSITE"}


def switch():
    """``{enumerator: True/False}`` read from ``evaluates_document``: each case's group ends in its ``return``."""
    text = guard_tests.WORK.read_text(encoding="utf-8")
    start = text.index("bool evaluates_document(const eWM_JobType type)")
    body = text[start:text.index("  /* Not an enumerator (no job is created with one): fail closed. */\n  return true;",
                                 start)]
    found, waiting = {}, []
    for enumerator, answer in re.findall(r"case WM_JOB_TYPE_(\w+):|return (true|false);", body):
        if enumerator:
            waiting.append(enumerator)
        elif answer:
            for name in waiting:
                found.setdefault(name, []).append(answer == "true")
            waiting = []
    return body, found


class ClassificationTests(unittest.TestCase):
    def test_every_job_type_is_classified_once(self):
        body, found = switch()
        self.assertEqual(sorted(found), sorted(guard_tests.enumerators()))
        for name, answers in found.items():
            self.assertEqual(len(answers), 1, name)
        self.assertNotIn("default:", body)

    def test_a_missing_case_is_a_compile_error_and_unsure_is_true(self):
        text = guard_tests.WORK.read_text(encoding="utf-8")
        start = text.index("bool evaluates_document(")
        self.assertLess(text.index("diagnostic error"), start)
        self.assertLess(start, text.index("diagnostic pop"), "inside the region where -Wswitch is an error")
        self.assertIn("if (!evaluates_document(type) || !WM_jobs_has_running(wm, nullptr, type))", text)

    def test_only_jobs_that_leave_the_scene_alone_hold_nothing(self):
        _body, found = switch()
        self.assertEqual({name for name, [answer] in found.items() if not answer}, LEAVE_THE_SCENE)
        for evaluating in ("RENDER", "RENDER_PREVIEW", "OBJECT_SIM_OCEAN", "OBJECT_SIM_FLUID", "DPAINT_BAKE",
                           "ALEMBIC_IMPORT", "UV_PACK", "LIGHT_BAKE"):
            self.assertTrue(found[evaluating][0], evaluating)


def stand_in(running=(), native=None):
    """A stand-in ``bpy`` whose ``is_job_running`` answers ``running``, and ``_bpy`` with ``native`` functions."""
    bpy = SimpleNamespace(app=SimpleNamespace(is_job_running=lambda kind: kind in running),
                          data=SimpleNamespace(scenes=[SimpleNamespace(name="Scene")]),
                          context=SimpleNamespace(window_manager=SimpleNamespace(windows=[], is_interface_locked=False)))
    fake = types.ModuleType("_bpy")
    fake._angelo_jobs = SimpleNamespace(progress=lambda scene, kind: None, **(native or {}))
    return bpy, fake


class CallerTests(unittest.TestCase):
    def use(self, bpy, fake):
        patch = mock.patch.dict(sys.modules, {"bpy": bpy, "_bpy": fake})
        patch.start()
        self.addCleanup(patch.stop)

    def both(self):
        return parallel_sessions._held_now(), program_context._jobs_run()

    def test_a_job_no_closed_list_named_holds_both(self):
        jobs = []
        self.use(*stand_in(native={"running_scene_jobs": lambda: tuple(jobs)}))
        self.assertEqual(self.both(), (False, False))
        for kind in ("DPAINT_BAKE", "OBJECT_SIM_OCEAN", "USD_IMPORT", "QUADRIFLOW_REMESH", "RENDER_PREVIEW"):
            jobs[:] = [kind]
            with self.subTest(kind=kind):
                self.assertEqual(self.both(), (True, True))

    def test_a_native_failure_holds_both(self):
        def broken():
            raise RuntimeError("Job status requires Blender's main thread")
        self.use(*stand_in(native={"running_scene_jobs": broken}))
        with mock.patch("builtins.print"):
            self.assertEqual(self.both(), (True, True))

    def test_a_build_without_it_keeps_each_callers_list(self):
        for running, expected in (({"RENDER"}, (True, True)), ({"RENDER_PREVIEW"}, (True, False)),
                                  ({"COMPOSITE"}, (True, False)), (set(), (False, False))):
            with self.subTest(running=running):
                patch = mock.patch.dict(sys.modules, dict(zip(("bpy", "_bpy"), stand_in(running))))
                with patch:
                    self.assertEqual(self.both(), expected)

    def test_running_names_the_job_types(self):
        bpy, fake = stand_in(native={"running_scene_jobs": lambda: ("LINEART",)})
        self.use(bpy, fake)
        self.assertEqual(scene_jobs.running(bpy, lambda bpy: self.fail("the native answer is used")), ("LINEART",))


if __name__ == "__main__":
    unittest.main()
