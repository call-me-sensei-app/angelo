# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A recovery hold's hints: never an empty list, and every tool named as the chat's model calls it (``recovery_reads``).

Recorded (C3 early town-centre diagnostic, Sol on Codex): a stale script left a hold that named no target, and its
refusal read "Read again: ." with nothing after it; the hint's ``angelo_scene_settings`` was
``tools.more__angelo_scene_settings`` in code mode. Pinned here for every hold kind and runtime, not that one. The
hold's semantics are C3's own: a hold that names no target waits for the next request, which clears it, Angelo's
own continuation included (a send with ``host_request``, as ``worker_resume`` makes when workers end). No Blender.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_refusal_recovery import HANDLE, TOOL, failure, ran  # noqa: E402
from agent_runtime_fixtures import AgentRuntimeFixture, program  # noqa: E402
from unittest.mock import patch  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.request_recovery import RecoveryBarrier  # noqa: E402
from angelo_agent.tool_specs import CODEX_DEFERRED_NAMESPACE, TOOL_MAP, codex_tools  # noqa: E402

SCRIPT = {"description": "Retry", "code": "result = {}"}
EMPTY = {"mode": "APPLY", "read_set": [], "write_set": [], "operations": []}


def targetless(code="STALE_REVISION"):
    result = ran(code)
    result["refusal_details"] = {}
    return result


def namer(controller):
    from angelo_agent import recovery_reads   # Imported per test: each fails on its own where the module is absent.
    return recovery_reads.namer(controller)


def chat(mode, voice=None):
    return SimpleNamespace(state={"provider": mode}, voice=voice)


def refusal(barrier):
    try:
        barrier.check(TOOL, program())
    except RefusalError as error:
        return str(error)
    return None


def callable_names(mode, voice=False):
    """Every tool name the chat's model can call, as its runtime declares it."""
    if mode.startswith("codex") and not voice:
        names = set()
        for tool in codex_tools(None, defer=True):
            if tool.get("type") == "namespace":
                names.update(CODEX_DEFERRED_NAMESPACE + "__" + inner["name"] for inner in tool["tools"])
            else:
                names.add(tool["name"])
        return names
    return set(TOOL_MAP)


CASES = ((targetless(), EMPTY), (targetless("INTERNAL_ERROR"), EMPTY), (ran(), program()), (ran("UNKNOWN"), program()),
         (ran("STALE_CONTEXT"), program()), (failure(), program()))


class EmptyListTests(unittest.TestCase):
    def test_no_refusal_names_an_empty_list_and_a_targetless_hold_says_what_it_waits_for(self):
        for result, arguments in CASES:
            with self.subTest(code=result["error_code"], targets=bool(arguments["write_set"])):
                barrier = RecoveryBarrier()
                barrier.refused(TOOL, arguments, dict(result))
                text = refusal(barrier)
                if text is None:
                    continue   # Nothing holds (the refusal changed nothing).
                for empty in (": .", ": )", "()", " ,", ":  "):
                    self.assertNotIn(empty, text)
                if not barrier.description().get("read"):
                    self.assertIn("wait for the next request", text)
                    self.assertNotIn("Read again", text)
                else:
                    self.assertIn("Read again: " + HANDLE + " (angelo_scene_object", text)


