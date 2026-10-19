# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled attribution of the output tokens recorded typed plans spend, and of the same plans re-encoded.

No provider call, Blender process or network. For ``bench_mcp_vs_angelo.py`` or ``eval_live_agent.py`` run
directories it reads:

* Claude: every model call of ``angelo-trial-NN-transcript.jsonl`` with its usage (output and thinking tokens)
  and what it wrote (the StructuredOutput plan, any text);
* Codex, with ``--codex-sessions DIR`` (the sessions folder of the connection's Codex home; only rollouts of
  the run's own threads are read): every model response of the trials' threads, its usage and what it wrote
  (code-mode ``exec`` scripts, the final message).

Token counts, all stated with the result:

* ``fit``: per-class rates (ASCII digits, ASCII letters, whitespace, other bytes) and a per-call constant,
  fitted by least squares to the runs' own recorded visible output (output minus thinking or reasoning tokens)
  against what each call wrote; residuals are printed. On recorded Opus one-shot runs (826 calls)
  Claude's tokenizer spent about 1.1 tokens per digit, 0.35 per letter and 0.68 per other byte (rms 7.6 on a
  mean of 203): every digit of a number is a token. An approximation, not the tokenizer.
* ``o200k``: tiktoken's ``o200k_base`` when it can be loaded (set ``TIKTOKEN_CACHE_DIR`` for an offline copy).
  It is GPT-6.x's tokenizer as far as the recordings show: every recorded final message counted o200k + 10.
* ``bytes/4``.

Parts: the plan's compact JSON is split into ``script`` (a Python step's code), ``script text`` (its
description), ``reply``, ``op kinds`` (``"kind":...``), ``keys`` (argument names), ``numbers`` (numbers and the
brackets of numeric arrays), ``names`` (object, material and modifier names), ``aliases``, ``identifiers``
(handles, revisions), ``enums`` (upper-case constants), ``defaults`` (an argument equal to its schema
default, written anyway), ``checks`` (``expected_checks``) and ``framing`` (the rest: braces, the plan and
tool wrapper, ``mode``). Per-part counts tokenize each part on its own, so they add up to slightly more or
less than the whole.

