# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``--ask-why-on-miss``: one follow-up question in the same chat after a miss, recorded as ``why.md``.

**When.** After the trial's last task is graded and, for a scenario or
one-shot set, after its hygiene report and the saved copy that is scored, but
before the Undo probe, which runs after the follow-up (``scenario_session``,
``bench_mcp_scenario.end_of_trial``): the agent is asked with the graded scene
in front of it. Only when a closed check failed. An aborted or rate-limited
trial is never asked: no further billed message. A trial its own cost or time
cap stopped is asked (its checks failed); the follow-up has its own time
limit (``WHY_TIMEOUT_S``) and its usage is recorded, outside the trial's.

**What.** The question names each failed check with the values the harness
measured (``failed_checks``) and asks, for each, three decision-level
questions a person would ask: which detail of the request or the reference
pictures led to the choice, whether the mismatch was noticed before the work
was finished, and why it was kept. It never asks the model to reproduce or
explain its thinking: that comes only from the thinking summaries
(``--debug-thinking``), the supported display. A question that
asked for a step-by-step account of the reasoning was refused by Opus 5.5's
safeguards (``reasoning_extraction``).

**Out of the results.** Angelo's chat is switched to Read only first (the
product refuses every mutation, Python included), the driver declines
whatever still asks and compares the scene digest before and after. The
question goes through the chat's own ``send``: the one-shot session's guard
covers the graded message and that session has finished. On the bench's MCP
side it goes to the same Claude Code process through the stream's own
``send`` and ``collect`` (not the one-shot wrappers, so the graded message's
count, cost watch and stop stay its own); its raw lines, tagged ``ask_why``,
go to ``ask-why-stream.jsonl`` and Angelo's transcript copy is split the same
way (``split_transcript``), so every reader of the graded stream or
transcript is unchanged. The client's MCP tools stay allowed, so any call it
makes is listed. The Codex MCP client's thread is not continued (``MCP_SIDE``).

**What the reply is.** The model's account after the fact: a lead to check
against its thinking summaries (``--debug-thinking``) and its actions, not
proof. A provider may refuse the question; the refusal is recorded as the
outcome and the question is never rephrased or retried.

Importable without Blender: ``bpy`` and the controller are passed in.
"""

import json
import os
import re
import time
from pathlib import Path

WHY_TIMEOUT_S = 300.0
STOP_GRACE_S = 30.0
WHY_FILE = "why.md"
ASK_WHY_TASK = "ask_why"   # The task id of the follow-up's lines in an MCP raw stream.
# Where the follow-up's turns are kept apart from the graded ones: the MCP side's raw stream, Angelo's transcript.
WHY_STREAM, WHY_TRANSCRIPT = "ask-why-stream.jsonl", "angelo-trial-%02d-why-transcript.jsonl"
MAX_CHECKS, MAX_MEASURED_CHARS = 12, 400
HEADER = "Diagnostic question from the test harness, after grading."
# Decision-level questions, as a person reviewing the result would ask them.
QUESTIONS = ("1. Which detail of the request or the reference pictures led you to this choice?\n"
             "2. Did you notice, before you finished, that it did not match what was asked?\n"
             "3. If you did, why did you keep it?")
MCP_SIDE = {"claude": "asked in the same Claude Code session",
            "codex": "not asked: the harness does not continue the Codex MCP client's thread"}
# Claude Code names a refusal's category in its error text: "Details: `[reasoning_extraction]`".
REFUSAL_DETAILS = re.compile(r"Details: `\[([a-z_]{1,64})\]`")


def failed_checks(tasks):
    """``[(task id, check label, measured values)]`` of every failed closed check in graded task records."""
    found = []
    for record in tasks or []:
        if (not isinstance(record, dict) or record.get("host_check_passed") is not False
                or record.get("status") in ("skipped", "invalid")):
            continue
        check = record.get("host_check") if isinstance(record.get("host_check"), dict) else {}
        entries = [entry for entry in check.get("results") or check.get("checks") or []
                   if isinstance(entry, dict) and entry.get("passed") is False]
        for entry in entries:   # Scenario checks carry a label and details; closed predicates a kind and target.
            label = entry.get("label") or " on ".join(str(entry[key]) for key in ("kind", "target") if entry.get(key))
            measured = (entry["details"] if "details" in entry else
                        {key: entry[key] for key in ("observed", "status", "reason") if key in entry})
            found.append((str(record.get("task")), label or "check", measured))
        if not entries:   # A task-level check: the scene had to stay unchanged, an answer, the smooth shading.
            keys = ("reason", "status", "scene_unchanged", "reply_missing", "answer", "smooth_shaded", "error")
            found.append((str(record.get("task")), "task check",
                          {key: check[key] for key in keys if key in check} or {"status": record.get("status")}))
    return found


def compact(value):
    text = json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= MAX_MEASURED_CHARS else text[:MAX_MEASURED_CHARS - 1] + "…"


def question(failures, read_only=True):
    """The follow-up's text: the failed checks with their measured values, then three questions about the decision.

    The questions are the ones a person would ask about a result: what in the request or the pictures led to the
    choice, whether the mismatch was noticed, and why it was kept. They never ask the model to reproduce or explain
    its thinking; the reasoning itself comes only from the thinking summaries (``--debug-thinking``).
    """
    shown = failures[:MAX_CHECKS]
    lines = [HEADER + " Your result was checked and saved; nothing you say or do now changes it"
             + (", and this chat is now read-only." if read_only else ".")
             + " Please answer without calling tools or changing anything.", "",
             "These checks of the result failed, with the values measured in it:"]
    lines += ["- %s: %s. Measured: %s" % (task, label, compact(measured)) for task, label, measured in shown]
    if len(failures) > len(shown):
        lines.append("- and %d more" % (len(failures) - len(shown)))
    lines += ["", "For each one:", QUESTIONS, "", "A short, direct answer is fine; there is no need to defend the result."]
    return "\n".join(lines)


def follow_up_question(text):
    """``--follow-up``: the run's own question after the same read-only preface, whatever the checks."""
    return "\n".join([HEADER + " Your result was checked and saved; nothing you say or do now changes it, and this chat "
                      "is now read-only. Please answer without calling tools or changing anything.", "", text.strip(), "",
                      "A short, direct answer is fine."])


