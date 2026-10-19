# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""angelo_view's RENDERED look on the host: its contract, its job, its bounds and what it leaves behind.

``rendered_view_case`` runs the real job manager, reference store and
``view_render`` over a stand-in Blender. Its bounds are in
``test_native_view_render_bounds``, regions, transparency and colour in
``test_native_view_render_region``. The compiled GUI suites
(``test_blender_rendered_view``, ``_safety`` and ``_pixels``) prove the same on
real EEVEE and Cycles renders.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rendered_view_case import LOOK, LookCase  # noqa: E402
from angelo_agent import job_spec, view_spec  # noqa: E402
from angelo_agent.job_store import JobStore, context_entries  # noqa: E402


class ContractTests(unittest.TestCase):
    def test_rendered_is_the_scene_camera_alone(self):
        self.assertEqual(view_spec.validate_view("angelo_view", {"views": ["SCENE_CAMERA"], "shading": "RENDERED"}),
                         {"targets": [], "views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 512})
        reuse = "0f6c3c87-6b4e-4a35-9b7f-6c4c8f6f0c11"
        for views in (["FRONT"], ["FIT"], ["SCENE_CAMERA", "TOP"], [reuse]):
            with self.subTest(views=views), self.assertRaisesRegex(ValueError, "SCENE_CAMERA"):
                view_spec.validate_view("angelo_view", {"views": views, "shading": "RENDERED"})

    def test_image_names_a_finished_render_and_needs_rendered(self):
        for name in ("j12", "RENDER_RESULT"):
            checked = view_spec.validate_view("angelo_view", {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": name})
            self.assertEqual(checked["image"], name)
        self.assertEqual(view_spec.JOB, job_spec.JOB_ID.pattern)
        for arguments in ({"views": ["SCENE_CAMERA"], "image": "j12"},
                          {"views": ["SCENE_CAMERA"], "shading": "LIT", "image": "RENDER_RESULT"},
                          {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "12"},
                          {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "../j1"},
                          {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "Render Result"},
                          {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": 3},
                          {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "job": "j12"}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                view_spec.validate_view("angelo_view", arguments)

    def test_lit_is_a_viewport_shading_for_any_view(self):
        # ATTRIBUTE and WEIGHT (doc/angelo/view_sources.rst) and the sculpt looks MATCAP, CURVATURE and DEPTH
        # (agent_execution.rst, "Sculpt looks") sit before LIT and RENDERED, which stay last.
        self.assertEqual(view_spec.SHADINGS, ("SOLID", "WIREFRAME", "MATCAP", "CURVATURE", "MATERIAL", "ATTRIBUTE",
                                              "WEIGHT", "DEPTH", "LIT", "RENDERED"))
        checked = view_spec.validate_view("angelo_view", {"views": ["TOP", "FIT", "SCENE_CAMERA"], "shading": "LIT"})
        self.assertEqual((checked["views"], checked["shading"]), (["TOP", "FIT", "SCENE_CAMERA"], "LIT"))

    def test_the_description_states_what_it_shows_and_what_it_costs(self):
        from angelo_agent.providers import CLAUDE_INSTRUCTIONS, CLAUDE_PLAN_INSTRUCTIONS, VIEW_GUIDANCE
        description = " ".join(view_spec.VIEW_TOOLS[0]["description"].split())
        for phrase in ("LIT is the same EEVEE draw with the scene's own lights and world", "a quick approximation",
                       "RENDER_RESULT (Blender's current Render Result", "a job ID or LAST_JOB (the latest render)",
                       "waiting for render jobs still running",
                       "one frame of a RENDER_ANIMATION chosen with frame", "in the scene's view transform",
                       "with file one of the job's outputs as it names them", "a UDIM tile, a baked map",
                       "a non-colour map as its stored values, with none",
                       "region [left, top, right, bottom] (fractions of the frame from its top-left corner)",
                       "at up to the scene's full resolution", "Without file only the combined image is shown",
                       "no separate passes, layers, stereo views or video", "OpenEXR is shown as an 8-bit preview",
                       "transparent pixels over a grey checkerboard",
                       "RENDERED", "its own engine, camera, lights, world, volumes", "as F12 renders it",
                       "at most %d samples" % view_spec.RENDER_SAMPLES, "about %d s on a heavy one" % view_spec.RENDER_SECONDS,
                       "stopped there once it has samples", "%d s of looks per request" % view_spec.RENDER_REQUEST_SECONDS,
                       "partial image says so",
                       "instead of calling them unverified", "never repeated after an edit",
                       "Its result says how long it took and how much of that was preparing the scene",
                       "on a heavy scene iterate with LIT and render at checkpoints"):
            self.assertIn(phrase, description)
        guidance = " ".join(VIEW_GUIDANCE.split())   # Codex threads read the facts text.
        self.assertIn("%d samples" % view_spec.RENDER_SAMPLES, guidance)
        # Facts-only text: what a look costs, not when to render.
        self.assertIn("seconds to about two minutes", guidance)
        self.assertEqual(view_spec.RENDER_SECONDS, 120, "the guidance says two minutes")
        self.assertNotIn("report what you saw", guidance)
        self.assertIn("LIT is that with the scene's lights and world", guidance)
        # A Claude session reads the Claude per-model view wording (claude_text).
        claude = " ".join(CLAUDE_INSTRUCTIONS.split())
        self.assertIn("%d samples" % view_spec.RENDER_SAMPLES, claude)
        self.assertIn("seconds to two minutes", claude)
        self.assertIn("report what you saw", claude)
        self.assertIn("on a heavy scene iterate with LIT and render at checkpoints", claude)
        self.assertIn("LIT is that with the scene's lights and world", claude)
        plan = " ".join(CLAUDE_PLAN_INSTRUCTIONS.split())
        # A Claude plan has the tool's arguments in its schema but not its description: what they mean is said here.
        self.assertIn("region [left, top, right, bottom] (fractions of the frame from its top-left corner) gets that "
                      "part at full detail", plan)
        self.assertIn("image (RENDER_RESULT, or a render job's ID or LAST_JOB, waited for while it renders; frame "
                      "picks one frame of an animation) shows that picture instead of rendering", plan)
        self.assertIn("ask again only for a view the result lists in stale_views", plan)   # The Claude wording (claude_text).
        self.assertIn("at most twelve (angelo_view and sculpt views together, sculpt at most six", plan)

    def test_the_host_enforces_every_limit_of_the_view_schema_itself(self):
        """The Claude plan carries no string lengths (plan_schema.without_lengths): validate_call answers for each."""
        import json
        from angelo_agent.tool_specs import TOOL_MAP, native_plan_schema, validate_call
        base = {"views": ["SCENE_CAMERA"], "shading": "RENDERED"}
        handle = "scn:" + "a" * 32 + ":%d"
        self.assertEqual(validate_call("angelo_view", dict(base, region=[0, 0, 0.5, 0.5]))["region"], [0.0, 0.0, 0.5, 0.5])
        self.assertEqual(validate_call("angelo_view", {"views": ["FRONT"], "targets": [handle % 7]})["targets"], [handle % 7])
        for arguments in (dict(base, region=[0, 0, 0.5, 0.5, 1]), dict(base, region=[0, 0, 0.5]), dict(base, region=[0, 0, 2, 1]),
                          dict(base, image="j3", frame=view_spec.MAX_FRAME + 1), dict(base, image="j" + "9" * 12),
                          dict(base, image="RENDER_RESULT" + "x" * 300), dict(base, resolution=2048),
                          {"views": ["FRONT"], "targets": [handle % 7 + "0" * 64]},
                          {"views": ["FRONT"], "targets": ["x" * 4000]},
                          {"views": ["FRONT", "BACK", "LEFT", "RIGHT"]},
                          {"views": ["FRONT"], "targets": [handle % n for n in range(1, view_spec.MAX_TARGETS + 2)]},
                          {"views": ["not-a-view-" + "x" * 500]}, dict(base, shading="RENDERED" * 40)):
            with self.subTest(arguments=str(arguments)[:80]), self.assertRaises(ValueError):
                validate_call("angelo_view", arguments)
        plan = json.dumps(native_plan_schema())
        self.assertNotIn("maxLength", plan)
        view = TOOL_MAP["angelo_view"]["inputSchema"]["properties"]
        self.assertEqual((view["region"]["minItems"], view["region"]["maxItems"]), (4, 4))

    def test_the_tool_stays_read_only_and_in_every_runtimes_core(self):
        from angelo_agent.plan_schema import DEFERRED_TOOLS
        from angelo_agent.tool_specs import COLLABORATOR_TOOLS, TOOL_MAP, codex_tools, native_plan_schema
        import json
        tool = TOOL_MAP["angelo_view"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["destructiveHint"])
        self.assertNotIn("angelo_view", DEFERRED_TOOLS)
        self.assertIn("angelo_view", COLLABORATOR_TOOLS)
        plan = json.dumps(native_plan_schema(), separators=(",", ":"))
        self.assertIn('"RENDERED"', plan)
        core = [item for item in codex_tools(None, short_identifiers=True, defer=True) if item.get("type") == "function"]
        view = next(item for item in core if item["name"] == "angelo_view")
        self.assertEqual(view["inputSchema"]["properties"]["shading"]["enum"][-2:], ["LIT", "RENDERED"])
        self.assertNotIn(job_spec.PREVIEW, job_spec.KINDS, "a look is never a kind of angelo_job_start")
        self.assertNotIn(job_spec.PREVIEW, json.dumps(job_spec.JOB_TOOLS))


class RenderTests(LookCase):
    def test_a_look_renders_small_as_a_job_and_returns_the_image(self):
        before = self.blender.scene.snapshot()
        pending = self.begin()
        self.assertIsNone(pending.poll(), "the call stays pending while Blender renders")
        record = self.manager.active()
        self.assertEqual((record["kind"], record["status"], record["call_id"]), (job_spec.PREVIEW, "running", "chat/call-1"))
        self.assertEqual(record["detail"], {"engine": "CYCLES", "resolution": [768, 432], "samples": 32, "frames": [12, 12]})
        self.assertEqual(self.blender.invoked, [{"mode": "INVOKE_DEFAULT", "write_still": True, "use_viewport": False}])
        during = self.blender.during["scene"]
        self.assertEqual((during["render"]["resolution_x"], during["render"]["resolution_y"],
                          during["render"]["resolution_percentage"]), (768, 432, 100))
        self.assertEqual((during["cycles"]["samples"], during["cycles"]["use_denoising"]), (32, True))
        image = during["render"]["image_settings"]
        self.assertEqual((image["media_type"], image["file_format"], image["color_mode"], image["color_depth"]),
                         ("IMAGE", "PNG", "RGBA", "8"), "with its alpha: transparency is shown, not written as black")
        self.assertEqual((during["render"]["use_render_cache"], during["render"]["use_auto_generate_texture_cache"],
                          during["render"]["use_file_extension"]), (False, False, False))
        self.assertTrue(Path(during["render"]["filepath"]).parent == self.references.directory)
        self.assertEqual(self.blender.during["display"], "NONE", "no render window opens")
        self.assertIn("_angelo_job_restore", during["custom"], "a file saved now is restored when it is opened")
        self.blender.progress = 0.4
        self.manager.poll()
        self.assertEqual(record["progress"], 0.4)
        self.clock.now += 14.0
        self.blender.end()
        self.manager.poll()
        result = pending.poll()
        self.assertEqual(self.blender.scene.snapshot(), before, "every setting is as the person left it")
        view = result["views"][0]
        self.assertEqual((view["shading"], view["view"], view["width"], view["height"], view["cache_hit"]),
                         ("RENDERED", "SCENE_CAMERA", 768, 432, False))
        self.assertEqual(self.references.tool_images(result), [view])
        self.assertEqual(result["rendered"], {"engine": "CYCLES", "complete": True, "seconds": 14.0, "budget_seconds": 120,
                                              "samples": 32, "scene_samples": 4096, "denoised": True,
                                              "color": {"view_transform": "AgX"},
                                              "lighting": {"shows": "NOTHING"},  # The stand-in scene has no World.
                                              "prepare_seconds": 0.0, "sampling_seconds": 14.0,
                                              "request_seconds_left": 346})
        self.assertIn("It took 14.0 s: 0.0 s preparing the scene for rendering and 14.0 s sampling.", result["note"])
        self.assertNotIn("iterate with LIT", result["note"], "a scene that prepares in no time needs no such advice")
        self.assertTrue(result["success"] and result["read_only"] and not result["scene_changed"])
        self.assertFalse(result["uncertain"] or result["side_effects"])
        self.assertIn("An actual CYCLES render of the scene camera", result["note"])
        self.assertIn("32 of the scene's 4096 samples", result["note"])
        self.assertIsNone(self.manager.active())
        self.assertEqual(self.manager.store.records, {}, "a look leaves no job record")
        self.assertEqual(context_entries(self.manager.store, "chat", now=self.clock()), {})

    def test_a_scene_with_fewer_samples_keeps_its_own(self):
        self.blender.scene.cycles.samples = 8
        _pending, result = self.run_look()
        self.assertEqual((result["rendered"]["samples"], result["rendered"]["scene_samples"]), (8, 8))

    def test_the_render_keeps_the_scenes_pixel_ratio(self):
        self.blender.scene.render.resolution_x, self.blender.scene.render.resolution_y = 1080, 1920
        pending = self.begin(resolution=512)
        self.assertEqual(self.manager.active()["detail"]["resolution"], [288, 512])
        pending.cancel()

    def test_looks_use_their_own_render_slot_and_put_the_persons_back(self):
        slots = self.blender.images[0].render_slots
        slots.active_index = 2
        self.run_look()
        self.assertEqual(self.blender.during["slot"], 8, "rendered into the slot named for Angelo")
        self.assertEqual(self.blender.during["slots"][-1], "Angelo")
        self.assertEqual(slots.active_index, 2)
        self.run_look()
        self.assertEqual([slot.name for slot in slots].count("Angelo"), 1, "one slot, reused")

    def test_a_running_look_shows_as_a_job_with_cancel_and_is_not_in_the_jobs_block(self):
        from angelo_agent import chat_cards, job_chat
        from angelo_agent.job_manager import install
        pending = self.begin()
        install(self.manager)
        try:
            row = {"role": "activity", "tool": "angelo_view", "status": "running", "call_id": "chat/call-1"}
            record = chat_cards.job_record(row)
            self.assertIs(record, self.manager.active())
            self.assertIsNone(chat_cards.job_record(dict(row, call_id="chat/other")))
            self.assertIsNone(chat_cards.job_record(dict(row, status="completed")))
            self.assertEqual(job_chat.TITLES[job_spec.PREVIEW], "Rendering a preview for the agent")
            self.assertEqual([action for _label, action, _primary, _reason in job_chat.buttons(record, False)],
                             ["job_cancel", "job_details"])
            self.assertEqual(context_entries(self.manager.store, "chat", now=self.clock()), {})
        finally:
            install(None)
            pending.cancel()


class EeveeTests(LookCase):
    engine = "BLENDER_EEVEE"
    image = dict(media_type="IMAGE", file_format="PNG", color_mode="RGBA", color_depth="16")

    def test_eevee_renders_with_its_own_samples_and_no_denoiser_setting(self):
        before = self.blender.scene.snapshot()
        pending = self.begin()
        during = self.blender.during["scene"]
        self.assertEqual(during["eevee"]["taa_render_samples"], 32)
        self.assertEqual(during["cycles"], before["cycles"], "another engine's settings are not touched")
        self.blender.progress = 1.0
        self.manager.poll()
        self.clock.now += 2.0
        self.blender.end()
        self.manager.poll()
        result = pending.poll()
        self.assertEqual(self.blender.scene.snapshot(), before)
        self.assertEqual((result["rendered"]["engine"], result["rendered"]["denoised"], result["rendered"]["samples"]),
                         ("BLENDER_EEVEE", False, 32))


class VideoFormatTests(LookCase):
    image = dict(media_type="VIDEO", file_format="FFMPEG", color_mode="RGB", color_depth="8")

    def test_a_video_output_format_is_restored_exactly(self):
        before = self.blender.scene.snapshot()
        _pending, result = self.run_look()
        self.assertEqual(self.blender.during["scene"]["render"]["image_settings"]["file_format"], "PNG")
        self.assertEqual(self.blender.scene.snapshot(), before)
        self.assertEqual(result["side_effects"], [])


class NoRenderResultTests(LookCase):
    render_result = False

    def test_the_render_result_image_blender_made_for_the_look_is_removed(self):
        self.run_look()
        self.assertEqual(self.blender.images, [])


class RefusalTests(LookCase):
    def refused(self, pattern, **arguments):
        before = self.blender.scene.snapshot()
        with self.assertRaisesRegex(RuntimeError, pattern):
            self.begin(**arguments)
        self.assertEqual(self.blender.invoked, [], "nothing started")
        self.assertEqual(self.blender.scene.snapshot(), before)
        self.assertIsNone(self.manager.active())
        self.assertEqual(self.reference_files(), [])

    def test_a_scene_without_a_camera(self):
        self.blender.scene.camera = None
        self.refused("no active camera, and a RENDERED look renders the scene camera")

    def test_stereoscopy_and_file_output_nodes_as_for_a_render_job(self):
        self.blender.scene.render.use_multiview = True
        self.refused("Stereoscopy")
        self.blender.scene.render.use_multiview = False
        node = SimpleNamespace(bl_idname="CompositorNodeOutputFile", mute=False)
        self.blender.scene.compositing_node_group = SimpleNamespace(nodes=[node])
        self.refused("File Output node")

    def test_one_render_at_a_time(self):
        pending = self.begin()
        with self.assertRaisesRegex(RuntimeError, r"Job j1 \(RENDER_PREVIEW\) is running"):
            self.begin()
        pending.cancel()

    def test_the_persons_own_render_bake_or_export_is_never_stopped(self):
        self.blender.blender_jobs["RENDER"] = True
        self.refused("Blender is rendering")
        self.blender.blender_jobs = {"OBJECT_BAKE": True}
        self.refused("a bake or an export")
        self.blender.blender_jobs, self.blender.other_jobs = {}, {"USD_EXPORT": 0.2}
        self.refused("a bake or an export")

    def test_a_staged_change_in_progress(self):
        self.staging = True
        self.refused("staged change is still in progress")

    def test_blender_refusing_to_start_leaves_nothing_behind(self):
        self.blender.start_result = {"CANCELLED"}
        before = self.blender.scene.snapshot()
        with self.assertRaisesRegex(RuntimeError, "Blender did not start the job"):
            self.begin()
        self.assertEqual(self.blender.scene.snapshot(), before)
        self.assertEqual(self.blender.images[0].render_slots.active_index, 0)
        self.assertEqual(self.manager.store.records, {})
        self.assertEqual(self.reference_files(), [])

    def test_a_look_needs_a_conversation(self):
        with self.assertRaisesRegex(RuntimeError, "Bind a conversation"):
            self.render.begin(self.blender.window, dict(LOOK), self.references, None)


class JobStoreTests(unittest.TestCase):
    def test_a_look_left_by_a_crash_is_never_reported_to_a_model(self):
        from angelo_agent.job_store import new_record
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(directory)
            look = new_record(store.allocate(), "chat", "chat/call-1", {"kind": job_spec.PREVIEW, "minutes": 2.0}, now=1.0)
            still = new_record(store.allocate(), "chat", "chat/call-2", {"kind": "RENDER_STILL", "minutes": 30.0,
                                                                         "output": "a.png"}, now=1.0)
            look["status"] = still["status"] = "running"
            store.add(look)
            store.add(still)
            self.assertEqual(list(context_entries(store, "chat", now=2.0)), ["j2"])
            store.remove("j1")
            self.assertEqual(list(JobStore(directory).records), ["j2"])


if __name__ == "__main__":
    unittest.main()
