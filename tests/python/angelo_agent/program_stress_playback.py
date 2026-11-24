# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The program stress suite's injected playbacks, each with its own receipt (test_blender_program_stress).

Playback starts inside a program's approval and stops only after the controller's staging probe
(``Controller._waiting_for_playback``) has seen it running and held the program, and after it has moved the current
frame (what makes playback a change for the program: the suite's ``frame_rebinds`` check counts on it); nothing stops
it on a clock. A
playback that ends any other way (its task ended before any probe, or Blender stopped it) is a miss, named by its
receipt. A fixed 0.25 s stop timer raced the probe: when the UI tick that approved a program had spent its dispatch
budget, the probe came on a later tick, after the timer had stopped playback, and the wait was missed although the
fencing held (2.1.0 Mac GUI round on 93d6a3d3336: 79 waits for 80 injections, the run's only failed check).

The probe is observed, never replaced: its answer is the product's, and the receipts' ``playback_waits`` count must
still cover every injection. Standard library only; the caller passes Blender's playback controls in.
"""

STOPPED_AFTER_WAIT = "after_wait"   # The controller held staging; this suite then stopped playback.
STOPPED_AT_TASK_END = "task_end"     # The task ended with this playback never seen by a probe.
STOPPED_ELSEWHERE = "elsewhere"     # Playback was no longer running when a probe or the task's end looked.


class PlaybackInjections:
    """Each injected playback from its start until the controller's wait lets it stop.

    ``start()`` starts playback, ``stop()`` stops it, ``playing()`` says whether it runs, ``frame()`` is the current
    frame and ``later(fn)`` runs ``fn`` once on a later main-loop pass (a timer; never inside the probe, which runs in
    the controller's pump).
    """

    def __init__(self, start, stop, playing, frame, later):
        self._start, self._stop, self._playing, self._frame, self._later = start, stop, playing, frame, later
        self.receipts = {}   # call id -> receipt, in injection order.
        self._current = None   # The receipt whose playback this suite started last.

    def inject(self, call_id, **label):
        """Start playback for ``call_id``'s program; False when it did not start. ``label`` names it in a miss."""
        if call_id in self.receipts:
            raise ValueError("playback was already injected for " + call_id)
        if self._playing():   # Blender's play operator toggles: it would stop a running playback instead.
            raise RuntimeError("playback was already running before " + call_id + "'s injection")
        self._start()
        receipt = dict(label, call=call_id, frame=self._frame(), started=bool(self._playing()), probes=0,
                       waited=False, moved=False, stopped=None)
        self.receipts[call_id] = self._current = receipt
        return receipt["started"]

    def observed(self, call_id, waiting):
        """The controller's probe answered ``waiting`` for ``call_id``; on its first hold, stop on a later pass."""
        receipt = self.receipts.get(call_id)
        if receipt is None or receipt["stopped"] is not None:
            return
        receipt["probes"] += 1
        if not waiting:
            # Not held although this suite stops nothing before a hold: playback already ended some other way.
            receipt["stopped"] = STOPPED_ELSEWHERE
            return
        if not receipt["waited"]:
            receipt["waited"] = True
            self._later(lambda: self._stop_after_wait(receipt))

    def _stop_after_wait(self, receipt):
        if receipt["stopped"] is not None or receipt is not self._current:   # Never a later injection's playback.
            return
        if not self._playing():
            receipt["stopped"] = STOPPED_ELSEWHERE
            return
        if self._frame() == receipt["frame"]:   # Held, but playback has not moved the frame yet: the next pass.
            self._later(lambda: self._stop_after_wait(receipt))
            return
        receipt["moved"], receipt["stopped"] = True, STOPPED_AFTER_WAIT
        self._stop()

    def task_ended(self, **label):
        """The task ended: name its receipts and stop a playback no probe ever saw, so none outlives its task."""
        running = bool(self._playing())
        for receipt in self.receipts.values():
            if receipt.get("task") is None and label:
                receipt.update(label)
            if receipt["stopped"] is None:
                receipt["stopped"] = STOPPED_AT_TASK_END if running else STOPPED_ELSEWHERE
        if running:
            self._stop()

    def misses(self):
        """The receipts of injections the controller never held staging for, in injection order."""
        return [dict(receipt) for receipt in self.receipts.values() if not receipt["waited"]]

    def summary(self):
        return {"injections": len(self.receipts), "waited": sum(r["waited"] for r in self.receipts.values()),
                "misses": self.misses()[:20]}
