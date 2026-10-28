# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's scripts without Blender: the route, the approved identity and the part guard (``worker_script``).

Pinned here: a worker chat's ``angelo_execute_python`` goes to its worker Blender (never headless), and its
``angelo_script`` reads this chat's own transcript; a script that would replace the open file is refused before its
card and again in the worker, with nothing run, while appending, linking and saving a copy are not; the approved
call's invocation digest goes with it, and the worker runs only that exact script, as its shadow's exact-code review,
when its binding still holds; after a script the worker saves its part again when the script saved the open file as
another file, and loads its part again as last saved, saving nothing, when the script loaded another file (ending
when it cannot). Evidence: the parallel-agents Python spike (2026-10-25, r2 r4 f8: a script that loaded a file had
another file's data saved as the part). The in-Blender proof is lane P3's ``test_blender_worker_python``.
"""

import contextlib
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import parallel_access, remote_executor, worker_host  # noqa: E402
from angelo_agent import worker_values as values  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from test_native_parallel_workers_routing import FakeCall, FakeShadow, FakeWorker, chat  # noqa: E402

SCRIPT = "angelo_execute_python"
LOADING = {   # Each way a script's code names a file-replacing operator that the check reads.
    "read_homefile": "import bpy\nbpy.ops.wm.read_homefile(use_empty=True)",
    "revert_mainfile": "import bpy\ngetattr(bpy.ops.wm, 'revert_mainfile')()",
    "recover_auto_save": "exec('bpy.ops.wm.recover_auto_save()')",
    "open_mainfile": "from bpy.ops import wm\nload = wm.open_mainfile\nload(filepath='/elsewhere/other.blend')",
    "read_factory_settings": "import bpy\nbpy.ops.wm.read_factory_settings()",
    "recover_last_session": "import bpy\nbpy.ops.wm.recover_last_session()",
}
NOT_LOADING = (   # Nothing here replaces the open file.
    "import bpy\nwith bpy.data.libraries.load('/kit.blend') as (source, target):\n    target.objects = source.objects",
    "import bpy\nbpy.ops.wm.append(filepath='/kit.blend/Object/Chair', directory='/kit.blend/Object/', filename='Chair')",
    "import bpy\nbpy.ops.wm.link(directory='/kit.blend/Material/', filename='Oak')",
    "import bpy\nbpy.ops.wm.save_as_mainfile(filepath='/out/copy.blend', copy=True)",
    "# bpy.ops.wm.read_homefile() would reset the scene\nopen_mainfile_count = 1",
    "def broken(:\n    pass",   # validate_call refuses it.
)


def digest(name, arguments):
    """The invocation digest a ``Controller`` gives an admitted call (``controller.request_tool``)."""
    return hashlib.sha256(json.dumps({"tool": name, "arguments": arguments}, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class LeadSideTests(unittest.TestCase):
    def executor(self, answers=None, controller=None):
        executor = remote_executor.RemoteExecutor(FakeWorker(answers), "w1")
        executor.studio_context = controller or chat()
        return executor

    def test_a_worker_chat_offers_and_routes_python_and_headless_never_gets_it(self):
        from angelo_agent.external_clients import HEADLESS_TOOLS
        reads, edits = remote_executor.worker_tools()
        self.assertIn(SCRIPT, edits)
        self.assertNotIn(SCRIPT, HEADLESS_TOOLS)   # A person approves a worker's script in Angelo; an app never.
        self.assertIn("angelo_script", remote_executor.local_tools())
        with patch.object(parallel_access, "enabled", return_value=True):
            offered = parallel_access.restrict(None, {SCRIPT, "angelo_script", "angelo_sculpt_batch"}, worker=True)
        self.assertEqual(offered, {SCRIPT, "angelo_script"})
        executor = self.executor({"precondition": {"value": values.encode((7, "bound"))}})
        self.assertEqual(executor.precondition(SCRIPT, {"code": "import bpy"}), (7, "bound"))   # Asked of the worker.
        self.assertEqual([request["op"] for request in executor.worker.requests], ["precondition"])

    def test_a_scripts_code_is_read_here_as_this_chats_own_transcript(self):
        executor = self.executor()
        local = SimpleNamespace(precondition=lambda *a: None, execute=lambda *a: {"code": "x"})
        with patch.object(executor, "_local_executor", return_value=local):
            self.assertIsNone(executor.precondition("angelo_script", {"script": "s0123abcd"}))
        self.assertEqual(executor.worker.requests, [])

    def test_a_script_that_would_replace_the_open_file_is_refused_before_its_card(self):
        executor = self.executor()
        for name, code in LOADING.items():
            for method in (executor.precondition, executor.execute):
                with self.subTest(name=name, method=method.__name__), self.assertRaises(RefusalError) as caught:
                    method(SCRIPT, {"description": "x", "code": code})
                self.assertEqual(caught.exception.code, "UNSUPPORTED")
                self.assertIs(caught.exception.partial_changes_possible, False)
                self.assertIn("(%s)" % name, str(caught.exception))
        self.assertEqual(executor.worker.requests, [])   # Nothing left Angelo.

    def test_appending_linking_and_saving_a_copy_are_not_file_loads(self):
        from angelo_agent import worker_script
        for code in NOT_LOADING:
            with self.subTest(code=code):
                self.assertIsNone(worker_script.file_load({"code": code}))
        # A computed name is not read: the worker's guard covers it (WorkerHostScriptTests).
        self.assertIsNone(worker_script.file_load({"code": "getattr(bpy.ops.wm, 'read_' + 'homefile')()"}))

    def test_the_approved_scripts_digest_goes_with_it_and_a_typed_edits_has_none(self):
        call = SimpleNamespace(precondition=(1, "bound"), invocation_digest="d" * 64, program_arguments=None,
                               arguments={"code": "import bpy"})
        executor = self.executor({"execute": {"result": {"success": True}}}, chat(None, call))
        self.assertEqual(executor.execute(SCRIPT, {"code": "import bpy"}), {"success": True})
        sent = executor.worker.requests[-1]
        self.assertEqual((sent["digest"], values.decode(sent["approved"])), ("d" * 64, (1, "bound")))
        self.assertTrue(executor.worker.ran_python)   # Its Blender is never offered another part (worker_pool).
        executor.execute("angelo_set_transform", {"object": "Cube", "location": [0, 0, 0]})
        self.assertNotIn("digest", executor.worker.requests[-1])


class ScriptShadow(FakeShadow):
    """The worker's shadow controller for a script: its admitted call has the controller's invocation digest."""

    def __init__(self, precondition):
        super().__init__(precondition)
        self.reviewed, self.documents = [], []

    def request_tool(self, call_id, name, arguments, callback):
        super().request_tool(call_id, name, arguments, callback)
        self.pending["chat-1/" + call_id].invocation_digest = digest(name, arguments)

    def review_script(self, call):
        self.reviewed.append(call.id)

    def set_document(self, document):
        self.documents.append(document)


class FakeBlender:
    """The ``bpy`` a worker's edit touches: the approve operator runs ``script``, saves and loads are recorded."""

    def __init__(self, part, shadow, script=None):
        self.part, self.shadow, self.script = part, shadow, script
        self.data = SimpleNamespace(filepath=part, is_dirty=False)
        self.context = SimpleNamespace(preferences=SimpleNamespace(filepaths=SimpleNamespace(save_version=1)),
                                       temp_override=lambda **_: contextlib.nullcontext())
        self.ran, self.saved, self.opened, self.can_open = [], [], [], True
        self.ops = SimpleNamespace(angelo=SimpleNamespace(approve=self.approve),
                                   wm=SimpleNamespace(save_as_mainfile=self.save_as, open_mainfile=self.open))

    def approve(self, *_args, call_id, session_id):
        call = self.shadow.pending[call_id]
        call.executing = True
        self.ran.append(call_id)
        result = (self.script or (lambda blender: {"success": True}))(self)
        self.shadow.finish_tool(call, result)
        return {"FINISHED"}

    def save_as(self, filepath, **_options):
        self.saved.append(filepath)
        self.data.filepath, self.data.is_dirty = filepath, False
        return {"FINISHED"}

    def open(self, filepath, **_options):
        self.opened.append(filepath)
        if not self.can_open:
            raise RuntimeError("Error: Cannot read file")
        self.data.filepath, self.data.is_dirty = filepath, False
        ui.load()
        return {"FINISHED"}


class FakeUi(SimpleNamespace):
    def load(self):   # ``ui.document_load_pre``: a load renews the document's instance.
        self._document_serial += "+"


ui = FakeUi(_document_serial="serial", _controllers={}, Controller=object, state_directory=lambda: "/state",
            main_window=lambda: "window")
ui.document_key = lambda: (ui._document_serial, 34)


class WorkerHostScriptTests(unittest.TestCase):
    PART = os.path.join(os.sep, "w", "part.blend")
    CODE = {"description": "a chair", "code": "import bpy\nbpy.ops.mesh.primitive_cube_add()"}

    def edit(self, script=None, digest_sent="exact", precondition=(1, "bound"), approved=(1, "bound"), code=None):
        ui._document_serial = "serial"
        shadow = ScriptShadow(precondition)
        arguments = dict(self.CODE, code=code) if code else self.CODE
        host = worker_host.WorkerHost(ui, None, endpoint=None, part=self.PART)
        host.bpy = blender = FakeBlender(self.PART, shadow, script)
        host.shadows["chat-1"] = shadow
        host.layout = lambda: {"revision": host.revision}
        request = {"name": SCRIPT, "arguments": arguments, "approved": values.encode(approved)}
        if digest_sent is not None:
            request["digest"] = digest(SCRIPT, arguments) if digest_sent == "exact" else digest_sent
        with patch("angelo_agent.controller.pump_controller_tools"):
            answer = host.edit(shadow, None, request)
        return answer, host, shadow, blender

    def test_the_approved_script_runs_once_as_the_shadows_exact_code_review(self):
        def made_a_chair(blender):
            blender.data.is_dirty = True
            return {"success": True}
        answer, host, shadow, blender = self.edit(made_a_chair)
        self.assertEqual(answer["result"], {"success": True})
        self.assertEqual(len(blender.ran), 1)
        self.assertEqual(shadow.reviewed, blender.ran)   # Reviewed exactly the call that ran, before it ran.
        self.assertEqual((answer["saved"], blender.saved, answer["revision"]), (self.PART, [self.PART], 1))
        self.assertEqual(blender.opened, [])

    def test_a_script_other_than_the_approved_one_runs_nothing(self):
        for sent in ("0" * 64, "", None, 7):   # Forged on the connection, empty, missing, not a digest.
            with self.subTest(digest=sent):
                answer, host, shadow, blender = self.edit(digest_sent=sent)
                self.assertEqual(answer["result"]["error_code"], "OWNERSHIP")
                self.assertIs(answer["result"]["scene_changed"], False)
                self.assertEqual((blender.ran, shadow.reviewed, answer["saved"]), ([], [], None))

    def test_a_script_whose_binding_changed_since_approval_runs_nothing(self):
        answer, host, shadow, blender = self.edit(approved=(1, "moved since"))
        self.assertEqual(answer["result"]["error_code"], "STALE_REVISION")
        self.assertEqual((blender.ran, shadow.reviewed), ([], []))

    def test_a_script_that_would_replace_the_open_file_is_refused_in_the_worker_too(self):
        for name, code in LOADING.items():
            with self.subTest(name=name), self.assertRaises(RefusalError) as caught:
                self.edit(code=code)
            self.assertIs(caught.exception.partial_changes_possible, False)

    def test_a_script_that_saved_as_another_file_leaves_the_part_the_open_file(self):
        def saved_elsewhere(blender):   # It made something, then saved the open file as another file.
            blender.save_as("/elsewhere/copy.blend")
            return {"success": True}
        answer, host, shadow, blender = self.edit(saved_elsewhere)
        self.assertEqual(blender.saved, ["/elsewhere/copy.blend", self.PART])   # Saved as the part again.
        self.assertEqual((blender.data.filepath, answer["saved"], blender.opened), (self.PART, self.PART, []))
        self.assertIn("copy.blend", answer["result"]["part_saved_again"])

    def test_a_script_that_loaded_another_file_never_has_it_saved_as_the_part(self):
        from angelo_agent.script_document import DOCUMENT_REPLACED

        def loaded(blender):   # A computed load: the executor's guard ends it in DOCUMENT_REPLACED.
            blender.open("/elsewhere/other.blend")
            blender.data.is_dirty = True
            return dict(DOCUMENT_REPLACED)
        answer, host, shadow, blender = self.edit(loaded)
        self.assertEqual(blender.opened, ["/elsewhere/other.blend", self.PART])   # The part, as last saved.
        self.assertEqual((blender.saved, answer["saved"], answer["revision"]), ([], None, None))
        self.assertEqual(answer["result"]["error_code"], "DOCUMENT_REPLACED")
        self.assertIn("other.blend", answer["result"]["part_reloaded"])
        self.assertEqual(shadow.documents, [ui.document_key()])   # Its chats are told, as a window's are of a load.
        self.assertFalse(host.ended)

    def test_any_new_document_loads_the_part_again_and_one_that_cannot_ends_the_worker(self):
        def read_startup(blender):   # The document changed although the result did not say so.
            ui.load()
            blender.data.filepath = ""
            return {"success": True}
        answer, host, shadow, blender = self.edit(read_startup)
        self.assertEqual((blender.opened, blender.saved), ([self.PART], []))
        self.assertIn("an unsaved file", answer["result"]["part_reloaded"])

        def deleted_and_loaded(blender):   # The part cannot be loaded again: nothing is ever saved over it.
            blender.can_open = False
            ui.load()
            blender.data.filepath, blender.data.is_dirty = "/elsewhere/other.blend", True
            return {"success": True}
        answer, host, shadow, blender = self.edit(deleted_and_loaded)
        self.assertEqual((blender.saved, answer["saved"]), ([], None))
        self.assertTrue(host.ended)   # The run loop ends after this answer.
        self.assertIn("ends without saving", answer["result"]["part_reloaded"])

    def test_a_script_that_changed_nothing_saves_nothing(self):
        answer, host, shadow, blender = self.edit()
        self.assertEqual((answer["saved"], blender.saved, blender.opened), (None, [], []))


if __name__ == "__main__":
    unittest.main()
