# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background jobs without Blender: the chat card, the approval, the jobs block and the scene guard.

The card's pixels, clicks and keyboard focus in the running app are covered by
``test_blender_job_card.py``.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import context_ledger, job_chat, job_host, job_manager, ui_text  # noqa: E402
from angelo_agent.approvals import approval_message, review_arguments  # noqa: E402
from angelo_agent.job_spec import JOB_NAMES  # noqa: E402
from angelo_agent.job_store import JobStore, new_record  # noqa: E402


def job(identity="j1", kind="RENDER_ANIMATION", status="running", **fields):
    request = {"kind": kind, "output": "renders/walk", "minutes": 30.0, "format": "PNG"}
    request.update(fields.pop("request", {}))
    value = new_record(identity, "chat", "call", request, now=100.0)
    value.update(status=status, started=100.0, **fields)
    value["detail"].setdefault("frames", [1, 24])
    return value


def wrap(text, width, size):
    return [(line, None) for line in text.split("\n")]


def texts(ops):
    return [op[1] for op in ops if op[0] == "text"]


def fills(ops):
    return [op[5] for op in ops if op[0] == "rect"]


class Translated:
    """Marks every string that went through ``iface_`` so a test can see what is translatable."""

    def __enter__(self):
        self.saved = dict(ui_text._pgettext)
        mark = lambda message, context=None: "‹" + message + "›"  # noqa: E731
        ui_text._pgettext.update(iface=mark, tip=mark, rpt=mark)

    def __exit__(self, *_):
        ui_text._pgettext.clear()
        ui_text._pgettext.update(self.saved)


