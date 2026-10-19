# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A background-job call inside a turn: a refusal is corrected, an approval is not silence.

The job tools under the turn rules of doc/angelo/agent_execution.rst ("Failed
plan steps", "Turn limits"), on both runtimes with stand-in providers:

* A job the host refuses at a known pre-effect admission, or a cancel of a
  job that ended, started nothing. In a typed plan it comes back for a
  corrected plan within the request's correction budget, on Claude's loop and
  on Codex's; as a tool call it is an answer inside the turn. A decline, an
  expiry and a result that does not say the scene is unchanged still end the
  turn.
* A request the host rejects before running anything (``validate_job``) is
  answered in one line that names the limit and what it got.
* While a job start waits for the person's approval the host holds the call,
  so neither turn limit runs. A started job is not held: the call returns at
  once and the job runs outside the turn.

No executable, account, network or inference: a mocked Claude round and the
scripted Codex app-server of ``test_native_codex_plan`` around a real
``Controller``. The refusals themselves against real Blender jobs are in
``test_blender_long_jobs`` and the ``test_blender_production_*`` suites.
"""

import ast
import contextlib
import copy
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import claude_outcomes, job_host, plan_recovery, turn_activity  # noqa: E402
from angelo_agent.job_manager import JobManager, JobStartError  # noqa: E402
from angelo_agent.job_spec import JOB_NAMES, MAX_OUTPUT, MAX_SOURCES, JobRequestError, validate_job  # noqa: E402
from angelo_agent.job_store import JobStore  # noqa: E402
from angelo_agent.providers import CLAUDE_PLAN_REJECTIONS, _plan_problems  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP  # noqa: E402
from test_native_codex_plan import PlanTurnFixture  # noqa: E402
from test_native_turn_recovery import SCRIPT, SCRIPT_RESULT, LoopCase  # noqa: E402

HANDLE = "scn:" + "a" * 32 + ":21"
START = {"tool": "angelo_job_start", "arguments": {"kind": "RENDER_STILL", "output": "renders/shot.png"}}
CANCEL = {"tool": "angelo_job_cancel", "arguments": {"job": "j1"}}
# What the controller answers for a request refused at dispatch, before any approval (Controller.pump_tools).
NO_CAMERA = {"error": "The scene has no active camera to render from", "error_code": "VALIDATION", "scene_changed": False}
# What job_host.execute answers when a known pre-effect admission refuses the approved job.
NOT_STARTED = {"job": "j1", "status": "failed", "scene_changed": False,
               "error_code": "VALIDATION", "error": "Blender refused to start the job: Cannot render, no camera"}
ENDED = {"error": "Job j1 already ended (done)", "error_code": "VALIDATION", "scene_changed": False}
STARTED = {"job": "j1", "status": "running", "kind": "RENDER_STILL", "output": "renders/shot.png",
           "note": "Runs in the background. Its progress and result arrive in a later message's jobs block; "
                   "do not wait or poll. Scene changes are refused until it ends."}
DECLINED = {"error": "The user declined this operation. Do not repeat it."}


class RefusalShapeTests(unittest.TestCase):
    """What the host returns for a refused job, before any loop reads it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = JobManager(JobStore(Path(self.temp.name) / "jobs.json"))
        # job_ui binds the manager to Blender's timers and handlers; the host suite stands in for it.
        stand_in = types.ModuleType("angelo_agent.job_ui")
        stand_in.manager = lambda: self.manager
        patcher = patch.dict(sys.modules, {"angelo_agent.job_ui": stand_in})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.controller = type("Chat", (), {"state": {"id": "chat-1"}, "files": None, "executing_call": None})()

    def test_a_refused_request_says_that_nothing_started(self):
        self.assertFalse(JobRequestError("x").partial_changes_possible)
        self.assertFalse(job_host.JobBusyError("x").partial_changes_possible)

    def test_a_cancel_refused_after_its_approval_is_an_unchanged_result_not_an_exception(self):
        result = job_host.execute(self.controller, object(), "angelo_job_cancel", {"job": "j9"})
        self.assertEqual(result, {"error": "No job j9 in this conversation", "error_code": "OWNERSHIP", "scene_changed": False})

    def test_a_start_refused_after_its_approval_is_an_unchanged_result_not_an_exception(self):
        def refuse(controller, window, arguments):
            raise JobRequestError("A job is already running; pass queue=true to wait behind it", code="VALIDATION")
        with patch.object(job_host, "_admit", refuse):
            result = job_host.execute(self.controller, object(), "angelo_job_start", START["arguments"])
        self.assertEqual(result, {"error": "A job is already running; pass queue=true to wait behind it",
                                  "error_code": "VALIDATION", "scene_changed": False})

    def test_a_known_pre_effect_start_refusal_reports_failed_and_unchanged(self):
        class Refused:
            output_name = None

            def describe(self):
                return {}

            def start(self):
                self.startup_effects_possible = False  # Stand-in for Runner's known pre-effect admission.
                raise JobStartError("Blender refused to start the job: Cannot render, no camera", code="VALIDATION")
        request = validate_job("angelo_job_start", START["arguments"])
        with patch.object(job_host, "_admit", lambda controller, window, arguments: (request, Refused())):
            result = job_host.execute(self.controller, object(), "angelo_job_start", START["arguments"])
        self.assertEqual((result["status"], result["scene_changed"], result["error"]),
                         ("failed", False, NOT_STARTED["error"]))
        self.assertIsNone(self.manager.active(), "Nothing is left running")
        self.assertEqual(result["error_code"], "VALIDATION")

    def test_unclassified_start_and_cleanup_failures_never_claim_unchanged_or_correctable(self):
        for error in (JobStartError("No camera"), JobRequestError("A request-looking unknown"),
                      RuntimeError("Blender refused to start"), JobStartError("Cleanup failed", code="CLEANUP_FAILED")):
            with self.subTest(error=type(error).__name__, code=getattr(error, "code", None)):
                class Failed:
                    output_name = None
                    def describe(self):
                        return {}
                    def start(self):
                        raise error
                request = validate_job("angelo_job_start", START["arguments"])
                with patch.object(job_host, "_admit", lambda *args: (request, Failed())):
                    result = job_host.execute(self.controller, object(), "angelo_job_start", START["arguments"])
                self.assertTrue(result["partial_changes_possible"])
                self.assertNotIn("scene_changed", result)
                self.assertIsNone(plan_recovery.unchanged_failure([START], [result]))
                self.assertIsNone(self.manager.active())

    def test_unclassified_admission_refusal_never_claims_unchanged(self):
        with patch.object(job_host, "_admit", side_effect=JobRequestError(NO_CAMERA["error"])):
            result = job_host.execute(self.controller, object(), "angelo_job_start", START["arguments"])
        self.assertEqual(result["error_code"], "UNKNOWN")
        self.assertTrue(result["partial_changes_possible"])
        self.assertNotIn("scene_changed", result)
        self.assertIsNone(plan_recovery.unchanged_failure([START], [result]))

    def test_real_render_precondition_is_classified_but_operator_exceptions_are_not(self):
        # Execute the two actual producer methods over stand-ins without
        # importing bpy, invoking a native operator or accepting error prose.
        path = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/job_runners.py"
        tree = ast.parse(path.read_text())
        methods = []
        for owner, name in (("RenderRunner", "admit"), ("Runner", "_invoke")):
            cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner)
            methods.append(next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name))
        namespace = {"JobRequestError": JobRequestError, "JobStartError": JobStartError,
                     "bpy": types.SimpleNamespace(context=types.SimpleNamespace(
                         temp_override=lambda **kwargs: contextlib.nullcontext()))}
        exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), str(path), "exec"), namespace)
        with self.assertRaises(JobRequestError) as admission:
            namespace["admit"](types.SimpleNamespace(scene=types.SimpleNamespace(render=object(), camera=None)))
        self.assertEqual(admission.exception.code, "VALIDATION")
        for message in ("No camera", "The user declined", "Expected check failed"):
            with self.subTest(message=message):
                def operator(*args, **kwargs):
                    raise RuntimeError(message)
                with self.assertRaises(JobStartError) as started:
                    namespace["_invoke"](types.SimpleNamespace(window=object()), operator)
                self.assertEqual(started.exception.code, "UNKNOWN")

    def test_any_other_failure_still_raises(self):
        def broken(controller, window, arguments):
            raise RuntimeError("unexpected")
        with patch.object(job_host, "_admit", broken), self.assertRaises(RuntimeError):
            job_host.execute(self.controller, object(), "angelo_job_start", START["arguments"])

    def test_the_rule_names_exactly_the_job_tools(self):
        self.assertTrue(JOB_NAMES <= claude_outcomes._UNCHANGED_WHEN_REFUSED)
        self.assertEqual({name for name in claude_outcomes._UNCHANGED_WHEN_REFUSED if "_job_" in name}, set(JOB_NAMES))

    def test_only_a_result_that_says_unchanged_is_correctable(self):
        for result in (NO_CAMERA, NOT_STARTED, ENDED):
            with self.subTest(result=result):
                self.assertEqual(plan_recovery.unchanged_failure([START], [dict(result)]), result["error"])
        for result in (DECLINED, {"error": NO_CAMERA["error"]}, dict(NO_CAMERA, expired=True),
                       dict(NO_CAMERA, error_code="UNKNOWN"), dict(NO_CAMERA, error_code="INTERNAL_ERROR"),
                       dict(NO_CAMERA, partial_changes_possible=True), dict(NO_CAMERA, interrupted=True),
                       {"error": "This chat is read-only. Scene, library and paid usage changes are disabled; "
                                 "do not retry them.", "scene_changed": False},
                       {"error": "The target changed after the approval request; inspect it before retrying",
                        "scene_changed": False}):
            with self.subTest(result=result):
                self.assertIsNone(plan_recovery.unchanged_failure([START], [result]))


