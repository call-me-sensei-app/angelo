# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Diagnostic runs of the live eval and the MCP benchmark: debug thinking and asking why after a miss.

Two opt-in flags, both off by default (doc/angelo/debug_mode.rst):

* ``--debug-thinking``: Claude's thinking summaries are shown and recorded.
  On Angelo's side the in-app driver sets ``Controller.debug_thinking``
  (``angelo_agent.debug_thinking``); the bench's MCP side gets the same
  Claude Code argument, so both sides run under the same display. The rows
  land in the chat, its session journal and the copied transcript.
* ``--ask-why-on-miss``: after a trial is graded and saved, one follow-up
  question in the same chat when a closed check failed (``eval_ask_why``).

On Codex, Angelo's chats always ask for reasoning summaries; ``--debug-thinking`` asks the bench's MCP side for the
same and keeps Angelo's rollouts with each trial (``bench_mcp_vs_angelo.copy_codex_rollouts``).

A run with either flag is a diagnostic run, never a timing or benchmark claim:
showing thinking and the extra question change what a run costs and how long
it takes, and neither was measured for that. The run plan, each trial report
and every summary, scorecard and transcript the harness writes say so
(``mark_outputs``), and the gate and measured-results tools refuse such runs
(``claim_refusal``). Nothing here reads a credential. Standard library only.
"""

import json
import os
from pathlib import Path
import eval_send_record as send_record  # The trial's record: an end write, never one written at a send.

DEBUG_ENV, ASK_ENV, FOLLOW_ENV = "ANGELO_EVAL_DEBUG_THINKING", "ANGELO_EVAL_ASK_WHY_ON_MISS", "ANGELO_EVAL_FOLLOW_UP"
FLAGS = ("debug_thinking", "ask_why_on_miss", "follow_up")   # follow_up carries its question as its value.
NOT_A_CLAIM = ("Diagnostic run (%s): not a timing or benchmark claim. Its timings, costs and pass rates are for "
               "diagnosis only; the gate and measured-results tools refuse it.")
REFUSED = "%s is a diagnostic run (%s): it is never a gate, timing or benchmark figure"
# A first line the harness puts in the transcripts it copies or writes; readers skip types they do not know.
TRANSCRIPT_MARK = "angelo-diagnostic-run"
# Run-level human reports that get the banner, and machine-readable scorecards that get the stamp.
REPORTS = ("SUMMARY.md", "ONESHOT-REPORT.md", "SCENARIO-REPORT.md")
SCORECARDS = ("oneshot-scorecard.json", "scenario-scorecard.json")
PAGES = ("oneshot-report.html", "scenario-report.html")


def add_arguments(parser):
    parser.add_argument("--debug-thinking", action="store_true",
                        help="diagnostic run: show and record Claude's thinking summaries (Claude Code "
                             "--thinking-display summarized; both sides of the bench). Never for timed or "
                             "benchmark-claim runs; the results are marked and the gate tools refuse them")
    parser.add_argument("--ask-why-on-miss", action="store_true",
                        help="diagnostic run: after a trial is graded and saved, when a closed check failed, send one "
                             "follow-up asking the agent why, in the same chat; the reply is recorded as why.md")
    parser.add_argument("--follow-up", default="", metavar="QUESTION",
                        help="diagnostic run: after a trial is graded and saved, send this one question in the same "
                             "chat (read-only) whatever its checks, e.g. why the agent did or did not use something; "
                             "recorded as why.md, as --ask-why-on-miss")

def check(parser, args):
    """Nothing to refuse: ``--debug-thinking`` works on both runtimes.

    Claude: both sides launch with the summarized thinking display. Codex: Angelo's chats already ask for reasoning
    summaries ("auto", ``providers``), and the MCP side's Codex then asks for the same
    (``bench_mcp_codex.reasoning_summary``); Angelo's rollouts are kept with each trial.
    """
    return args


def stamp(args):
    """``{"debug_thinking": ..., "ask_why_on_miss": ...}`` with only the flags that are on ({} when none)."""
    return {name: (getattr(args, name) if name == "follow_up" else True) for name in FLAGS if getattr(args, name, False)}


def arguments(flags):
    """The live eval's command-line flags for a stamp (the bench passes its own on to each Angelo trial)."""
    return [item for name in FLAGS if flags.get(name)
            for item in (["--follow-up", str(flags[name])] if name == "follow_up" else ["--" + name.replace("_", "-")])]


def only_known(flags):
    """A stamp's own keys from a plan entry, a scorecard or a report (drops ``claim`` and anything else)."""
    return {name: (flags[name] if name == "follow_up" else True) for name in FLAGS if (flags or {}).get(name)}


