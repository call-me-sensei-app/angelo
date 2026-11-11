# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""When the engine link sends a live frame (``engine_link._tick``; doc/angelo/engine_link.rst, "Cadence"). No Blender.

A frame goes out at the first change after a pause (the leading edge), then at most once per ``gap_seconds`` while
changes keep coming, and the last change always goes out: a frame is due whenever a change waits and the gap since
the last export has passed. Each frame holds every change made since the one before, so nothing queues in Angelo.

* **The gap** is at least ``MIN_GAP_SECONDS`` and ``SHARE`` times the last export's time (at most
  ``MAX_GAP_SECONDS``): the exports never take more than about 1/(SHARE + 1) of the main thread, however large the
  scene. It runs from the end of the last export, so a slow export never runs back to back.
* **Acknowledgements hold the next frame**: while an editor that acknowledges (capability ``acks``) and got the last
  frame hasn't answered it, no new frame goes, for up to ``ACK_WAIT_SECONDS``. A page or engine that applies slower
  than Angelo exports gets the newest state when it is ready instead of a queue of stale frames, so its lag stays one
  frame, never growing while the edits go on.
* **A forced frame** (``dirty_at`` 0.0: the link began, an editor joined, a page asked for a whole frame, a page fell
  behind) goes at once, whatever the gap or the answers.

Before 1.0.11 the link waited for the scene to be still (0.35 s, or three times the last export): a fade, a dragged
slider or an agent's animation showed nothing until it stopped (the 1.0.10 demo: a 3 s colour fade reached the page
as one jump, 0.4 s after it ended). The cadence's floor is the link's tick, from measurement (doc/angelo/web_link.rst,
"Cadence"): a one-object delta exports in a few milliseconds and a page answers it within one tick.
"""

MIN_GAP_SECONDS = 0.05    # Half the link's tick (engine_link.POLL_SECONDS), so a cheap frame can go at every tick.
SHARE = 3.0               # Wait this many times the last export's time: exports take at most about a quarter.
MAX_GAP_SECONDS = 10.0
ACK_WAIT_SECONDS = 1.0    # An editor that hasn't answered the last frame by then gets the next one anyway.

from .engine_link_facts import preview_ack   # noqa: E402  Plain Python, like this module.

FORCED = 0.0   # ``dirty_at`` of a frame that goes at once.

# The link's state these read (``engine_link._state``): ``dirty_at``, when the oldest unsent change was noted
# (``FORCED``: send at once); ``exported_at``, when the last live export ended; ``awaiting``, the acknowledging
# editors the last frame reached (``engine_link_live.publish``); ``facts``, their answers (``engine_link_facts``).


def gap_seconds(last_ms, minimum=MIN_GAP_SECONDS, share=SHARE, maximum=MAX_GAP_SECONDS):
    """The time from the end of one export to the start of the next while changes keep coming."""
    return min(maximum, max(minimum, share * max(0.0, last_ms) / 1000.0))


def due(now, dirty_at, exported_at, last_ms, waiting_since=None):
    """Whether a live frame should be exported now.

    ``dirty_at``: when the oldest change still unsent was noted (None: nothing waits; ``FORCED``: at once);
    ``exported_at``: when the last export ended (None: none yet in this link); ``last_ms``: that export's time;
    ``waiting_since``: when the last frame went, while an acknowledging editor that got it hasn't answered it (None:
    every one did, or none acknowledges)."""
    if dirty_at is None:
        return False
    if dirty_at == FORCED:
        return True
    if waiting_since is not None and now - waiting_since < ACK_WAIT_SECONDS:
        return False
    return exported_at is None or now - exported_at >= gap_seconds(last_ms)


def waiting_since(state, connected):
    """When the last live frame went, while an acknowledging editor it reached, still in ``connected``, hasn't
    answered it (``previewed`` or ``preview_failed`` of that version or a newer one); else None. Once every one has
    answered, ``awaiting`` empties, so nothing is read again until the next frame."""
    push, waiting = state["facts"]["push"], state["awaiting"]
    if push is None or not waiting:
        return None
    if any(client in connected and preview_ack(state["facts"], client, push["version"], push["at"]) is None
           for client in waiting):
        return push["at"]
    state["awaiting"] = frozenset()
    return None
