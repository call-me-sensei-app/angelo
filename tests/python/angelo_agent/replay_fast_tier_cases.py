# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Replay the recorded fast-tier failure patterns through today's host code (unbilled, offline).

For every trial of a ``bench_mcp_vs_angelo.py`` run (the Angelo chat record
``angelo-trial-NN/trial-01/state/sessions/<chat>.json`` and Claude Code's
transcript ``angelo-trial-NN-transcript.jsonl``), it finds the patterns
doc/angelo/fast_tier_reliability.rst names and asks today's code what it does
with the recorded data:

* ``named_deltas``: every observation delta a request naming an object
  received, rebuilt through today's ledger (``context_ledger.TurnBlocks``, a
  fresh identifier table per transcript): recorded deltas that did not list
  the named object, and rebuilt ones that still do not.
* ``lookup_only``: plans of lookups only with ``verify`` left out, and whether
  today's provider gives them the next round (``providers._lookups_only``).
* ``superseded``: turns with more than one ``StructuredOutput`` call whose last
  call drops operations (``providers.superseded_plans``).
* ``stage_failures``: programs that failed on their isolated stage, and whether
  today's provider returns them once for a corrected plan
  (``claude_outcomes.correctable_failure``).
* ``rejections``: plans the host rejected before running, re-validated with
  today's validators (identifiers restored to long forms).

