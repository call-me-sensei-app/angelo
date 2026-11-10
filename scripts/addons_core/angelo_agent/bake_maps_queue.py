# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A texture set's memory under one budget: a producer/consumer queue on one shared pool. No Blender here.

Contract: doc/angelo/long_jobs.rst, "Texture sets". The bakes (the producer, on the
main thread) hand each target's arrays over when its last bake is read; the queue
finishes them (``bake_maps_write.prepare``) and writes each file
(``bake_maps_write.encode_one``) as units on ONE pool of ``bake_maps_write.WORKERS``
threads. Everything the set holds is counted: the bake running, reserved from its
start until its scratch image is freed (``reserve_bake``, Blender's own bake
memory as ``bake_maps_steps.step_bytes`` measures it), the arrays of the target
still baking, those of targets waiting, each target's prepared channels until its
last file is written, and each unit's peak while it runs (its measured
``prepare_bytes`` or ``file_bytes``). A unit starts only while the total stays
within ``budget``, and the job starts its next bake only when there is room for
it beside the largest unit still to run (``room``), so baking and writing overlap
only within the budget. One unit always may run when nothing else (no unit, no
bake) does, so the set always moves; the job's budget is never below a target's
own smallest pipeline (``bake_maps_steps.memory_needed``), so that stays within
it too. ``peak`` is the largest total held.
"""

from . import bake_maps_write as write


class WriteQueue:
    def __init__(self, pool, budget, cancelled=lambda: False, workers=write.WORKERS):
        self.pool, self.budget, self.cancelled, self.workers = pool, budget, cancelled, workers
        self.baking = {}      # Target index -> bytes of its arrays read so far.
        self.waiting = []     # [(index, work, bytes, preparing bytes, widest file bytes)] not yet prepared.
        self.prepared = {}    # index -> {"prepared", "jobs" left, "running", "entries", "bytes", "widest"}
        self.running = {}     # future -> (kind, index, job or None, bytes)
        self.results, self.error, self.held, self.peak = {}, None, 0, 0
        self.bake = 0         # Bytes reserved for the bake running (``reserve_bake``).

    def _hold(self, count):
        self.held += count
        self.peak = max(self.peak, self.held)

    def room(self, count):
        """Whether the producer may add ``count`` bytes: they fit beside everything held and the largest unit still
        to run (``_reserve``), so the queue never has to start one beyond the budget. True when nothing is held."""
        return self.held + count + self._reserve() <= self.budget or (not self.running and self.held == 0)

    def _reserve(self):
        """The largest unit not started yet: a prepared target's next file, or a waiting target's preparation."""
        largest = 0
        for state in self.prepared.values():
            if state["prepared"] is None:   # Still preparing: its widest file is still to come.
                largest = max(largest, state["widest"])
            for job in state["jobs"]:
                largest = max(largest, write.file_bytes(state["prepared"], job))
        for _index, _work, _count, preparing, widest in self.waiting:
            largest = max(largest, preparing, widest)
        return largest

    def reserve_bake(self, count):
        """A bake starts: hold ``count`` bytes until ``release_bake`` (its scratch image freed)."""
        self.release_bake()
        self.bake = count
        self._hold(count)

    def release_bake(self):
        self.held -= self.bake
        self.bake = 0

    def hold_baked(self, index, count):
        """Arrays of a bake just read for target ``index``. The bake's reservation already counts the arrays it reads
        back (``bake_maps_steps.step_bytes``), so they move out of it to the target and are not held twice; only
        what the reservation did not cover is added."""
        moved = min(count, self.bake)
        self.bake -= moved
        self.baking[index] = self.baking.get(index, 0) + count
        self._hold(count - moved)

    def hand_over(self, index, work):
        """Target ``index`` has all its arrays: queue it for writing."""
        self.waiting.append((index, work, self.baking.pop(index, 0), write.prepare_bytes(work),
                             write.widest_file_bytes(work)))
        self.pump()

    def busy(self):
        return bool(self.waiting or self.prepared or self.running)

    def pump(self):
        """Collect finished units, then start what fits: files of prepared targets first (they free memory), then
        the next target's preparation."""
        for future in [future for future in self.running if future.done()]:
            self._collect(future)
        for index, state in list(self.prepared.items()):
            while state["jobs"] and len(self.running) < self.workers:
                cost = write.file_bytes(state["prepared"], state["jobs"][0])
                if not self._fits(cost):
                    break
                job = state["jobs"].pop(0)
                self._start("encode", index, job, cost, write.encode_one, state["prepared"], job, self.cancelled)
                state["running"] += 1
        while self.waiting and len(self.running) < self.workers:
            index, work, count, preparing, widest = self.waiting[0]
            if not self._fits(preparing):
                break
            self.waiting.pop(0)
            self._start("prepare", index, None, preparing, write.prepare, work, self.cancelled)
            self.prepared[index] = {"prepared": None, "jobs": [], "running": 1, "entries": [], "bytes": count,
                                    "widest": widest}

    def _fits(self, cost):
        return self.held + cost <= self.budget or not (self.running or self.bake)

    def _start(self, kind, index, job, cost, function, *args):
        self._hold(cost)
        self.running[self.pool.submit(function, *args)] = (kind, index, job, cost)

    def _collect(self, future):
        kind, index, _job, cost = self.running.pop(future)
        state = self.prepared[index]
        self.held -= cost
        state["running"] -= 1
        try:
            result = future.result()
        except Exception as error:   # Said by the job's finish; nothing is published.
            self.error = self.error or error
            result = None
        if kind == "prepare":
            self.held -= state["bytes"]   # The handed-over arrays are gone; the channels stay until written.
            state["bytes"] = write.array_bytes(result["channels"]) if result else 0
            self._hold(state["bytes"])
            state["prepared"], state["jobs"] = result, list(result["jobs"]) if result else []
        else:
            state["entries"] += result or []
        if not state["jobs"] and not state["running"]:
            self.held -= state["bytes"]
            facts = state["prepared"]["facts"] if state["prepared"] else {}
            self.results[index] = (state["entries"], facts)
            del self.prepared[index]

    def progress(self):
        """The written share of the files known so far (0 to 1)."""
        done = sum(len(entries) for entries, _facts in self.results.values())
        pending = sum(len(state["jobs"]) + state["running"] for state in self.prepared.values()) + len(self.waiting)
        return done / max(1, done + pending)