Re-encodings, the "after" columns (the model's other choices kept as recorded):

* Claude: create_primitive operations that differ only in name, alias and location become the first of them
  and one ``batch`` at the end of the program with every location as ``at`` (``--best-batch``: a mirror or a
  two-row array where the positions allow); objects that only received the unit's material drop out of
  ``also``, as linked copies share it; fit-dimensions bounds equal to the defaults are left out.
* Codex: each final message is decoded with today's plan document and encoded with today's output schema
  (``codex_plan``), leaving out what the old strict form made the model write: nulls, values equal to their
  defaults (a recipe's included) and, as an assumption printed with the result, a recipe's ``tolerance`` and a
  recipe call's ``revision`` (the host infers it from the observation).

Usage, from the repository root::

    python3 tests/python/angelo_agent/plan_output_accounting.py DIR [DIR ...] [--codex-sessions SESSIONS]
        [--fit-also DIR ...] [--best-batch] [--json OUT]
"""

import argparse
import copy
import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDONS = HERE.parents[2] / "scripts" / "addons_core"
for _path in (str(ADDONS), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_DIGIT, _LETTER, _SPACE = re.compile(r"[0-9]"), re.compile(r"[A-Za-z]"), re.compile(r"\s")
# Activity call IDs are session/connection/thread/call; every ID is a candidate, the rollout file names decide.
_THREAD = re.compile(r"(?<=/)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=/)")
NAME_KEYS = frozenset({"name", "material", "modifier", "copy_as", "collection", "shade_modifier_name", "modifier_name"})
SCRIPT_TOOL = "angelo_execute_python"


def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


# Recorded calls ------------------------------------------------------------------------------------------------

def claude_calls(directory):
    """``[{trial, call, output, thinking, plans, text}]``: one row per model call, in order."""
    rows = []
    for path in sorted(Path(directory).glob("angelo-trial-*-transcript.jsonl")):
        calls, order = {}, []
        for line in path.read_text().splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message") or {}
            key = entry.get("requestId") or entry.get("uuid")
            if key not in calls:
                calls[key] = {"usage": {}, "plans": [], "text": []}
                order.append(key)
            calls[key]["usage"] = message.get("usage") or calls[key]["usage"]
            for block in message.get("content") or ():
                if block.get("type") == "tool_use" and block.get("name") == "StructuredOutput":
                    calls[key]["plans"].append(block.get("input"))
                elif block.get("type") == "text":
                    calls[key]["text"].append(block.get("text") or "")
        for number, key in enumerate(order):
            usage = calls[key]["usage"]
            rows.append({"trial": path.name.split("-transcript")[0], "call": number,
                         "output": usage.get("output_tokens"),
                         "thinking": (usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0,
                         "plans": calls[key]["plans"], "text": "".join(calls[key]["text"])})
    return rows


def codex_calls(directory, sessions):
    """``[{trial, call, output, thinking, writes: [{kind, text}]}]`` from the rollouts of the run's threads."""
    threads = {}
    for path in Path(directory).glob("angelo-trial-*/trial-*/state/sessions/*.json"):
        trial = path.relative_to(directory).parts[0]
        for thread in set(_THREAD.findall(path.read_text())):
            threads[thread] = trial
    rows = []
    for path in sorted(Path(sessions).rglob("rollout-*.jsonl")):
        thread = next((thread for thread in threads if path.name.endswith(thread + ".jsonl")), None)
        if thread is None:
            continue
        writes, number = [], 0
        for line in path.read_text().splitlines():
            entry = json.loads(line)
            payload = entry.get("payload") or {}
            kind = payload.get("type")
            if entry.get("type") == "response_item" and kind == "custom_tool_call":
                writes.append({"kind": "exec", "text": payload.get("input") or ""})
            elif entry.get("type") == "response_item" and kind == "message" and payload.get("role") == "assistant":
                writes.append({"kind": "message", "text": "".join(part.get("text", "") for part in
                                                                  payload.get("content") or ())})
            elif entry.get("type") == "token_usage_record":
                usage = payload.get("usage") or {}
                rows.append({"trial": threads[thread], "call": number, "output": usage.get("output_tokens"),
                             "thinking": usage.get("reasoning_output_tokens") or 0, "writes": writes})
                writes, number = [], number + 1
    return rows


def written_text(row):
    """Everything a call wrote, as the model wrote it (Claude's plan as compact JSON)."""
    if "plans" in row:
        return "".join(compact(plan) for plan in row["plans"]) + row["text"]
    return "".join(write["text"] for write in row["writes"])


# Tokens --------------------------------------------------------------------------------------------------------

def features(text):
    digits, letters, spaces = len(_DIGIT.findall(text)), len(_LETTER.findall(text)), len(_SPACE.findall(text))
    return [digits, letters, spaces, len(text.encode("utf-8")) - digits - letters - spaces]


def _solve(rows, values):
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


class ClassFit:
    """Per-class token rates and one per-call constant, fitted to recorded (text, visible output tokens)."""

    def __init__(self, samples):
        self.rates, self.constant = [0.0] * 4, 0.0
        *self.rates, self.constant = _solve([features(text) + [1] for text, _ in samples],
                                            [tokens for _, tokens in samples])
        errors = [tokens - self.constant - self.tokens(text) for text, tokens in samples]
        self.rms = (sum(error * error for error in errors) / len(errors)) ** .5
        self.mean = sum(tokens for _, tokens in samples) / len(samples)
        self.samples = len(samples)

    def tokens(self, text):
        return sum(rate * count for rate, count in zip(self.rates, features(text)))

    def describe(self):
        return ("fit (%d calls): %.3f per digit, %.3f per letter, %.3f per whitespace, %.3f per other byte, "
                "%.1f per call; rms %.1f on a mean of %.0f" % (self.samples, *self.rates, self.constant,
                                                               self.rms, self.mean))


def o200k():
    """tiktoken's o200k_base, or None when it cannot be loaded (the counts are then left out)."""
    try:
        import tiktoken
        return tiktoken.get_encoding("o200k_base")
    except Exception:  # Not installed, or no cached copy offline.
        return None


class Counter:
    def __init__(self, fit, encoding):
        self.fit, self.encoding = fit, encoding

    def __call__(self, text):
        return {"fit": self.fit.tokens(text), "o200k": len(self.encoding.encode(text)) if self.encoding else None,
                "bytes/4": len(text.encode("utf-8")) / 4}


# Parts ---------------------------------------------------------------------------------------------------------

def _member(schema, value):
    """The member of a union a dict value selects by its constant fields (else the first object member)."""
    if not isinstance(schema, dict) or "oneOf" not in schema:
        return schema
    objects = [member for member in schema["oneOf"] if isinstance(member, dict) and member.get("type") == "object"]
    for member in objects:
        constants = {key: field["const"] for key, field in (member.get("properties") or {}).items()
                     if isinstance(field, dict) and "const" in field}
        if constants and isinstance(value, dict) and all(value.get(key) == const for key, const in constants.items()):
            return member
    return objects[0] if objects else None


def _scalar_part(key, value):
    if isinstance(value, bool) or value is None:
        return "framing"
    if isinstance(value, (int, float)):
        return "numbers"
    if key == "alias" or value.startswith("$"):
        return "aliases"
    if re.fullmatch(r"o[1-9][0-9]*|[0-9a-f]{8,64}|scn:[0-9a-f]{32}:[0-9]+", value):
        return "identifiers"
    if key in NAME_KEYS:
        return "names"
    return "enums" if re.fullmatch(r"[A-Z0-9_]+", value) else "values"


def _value(parts, key, value, schema):
    if isinstance(value, dict):
        _object(parts, value, _member(schema, value))
    elif isinstance(value, list) and value and all(isinstance(item, (int, float, list)) and not isinstance(item, bool)
                                                   for item in value):
        parts.append(("numbers", compact(value)))
    elif isinstance(value, list):
        items = schema.get("items") if isinstance(schema, dict) else None
        parts.append(("framing", "["))
        for index, item in enumerate(value):
            if index:
                parts.append(("framing", ","))
            _value(parts, key, item, items)
        parts.append(("framing", "]"))
    else:
        parts.append((_scalar_part(key, value), compact(value)))


def _object(parts, value, schema):
    properties = (schema or {}).get("properties") or {}
    parts.append(("framing", "{"))
    for index, (key, item) in enumerate(value.items()):
        if index:
            parts.append(("framing", ","))
        field = properties.get(key)
        if isinstance(field, dict) and field.get("default") is not None and field.get("default") == item:
            parts.append(("defaults", compact(key) + ":" + compact(item)))
            continue
        parts.append(("keys", compact(key) + ":"))
        _value(parts, key, item, field)
    parts.append(("framing", "}"))


def _program_operation(parts, operation):
    from angelo_agent.action_schemas import ACTION_SCHEMAS
    parts.append(("framing", "{"))
    for index, (key, value) in enumerate(operation.items()):
        if index:
            parts.append(("framing", ","))
        if key == "kind":
            parts.append(("op kinds", compact(key) + ":" + compact(value)))
        elif key == "target" and value is None:
            parts.append(("defaults", '"target":null'))
        elif key == "parameters":
            parts.append(("framing", '"parameters":'))
            _value(parts, key, value, ACTION_SCHEMAS.get(operation.get("kind")))
        else:
            parts.append(("framing", compact(key) + ":"))
            _value(parts, key, value, None)
    parts.append(("framing", "}"))


def segments(plan):
    """``[(part, text)]`` whose texts join to the plan's compact JSON exactly."""
    from angelo_agent.tool_specs import TOOL_MAP
    parts = [("framing", "{")]
    for index, (key, value) in enumerate(plan.items()):
        if index:
            parts.append(("framing", ","))
        if key == "reply":
            parts += [("framing", '"reply":'), ("reply", compact(value))]
        elif key != "operations" or not isinstance(value, list):
            parts.append(("framing", compact(key) + ":" + compact(value)))
        else:
            parts.append(("framing", '"operations":['))
            for number, operation in enumerate(value):
                if number:
                    parts.append(("framing", ","))
                _tool_operation(parts, operation, TOOL_MAP)
            parts.append(("framing", "]"))
    parts.append(("framing", "}"))
    return parts


def _tool_operation(parts, operation, tools):
    tool, arguments = (operation.get("tool"), operation.get("arguments")) if isinstance(operation, dict) else (None, None)
    if not isinstance(arguments, dict) or set(operation) != {"tool", "arguments"} or list(operation)[0] != "tool":
        parts.append(("framing", compact(operation)))
        return
    parts.append(("framing", '{"tool":' + compact(tool) + ',"arguments":{'))
    schema = (tools.get(tool) or {}).get("inputSchema")
    for index, (key, value) in enumerate(arguments.items()):
        if index:
            parts.append(("framing", ","))
        if tool == SCRIPT_TOOL and key in ("code", "description"):
            parts += [("framing", compact(key) + ":"), ("script" if key == "code" else "script text", compact(value))]
        elif key == "operations" and tool == "angelo_scene_program" and isinstance(value, list):
            parts.append(("framing", '"operations":['))
            for number, item in enumerate(value):
                if number:
                    parts.append(("framing", ","))
                if isinstance(item, dict):
                    _program_operation(parts, item)
                else:
                    parts.append(("framing", compact(item)))
            parts.append(("framing", "]"))
        elif key in ("expected_checks", "checks"):
            parts.append(("checks", compact(key) + ":" + compact(value)))
        elif key == "mode":
            parts.append(("framing", compact(key) + ":" + compact(value)))
        else:
            parts.append(("keys", compact(key) + ":"))
            member = _member(schema, arguments) if isinstance(schema, dict) else None
            _value(parts, key, value, ((member or schema or {}).get("properties") or {}).get(key))
    parts.append(("framing", "}}"))


# Re-encodings --------------------------------------------------------------------------------------------------

def _mirror(positions):
    """The axis across whose plane through the origin two positions are each other's mirror image, if any."""
    first, second = positions
    for axis, name in enumerate("XYZ"):
        if first[axis] != 0 and second == [-value if index == axis else value for index, value in enumerate(first)]:
            return name
    return None


def _grid(positions):
    """Two-row array steps for four positions on a rectangle, the first a corner, or None."""
    xs, ys = sorted({p[0] for p in positions}), sorted({p[1] for p in positions})
    if len(xs) != 2 or len(ys) != 2 or len({p[2] for p in positions}) != 1 or len({tuple(p) for p in positions}) != 4:
        return None
    x0, y0 = positions[0][:2]
    step = lambda values, start: round((values[1] - values[0]) * (1 if start == values[0] else -1), 6)  # noqa: E731
    return [{"step": [step(xs, x0), 0, 0], "count": 2}, {"step": [0, step(ys, y0), 0], "count": 2}]


def batch_parameters(alias, positions, best=False):
    if best and len(positions) == 2 and _mirror(positions):
        return {"sources": [alias], "mirror": _mirror(positions)}
    if best and len(positions) == 4 and _grid(positions):
        return {"sources": [alias], "array": _grid(positions)}
    return {"sources": [alias], "at": positions}


def repeat_as_batch(operations, best=False):
    """A program whose repeated create_primitive units are one create and one batch each (see the module doc)."""
    groups = {}
    for index, operation in enumerate(operations):
        parameters = operation.get("parameters") if isinstance(operation, dict) else None
        if operation.get("kind") == "create_primitive" and not operation.get("target") and isinstance(parameters, dict):
            key = compact({k: v for k, v in parameters.items() if k not in ("name", "alias", "location")})
            groups.setdefault(key, []).append(index)
    operations = copy.deepcopy(operations)
    removed, gone, batches = set(), set(), []
    for members in (group for group in groups.values() if len(group) > 1):
        unit = operations[members[0]]["parameters"]
        unit.setdefault("alias", "unit%d" % members[0])
        positions = [operations[index]["parameters"].get("location", [0, 0, 0]) for index in members]
        batches.append({"kind": "batch", "parameters": batch_parameters("$" + unit["alias"], positions, best)})
        removed.update(members[1:])
        gone.update("$" + operations[index]["parameters"]["alias"] for index in members[1:]
                    if operations[index]["parameters"].get("alias"))
    result = []
    for index, operation in enumerate(operations):
        if index in removed or operation.get("target") in gone:
            continue
        also = (operation.get("parameters") or {}).get("also")
        if isinstance(also, list):
            operation["parameters"]["also"] = [item for item in also if item not in gone]
            if not operation["parameters"]["also"]:
                del operation["parameters"]["also"]
        result.append(operation)
    return result + batches


def claude_after(plan, best=False):
    from angelo_agent.action_recipes import FIT_BOUNDS
    plan = copy.deepcopy(plan)
    for operation in plan.get("operations") or ():
        arguments = operation.get("arguments") if isinstance(operation, dict) else None
        if not isinstance(arguments, dict):
            continue
        if operation.get("tool") == "angelo_scene_program" and isinstance(arguments.get("operations"), list):
            arguments["operations"] = repeat_as_batch(arguments["operations"], best)
        if operation.get("tool") == "angelo_apply_recipe" and isinstance(arguments.get("parameters"), dict):
            for key, value in FIT_BOUNDS.items():
                if arguments["parameters"].get(key) == value:
                    del arguments["parameters"][key]
    return plan


def _without_defaults(value, schema, drop):
    """An ordinary plan value without nulls and values equal to their schema default (``drop``: keys too)."""
    from angelo_agent.strict_schema import _union
    if isinstance(value, list):
        items = schema.get("items") if isinstance(schema, dict) else None
        return [_without_defaults(item, items, drop) for item in value]
    if not isinstance(value, dict):
        return value
    members = _union(schema) if isinstance(schema, dict) else None
    if members:
        from angelo_agent.strict_schema import _matches
        schema = next((member for member in members if _matches(value, member)), members[0])
    properties = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or ())
    result = {}
    for key, item in value.items():
        field = properties.get(key) or {}
        if key not in required and (item is None or key in drop or ("default" in field and field["default"] == item)):
            continue
        result[key] = _without_defaults(item, field, drop)
    return result


def codex_after(message):
    """A recorded Codex final message re-encoded with today's output schema (see the module doc)."""
    from angelo_agent import codex_plan
    from angelo_agent.action_recipes import DEFAULTS
    from angelo_agent.strict_schema import decode, encode
    document = codex_plan._documents(None)[0]
    plan = decode(json.loads(message), document)
    for operation in plan.get("operations") or ():
        arguments = operation.get("arguments")
        if operation.get("tool") == "angelo_apply_recipe" and isinstance(arguments, dict):
            parameters = arguments.get("parameters") or {}
            for key, value in list(parameters.items()):
                if key == "tolerance" or DEFAULTS.get(arguments.get("recipe"), {}).get(key) == value:
                    del parameters[key]
            arguments.pop("revision", None)
    plan = _without_defaults(plan, document, frozenset({"recovery_observation"}))
    return compact(encode(plan, document))


# Report --------------------------------------------------------------------------------------------------------

def _stats(values):
    """Median [least-most], mean."""
    values = [value for value in values if value is not None]
    if not values:
        return "-"
    return "%.0f [%.0f-%.0f], mean %.1f" % (statistics.median(values), min(values), max(values),
                                           sum(values) / len(values))


def _kind(plans):
    """What a plan call did: its first operation's tool (and a recipe's name), or a reply."""
    for plan in plans:
        for operation in plan.get("operations") or ():
            if isinstance(operation, dict):
                arguments = operation.get("arguments")
                recipe = arguments.get("recipe") if isinstance(arguments, dict) else None
                return operation.get("tool", "?") + (":" + recipe if isinstance(recipe, str) else "")
    return "reply only"


def account(directories, sessions=None, fit_also=(), best=False):
    report, runs, samples = {"fit": {}}, {}, {"claude": [], "codex": []}
    for directory in list(directories) + list(fit_also):
        rows = claude_calls(directory)
        runtime = "claude" if rows else "codex"
        rows = rows or (codex_calls(directory, sessions) if sessions else [])
        samples[runtime] += [(written_text(row), row["output"] - row["thinking"]) for row in rows
                             if row["output"] is not None]
        if directory in directories:
            runs[str(directory)] = (runtime, rows)
    encoding = o200k()
    counters = {}
    for runtime, pairs in samples.items():  # One fit per runtime: their tokenizers differ.
        if pairs:
            fit = ClassFit(pairs)
            counters[runtime] = Counter(fit, encoding)
            report["fit"][runtime] = fit.describe()
    report["o200k"] = encoding is not None
    report["runs"] = {}
    for directory, (runtime, rows) in runs.items():
        count = counters.get(runtime)
        if count is None:
            continue
        parts, calls, before, after, recorded, kinds = {}, 0, [], [], [], []
        for row in rows:
            plans = row["plans"] if "plans" in row else [json.loads(write["text"]) for write in row["writes"]
                                                         if write["kind"] == "message" and write["text"].startswith("{")]
            if not plans:
                continue
            calls += 1
            recorded.append((row["output"], row["thinking"]))
            for plan in plans:
                for part, text in segments(plan):
                    counted = count(text)
                    for unit, value in counted.items():
                        if value is not None:
                            parts.setdefault(part, {}).setdefault(unit, 0.0)
                            parts[part][unit] += value
            if "plans" in row:
                old = written_text(row)
                new = "".join(compact(claude_after(plan, best)) for plan in plans) + row["text"]
            else:
                old = "".join(write["text"] for write in row["writes"] if write["kind"] == "message")
                new = "".join(codex_after(write["text"]) if write["text"].startswith("{") else write["text"]
                              for write in row["writes"] if write["kind"] == "message")
            before.append(count(old))
            after.append(count(new))
            kinds.append(_kind(plans))
        report["runs"][directory] = {
            "calls": calls,
            "recorded_output": _stats([output for output, _ in recorded]),
            "recorded_thinking": _stats([thinking for _, thinking in recorded]),
            "parts_per_call": {part: {unit: value / calls for unit, value in units.items()}
                               for part, units in sorted(parts.items(), key=lambda kv: -kv[1]["fit"])} if calls else {},
            "before": {unit: _stats([row[unit] for row in before]) for unit in ("fit", "o200k", "bytes/4")},
            "after": {unit: _stats([row[unit] for row in after]) for unit in ("fit", "o200k", "bytes/4")},
            "cut": {unit: _stats([b[unit] - a[unit] for b, a in zip(before, after)] if before and before[0][unit]
                                 is not None else []) for unit in ("fit", "o200k", "bytes/4")},
            "by_kind": {kind: {"calls": kinds.count(kind),
                               **{label: {unit: sum(row[unit] for row, k in zip(rows_, kinds) if k == kind)
                                          / kinds.count(kind) for unit in ("fit", "o200k") if rows_[0][unit] is not None}
                                  for label, rows_ in (("before", before), ("after", after))}}
                        for kind in sorted(set(kinds))},
        }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Unbilled plan output token accounting")
    parser.add_argument("directories", nargs="+")
    parser.add_argument("--codex-sessions", default="", help="the Codex home's sessions folder (Codex runs)")
    parser.add_argument("--fit-also", action="append", default=[], help="another run that joins the fit")
    parser.add_argument("--best-batch", action="store_true", help="mirror or array where positions allow")
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)
    report = account(args.directories, args.codex_sessions or None, args.fit_also, args.best_batch)
    for runtime, text in report["fit"].items():
        print("%s %s" % (runtime, text))
    print("o200k loaded" if report["o200k"] else "o200k unavailable")
    for directory, run in report["runs"].items():
        print("\n%s: %d plan calls; recorded output %s, of which thinking %s" % (
            Path(directory).name, run["calls"], run["recorded_output"], run["recorded_thinking"]))
        print("  %-12s %8s %8s %8s   (per call)" % ("part", "fit", "o200k", "bytes/4"))
        for part, units in run["parts_per_call"].items():
            o200k_value = units.get("o200k")
            print("  %-12s %8.0f %8s %8.0f" % (part, units["fit"], "-" if o200k_value is None else "%.0f" % o200k_value,
                                                units["bytes/4"]))
        for label in ("before", "after", "cut"):
            print("  %-6s %s" % (label, "; ".join("%s %s" % item for item in run[label].items())))
        for kind, row in run["by_kind"].items():
            print("  %-36s %4d calls, mean before -> after: %s" % (kind, row["calls"], "; ".join(
                "%s %.1f -> %.1f" % (unit, row["before"][unit], row["after"][unit]) for unit in row["before"])))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