class CallableNameTests(unittest.TestCase):
    MODES = (("codex_subscription", False), ("codex_api", False), ("codex_subscription", True),
             ("claude_subscription", False), ("claude_api", False))

    def test_every_named_tool_is_callable_by_that_name_in_its_runtime(self):
        for mode, voice in self.MODES:
            names = callable_names(mode, voice)
            for result, arguments in CASES:
                with self.subTest(mode=mode, voice=voice, code=result["error_code"]):
                    barrier = RecoveryBarrier(namer(chat(mode, voice=object() if voice else None)))
                    barrier.refused(TOOL, arguments, dict(result))
                    description = barrier.description()
                    self.assertLessEqual(set(description.get("read_tools", ())), names, (mode, description))
                    for text in (description["reason"], refusal(barrier) or ""):
                        for word in text.replace("(", " ").replace(")", " ").replace(",", " ").split():
                            if word.startswith(("angelo_", CODEX_DEFERRED_NAMESPACE + "__")):
                                self.assertIn(word.rstrip(".:"), names, (mode, text))

    def test_code_mode_names_a_deferred_read_with_its_namespace(self):
        barrier = RecoveryBarrier(namer(chat("codex_subscription")))
        barrier.refused(TOOL, program(), ran())
        self.assertEqual(barrier.description()["read_tools"], ["angelo_scene_object", "more__angelo_scene_settings"])
        self.assertIn("(angelo_scene_object, more__angelo_scene_settings)", refusal(barrier))
        plain = RecoveryBarrier(namer(chat("claude_subscription")))
        plain.refused(TOOL, program(), ran())
        self.assertEqual(plain.description()["read_tools"], ["angelo_scene_object", "angelo_scene_settings"])
        self.assertEqual(namer(chat(None))("angelo_scene_settings"), "angelo_scene_settings")

    def test_a_refusals_own_prose_names_tools_as_the_codex_model_calls_them(self):
        from angelo_agent import recovery_reads
        result = {"error": "The scene settings changed; read angelo_scene_settings again before retrying",
                  "error_code": "STALE_REVISION", "recovery": {"reason": "angelo_scene_object or angelo_scene_settings"},
                  "objects": ["angelo_scene_settings"]}
        named = recovery_reads.named_for(chat("codex_subscription"), result)
        self.assertIn("read more__angelo_scene_settings again", named["error"])
        self.assertEqual(named["recovery"]["reason"], "angelo_scene_object or more__angelo_scene_settings")
        self.assertEqual(named["objects"], ["angelo_scene_settings"], "only refusal and recovery text is renamed")
        self.assertEqual(recovery_reads.named_for(chat("codex_subscription"), named), named, "never twice")
        self.assertIs(recovery_reads.named_for(chat("claude_subscription"), result), result)

    def test_the_codex_delivery_boundary_renames(self):
        from angelo_agent import providers
        packed = providers.tool_content(chat("codex_subscription"), "angelo_scene_program", {
            "error": "read angelo_scene_settings again", "error_code": "STALE_REVISION"})
        self.assertIn("more__angelo_scene_settings", json.loads(packed[0]["text"])["error"])


class ControllerPathTests(unittest.TestCase):
    """The recorded path in the Controller: a script refused stale at its run, then a retry on Codex."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = Controller(directory.name)
        self.addCleanup(self.c.close)
        self.c.busy = True
        self.c.set_document(("scratch",))
        self.c.state["options"]["approval"] = "ASK"
        self.c.state["provider"] = "codex_subscription"
        self.c.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
        self.revision = 0
        self.executor = SimpleNamespace(precondition=lambda name, args: self.revision,
                                        execute=lambda name, args: {"success": True, "scene_changed": True})

    def request(self, identity, name, arguments):
        replies = []
        self.c.request_tool(identity, name, arguments, replies.append)
        self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60),
                          apply_mutation=lambda call: self.c.execute_tool(call, self.executor, self.c.document))
        return replies, self.c.pending.get(self.c.state["id"] + "/" + identity)

    def test_a_stale_script_leaves_a_hold_whose_hint_is_never_empty(self):
        replies, script = self.request("script", "angelo_execute_python", SCRIPT)
        self.revision += 1   # Something else changed the scene while the card waited.
        self.c.review_script(script)
        self.c.execute_tool(script, self.executor, self.c.document)
        self.assertEqual(replies[0]["error_code"], "STALE_REVISION")
        self.assertEqual(replies[0]["recovery"]["read_tools"], ["angelo_scene_object", "more__angelo_scene_settings"])
        retry, _ = self.request("retry", "angelo_execute_python", SCRIPT)
        self.assertEqual(retry[0]["error_code"], "RECOVERY_REQUIRED")
        self.assertNotIn("Read again: .", retry[0]["error"])
        self.assertIn("wait for the next request", retry[0]["error"])


class NextRequestTests(AgentRuntimeFixture, unittest.TestCase):
    """A no-target hold from an unclassified failure is cleared by the next request, through ``Controller.send``."""

    def send(self, **kwargs):
        self.c.busy = False
        with patch("angelo_agent.controller.threading.Thread",
                   return_value=SimpleNamespace(start=lambda: None, is_alive=lambda: False)):
            self.c.send("Workers 1 and 2 are done", **kwargs)

    def held(self):
        self.c.recovery_barrier.refused(TOOL, EMPTY, targetless("INTERNAL_ERROR"))
        self.assertIsNotNone(refusal(self.c.recovery_barrier))
        self.assertIn("wait for the next request", refusal(self.c.recovery_barrier))

    def test_angelos_own_continuation_clears_it(self):
        self.held()
        self.send(host_request=["1 \"Library\"", "2 \"School\""])   # worker_resume's own next request.
        self.assertIsNone(self.c.recovery_barrier.pending)
        self.assertFalse(self.c.recovery_barrier.check(TOOL, program()))

    def test_the_persons_next_request_clears_it(self):
        self.held()
        self.send()
        self.assertIsNone(self.c.recovery_barrier.pending)
        self.assertFalse(self.c.recovery_barrier.check(TOOL, program()))


if __name__ == "__main__":
    unittest.main()
