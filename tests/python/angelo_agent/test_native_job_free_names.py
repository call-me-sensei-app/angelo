# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A job never moves, renames or replaces a file; a taken name gets the next free one (job_output_names, 1.0).

Re-rendering a still after a fix, under the name the person asked for, was refused as "already exists" and ended
three recorded turns (doc/angelo/long_jobs.rst, "Taken names"). Pinned here on real files, without Blender: an
existing file or folder is never changed (bytes, file ID); a taken name publishes under the next free one, said by
the approval and the start result and named by the receipt; a name that appears between approval and publication,
or while a set is published, is never overwritten; a name taken at approval is never written even if freed; names
the file system treats as the same (APFS: case, Unicode normalisation) count as taken; past every name nothing is
published; and "already exists" never reaches the model as a refusal. Every test also runs on the Windows handle
backend through ``fake_nt_files.FakeNtApi``.
"""

import ast
import json
import os
import sys
import tempfile
import types
import unicodedata
import unittest

from host_test_policy import environment_skip
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import checkpoint_filesystem as cf  # noqa: E402
from angelo_agent import job_host, job_outputs  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.job_approvals import message as approval  # noqa: E402
from angelo_agent.job_output_names import MAX_NAMES, candidates, free_names  # noqa: E402
from angelo_agent.job_store import context_entry, new_record  # noqa: E402
from fake_nt_files import FakeNtApi  # noqa: E402

STILL = {"kind": "RENDER_STILL", "output": "castle-final.png", "format": "PNG", "minutes": 30.0, "queue": False}


class PosixFreeNames(unittest.TestCase):
    windows = False

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-free-names-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "workspace"
        self.root.mkdir()
        self.files = FileWorkspace(self.root)
        self.controller = types.SimpleNamespace(files=self.files, workspace=None, state={"id": "chat-a"})
        if self.windows:
            patcher = patch.object(cf, "_windows_api", return_value=FakeNtApi())
            patcher.start()
            self.addCleanup(patcher.stop)

    def publish(self, relative, identity, data=b"new", *, folder=False, extra=(), before=None):
        """Admission (its ``skip_requested`` as job_host takes it), then staging and publication as a runner does."""
        output = job_outputs.admit_output(self.files, relative)
        skip = os.path.lexists(output)
        staging = job_outputs.Staging(self.root, output, identity, skip).create()
        if folder:
            (staging.path / "frame_0001.png").write_bytes(data)
        else:
            (staging.path / "main").write_bytes(data)
            for name in extra:
                (staging.path / name).write_bytes(b"dependency")
        if before is not None:
            before()   # Something appears between approval and publication.
        published = staging.publish_folder() if folder else staging.publish_file("main")
        relative_output = staging.relative_output()
        staging.close()
        return published, relative_output

    def snapshot(self, path):
        info = os.stat(path)
        return path.read_bytes() if path.is_file() else sorted(os.listdir(path)), info.st_ino

    # Never changed -----------------------------------------------------------------------------------------------

    def test_a_job_never_changes_an_existing_file_or_folder(self):
        (self.root / "castle-final.png").write_bytes(b"the person's render")
        (self.root / "anim").mkdir()
        (self.root / "anim" / "frame_0001.png").write_bytes(b"theirs")
        kept = {name: self.snapshot(self.root / name) for name in ("castle-final.png", "anim")}
        self.assertEqual(self.publish("castle-final.png", "j2")[1], "castle-final-j2.png")
        self.assertEqual(self.publish("anim", "j3", folder=True)[1], "anim-j3")
        for name, state in kept.items():
            self.assertEqual(self.snapshot(self.root / name), state, name)
        self.assertEqual((self.root / "castle-final-j2.png").read_bytes(), b"new")

    def test_a_taken_name_gets_the_next_free_one_and_the_receipt_names_it(self):
        for name in ("castle-final.png", "castle-final-j2.png", "castle-final-j2-2.png"):
            (self.root / name).write_bytes(b"taken")
        published, relative = self.publish("castle-final.png", "j2")
        self.assertEqual(relative, "castle-final-j2-3.png")
        self.assertEqual(published, self.root / "castle-final-j2-3.png")
        record = new_record("j2", "chat-a", "call", STILL, now=0.0)
        record.update(status="done", receipt={"output": relative, "files": 1})
        self.assertEqual(context_entry(record)["output"], "castle-final-j2-3.png")   # The jobs block names it.

    def test_a_set_moves_whole_into_the_next_free_folder(self):
        (self.root / "scene.usd").write_bytes(b"theirs")
        (self.root / "scene-j4").mkdir()   # Its first fallback folder is taken too.
        published, relative = self.publish("scene.usd", "j4", extra=("tex.png",))
        self.assertEqual(relative, "scene-j4-2/scene.usd")
        self.assertEqual(sorted(os.listdir(published.parent)), ["scene.usd", "tex.png"])
        self.assertEqual((self.root / "scene.usd").read_bytes(), b"theirs")

    # Races ---------------------------------------------------------------------------------------------------------

    def test_a_name_that_appears_before_publication_is_never_overwritten(self):
        def appear():
            (self.root / "castle-final.png").write_bytes(b"appeared")
            (self.root / "castle-final-j5.png").write_bytes(b"appeared too")
        _published, relative = self.publish("castle-final.png", "j5", before=appear)
        self.assertEqual(relative, "castle-final-j5-2.png")
        self.assertEqual(((self.root / "castle-final.png").read_bytes(), (self.root / "castle-final-j5.png").read_bytes()),
                         (b"appeared", b"appeared too"))

    def test_a_set_whose_file_name_appears_while_it_publishes_moves_whole_and_leaves_nothing(self):
        real = job_outputs.Staging._publish_set

        def racing(staging, target, main, dependencies, budget, inside=False):
            if not inside:   # Published at the name itself: a dependency's name appears right after the main file.
                (self.root / "tex.png").write_bytes(b"appeared")
            return real(staging, target, main, dependencies, budget, inside)
        with patch.object(job_outputs.Staging, "_publish_set", racing):
            _published, relative = self.publish("scene.usd", "j6", extra=("tex.png",))
        self.assertEqual(relative, "scene-j6/scene.usd")
        self.assertEqual(sorted(os.listdir(self.root)), ["scene-j6", "tex.png"])   # Nothing half published.
        self.assertEqual((self.root / "tex.png").read_bytes(), b"appeared")

    def test_a_name_taken_at_approval_is_never_written_even_if_freed(self):
        (self.root / "castle-final.png").write_bytes(b"theirs")
        output = job_outputs.admit_output(self.files, "castle-final.png")
        staging = job_outputs.Staging(self.root, output, "j7", os.path.lexists(output)).create()
        (staging.path / "main").write_bytes(b"new")
        (self.root / "castle-final.png").unlink()   # Freed before the job ends.
        staging.publish_file("main")
        self.assertEqual(staging.relative_output(), "castle-final-j7.png")   # Where its approval said.
        staging.close()
        self.assertFalse((self.root / "castle-final.png").exists())

    def test_past_every_name_nothing_is_published_and_nothing_changes(self):
        for name in candidates("castle-final.png", "j8", False, False):
            (self.root / name).write_bytes(b"taken")
        output = job_outputs.admit_output(self.files, "castle-final.png")
        staging = job_outputs.Staging(self.root, output, "j8").create()
        (staging.path / "main").write_bytes(b"new")
        with self.assertRaises(job_outputs.OutputMoved) as caught:
            staging.publish_file("main")
        self.assertEqual(str(caught.exception), job_outputs.TAKEN)
        staging.discard()
        self.assertEqual(len(os.listdir(self.root)), MAX_NAMES)
        self.assertTrue(all(path.read_bytes() == b"taken" for path in self.root.iterdir()))

    # Same names to the file system (APFS) -------------------------------------------------------------------------

    def test_names_the_file_system_folds_together_count_as_taken(self):
        probe = self.root / "Probe"
        probe.write_bytes(b"")
        folds = os.path.exists(self.root / "probe")
        probe.unlink()
        if self.windows or not folds:
            self.skipTest(environment_skip("a case-sensitive file system"))
        (self.root / "CASTLE-FINAL.png").write_bytes(b"theirs")
        (self.root / unicodedata.normalize("NFD", "café-j9.png")).write_bytes(b"theirs too")
        self.assertEqual(self.publish("castle-final.png", "j9")[1], "castle-final-j9.png")
        (self.root / unicodedata.normalize("NFD", "café.png")).write_bytes(b"theirs")
        relative = self.publish(unicodedata.normalize("NFC", "café.png"), "j9")[1]
        self.assertEqual(unicodedata.normalize("NFC", relative), unicodedata.normalize("NFC", "café-j9-2.png"))
        self.assertEqual(sorted(path.read_bytes() for path in self.root.iterdir() if b"theirs" in path.read_bytes()),
                         [b"theirs", b"theirs", b"theirs too"])

    # What the approval, the start result and the model read ------------------------------------------------------

    def test_the_approval_and_the_start_result_name_where_it_goes(self):
        (self.root / "renders").mkdir()
        (self.root / "renders/castle.png").write_bytes(b"theirs")
        location = job_host.output_location(self.controller, "renders/castle.png", "j10", "file", True)
        self.assertEqual((location["writes"], location["if_taken"]), ("renders/castle-j10.png", "renders/castle-j10-2.png"))
        body = approval("angelo_job_start", dict(STILL, output="renders/castle.png", _facts={
            "writes": location["writes"], "taken": True, "requested": "renders/castle.png"}))
        self.assertIn("renders/castle.png already exists and is never overwritten; this job writes "
                      "renders/castle-j10.png instead", body["body"])
        free = job_host.output_location(self.controller, "renders/new.png", "j10", "file")
        self.assertEqual((free["writes"], free["if_taken"]), ("renders/new.png", "renders/new-j10.png"))
        self.assertIn("nothing is overwritten", approval("angelo_job_start", dict(STILL, output="renders/new.png"))["body"])

    def test_already_exists_never_reaches_the_model_as_a_refusal(self):
        (self.root / "castle-final.png").write_bytes(b"theirs")
        self.assertEqual(job_outputs.admit_output(self.files, "castle-final.png"), self.root / "castle-final.png")
        runner = types.SimpleNamespace(output=self.root / "castle-final.png", output_name="castle-final.png",
                                       publishes="file", skip_requested=True, describe=dict)
        record = {"id": "j11", "status": "running", "receipt": {}, "detail": {}, "request": dict(STILL)}
        manager = types.SimpleNamespace(submit=lambda *args: record, queue=[], locks_interface=lambda: False)
        jobs = types.ModuleType("angelo_agent.job_ui")
        jobs.manager = lambda: manager
        with patch.object(job_host, "_admit", return_value=(dict(STILL), runner)), \
                patch.dict(sys.modules, {"angelo_agent.job_ui": jobs}), patch("angelo_agent.job_ui", jobs, create=True):
            result = job_host.execute(self.controller, object(), "angelo_job_start", STILL)
        self.assertNotIn("error", result)
        self.assertEqual((result["status"], result["output"], result["requested"]),
                         ("running", "castle-final-j11.png", "castle-final.png"))
        self.assertIn("castle-final.png already exists and is never overwritten, so this job writes "
                      "castle-final-j11.png", result["note"])
        self.assertNotIn("choose a new name", json.dumps(result))
        from angelo_agent.job_store import output_name
        self.assertEqual(output_name(record), "castle-final-j11.png")   # The jobs block and card while it runs.
        record.update(status="failed", reason="No camera", receipt={"note": "Nothing ran", "scene_changed": False})
        with patch.object(job_host, "_admit", return_value=(dict(STILL), runner)), \
                patch.dict(sys.modules, {"angelo_agent.job_ui": jobs}), patch("angelo_agent.job_ui", jobs, create=True):
            failed = job_host.execute(self.controller, object(), "angelo_job_start", STILL)
        self.assertNotIn("requested", failed)
        self.assertNotIn("never overwritten", failed.get("note", ""))

    def test_free_names_follows_publication_order(self):
        taken = {"a.png", "a-j1.png"}
        self.assertEqual(free_names("x/a.png", "j1", "file", lambda entry: entry in taken), ("x/a-j1-2.png", "x/a-j1-3.png"))
        self.assertEqual(free_names("x/a.png", "j1", "file", lambda entry: False), ("x/a.png", "x/a-j1.png"))
        self.assertEqual(free_names("a.usd", "j1", "file_set", lambda entry: entry == "a.usd"), ("a-j1/a.usd", "a-j1-2/a.usd"))

    # Regression cases ---------------------------------------------------------------------------------------------

    def precondition(self, output, *, beside=(), next_id="j20"):
        """job_host.precondition over a stand-in runner admitted as _admit admits it (its skip rule included)."""
        path = self.root / output
        runner = types.SimpleNamespace(output=path, output_name=output, publishes="file_set" if beside else "file",
                                       beside=beside, describe=dict)
        runner.skip_requested = any(os.path.lexists(item) for item in [path, *(path.parent / name for name in beside)])
        jobs = types.ModuleType("angelo_agent.job_ui")
        jobs.manager = lambda: types.SimpleNamespace(admit=lambda request: None)
        window = types.SimpleNamespace(scene=types.SimpleNamespace(session_uid=1))
        with patch.object(job_host, "_admit", return_value=(dict(STILL, output=output), runner)), \
                patch.object(job_host, "next_identity", return_value=next_id), \
                patch.dict(sys.modules, {"angelo_agent.job_ui": jobs}), patch("angelo_agent.job_ui", jobs, create=True), \
                patch("angelo_agent.render_extensions.facts", lambda runner: {}):
            return job_host.precondition(self.controller, window, "angelo_job_start", dict(STILL, output=output))

    def test_the_click_never_refuses_for_a_moved_id_or_a_name_taken_meanwhile(self):
        # Review finding 1: the card's next free name was compared at the click, so another job started first, or a
        # name taken or freed meanwhile, refused the approval (STALE_REVISION). It is shown, never compared.
        (self.root / "castle.png").write_bytes(b"theirs")
        card = self.precondition("castle.png", next_id="j20")
        later = self.precondition("castle.png", next_id="j21")
        self.assertEqual(card, later)
        self.assertEqual((card.shown["writes"], later.shown["writes"]), ("castle-j20.png", "castle-j21.png"))
        free = self.precondition("new.png")
        (self.root / "new.png").write_bytes(b"appeared")
        self.assertEqual(free, self.precondition("new.png"))
        from angelo_agent.approvals import _review_arguments
        call = types.SimpleNamespace(name="angelo_job_start", arguments=dict(STILL, output="castle.png"), precondition=card)
        facts = _review_arguments(call)["_facts"]
        self.assertEqual((facts["writes"], facts["taken"], facts["requested"]), ("castle-j20.png", True, "castle.png"))

    def test_a_set_whose_exported_folder_is_taken_says_and_goes_where_it_publishes(self):
        # Review finding 3: a textures/ folder beside the output moved a USD export into scene-<job>/, while the card
        # and the start result said scene.usd.
        (self.root / "textures").mkdir()
        card = self.precondition("scene.usd", beside=("textures",))
        self.assertEqual((card.shown["writes"], card.shown["taken"]), ("scene-j20/scene.usd", False))
        body = approval("angelo_job_start", dict(STILL, kind="EXPORT_USD", output="scene.usd",
                                                 _facts=_review_facts(card)))["body"]
        self.assertIn("Writes scene-j20/scene.usd in this chat's file workspace", body)
        output = job_outputs.admit_output(self.files, "scene.usd")
        staging = job_outputs.Staging(self.root, output, "j20", True, True).create()
        (staging.path / "main").write_bytes(b"usd")   # No textures written this time: still where the card said.
        staging.publish_file("main")
        self.assertEqual(staging.relative_output(), "scene-j20/scene.usd")
        staging.close()

    def test_the_card_names_the_admitted_output(self):
        # Review finding 9: "renders/shot" is admitted as renders/shot.png; the card said "renders/shot".
        (self.root / "renders").mkdir()
        card = self.precondition("renders/shot.png")
        self.assertEqual(card.shown["requested"], "renders/shot.png")

    def test_a_crash_during_a_publication_says_its_output_may_be_unfinished(self):
        # Review finding 4: the publishing mark went with the replacement; a crash mid-publication said "Nothing was
        # published" while half the frames stayed.
        marks = []
        output = job_outputs.admit_output(self.files, "anim")
        staging = job_outputs.Staging(self.root, output, "j21", announce=marks.append).create()
        (staging.path / "frame_0001.png").write_bytes(b"f")
        staging.publish_folder()
        self.assertEqual(marks, ["anim"])   # Saved before the first link.
        staging.unpublish()
        self.assertEqual(marks, ["anim", None])
        staging.discard()
        store = types.SimpleNamespace(records={}, save=lambda: None)
        record = {"id": "j21", "kind": "RENDER_ANIMATION", "status": "running", "detail": {"publishing": "anim"},
                  "receipt": {}}
        store.interrupted = lambda now: [record]
        # job_ui imports bpy: its _reconcile runs as written, over the store and staging stand-ins.
        source = HERE.parents[2] / "scripts/addons_core/angelo_agent/job_ui.py"
        tree = ast.parse(source.read_text())
        body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_reconcile"
                or isinstance(node, ast.Assign) and node.targets[0].id in ("APP_CLOSED", "APP_CLOSED_PUBLISHING")]
        namespace = {"n_": lambda text: text, "time": types.SimpleNamespace(time=lambda: 0.0), "Path": Path,
                     "PREVIEW": "PREVIEW"}
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(source), "exec"), namespace)
        outputs = types.ModuleType("angelo_agent.job_outputs")
        with patch.dict(sys.modules, {"angelo_agent.job_outputs": outputs}):
            namespace["_reconcile"].__globals__["__package__"] = "angelo_agent"
            namespace["_reconcile"](types.SimpleNamespace(store=store))
        self.assertEqual(record["receipt"], {"note": namespace["APP_CLOSED_PUBLISHING"], "output": "anim"})

    def test_a_link_at_the_name_is_a_taken_name(self):
        # Review finding 5: a link (live or dangling) at the output's name was refused as a link to follow.
        (self.root / "elsewhere.png").write_bytes(b"target")
        os.symlink(self.root / "elsewhere.png", self.root / "live.png")
        os.symlink(self.root / "missing.png", self.root / "dangling.png")
        for name, written in (("live.png", "live-j22.png"), ("dangling.png", "dangling-j22.png")):
            self.assertEqual(self.publish(name, "j22")[1], written)
            self.assertTrue((self.root / name).is_symlink())
        self.assertEqual((self.root / "elsewhere.png").read_bytes(), b"target")
        os.symlink(self.root, self.root / "loop")
        with self.assertRaises(job_outputs.JobRequestError):   # A link on the way is still never followed.
            job_outputs.admit_output(self.files, "loop/x.png")

    def test_names_are_never_longer_than_file_systems_allow(self):
        # Review finding 6: a taken name near NAME_MAX made a fallback too long, which failed the finished job.
        name = "a" * 251 + ".png"
        (self.root / name).write_bytes(b"theirs")
        self.assertTrue(all(len(entry.encode()) <= 255 for entry in candidates(name, "j23", False, False)[1:]))
        self.assertEqual(self.publish(name, "j23")[1], "a" * 247 + "-j23.png")

    def test_every_name_taken_is_said_at_admission_never_after_the_job_ran(self):
        # Review finding 7: with every name taken the card said "writes shot.png" and the job ran, then failed.
        import angelo_agent
        for name in candidates("shot.png", "j30", False, False):
            (self.root / name).write_bytes(b"taken")
        runner = types.SimpleNamespace(output=self.root / "shot.png", output_name="shot.png", publishes="file")
        runners = types.ModuleType("angelo_agent.job_runners")
        runners.build = lambda *args: runner
        programs = types.ModuleType("angelo_agent.native_programs")
        programs.has_active_programs = lambda: False
        queries = types.ModuleType("angelo_agent.scene_queries")
        queries.resolve_object, queries.SceneQueryError = (lambda *args: None), type("SceneQueryError", (Exception,), {})
        window = types.SimpleNamespace(scene=types.SimpleNamespace(session_uid=1))
        stand_ins = {"angelo_agent.job_runners": runners, "angelo_agent.native_programs": programs,
                     "angelo_agent.scene_queries": queries}
        with patch.dict(sys.modules, stand_ins), \
                patch.multiple(angelo_agent, job_runners=runners, native_programs=programs, scene_queries=queries,
                               create=True), \
                patch.object(job_host, "next_identity", return_value="j30"):
            with self.assertRaises(job_host.JobRequestError) as caught:
                job_host._admit(self.controller, window, dict(STILL, output="shot.png"))
            self.assertEqual(caught.exception.code, "VALIDATION")
            self.assertIn("every name tried after it are taken", str(caught.exception))
            (self.root / "shot-j30-5.png").unlink()   # One free name: admitted, writing there.
            _request, admitted = job_host._admit(self.controller, window, dict(STILL, output="shot.png"))
        self.assertTrue(admitted.skip_requested)

    def test_unpublish_removes_only_the_entries_it_made(self):
        # Review finding 10: an entry swapped in under a name this publication made was removed with it.
        output = job_outputs.admit_output(self.files, "anim")
        staging = job_outputs.Staging(self.root, output, "j24").create()
        (staging.path / "frame_0001.png").write_bytes(b"f")
        staging.publish_folder()
        os.rename(self.root / "anim", self.root / "anim-moved")
        (self.root / "anim").mkdir()
        (self.root / "anim" / "persons-work.blend").write_bytes(b"theirs")
        staging.unpublish()
        staging.discard()
        self.assertEqual((self.root / "anim" / "persons-work.blend").read_bytes(), b"theirs")

    # Its recheck ----------------------------------------------------------------------------------------------------

    def jobs(self):
        """A manager stand-in whose store allocates IDs, and whose submit takes the reserved one."""
        from angelo_agent.job_store import JobStore
        store = JobStore(self.root.parent / "state")
        records = []

        def submit(session, call, request, runner, identity=None):
            record = {"id": identity or store.allocate(), "status": "running", "receipt": {}, "detail": {},
                      "request": request}
            runner.identity = record["id"]
            records.append(record)
            return record
        return types.SimpleNamespace(store=store, submit=submit, queue=[], locks_interface=lambda: False,
                                     admit=lambda request: None), records

    def card(self, output, manager):
        """An approval card (job_host.precondition) over a stand-in runner admitted as _admit admits it."""
        path = self.root / output
        runner = types.SimpleNamespace(output=path, output_name=output, publishes="file", beside=(), describe=dict)

        def admit(*args):
            runner.skip_requested = os.path.lexists(path)
            return dict(STILL, output=output), runner
        jobs = types.ModuleType("angelo_agent.job_ui")
        jobs.manager = lambda: manager
        window = types.SimpleNamespace(scene=types.SimpleNamespace(session_uid=1))
        context = [patch.object(job_host, "_admit", admit), patch.object(job_host, "_manager", lambda: manager),
                   patch.dict(sys.modules, {"angelo_agent.job_ui": jobs}), patch("angelo_agent.job_ui", jobs, create=True),
                   patch("angelo_agent.render_extensions.facts", lambda runner: {})]
        for item in context:
            item.start()
        try:
            return job_host.precondition(self.controller, window, "angelo_job_start", dict(STILL, output=output))
        finally:
            for item in reversed(context):
                item.stop()

    def test_two_pending_cards_and_a_freed_name_write_where_each_card_said(self):
        # Recheck finding 1: (b) two pending taken-name cards both said -j1; (d) a name the card called taken was
        # written once freed. Each card reserves its job's ID, and its choice is carried into the dispatch.
        manager, records = self.jobs()
        for name in ("tree.png", "gone.png"):
            (self.root / name).write_bytes(b"theirs")
        first = self.card("tree.png", manager)
        second = self.card("tree.png", manager)   # The same request again (the click's recheck): the same reservation.
        other = self.card("gone.png", manager)
        self.assertEqual((first.shown["writes"], second.shown["writes"], other.shown["writes"]),
                         ("tree-j1.png", "tree-j1.png", "gone-j2.png"))
        (self.root / "gone.png").unlink()   # Freed before the click.
        jobs = types.ModuleType("angelo_agent.job_ui")
        jobs.manager = lambda: manager
        for card, output in ((other, "gone.png"), (first, "tree.png")):
            runner = types.SimpleNamespace(output=self.root / output, output_name=output, publishes="file", beside=(),
                                           skip_requested=os.path.lexists(self.root / output))
            self.controller.executing_call = types.SimpleNamespace(id="c", precondition=card)
            with patch.object(job_host, "_admit", return_value=(dict(STILL, output=output), runner)), \
                    patch.object(job_host, "_manager", lambda: manager), \
                    patch.dict(sys.modules, {"angelo_agent.job_ui": jobs}), patch("angelo_agent.job_ui", jobs, create=True):
                result = job_host.execute(self.controller, object(), "angelo_job_start", dict(STILL, output=output))
            self.assertEqual(result["output"], card.shown["writes"])
            self.assertTrue(runner.skip_requested)
        self.controller.executing_call = None
        self.assertEqual([record["id"] for record in records], ["j2", "j1"])

    def test_a_usd_exports_volumes_folder_counts_and_a_set_always_falls_back_to_a_folder(self):
        # Recheck findings 2 and 3: a volumes/ folder beside the file moved the set unannounced; a set without
        # dependencies whose name was taken while it ran went to scene-<job>.usd while if_taken said scene-<job>/.
        (self.root / "volumes").mkdir()
        card = self.precondition("scene.usd", beside=("textures", "volumes"))
        self.assertEqual(card.shown["writes"], "scene-j20/scene.usd")
        output = job_outputs.admit_output(self.files, "plain.usd")
        staging = job_outputs.Staging(self.root, output, "j21", False, True).create()
        (staging.path / "main").write_bytes(b"usd")
        (self.root / "plain.usd").write_bytes(b"appeared")
        staging.publish_file("main")
        self.assertEqual(staging.relative_output(), "plain-j21/plain.usd")
        staging.close()

    def test_a_link_at_a_hidden_or_private_name_is_never_admitted(self):
        # Recheck finding 4: a link at .hidden.png was admitted as a taken name.
        os.symlink(self.root / "missing", self.root / ".hidden.png")
        with self.assertRaises(job_outputs.JobRequestError):
            job_outputs.admit_output(self.files, ".hidden.png")

    def test_a_lone_file_swapped_after_publication_is_left_as_it_is(self):
        # Recheck finding 6: a link's identity is its staging file's, known before the link exists.
        output = job_outputs.admit_output(self.files, "still.png")
        staging = job_outputs.Staging(self.root, output, "j25").create()
        (staging.path / "main").write_bytes(b"new")
        staging.publish_file("main")
        (self.root / "still.png").unlink()
        (self.root / "still.png").write_bytes(b"theirs")
        staging.unpublish()
        staging.discard()
        self.assertEqual((self.root / "still.png").read_bytes(), b"theirs")

    def test_an_output_name_too_long_for_the_file_system_is_refused_without_a_local_path(self):
        # Recheck finding 7: an uncaught OSError reached the model as UNKNOWN with the absolute path.
        (self.root / "renders").mkdir()
        with self.assertRaises(job_outputs.JobRequestError) as caught:
            job_outputs.admit_output(self.files, "renders/" + "b" * 300 + ".png")
        self.assertEqual(caught.exception.code, "VALIDATION")
        self.assertNotIn(str(self.root), str(caught.exception))


def _review_facts(precondition):
    return dict(json.loads(precondition[2]), **(precondition.shown or {}))


@unittest.skipIf(os.name == "nt", environment_skip("The POSIX stand-in opens links with O_PATH or O_SYMLINK; on Windows "
                                   "windows_ntfs_checks.py runs the real backend"))
class WindowsFreeNames(PosixFreeNames):
    """The same contract through ``anchored_windows`` (by-handle creation that never replaces a name)."""
    windows = True


if __name__ == "__main__":
    unittest.main()
