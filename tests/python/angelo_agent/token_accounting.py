# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled per-component token accounting of recorded Claude trials, before and after.

No provider call, Blender process or network. For a ``bench_mcp_vs_angelo.py``
(or ``eval_live_agent.py``) run directory it reports, per trial and turn:

* output: the plan the model emitted, split into identifiers, structure and prose;
* turn suffix: what the call wrote to the prompt cache (the previous plan plus
  the new user message), split the same way.

Method, stated in the output:

* Tokenizer approximation. Text is split into three byte classes: hex runs (8
  or more characters of 0-9a-f mixing digits and letters, or 32 or more), ASCII
  letters, and every other byte. Per-class token rates are fitted by least
  squares to the recorded run itself: every call's ``output_tokens`` (minus
  thinking tokens) against its plan, and every later call's
  ``cache_creation_input_tokens`` against the previous plan plus the new
  message, with one constant each for the tool-call framing and the per-turn
  wrapper. Residuals are printed. This is an approximation of Claude's
  tokenizer, not the tokenizer.
* Components. Identifiers are scene handles (``scn:...``, ``o<n>``), revisions
  and other hex digests, UUIDs and observation/page tokens. Prose is
  natural-language text: lines that are not JSON, and JSON strings of four or
  more words. Structure is everything else (keys, punctuation, numbers, enums,
  names).
* Before: the recorded messages and plans. After: each message rebuilt by
  ``replay_turn_context.py`` through today's composition, and each recorded
  plan rewritten deterministically to today's contract (short handles and
  revisions from the replayed context's own table, a read_set entry that
  repeats the write_set dropped, ``verify: false`` omitted). The model's reply
  text and choices are kept as recorded; ``--reply-words N`` additionally caps
  edit replies at N words to estimate the reply guidance (an assumption).

Estimates for a billed rerun to confirm. Usage, from the repository root::

    python3 tests/python/angelo_agent/token_accounting.py DIR [--json OUT] [--reply-words 15]