def outcome(entry):
    if entry.get("reply"):
        return "answered"
    if entry.get("refusal"):
        return "refused by the provider (%s)" % entry["refusal"]
    return "stopped after %g s without a reply" % WHY_TIMEOUT_S if entry.get("stopped") else "no reply"


def refusal_in(texts):
    for text in texts:
        match = REFUSAL_DETAILS.search(str(text))
        if match:
            return match.group(1)
    return None


def render(entry):
    """``why.md``: the failed checks, the question, any thinking summaries, the reply and the facts."""
    lines = ["# Why: %s trial %s" % (entry["side"], entry.get("trial")), "",
             "> **Diagnostic follow-up** (`--ask-why-on-miss`): asked after the trial was graded and saved, never "
             "graded, and not a timing or benchmark claim. The reply is the model's account after the fact: a lead "
             "to check against its thinking summaries and its actions, not proof.", "", "## Failed checks", ""]
    lines += ["- %s: %s. Measured: `%s`" % (task, label, compact(measured)) for task, label, measured in entry["failures"]]
    lines += ["", "## Question", "", entry["question"], ""]
    if entry.get("thinking"):
        lines += ["## Thinking summaries (debug thinking)", "", "\n\n---\n\n".join(entry["thinking"]), ""]
    lines += ["## Reply", "", entry.get("reply") or "(%s)" % entry["outcome"], ""]
    if entry.get("errors"):
        lines += ["## Runtime messages", ""] + ["> " + str(text).replace("\n", "\n> ") for text in entry["errors"]] + [""]
    facts = {key: value for key, value in entry.items() if key not in ("failures", "question", "thinking", "reply", "errors")}
    return "\n".join(lines + ["## Facts", "", "```json", json.dumps(facts, indent=2, default=str), "```", ""])


def write(folder, entry):
    """``why.md`` in the trial's folder; the report's summary of it (no long texts)."""
    entry["outcome"] = outcome(entry)
    path = Path(folder) / WHY_FILE
    path.write_text(render(entry), encoding="utf-8")
    return {"asked": True, "outcome": entry["outcome"], "path": str(path), "checks": len(entry["failures"]),
            **{key: entry.get(key) for key in ("refusal", "tools", "declined", "scene_unchanged", "seconds", "usage")}}


# Angelo's side, in the app (eval_live_agent_session) ------------------------------------------

# Set only by the unbilled dry run (scenario_dry_run.py), where the scripted models pass every check: the
# follow-up is then asked anyway, so the in-app path runs in a real app without a model.
FORCE_ENV, DRY_RUN_ENV = "ANGELO_EVAL_ASK_WHY_FORCE", "ANGELO_SCENARIO_DRY_RUN"
FORCED = ("dry run", "every check passed; the dry run asks anyway to prove the path", {})


