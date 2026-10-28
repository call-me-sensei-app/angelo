# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker Blenders without Blender: ``RemoteExecutor``, the window's routing and the worker's request handling.

A fake worker answers ``RemoteExecutor`` as ``worker_host`` does; a fake shadow controller stands in for the
worker's own one. Pinned here: nothing leaves Angelo for a tool a worker does not run; reads bring their recovery
evidence home; an approved edit carries its exact approved binding; an edit whose outcome is unknown says so; the
window's operators, pump, Undo and file loads route by chat. The in-Blender proof is
``test_blender_parallel_workers.py``.
"""

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import recovery_evidence, remote_executor, worker_host, worker_routing  # noqa: E402
from angelo_agent import worker_values as values  # noqa: E402
from angelo_agent.identifiers import IdentifierTable  # noqa: E402
from angelo_agent.program_checks import resolve_obligations  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.worker_process import WorkerUnavailable  # noqa: E402
from test_native_parallel_workers_transport import a_binding, a_program  # noqa: E402


class FakeWorker:
    def __init__(self, answers=None):
        self.requests, self.answers = [], answers or {}

    def request(self, op, timeout, **fields):
        self.requests.append(dict(fields, op=op))
        answer = self.answers.get(op, {})
        answer = answer(fields) if callable(answer) else answer
        if isinstance(answer, Exception):
            raise answer
        return dict({"document": ["serial", 34]}, **answer)


def chat(identifiers=None, call=None):
    return SimpleNamespace(state={"id": "chat-1", "options": {}}, _identifiers=identifiers, executing_call=call,
                           remote_captures={})


class RemoteExecutorTests(unittest.TestCase):
    def executor(self, answers=None, controller=None):
        executor = remote_executor.RemoteExecutor(FakeWorker(answers), "w1")
        executor.studio_context = controller or chat()
        return executor

    def test_tools_a_worker_does_not_run_never_leave_angelo(self):
        executor = self.executor()
        # angelo_view runs in a worker (the worker capability contract, 2026-10-25: test_native_worker_capability), and
        # so does a script, except one that would replace the open file (test_native_worker_python_route).
        for name, arguments in (("angelo_execute_python", {"code": "import bpy\nbpy.ops.wm.read_homefile()"}),
                                ("angelo_sculpt_batch", {}), ("angelo_job_start", {}), ("angelo_studio_import_3d", {}),
                                ("angelo_accept_preview", {}), ("angelo_blend_file", {}),
                                ("angelo_scene_program", {"mode": "PREVIEW"}),
                                ("angelo_apply_recipe", {"mode": "PREVIEW"})):
            for method in (executor.precondition, executor.execute):
                with self.subTest(name=name, method=method.__name__), self.assertRaises(RefusalError) as caught:
                    method(name, arguments)
                self.assertEqual(caught.exception.code, "UNSUPPORTED")
        self.assertEqual(executor.worker.requests, [])
        self.assertFalse(executor.supports_native_job("angelo_scene_program"))
        with self.assertRaises(RefusalError):
            executor.start_native_job("angelo_scene_program", {})

    def test_task_memory_and_metrics_run_in_this_chat(self):
        executor = self.executor()
        local = SimpleNamespace(precondition=lambda *a: ("memory",), execute=lambda *a: {"memory": True})
        with patch.object(executor, "_local_executor", return_value=local):
            self.assertEqual(executor.precondition("angelo_memory_update", {}), ("memory",))
            self.assertEqual(executor.execute("angelo_agent_metrics", {}), {"memory": True})
        self.assertEqual(executor.worker.requests, [])

    def test_a_read_brings_its_recovery_evidence_home(self):
        row = {"handle": "scn:" + "a" * 32 + ":4", "revision": "r", "fields": {"transform": "d"}, "values": {}}
        executor = self.executor({"execute": {"result": {"objects": []}, "evidence": {row["handle"]: row}}})
        with recovery_evidence.collect() as records:
            self.assertEqual(executor.execute("angelo_inspect_scene", {}), {"objects": []})
        self.assertEqual(records[row["handle"]]["revision"], "r")
        self.assertEqual(executor.document_key, ("angelo-worker", "w1", ("serial", 34)))

    def test_preconditions_come_back_exact_and_errors_come_back_typed(self):
        binding = a_binding()
        executor = self.executor({"precondition": {"value": values.encode(binding)}})
        self.assertEqual(executor.precondition("angelo_scene_program", a_program()), binding)
        executor = self.executor({"precondition": {"error": values.encode_error(
            RefusalError("moved", code="STALE_REVISION"))}})
        with self.assertRaises(RefusalError) as caught:
            executor.precondition("angelo_set_transform", {"object": "A", "location": [0, 0, 0]})
        self.assertEqual(caught.exception.code, "STALE_REVISION")

    def test_an_edit_carries_its_approved_binding_and_mints_only_matching_evidence(self):
        from angelo_agent.program_completion import _CapturedProgram
        program = a_program()
        captured = _CapturedProgram(resolve_obligations(program), "d" * 64, "committed")
        call = SimpleNamespace(id="chat-1/c1", precondition=a_binding(program), program_arguments=None,
                               arguments=program)
        controller = chat(IdentifierTable("worker"), call)
        executor = self.executor({"execute": {"result": {"status": "committed"},
                                              "captured": values.encode_capture(captured)}}, controller)
        self.assertEqual(executor.execute("angelo_scene_program", program), {"status": "committed"})
        sent = executor.worker.requests[-1]
        self.assertEqual(values.decode(sent["approved"]), call.precondition)
        self.assertIn("identifiers", sent)
        self.assertEqual(controller.remote_captures["chat-1/c1"].obligations, captured.obligations)
        executor.execute("angelo_scene_program", program)   # Unchanged handles: only their digest goes again.
        self.assertNotIn("identifiers", executor.worker.requests[-1])
        call.arguments = a_program("Other")   # Evidence for a program other than the approved one is dropped.
        controller.remote_captures.clear()
        executor.execute("angelo_scene_program", a_program("Other"))
        self.assertEqual(controller.remote_captures, {})

    def test_a_worker_that_lacks_the_handles_gets_them_once_before_anything_runs(self):
        answers = iter(({"resend_identifiers": True}, {"value": values.encode(None)}))
        executor = self.executor({"precondition": lambda fields: next(answers)}, chat(IdentifierTable("worker")))
        executor._sent_identifiers = "stale-digest-the-worker-never-saw"
        executor._identifiers()   # Pretend it was sent before.
        self.assertIsNone(executor.precondition("angelo_create_cube", {"name": "A", "size": 1, "location": [0, 0, 0]}))
        self.assertIn("identifiers", executor.worker.requests[-1])
        self.assertEqual(len(executor.worker.requests), 2)

    def test_an_edit_whose_worker_ended_has_an_unknown_outcome(self):
        call = SimpleNamespace(id="c", precondition=None, program_arguments=None, arguments={})
        executor = self.executor({"execute": WorkerUnavailable("gone")}, chat(None, call))
        with self.assertRaises(RuntimeError) as caught:
            executor.execute("angelo_create_cube", {"name": "A", "size": 1, "location": [0, 0, 0]})
        self.assertIs(caught.exception.partial_changes_possible, True)
        self.assertIn("unknown", str(caught.exception))
        self.assertEqual(executor.document_key, ("angelo-worker", "w1", "ended"))
        read = self.executor({"execute": WorkerUnavailable("gone")})
        with self.assertRaises(RefusalError) as caught:   # A read changes nothing: a classified refusal.
            read.execute("angelo_inspect_scene", {})
        self.assertEqual(caught.exception.code, "CANCELLED")
        self.assertIs(caught.exception.partial_changes_possible, False)

    def test_a_slow_worker_keeps_its_chat_and_is_checked_before_the_next_call(self):
        calls = []

        def answer(op):
            def respond(fields):
                calls.append(op)
                return answers.pop(0) if answers else {"value": True, "result": None}
            return respond
        answers = [WorkerUnavailable("slow", timed_out=True)]
        executor = self.executor({"execute": answer("execute"), "hello": answer("hello")})
        executor.document_key = ("angelo-worker", "w1", ("serial", 34))
        with self.assertRaises(RefusalError) as caught:
            executor.execute("angelo_inspect_scene", {})
        self.assertEqual(caught.exception.code, "WRITER_BUSY")
        self.assertEqual(executor.document_key, ("angelo-worker", "w1", ("serial", 34)))   # Never "ended".
        self.assertTrue(executor.unresponsive)
        answers[:] = [WorkerUnavailable("still slow", timed_out=True)]
        call = SimpleNamespace(id="c", precondition=None, program_arguments=None, arguments={})
        executor.studio_context = chat(None, call)
        for _ in range(2):   # Only the check went out, then nothing (checks are throttled): the edit never did.
            with self.assertRaises(RefusalError) as caught:
                executor.execute("angelo_create_cube", {"name": "A", "size": 1, "location": [0, 0, 0]})
            self.assertIs(caught.exception.partial_changes_possible, False)
            self.assertEqual(caught.exception.code, "WRITER_BUSY")
        self.assertEqual(calls, ["execute", "hello"])
        executor._next_probe = 0.0   # The next check is due.
        self.assertEqual(executor.execute("angelo_inspect_scene", {}), None)   # It answers again: back in use.
        self.assertFalse(executor.unresponsive)

    def test_refusals_before_anything_ran_say_so(self):
        executor = self.executor()
        for name, arguments in (("angelo_execute_python", {"code": "bpy.ops.wm.open_mainfile(filepath='x.blend')"}),
                                ("angelo_scene_program", {"mode": "PREVIEW"})):
            with self.subTest(name=name), self.assertRaises(RefusalError) as caught:
                executor.execute(name, arguments)
            self.assertIs(caught.exception.partial_changes_possible, False)
        from angelo_agent.refusal_codes import error_result
        self.assertIs(error_result(caught.exception).get("scene_changed"), False)

    def test_turn_evidence_is_bounded_again(self):
        rows = {"scn:%032x:%d" % (0, index): {"handle": "scn:%032x:%d" % (0, index), "revision": "r", "fields": {},
                                             "values": {}} for index in range(1, 200)}
        executor = self.executor({"prepare_turn": {"token": "t", "observation": {}, "recovery_evidence": rows}})
        turn = executor.prepare_turn(None, "chat-1")
        self.assertLessEqual(len(turn.recovery_evidence), recovery_evidence.MAX_RECORDS)

    def test_angelo_tells_every_later_worker_whether_pictures_can_be_drawn(self):
        from angelo_agent import remote_executor as module
        module.OFFSCREEN_VERDICTS.clear()
        refusal = RefusalError("no GPU", code="UNSUPPORTED", details={"gpu_verdict": [False, "no GPU"]})
        first = self.executor({"thumbnail": {"error": values.encode_error(refusal)}})
        with self.assertRaises(RefusalError):
            first.thumbnail(256)
        self.assertIsNone(first.worker.requests[-1]["gpu_verdict"])
        later = self.executor({"thumbnail": {"pictures": []}})
        later.thumbnail(256)
        self.assertEqual(later.worker.requests[-1]["gpu_verdict"], [False, "no GPU"])
        module.OFFSCREEN_VERDICTS.clear()

    def test_turns_and_snapshots_are_the_workers(self):
        executor = self.executor({"prepare_turn": {"token": "t", "observation": {"objects": []},
                                                   "recovery_evidence": {}, "stats": {}},
                                  "checkpoint": {"record": {"id": "cp-1", "state": "READY"}}})
        turn = executor.prepare_turn(None, "chat-1", message="hi")
        turn.commit()
        turn.commit()   # Once only.
        self.assertEqual([r["op"] for r in executor.worker.requests], ["prepare_turn", "commit_turn"])
        controller = SimpleNamespace(checkpoints=SimpleNamespace(root="/state", session_id="chat-1"),
                                     _turn_checkpoint_policy=None)
        with patch("angelo_agent.snapshot_service.after_capture") as after:
            self.assertEqual(executor.capture_checkpoint(controller)["id"], "cp-1")
        after.assert_called_once()
        self.assertEqual(executor.worker.requests[-1]["root"], "/state")


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.state_folder = tempfile.mkdtemp()

    def tearDown(self):
        worker_routing._workers.clear()
        worker_routing._registries.clear()

    def controller(self, name="s1", busy=False):
        import uuid
        session = str(uuid.uuid5(uuid.NAMESPACE_URL, name))   # Chat ids are UUIDs, as the snapshot store needs.
        from angelo_agent.controller import Controller
        controller = Controller.__new__(Controller)   # Only the state the binding and its fences read.
        controller.state, controller.busy, controller.pending = {"id": session, "messages": []}, busy, {}
        controller.document, controller.document_invalid, controller.checkpoint_factory = None, False, None
        controller.documents, controller._native_preview_cards = [], {}
        controller.store = SimpleNamespace(directory=Path(self.state_folder))
        controller.files = SimpleNamespace(root="/workspace", precondition=lambda *a: "ran", execute=lambda *a: "ran")
        controller.mark_memory_stale = lambda reason: controller.documents.append(("stale", reason))
        from angelo_agent.checkpoints import CheckpointStore
        controller.checkpoints = CheckpointStore(tempfile.mkdtemp(), session)
        return controller

    def test_only_an_idle_unbound_chat_binds_and_it_follows_the_workers_document(self):
        with self.assertRaises(RuntimeError):
            worker_routing.bind(self.controller(busy=True), FakeWorker(), "w")
        controller = self.controller()
        executor = worker_routing.bind(controller, FakeWorker(), "w")
        self.assertIs(worker_routing.bound(controller), executor)
        self.assertEqual(controller.document, ("angelo-worker", "w", ("serial", 34)))
        self.assertTrue(callable(controller.checkpoint_factory))
        with self.assertRaises(RuntimeError):
            worker_routing.bind(controller, FakeWorker(), "w2")
        controller.set_document(("angelo's own window document",))   # A load in Angelo: not this chat's document.
        self.assertEqual(controller.document, executor.document_key)
        self.assertFalse(controller.document_invalid)
        # Kept in Angelo's own store (its limits apply), never under the chat's session, which Restore reads.
        self.assertEqual(controller.checkpoints.session_id, worker_routing.worker_snapshot_session(controller.state["id"]))
        worker_routing.unbind(controller, worker_routing.END_MERGED)
        self.assertIsNone(worker_routing.bound(controller))
        self.assertTrue(controller.document_invalid)
        self.assertTrue(worker_routing.worker_snapshots(controller))   # Still refused by Restore after an unbind.
        self.assertIsNone(controller.checkpoint_factory)

    def test_a_worker_chat_never_follows_angelos_document_after_a_restart(self):
        from angelo_agent.controller_external import bound_target
        controller = self.controller()
        worker_routing.bind(controller, FakeWorker(), "w")
        worker_routing._registries.clear()   # A restart: the binding is gone, the record is read from disk.
        again = self.controller()
        window = object()
        executor, document = bound_target(again, window, ("window",))
        self.assertIsNot(executor, window)
        self.assertEqual(document[2], "ended")
        for method in (executor.precondition, executor.execute):
            with self.assertRaises(RefusalError) as caught:
                method("angelo_create_cube", {})
            self.assertEqual(caught.exception.code, "CANCELLED")
            self.assertIs(caught.exception.partial_changes_possible, False)
        with self.assertRaises(RefusalError):
            worker_routing.prepare_turn(again, window, "hi")
        self.assertTrue(worker_routing.worker_snapshots(again))
        with self.assertRaises(RefusalError):   # Its file tools too, sealed at its first use (break-it L-a).
            again.files.execute("angelo_write_file", {}, None, None)
        self.assertEqual(again.files.root, "/workspace")
        ordinary = self.controller("s2")
        self.assertEqual(ordinary.files.execute("angelo_list_files", {}), "ran")
        self.assertEqual(bound_target(ordinary, window, ("window",)), (window, ("window",)))
        self.assertFalse(worker_routing.worker_snapshots(ordinary))

    def test_a_worker_chats_file_tools_are_refused_with_nothing_run(self):
        controller = self.controller()
        worker_routing.bind(controller, FakeWorker(), "w")
        for method in (controller.files.precondition, controller.files.execute):
            with self.assertRaises(RefusalError) as caught:
                method("angelo_write_file", {"path": "a.txt", "content": "x"})
            self.assertIs(caught.exception.partial_changes_possible, False)

    def test_a_chat_that_already_worked_is_never_moved_to_a_worker(self):
        controller = self.controller()
        controller.state["messages"] = [{"role": "user", "text": "make a chair"}]
        with self.assertRaises(RuntimeError):
            worker_routing.bind(controller, FakeWorker(), "w")
        self.assertIsNone(worker_routing.bound(controller))

    def test_angelos_restore_leaves_a_worker_chat_alone(self):
        from angelo_agent.controller_recovery import rebind_after_restore
        controller = self.controller()
        executor = worker_routing.bind(controller, FakeWorker(), "w")
        self.assertEqual(rebind_after_restore(controller, ("angelo's document",), "cp"), {"warnings": []})
        self.assertEqual(controller.document, executor.document_key)

    def test_every_caller_gets_the_chats_own_executor(self):
        from angelo_agent.controller_external import bound_target
        controller = self.controller()
        window_executor = object()
        self.assertEqual(bound_target(controller, window_executor, ("window",)), (window_executor, ("window",)))
        executor = worker_routing.bind(controller, FakeWorker(), "w")
        self.assertEqual(bound_target(controller, window_executor, ("window",)), (executor, executor.document_key))
        self.assertIs(executor.studio_context, controller)
        seen = []
        with patch("angelo_agent.controller.Controller._pump_tools",
                   lambda self, ex, doc, *rest: seen.append((ex, doc))), \
                patch("angelo_agent.controller.Controller._main_thread_block", lambda *a: None):
            controller._request_metrics = None
            from angelo_agent.controller import ToolDispatchBudget
            controller.pump_tools(window_executor, ("window",), budget=ToolDispatchBudget(seconds=60))
        self.assertEqual(seen, [(executor, executor.document_key)])


class FakeCall(SimpleNamespace):
    pass


class FakeShadow:
    """The worker's own controller, reduced to what ``WorkerHost.edit`` drives."""

    def __init__(self, precondition):
        self.state = {"id": "chat-1"}
        self.pending, self.finished, self.approved_calls, self.has_dispatchable_tools = {}, [], [], False
        self.precondition, self.closed = precondition, []

    def open_external_read(self):
        return "fence"

    def close_external_read(self, fence):
        self.closed.append(fence)

    def request_tool(self, call_id, name, arguments, callback):
        self.callback = callback
        self.pending["chat-1/" + call_id] = FakeCall(id="chat-1/" + call_id, name=name, approval=True,
                                                     executing=False, authorized=False, recovery_review=False,
                                                     committing=False, precondition=self.precondition)

    def pump_events(self):
        pass

    def pump_tools(self, executor, document, apply_mutation=None, budget=None, commit_mutation=None):
        pass

    def finish_tool(self, call, result):
        self.pending.pop(call.id, None)
        self.finished.append(result)
        self.callback(result)


