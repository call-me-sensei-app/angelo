# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The newest value of a live display that streams: shown at most every so often, never lost.

The live row redraws from values that change as a model streams (a plan's status and provisional steps as its
JSON is written, a script's newest lines): the first shows at once and later changes at most every ``seconds``,
so a fast stream costs a few redraws a second. A change inside that window is held, not dropped: the next
change after it shows the newest, and the end of what streams (``flush``: the plan call's block stopped, the
round's result) shows the one still held. Without that the display ends on whatever showed last before the
window, at any pace: a plan whose last change lands within half a second of its end kept "Rail" on the row
instead of "Rail ×2" (the 1.0.8 timing sweep's batched footbridge at Opus pace).

Used by ``claude_stream`` and ``codex_plan``. Display only. No ``bpy``: host-testable.
"""

import time


class Latest:
    """One display's newest value: the first change at once, later ones at most every ``seconds``, the held one
    on ``flush``. ``shown`` is what the display shows before the first change. Values are never None."""

    def __init__(self, seconds, shown=None):
        self.seconds, self.shown, self._at, self._held = seconds, shown, None, None

    @property
    def started(self):
        """Whether the display has shown a value of its own (not only ``shown`` from before the first change)."""
        return self._at is not None

    def offer(self, value):
        """``value`` when the display shows it now, else None (held when it differs from what shows)."""
        if value == self.shown:
            self._held = None
            return None
        now = time.monotonic()
        if self._at is not None and now - self._at < self.seconds:
            self._held = value
            return None
        self.shown, self._at, self._held = value, now, None
        return value

    def flush(self):
        """The held value, now shown, or None when the display already shows the newest."""
        value, self._held = self._held, None
        if value is not None:
            self.shown, self._at = value, time.monotonic()
        return value