"""

import argparse
import copy
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import replay_turn_context as replay  # noqa: E402

# USD per million tokens. Opus: Claude Opus 5.5's list prices (1-hour cache
# writes). Haiku: implied by the haiku-5task run's cost_usd records (least
# squares over its calls), for relative estimates only.
PRICE_TABLES = {"opus": {"input": 4.0, "cache_read": 0.20, "cache_write": 8.0, "output": 20.0},
                "haiku": {"input": 0.1, "cache_read": 0.01, "cache_write": 0.2, "output": 0.5}}
PRICES = dict(PRICE_TABLES["opus"])
HEAD_TO_HEAD_TASKS = 5  # The sixth recorded task (staged preview) is Angelo-only.
# ``features`` of the cached prefix's Angelo parts by the recording's toolset
# (18, 19 or 20): appended instructions and the typed-plan schema. Every call re-reads today's
# difference from the cache.
RECORDED_PREFIX_FEATURES = {18: {"instructions": [0, 7231, 1644], "plan_schema": [0, 24309, 15210]},
                            19: {"instructions": [0, 8023, 1853], "plan_schema": [0, 24073, 14958]},
                            20: {"instructions": [0, 8409, 1948], "plan_schema": [0, 24005, 14930]}}
_HEX = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{8,64}(?![0-9A-Za-z])")
_IDENTIFIER = re.compile(
    r"scn:[0-9a-f]{32}:[0-9]+|(?:obs|page|dat):[0-9A-Za-z_:]+"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|(?<=\")o[1-9][0-9]{0,8}(?=\")|(?<![0-9A-Za-z])[0-9a-f]{8,64}(?![0-9A-Za-z])")
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def _is_hex_identifier(run):
    return len(run) >= 32 or (re.search("[0-9]", run) is not None and re.search("[a-f]", run) is not None)


def features(text):
    """``[hex, letters, other]`` byte counts of ``text``."""
    hex_chars = sum(len(run) for run in _HEX.findall(text) if _is_hex_identifier(run))
    rest = _HEX.sub(lambda match: "" if _is_hex_identifier(match.group(0)) else match.group(0), text)
    letters = sum(character.isascii() and character.isalpha() for character in rest)
    return [hex_chars, letters, len(rest.encode("utf-8")) - letters]


def _solve(rows, values):
    """Least squares by the normal equations (a handful of columns)."""
    size = len(rows[0])
    matrix = [[sum(row[i] * row[j] for row in rows) for j in range(size)]
              + [sum(row[i] * value for row, value in zip(rows, values))] for i in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(matrix[index][column]))
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        for index in range(size):
            if index != column:
                factor = matrix[index][column] / matrix[column][column]
                matrix[index] = [a - factor * b for a, b in zip(matrix[index], matrix[column])]
    return [matrix[index][size] / matrix[index][index] for index in range(size)]


class Tokenizer:
    """Shared per-class token rates with separate output and cache-write constants."""

    def __init__(self, outputs, writes):
        rows = [[1, 0] + features(text) for text, _ in outputs] + [[0, 1] + features(text) for text, _ in writes]
        values = [tokens for _, tokens in outputs] + [tokens for _, tokens in writes]
        self.output_constant, self.write_constant, *self.rates = _solve(rows, values)
        self.residuals = {name: self._rms(pairs, constant) for name, pairs, constant in
                          (("output", outputs, self.output_constant), ("cache_write", writes, self.write_constant))}

    def _rms(self, pairs, constant):
        errors = [tokens - constant - self.tokens(text) for text, tokens in pairs]
        return {"rms": (sum(e * e for e in errors) / len(errors)) ** .5, "max": max(map(abs, errors)),
                "mean_tokens": sum(tokens for _, tokens in pairs) / len(pairs)}

    def tokens(self, text):
        return sum(rate * count for rate, count in zip(self.rates, features(text)))


def components(text):
    """``{"identifiers", "structure", "prose"}`` substrings of a message or plan."""
    identifiers = "".join(match.group(0) for match in _IDENTIFIER.finditer(text)
                          if not _HEX.fullmatch(match.group(0)) or _is_hex_identifier(match.group(0)))
    rest = _IDENTIFIER.sub(lambda m: "" if (not _HEX.fullmatch(m.group(0)) or _is_hex_identifier(m.group(0)))
                           else m.group(0), text)
    prose, structure = [], []
    for line in rest.splitlines(keepends=True):
        stripped = line.strip()
        json_like = stripped[:1] in "{[" or re.match(r"^[a-z_]+=[\[{\"0-9tfn]", stripped) is not None
        if not json_like:
            prose.append(line)
            continue
        last = 0
        for match in _STRING.finditer(line):
            if match.group(0).count(" ") >= 3:
                structure.append(line[last:match.start()])
                prose.append(match.group(0))
                last = match.end()
        structure.append(line[last:])
    return {"identifiers": identifiers, "structure": "".join(structure), "prose": "".join(prose)}


def split_tokens(tokenizer, text):
    return {name: round(tokenizer.tokens(part), 1) for name, part in components(text).items()}


# Plan rewriting ----------------------------------------------------------------

_LOOKUPS = frozenset({"angelo_scene_verify", "angelo_scene_object"})
_EDIT_TOOLS = frozenset({"angelo_scene_program", "angelo_apply_recipe"})


def _observed(table, handle, revision):
    """Whether today's host would infer this binding: the table's latest view shows it."""
    return table is not None and hasattr(table, "observed") and table.observed(handle) == revision


def rewrite_plan(plan, table, reply_words=0, *, omit_edit_reply=False, infer=False):
    """The recorded plan as today's contract lets the model write it.

    ``table`` is the replayed context's identifier table when the plan was
    written. ``infer`` also drops the bindings the host infers from the
    latest observation (toolset 20); ``omit_edit_reply`` drops an edit's reply.
    """
    if not isinstance(plan, dict):
        return plan  # A round that answered in text without a plan.
    long_form = copy.deepcopy(plan)
    plan = copy.deepcopy(plan)
    operations = []
    for operation, original in zip(plan.get("operations", []), long_form.get("operations", [])):
        arguments = table.compact(operation.get("arguments", {})) if table is not None else operation.get("arguments", {})
        source = original.get("arguments", {})
        tool = operation.get("tool")
        if tool == "angelo_scene_program":
            written = {entry["handle"] for entry in arguments.get("write_set", [])}
            arguments["read_set"] = [entry for entry in arguments.get("read_set", []) if entry["handle"] not in written]
            if infer:
                for key in ("read_set", "write_set"):
                    arguments[key] = [entry for entry, long_entry in zip(arguments.get(key, []), [
                        entry for entry in source.get(key, []) if key == "write_set" or entry["handle"] not in
                        {item["handle"] for item in source.get("write_set", [])}])
                        if not _observed(table, long_entry["handle"], long_entry["revision"])]
            for key in ("read_set", "write_set", "expected_checks"):
                if arguments.get(key) == []:
                    del arguments[key]
        elif infer and tool in ("angelo_apply_recipe", "angelo_expand_recipe") and "revision" in arguments:
            if _observed(table, source.get("target"), source.get("revision")):
                del arguments["revision"]
        elif infer and tool == "angelo_scene_verify":
            if all(_observed(table, entry["handle"], entry["revision"]) for entry in source.get("read_set", [])):
                arguments.pop("read_set", None)
        operations.append(dict(operation, arguments=arguments))
    plan["operations"] = operations
    if plan.get("verify") is False:
        del plan["verify"]
    edit = any(operation.get("tool") in _EDIT_TOOLS for operation in operations)
    if omit_edit_reply and edit:
        plan.pop("reply", None)
    elif reply_words and operations:
        words = plan["reply"].split()
        if len(words) > reply_words:
            plan["reply"] = " ".join(words[:reply_words])
    return plan


def plan_text(plan, text_block=""):
    return ("" if plan is None else json.dumps(plan, ensure_ascii=False, separators=(",", ":"))) + text_block


# Recorded data -----------------------------------------------------------------

def recorded_turns(transcript):
    """Per model call: message, plan, any text block, usage and its round in the turn."""
    calls = []
    for line in transcript.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("type") == "user" and isinstance(record["message"].get("content"), str):
            text = record["message"]["content"]
            later = calls and text.startswith(replay.LATER_ROUND_PREFIXES)
            calls.append({"message": text, "plan": None, "text": "", "usage": {},
                          "round": calls[-1]["round"] + 1 if later else 0})
        elif record.get("type") == "assistant" and calls:
            for part in record["message"].get("content", []):
                if part.get("type") == "tool_use" and part.get("name") == "StructuredOutput":
                    calls[-1]["plan"] = part["input"]
                elif part.get("type") == "text":
                    calls[-1]["text"] = part["text"]
            calls[-1]["usage"] = record["message"].get("usage") or calls[-1]["usage"]
    return calls


def thinking(usage):
    return (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0) or 0


def representative_shape(observation):
    """Today's turn context lists dimensions (and scale) for objects with revisions.

    Recordings predate those fields; for accounting, each such mesh entry gets
    representative values of the benchmark's scaled cube (the token count, not
    the numbers, is what matters). Pessimistic: the values change whenever the
    object's revision does, so every changed entry resends them.
    """
    observation = copy.deepcopy(observation)
    for entry in observation.get("objects") or ():
        if isinstance(entry, dict) and "object_revision" in entry and entry.get("type") == "MESH":
            if entry.get("name") == "Cube":
                entry.setdefault("dimensions", [2.0, 2.0, 2.0])
                continue
            seed = int(entry["object_revision"][:6], 16)
            scale = [0.5 + (seed >> shift) % 4 * 0.25 for shift in (0, 4, 8)]
            entry.setdefault("dimensions", [round(2 * value, 6) for value in scale])
            entry.setdefault("scale", scale)
    return observation


def _turns(calls):
    turns = []
    for index, call in enumerate(calls):
        if call["round"] == 0:
            turns.append([])
        turns[-1].append(index)
    return turns


def _answerable(calls, turn):
    """A turn whose first round only looked up an object and whose last round answered."""
    first, last = calls[turn[0]]["plan"] or {}, calls[turn[-1]]["plan"] or {}
    operations = first.get("operations") or []
    return (len(turn) > 1 and first.get("verify") is True and operations
            and all(operation.get("tool") in _LOOKUPS for operation in operations)
            and not last.get("operations"))


def fit_samples(directory):
    """``(outputs, writes)`` tokenizer samples of a run directory: (text, recorded tokens) pairs."""
    outputs, writes = [], []
    for transcript, _ in replay.find_trials(Path(directory)):
        calls = recorded_turns(transcript)
        for index, call in enumerate(calls):
            outputs.append((plan_text(call["plan"], call["text"]), call["usage"]["output_tokens"] - thinking(call["usage"])))
            if index:
                previous = calls[index - 1]
                writes.append((plan_text(previous["plan"], previous["text"]) + call["message"],
                               call["usage"]["cache_creation_input_tokens"]))
    return outputs, writes


def account(directory, reply_words=0, *, toolset=19, omit_edit_reply=False, answer_from_observation=False,
            fit_also=(), self_fit=True):
    """Per-call before/after token estimates for every recorded trial (see the module docstring).

    ``fit_also`` adds other run directories' calls to the tokenizer fit;
    ``self_fit=False`` fits on them alone (a noisy run, such as one with many
    retried rounds).
    """
    trials = []
    augment = representative_shape if toolset >= 20 else None
    for transcript, session in replay.find_trials(Path(directory)):
        calls = recorded_turns(transcript)
        maps = replay.long_form_maps(json.loads(session.read_text(encoding="utf-8")))
        for call in calls:  # A toolset-19+ recording's plans, in long forms for the rewrite.
            call["long_plan"] = replay.restore_long_forms(call["plan"], maps)
        trials.append((calls, replay.replay_trial(session, transcript, keep_tables=True, augment=augment)))
    if not trials:
        raise SystemExit("No recorded Claude trials in %s" % directory)
    outputs, writes = fit_samples(directory) if self_fit else ([], [])
    for other in fit_also:
        more = fit_samples(other)
        outputs, writes = outputs + more[0], writes + more[1]
    tokenizer = Tokenizer(outputs, writes)
    report = {"toolset": toolset, "scenario": {"reply_words": reply_words, "omit_edit_reply": omit_edit_reply,
                                               "answer_from_observation": answer_from_observation},
              "tokenizer": {"rates_per_byte": dict(zip(("hex", "letters", "other"), tokenizer.rates)),
                            "output_constant": tokenizer.output_constant, "write_constant": tokenizer.write_constant,
                            "residuals": tokenizer.residuals}, "trials": []}
    for calls, rebuilt in trials:
        rows, previous_before, previous_after = [], "", ""
        for number, turn in enumerate(_turns(calls)):
            collapse = answer_from_observation and _answerable(calls, turn)
            members = turn[:1] if collapse else turn
            for position, index in enumerate(members):
                call, replayed = calls[index], rebuilt[index]
                usage = call["usage"]
                if collapse:
                    after_plan = {"reply": calls[turn[-1]]["plan"]["reply"], "operations": []}
                else:
                    after_plan = rewrite_plan(call.get("long_plan", call["plan"]), replayed["table"], reply_words,
                                              omit_edit_reply=omit_edit_reply, infer=toolset >= 20)
                before_out = plan_text(call["plan"], call["text"])
                after_out = plan_text(after_plan, "" if collapse else call["text"])
                row = {"turn": number + 1, "round": position, "request": replayed["request"],
                       "kind": kind_of(calls[turn[0]]["message"]),
                       "recorded": {"output": usage["output_tokens"], "cache_write": usage["cache_creation_input_tokens"],
                                    "cache_read": usage["cache_read_input_tokens"], "thinking": thinking(usage)},
                       "output_before": split_tokens(tokenizer, before_out),
                       "output_after": split_tokens(tokenizer, after_out),
                       "suffix_before": split_tokens(tokenizer, previous_before + call["message"]),
                       "suffix_after": split_tokens(tokenizer, previous_after + replayed["text"])}
                if collapse:
                    row["collapsed_rounds"] = len(turn) - 1
                rows.append(row)
                previous_before, previous_after = before_out, after_out
            if collapse:
                # The later rounds still count on the recorded side only.
                for position, index in enumerate(turn[1:], 1):
                    usage = calls[index]["usage"]
                    rows.append({"turn": number + 1, "round": position, "request": rebuilt[index]["request"],
                                 "kind": kind_of(calls[turn[0]]["message"]), "dropped": True,
                                 "recorded": {"output": usage["output_tokens"],
                                              "cache_write": usage["cache_creation_input_tokens"],
                                              "cache_read": usage["cache_read_input_tokens"],
                                              "thinking": thinking(usage)}})
                previous_before = plan_text(calls[turn[-1]]["plan"], calls[turn[-1]]["text"])
        report["trials"].append(rows)
    report["recorded_toolset"] = recorded_toolset(directory)
    report["prefix_delta_tokens"] = round(prefix_delta(tokenizer, report["recorded_toolset"]))
    return report


def kind_of(message):
    """The long benchmark's task kinds, from the request text."""
    first = message.split("\n", 1)[0]
    for prefix, kind in (("Move ", "move"), ("Scale ", "resize"), ("Create ", "create"), ("Give every face", "material"),
                         ("Stage a PREVIEW", "preview"), ("What ", "question"), ("How many", "first_question")):
        if first.startswith(prefix):
            return kind
    return "bevel" if "bevel" in first else "other"


