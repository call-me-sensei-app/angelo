# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a Claude turn tells the person where it ends, never as a failed turn.

Self-checks still open (``self_checks.SelfChecks``): however the turn ends, the person reads which, as facts.

Jobs are never held for a self-check, so no job is ever owed here (doc/angelo/agent_execution.rst, "The script kit
and self-checks"). The stated-names round (a name prefix the request states that no object has) is not in 1.0: it
told the model how to name its parts, and stays on its branch.
"""


class TurnEndings:
    def __init__(self, checks=None):
        self.checks, self.told = checks, False

    def done(self, emit, owner, current):
        """A completed ending: the person reads the self-checks still open, once (no round, never a failed turn)."""
        row = self.checks.row() if self.checks is not None and current and not self.told else None
        if row:
            self.told = True
            emit(owner, "message", row)
        return "completed" if current else "interrupted"

    def around(self, run, emit, owner, current):
        """``run()`` a turn's plans; however it ends (failed, stopped by its round cap or a refusal, an error), the
        person reads the self-checks still open (``done``), unless the request no longer owns the turn."""
        try:
            return run()
        finally:
            self.done(emit, owner, current())
