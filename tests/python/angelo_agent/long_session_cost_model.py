# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Deterministic long-session cost model, calibrated on billed runs. No provider call.

Each side is a set of per-task shapes by task kind (model calls, tokens written
to the prompt cache over the task's calls, output tokens) plus the cached
prefix its first call reads. A session plays the long benchmark's task
sequence (``eval_live_tasks.build_long_session_tasks``, continued past 120
tasks by the same cycle) in one native context and prices every call the way
the prompt cache accounts it:

    read_k = prefix + every earlier write in the context,
    cost_k = read_k * READ + write_k * WRITE + output_k * OUTPUT + 2 * INPUT.

A task's writes and outputs are split evenly over its calls. Sides:

* ``mcp``, ``angelo_18`` and ``angelo_19``: medians measured by billed
  40-task runs (``bench_mcp_vs_angelo.py --task-set long``,
  claude-opus-5-5, effort low, stable working directories; toolset 19's from
  its two complete trials). The model reproduces their measured 40-task
  costs, and toolset 19's with a 50k-token context rebase (``validate``).
* ``angelo_20`` and ``angelo_20_short_replies``: unbilled estimates from
  ``token_accounting.py`` over the toolset-19 run's transcripts, rebuilt
  through toolset 20's composition with its plans rewritten to the toolset-20
  contract; ``_short_replies`` also omits edit replies (an assumption about
  model behaviour that a billed run must confirm).

Why an earlier version of this model missed the measured long-run costs. It
used five-task shapes that did not hold over a session: every read-only
question took two calls (a lookup round, then a continuation that re-read the
whole context) writing about 4.1k tokens, where
the model assumed one call writing 650; an edit wrote 1.55-1.8k tokens (replay
estimate 1,250) and output about 550 (assumed 350); the prefix was 27k tokens
(assumed 22.3k); and the reference side used five-task MCP shapes (2.4 calls,
351 written and 208 output per call) where the long run measured about 2.05
calls and 200 written and 133 output per call. Per-kind shapes from a long run
fix all of these; ``validate`` checks them against the run's measured costs.

Prices are the published rates of ``--model`` (default ``claude-opus-5-5``) at
the 1-hour cache-write rate Claude Code uses on a subscription, from
``angelo_agent.model_profiles`` (sources and date there). For Opus 5.5 they
reproduce the measured runs' cost_usd records to the cent; another model's
rates price the same Opus-measured token shapes (an estimate, not a bill).
A model with a per-request price step (Haiku 5.5 above 100k prompt tokens)
prices each call at the tier of that call's own prompt (``use_model``).

Auto-compaction is modelled as before (unmeasured): when a context passes
``COMPACT_AT`` tokens the runtime makes one summarizing call (reads the context,
writes ``COMPACT_OUTPUT``) and continues from the prefix plus that summary.
A context rebase (off by default in the product) starts a fresh native context
when the context reaches the threshold before a task: the prefix stays a cache
hit (one working directory; the billed rebase run read exactly the 27k prefix
on the first call after each rebase), and the first message writes the side's
``rebase_extra`` more tokens than the task otherwise would (the seed, a
bounded handoff and complete context blocks): measured 11.7k at toolset 19,
about 1.9k at toolset 20 (``replay_turn_context.py`` rebuilt the same turn).

Usage::

    python3 tests/python/angelo_agent/long_session_cost_model.py [--tasks 40 100] [--json OUT]
"""

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import model_profiles  # noqa: E402

MODEL = "claude-opus-5-5"


def _rates(model, prompt_tokens=0):
    """(cache read, cache write at the 1-hour TTL, output, uncached input), USD/MTok, for one call's prompt."""
    rates = model_profiles.rates(model, "1h", prompt_tokens=prompt_tokens)
    if rates is None:
        raise ValueError("No published prices for %s in model_profiles" % model)
    return rates["cache_read"], rates["cache_write"], rates["output"], rates["input"]


def use_model(model):
    """Price calls at ``model``'s rates: READ, WRITE, OUTPUT and INPUT are its base tier; a call whose prompt
    exceeds its per-request price step (``STEP``; 0 for one tier) is priced at ``ABOVE``, its higher rates."""
    global READ, WRITE, OUTPUT, INPUT, STEP, ABOVE
    READ, WRITE, OUTPUT, INPUT = _rates(model)
    identity = model_profiles.resolve(model)
    STEP = model_profiles.MODELS[identity].get("price_step_tokens", 0)
    ABOVE = _rates(model, STEP + 1) if STEP else None


def _call_rates(prompt):
    """(read, write, output, input) rates of one call whose prompt is ``prompt`` tokens (the step is per call)."""
    return ABOVE if STEP and prompt > STEP else (READ, WRITE, OUTPUT, INPUT)


READ = WRITE = OUTPUT = INPUT = STEP = ABOVE = None
use_model(MODEL)  # Opus 5.5: 0.20, 8.0, 20.0, 4.0, no step
COMPACT_AT = 160000
COMPACT_OUTPUT = 4000
TASKS = (5, 25, 40, 50, 100)
# Median per-trial cost of the billed 40-task runs: (side, rebase threshold) -> USD.
MEASURED_40 = {("mcp", 0): 0.838, ("angelo_18", 0): 1.592, ("angelo_19", 0): 0.849, ("angelo_19", 50000): 0.956}

# Per kind: (model calls, tokens written over the task, output tokens over the task).
SIDES = {
    "mcp": {"prefix": 21030, "source": "billed 40-task run, medians", "kinds": {
        "first_question": (3, 0, 145), "create": (3, 813, 521), "move": (2, 262, 134), "resize": (2, 334, 313),
        "bevel_add": (2, 669, 662), "bevel": (2, 423, 256), "material": (2, 612, 482), "question": (2, 389, 155)}},
    "angelo_18": {"prefix": 27009, "rebase_extra": 8000, "source": "billed 40-task run, medians", "kinds": {
        "first_question": (1, 1668, 116), "create": (1, 1508, 325), "move": (1, 704, 393), "resize": (1, 1552, 535),
        "bevel_add": (1, 1740, 552), "bevel": (1, 1729, 561), "material": (1, 1750, 562),
        "question": (2, 4056, 550)}},
    # Toolset 19 and its toolset-20 estimates use means over the complete trials
    # (a move occasionally took a lookup round: 1.06 calls), warm first call.
    "angelo_19": {"prefix": 26997, "rebase_extra": 11700, "source": "billed 40-task run, means", "kinds": {
        "first_question": (1, 1204, 92), "create": (1, 752, 266), "move": (1.06, 519, 229), "resize": (1, 751, 292),
        "bevel_add": (1, 847, 382), "bevel": (1, 847, 382), "material": (1, 940, 315),
        "question": (1, 1038, 149)}},
    "angelo_20": {"prefix": 26997 + 143, "rebase_extra": 1900, "source": "token_accounting.py, toolset 20 (unbilled)",
                  "kinds": {
        "first_question": (1, 1215, 92), "create": (1, 701, 266), "move": (1.06, 462, 203), "resize": (1, 460, 266),
        "bevel_add": (1, 538, 355), "bevel": (1, 538, 355), "material": (1, 638, 288),
        "question": (1, 598, 149)}},
    "angelo_20_short_replies": {"prefix": 26997 + 143, "rebase_extra": 1900,
                                "source": "as angelo_20, edit replies omitted (unbilled)", "kinds": {
        "first_question": (1, 1215, 92), "create": (1, 701, 236), "move": (1.06, 458, 179), "resize": (1, 437, 239),
        "bevel_add": (1, 511, 269), "bevel": (1, 511, 269), "material": (1, 552, 250),
        "question": (1, 559, 149)}},
}


def task_kinds(count):
    """The long benchmark's task kinds, in order (eval_live_tasks.build_long_session_tasks)."""
    kinds = ["first_question", "create"][:count]
    for index in range(count - len(kinds)):
        step = index % 5
        kinds.append(("move", "resize", "bevel_add" if index == 2 else "bevel", "material", "question")[step])
    return kinds


class Session:
    """Running totals of one simulated native context."""

    def __init__(self, prefix, rebase=0, rebase_extra=0):
        self.prefix, self.rebase, self.rebase_extra = prefix, rebase, rebase_extra
        self.context = prefix
        self.calls = self.read = self.written = self.output = 0
        self.cost, self.compactions, self.rebases, self.peak = 0.0, 0, 0, prefix
        self.curve = {}

    def _call(self, write, output):
        if self.context >= COMPACT_AT:
            self.compactions += 1
            self._price(self.context, 0, COMPACT_OUTPUT)
            self.context = self.prefix + COMPACT_OUTPUT
        self._price(self.context, write, output)
        self.context += write
        self.peak = max(self.peak, self.context)

    def _price(self, read, write, output):
        self.calls += 1
        self.read, self.written, self.output = self.read + read, self.written + write, self.output + output
        read_rate, write_rate, output_rate, input_rate = _call_rates(read + write + 2)
        self.cost += (read * read_rate + write * write_rate + output * output_rate + 2 * input_rate) / 1e6

    def task(self, calls, write, output):
        """One task: ``calls`` may be fractional (an occasional extra round, by its frequency)."""
        rebased = bool(self.rebase and self.context >= self.rebase)
        if rebased:
            self.rebases += 1
            self.context = self.prefix
        whole = max(1, int(calls))
        for _ in range(whole):
            self._call(write / whole + (self.rebase_extra if rebased else 0), output / whole)
            rebased = False
        if calls > whole:  # The extra round's share: one more read of the context.
            fraction = calls - whole
            self.calls += fraction
            self.read += fraction * self.context
            read_rate, _, _, input_rate = _call_rates(self.context + 2)
            self.cost += fraction * (self.context * read_rate + 2 * input_rate) / 1e6


def simulate(side, tasks, *, rebase=0):
    shape = SIDES[side]
    session = Session(shape["prefix"], rebase, shape.get("rebase_extra", 0))
    for number, kind in enumerate(task_kinds(tasks), 1):
        session.task(*shape["kinds"][kind])
        session.curve[number] = round(session.cost, 4)
    return session


def _row(session):
    return {"calls": round(session.calls, 1), "cost_usd": round(session.cost, 4), "read_tokens": round(session.read),
            "write_tokens": round(session.written), "output_tokens": round(session.output),
            "peak_context": round(session.peak), "compactions": session.compactions, "rebases": session.rebases}


def project(tasks=TASKS, rebase=0):
    """Cumulative cost per side and number of tasks; deterministic."""
    return {count: {side: _row(simulate(side, count, rebase=rebase if side.startswith("angelo") else 0))
                    for side in SIDES} for count in tasks}


def validate():
    """Modelled against measured 40-task costs: ``{"side@threshold": (model, measured, relative error)}``."""
    output = {}
    for (side, rebase), measured in MEASURED_40.items():
        model = _row(simulate(side, 40, rebase=rebase))["cost_usd"]
        output["%s@%d" % (side, rebase)] = (model, measured, round(model / measured - 1, 4))
    return output


def best_rebase(side, tasks, thresholds=range(35000, 160001, 5000)):
    """The rebase threshold with the lowest cost over ``tasks`` (0 when none beats no rebase)."""
    costs = {0: simulate(side, tasks).cost}
    costs.update({threshold: simulate(side, tasks, rebase=threshold).cost for threshold in thresholds})
    best = min(costs, key=costs.get)
    return best, round(costs[best], 4), round(costs[0], 4)


def _price_assumption(model):
    step = ("; a call whose prompt exceeds %d tokens at the higher rates (read %.2f, write %.2f, output %.2f, input "
            "%.2f): the step is per call" % ((STEP,) + ABOVE)) if STEP else ""
    return ("Prices (USD/MTok) for %s: cache read %.2f, cache write %.2f (1-hour TTL), output %.2f, uncached input "
            "%.2f (2 per call)%s, published %s (angelo_agent.model_profiles); token shapes are the Opus 5.5 runs'."
            % (model, READ, WRITE, OUTPUT, INPUT, step, model_profiles.READ_ON))


ASSUMPTIONS = [
    _price_assumption(MODEL),
    "Task sequence: the long benchmark's (a question, a cube, then move/resize/bevel/material/question cycles).",
    "mcp and angelo_18 shapes are the billed 40-task run's medians; angelo_19/20 are token_accounting.py "
    "estimates over that run's transcripts, not billed; angelo_20 assumes questions answered from the observation "
    "in one round, angelo_20_short_replies also omitted edit replies.",
    "A task's writes and outputs are split evenly over its calls; every call re-reads the whole context.",
    "Auto-compaction: one summarizing call when a context passes %d tokens (reads it, writes %d); unmeasured."
    % (COMPACT_AT, COMPACT_OUTPUT),
    "Context rebase: the prefix stays a cache hit; the first message after it writes the side's rebase_extra more "
    "tokens (measured 11,700 at toolset 19; about 1,900 at toolset 20); off by default in the product.",
]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Long-session cost model (no provider calls)")
    parser.add_argument("--tasks", type=int, nargs="*", default=list(TASKS))
    parser.add_argument("--rebase", type=int, default=0, help="Angelo context rebase threshold in tokens (0: off)")
    parser.add_argument("--model", default=MODEL, help="price the same token shapes at this model's rates")
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)
    use_model(args.model)
    ASSUMPTIONS[0] = _price_assumption(args.model)
    projection = project(args.tasks, args.rebase)
    print("Assumptions:")
    for line in ASSUMPTIONS:
        print("  -", line)
    print("\nCalibration (40 tasks, model vs measured):")
    for side, (model, measured, error) in validate().items():
        print("  %-16s $%.3f vs $%.3f (%+.1f%%)" % (side, model, measured, error * 100))
    print("\nCumulative cost USD by number of tasks [calls] (peak context; compactions; rebases):")
    print("%-6s" % "tasks" + "".join("%-30s" % side for side in SIDES))
    for count, row in projection.items():
        print("%-6d" % count + "".join("%-30s" % ("%.3f [%d] %dk%s%s" % (
            row[side]["cost_usd"], row[side]["calls"], row[side]["peak_context"] // 1000,
            " c%d" % row[side]["compactions"] if row[side]["compactions"] else "",
            " r%d" % row[side]["rebases"] if row[side]["rebases"] else "")) for side in SIDES))
    print("\nBest rebase threshold (side, tasks: threshold, cost with it, cost without):")
    rebases = {}
    for side in ("angelo_19", "angelo_20", "angelo_20_short_replies"):
        for count in (40, 100):
            rebases[side + "@" + str(count)] = best_rebase(side, count)
            print("  %-24s %3d tasks: %s" % (side, count, rebases[side + "@" + str(count)]))
    if args.json:
        Path(args.json).write_text(json.dumps({"assumptions": ASSUMPTIONS, "validation": validate(),
                                               "by_tasks": projection, "best_rebase": rebases}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
