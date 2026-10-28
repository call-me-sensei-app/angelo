# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""When Angelo reads a worker's newest part again for its live view: never in the middle of the artist's own work.

A read (``live_view.refresh``) loads the part file into a private Main and evaluates it: a cost of the part alone,
never of the artist's document (measured in ``bench_live_view.py``), but still main-thread time. So this policy reads
only when no tool, navigation or render/bake job holds the window (``held``), at most every ``ACTIVE_INTERVAL`` for
the session shown full screen and every ``RAIL_INTERVAL`` for the others, and only after the artist has been idle for
``IDLE`` seconds unless the save has waited ``MAX_DEFER`` (then it runs during input, when its cost fits in a frame).
Each tick reads the first part ``choose`` picks, then more while their reads together fit in a frame (``batch``), so
four workers whose saves turn due together are read in one tick, not one per tick (break-it P1-8 P4).

When more saves are due than fit in a frame (eight or more workers at a few ms a read), a tick that read leaves the
rest to the next tick ``BACKLOG`` later instead of ``POLL`` (``next_poll``), still a frame of reads a tick: 4 ms reads
stay within the bound below up to ten workers and add at most about 0.05 s at sixteen (host-tested; break-it P1-20
M4: the count now follows the machine, up to 16).

So a committed edit is read within ``max(RAIL_INTERVAL, MAX_DEFER) + POLL`` (0.7 s) whatever the input, its picture
is drawn in the same tick, and the rail is drawn again by ``rail_redraw``'s fact pacing (0.25 s on a cheap view, at
most ``rail_redraw.FACT_WAIT`` on a heavy one): about half a second commit to screen on a cheap view with the artist
idle, at most about 1.5 s on a heavy view under continuous input (the coordinator's ~1 s rule, 2026-10-24); never
batched behind input. Two things still wait,
by design: a tool or a job holding the window (``held``: nothing overrides it, a read never races them, break-it
P1-1 H1), and a part so heavy that reading it more often would take more than 5% of the main thread (``SHARE``).

Pure: no Blender, host-tested. ``parallel_sessions`` keeps the state and calls ``batch`` from its timer.
"""

IDLE = 0.25            # Seconds without input before a reload may run.
ACTIVE_INTERVAL = 0.5  # The live view the artist watches: at most two reloads a second.
RAIL_INTERVAL = 0.6    # A tile's picture: at most one reload per worker every 0.6 s (with the poll, read within 0.7 s;
                       # the redraw that shows it is paced by ``rail_redraw``).
MAX_DEFER = 0.6        # A save waiting this long is read even while the artist gives input (never while held).
POLL = 0.1             # The timer's period while saves wait (a dictionary walk: no cost worth measuring).
BACKLOG = 0.032        # Its period after a tick that read and left reads waiting: two frames, a frame of reads each.
SHARE = 20.0           # A session is read again no sooner than this many times its last cost: at most 5% of the
                       # main thread per part, however heavy it is (a heavy part's next draw costs 0.3 s, measured).
FRAME = 0.016          # A late save runs during input only when its cost fits in one frame.


def choose(pending, last, active, now, idle_for, held, costs=None):
    """The session to reload now, or None.

    ``pending``: ``{session: when its newest unreloaded save arrived}``; ``last``: ``{session: when it last
    reloaded}``; ``active``: the session shown full screen (or None); ``idle_for``: seconds since the artist's last
    input; ``held``: a tool, a navigation or a job holds the window (nothing runs then); ``costs``: ``{session: seconds
    its last read and draw took}``. Overdue saves first (oldest first), then the active session, then the oldest
    other save."""
    costs = costs or {}
    ready = []
    for session, since in pending.items():
        cost = costs.get(session, 0.0)
        interval = max(ACTIVE_INTERVAL if session == active else RAIL_INTERVAL, SHARE * cost)
        if now - last.get(session, float("-inf")) < interval:
            continue
        overdue = now - since >= MAX_DEFER and cost <= FRAME
        if held or (not overdue and idle_for < IDLE):
            continue
        ready.append((not overdue, session != active, since, session))
    return min(ready)[3] if ready else None


def batch(pending, last, active, now, idle_for, held, costs=None):
    """The sessions to reload in this tick, in order: ``choose``'s pick, then the next picks while their last costs
    together stay within one frame (``FRAME``); a part never read yet counts as a whole frame (break-it P1-9 Q5). The
    first read is never refused for its cost: ``choose`` already paces a heavy part (``SHARE``) and runs a late one
    during input only when it fits in a frame."""
    costs = costs or {}
    waiting, chosen, spent = dict(pending), [], 0.0
    while waiting:
        session = choose(waiting, last, active, now, idle_for, held, costs)
        cost = costs.get(session, FRAME)
        if session is None or (chosen and spent + cost > FRAME):
            break
        chosen.append(session)
        spent += cost
        del waiting[session]
    return chosen


def next_poll(read_any, waiting):
    """Seconds until the read timer looks again: sooner (``BACKLOG``) after a tick that read while saves still wait, so
    a burst of many workers' saves drains a frame of reads at a time; else ``POLL``; None when nothing waits."""
    if not waiting:
        return None
    return BACKLOG if read_any else POLL
