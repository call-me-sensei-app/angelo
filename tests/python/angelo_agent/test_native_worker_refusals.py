# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat refuses the same calls on both paths (``worker_refusals``), without Blender.

Pinned here: for every tool Angelo declares, and the arguments each refusal turns on (a PREVIEW, a script that names a
file load, one that does not), the lane path (``RemoteExecutor`` in Angelo, ``WorkerHost`` again in the worker) and
the hosted path (design A′: a chat its worker hosts, whose executor ``controller_external.bound_target`` confines,
``worker_chat_executor``) refuse exactly the same calls with the same code and words, and pass the same calls on.
A hosted chat's turn gets ``UNSUPPORTED`` for a job and for a script that loads a file, before any card and with
nothing run, and its file tools are sealed as a lane chat's are. Evidence: FE's native A′ coverage, defect D6
(parallel_workers @hosted: ``angelo_job_start`` answered VALIDATION, an approved ``read_homefile`` script replaced
the document and ended the chat).
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import controller_external, remote_executor, worker_host, worker_refusals  # noqa: E402
from angelo_agent import worker_values as values  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP  # noqa: E402
from angelo_agent.worker_chat_executor import ConfinedExecutor, confined  # noqa: E402
from test_native_parallel_workers_routing import FakeWorker, chat  # noqa: E402
from test_native_worker_chat_host import FakeProvider, Fixture  # noqa: E402

SCRIPT = "angelo_execute_python"
VARIANTS = ({}, {"mode": "PREVIEW"}, {"mode": "APPLY"},
            {"description": "x", "code": "import bpy\nbpy.ops.wm.read_homefile(use_empty=True)"},
            {"description": "x", "code": "import bpy\ngetattr(bpy.ops.wm, 'revert_mainfile')()"},
            {"description": "x", "code": "import bpy\nbpy.data.objects.new('Kept', None)"})
NAMES = sorted(TOOL_MAP) + ["angelo_not_a_tool"]


class Recording:
    """The window's ``BlenderExecutor`` as far as a confined executor passes calls on: each recorded, none run."""

    def __init__(self):
        self.calls = []

    def precondition(self, name, arguments):
        self.calls.append(("precondition", name))
        return "bound"

    def execute(self, name, arguments):
        self.calls.append(("execute", name))
        return {"success": True}

    def start_native_job(self, name, arguments, *, approved_obligations=None):   # BlenderExecutor's signature.
        self.calls.append(("start_native_job", name, approved_obligations))
        return "job"


def outcome(method, name, arguments):
    """("refused", code, words) or ("passed",) for one call through ``method``."""
    try:
        method(name, arguments)
    except RefusalError as error:
        return ("refused", error.code, str(error), error.partial_changes_possible)
    return ("passed",)


def hosted_chat():
    return SimpleNamespace(chat_host=SimpleNamespace(host=None), state={"id": "chat-1"})