def _total(parts):
    return sum(parts.values())


def recorded_toolset(directory):
    """The toolset a run recorded (its chats' saved ``toolset_version``), at least 18."""
    versions = [json.loads(session.read_text(encoding="utf-8")).get("toolset_version", 18)
                for _, session in replay.find_trials(Path(directory))]
    return max(18, min(versions)) if versions else 18


def prefix_delta(tokenizer, toolset=18):
    """Estimated tokens today's appended instructions and plan schema add to every call's cached prefix."""
    sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
    from angelo_agent.providers import CLAUDE_INSTRUCTIONS, CLAUDE_PLAN_INSTRUCTIONS
    from angelo_agent.tool_specs import native_plan_schema
    current = {"instructions": features(CLAUDE_INSTRUCTIONS + CLAUDE_PLAN_INSTRUCTIONS),
               "plan_schema": features(json.dumps(native_plan_schema(), separators=(",", ":")))}
    recorded = RECORDED_PREFIX_FEATURES[min(toolset, max(RECORDED_PREFIX_FEATURES))]
    return sum(rate * (now - before) for name in current
               for rate, now, before in zip(tokenizer.rates, current[name], recorded[name]))


def per_call(row, *, after):
    """``(cache_write, output)`` of one call: recorded, or recorded minus the estimated saving."""
    recorded = row["recorded"]
    if not after:
        return recorded["cache_write"], recorded["output"]
    return (recorded["cache_write"] - (_total(row["suffix_before"]) - _total(row["suffix_after"])),
            recorded["output"] - (_total(row["output_before"]) - _total(row["output_after"])))


