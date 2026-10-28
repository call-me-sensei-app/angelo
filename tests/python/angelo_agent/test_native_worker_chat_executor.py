# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted worker chat's executor keeps what runs to the worker's part, as the lane path does, without Blender.

Pinned here (``worker_chat_executor``, ``worker_chat_part``): after a hosted chat's script, a load the name check
could not see loads the part again as last saved, with nothing saved over it, and a part that cannot be loaded again
ends the worker without saving (FE native A′ defect D6, worker_python @hosted ``a_hidden_load_is_contained``: the
worker was left on the startup file); a script that saved the open file as another file has the part saved again; a
view runs the worker's GPU check and initializes Blender's GPU module first, the lane view route's own, and draws
nothing on a machine that cannot (D7: "gpu module not initialized"); and a hosted chat's recovery words never send its
model to a request snapshot, which Restore refuses for a worker chat (D10).
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import script_errors, worker_host, worker_routing  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.script_document import DOCUMENT_REPLACED  # noqa: E402
from angelo_agent.trusted_scripts import TrustedScripts  # noqa: E402
from angelo_agent.worker_chat_executor import confined  # noqa: E402
from test_native_worker_chat_host import Fixture  # noqa: E402
from test_native_worker_python_route import FakeBlender, ScriptShadow, ui  # noqa: E402

SCRIPT = "angelo_execute_python"
CODE = {"description": "x", "code": "import bpy\ngetattr(bpy.ops.wm, 'read_' + 'homefile')(use_empty=True)"}
PART = "/w/part.blend"


class Running:
    """The window's executor: ``run`` stands for the script's effect on the fake Blender."""

    def __init__(self, blender, run):
        self.blender, self.run, self.calls = blender, run, []

    def execute(self, name, arguments):
        self.calls.append(name)
        return self.run(self.blender)


def hosted(run):
    ui._document_serial = "serial"
    host = worker_host.WorkerHost(ui, None, endpoint=None, part=PART)
    host.bpy = blender = FakeBlender(PART, ScriptShadow(None))
    chats = SimpleNamespace(host=host, save_due=False)
    executor = confined(Running(blender, run), SimpleNamespace(chat_host=chats, state={"id": "chat-1"}))
    return executor, host, blender, chats


class ScriptContainmentTests(unittest.TestCase):
    def test_a_hidden_load_loads_the_part_again_and_saves_nothing(self):
        def loaded(blender):   # A computed load: the executor's own guard ends it in DOCUMENT_REPLACED.
            blender.open("/elsewhere/other.blend")
            blender.data.is_dirty = True
            return dict(DOCUMENT_REPLACED)
        executor, host, blender, chats = hosted(loaded)
        result = executor.execute(SCRIPT, CODE)
        self.assertEqual(blender.opened, ["/elsewhere/other.blend", PART])   # The part again, as last saved.
        self.assertEqual(result["error_code"], "DOCUMENT_REPLACED")
        self.assertIn("other.blend", result["part_reloaded"])
        self.assertEqual((blender.saved, chats.save_due, host.ended), ([], False, False))

    def test_a_part_that_cannot_be_loaded_again_ends_the_worker(self):
        def lost(blender):
            blender.can_open = False
            ui.load()
            blender.data.filepath = ""
            return {"success": True}
        executor, host, blender, chats = hosted(lost)
        result = executor.execute(SCRIPT, CODE)
        self.assertTrue(host.ended)
        self.assertIn("ends without saving", result["part_reloaded"])
        self.assertEqual(blender.saved, [])

    def test_a_script_that_saved_as_another_file_has_the_part_saved_again(self):
        def saved_elsewhere(blender):
            blender.save_as("/elsewhere/copy.blend")
            return {"success": True}
        executor, host, blender, chats = hosted(saved_elsewhere)
        result = executor.execute(SCRIPT, CODE)
        self.assertIn("copy.blend", result["part_saved_again"])
        self.assertIs(chats.save_due, True)
        self.assertEqual(blender.opened, [])

    def test_a_script_that_stayed_on_its_part_changes_nothing_here(self):
        executor, host, blender, chats = hosted(lambda blender: {"success": True})
        self.assertEqual(executor.execute(SCRIPT, CODE), {"success": True})
        self.assertEqual((blender.opened, blender.saved, chats.save_due), ([], [], False))