class CardTests(unittest.TestCase):
    def layout(self, record, ui=None, has_output=False):
        return job_chat.card_layout(record, 16, 600, 480, wrap, 18, ui or {}, has_output=has_output)

    def test_every_state_has_its_buttons(self):
        cases = {"queued": [("Cancel", None)], "starting": [("Cancel", None)], "running": [("Cancel", None)],
                 "stopping": [("Stopping…", job_chat.STOPPING_REASON)], "done": [("Open", None)],
                 "cancelled": [("Open", job_chat.MOVED_REASON)], "failed": [], "interrupted": []}
        for status, expected in cases.items():
            with self.subTest(status=status):
                # A cancelled animation kept frames, but they were moved since: Open is disabled.
                record = job(status=status, receipt={"files": 2} if status in {"done", "cancelled"} else {})
                rows = job_chat.buttons(record, has_output=status == "done")
                self.assertEqual([(label, reason) for label, _, _, reason in rows], expected + [("Details", None)])
                ops, height = self.layout(record, has_output=status == "done")
                hits = [(op[2], op[3]) for op in ops if op[0] == "hit"]
                self.assertEqual(hits, [(action, "j1") if reason is None else ("disabled", reason)
                                        for _, action, _, reason in rows])
                self.assertGreater(height, 60)
        physics = job(kind="BAKE_PHYSICS", status="done", receipt={"caches": 2})
        self.assertEqual([row[0] for row in job_chat.buttons(physics, False)], ["Details"])

    def test_buttons_take_the_canvas_states(self):
        """Hover, pressed and focus are canvas_controls' for every hit; a disabled button explains itself."""
        from angelo_agent import canvas_controls
        ops = self.layout(job())[0]
        self.assertIn(job_chat.BUTTON, fills(ops))
        self.assertNotIn("hover", [op[0] for op in ops])  # No private hover bookkeeping.
        hits = [op[1:] for op in ops if op[0] == "hit"]
        self.assertEqual(canvas_controls.focus_order(hits), [("job_cancel", "j1"), ("job_details", "j1")])
        state = {"hits": hits}
        canvas_controls.move_focus(state)
        self.assertEqual(state["focus"], ("job_cancel", "j1"))
        stopping = self.layout(job(status="stopping"))[0]
        self.assertIn(job_chat.DISABLED, fills(stopping))
        disabled = [op[1:] for op in stopping if op[0] == "hit"]
        self.assertEqual(canvas_controls.focus_order(disabled), [("job_details", "j1")])  # Never focused.
        self.assertIn(("disabled", job_chat.STOPPING_REASON), [hit[1:] for hit in disabled])
        primary = fills(self.layout(job(status="done", receipt={"files": 1}), has_output=True)[0])
        self.assertIn(job_chat.PRIMARY, primary)

    def test_progress_bar_frames_and_eta(self):
        record = job(progress=0.5, eta_s=125.0, detail={"frames_done": 12, "frames_total": 24, "frames": [1, 24],
                                                        "resolution": [1920, 1080], "samples": 64, "engine": "CYCLES"})
        ops, _ = self.layout(record)
        self.assertIn("12 of 24 frames · 50% · about 2 min 5 s left", texts(ops))
        self.assertIn("Frames 1–24 · 1920×1080 · 64 samples · PNG", texts(ops))
        bars = [op for op in ops if op[0] == "rect" and op[4] == job_chat.BAR]
        self.assertEqual(len(bars), 2)
        self.assertAlmostEqual(bars[1][3], bars[0][3] / 2)
        self.assertEqual(len([op for op in self.layout(job(status="done"))[0] if op[0] == "rect" and op[4] == job_chat.BAR]), 0)

    def test_endings_explain_themselves(self):
        cases = [
            (job(status="done", ended=230.0, receipt={"files": 24}), "Finished in 2 min 10 s · 24 files"),
            (job(status="cancelled", reason="time_budget", ended=160.0,
                 receipt={"kept": "3/24 frames", "files": 3, "frames_total": 24}),
             "Stopped at its time budget · 1 min 0 s · kept 3 of 24 frames"),
            (job(status="cancelled", reason="agent", ended=101.0), "Cancelled by the agent · 1 s"),
            (job(status="interrupted", reason="app_closed", receipt={"note": "Angelo closed while this job ran."}),
             "Angelo closed while this job ran."),
            (job(status="failed", reason="Angelo ended the render early; see its Info log"),
             "Angelo ended the render early; see its Info log"),
        ]
        for record, expected in cases:
            with self.subTest(status=record["status"]):
                self.assertEqual(job_chat.progress_text(record), expected)
        self.assertEqual(job_chat.notice_text(job(status="done", ended=110.0, receipt={"files": 1, "output": "renders/walk-j1"})),
                         "Job j1 · Rendering an animation: Finished in 10 s · renders/walk-j1")
        stored = job(status="cancelled", ended=110.0, receipt={"files": 2, "kept": "2/24 frames"})
        self.assertEqual(job_chat.stored_notice(stored), "Job j1 (RENDER_ANIMATION) cancelled · renders/walk")
        message = {"role": "system", "notice": True, "job": "j1", "text": job_chat.stored_notice(stored)}
        self.assertEqual(job_chat.notice_display(message), message["text"])  # The record is gone: as stored.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = JobStore(folder.name)
        store.add(stored)
        job_manager.install(FakeManager(store))
        self.addCleanup(job_manager.install, None)
        with Translated():  # Shown from the live record, translated at display time.
            self.assertTrue(job_chat.notice_display(message).startswith("‹Job j1 · ‹Rendering an animation›: "))
        self.assertEqual([job_chat.duration(s) for s in (0, 59, 61, 600, 3725)],
                         ["0 s", "59 s", "1 min 1 s", "10 min", "1 h 2 min"])

    def test_all_card_text_is_translatable(self):
        record = job(progress=0.25, eta_s=60.0, detail={"frames_done": 1, "frames_total": 4, "frames": [1, 4]})
        with Translated():
            ops, _ = self.layout(record, {"expanded": True})
            done = job(status="failed", reason="Angelo ended the render early; see its Info log",
                       receipt={"note": "Nothing was written."})
            failed_ops, _ = self.layout(done, {"expanded": True})
        data_only = {"→ renders/walk", "PNG"}
        for value in texts(ops) + texts(failed_ops):
            with self.subTest(text=value):
                self.assertTrue("‹" in value or value in data_only, value)

    def test_expanded_details_grow_the_card(self):
        record = job(status="done", receipt={"files": 3, "note": "Kept."}, detail={"engine": "CYCLES", "frames": [1, 3]})
        closed = self.layout(record)[1]
        opened, height = self.layout(record, {"expanded": True})
        self.assertGreater(height, closed)
        self.assertIn("Undo does not remove files a job wrote.", texts(opened))
        self.assertNotIn("/", "".join(t for t in texts(opened) if not t.startswith("→")))