def due(report, controller, environ=None):
    """``(failures, reason)``: the failed checks to ask about, and why nothing is asked (None when it is)."""
    environ = os.environ if environ is None else environ
    failures = failed_checks(report.get("tasks"))
    if not failures and environ.get(FORCE_ENV) == "1" and environ.get(DRY_RUN_ENV) == "1":
        failures = [FORCED]
    asked = bool(environ.get("ANGELO_EVAL_FOLLOW_UP"))   # --follow-up: asked whatever the checks (never if aborted).
    reason = ("the trial was stopped: %s" % report["aborted"] if report.get("aborted")
              else "no chat" if controller is None else "no closed check failed" if not failures and not asked else None)
    return failures, reason


def finish(trial, bpy):
    """End the trial: first the Undo probe the scenario left for after the follow-up (``scenario_session``)."""
    if getattr(trial, "deferred_probe", False) and getattr(trial, "scenario", None) is not None:
        trial.scenario.undo_probe(bpy, trial.window)
    return trial.finish()


def after_trial(trial, bpy, environ=None):
    """With ``--ask-why-on-miss``: start the follow-up when one is due (``trial.phase = "why"``), else finish."""
    failures, reason = due(trial.report, trial.controller, environ)
    if reason:
        trial.report["ask_why"] = {"asked": False, "reason": reason}
        return finish(trial, bpy)
    trial.why, trial.phase = InAppAskWhy(trial, bpy, failures, environ), "why"
    return trial.why.send()


class InAppAskWhy:
    """Ask in the trial's chat in Read only, decline whatever asks, wait for the turn, record, then finish."""

    def __init__(self, trial, bpy, failures, environ=None):
        self.trial, self.bpy, self.failures = trial, bpy, failures
        environ = os.environ if environ is None else environ
        follow_up = environ.get("ANGELO_EVAL_FOLLOW_UP")
        self.controller = trial.controller
        self.question = follow_up_question(follow_up) if follow_up else question(failures, read_only=True)
        self.folder = Path(environ["ANGELO_QA_DIR"])
        self.declined, self.stopping = set(), False

    def digest(self):
        import scenario_hygiene
        return scenario_hygiene.state_digest(self.bpy, self.trial.window.scene)

    def send(self):
        from eval_receipts import receipt_marker
        controller = self.controller
        self.before = self.digest()
        controller.set_options(approval="READ_ONLY")   # Product-enforced: every mutation, Python included, refused.
        self.marker, self.first_row = receipt_marker(controller), len(controller.state["messages"])
        self.started = time.monotonic()
        self.deadline = self.started + WHY_TIMEOUT_S
        try:
            type(controller).send(controller, self.question)   # The chat's own send (see the module docstring).
        except (RuntimeError, ValueError) as error:   # A changed document or a busy chat: nothing was sent.
            self.trial.report["ask_why"] = {"asked": False, "reason": "the chat did not send it: %s" % error}
            return finish(self.trial, self.bpy)
        return .1

    def busy(self):
        worker = self.controller._worker
        return bool(self.controller.busy or (worker and worker.is_alive()))

    def tick(self):
        for call in list(self.controller.pending.values()):
            if call.approval and not call.executing and not call.authorized and call.id not in self.declined:
                self.declined.add(call.id)
                self.controller.decline_tool(call.id)
        if self.busy() and time.monotonic() < self.deadline:
            return .1
        if self.busy() and not self.stopping:
            self.controller.stop()
            self.stopping, self.deadline = True, time.monotonic() + STOP_GRACE_S
            return .1
        return self.record()

    def record(self):
        from eval_receipts import receipt_since
        rows = [row for row in self.controller.state["messages"][self.first_row:] if isinstance(row, dict)]
        texts = lambda *roles: [str(row.get("text") or "") for row in rows if row.get("role") in roles and row.get("text")]
        errors = texts("error", "system")
        entry = {"side": "angelo", "trial": self.trial.report.get("trial"), "failures": self.failures,
                 "question": self.question, "reply": "\n\n".join(texts("assistant")), "thinking": texts("thinking"),
                 "errors": errors, "refusal": refusal_in(errors),
                 "tools": sorted({row["tool"] for row in rows if row.get("role") == "activity" and row.get("tool")}),
                 "declined": len(self.declined), "stopped": self.stopping,
                 "seconds": round(time.monotonic() - self.started, 3), "scene_unchanged": self.digest() == self.before,
                 "approval": "READ_ONLY", "debug_thinking": bool(getattr(self.controller, "debug_thinking", False)),
                 "usage": (receipt_since(self.controller, self.marker) or {}).get("usage")}
        self.trial.report["ask_why"] = write(self.folder, entry)
        return finish(self.trial, self.bpy)


# The bench's MCP side (bench_mcp_trial.run_trial) -----------------------------------------------

