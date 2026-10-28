# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One UI tick's tool dispatch across every chat (moved out of ``controller``, which re-exports it).

A chat bound to a worker Blender takes its turn on its own lane (``worker_dispatch``), so the tick never waits for a
worker's answer; every other chat pumps here, on the main thread, within the tick's shared budget. The lanes share it
too: each step gets what the tick has left, and once it is spent the next chat goes first next tick (break-it E2E-1
L-2: four lanes with a budget each reached 13 ms in one tick).

Another chat's pump with work is an away region (``worker_dispatch.away``): its tools (a view, a script, a merge) may
hold the main thread for seconds, and the lanes carry on meanwhile; ``back`` reclaims them before the next chat's turn.
"""
import time

from . import worker_dispatch


def pump_controller_tools(controllers, cursor, executor, document, apply_mutation, commit_mutation=None, budget=None):
    """Share one main-thread budget (one UI tick's unless given) and resume after the last serviced chat."""
    from .controller import ToolDispatchBudget
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
            if worker_dispatch.pump(controller, budget):   # On its lane, with what the tick has left.
                cursor = (index + 1) % len(controllers)
            continue
        admissions = budget.admissions
        region = worker_dispatch.away(controller)   # None while no worker chat has a lane, or this chat no work.
        try:
            controller.pump_tools(executor, document,
                apply_mutation=lambda call, target=controller: apply_mutation(target, call), budget=budget,
                commit_mutation=(lambda call, target=controller: commit_mutation(target, call)) if commit_mutation else None)
        finally:
            worker_dispatch.back(region)
        if budget.admissions != admissions:
            cursor = (index + 1) % len(controllers)
        if budget.exhausted:
            break
    return cursor
