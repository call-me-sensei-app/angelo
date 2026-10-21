# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo side of the unbilled scenario dry run, inside a disposable GUI process (``scenario_dry_run.py``).

Replaces both provider classes with a scripted runtime before the real trial
driver (``eval_live_agent_session.py``) starts, then runs that driver
unchanged: the real controller, attachments, approval and commit operators,
receipts, scenario grading, hygiene report, saved copy and Undo probe. The
scripted runtime plays a perfect model: per turn it checks the image blocks
the controller would send to Claude (``references.claude_inputs``) against
the reference manifest, requests reviewed Python with that turn's
ground-truth code (approved by the scenario rule), starts the turn's
background job when it names one (``angelo_job_start``, approved because the
scenario allows it; the trial driver waits for it) and replies. Nothing
starts a provider process, reads a credential or opens a socket. A diagnostic or warm-up dry run's chat is a Claude
one (``scenario_dry_run.run_angelo``): per request the runtime also records the Claude Code launch the real
builder (``providers.claude_session``) makes from the request's own context, never starting it, so the dry run
can check that debug thinking reached the request and that a warm-up's launch is its trial's (``record_launch``).
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import uuid

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use the dry-run launcher's disposable GUI process"
assert os.environ.get("ANGELO_SCENARIO_DRY_RUN") == "1", "Started only by scenario_dry_run.py"
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
OUT = Path(os.environ["ANGELO_QA_DIR"])
TASK_SET = os.environ["ANGELO_EVAL_TASK_SET"]

import scenario_fake_models as fake  # noqa: E402
import scenario_specs as specs  # noqa: E402

SCENARIO = specs.scenario_id(TASK_SET)
TASKS = specs.tasks(SCENARIO)
IMAGES = {}
LAUNCHES = []
CLAUDE_LAUNCH = "dry-run-claude-launch.json"   # scenario_dry_run.CLAUDE_LAUNCH


CODEX_LAUNCH = "dry-run-codex-launch.json"   # scenario_dry_run.CODEX_LAUNCH
CODEX_LAUNCHES = []


def record_codex_launch(context):
    """A Codex chat: what its thread would open with, from the real provider's own builders, never started.

    The parts of the cached opening a ``cacheAffinityKey`` is derived from (``cache_affinity.prefix_parts``: base
    instructions, dynamic tools, model, provider, sandbox and approval, the plan's output schema) and the turn's
    reasoning summary request, so a warm-up's opening can be compared with its trial's.
    """
    from angelo_agent import cache_affinity, team_runtime
    from angelo_agent.chat_settings import codex_turn_options
    from angelo_agent.codex_plan import output_schema
    from angelo_agent.providers import CodexProvider
    from angelo_agent.tool_specs import codex_tools
    mode = str(context.state.get("provider", ""))
    if not mode.startswith("codex"):
        return
    provider = CodexProvider(context, dict(context.settings))
    start, model = provider._thread_parameters(list(context.models))
    start["dynamicTools"] = codex_tools(getattr(context, "allowed_tools", None),
                                        short_identifiers=provider._short_identifiers(),
                                        defer=team_runtime.openai_service(mode))
    turn = {"outputSchema": output_schema(getattr(context, "allowed_tools", None))}
    CODEX_LAUNCHES.append({"provider": mode, "model": model, "prefix": cache_affinity.prefix_parts(start, turn),
                           "turn_options": codex_turn_options(context),
                           "base_instructions_bytes": len(start["baseInstructions"].encode())})
    (OUT / CODEX_LAUNCH).write_text(json.dumps(CODEX_LAUNCHES, indent=2, default=str))


def record_launch(context):
    """A Claude chat: what its request's Claude Code launch would carry (flags, schema, instructions), unstarted."""
    if not str(context.state.get("provider", "")).startswith("claude"):
        return
    from angelo_agent.providers import CLAUDE_INSTRUCTIONS, CLAUDE_PLAN_INSTRUCTIONS, claude_session
    command, initialize = claude_session("claude", context)
    schema, instructions = initialize["request"]["jsonSchema"], "".join(initialize["request"]["systemPrompt"])
    tool = schema["properties"]["operations"]["items"].get("properties", {}).get("tool", {})
    clean = (getattr(context, "base_instructions", CLAUDE_INSTRUCTIONS) + getattr(context, "instructions", "")
             + CLAUDE_PLAN_INSTRUCTIONS)
    LAUNCHES.append({
        "provider": context.state.get("provider"), "flags": [part for part in command if part.startswith("--")],
        "command": command[1:],   # Without the executable's path; a warm-up's must equal its trial's.
        "initialize_sha256": hashlib.sha256(json.dumps(initialize, sort_keys=True).encode()).hexdigest(),
        "thinking_display": (command[command.index("--thinking-display") + 1]
                             if "--thinking-display" in command else None),
        "allowed_tools": None if context.allowed_tools is None else sorted(context.allowed_tools),
        "schema_bytes": len(json.dumps(schema, separators=(",", ":")).encode()),
        "schema_tools": tool.get("enum") or ([tool["const"]] if "const" in tool else None),
        "instructions_bytes": len(instructions.encode()),
        "instructions_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
        "instructions_mode": "clean" if instructions == clean else "other"})
    (OUT / CLAUDE_LAUNCH).write_text(json.dumps(LAUNCHES, indent=2))