def mcp_due(config, output, stream):
    """``(failures, reason)`` for the MCP side: as ``due``, and the Claude Code process must still run."""
    if not config.get("ask_why_on_miss"):
        return [], "--ask-why-on-miss is off"
    failures = failed_checks(output.get("tasks")) or ([FORCED] if config.get("ask_why_force") else [])
    running = stream is not None and stream.process is not None and stream.process.poll() is None
    return failures, ("the trial was stopped: %s" % output["aborted"] if output.get("aborted")
                      else "Claude Code is not running" if not running
                      else "no closed check failed" if not failures else None)


def ask_mcp(stream, usage, config, folder, output, client=None):
    """The MCP side's follow-up in the same Claude Code process, after every task was graded and saved.

    ``client`` (the add-on socket): the scene digest is compared before and after, as on Angelo's side; the
    client's MCP tools stay allowed, so a change is recorded, not prevented.
    """
    if not config.get("ask_why_on_miss"):
        return None
    failures, reason = mcp_due(config, output, stream)
    if reason:
        output["ask_why"] = {"asked": False, "reason": reason}
        return None
    digest = _scene_digest(client)
    text, own = question(failures, read_only=False), type(stream)   # The stream's own send and collect.
    # Its raw lines go to their own file: the graded stream, which the one-shot path metrics read whole, stays
    # the graded turns' alone.
    graded_raw, stream.raw = stream.raw, (Path(folder) / WHY_STREAM).open("w", encoding="utf-8")
    started = time.monotonic()
    try:
        own.send(stream, text)
        events, result, problem = own.collect(stream, started + WHY_TIMEOUT_S, ASK_WHY_TASK)
        if problem == "deadline":
            own.interrupt(stream)
            more, result, problem = own.collect(stream, time.monotonic() + STOP_GRACE_S, ASK_WHY_TASK)
            events += more
    finally:
        stream.raw.close()
        stream.raw = graded_raw
    after = _scene_digest(client)
    entry = dict(claude_events([message for _, message in events], result), side="mcp", trial=output.get("trial"),
                 failures=failures, question=text, stopped=problem == "deadline",
                 seconds=round(time.monotonic() - started, 3), debug_thinking=bool(config.get("debug_thinking")),
                 usage=usage.turn(result) if result else None,
                 scene_unchanged=None if None in (digest, after) else digest == after)
    output["ask_why"] = write(folder, entry)
    return output["ask_why"]


def _scene_digest(client):
    """The MCP session's scene digest through the add-on socket (``bench_mcp_grading.snapshot_code``), or None."""
    if client is None:
        return None
    try:
        import bench_mcp_grading
        return client.execute(bench_mcp_grading.snapshot_code()).get("digest")
    except (OSError, RuntimeError, ValueError):
        return None


def claude_events(messages, result):
    """Reply text, non-empty thinking, tool names, runtime errors and a refusal category from Claude Code lines."""
    reply, thinking, tools, errors, refusal = [], [], [], [], None
    for message in messages:
        if message.get("type") == "assistant":
            for block in (message.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and (message.get("message") or {}).get("model") != "<synthetic>":
                    reply.append(block.get("text", ""))
                elif block.get("type") == "thinking" and block.get("thinking"):
                    thinking.append(block["thinking"])
                elif block.get("type") == "tool_use":
                    tools.append(block.get("name"))
        elif message.get("type") == "system" and message.get("subtype") == "model_refusal_no_fallback":
            refusal = refusal or message.get("api_refusal_category")
    if isinstance(result, dict) and result.get("is_error"):
        errors.append(str(result.get("result") or result.get("subtype") or "error"))
    return {"reply": "\n\n".join(filter(None, reply)), "thinking": thinking, "tools": sorted(set(filter(None, tools))),
            "errors": errors, "refusal": refusal or refusal_in(errors)}


def split_transcript(source, graded, follow_up):
    """Copy a Claude Code session file: the graded turns to ``graded``, the follow-up from its question on.

    Readers of the transcript (steps, plan accounting, replay tools) then see only the graded turns.
    Returns whether a follow-up was found.
    """
    lines, marker = Path(source).read_bytes().splitlines(keepends=True), HEADER.encode()   # Byte for byte.
    cut = next((index for index, line in enumerate(lines)
                if marker in line and HEADER in _user_text(line.decode("utf-8", "replace"))), None)
    Path(graded).write_bytes(b"".join(lines if cut is None else lines[:cut]))
    if cut is not None:
        Path(follow_up).write_bytes(b"".join(lines[cut:]))
    return cut is not None


def _user_text(line):
    try:
        entry = json.loads(line)
    except ValueError:
        return ""
    if not isinstance(entry, dict) or entry.get("type") != "user":
        return ""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return "\n".join(str(block.get("text", "")) for block in content or [] if isinstance(block, dict))
