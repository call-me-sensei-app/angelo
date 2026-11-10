# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The end of a chat's message (``Controller._after_turn``: Auto continues nothing more), and what waits for it.

In order: a held goal's round ends (``goal_loop.turn_ended``: its working time and spend count, then whether the goal
is due), then a worker chat's turn is one sample (``worker_facts.turn_ended``). Never while Auto re-runs a turn.
"""


def ended(controller, data):
    """``data``: the turn's end (``status``, ``not_sent``). Returns what ``worker_facts.turn_ended`` returns."""
    from . import goal_loop, worker_facts
    goal_loop.turn_ended(controller, data)
    return worker_facts.turn_ended(controller, data.get("status"))