def costs(report, tasks=None):
    """Per-trial cost of the first ``tasks`` user turns: recorded, and with today's estimates.

    Reads are cumulative, exactly as recorded: each call reads the previous
    call's read plus its write. After: writes and outputs per ``per_call``;
    the first read gains the prefix change (``prefix_delta``). The one-time
    write of a changed prefix, once per working directory and cache lifetime,
    is not per trial and is left out. Prices are Claude Opus 5.5's; two
    uncached input tokens per call as recorded. Also the cumulative cost
    after each turn.
    """
    result = []
    longest = max(max(row["turn"] for row in rows) for rows in report["trials"])
    for rows in report["trials"]:
        if tasks is None and max(row["turn"] for row in rows) < longest:
            continue  # An incomplete trial (it stopped early) is not a session's cost.
        rows = [row for row in rows if tasks is None or row["turn"] <= tasks]
        trial = {}
        for side in ("before", "after"):
            read = rows[0]["recorded"]["cache_read"] + (report["prefix_delta_tokens"] if side == "after" else 0)
            totals = {"cache_read": 0.0, "cache_write": 0.0, "output": 0.0, "input": 0.0}
            curve, calls = {}, 0
            for row in rows:
                if side == "after" and row.get("dropped"):
                    continue
                write, output = per_call(row, after=side == "after")
                if side == "before":
                    read = row["recorded"]["cache_read"]
                for name, tokens in (("cache_read", read), ("cache_write", write), ("output", output), ("input", 2)):
                    totals[name] += tokens * PRICES[name] / 1e6
                calls += 1
                curve[row["turn"]] = {"usd": round(sum(totals.values()), 4), "calls": calls, "read": read}
                read += write
            trial[side + "_usd"] = round(sum(totals.values()), 4)
            trial[side + "_components_usd"] = {name: round(value, 4) for name, value in totals.items()}
            trial[side + "_curve"] = curve
        result.append(trial)
    return result


