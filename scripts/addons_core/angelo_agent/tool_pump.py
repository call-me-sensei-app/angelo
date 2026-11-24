# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One UI tick's tool dispatch across every chat (moved out of ``controller``, which re-exports it).

A chat bound to a worker Blender takes its turn on its own lane (``worker_dispatch``), so the tick never waits for a
worker's answer; every other chat pumps here, on the main thread, within the tick's shared budget. The lanes share it
too: each step gets what the tick has left, and once it is spent the next chat goes first next tick (break-it E2E-1
L-2: four lanes with a budget each reached 13 ms in one tick).

Another chat's pump with work is an away region (``worker_dispatch.away``): its tools (a view, a script, a merge) may
hold the main thread for seconds, and the lanes carry on meanwhile; ``back`` reclaims them before the next chat's turn.

A chat whose step raises is reported once (``pump_guard.failed``), the next chat goes first next tick, and its turn
ends once with an error row the person sees (``_end_failed_turn``): one chat's failure never stalls another's, and
never leaves its own busy with only a console line.
"""
import threading
import time
from dataclasses import dataclass, field

from . import pump_guard, worker_dispatch


def _end_failed_turn(controller, error):
    """``controller``'s turn, its tool step having raised ``error``: an error row (once per turn) and a stop. A stop
    that raises falls back to cancelling the turn and stopping its provider; until one of them took, the next failed
    tick tries again (the row is not shown again)."""
    turn = (getattr(controller, "_epoch", None), getattr(controller, "_request_generation", None))
    if not getattr(controller, "busy", False) or getattr(controller, "_tool_step_failed", None) == turn:
        return
    redact = _redact(controller)
    if getattr(controller, "_tool_step_shown", None) != turn:
        try:
            from .crash_resume_ui import FAILED
            from .ui_text import notice
            text = pump_guard.redacting(redact)(str(error) or type(error).__name__)[:300]   # Never a whole traceback.
            controller.emit("message", {"role": "error", **notice(FAILED, error=text)})
            controller._tool_step_shown = turn
        except Exception as failure:  # noqa: BLE001 - showing it is best effort; its report stands.
            pump_guard.failed("showing a failed turn", failure, redact, _session(controller))
    try:
        controller.stop("Angelo could not continue this request; no further operation was applied")
    except Exception as failure:  # noqa: BLE001 - the stop itself failed: end the turn directly.
        pump_guard.failed("ending a failed turn", failure, redact, _session(controller))
        try:
            controller.cancelled = True
            provider = getattr(controller, "provider", None)
            if provider is not None:
                threading.Thread(target=provider.stop, daemon=True, name="AngeloFailedTurnStop").start()
        except Exception as fallback:  # noqa: BLE001 - nothing took: the next failed tick tries again.
            pump_guard.failed("ending a failed turn", fallback, redact, _session(controller))
            return
    controller._tool_step_failed = turn


def _failed(controller, error, raise_failures):
    """A chat's step raised: its caller's to handle (``raise_failures``), or reported and its turn ended."""
    if raise_failures:
        raise error
    pump_guard.failed("the tool step", error, _redact(controller), _session(controller))
    _end_failed_turn(controller, error)


def _session(controller):
    return (getattr(controller, "state", None) or {}).get("id")


def _redact(controller):
    return getattr(controller, "redact", str)


@dataclass
class ToolDispatchBudget:
    """One native operation per UI tick, including across background chats.

    A running native operation remains atomic. The deadline limits admission
    and preflight work between operations; it cannot interrupt native code.
    ``owed`` (default True): the tick's first admission is never refused for
    time, so a chat whose own setup (its scene and tool holds, which admit
    nothing) outlasts the tick on a loaded machine still serves its work. One
    admission at most runs past the deadline. ``pump_controller_tools`` passes
    the debt on past chats that neither admit nor reach the deadline (an idle
    chat) and ends it at the first that does. A one-chat budget (a lane's,
    ``_free_step``'s, ``pump_tools``' default) never ends it: one chat, one
    late admission.
    """
    seconds: float = 0.008
    max_admissions: int = 8
    max_executions: int = 1
    started: float = field(default_factory=time.monotonic)
    admissions: int = 0
    executions: int = 0
    owed: bool = True

    @property
    def exhausted(self):
        if self.admissions >= self.max_admissions or self.executions >= self.max_executions:
            return True
        return time.monotonic() - self.started >= self.seconds and not (self.owed and self.admissions == 0)


def _settle_debt(budget, admitted):
    """The tick's one late admission (``ToolDispatchBudget.owed``) passes on past a chat that neither admitted nor
    reached the deadline (an idle chat), so it goes to the first chat whose own setup spends the tick."""
    if admitted or time.monotonic() - budget.started >= budget.seconds:
        budget.owed = False


def pump_controller_tools(controllers, cursor, executor, document, apply_mutation, commit_mutation=None, budget=None,
                          raise_failures=False):
    """Share one main-thread budget (one UI tick's unless given) and resume after the last serviced chat.
    ``raise_failures``: a caller pumping one chat for its own answer (a worker's shadow edit) gets a step's error."""
    from .worker_merge_gate import sweep
    from .worker_part_counts import pump as count_parts
    sweep()   # A merge's holds and leases whose merge left its chat are released (A').
    count_parts()   # Parts' object counts read under the leases granted since (A'): no window waits on a worker.
    if not controllers:
        return 0
    budget = budget or ToolDispatchBudget()
    start = cursor % len(controllers)
    for offset in range(len(controllers)):
        index = (start + offset) % len(controllers)
        controller = controllers[index]
        if worker_dispatch.owns(controller):
            if time.monotonic() - budget.started >= budget.seconds:
                cursor = index   # The tick's time is spent: this chat goes first next tick.
                break
            admitted = False
            try:
                admitted = worker_dispatch.pump(controller, budget)   # On its lane, with what the tick has left.
                if admitted:
                    cursor = (index + 1) % len(controllers)
            except Exception as error:  # noqa: BLE001 - one chat's failure never stalls another's.
                _failed(controller, error, raise_failures)
                cursor = (index + 1) % len(controllers)
            finally:
                _settle_debt(budget, admitted)
            continue   # One that spent the tick: the next chat, reached spent, goes first next tick (below).
        admissions, spent, failed = budget.admissions, budget.exhausted, False   # Spent: reached with no time left.
        region = worker_dispatch.away(controller)   # None while no worker chat has a lane, or this chat no work.
        try:
            controller.pump_tools(executor, document,
                apply_mutation=lambda call, target=controller: apply_mutation(target, call), budget=budget,
                commit_mutation=(lambda call, target=controller: commit_mutation(target, call)) if commit_mutation else None)
        except Exception as error:  # noqa: BLE001 - one chat's failure never stalls another's: the next goes first.
            _failed(controller, error, raise_failures)
            failed = True
        finally:
            worker_dispatch.back(region)
            _settle_debt(budget, budget.admissions != admissions)
        # A chat that failed, admitted work, or ran while the tick had time and spent it, goes to the back; one reached
        # with the tick already spent goes first next tick. Moving only on an admission let a step that spent the tick
        # admitting nothing (a loaded machine) go first every tick, and no later chat ever ran.
        exhausted = budget.exhausted
        if failed or budget.admissions != admissions or exhausted and not spent:
            cursor = (index + 1) % len(controllers)
        elif spent:
            cursor = index
        if exhausted:
            break
    return cursor