class ScriptedRuntime:
    """A perfect model for the scenario's turns. It never starts a process or opens a socket."""

    def __init__(self, context, settings):
        self.controller, self.warm = context, False

    def connect(self):
        self.warm = True
        self.controller.emit("native_session", {"id": "scenario-dry-run"})
        return "scenario-dry-run"

    def has_warm_runtime(self):
        return self.warm

    def bind_request(self, context):
        self.controller = context

    def stop(self):
        pass

    def stop_request(self, fence):
        pass

    def close(self):
        pass

    def call(self, context, name, arguments):
        done, box = threading.Event(), {}
        context.request_tool("dry-" + uuid.uuid4().hex, name, arguments,
                             lambda result: (box.update(result=result), done.set()))
        if not done.wait(300):
            raise RuntimeError("Scripted tool call timed out: " + name)
        return box["result"]

    def say(self, context, text):
        context.emit("delta", {"item": "scenario-dry-run", "text": text})
        context.emit("message_complete", {"item": "scenario-dry-run", "text": text})

    def run_request(self, context, native_id, text, observation=None):
        context.record_provider_turn()
        context.record_provider_accepted()
        record_launch(context)   # A Claude chat only (a diagnostic dry run).
        record_codex_launch(context)   # A Codex chat only.
        import eval_ask_why
        import eval_warm_up
        if eval_warm_up.PROMPT in text:   # --warm-up-only: the bench's warm-up message, answered like a model would.
            self.say(context, "OK")
            return "completed"
        if eval_ask_why.HEADER in text:   # The --ask-why-on-miss follow-up: a thinking row and an answer, no tool.
            context.emit("thinking", {"item": "dry-run-why", "text": "The dry run's scripted answer."})
            context.emit("thinking_completed", {"item": "dry-run-why"})
            self.say(context, "The scripted model built the ground truth; no check failed.")
            return "completed"
        task = fake.match_turn(text, TASKS)
        if task is None:
            raise RuntimeError("The dry run has no script for: " + text[:120])
        sent = [hashlib.sha256(base64.b64decode(block["source"]["data"])).hexdigest()
                for block in context.references.claude_inputs(context.turn_attachments)]
        expected = [digest for _, _, digest in specs.reference_files(SCENARIO, task["references"])]
        IMAGES[task["id"]] = {"sent": sent, "expected": expected, "match": sent == expected}
        (OUT / "dry-run-images.json").write_text(json.dumps(IMAGES, indent=2))
        self.say(context, "Building step: %s." % task["title"])
        result = self.call(context, "angelo_execute_python", {"description": "Ground truth: " + task["title"],
                                                               "code": fake.turn_code(SCENARIO, task["id"])})
        if not (isinstance(result, dict) and result.get("success")):
            self.say(context, "The step failed: %s" % json.dumps(result)[:300])
            return "failed"
        if specs.is_oneshot(SCENARIO):
            import oneshot_fake_models   # A one-shot agent also looks at its result, before the render job starts.
            for name, arguments in oneshot_fake_models.angelo_follow_up(task):
                self.call(context, name, arguments)
        if task.get("scenario_job"):
            started = self.call(context, "angelo_job_start", dict(task["scenario_job"]))
            if not (isinstance(started, dict) and started.get("job")) or started.get("error"):
                self.say(context, "The job did not start: %s" % json.dumps(started, default=str)[:300])
                return "failed"
            self.say(context, "Started job %s." % started["job"])
        self.say(context, "Done: %s." % task["title"])
        return "completed"


from angelo_agent import controller as controller_module, ui  # noqa: E402

controller_module.CodexProvider = controller_module.ClaudeProvider = ScriptedRuntime
ui._authentication.start = lambda *args, **kwargs: None  # No native sign-in status process.
runpy.run_path(str(HERE / "eval_live_agent_session.py"), run_name="scenario_dry_run")
