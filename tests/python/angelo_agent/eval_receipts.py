# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-request performance receipts for the live-eval session (``eval_live_agent_session``).

Finds the receipts a chat recorded after a marker by identity (the store keeps
a bounded number), adds up Auto's escalation attempts, and derives the per-task
fields the bench reports. Counts every billed warm-up no chat made once
(``warm_ups_since``), and readies the bound chat's opening before the first
message (``begin_opening``, ``opening_wait``). Standard library only at import
(no bpy); the opening helpers import the add-on when called.
"""

import copy
import os
import time

import bench_totals

# 0 (the default, the published method): nothing is readied before the first message, whose time includes the
# runtime launch, as on the MCP side. Above 0 (--opening-wait, a labelled extra): the bound chat's opening is
# readied (codex_opening.prepare) and the first message waits for it up to this long, as a person types theirs
# seconds after launch; never in a task's time, and its warm-up is billed and counted (warm_ups_since).
OPENING_WAIT = float(os.environ.get("ANGELO_EVAL_OPENING_WAIT", "0"))


def receipt_marker(controller):
    """The newest receipt now retained (None when there is none), to find later ones by identity."""
    receipts = controller.telemetry.receipts
    return receipts[-1] if receipts else None


def receipts_since(controller, marker):
    """Every receipt this chat recorded after ``marker`` (``receipt_marker``), oldest first.

    The store retains a bounded number of receipts, so a count of earlier ones
    stops locating new receipts once it is full; the marker's identity does not.
    """
    receipts = list(controller.telemetry.receipts)
    position = next((index for index in range(len(receipts) - 1, -1, -1) if receipts[index] is marker), None)
    if position is not None:
        receipts = receipts[position + 1:]
    return [r for r in receipts if r.get("session_id") == controller.state["id"]]


def receipt_since(controller, marker):
    """The latest receipt this chat recorded after ``marker`` (``receipt_marker``)."""
    receipts = receipts_since(controller, marker)
    return copy.deepcopy(receipts[-1]) if receipts else None


# Usage fields a receipt carries only when nonzero (telemetry: a request the host cut short).
ZERO_WHEN_ABSENT = frozenset({"interrupted_turns"})


def attempts_since(controller, marker):
    """Every receipt this chat recorded after ``marker``: Auto's escalation re-run adds one.

    ``usage_all_attempts`` sums each usage field over the attempts that reached the provider (a provider turn
    or recorded usage), under the ``bench_totals`` rules: a field one of them did not record is None (unknown),
    never a partial sum; an attempt that sent nothing adds nothing; None when no attempt recorded usage.
    """
    receipts = receipts_since(controller, marker)
    sent = [r for r in receipts if (r.get("counts") or {}).get("provider_turn_requests") or r.get("usage")]
    fields = sorted({key for r in sent for key, value in (r.get("usage") or {}).items()
                     if bench_totals.is_number(value)})
    total = {key: bench_totals.known_sum(sent, lambda r, key=key: (r.get("usage") or {}).get(
        key, 0 if key in ZERO_WHEN_ABSENT and r.get("usage") else None)) for key in fields}
    return {"attempts": len(receipts), "usage_all_attempts": total or None}


def begin_opening(controller):
    """With ``OPENING_WAIT``: the chat's send path settings (``ui.prepare_controller``), then its opening readied.

    ``(thread or None, started)``; with no wait nothing is readied (the first prompt includes the runtime launch).
    """
    if OPENING_WAIT <= 0:
        return None, time.monotonic()
    from angelo_agent import codex_opening, ui
    ui.prepare_controller()
    return codex_opening.prepare(controller), time.monotonic()


def opening_wait(trial, limit=None):
    """Whether ``trial``'s first message still waits for its opening (``trial.opening``, at most ``limit``).

    When it stops waiting, just before the first message is sent, the report's
    ``opening`` records whether an opening was readied, whether a shared Codex
    app-server was already running (the app may start one when a model catalog
    loads, ``codex_opening``, even with no wait), how long the message waited
    and the states of the bases; the next calls return False at once.
    """
    if "opening" in trial.report or not hasattr(trial, "opening"):
        return False
    from angelo_agent import codex_runtime
    thread, started = trial.opening
    bases = [base["state"] for runtime in list(codex_runtime._POOL.values()) for base in runtime.bases.values()]
    limit = OPENING_WAIT if limit is None else limit
    if ((thread is not None and thread.is_alive()) or "warming" in bases) and time.monotonic() - started < limit:
        return True
    live = any(not runtime.dead for runtime in list(codex_runtime._POOL.values()))
    trial.report["opening"] = {"prepared": thread is not None, "runtime_live": live, "bases": bases,
                               "waited_s": round(time.monotonic() - started, 3), "wait_limit_s": limit}
    return False


def warm_up_cursor(store=None):
    """Where this launch's warm-up receipts begin: the newest receipt the profile's store holds now.

    Kept by reference, never by ``id()``: earlier launches' receipts (loaded
    from ``performance.json`` of a kept profile) come before it, and a held
    object's identity cannot be reused after the store evicts it.
    """
    if store is None:
        from angelo_agent import ui
        from angelo_agent.telemetry import TelemetryStore
        store = TelemetryStore.shared(ui.state_directory())
    receipts = list(store.receipts)
    return {"marker": receipts[-1] if receipts else None}


def warm_ups_since(controller, cursor):
    """Warm-up receipts (``kind`` ``warm_up``: a model request no chat made) recorded after ``cursor``.

    ``cursor`` (``warm_up_cursor``) moves past them, so each billed warm-up of
    this launch is counted exactly once and none of an earlier launch. A marker
    the bounded store has evicted means every retained receipt is newer. Usage
    sums only the fields every such receipt reported; a warm-up without usage
    leaves the sum None (unmeasured, never zero).
    """
    receipts, marker = list(controller.telemetry.receipts), cursor["marker"]
    position = next((index for index in range(len(receipts) - 1, -1, -1) if receipts[index] is marker), None)
    fresh = [r for r in receipts[0 if position is None else position + 1:] if r.get("kind") == "warm_up"]
    if receipts:
        cursor["marker"] = receipts[-1]
    usages = [r.get("usage") for r in fresh]
    total = None
    if fresh and all(isinstance(u, dict) for u in usages):
        keys = set.intersection(*(set(u) for u in usages))
        total = {key: sum(u[key] for u in usages) for key in sorted(keys) if all(type(u[key]) in {int, float}
                                                                              for u in usages)}
    # Their wall time, from each warm-up's send to its end (codex_bases.report_warm_up): part of the cold start
    # (bench_cold_warm). Unknown when a warm-up's is.
    walls = [(r.get("phases") or {}).get("total_ms") for r in fresh]
    wall = round(sum(walls), 3) if all(type(value) in {int, float} for value in walls) else None
    return {"warm_ups": len(fresh), "warm_up_usage": total, "warm_up_ms": wall if fresh else 0}


def task_extras(trial):
    """A finished task's warm-ups, what it was sent and graded with, and a prestarted runtime relabelled.

    The receipt's own ``runtime_start`` says whether the chat's provider was
    warm; when a shared Codex app-server was already running at the first send
    (a readied opening, or one the app started when a catalog loaded), the
    first task is ``prestarted``, not cold (the receipt keeps its value). The
    prompt's SHA-256 and the test code's version (``bench_integrity``) let a
    report and ``--reuse-mcp-results`` check what each trial actually had.
    """
    import bench_integrity
    extras = dict(warm_ups_since(trial.controller, trial.warm_ups),
                  **bench_integrity.trial_stamp(trial.tasks[trial.index]))
    if trial.index == 0 and (trial.report.get("opening") or {}).get("runtime_live"):
        extras["runtime_start"] = "prestarted"
    return extras


def summarize_receipt(receipt):
    """Derived per-task fields; Codex internal model rounds are never inferred."""
    if not receipt:
        return {}
    phases, counts = receipt.get("phases", {}), receipt.get("counts", {})
    claude = receipt.get("runtime") == "claude"
    return {"runtime": receipt.get("runtime"), "runtime_start": receipt.get("runtime_start"),
            "first_text_ms": phases.get("first_text_ms"), "first_scene_change_ms": phases.get("first_scene_change_ms"),
            "first_verified_result_ms": phases.get("first_verified_result_ms"), "total_ms": phases.get("total_ms"),
            "main_thread_block_ms": phases.get("main_thread_block_ms"), "approval_wait_ms": phases.get("approval_wait_ms"),
            "checkpoint_ms": phases.get("checkpoint_ms"), "provider_turn_requests": counts.get("provider_turn_requests"),
            "model_rounds": counts.get("model_rounds") if claude else None,
            "model_rounds_scope": "Claude typed-plan rounds" if claude else "unavailable: Codex internal rounds are not observable",
            "python_fallbacks": counts.get("python_fallbacks"),
            "fallback_categories": {name[len("python_fallback_"):]: value for name, value in counts.items()
                                    if name.startswith("python_fallback_") and value},
            "provider_usage": receipt.get("usage")}


class ProgressWatch:
    """When the chat's live row first showed what the model is doing, polled every 20 ms while a task runs.

    Read from what the chat draws, never from a receipt: ``first_plan_ms`` is when a typed plan began to be
    written ("Preparing the next steps"), ``first_rows_ms`` when its first provisional step was drawn
    (``plan_steps``), ``first_progress_ms`` when the live row first named anything of the model's (those steps, a
    plan's names, or a reasoning summary's title), ``first_step_ms`` when the transcript first showed a tool
    call's row (a Codex ``exec`` lookup, a Claude plan call) and ``first_thinking_ms`` when a thinking row first
    held summary text (opened rows only show it), from the moment the message was sent. With a
    ``controller`` it polls on its own Blender timer until ``record``; display timing only.
    """

    EVERY, LONGEST = 0.02, 3600.0

    def __init__(self, sent, controller=None):
        self.sent, self.marks, self._controller = sent, {}, controller
        if controller is not None:
            self._start = len(controller.state["messages"])  # The request's rows come after the ones there now.
            import bpy
            bpy.app.timers.register(self._poll, first_interval=0)

    def _poll(self):
        controller = self._controller
        if controller is None or time.monotonic() - self.sent > self.LONGEST:
            return None
        self.observe(controller.status, rows=controller.state["messages"][self._start:])
        return self.EVERY

    def observe(self, status, now=None, rows=()):
        from angelo_agent import plan_progress
        from angelo_agent.claude_stream import STATUS_PLAN
        try:  # An app from before the provisional steps (1.0 rc6) has only the names and titles.
            from angelo_agent.plan_steps import rows as planned
        except ImportError:
            planned = lambda _: ([], 0)  # noqa: E731
        base, detail = plan_progress.split(status)
        planned_rows = planned(status)[0]
        elapsed = round(((time.monotonic() if now is None else now) - self.sent) * 1000, 1)
        steps = any(row.get("role") == "activity" for row in rows)
        thought = any(row.get("role") == "thinking" and row.get("text") for row in rows)
        for name, shown in (("first_plan_ms", base == STATUS_PLAN), ("first_rows_ms", bool(planned_rows)),
                            ("first_progress_ms", base in (STATUS_PLAN, plan_progress.THINKING)
                             and bool(detail or planned_rows)), ("first_step_ms", steps),
                            ("first_thinking_ms", thought)):
            if shown:
                self.marks.setdefault(name, elapsed)

    def record(self):
        self._controller = None  # Its timer stops at its next poll.
        return dict(self.marks)