def plan(args, mcp_side=None):
    """The run plan's part: nothing for an ordinary run, so its plan (and ``--resume``) is unchanged.

    ``mcp_side``: the bench says whether its MCP side is asked too (a Claude Code session it can continue).
    """
    flags = stamp(args)
    if not flags:
        return {}
    entry = dict(flags, claim=NOT_A_CLAIM % describe(flags))
    if flags.get("ask_why_on_miss") and mcp_side is not None:
        entry["mcp_ask_why"] = mcp_side
    return {"diagnostics": entry}


def describe(flags):
    names = {"debug_thinking": "debug thinking", "ask_why_on_miss": "ask why on miss", "follow_up": "a follow-up question"}
    return " + ".join(names[name] for name in FLAGS if flags.get(name))


def environment(args):
    """The trial's variables for the in-app driver; empty values when off."""
    return {DEBUG_ENV: "1" if getattr(args, "debug_thinking", False) else "",
            ASK_ENV: "1" if getattr(args, "ask_why_on_miss", False) else "",
            FOLLOW_ENV: getattr(args, "follow_up", "") or ""}


def claude_arguments(config):
    """The bench's MCP side: the Claude Code argument Angelo's side gets in debug mode (``angelo_agent.debug_thinking``)."""
    from angelo_agent import debug_thinking
    return ["--thinking-display", debug_thinking.DISPLAY] if config.get("debug_thinking") else []


def extra_seconds(args):
    """What a trial process may need beyond its tasks: one follow-up turn and its stop grace."""
    if not (getattr(args, "ask_why_on_miss", False) or getattr(args, "follow_up", "")):
        return 0
    import eval_ask_why
    return eval_ask_why.WHY_TIMEOUT_S + eval_ask_why.STOP_GRACE_S + 30


# In the app (eval_live_agent_session) -----------------------------------------------------------

def in_app_flags(environ=None):
    environ = os.environ if environ is None else environ
    flags = {name: True for name, variable in (("debug_thinking", DEBUG_ENV), ("ask_why_on_miss", ASK_ENV))
             if environ.get(variable) == "1"}
    return dict(flags, **({"follow_up": environ[FOLLOW_ENV]} if environ.get(FOLLOW_ENV) else {}))


def configure(controller, environ=None):
    """Before the chat's first launch: debug mode on the controller; the trial report's stamp ({} when off).

    On a Codex chat ``debug_thinking`` changes no launch (its chats always ask for reasoning summaries); the stamp
    still marks the run diagnostic."""
    flags = in_app_flags(environ)
    controller.debug_thinking = bool(flags.get("debug_thinking"))
    return {"diagnostics": dict(flags, claim=NOT_A_CLAIM % describe(flags))} if flags else {}


def asks_why(trial, environ=None):
    """Whether the driver's end will ask (``--ask-why-on-miss`` and a follow-up is due): a scenario's Undo probe
    then waits until after it (``scenario_session.finish``), so the agent is asked about the graded scene."""
    flags = in_app_flags(environ)
    if not (flags.get("ask_why_on_miss") or flags.get("follow_up")):
        return False
    import eval_ask_why
    trial.deferred_probe = eval_ask_why.due(trial.report, trial.controller, environ)[1] is None
    return trial.deferred_probe


def after_trial(trial, bpy, environ=None):
    """The driver's end, after every grade and save: the ask-why follow-up when one is due, else ``trial.finish()``."""
    flags = in_app_flags(environ)
    if not (flags.get("ask_why_on_miss") or flags.get("follow_up")):
        return trial.finish()
    import eval_ask_why
    return eval_ask_why.after_trial(trial, bpy, environ)


# Marking what a diagnostic run wrote ------------------------------------------------------------

def run_stamp(data):
    """The diagnostics a run's results state (bench JSON, live-eval JSON, a plan or a trial report), or None."""
    if not isinstance(data, dict):
        return None
    for holder in (data, data.get("plan"), (data.get("meta") or {}).get("plan")):
        if isinstance(holder, dict) and isinstance(holder.get("diagnostics"), dict):
            return holder["diagnostics"]
    for trial in data.get("trials") or []:
        if isinstance(trial, dict) and isinstance(trial.get("diagnostics"), dict):
            return trial["diagnostics"]
    return None


class DiagnosticRun(ValueError):
    """A run folder was a diagnostic run: no option makes it a gate or measured figure."""


def refuse_claims(directories, angelo_only=False):
    """Raise ``DiagnosticRun`` naming every diagnostic run among ``directories`` (the gate tools' first check).

    ``angelo_only``: the caller reads only Angelo's own samples (``measured_results``), so a cross-model reuse,
    which changes only the MCP side it is compared with, is no reason to refuse.
    """
    refusals = [reason for reason in (claim_refusal(directory, angelo_only) for directory in directories) if reason]
    if refusals:
        raise DiagnosticRun("; ".join(refusals))