class WorkerHostTests(unittest.TestCase):
    def host(self):
        ui = SimpleNamespace(document_key=lambda: ("serial", 34), main_window=lambda: "window", _controllers={},
                             Controller=object, state_directory=lambda: "/state")
        bpy = SimpleNamespace(data=SimpleNamespace(is_dirty=False), context=SimpleNamespace(temp_override=None))
        return worker_host.WorkerHost(ui, bpy, endpoint=None, part="/w/part.blend")

    def test_an_edit_whose_binding_changed_since_approval_runs_nothing(self):
        host = self.host()
        shadow = FakeShadow(precondition=(1, (0.0, 0.0, 0.0)))
        with patch("angelo_agent.controller.pump_controller_tools"):
            answer = host.edit(shadow, None, {"name": "angelo_set_transform", "arguments": {},
                                              "approved": values.encode((1, (5.0, 0.0, 0.0)))})
        self.assertEqual(answer["result"]["error_code"], "STALE_REVISION")
        self.assertIs(answer["result"]["scene_changed"], False)
        self.assertEqual(shadow.closed, ["fence"])

    def test_an_edit_whose_binding_holds_is_approved_once(self):
        host = self.host()
        shadow = FakeShadow(precondition=(1, (5.0, 0.0, 0.0)))
        approved = []

        def approve(*args, **kwargs):
            approved.append(kwargs["call_id"])
            call = shadow.pending[kwargs["call_id"]]
            call.executing = True
            shadow.finish_tool(call, {"success": True})
            return {"FINISHED"}
        host.bpy.ops = SimpleNamespace(angelo=SimpleNamespace(approve=approve))
        host.bpy.context.temp_override = lambda **_: __import__("contextlib").nullcontext()
        with patch("angelo_agent.controller.pump_controller_tools"):
            answer = host.edit(shadow, None, {"name": "angelo_set_transform", "arguments": {},
                                              "approved": values.encode((1, (5.0, 0.0, 0.0)))})
        self.assertEqual(answer["result"], {"success": True})
        self.assertEqual(len(approved), 1)

    def test_a_picture_is_drawn_only_after_a_child_drew_one_and_never_kills_the_worker(self):
        host = self.host()
        host.bpy.app = SimpleNamespace(binary_path="/app/Angelo")
        runs = []

        def crashed(command, *args, **options):   # The test draw's process died, as on a machine with no GPU.
            runs.append(command)
            return SimpleNamespace(returncode=-6, stdout="")
        visible = SimpleNamespace(visible_get=lambda: True)
        host.ui.main_window = lambda: SimpleNamespace(scene=SimpleNamespace(objects=[visible]))
        with patch.object(worker_host, "_run_group", crashed), patch("angelo_agent.child_process.run", crashed):
            for _ in range(2):
                with self.assertRaises(RefusalError) as caught:
                    host.thumbnail(FakeShadow(None), SimpleNamespace(), {"size": 256})
                self.assertIs(caught.exception.partial_changes_possible, False)
                self.assertIn("cannot draw offscreen pictures", str(caught.exception))
                self.assertIs(caught.exception.details["gpu_verdict"][0], False)   # Angelo keeps it for others.
        self.assertEqual(len(runs), 1)   # Learnt once: a failed probe is never run again.
        told = self.host()   # A later worker Angelo tells: it never runs the probe at all.
        told.ui.main_window, told.bpy.app = host.ui.main_window, host.bpy.app
        with patch.object(worker_host, "_run_group", crashed), patch("angelo_agent.child_process.run", crashed):
            with self.assertRaises(RefusalError):
                told.thumbnail(FakeShadow(None), SimpleNamespace(), {"size": 256, "gpu_verdict": [False, "no GPU"]})
        self.assertEqual(len(runs), 1)
        empty = self.host()   # An empty part needs no draw, so no probe either.
        empty.ui.main_window = lambda: SimpleNamespace(scene=SimpleNamespace(objects=[]))
        self.assertTrue(empty.thumbnail(FakeShadow(None), SimpleNamespace(), {"size": 256})["empty"])
        self.assertIn("--background", runs[0])
        drew = SimpleNamespace(returncode=0, stdout="ANGELO-OFFSCREEN-OK\n")
        self.assertEqual(worker_host.offscreen_draw_usable(host.bpy, run=lambda *a, **k: drew), (True, ""))
        self.assertFalse(worker_host.offscreen_draw_usable(host.bpy, run=lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="no marker"))[0])

        def raised(*args, **kwargs):
            raise OSError("no such binary")
        self.assertFalse(worker_host.offscreen_draw_usable(host.bpy, run=raised)[0])

    def test_requests_for_tools_a_worker_does_not_run_are_refused_there_too(self):
        host = self.host()
        host.shadow = lambda session: FakeShadow(None)
        host.executor = lambda shadow: None
        for op in ("precondition", "execute", "rebind_program", "precondition_change"):
            with self.subTest(op=op), self.assertRaises(RefusalError):
                host.handle({"op": op, "name": "angelo_sculpt_batch", "arguments": {}})
        answer = host.handle({"op": "precondition", "name": "angelo_create_cube", "arguments": {},
                              "identifiers_digest": "never-sent"})
        self.assertEqual(answer, {"resend_identifiers": True})


