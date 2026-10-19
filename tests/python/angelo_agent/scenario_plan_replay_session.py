# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo side of an unbilled plan replay, inside a disposable GUI process (``scenario_plan_replay.py``).

A replay answers one question without a model: *if a model wrote exactly
these plans, what would the host do with them, and how many bytes are they?*
Both provider classes are replaced by subclasses of the real ones that take
the model's output from a replay file instead of a runtime, and the unchanged
trial driver (``eval_live_agent_session.py``) then sends the scenario's
messages. Everything after the model's output is the product's own code:

* Codex: ``CodexProvider.run`` and ``codex_plan.finish``. The final message
  is the strict JSON a model writes (``strict_schema.encode`` of the scripted
  plan), decoded, validated, run through approvals and summarized by the host.
  In-turn calls go through ``Controller.request_tool`` as ``exec`` calls do.
* Claude: ``ClaudeProvider.run_request``. Each scripted plan is what Claude
  Code returns as structured output; lookups are ``verify`` plans.

The replay file is JSON: ``{"turns": {task id: [step, ...]}}`` where a step is
``{"call": {"tool", "arguments"}}`` (Codex only), ``{"python": {...}}`` (a
reviewed script, either runtime) or ``{"plan": {"reply", "operations",
"verify"?}}``. Scripted plans name existing objects as the model would after
reading its context: ``"@name:<object name>"`` is the handle the latest
observation or result showed for that name, ``"@revision:<object name>"`` the
revision shown beside it and ``"@scene"`` the scene entry's handle. A name the context never showed fails the replay: a model could not
have written it either.

``replay-bytes.json`` in the QA folder records, per turn, the bytes of every
model output as written (final messages, call arguments, scripts). Nothing
starts a provider process, reads a credential or opens a socket.
"""

import json
import os
from pathlib import Path
import re
import runpy
import sys
import threading
import uuid

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use the replay launcher's disposable GUI process"
assert os.environ.get("ANGELO_PLAN_REPLAY"), "Started only by scenario_plan_replay.py"
HERE = Path(__file__).resolve().parent
# The harness may be another checkout's (a scenario set this one does not have); the add-on is always this one's.
HARNESS = Path(os.environ.get("ANGELO_PLAN_REPLAY_HARNESS") or HERE)
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HARNESS)]
OUT = Path(os.environ["ANGELO_QA_DIR"])
REPLAY = json.loads(Path(os.environ["ANGELO_PLAN_REPLAY"]).read_text(encoding="utf-8"))
RUNTIME = os.environ.get("ANGELO_PLAN_REPLAY_RUNTIME", "codex")
TASK_SET = os.environ["ANGELO_EVAL_TASK_SET"]

import angelo_agent  # noqa: E402
# The add-on is imported here, from this checkout, before another checkout's harness puts its own add-on folder
# first on the path. addon_utils reloads a module whose recorded time differs from its file's, and a reload
# resolves the package again through that path: record the time so the harness's enable() keeps this add-on.
angelo_agent.__time__ = os.path.getmtime(angelo_agent.__file__)
from angelo_agent import codex_plan, controller as controller_module, ui  # noqa: E402
from angelo_agent.providers import ClaudeProvider, CodexProvider, _record_runtime_round, tool_content  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402
from angelo_agent.strict_schema import encode  # noqa: E402
import scenario_specs as specs  # noqa: E402

TASKS = specs.tasks(specs.scenario_id(TASK_SET))
_HANDLE = re.compile(r"(?:o[1-9][0-9]*|scn:[a-f0-9]{32}:[0-9]+)\Z")
BYTES = {}
KNOWN = {}  # What the scripted model has read so far: object name -> the handle its context showed.


def _dump(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _task_for(text):
    """The scenario turn a message opens: its prompt, with the message's context blocks after it."""
    return next((task for task in TASKS if task["prompt"][:120] in text), None)


def _learn(value):
    """Remember the handles a context block or a result shows beside a name (``scene`` for the scene entry)."""
    if isinstance(value, dict):
        for key in ("handle", "object"):  # Observation entries and result rows; search rows call it "object".
            handle = value.get(key)
            if isinstance(handle, str) and _HANDLE.match(handle) and isinstance(value.get("name"), str):
                KNOWN[value["name"]] = handle
                revision = value.get("object_revision", value.get("revision"))
                if isinstance(revision, str):
                    KNOWN["@revision:" + value["name"]] = revision
        scene = value.get("scene_settings")
        if isinstance(scene, dict) and isinstance(scene.get("handle"), str):
            KNOWN["@scene"] = scene["handle"]
        for item in value.values():
            _learn(item)
    elif isinstance(value, list):
        for item in value:
            _learn(item)