class RejectedBeforeRunningTests(unittest.TestCase):
    """A request the host rejects before it runs anything: one line with the limit and what it got."""

    def refusal(self, arguments):
        with self.assertRaises(JobRequestError) as caught:
            validate_job("angelo_job_start", arguments)
        return str(caught.exception)

    def test_a_length_names_its_limit_and_the_length_it_got(self):
        long_path = "renders/" + "a" * MAX_OUTPUT + ".png"
        message = self.refusal(dict(START["arguments"], output=long_path))
        self.assertEqual(message, "output must be a printable workspace-relative path of at most %d characters "
                                  "(this one has %d)" % (MAX_OUTPUT, len(long_path)))
        bake = {"kind": "BAKE_TEXTURE", "target": HANDLE, "bake_type": "NORMAL"}
        many = ["scn:" + "b" * 32 + ":%d" % number for number in range(1, MAX_SOURCES + 3)]
        self.assertEqual(self.refusal(dict(bake, sources=many)),
                         "sources lists 1 to %d observed object handles to bake from (this one has %d)"
                         % (MAX_SOURCES, len(many)))
        self.assertIn("(this one has 0)", self.refusal(dict(bake, sources=[])))
        for message in (self.refusal(dict(START["arguments"], output=long_path)), self.refusal(dict(bake, sources=many))):
            self.assertNotIn("\n", message)

    def test_the_schema_states_the_same_limits(self):
        properties = TOOL_MAP["angelo_job_start"]["inputSchema"]["properties"]
        self.assertEqual((properties["output"]["maxLength"], properties["sources"]["maxItems"]), (MAX_OUTPUT, MAX_SOURCES))

    def test_a_plan_with_such_a_request_is_rejected_with_that_line_before_anything_runs(self):
        operation = {"tool": "angelo_job_start", "arguments": dict(START["arguments"], output="a" * (MAX_OUTPUT + 6))}
        problems, _ = _plan_problems([operation], frozenset(TOOL_MAP), None)
        self.assertEqual(len(problems), 1)
        self.assertIn("at most %d characters (this one has %d)" % (MAX_OUTPUT, MAX_OUTPUT + 6), problems[0])


