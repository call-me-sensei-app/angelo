# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every kind of Blender job is classified, and the work guard reads them all (work_guard, bpy_angelo_jobs_work.cc).

Restart to Update must never stop a job someone started. ``_angelo_jobs.running_work`` classifies every
``eWM_JobType`` in one switch that does not build while an enumerator is missing; these tests read the enum and the
switch as text, so a job type added without a decision (a base move to a newer Blender included) fails here too,
before any build. The real job, in a real Blender, is ``test_blender_work_guard_jobs``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import update_card, work_guard  # noqa: E402

WM_API = ROOT / "source/blender/windowmanager/WM_api.hh"
WORK = ROOT / "source/blender/python/intern/bpy_angelo_jobs_work.cc"
# Jobs that only recompute what a relaunch recomputes by itself: the only ones a restart may stop.
NOT_WORK = {"ANY", "RENDER_PREVIEW", "LOAD_PREVIEW", "FILESEL_READDIR", "ASSET_LIBRARY_LOAD",
            "ASSET_LIBRARY_GLOBAL_LISTING_LOAD", "CLIP_PREFETCH", "SEQ_BUILD_PREVIEW", "SHADER_COMPILATION",
            "STUDIOLIGHT", "FSMENU_BOOKMARK_VALIDATE", "SEQ_DRAW_THUMBNAIL", "SEQ_DRAG_DROP_PREVIEW"}


def enumerators():
    text = WM_API.read_text(encoding="utf-8")
    body = re.search(r"enum eWM_JobType \{(.*?)\n\};", text, re.S).group(1)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return re.findall(r"\bWM_JOB_TYPE_(\w+)\b", body)


def classified():
    """``{enumerator: (name, kind)}`` read from the switch."""
    text = WORK.read_text(encoding="utf-8")
    start = text.index("JobClass classify(const eWM_JobType type)")
    switch = text[start:text.index('return {"UNKNOWN", JobWork::Task};')]
    found = {}
    for enumerator, name, kind in re.findall(r'case WM_JOB_TYPE_(\w+):\s*return \{"(\w+)", JobWork::(\w+)\};', switch):
        found.setdefault(enumerator, []).append((name, kind))
    return switch, found


class ClassificationTests(unittest.TestCase):
    def test_every_job_type_is_classified_once_by_its_own_name(self):
        names = enumerators()
        self.assertIn("DPAINT_BAKE", names)
        switch, found = classified()
        self.assertEqual(sorted(found), sorted(names), "every eWM_JobType has a case, and nothing else does")
        for enumerator, cases in found.items():
            self.assertEqual(len(cases), 1, enumerator)
            self.assertEqual(cases[0][0], enumerator, "named as bpy.app.is_job_running spells it")
        self.assertNotIn("default:", switch, "a missing type must be a compile error, not a default")

    def test_the_loop_reaches_the_last_job_type(self):
        text = WORK.read_text(encoding="utf-8")
        self.assertIn("constexpr int LAST_JOB_TYPE = WM_JOB_TYPE_%s;" % enumerators()[-1], text)
        self.assertIn("value <= LAST_JOB_TYPE", text)

    def test_a_missing_case_is_a_compile_error_on_every_compiler(self):
        text = WORK.read_text(encoding="utf-8")
        for pragma in ('#  pragma clang diagnostic error "-Wswitch"', '#  pragma GCC diagnostic error "-Wswitch"',
                       "#  pragma warning(error : 4062)"):
            self.assertIn(pragma, text)
        self.assertLess(text.index("diagnostic error"), text.index("JobClass classify("))
        self.assertLess(text.index('return {"UNKNOWN", JobWork::Task};'), text.index("diagnostic pop"))

    def test_only_what_a_relaunch_recomputes_is_not_work(self):
        _switch, found = classified()
        idle = {enumerator for enumerator, [(name, kind)] in found.items() if kind == "None"}
        self.assertEqual(idle, NOT_WORK)
        kinds = {enumerator: kind for enumerator, [(name, kind)] in found.items()}
        for render in ("RENDER", "COMPOSITE", "OBJECT_BAKE", "POINTCACHE", "OBJECT_SIM_FLUID", "OBJECT_SIM_OCEAN",
                       "DPAINT_BAKE", "USD_EXPORT", "ALEMBIC_EXPORT"):
            self.assertEqual(kinds[render], "Render", render)
        for task in ("SEQ_BUILD_PROXY", "CLIP_TRACK_MARKERS", "QUADRIFLOW_REMESH", "USD_IMPORT", "UV_PACK"):
            self.assertEqual(kinds[task], "Task", task)

    def test_running_or_waiting_jobs_of_any_owner_count(self):
        text = WORK.read_text(encoding="utf-8")
        self.assertIn("WM_jobs_has_running(wm, nullptr, type)", text)
        jobs = (ROOT / "source/blender/windowmanager/intern/wm_jobs.cc").read_text(encoding="utf-8")
        early = jobs[jobs.index("bool WM_jobs_has_running(const wmWindowManager *window_manager,"):]
        self.assertIn("if (job.running || job.suspended)", early[:early.index("\n}\n")])
        self.assertIn("PyModule_AddFunctions(mod, BPY_angelo_jobs_work_methods())",
                      (ROOT / "source/blender/python/intern/bpy_angelo_jobs.cc").read_text(encoding="utf-8"))
        self.assertIn("bpy_angelo_jobs_work.cc",
                      (ROOT / "source/blender/python/intern/CMakeLists.txt").read_text(encoding="utf-8"))