class ApprovalTests(unittest.TestCase):
    def test_the_card_shows_the_host_facts_and_effects(self):
        call = SimpleNamespace(name="angelo_job_start", arguments={"kind": "RENDER_ANIMATION", "output": "renders/walk",
                                                                   "samples": 999999},
                               precondition=("scene", "{}", json.dumps({"engine": "CYCLES", "resolution": [1280, 720],
                                                                        "samples": 64, "frames": [1, 24]})))
        arguments = review_arguments(call)
        message = approval_message("angelo_job_start", arguments)
        self.assertEqual(message["title"], "Render frames 1–24")
        self.assertIn("1280×720 pixels", message["body"])
        self.assertIn("64 samples (Cycles)", message["body"])  # The host's facts, not the model's claim.
        self.assertIn("Writes renders/walk in this chat's file workspace", message["body"])
        self.assertNotIn("locked", message["warning"])
        export = approval_message("angelo_job_start", {"kind": "EXPORT_USD", "output": "a.usd", "_facts": {}})
        self.assertIn("interface stays locked", export["warning"])

    def test_bakes_state_what_they_replace(self):
        texture = approval_message("angelo_job_start", {"kind": "BAKE_TEXTURE", "target": "h", "_facts": {
            "images": ["Albedo"], "samples": 16, "engine": "CYCLES", "restorable": True}})
        self.assertIn("Into: Albedo", texture["body"])
        self.assertIn("Cancel puts them back", texture["body"])
        large = approval_message("angelo_job_start", {"kind": "BAKE_TEXTURE", "target": "h", "_facts": {
            "images": ["Albedo"], "restorable": False}})
        self.assertIn("even after Cancel", large["body"])
        physics = approval_message("angelo_job_start", {"kind": "BAKE_PHYSICS", "_facts": {"caches": 2, "kept": 1}})
        self.assertIn("Leaves 1 already baked cache as it is", physics["body"])  # Counted (plural_).
        simulation = approval_message("angelo_job_start", {"kind": "BAKE_SIMULATION", "target": "h",
                                                           "_facts": {"objects": ["Smoke"]}})
        self.assertIn("Replaces an earlier bake", simulation["body"])
        on_disk = approval_message("angelo_job_start", {"kind": "BAKE_SIMULATION", "target": "h", "output": "caches/smoke",
                                                        "_facts": {"objects": ["Smoke"], "disk": True}})["body"]
        self.assertIn("Bakes to disk; these simulations then read their cache from the folder below, by a path from "
                      "this file. An earlier packed bake is replaced.\nWrites caches/smoke in this chat's file workspace",
                      on_disk)
        self.assertNotIn("packed into this file", on_disk)
        with Translated():
            translated = approval_message("angelo_job_start", {"kind": "BAKE_PHYSICS", "_facts": {"caches": 2}})
        self.assertTrue(all("‹" in line for line in translated["body"].split("\n")))
        self.assertTrue(translated["title"].startswith("‹"))


    def test_a_high_to_low_bake_states_its_objects_and_rays(self):
        facts = {"images": ["Normal"], "samples": 16, "engine": "CYCLES", "restorable": True, "target": "Low",
                 "sources": ["High", "Bolts"], "extrusion": 0.05, "ray_distance": 0.0}
        plain = approval_message("angelo_job_start", {"kind": "BAKE_TEXTURE", "target": "h", "bake_type": "NORMAL",
                                                      "output": "bakes/normal", "_facts": facts})["body"].split("\n")
        self.assertEqual(plain[:4], ["16 samples (Cycles)", "From High, Bolts onto Low",
                                     "Extrusion 0.05, no max ray distance", "Into: Normal"])
        self.assertIn("Writes bakes/normal in this chat's file workspace (new; nothing is overwritten)", plain)
        caged = approval_message("angelo_job_start", {"kind": "BAKE_TEXTURE", "target": "h", "_facts": dict(
            facts, cage="Cage", ray_distance=1.5)})["body"]
        self.assertIn("Cage: Cage", caged)
        self.assertIn("Extrusion 0.05, max ray distance 1.5", caged)
        own = approval_message("angelo_job_start", {"kind": "BAKE_TEXTURE", "target": "h", "_facts": {
            "images": ["Normal"], "restorable": True}})["body"]
        self.assertNotIn("onto", own)  # A bake of the target's own surface names no sources.
        new = approval_message("angelo_job_start", {"kind": "BAKE_TEXTURE", "target": "h", "_facts": {
            "images": ["Low Normal"], "new_image": [2048, 1024], "restorable": True}})["body"]
        self.assertIn("Into: Low Normal\nA new 2048×1024 image; Cancel removes it.", new)
        self.assertNotIn("Replaces their pixels", new)
        with Translated():
            translated = approval_message("angelo_job_start", {"kind": "BAKE_TEXTURE", "target": "h", "_facts": dict(
                facts, cage="Cage")})
        self.assertTrue(all("‹" in line for line in translated["body"].split("\n")))

    def test_a_udim_bake_names_its_tiles_and_what_cancel_can_undo(self):
        facts = {"images": ["Skin"], "samples": 16, "engine": "CYCLES", "restorable": True,
                 "tiles": {"Skin": {"baked": [1001, 1002], "count": 5}}}
        body = approval_message("angelo_job_start", {"kind": "BAKE_TEXTURE", "target": "h", "_facts": facts})["body"]
        self.assertIn("Into: Skin\nSkin: tiles 1001, 1002 of 5\nReplaces their pixels; Undo can't restore them. "
                      "Cancel puts them back.", body)
        unsaved = approval_message("angelo_job_start", {"kind": "BAKE_TEXTURE", "target": "h", "_facts": dict(
            facts, restorable=False, unsaved=["Skin"])})["body"]
        self.assertIn("Save Skin first if Cancel should put the tiles back.", unsaved)
        self.assertIn("even after Cancel", unsaved)

    def test_details_name_settings_blender_did_not_take_back(self):
        record = job(status="done", receipt={"files": 1, "unrestored": ["render.image_settings.color_depth"]})
        self.assertIn("Angelo did not take back these settings: render.image_settings.color_depth",
                      job_chat.details_text(record))