class ClaudePlanTests(LoopCase):
    """Claude's typed plan: a refused job call is corrected like a plan stopped before it changed anything."""

    def test_a_job_refused_at_admission_gets_a_corrected_plan(self):
        plans = [{"reply": "", "operations": [START, SCRIPT]}, {"reply": "Rendering as job j1.", "operations": [START]}]
        provider, context = self.provider(plans, [dict(NO_CAMERA), dict(STARTED)])
        self.assertEqual(provider.run(None, "Render the shot", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_job_start", "angelo_job_start"],
                         "The script after the refused job never ran")
        prompt = provider._turn.call_args.args[1]
        self.assertTrue(prompt.startswith("The host stopped your last plan before it changed the scene"))
        self.assertIn(NO_CAMERA["error"], prompt)
        self.assertIn('"angelo_job_start"', prompt, "The exact schema of the deferred tool it used comes with it")
        self.assertTrue(any(text.startswith("The plan stopped before changing the scene") for text in self.messages(context)))

    def test_a_job_blender_refused_to_start_and_a_refused_cancel_come_back_too(self):
        for first, refused in ((START, NOT_STARTED), (CANCEL, ENDED)):
            with self.subTest(tool=first["tool"]):
                plans = [{"reply": "", "operations": [first]}, {"reply": "It had already finished.", "operations": []}]
                provider, context = self.provider(plans, [dict(refused)])
                self.assertEqual(provider.run(None, "Render the shot", observation={}), "completed")
                self.assertEqual(provider._turn.call_count, 2)
                self.assertIn(refused["error"], provider._turn.call_args.args[1])

    def test_after_earlier_operations_ran_only_the_rest_is_asked_for(self):
        plans = [{"reply": "", "operations": [SCRIPT, START]}, {"reply": "", "operations": [START]}]
        provider, context = self.provider(plans, [copy.deepcopy(SCRIPT_RESULT), dict(NO_CAMERA), dict(STARTED)])
        self.assertEqual(provider.run(None, "Add a light, then render", observation={}), "completed")
        self.assertEqual(self.calls(provider), ["angelo_execute_python", "angelo_job_start", "angelo_job_start"])
        head = provider._turn.call_args.args[1].split("\n", 1)[0]
        self.assertIn("operations[1] (angelo_job_start) failed and changed nothing", head)
        self.assertIn(NO_CAMERA["error"], head)
        self.assertIn("do not repeat", head)

    def test_job_refusals_share_the_requests_correction_budget(self):
        plans = [{"reply": "", "operations": [START]}] * (CLAUDE_PLAN_REJECTIONS + 1)
        provider, context = self.provider(plans, [dict(NO_CAMERA)] * (CLAUDE_PLAN_REJECTIONS + 1))
        self.assertEqual(provider.run(None, "Render the shot", observation={}), "interrupted")
        self.assertEqual(provider._turn.call_count, CLAUDE_PLAN_REJECTIONS + 1)
        self.assertEqual(self.messages(context)[-1], NO_CAMERA["error"])

    def test_a_decline_and_an_unknown_state_still_end_the_turn(self):
        for refused in (DECLINED, {"error": "Blender couldn't start the job (OSError)", "partial_changes_possible": True}):
            with self.subTest(refused=refused):
                provider, context = self.provider([{"reply": "", "operations": [START]}], [dict(refused)])
                self.assertEqual(provider.run(None, "Render the shot", observation={}), "interrupted")
                self.assertEqual(provider._turn.call_count, 1)
                self.assertEqual(self.messages(context)[-1], refused["error"])


class CodexTurnTests(PlanTurnFixture):
    """The Codex runtime: a job is a plan item on OpenAI and a tool call elsewhere."""

    def limits(self, idle, stretch):
        for name, value in (("IDLE_SECONDS", idle), ("STRETCH_SECONDS", stretch), ("POLL_SECONDS", 0.01)):
            patcher = patch.object(turn_activity, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_job_refused_at_admission_comes_back_for_a_corrected_plan(self):
        self.results["angelo_job_start"] = [dict(NO_CAMERA), dict(STARTED)]
        status = self.run_turns([("message", self.plan_text(START))], [("message", self.plan_text(START))])
        self.assertEqual(status, "completed")
        prompt = self.starts()[1]["input"][0]["text"]
        self.assertTrue(prompt.startswith("The host stopped your last plan before it changed the scene"))
        self.assertIn(NO_CAMERA["error"], prompt)
        self.assertEqual([name for _, name, _ in self.dispatched], ["angelo_job_start", "angelo_job_start"])
        rows = [row["text"] for row in self.c.state["messages"] if row["role"] == "system"]
        self.assertTrue(any(text.startswith("The plan stopped before changing the scene") for text in rows))

    def test_a_decline_ends_the_request(self):
        self.results["angelo_job_start"] = [dict(DECLINED)]
        self.assertEqual(self.run_turns([("message", self.plan_text(START))]), "interrupted")
        self.assertEqual(len(self.starts()), 1)

    def test_as_a_tool_call_a_refusal_is_an_answer_within_the_turn(self):
        self.results["angelo_job_start"] = [dict(NO_CAMERA), dict(STARTED)]
        status = self.run_turns([("tool", "angelo_job_start", START["arguments"]),
                                 ("tool", "angelo_job_start", START["arguments"]),
                                 ("message", '{"reply": "Added a camera; rendering as job j1.", "operations": []}')])
        self.assertEqual(status, "completed")
        (failed, refusal), (started, answer) = [(payload["success"], payload["contentItems"][0]["text"])
                                                for _, payload in self.p.rpc.responses]
        self.assertFalse(failed)
        self.assertIn(NO_CAMERA["error"], refusal)
        self.assertTrue(started)
        self.assertEqual(json.loads(answer)["job"], "j1")
        self.assertEqual(len(self.starts()), 1, "One turn: the model read the refusal and went on")

    def test_a_job_start_waiting_for_approval_is_host_time_not_model_silence(self):
        # Wide enough that a loaded host reaches the held call within the idle limit; the wait outlasts both.
        self.limits(idle=0.5, stretch=1.0)
        del self.context.request_tool  # The real request context: Controller.pending holds the call.

        def the_person_reads_the_approval():
            deadline = time.monotonic() + 5
            while not self.c.pending and time.monotonic() < deadline:
                time.sleep(0.005)
            (call,) = self.c.pending.values()
            self.held = call.name == "angelo_job_start" and self.context.tools_pending()
            time.sleep(1.5)  # Longer than both limits.
            self.c.finish_tool(call, dict(STARTED))
        status = self.run_turns([("tool", "angelo_job_start", START["arguments"]),
                                 ("call", the_person_reads_the_approval),
                                 ("message", '{"reply": "Rendering as job j1.", "operations": []}')])
        self.assertEqual(status, "completed")
        self.assertTrue(self.held, "The host held the call while it waited")
        self.assertFalse(self.context.tools_pending(), "A started job is not a held call: it runs outside the turn")
        ((_, payload),) = self.p.rpc.responses
        self.assertEqual(json.loads(payload["contentItems"][0]["text"])["job"], "j1")

    def test_without_a_held_call_the_same_wait_is_given_up(self):
        self.limits(idle=0.15, stretch=0.3)
        from angelo_agent.protocol import RpcError
        with self.assertRaisesRegex(RpcError, "nothing came from the model"):
            self.run_turns([("call", lambda: time.sleep(0.7))])


if __name__ == "__main__":
    unittest.main()