def stand_in_bpy(running=()):
    return SimpleNamespace(app=SimpleNamespace(is_job_running=lambda kind: kind in running),
                           data=SimpleNamespace(scenes=[]),
                           context=SimpleNamespace(window_manager=SimpleNamespace(windows=[])))


class WorkGuardTests(unittest.TestCase):
    def setUp(self):
        idle = SimpleNamespace(busy=False, pending={}, _native_preview_cards={})
        owners = mock.patch.multiple(work_guard, _controllers=lambda: [idle], _flow=lambda: False,
                                     _generation=lambda: False, _job=lambda: False)
        owners.start()
        self.addCleanup(owners.stop)

    def native(self, **functions):
        fake = types.ModuleType("_bpy")
        fake._angelo_jobs = SimpleNamespace(progress=lambda scene, kind: None, **functions)
        patch = mock.patch.dict(sys.modules, {"_bpy": fake})
        patch.start()
        self.addCleanup(patch.stop)

    def test_every_running_blender_job_is_named_by_its_kind(self):
        jobs = []
        self.native(running_work=lambda: tuple(jobs))
        bpy = stand_in_bpy()
        self.assertEqual(work_guard.reasons(bpy), [])
        for running, expected in (([("DPAINT_BAKE", "render")], ["render"]),
                                  ([("OBJECT_SIM_OCEAN", "render")], ["render"]),
                                  ([("SEQ_BUILD_PROXY", "task")], ["task"]),
                                  ([("CLIP_SOLVE_CAMERA", "task"), ("RENDER", "render")], ["render", "task"])):
            jobs[:] = running
            with self.subTest(running=running):
                self.assertEqual(work_guard.reasons(bpy), expected)
                self.assertFalse(work_guard.idle(bpy))

    def test_the_native_answer_replaces_the_closed_lists(self):
        self.native(running_work=lambda: ())
        # A material preview (RENDER_PREVIEW) is redone by the relaunch: the classification, not the old list, says so.
        self.assertEqual(work_guard.reasons(stand_in_bpy(running={"RENDER_PREVIEW"})), [])

    def test_a_build_without_it_keeps_the_closed_lists(self):
        self.native()
        self.assertEqual(work_guard.reasons(stand_in_bpy(running={"RENDER"})), ["render"])
        self.assertEqual(work_guard.reasons(stand_in_bpy()), [])

    def test_a_native_failure_fails_closed(self):
        def broken():
            raise RuntimeError("Work status requires Blender's main thread")
        self.native(running_work=broken)
        self.assertEqual(work_guard.reasons(stand_in_bpy()), ["unknown"])

    def test_every_reason_has_a_sentence(self):
        self.assertEqual(set(work_guard.ORDER), set(update_card.WHY_WAIT))
        self.assertNotIn("render", update_card.WHY_WAIT["task"], "a proxy build is not called a render")


if __name__ == "__main__":
    unittest.main()