class FakeManager:
    def __init__(self, store, active=None, locks=False):
        self.store, self.queue, self._active, self._locks = store, [], active, locks

    def active(self):
        return self._active

    def record(self, identity):
        return self.store.get(identity)

    def locks_interface(self):
        return self._locks


class JobsBlockTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = JobStore(folder.name)
        self.addCleanup(job_manager.install, None)

    def test_a_message_carries_only_jobs_that_changed(self):
        job_manager.install(FakeManager(self.store))
        blocks = context_ledger.TurnBlocks()
        self.assertEqual(blocks.jobs("chat"), "")
        self.assertNotIn("jobs", blocks.after)
        running = job(progress=0.1)
        self.store.add(running)
        blocks = context_ledger.TurnBlocks()
        text = blocks.jobs("chat")
        self.assertTrue(text.startswith(context_ledger.JOBS_HEADER))
        self.assertEqual(json.loads(text[len(context_ledger.JOBS_HEADER):])[0]["progress"], 0.1)
        received = context_ledger.clean(blocks.ledger("native-1"))
        self.assertIn("j1", received["jobs"])
        self.assertEqual(context_ledger.TurnBlocks(received).jobs("chat"), "")
        running["progress"] = 0.5
        again = context_ledger.TurnBlocks(received)
        self.assertIn('"progress":0.5', again.jobs("chat"))
        self.assertEqual(context_ledger.TurnBlocks(received).jobs("other chat"), "")

    def test_a_ledger_keeps_only_valid_job_digests(self):
        good = context_ledger.digest("x")
        ledger = {"version": 1, "native_id": "native-1"}
        self.assertEqual(context_ledger.clean(dict(ledger, jobs={"j3": good}))["jobs"], {"j3": good})
        for jobs in ({"../x": good}, {"j3": "not-hex"}, ["j3"]):
            self.assertNotIn("jobs", context_ledger.clean(dict(ledger, jobs=jobs)))


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(job_manager.install, None)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = JobStore(folder.name)

    def test_without_a_job_everything_runs(self):
        job_host.guard("angelo_scene_program")
        job_manager.install(FakeManager(self.store))
        job_host.guard("angelo_execute_python")
        job_host.refuse_turn_while_locked()

    def test_a_running_job_refuses_scene_changes(self):
        job_manager.install(FakeManager(self.store, active=job(progress=0.42)))
        for name in ("angelo_scene_search", "angelo_inspect_scene", "angelo_studio_generate", "angelo_job_cancel",
                     "angelo_job_start", "angelo_agent_capabilities"):
            job_host.guard(name)
        for name in ("angelo_scene_program", "angelo_execute_python", "angelo_write_file", "angelo_apply_recipe"):
            with self.subTest(name=name), self.assertRaises(job_host.JobBusyError) as caught:
                job_host.guard(name)
            self.assertIn("Job j1 (RENDER_ANIMATION, 42%) is running", str(caught.exception))
            self.assertIn("angelo_job_cancel", str(caught.exception))
        job_host.refuse_turn_while_locked()

    def test_a_locking_job_refuses_scene_reads_and_new_turns(self):
        job_manager.install(FakeManager(self.store, active=job(kind="BAKE_PHYSICS"), locks=True))
        job_host.guard("angelo_agent_capabilities")
        job_host.guard("angelo_job_cancel")
        with self.assertRaises(job_host.JobBusyError) as caught:
            job_host.guard("angelo_scene_search")
        self.assertIn("has locked Blender", str(caught.exception))
        with self.assertRaises(RuntimeError):
            job_host.refuse_turn_while_locked()


