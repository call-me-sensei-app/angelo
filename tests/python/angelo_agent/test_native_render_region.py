# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render a region over the last render (doc/angelo/render_region.rst). No Blender.

On a stand-in ``bpy``: the region's fractions from a drag (clamped, ordered, from the camera's frame), the smallest
region, the scene's kept region, the agent's region converted to Blender's convention, and the RENDER_REGION job
contract (region required and checked, no other field). Then the native side, read from its sources: the render
pipeline composes a region render where a border render inserts into black, a tile lands on the shown last render,
a new render drops a finished region render's kept pixels, the display shows the last render during a region render,
the operator checks the last render before taking it and gives the scene's render region back however the render
ends, and the external engine draws a region render over the last render without clearing it. The pixels themselves
are checked in Blender by ``test_blender_render_region_pixels``; the window by ``test_blender_render_region``.
"""

import re
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "source/blender"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    same = lambda text, context=None: text
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=same, pgettext_rpt=same, pgettext_tip=same,
                                                           locale="en_US"),
                              handlers=handlers, background=True)
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    bpy.data = SimpleNamespace(images=[])
    return bpy, {"bpy": bpy, "bpy.types": bpy.types, "bpy.app": bpy.app, "bpy.app.handlers": handlers}


BPY, MODULES = fake_bpy()
with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import job_render_region, job_spec, render_region


def read(path):
    return (SOURCE / path).read_text(encoding="utf-8")


def body(text, signature):
    """The brace-matched body of the first function whose definition starts with ``signature``."""
    start = text.index(signature)
    open_brace = text.index("{", start)
    depth = 0
    for index in range(open_brace, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[open_brace:index + 1]
    raise AssertionError("unbalanced " + signature)


class FractionTests(unittest.TestCase):
    def test_a_drag_in_any_direction_is_one_ordered_region_clamped_to_the_frame(self):
        self.assertEqual(render_region.fractions((0.8, 0.9), (0.2, 0.1)), (0.2, 0.1, 0.8, 0.9))
        self.assertEqual(render_region.fractions((-0.3, 0.5), (1.4, 0.6)), (0.0, 0.5, 1.0, 0.6))

    def test_a_camera_view_point_is_read_against_the_camera_frame(self):
        self.assertEqual(render_region.frame_point((150, 100), (100, 50), (300, 250)), (0.25, 0.25))
        self.assertIsNone(render_region.frame_point((1, 1), (5, 5), (5, 9)))   # A frame with no width.

    def test_the_smallest_region_is_a_few_pixels_of_the_frame_whatever_its_size(self):
        for width, height in ((320, 240), (1920, 1080), (4096, 4096)):
            side = render_region.MIN_PIXELS
            self.assertTrue(render_region.large_enough((0, 0, side / width, side / height), width, height))
            self.assertFalse(render_region.large_enough((0, 0, (side - 1) / width, 1), width, height))

    def test_the_kept_region_is_read_only_when_it_is_a_region(self):
        self.assertEqual(render_region.stored({"angelo_render_region": [0.1, 0.2, 0.3, 0.4]}), (0.1, 0.2, 0.3, 0.4))
        for value in (None, "x", [0.1, 0.2], [0.3, 0.2, 0.1, 0.4]):
            self.assertIsNone(render_region.stored({"angelo_render_region": value}))

    def test_the_frame_size_is_the_next_render_s(self):
        scene = SimpleNamespace(render=SimpleNamespace(resolution_x=1920, resolution_y=1080, resolution_percentage=50))
        self.assertEqual(render_region.frame_size(scene), (960, 540))

    def test_the_agent_s_region_from_the_top_left_becomes_blender_s_from_the_bottom_left(self):
        for got, want in zip(job_render_region.blender_region([0.1, 0.2, 0.5, 0.9]), (0.1, 0.1, 0.5, 0.8)):
            self.assertAlmostEqual(got, want)


class JobContractTests(unittest.TestCase):
    def start(self, **args):
        return job_spec.validate_job("angelo_job_start", dict(kind="RENDER_REGION", **args))

    def test_a_region_job_takes_its_region_and_nothing_else(self):
        request = self.start(region=[0.25, 0.25, 0.75, 0.75])
        self.assertEqual(request["region"], [0.25, 0.25, 0.75, 0.75])
        self.assertNotIn("output", request)
        with self.assertRaisesRegex(job_spec.JobRequestError, "needs: region"):
            self.start()
        with self.assertRaisesRegex(job_spec.JobRequestError, "does not take: output"):
            self.start(region=[0.25, 0.25, 0.75, 0.75], output="x.png")

    def test_a_region_is_checked_as_angelo_view_s(self):
        for region in ([0.5, 0.2, 0.4, 0.9], [0, 0, 1], [0, 0, 1.2, 1], [0.1, 0.1, 0.11, 0.9]):
            with self.assertRaises(job_spec.JobRequestError):
                self.start(region=region)

    def test_the_declaration_offers_the_kind_and_states_its_field(self):
        tool = job_spec.JOB_TOOLS[0]
        self.assertIn("RENDER_REGION", tool["inputSchema"]["properties"]["kind"]["enum"])
        self.assertIn("RENDER_REGION: region*", tool["description"])
        self.assertIn("RENDER_REGION", job_spec.RENDERS)   # Reads the scene; never a scene change (request_recovery).


class NativeSourceTests(unittest.TestCase):
    def test_the_pipeline_composes_a_region_render_where_a_border_render_inserts_into_black(self):
        pipeline = read("render/intern/pipeline.cc")
        uncrop = body(pipeline, "static void render_result_uncrop(Render *re)")
        self.assertLess(uncrop.index("region_render_compose(re, cancelled)"), uncrop.index("render_result_new("))
        self.assertIn("re->display->test_break()", uncrop)
        self.assertIn("render::angelo::region_render_new_render(re)", body(pipeline, "void RE_InitState("))
        shown = body(pipeline, "void RE_AcquireResultImage(Render *re, RenderResult *rr, const int view_id)")
        self.assertIn("region_render_display_result(re)", shown)
        self.assertNotIn("re->result->", shown)

    def test_every_merged_tile_also_lands_on_the_shown_last_render(self):
        engine = read("render/intern/engine.cc")
        for function in ("void RE_engine_update_result(", "void RE_engine_end_result("):
            text = body(engine, function)
            self.assertLess(text.index("render_result_merge(re->result, result)"),
                            text.index("region_render_merge_tile(re, result)"), function)

    def test_a_cancelled_or_stopped_region_render_puts_the_last_render_back(self):
        region = read("render/intern/angelo_region_render.cc")
        compose = body(region, "bool region_render_compose(Render *re, const bool cancelled)")
        self.assertIn("apply_kept_passes(*state, base, false)", compose)
        finish = body(region, "RegionOutcome region_render_finish(Render *re)")
        self.assertIn("apply_kept_passes(*state, state->base, false)", finish)
        self.assertIn("RegionOutcome::Restored", finish)

    def test_the_operator_checks_the_last_render_before_taking_it(self):
        operator = read("editors/render/render_region.cc")
        begin = body(operator, "bool render_region_begin(")
        self.assertLess(begin.index("region_render_base_usable("), begin.index("take_base(re, ima)"))
        self.assertLess(begin.index("region_render_arm("), begin.index("scene->r.mode = "))
        end = body(operator, "void render_region_end(")
        self.assertIn("scene->r.border = job->border", end)

    def test_the_operator_its_poll_and_the_rna_read_one_render_by_one_rule(self):
        # An agent's look renders into its own slot and puts the person's back: what a region goes over (the shown
        # slot's own render, else the last render) must be what the RNA reports, or the job refuses a fine region
        # as STALE_SETTING and a look changes the person's values.
        operator = read("editors/render/render_region.cc")
        self.assertIn("region_render_shown_result(re, ima)", body(operator, "bool render_region_begin("))
        self.assertIn("region_render_shows_last(ima)", body(operator, "static RenderResult *take_base("))
        self.assertIn("region_render_shows_last(ima)", body(operator, "static bool region_revert_poll("))
        rna = read("makesrna/intern/rna_scene.cc")
        size = body(rna, "static void rna_RenderSettings_angelo_last_render_size_get(")
        self.assertIn("region_render_shown_size(", size)
        self.assertNotIn("RE_AcquireResultRead", size)
        self.assertIn("region_render_shows_last(", body(rna, "static int rna_RenderSettings_angelo_region_render_get("))
        slots = read("render/intern/angelo_region_slots.cc")
        self.assertIn("ima->render_slot == ima->last_render_slot",
                      body(slots, "bool region_render_shows_last(const Image *ima)"))
        self.assertIn("THREAD_LOCK_READ", body(slots, "bool region_render_shown_size("))

    def test_what_a_region_cannot_keep_exact_is_refused_before_anything_changes(self):
        begin = body(read("editors/render/render_region.cc"), "bool render_region_begin(")
        taken = begin.index("take_base(re, ima)")
        for refusal in ("G.is_rendering", "R_STAMP_DRAW", "compositor_renders_other_scenes(scene)",
                        "RE_seq_render_active(", "is_animation"):
            self.assertLess(begin.index(refusal), taken, refusal)

    def test_a_region_render_shows_its_passes_not_the_last_render_s_composite(self):
        shown = body(read("render/intern/pipeline.cc"),
                     "void RE_AcquireResultImage(Render *re, RenderResult *rr, const int view_id)")
        self.assertIn("rv->ibuf != nullptr && shown == re->result", shown)

    def test_main_thread_reads_of_the_state_hold_the_result_lock(self):
        region = read("render/intern/angelo_region_render.cc")
        for function in ("bool region_render_can_revert(", "bool region_render_is_reverted(",
                         "bool region_render_last_region("):
            self.assertIn("THREAD_LOCK_READ", body(region, function), function)
        self.assertIn("region_render_new_render(re)", body(read("render/intern/pipeline.cc"), "void RE_InitState("))

    def test_region_renders_keep_the_engine_and_ordinary_renders_start_without_it(self):
        begin = body(read("editors/render/render_region.cc"), "bool render_region_begin(")
        ordinary, region = begin.split('RNA_boolean_get(op->ptr, "region_over_last")', 1)[1].split("return true;", 1)
        self.assertIn("RE_FreePersistentData(scene)", ordinary)   # Nothing a region render kept leaks into F12.
        self.assertIn("R_PERSISTENT_DATA", ordinary)               # Unless the person keeps Persistent Data.
        self.assertIn("| R_PERSISTENT_DATA", region)
        self.assertIn("R_PERSISTENT_DATA", read("editors/render/render_region.cc").split("REGION_MODE_BITS =", 1)[1][:80])

    def test_every_way_a_render_ends_gives_the_scene_s_render_region_back(self):
        internal = read("editors/render/render_internal.cc")
        self.assertIn("render_region_end(rj->scene, rj->re, &rj->angelo_region, rj->reports)",
                      body(internal, "static void render_endjob(void *rjv)"))
        self.assertIn("render_region_end(rj->scene, rj->re, &rj->angelo_region, nullptr)",
                      body(internal, "static void render_freejob(void *rjv)"))
        execute = body(internal, "static wmOperatorStatus screen_render_exec(bContext *C, wmOperator *op)")
        self.assertLess(execute.index("render_region_begin("), execute.index("BKE_image_backup_render("))
        self.assertIn("render_region_end(scene, re, &region_job, op->reports)", execute)
        invoke = body(internal, "static wmOperatorStatus screen_render_invoke(")
        self.assertLess(invoke.index("render_region_begin("), invoke.index("BKE_image_backup_render("))

    def test_the_engine_draws_a_region_render_over_the_last_render(self):
        external = read("draw/engines/external/external_engine.cc")
        for clear in ("GPU_framebuffer_clear_color(dfbl->default_fb", "GPU_framebuffer_clear_depth(dfbl->default_fb"):
            start = external.index(clear)
            self.assertIn("DRW_engine_external_region_underlay(draw_ctx)", external[start - 300:start], clear)
        order = read("draw/intern/draw_view_data.hh")
        self.assertLess(order.index("callback(image);"), order.index("callback(external);"))

    def test_the_new_files_stay_under_the_size_limit(self):
        for path in ("render/intern/angelo_region_render.cc", "render/intern/angelo_region_pixels.cc",
                     "render/intern/angelo_region_slots.cc",
                     "editors/render/render_region.cc", "render/RE_angelo_region.hh"):
            self.assertLess(len(read(path).splitlines()), 500, path)


class AddonSourceTests(unittest.TestCase):
    def test_the_person_reaches_it_from_the_plus_menu_the_render_menu_and_the_render_window(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/render_region.py").read_text()
        self.assertIn('"render_region", TITLE, WHY', source)
        self.assertIn("TOPBAR_MT_render.append(render_menu)", source)
        pills = (ROOT / "scripts/addons_core/angelo_agent/render_region_pills.py").read_text()
        self.assertIn("SpaceImageEditor", pills)
        self.assertTrue(re.search(r'bl_idname = "angelo\.render_region_input"', pills))


if __name__ == "__main__":
    unittest.main()