CROSS_MODEL = ("%s pairs Angelo trials with reused MCP trials of another model or effort (--cross-model-reuse): "
               "a tier measurement, never a gate, timing or benchmark figure")


def cross_model(data):
    """The ``cross_model_reuse`` entry a run's results or plan state, or None."""
    if not isinstance(data, dict):
        return None
    for holder in (data, data.get("plan"), (data.get("meta") or {}).get("plan")):
        if isinstance(holder, dict) and holder.get("cross_model_reuse"):
            return holder["cross_model_reuse"]
    return None


def claim_refusal(directory, angelo_only=False):
    """Why a run folder may not be used as a gate or measured figure, or None (gate tools call this).

    A diagnostic run, a run that reused MCP trials of another model or effort, and a one-shot group whose members
    include either are refused.
    """
    for name in ("bench-mcp-vs-angelo.json", send_record.NAME, "RUN-PLAN.json", "oneshot-group.json"):
        path = Path(directory) / name
        try:
            data = ((send_record.read(path) if name == send_record.NAME else json.loads(path.read_text()))
                    if path.is_file() else None)   # A trial record written at a send reads as none.
        except (OSError, ValueError):
            data = None
        found = run_stamp(data)
        if found:
            return REFUSED % (Path(directory).name, describe(found))
        if cross_model(data) and not angelo_only:
            return CROSS_MODEL % Path(directory).name
    return None


def banner(flags, why_files=()):
    lines = ["> **%s**" % (NOT_A_CLAIM % describe(flags))]
    if why_files:
        lines.append("> Ask-why replies: " + ", ".join("`%s`" % name for name in why_files) + ".")
    return "\n".join(lines) + "\n\n"


def transcript_mark(flags, stream=False):
    """The first line of a marked transcript; ``stream``: in the MCP raw stream's own line shape."""
    mark = {"type": TRANSCRIPT_MARK, "diagnostics": dict(flags), "note": NOT_A_CLAIM % describe(flags)}
    return json.dumps({"t_s": 0.0, "task": "diagnostics", "message": mark} if stream else mark)


def mark_transcript(path, flags, stream=False):
    """Put the diagnostic mark first in a transcript or raw stream the harness kept (no-op when off or marked).

    Every reader of these files skips a line type it does not know, and a raw stream's readers select lines by
    task, which the mark's ``diagnostics`` task never matches.
    """
    path = Path(path)
    if not flags or not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if TRANSCRIPT_MARK not in text.split("\n", 1)[0]:
        path.write_text(transcript_mark(flags, stream) + "\n" + text, encoding="utf-8")


def recorded(output):
    """The diagnostics an earlier run's plan states (``RUN-PLAN.json``), so ``--summarize-only`` keeps its marks."""
    try:
        return run_stamp(json.loads((Path(output) / "RUN-PLAN.json").read_text())) or {}
    except (OSError, ValueError):
        return {}


def why_files(output):
    """The run's ask-why replies, relative to the run folder."""
    output = Path(output)
    return sorted(str(path.relative_to(output)) for pattern in ("*-trial-*/why.md", "*-trial-*/trial-*/qa/why.md")
                  for path in output.glob(pattern))


def mark_outputs(output, flags):
    """The banner on every run-level report, the stamp on every scorecard and page the run wrote.

    ``flags``: the plan's ``diagnostics`` entry (``plan``); nothing is touched when it is empty. Returns the
    names it marked.
    """
    flags = only_known(flags)
    if not flags:
        return []
    output, marked, top = Path(output), [], banner(flags, why_files(output))
    for name in REPORTS:
        path = output / name
        if path.is_file() and not path.read_text(encoding="utf-8").startswith(top[:40]):
            path.write_text(top + path.read_text(encoding="utf-8"), encoding="utf-8")
            marked.append(name)
    for name in SCORECARDS:
        path = output / name
        if path.is_file():
            card = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(card, dict):
                card["diagnostics"] = dict(flags, claim=NOT_A_CLAIM % describe(flags), why=why_files(output))
                path.write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
                marked.append(name)
    for name in PAGES:
        path = output / name
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if "<body" in text and TRANSCRIPT_MARK not in text:
            head, rest = text.split("<body", 1)
            tag, body = rest.split(">", 1)
            note = ('<p class="%s" style="padding:8px 12px;border:2px solid #b45309;font-weight:600">%s</p>'
                    % (TRANSCRIPT_MARK, NOT_A_CLAIM % describe(flags)))
            path.write_text(head + "<body" + tag + ">" + note + body, encoding="utf-8")
            marked.append(name)
    return marked
