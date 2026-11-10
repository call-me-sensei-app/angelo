# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An explicit web export that waits for its World's picture without blocking the main thread. No Blender.

An explicit export (the person's File > Export > Web, the agent's ``angelo_web_export``) carries the World's
picture, which ``web_environment`` makes on a worker thread or in a background Blender. ``web_export.export`` raises
``EnvironmentPending`` instead of waiting while it is still being made, before anything is written.
``PendingExport.step`` is then called on a timer (the operator's modal timer, the agent's pending tool call):
nothing happens until the picture's job is done, then the export runs again, reading the scene afresh: an edit made
meanwhile that changes the World waits for its own picture. Blender keeps drawing and answering input all along,
and ``cancel`` (Esc, a stopped turn) ends the wait; the picture still lands for the next export. A picture not made
after ``WAIT_SECONDS`` (a stalled worker) is left out: the export is written without it (``write(None)``: the file's
World is none, never "pending"), its environment's status is "failed", a finding says why, and the person's status
bar says so (``left_out``): an explicit export never drops the World quietly.
"""

import time

from . import web_compat

WAIT_SECONDS = 130.0   # web_environment.BAKE_SECONDS and a margin: a bake has ended, made or stopped, by then.


WORLD_LEFT_OUT = frozenset({"world_failed", "world_missing"})   # Findings of a World the file goes without.


def left_out(report):
    """The findings of a written export that say its World was left out, and why."""
    return [item for item in report.get("findings", ()) if item["code"] in WORLD_LEFT_OUT]


class EnvironmentPending(Exception):
    """The World's picture an explicit export needs is still being made; ``job`` (``web_environment``) lands it."""

    def __init__(self, job):
        super().__init__("the World's picture is still being made")
        self.job = job


class PendingExport:
    """``write(require)`` exports now and returns the report; with ``require`` True it raises ``EnvironmentPending``
    while the picture is still being made, and with None (the wait ran out) it leaves that picture out. ``step``
    returns the report once written, None while waiting."""

    def __init__(self, write, world_name="", wait_seconds=WAIT_SECONDS, clock=time.monotonic):
        self.write, self.world_name, self.clock = write, world_name, clock
        self.started = clock()
        self.deadline = self.started + wait_seconds
        self.wait_seconds = wait_seconds
        self.job = None
        self.cancelled = False

    def waiting(self):
        """True while a picture is being made and the export is not written yet."""
        return self.job is not None and not self.cancelled

    def step(self):
        if self.cancelled:
            return None
        overdue = self.clock() >= self.deadline
        if self.job is not None and not self.job.done.is_set() and not overdue:
            return None
        try:
            report = self.write(None if overdue else True)
        except EnvironmentPending as pending:
            self.job = pending.job
            return None
        report["waited_ms"] = round((self.clock() - self.started) * 1000.0, 1) if self.job is not None else 0.0
        environment = report["settings"].get("environment", {})
        if overdue and environment.get("status") == "failed" and not left_out(report):
            report["findings"].insert(0, web_compat.finding(
                "world_failed", self.world_name, reason="not made within %d s" % self.wait_seconds))
        return report

    def cancel(self):
        self.cancelled = True