class BothPathsRefuseTheSameCallsTests(unittest.TestCase):
    def lane(self):
        executor = remote_executor.RemoteExecutor(FakeWorker({"precondition": {"value": values.encode("bound")}}), "w1")
        executor.studio_context = chat()
        executor._local_executor = lambda: Recording()
        return executor

    def test_every_tool_and_refusal_argument_has_one_outcome_on_both_paths(self):
        lane = self.lane()
        hosted = confined(Recording(), hosted_chat())
        refused = 0
        for name in NAMES:
            for arguments in VARIANTS:
                with self.subTest(name=name, arguments=arguments):
                    there = outcome(lane.precondition, name, dict(arguments))
                    self.assertEqual(outcome(hosted.precondition, name, dict(arguments)), there)
                    refused += there[0] == "refused"
                    if there[0] == "refused":
                        self.assertEqual(there[1], "UNSUPPORTED")
                        self.assertIs(there[3], False)   # Nothing ran.
        self.assertGreater(refused, len(VARIANTS))   # The comparison covered refusals, not only passes.
        self.assertNotIn(("precondition", "angelo_job_start"), hosted.inner.calls)   # A refusal passes nothing on.

    def test_the_lane_paths_worker_refuses_every_call_the_lead_refuses(self):
        """The worker checks again (``WorkerHost.handle``): with the same words, and the chat's own tools too, which
        Angelo never sends it."""
        lane = self.lane()
        host = worker_host.WorkerHost(SimpleNamespace(main_window=lambda: "window", Controller=object), None, endpoint=None, part="/p")
        host.shadow = lambda session: SimpleNamespace(state={"id": "chat-1"})
        host.executor = lambda shadow: Recording()
        host.bind_identifiers = lambda shadow, request: True
        for name in NAMES:
            for arguments in VARIANTS:
                with self.subTest(name=name, arguments=arguments):
                    there = outcome(lane.precondition, name, dict(arguments))
                    here = outcome(lambda n, a: host.handle({"op": "precondition", "name": n, "arguments": a,
                                                             "session": "chat-1"}), name, dict(arguments))
                    if name in worker_refusals.local_tools():
                        self.assertEqual(here[:2], ("refused", "UNSUPPORTED"))
                    else:
                        self.assertEqual(here, there)

    def test_a_hosted_chats_executor_is_confined_wherever_it_comes_from(self):
        given = Recording()
        executor, document = controller_external.bound_target(hosted_chat(), given, ("doc", 1))
        self.assertIsInstance(executor, ConfinedExecutor)
        self.assertIs(executor.inner, given)
        self.assertEqual(document, ("doc", 1))
        self.assertIs(confined(executor, hosted_chat()), executor)   # Never twice.
        executor.studio_context = "the chat"   # What the controller sets reaches the window's executor.
        self.assertEqual(given.studio_context, "the chat")
        window_chat = SimpleNamespace(state={"id": "w"}, store=None)
        self.assertIs(controller_external.bound_target(window_chat, given, ("doc", 1))[0], given)

    def test_a_confined_executor_refuses_a_job_and_a_load_on_every_route_with_nothing_passed_on(self):
        executor = confined(Recording(), hosted_chat())
        for name, arguments in (("angelo_job_start", {"kind": "RENDER_STILL", "output": "still.png"}),
                                (SCRIPT, {"description": "x", "code": "import bpy\nbpy.ops.wm.read_homefile()"})):
            for method in (executor.precondition, executor.execute, executor.start_native_job):
                with self.subTest(name=name, method=method.__name__), self.assertRaises(RefusalError) as caught:
                    method(name, arguments)
                self.assertEqual(caught.exception.code, "UNSUPPORTED")
        self.assertEqual(executor.inner.calls, [])


class NativeJobTests(unittest.TestCase):
    def test_a_confined_native_job_gets_only_the_keywords_the_window_executor_takes(self):
        """``program_completion`` reads the starter's signature: the window's takes ``approved_obligations`` only
        (the first native run of this fix passed ``invocation_id`` through a ``**`` and every commit failed)."""
        import ast
        from angelo_agent import program_completion
        tree = ast.parse((ADDON / "blender_tools.py").read_text(encoding="utf-8"))
        window = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                      and node.name == "start_native_job")
        self.assertEqual([arg.arg for arg in window.args.kwonlyargs], ["approved_obligations"])   # As Recording's.
        executor = confined(Recording(), hosted_chat())
        call = SimpleNamespace(id="c1", name="angelo_scene_program", arguments={"operations": []},
                               program_arguments=None, precondition=SimpleNamespace(obligations=("o",)))
        self.assertEqual(program_completion.start_native_job(call, executor), "job")
        self.assertEqual(executor.inner.calls, [("start_native_job", "angelo_scene_program", ("o",))])


class HostedTurnRefusalTests(Fixture):
    def turn(self, name, arguments):
        FakeProvider.tools = ((name, arguments),)
        self.open()
        self.ask("chat.send", text="go", attachments=[])
        self.run_turn()
        return FakeProvider.turned()[0].results[0]

    def test_a_hosted_chats_job_is_unsupported_and_nothing_ran(self):
        result = self.turn("angelo_job_start", {"kind": "RENDER_STILL", "output": "still.png"})
        self.assertEqual(result.get("error_code"), "UNSUPPORTED", result)
        self.assertEqual(self.executor.calls, [])

    def test_a_hosted_chats_script_that_loads_a_file_is_refused_before_its_card(self):
        result = self.turn(SCRIPT, {"description": "x", "code": "import bpy\nbpy.ops.wm.read_homefile(use_empty=True)"})
        self.assertEqual(result.get("error_code"), "UNSUPPORTED", result)
        self.assertIn("read_homefile", result.get("error", ""))
        self.assertIs(result.get("scene_changed", False), False)
        cards = [call for event in self.endpoint.events("pending") for call in event["data"]["calls"]
                 if call["summary"]["approval"] or call["needs_person"]]
        self.assertEqual(cards, [])   # It waited for admission only: no card was ever drawn for it.
        self.assertEqual(self.bpy.approved, [])

    def test_a_hosted_chats_file_tools_are_sealed(self):
        from angelo_agent.worker_routing import WorkerChatFiles
        self.open()
        chat = self.chats.hosted(self.session)
        self.assertIsInstance(chat.files, WorkerChatFiles)
        with self.assertRaises(RefusalError) as caught:
            chat.files.precondition("angelo_write_file", {})
        self.assertEqual(caught.exception.code, "UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