class ToolSurfaceTests(unittest.TestCase):
    def test_jobs_are_a_deferred_family_never_offered_to_collaborators(self):
        from angelo_agent.plan_schema import DEFERRED_TOOLS, lookup
        from angelo_agent.tool_specs import COLLABORATOR_TOOLS, codex_tools
        from angelo_agent.agent_guidance import JOB_GUIDANCE
        self.assertTrue(JOB_NAMES <= DEFERRED_TOOLS)
        self.assertFalse(JOB_NAMES & COLLABORATOR_TOOLS)
        result = lookup("TOOLS", sorted(JOB_NAMES))
        self.assertEqual(result["guidance"], {"JOBS": JOB_GUIDANCE})
        deferred = next(tool for tool in codex_tools() if tool.get("type") == "namespace")
        self.assertTrue(JOB_NAMES <= {tool["name"] for tool in deferred["tools"]})
        self.assertIn("background render/bake/export jobs", deferred["description"])

    def test_a_binary_without_the_native_module_offers_no_job_tools(self):
        """Audit hardening: a stale build without _bpy._angelo_jobs declared tools that could only fail."""
        import subprocess
        probe = """
import json, sys, types
sys.path.insert(0, %r)
bpy = types.ModuleType("_bpy")
if sys.argv[1] == "with":
    bpy._angelo_jobs = types.ModuleType("_bpy._angelo_jobs")
sys.modules["_bpy"] = bpy
from angelo_agent import providers, tool_specs, codex_plan, plan_schema
names = sorted(name for name in tool_specs.TOOL_MAP if name.startswith("angelo_job_"))
text = json.dumps([tool_specs.native_plan_schema(), tool_specs.codex_tools(), tool_specs.dynamic_tools(),
                   codex_plan.output_schema(), plan_schema.deferred_index()])
print(json.dumps({"names": names, "mentioned": "angelo_job" in text or "background render" in text,
                  "instructions": "angelo_job_start" in providers.INSTRUCTIONS + providers.CLAUDE_INSTRUCTIONS,
                  "codex": "background jobs" in providers.codex_instructions(short_identifiers=True, deferred=True)}))
""" % str(ROOT / "scripts/addons_core")
        results = {}
        for variant in ("with", "without"):
            output = subprocess.run([sys.executable, "-c", probe, variant], capture_output=True, text=True, timeout=120)
            self.assertEqual(output.returncode, 0, output.stderr)
            results[variant] = json.loads(output.stdout.strip().splitlines()[-1])
        self.assertEqual(results["with"], {"names": ["angelo_job_cancel", "angelo_job_start"], "mentioned": True,
                                           "instructions": True, "codex": True})
        self.assertEqual(results["without"], {"names": [], "mentioned": False, "instructions": False, "codex": False})

    def test_the_codex_plan_schema_stays_strict_in_both_thread_variants(self):
        from angelo_agent.codex_plan import output_schema
        from angelo_agent.strict_schema import strict_problems
        from angelo_agent.team_library_spec import TEAM_NAMES
        from angelo_agent.tool_specs import TOOL_MAP
        for allowed in (None, frozenset(TOOL_MAP) - TEAM_NAMES):
            with self.subTest(team=allowed is None):
                schema = output_schema(allowed)
                self.assertEqual(strict_problems(schema), [])
                # A plan may start a job; cancelling one is an in-turn call, like every read-only tool.
                self.assertIn('"angelo_job_start"', json.dumps(schema))
                self.assertNotIn('"angelo_job_cancel"', json.dumps(schema))


if __name__ == "__main__":
    unittest.main()