def _function(path, name, owner=None):
    tree = ast.parse((ADDON / path).read_text(encoding="utf-8"))
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == owner] if owner else [tree]
    for root in nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment((ADDON / path).read_text(encoding="utf-8"), node)
    raise AssertionError(name)


class WindowRoutingSourceTests(unittest.TestCase):
    def test_the_windows_call_sites_route_by_chat(self):
        approve = _function("ui.py", "execute", "ANGELO_OT_approve")
        branch = approve.index('getattr(controller, "remote_executor", None)')
        self.assertLess(branch, approve.index("use_global_undo"))   # Before the window's Undo route is chosen.
        self.assertIn("worker_dispatch.approve(controller, call)", approve)   # On the chat's lane (worker_dispatch).
        self.assertIn('if not getattr(c, "remote_executor", None)', _function("ui.py", "invalidate_workspace_state"))
        self.assertIn("worker_routing.stop_all()", _function("ui.py", "unregister"))
        self.assertIn("worker_routing.deliver(controller", _function("console.py", "execute"))
        self.assertIn("worker_routing.worker_snapshots(controller)", _function("checkpoint_restore_ui.py", "execute_restore"))
        self.assertIn("worker_routing.prepare_turn(controller", _function("voice_ui.py", "execute"))

    def test_the_controller_entry_points_take_the_binding(self):
        for name in ("pump_tools", "approve_tool", "execute_tool"):
            with self.subTest(name=name):
                self.assertIn("executor, document = bound_target(self, executor, document)",
                              _function("controller.py", name))
        self.assertIn("document = bound_target(self, None, document)[1]", _function("controller.py", "set_document"))
        self.assertIn('getattr(executor, "prepare_turn", turn_context.prepare)',
                      _function("controller.py", "_answer_observation_request"))
        self.assertIn('getattr(self, "remote_captures", {}).pop(call.id, None)', _function("controller.py", "finish_tool"))


class SealedFilesTests(unittest.TestCase):
    """A worker chat's files stay sealed whatever workspace it is given later (break-it E2E-1 M-7)."""

    def test_a_workspace_assigned_after_the_seal_is_sealed_too(self):
        from angelo_agent.controller import Controller
        self.assertTrue(issubclass(Controller, worker_routing.SealedFiles))   # The real chat has the property.

        class Chat(worker_routing.SealedFiles):
            pass
        worker, person = Chat(), Chat()
        worker.files = person.files = "first workspace"
        worker_routing.seal_files(worker)
        worker.files = person.files = "the person's project folder"   # ui.prepare_controller's assignment.
        self.assertIsInstance(worker.files, worker_routing.WorkerChatFiles)
        self.assertEqual(worker.files.files, "the person's project folder")
        self.assertEqual(person.files, "the person's project folder")   # An ordinary chat is never sealed.
        worker.files = None
        self.assertIsNone(worker.files)


if __name__ == "__main__":
    unittest.main()