class ViewTests(unittest.TestCase):
    def test_a_view_initializes_the_gpu_module_first_as_the_lane_route_does(self):
        order = []
        executor, host, blender, chats = hosted(lambda blender: order.append("drawn") or {"pictures": []})
        host.gpu_verdict = (True, "")   # This machine draws offscreen (the probe's answer).
        with patch.dict(sys.modules, {"gpu": SimpleNamespace(init=lambda: order.append("gpu.init"))}):
            executor.execute("angelo_view", {"views": ["ISO"]})
            executor.execute("angelo_view", {"views": ["TOP"]})
        self.assertEqual(order, ["gpu.init", "drawn", "drawn"])   # Once per worker, before its first picture.

    def test_a_machine_that_cannot_draw_offscreen_draws_nothing(self):
        executor, host, blender, chats = hosted(lambda blender: self.fail("drawn"))
        host.gpu_verdict = (False, "no display adapter")
        with self.assertRaises(RefusalError) as caught:
            executor.execute("angelo_view", {"views": ["ISO"]})
        self.assertEqual(caught.exception.code, "UNSUPPORTED")
        self.assertIn("no display adapter", str(caught.exception))
        self.assertEqual(executor.inner.calls, [])


class PartSaveTests(Fixture):
    def test_a_lost_part_ends_the_loop_and_is_never_saved(self):
        self.open()
        saves = []
        self.host.save = lambda: saves.append(1) or self.host.part
        self.bpy.data.is_dirty, self.host.ended = True, True
        self.assertIsNotNone(self.chats.save_part_now())
        self.assertIsNone(self.chats.next_request())   # The run loop ends, every hosted chat closed.
        self.assertEqual((saves, self.chats.chats), ([], {}))

    def test_a_part_saved_as_another_file_is_saved_again_once_idle(self):
        self.open()
        saves = []
        self.host.save = lambda: saves.append(1) or self.host.part
        self.chats.save_due = True   # Set by the executor after such a script; the file itself is not dirty.
        self.chats.pump()
        self.assertEqual((saves, self.chats.save_due), ([1], False))
        self.chats.pump()
        self.assertEqual(saves, [1])


class RecoveryWordsTests(unittest.TestCase):
    def test_a_hosted_chat_is_never_sent_to_a_request_snapshot(self):
        chat = SimpleNamespace(chat_host=object(), state={"id": "chat-1"},
                               checkpoints=SimpleNamespace(session_id=worker_routing.worker_snapshot_session("chat-1")))
        call = SimpleNamespace(name=SCRIPT, executing=True, automatic=False, consent_kind=None)
        result = TrustedScripts.script_result(chat, call, {"error": "boom", "recovery": script_errors.RECOVERY})
        self.assertTrue(worker_routing.worker_snapshots(chat))   # Restore refuses it, so its words name none.
        self.assertEqual(result["recovery"], script_errors.RECOVERY_IN_WORKER)
        self.assertNotIn("pre-request snapshot", result["recovery"])

    def test_a_window_chat_keeps_the_snapshot_advice(self):
        chat = SimpleNamespace(state={"id": "chat-1"}, store=None,
                               checkpoints=SimpleNamespace(session_id="chat-1"))
        call = SimpleNamespace(name=SCRIPT, executing=True, automatic=False, consent_kind=None)
        result = TrustedScripts.script_result(chat, call, {"error": "boom", "recovery": script_errors.RECOVERY})
        self.assertEqual(result["recovery"], script_errors.RECOVERY)


if __name__ == "__main__":
    unittest.main()