def _median(values):
    values = sorted(values)
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def by_kind(report, *, after, from_turn=1):
    """Median per-turn cache write, output and calls by task kind (turns from ``from_turn``)."""
    collected = {}
    for rows in report["trials"]:
        turns = {}
        for row in rows:
            if row["turn"] < from_turn or (after and row.get("dropped")):
                continue
            write, output = per_call(row, after=after)
            entry = turns.setdefault(row["turn"], {"kind": row["kind"], "write": 0, "output": 0, "calls": 0})
            entry["write"] += write
            entry["output"] += output
            entry["calls"] += 1
        for entry in turns.values():
            collected.setdefault(entry["kind"], []).append(entry)
    return {kind: {key: round(_median([entry[key] for entry in entries]), 1) for key in ("write", "output", "calls")}
            for kind, entries in sorted(collected.items())}


def summary(report):
    """Medians across trials of per-call component tokens, for runs whose trials share one call sequence."""
    trials = report["trials"]
    output = []
    trials = [[row for row in rows if not row.get("dropped")] for rows in trials]
    for index in range(min(len(rows) for rows in trials)):
        row = {"turn": trials[0][index]["turn"], "request": trials[0][index]["request"]}
        for key in ("output_before", "output_after", "suffix_before", "suffix_after"):
            row[key] = {name: round(_median([rows[index][key][name] for rows in trials]))
                        for name in ("identifiers", "structure", "prose")}
        row["recorded"] = {name: _median([rows[index]["recorded"][name] for rows in trials])
                           for name in ("output", "cache_write")}
        output.append(row)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Unbilled per-component token accounting")
    parser.add_argument("directory")
    parser.add_argument("--json", default="")
    parser.add_argument("--tasks", type=int, default=0, help="price only the first N user turns (default: all)")
    parser.add_argument("--prices", choices=sorted(PRICE_TABLES), default="opus")
    parser.add_argument("--no-self-fit", action="store_true", help="fit the tokenizer on --fit-also runs only")
    parser.add_argument("--fit-also", action="append", default=[],
                        help="another run directory whose calls join the tokenizer fit (repeatable)")
    parser.add_argument("--toolset", type=int, default=21, choices=(19, 20, 21),
                        help="the contract to rewrite plans to (20 and 21 infer bindings and add observed "
                             "dimensions; the messages are always today's composition)")
    parser.add_argument("--reply-words", type=int, default=0,
                        help="also cap edit replies at this many words (an assumption about the guidance)")
    parser.add_argument("--omit-edit-replies", action="store_true",
                        help="also drop edit replies (an assumption: the model omits an optional reply)")
    parser.add_argument("--answer-from-observation", action="store_true",
                        help="also answer a lookup-then-answer question in one round (an assumption: the "
                             "observation now holds the values)")
    args = parser.parse_args(argv)
    PRICES.update(PRICE_TABLES[args.prices])
    report = account(args.directory, args.reply_words, toolset=args.toolset, omit_edit_reply=args.omit_edit_replies,
                     answer_from_observation=args.answer_from_observation, fit_also=args.fit_also,
                     self_fit=not args.no_self_fit)
    report["costs"] = costs(report, args.tasks or None)
    fit = report["tokenizer"]
    print("Tokenizer approximation (fitted to this run): tokens per byte hex %.3f, letters %.3f, other %.3f; "
          "constants: output %.1f, cache write %.1f" % (*fit["rates_per_byte"].values(), fit["output_constant"],
                                                        fit["write_constant"]))
    for name, residual in fit["residuals"].items():
        print("  %s residual rms %.1f, max %.1f (mean %.0f tokens)" % (name, residual["rms"], residual["max"],
                                                                      residual["mean_tokens"]))
    if len({len(rows) for rows in report["trials"]}) == 1:
        report["summary"] = summary(report)
        print("\nMedian tokens per call by component (identifiers/structure/prose), excluding framing constants:")
        for row in report["summary"]:
            line = "%2d %-44s" % (row["turn"], row["request"][:44])
            for key in ("output_before", "output_after", "suffix_before", "suffix_after"):
                parts = row[key]
                line += " | %s %d/%d/%d" % (key, parts["identifiers"], parts["structure"], parts["prose"])
            print(line + " | recorded out %d write %d" % (row["recorded"]["output"], row["recorded"]["cache_write"]))
    for label, start in (("all turns", 1), ("turns 20+", 20)):
        print("\nPer turn by kind, %s: median cache write / output / calls, recorded -> estimated after" % label)
        before, after = by_kind(report, after=False, from_turn=start), by_kind(report, after=True, from_turn=start)
        for kind in before:
            print("  %-9s %6.0f / %4.0f / %.0f -> %6.0f / %4.0f / %.0f" % (
                kind, before[kind]["write"], before[kind]["output"], before[kind]["calls"],
                after[kind]["write"], after[kind]["output"], after[kind]["calls"]))
    print("\nPrefix change re-read by every call: %+d tokens (instructions and plan schema)"
          % report["prefix_delta_tokens"])
    print("\nTrial cost USD (recorded -> estimated after):")
    for number, cost in enumerate(report["costs"], 1):
        print("  trial %d: %.4f -> %.4f  %s -> %s" % (number, cost["before_usd"], cost["after_usd"],
                                                     cost["before_components_usd"], cost["after_components_usd"]))
    print("  median: %.4f -> %.4f" % (_median([c["before_usd"] for c in report["costs"]]),
                                      _median([c["after_usd"] for c in report["costs"]])))
    turns = sorted({turn for cost in report["costs"] for turn in cost["before_curve"]})
    marks = [turn for turn in turns if turn in (5, 10, 20, 30, 40, 60, 80, 100)] or turns[-1:]
    print("  cumulative median by turn: " + ", ".join(
        "%d: %.3f -> %.3f" % (turn, _median([c["before_curve"][turn]["usd"] for c in report["costs"] if turn in c["before_curve"]]),
                             _median([c["after_curve"][turn]["usd"] for c in report["costs"] if turn in c["after_curve"]]))
        for turn in marks))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
