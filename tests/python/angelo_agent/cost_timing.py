# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a cost check compares one piece of work at different sizes (``test_blender_web_link_cost``); stdlib only.

Load from other processes only ever adds time: another suite's burst, a preemption or a busy cache makes some
repeats slower, never faster. So a check of how work grows with size judges each size by its best repeat (the
least time), and the sizes take turns, one repeat of each per round in a rotating order, so a burst that lasts
seconds slows every size alike instead of the one being timed then. A real regression slows every repeat of a size,
its best included, and still fails. The bound itself is the check's own and is never widened to fit a run.

1.1.0 candidate ce9ddce (Windows build3, four background suites at once): the median of 9 at 10,000 instancers,
timed after the 1,000 ones had finished, read 147.7 ms against 8.24 ms (bound 128.6 ms) while the same check passed
in its ten earlier runs. ``test_native_cost_timing.py`` replays such load against this rule, deterministically.
"""

import time


def best_ms(works, rounds, warm=1, clock=time.perf_counter):
    """The least time each of ``works`` ({name: callable}) took, in milliseconds rounded to 0.001, over ``rounds``
    rounds after ``warm`` untimed ones. Each round runs every work once; round ``k`` starts at the ``k``-th work, so
    no work always runs first or after the same neighbour."""
    names = list(works)
    for _ in range(warm):
        for name in names:
            works[name]()
    best = {name: float("inf") for name in names}
    for index in range(rounds):
        start = index % len(names)
        for name in names[start:] + names[:start]:
            started = clock()
            works[name]()
            best[name] = min(best[name], (clock() - started) * 1000.0)
    return {name: round(value, 3) for name, value in best.items()}


def bounded(small, large, factor, plus_ms):
    """Whether the larger size's time is at most ``factor`` times the smaller's plus ``plus_ms``: the bound of a
    check such as "grows linearly" (``factor`` = 1.5 x the size ratio) or "stays flat" (``factor`` = 2)."""
    return large <= factor * small + plus_ms