Only what the recording holds is replayed: a model's next plan is never
invented, so a count says what the host now does, not what the model would
have written. Usage, from the repository root::

    python3 tests/python/angelo_agent/replay_fast_tier_cases.py RUN_DIR [RUN_DIR ...] [--json OUT]
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _path in (str(ROOT / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import replay_turn_context as replay  # noqa: E402
from angelo_agent import context_ledger, session_journal  # noqa: E402
from angelo_agent.claude_outcomes import correctable_failure  # noqa: E402
from angelo_agent.identifiers import IdentifierTable  # noqa: E402
from angelo_agent.providers import _lookups_only, superseded_plans  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

NAMED = re.compile(r"'([^']{1,64})'")
REJECTED = "Host rejected the plan before running any operation"
PROGRAM_TOOLS = ("angelo_scene_program", "angelo_apply_recipe")


def transcript_turns(path):
    """Per user message (round 0 only): its text and the StructuredOutput calls of its turn the runtime accepted."""
    turns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        message = record.get("message") or {}
        if record.get("type") == "user" and isinstance(message.get("content"), str):
            later = turns and message["content"].startswith(replay.LATER_ROUND_PREFIXES)
            if not later:
                turns.append({"sent": message["content"], "calls": [], "rounds": 1})
            else:
                turns[-1]["rounds"] += 1
        elif record.get("type") == "assistant" and turns:
            for item in message.get("content") or ():
                if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") == "StructuredOutput":
                    turns[-1]["calls"].append((turns[-1]["rounds"], item.get("input"), item.get("id")))
        elif record.get("type") == "user" and turns and isinstance(message.get("content"), list):
            rejected = {item.get("tool_use_id") for item in message["content"]
                        if isinstance(item, dict) and item.get("type") == "tool_result" and item.get("is_error")}
            turns[-1]["calls"] = [call for call in turns[-1]["calls"] if call[2] not in rejected]
    for turn in turns:
        turn["calls"] = [(number, value) for number, value, _ in turn["calls"]]  # Accepted calls only.
    return turns


def named_deltas(turns, maps):
    """Observation blocks of requests naming an object: recorded vs rebuilt (see the module docstring)."""
    table, received, observed = IdentifierTable("replay"), None, None
    report = {"requests_naming_an_object": 0, "deltas": 0, "recorded_without_named": 0, "rebuilt_without_named": 0,
              "examples": []}
    for turn in turns:
        request, recorded, _, _ = replay.parse_sent(turn["sent"])
        recorded = replay.restore_long_forms(recorded, maps)
        whole_recorded = replay.OBSERVATION in turn["sent"]
        observation = observed = replay.full_observation(observed, recorded)
        if observation is None:
            continue
        blocks = context_ledger.TurnBlocks(received, identifiers=table)
        text = blocks.observation(observation)
        received = dict(blocks.after)
        names = set(NAMED.findall(request.split("\n", 1)[0])) & {
            entry.get("name") for entry in observation.get("objects", ()) if isinstance(entry, dict)}
        if not names:
            continue
        report["requests_naming_an_object"] += 1
        if whole_recorded or text.startswith(context_ledger.OBSERVATION_HEADER):
            continue  # A whole observation lists every object it covers.
        report["deltas"] += 1
        by_handle = {entry.get("handle"): entry.get("name") for entry in observation.get("objects", ())
                     if isinstance(entry, dict)}
        recorded_listed = {by_handle.get(entry.get("handle")) for entry in (recorded or {}).get("objects", ())
                           if isinstance(entry, dict)}
        sent = (json.loads(text[len(context_ledger.OBSERVATION_DELTA_HEADER):])
                if text.startswith(context_ledger.OBSERVATION_DELTA_HEADER) else {"objects": []})
        rebuilt_listed = {entry.get("name") for entry in sent.get("objects", ()) if isinstance(entry, dict)}
        if not names & recorded_listed:
            report["recorded_without_named"] += 1
            if not names & rebuilt_listed:
                report["rebuilt_without_named"] += 1
            if len(report["examples"]) < 3:
                report["examples"].append({"request": request.split("\n", 1)[0][:70],
                                           "rebuilt_lists_whole": sorted(names & rebuilt_listed)})
    return report


def plan_patterns(turns):
    """Lookup-only plans with verify left out, and turns with superseded StructuredOutput calls."""
    lookup = {"plans": 0, "next_round_now": 0}
    superseded = {"turns": 0, "flagged_now": 0, "examples": []}
    for turn in turns:
        plans = [call for _, call in turn["calls"] if isinstance(call, dict)]
        for plan in plans:
            operations = plan.get("operations")
            if isinstance(operations, list) and operations and not plan.get("verify") and all(
                    isinstance(op, dict) and op.get("tool") != "angelo_scene_verify" for op in operations):
                if _lookups_only(operations):
                    lookup["plans"] += 1
                    lookup["next_round_now"] += 1  # run_request forces verify on exactly these.
        by_round = {}
        for number, call in turn["calls"]:
            by_round.setdefault(number, []).append(call)
        for calls in by_round.values():
            if len(calls) > 1 and isinstance(calls[-1], dict) and isinstance(calls[-1].get("operations"), list):
                final = dict(calls[-1], operations=calls[-1]["operations"])
                earlier_operations = [c for c in calls[:-1] if isinstance(c, dict) and c.get("operations")]
                if not earlier_operations:
                    continue
                superseded["turns"] += 1
                if superseded_plans(calls, final):
                    superseded["flagged_now"] += 1
                    if len(superseded["examples"]) < 3:
                        superseded["examples"].append(turn["sent"].split("\n", 1)[0][:70])
    return lookup, superseded


def session_patterns(session, maps):
    """Stage failures and host-rejected plans from the chat record."""
    rows = session.get("messages") or []
    stages = {"failures": 0, "correctable_now": 0, "examples": []}
    rejections = {"plans": 0, "accepted_now": 0, "still_rejected": []}
    for index, row in enumerate(rows):
        if (row.get("role") == "activity" and row.get("tool") in PROGRAM_TOOLS and row.get("status") == "failed"
                and isinstance(row.get("result"), dict) and "content" not in row["result"]):
            # ("content" alone: the runtime refused a direct tool call; the host never saw it.)
            stages["failures"] += 1
            if correctable_failure([{"tool": row["tool"], "arguments": row.get("arguments") or {}}], [row["result"]]):
                stages["correctable_now"] += 1
                if len(stages["examples"]) < 3:
                    stages["examples"].append(str(row["result"].get("error", ""))[:120])
        if row.get("role") == "system" and str(row.get("text", "")).startswith(REJECTED):
            plan = next((r.get("arguments") for r in reversed(rows[:index])
                         if r.get("role") == "activity" and r.get("tool") == "StructuredOutput"), None)
            for operation in (plan or {}).get("operations") or ():
                if not isinstance(operation, dict) or not str(operation.get("tool", "")).startswith("angelo_"):
                    continue
                rejections["plans"] += 1
                try:
                    validate_call(operation["tool"], replay.restore_long_forms(operation.get("arguments") or {}, maps))
                    rejections["accepted_now"] += 1
                except (ValueError, KeyError, TypeError) as error:
                    if len(rejections["still_rejected"]) < 3:
                        rejections["still_rejected"].append(str(error)[:140])
    return stages, rejections


def replay_run(directory):
    trials = []
    for transcript, session_path in replay.find_trials(Path(directory)):
        session = session_journal.read(session_path)[0]  # Its base and every record (sessions.read_transcript).
        maps = replay.long_form_maps(session)
        turns = transcript_turns(transcript)
        lookup, superseded = plan_patterns(turns)
        stages, rejections = session_patterns(session, maps)
        trials.append({"trial": transcript.name, "transcript_requests": len(turns),
                       "named_deltas": named_deltas(turns, maps), "lookup_only": lookup, "superseded": superseded,
                       "stage_failures": stages, "rejections": rejections})
    return {"run": str(directory), "trials": trials}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay recorded fast-tier failure patterns through today's host")
    parser.add_argument("directories", nargs="+")
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)
    reports = [replay_run(directory) for directory in args.directories]
    for report in reports:
        print(report["run"])
        for trial in report["trials"]:
            named, lookup, superseded = trial["named_deltas"], trial["lookup_only"], trial["superseded"]
            stages, rejections = trial["stage_failures"], trial["rejections"]
            print("  %s: %d requests; named-object deltas without the object: recorded %d -> today %d; "
                  "lookup-only plans %d (next round now %d); superseded turns %d (flagged now %d); "
                  "stage failures %d (corrected plan now %d); rejected plans %d (accepted now %d)" % (
                      trial["trial"], trial["transcript_requests"], named["recorded_without_named"],
                      named["rebuilt_without_named"], lookup["plans"], lookup["next_round_now"], superseded["turns"],
                      superseded["flagged_now"], stages["failures"], stages["correctable_now"], rejections["plans"],
                      rejections["accepted_now"]))
    if args.json:
        Path(args.json).write_text(json.dumps(reports, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