def _read_context(text):
    """Every JSON value in a message's context blocks (observations, handoffs), as a model reads them."""
    decoder = json.JSONDecoder()
    for marker in ("{", "["):
        position = 0
        while True:
            position = text.find("\n" + marker, position)
            if position < 0:
                break
            try:
                value, _ = decoder.raw_decode(text, position + 1)
                _learn(value)
            except ValueError:
                pass
            position += 1


def _resolved(value):
    if isinstance(value, dict):
        return {key: _resolved(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolved(item) for item in value]
    if isinstance(value, str) and (value.startswith(("@name:", "@revision:")) or value == "@scene"):
        key = value[len("@name:"):] if value.startswith("@name:") else value
        if key not in KNOWN:
            raise RuntimeError("The replay names %r, which no context block or result has shown" % key)
        return KNOWN[key]
    return value


def _steps(context, text):
    """The scripted steps of the turn this message opens, and its byte record."""
    task = _task_for(text)
    if task is None:
        raise RuntimeError("The replay has no turn for: " + text[:120])
    _read_context(text)
    record = BYTES.setdefault(task["id"], {"plans": [], "calls": [], "scripts": [], "replies": 0, "corrections": [],
                                           "call_errors": []})
    return task, list(REPLAY["turns"].get(task["id"], ())), record


def _strict(plan, ordinary):
    """The plan as a Codex model writes it in the strict format.

    A kind the strict format does not spell out has every field of its generic form required, so a target the
    plan left out (a creating or scene-only kind) is written as null there, and counted.
    """
    operations = []
    for item in plan["operations"]:
        if item["tool"] == "angelo_scene_program":
            spelled = []
            for operation in item["arguments"].get("operations", ()):
                try:
                    encode({"reply": "", "operations": [{"tool": item["tool"], "arguments": dict(
                        item["arguments"], operations=[operation])}]}, ordinary)
                except ValueError:
                    operation = dict(operation, target=operation.get("target"))
                spelled.append(operation)
            item = dict(item, arguments=dict(item["arguments"], operations=spelled))
        operations.append(item)
    return encode(dict(plan, operations=operations), ordinary)


def _write_bytes():
    (OUT / "replay-bytes.json").write_text(json.dumps(BYTES, indent=1))


def _call(context, name, arguments):
    """One host call from the model's side, as ``exec`` or a plan operation reaches the host."""
    done, box = threading.Event(), {}
    context.request_tool("replay-" + uuid.uuid4().hex, name, arguments,
                         lambda result: (box.update(result=result), done.set()))
    if not done.wait(600):
        raise RuntimeError("Replayed call timed out: " + name)
    _learn(box["result"])
    try:
        # The provider's own path for an in-turn result: short identifiers for the model, and the host's record of
        # the revisions the result states (a later inferred binding in this turn uses them).
        tool_content(context, name, box["result"])
    except ValueError:
        pass  # A view for a model without images: the result itself stands.
    return box["result"]


class ReplayCodex(CodexProvider):
    """The real Codex provider with a scripted model: no app-server, no account."""

    def connect(self, catalog_only=False):
        self.thread_id = "plan-replay"
        self._scope.begin(self.controller, self.thread_id, starting=False)
        self.controller.state["toolset_version"] = TOOLSET_VERSION
        self.controller.emit("native_session", {"id": self.thread_id, "model": "plan-replay"})
        return self.thread_id

    def has_warm_runtime(self):
        return bool(self.thread_id)

    def stop(self):
        pass

    def stop_request(self, fence):
        pass

    def close(self):
        self.thread_id = None

    def _run_turn(self, context, native_id, text, turns, attachments):
        with self._scope.lock:
            self.thread_id = native_id
            self._scope.begin(context, native_id)
            self._turn_calls, self._turn_reply, self._plan = [], None, turns
            if turns is not None:
                turns.begin_turn()
            fence = self._scope.fence
        context.record_provider_turn()
        task = _task_for(text)
        if task is None:
            # A correction turn: the host sent a rejection or a stage failure. The replay scripted none.
            record = next(reversed(BYTES.values()))
            record["corrections"].append(text[:600])
            _write_bytes()
            final = {"reply": "The replay has no corrected plan for: " + text[:200], "operations": []}
        else:
            _, steps, record = _steps(context, text)
            final = {"reply": "", "operations": []}
            for step in steps:
                if "plan" in step:
                    final = _resolved(step["plan"])
                    continue
                call = step.get("call") or {"tool": "angelo_execute_python", "arguments": step["python"]}
                arguments = _resolved(call["arguments"])
                written = _dump(arguments)
                (record["scripts"] if call["tool"] == "angelo_execute_python" else record["calls"]).append(
                    {"tool": call["tool"], "bytes": len(written.encode())})
                result = _call(context, call["tool"], arguments)
                if isinstance(result, dict) and result.get("error"):  # A scripted call the host refused: no proof.
                    record["call_errors"].append({"tool": call["tool"], "error": str(result["error"])[:600]})
                    _write_bytes()
                resolve = getattr(context, "resolve_call", None)
                with self._scope.lock:
                    if isinstance(result, dict) and not set(result) <= {"error", "exact_schemas"}:
                        self._turn_calls.append(({"tool": call["tool"], "arguments": resolve(call["tool"], arguments)
                                                  if callable(resolve) else arguments}, result))
        final = dict(final, reply=final.get("reply", ""))
        final.pop("verify", None)
        message = _dump(_strict(final, codex_plan.ordinary_schema(getattr(context, "allowed_tools", None))))
        if task is not None:
            record["plans"].append({"bytes": len(message.encode()), "operations": len(final["operations"]),
                                    "program_operations": sum(len(item["arguments"].get("operations", ()))
                                                              for item in final["operations"]
                                                              if item["tool"] == "angelo_scene_program")})
            record["replies"] += len(final["reply"].encode())
            _write_bytes()
        self._turn_reply = message
        shown = turns.message_completed("plan-replay", message) if turns is not None else message
        context.emit("message_complete", {"item": "plan-replay-" + uuid.uuid4().hex[:8], "text": shown})
        self.turn_status = "completed"
        return "completed", fence


def _claude_rounds(steps):
    """A turn's steps as Claude plan rounds: a script is its own round, consecutive programs share one plan.

    A Claude turn edits only through plans, so the programs a Codex turn calls from ``exec`` one by one are
    one plan here (several programs in one round), run in order; lookups and views are the runtime's own.
    """
    rounds = []
    for step in steps:
        call = step.get("call")
        if "plan" in step or "python" in step:
            rounds.append(step)
        elif call and call["tool"] == "angelo_scene_program":
            if rounds and rounds[-1].get("programs"):
                rounds[-1]["plan"]["operations"].append(call)
            else:
                rounds.append({"programs": True, "plan": {"operations": [call], "verify": True}})
    return rounds


class ReplayClaude(ClaudeProvider):
    """The real Claude provider with a scripted model: no Claude Code process, no account."""

    def connect(self):
        self.controller.state["toolset_version"] = TOOLSET_VERSION
        self.controller.emit("native_session", {"id": "plan-replay", "model": "plan-replay"})
        return "plan-replay"

    def has_warm_runtime(self):
        return True

    def stop(self):
        pass

    def stop_request(self, fence):
        pass

    def close(self):
        pass

    def _turn(self, native_id, text, references=(), *, owner):
        context = owner.controller
        task = _task_for(text)
        if task is not None:
            _, steps, record = _steps(context, text)
            self._pending = _claude_rounds(steps)
            self._record = record
        else:
            _read_context(text)  # A later round: results and a fresh observation.
        _record_runtime_round(context, claude=True)
        if not getattr(self, "_pending", None):
            if task is None:
                self._record["corrections"].append(text[:600])
                _write_bytes()
            plan = {"reply": "The replay has no further plan.", "operations": []}
        else:
            step = self._pending.pop(0)
            plan = _resolved(step["plan"]) if "plan" in step else {
                "operations": [{"tool": "angelo_execute_python", "arguments": step["python"]}], "verify": True}
            written = _dump(plan)
            self._record["plans"].append({"bytes": len(written.encode()), "operations": len(plan["operations"]),
                                          "program_operations": sum(len(item["arguments"].get("operations", ()))
                                                                    for item in plan["operations"]
                                                                    if item["tool"] == "angelo_scene_program")})
            _write_bytes()
        plan = dict(plan, verify=plan.get("verify", False), reply=plan.get("reply", ""))
        self._emit(owner, "message_complete", {"item": "claude-assistant", "text": plan["reply"],
                                               "plan_operations": bool(plan["operations"])})
        return plan


controller_module.CodexProvider = ReplayCodex
controller_module.ClaudeProvider = ReplayClaude
ADDON = Path(angelo_agent.__file__).resolve().parent


def _own_addon():
    """Every loaded add-on module comes from this checkout (a mixed add-on would prove nothing)."""
    foreign = [name for name, module in list(sys.modules.items()) if name.startswith("angelo_agent.")
               and getattr(module, "__file__", None) and ADDON not in Path(module.__file__).resolve().parents]
    if foreign:
        raise RuntimeError("Add-on modules from another checkout: " + ", ".join(sorted(foreign)[:5]))


bpy.app.timers.register(lambda: _own_addon(), first_interval=3.)
ui._authentication.start = lambda *args, **kwargs: None  # No native sign-in status process.
runpy.run_path(str(HARNESS / "eval_live_agent_session.py"), run_name="scenario_plan_replay")
