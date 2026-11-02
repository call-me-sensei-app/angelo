# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bake every map at once without Blender: the memory a texture set really uses against its budget.

``tracemalloc`` traces every array numpy and zlib allocate (Python's raw allocator
included), so these tests measure what the writing units hold at their peak, not
the queue's bookkeeping: each file and each preparation within the estimate the
queue charges for it (``bake_maps_write.file_bytes``, ``prepare_bytes``), and a
whole set, written as the job writes it while stand-in bakes hold their reserved
memory, within its budget. Blender's own bake memory (``bake_maps_steps.step_bytes``)
lives outside Python; its measurement is described there.
"""

import sys
import tempfile
import time
import tracemalloc
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import bake_maps_queue as queue_module  # noqa: E402
from angelo_agent import bake_maps_write as write  # noqa: E402
from angelo_agent.bake_maps_spec import HIGH_POSITION, LOW_NORMAL, LOW_POSITION  # noqa: E402

GREY = (("ROUGHNESS", 0, ""),)
RGB = (("NORMAL", 0, ""), ("NORMAL", 1, ""), ("NORMAL", 2, ""))
RGBA = (("BASE_COLOR", 0, ""), ("BASE_COLOR", 1, ""), ("BASE_COLOR", 2, ""), ("ALPHA", 0, "invert"))
EVERY = ("BASE_COLOR", "METALLIC", "ROUGHNESS", "ALPHA", "EMISSION", "HEIGHT", "NORMAL", "AO")


def traced_peak(function):
    """Bytes ``function`` held at its peak beyond what was live when it started (everything it frees counts)."""
    tracemalloc.start()
    try:
        function()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def tiles_of(rng, size, tiles, width, scale=1.0, offset=0.0):
    numbers = list(range(1001, 1001 + tiles)) if tiles > 1 else [None]
    return [(number, (rng.random((size, size, width), dtype=numpy.float32) * scale + offset).astype(numpy.float32))
            for number in numbers]


def sums_of(case, rng, size, tiles):
    """A target's handed-over sums: every channel weighted, a packed bake's views, constants over a varying weight,
    or a high-to-low height's positions."""
    arrays = lambda width, scale=1.0, offset=0.0: tiles_of(rng, size, tiles, width, scale, offset)  # noqa: E731
    if case == "weighted":
        return {"BASE_COLOR": arrays(3), "ROUGHNESS": arrays(1), "METALLIC": arrays(1), "WEIGHT": arrays(1, 0.9, 0.05),
                "TRANSPARENCY": arrays(1), "EMISSION": arrays(3, 4.0), "HEIGHT": arrays(1, 2.0, -1.0),
                "NORMAL": arrays(3), "AO": arrays(1)}
    if case == "packed":
        base = arrays(3)
        return {"BASE_COLOR": arrays(3), "WEIGHT": (1.0, 1.0, 1.0),
                **{key: [(n, a[..., i:i + 1]) for n, a in base]
                   for i, key in enumerate(("ROUGHNESS", "METALLIC", "AO"))}}
    if case == "constants":
        return {"BASE_COLOR": (0.8, 0.2, 0.1), "METALLIC": (1.0,) * 3, "ROUGHNESS": (0.3,) * 3,
                "WEIGHT": arrays(1, 0.9, 0.05), "EMISSION": (3.0, 2.0, 1.0), "HEIGHT": (0.2,) * 3}
    return {HIGH_POSITION: arrays(4), LOW_POSITION: arrays(4), LOW_NORMAL: arrays(4), "BASE_COLOR": arrays(3),
            "WEIGHT": (1.0, 1.0, 1.0)}


CHANNELS = {"weighted": EVERY, "packed": ("BASE_COLOR", "METALLIC", "ROUGHNESS", "AO"),
            "constants": ("BASE_COLOR", "METALLIC", "ROUGHNESS", "EMISSION", "HEIGHT"),
            "height": ("BASE_COLOR", "HEIGHT")}


def work_of(case, size, tiles, image_format, folder, packing="SEPARATE", index=0):
    rng = numpy.random.default_rng(index)
    return {"target": "T%d" % index, "stem": "T%d" % index,
            "tiles": list(range(1001, 1001 + tiles)) if tiles > 1 else [], "size": (size, size),
            "sums": sums_of(case, rng, size, tiles), "channels": list(CHANNELS[case]), "folder": folder,
            "request": {"packing": packing, "format": image_format}}


class UnitPeakTests(unittest.TestCase):
    """Each unit's traced peak within what the queue charges for it."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def test_every_file_peaks_within_its_estimate(self):
        for width, height, tiles in ((48, 40, 1), (512, 512, 1), (256, 128, 3), (8192, 40, 1)):
            rng = numpy.random.default_rng(width)
            pixels = rng.random((height * tiles, width, 4), dtype=numpy.float32)
            channels = {"BASE_COLOR": pixels[..., :3], "ALPHA": pixels[..., 3:], "ROUGHNESS": pixels[..., 1:2],
                        "NORMAL": pixels[..., 1:4]}
            for image_format, depths in (("PNG", (8, 16)), ("OPEN_EXR", (16, 32))):
                prepared = {"channels": channels, "shape": (height * tiles, width), "height": height,
                            "tiles": list(range(1001, 1001 + tiles)) if tiles > 1 else [None], "stem": "T",
                            "folder": Path(self.folder), "format": image_format,
                            "extension": write.FORMATS[image_format]}
                for slots, colour in ((GREY, False), (RGB, False), (RGBA, True), (RGBA, False)):
                    for depth in depths:
                        job = ("Map", slots, colour, depth)
                        with self.subTest(size=(width, height, tiles), format=image_format, depth=depth,
                                          components=len(slots), colour=colour):
                            peak = traced_peak(lambda: write.encode_one(prepared, job))
                            self.assertLessEqual(peak, write.file_bytes(prepared, job))

    def preparation(self, case, size, tiles, image_format):
        """``(net peak, estimate, handed-over bytes)`` of one preparation: its peak beyond the arrays handed over,
        which only ``work`` holds, as in the job."""
        found = {}

        def run():
            work = work_of(case, size, tiles, image_format, self.folder)
            found["estimate"], found["handed"] = write.prepare_bytes(work), write.array_bytes(work["sums"])
            before = tracemalloc.get_traced_memory()[0]
            tracemalloc.reset_peak()
            self.assertIsNotNone(write.prepare(work))
            found["peak"] = tracemalloc.get_traced_memory()[1] - before

        traced_peak(run)
        return found["peak"], found["estimate"], found["handed"]

    def test_every_preparation_peaks_within_its_estimate(self):
        for case in CHANNELS:
            for size, tiles in ((256, 1), (128, 4), (96, 9)):
                for image_format in ("PNG", "OPEN_EXR"):
                    with self.subTest(case=case, tiles=tiles, format=image_format):
                        peak, estimate, _handed = self.preparation(case, size, tiles, image_format)
                        self.assertLessEqual(peak, estimate)

    def test_a_preparation_frees_the_sums_as_it_goes(self):
        """Its channels replace the sums rather than sitting beside them all (which needed as much again)."""
        peak, _estimate, handed = self.preparation("weighted", 256, 1, "PNG")
        self.assertLess(peak, handed / 2)


class QueueTests(unittest.TestCase):
    """The writing queue: one shared pool, everything held counted against one budget."""

    def work(self, folder, index, size=32):
        rng = numpy.random.default_rng(index)
        sums = {"BASE_COLOR": [(None, rng.random((size, size, 3), dtype=numpy.float32))],
                "ROUGHNESS": [(None, rng.random((size, size, 1), dtype=numpy.float32))],
                "METALLIC": (0.0, 0.0, 0.0), "WEIGHT": (1.0, 1.0, 1.0)}
        return {"target": "T%d" % index, "stem": "T%d" % index, "tiles": [], "size": (size, size), "sums": sums,
                "channels": ["BASE_COLOR", "METALLIC", "ROUGHNESS"], "folder": folder,
                "request": {"packing": "SEPARATE", "format": "PNG"}}

    def drain(self, queue, deadline):
        while queue.busy() and time.monotonic() < deadline:
            queue.pump()
            time.sleep(0.001)
        self.assertFalse(queue.busy())
        self.assertIsNone(queue.error)

    def run_queue(self, budget, targets=3):
        folder = tempfile.mkdtemp()
        with ThreadPoolExecutor(max_workers=write.WORKERS) as pool:
            queue = queue_module.WriteQueue(pool, budget)
            deadline = time.monotonic() + 30
            for index in range(targets):   # As the job produces: the next target's bakes only when they fit.
                work = self.work(folder, index)
                cost = write.array_bytes(work["sums"])
                while not queue.room(cost) and time.monotonic() < deadline:
                    queue.pump()
                    time.sleep(0.001)
                queue.hold_baked(index, cost)
                queue.hand_over(index, work)
            self.drain(queue, deadline)
        self.assertEqual(sorted(queue.results), list(range(targets)))
        written = sorted(entry["file"] for entries, _facts in queue.results.values() for entry in entries)
        maps = ("BaseColor", "Metallic", "Roughness")
        self.assertEqual(written, sorted("T%d_%s.png" % (i, m) for i in range(targets) for m in maps))
        self.assertTrue(all(Path(folder, name).is_file() for name in written))
        self.assertEqual(queue.held, 0, "every byte held is given back")
        return queue

    def test_everything_held_stays_within_the_budget(self):
        work = self.work("", 0)
        arrays = write.array_bytes(work["sums"])
        widest = write.widest_file_bytes(work)
        budget = 2 * arrays + widest   # One target's channels and its widest file, not every file at once.
        queue = self.run_queue(budget)
        self.assertLessEqual(queue.peak, budget)

    def test_a_unit_larger_than_the_budget_still_runs_alone(self):
        queue = self.run_queue(1)
        self.assertGreater(queue.peak, 1)

    def test_a_bake_keeps_its_memory_until_its_image_is_freed(self):
        """No unit starts in the room a running bake reserved; the room is given back when its image is freed."""
        folder = tempfile.mkdtemp()
        work = self.work(folder, 0)
        arrays = write.array_bytes(work["sums"])
        budget = arrays + write.prepare_bytes(work) + write.widest_file_bytes(work)
        with ThreadPoolExecutor(max_workers=write.WORKERS) as pool:
            queue = queue_module.WriteQueue(pool, budget)
            queue.reserve_bake(budget - arrays)
            queue.hold_baked(0, arrays)
            queue.hand_over(0, work)
            queue.pump()
            self.assertEqual((queue.running, len(queue.waiting)), ({}, 1), "the target waits for the bake")
            queue.release_bake()
            queue.pump()
            self.assertEqual(len(queue.running), 1)
            self.drain(queue, time.monotonic() + 30)
        self.assertEqual(queue.held, 0)
        self.assertLessEqual(queue.peak, budget)


class SetPeakTests(unittest.TestCase):
    """A whole set as the job runs it: stand-in bakes hold the memory they reserved (a scratch image) while files
    of earlier targets are written; the traced peak stays within the budget."""

    SIZE, TARGETS = 384, 4
    STEPS = (("BASE_COLOR",), ("ROUGHNESS", "METALLIC", "AO"))   # A colour bake and a packed one.
    CHANNELS = ("BASE_COLOR", "METALLIC", "ROUGHNESS", "AO")

    def needed(self, image_format, depth):
        """The largest target's pipeline alone, as ``bake_maps_steps.memory_needed`` takes it, for these bakes: the
        second bake beside the first's arrays, both bakes' arrays while they are prepared, then the channels while
        the widest file is written."""
        texels = self.SIZE ** 2
        packed = numpy.empty((self.SIZE, self.SIZE, 3), numpy.float32)
        work = {"size": (self.SIZE, self.SIZE), "tiles": [], "channels": list(self.CHANNELS),
                "sums": {"BASE_COLOR": [(None, numpy.empty((self.SIZE, self.SIZE, 3), numpy.float32))],
                         **{key: [(None, packed[..., i:i + 1])] for i, key in enumerate(self.STEPS[1])}},
                "request": {"packing": "SEPARATE", "format": image_format, "depth": depth}}
        arrays = write.array_bytes(work["sums"])
        bake = texels * 16 + arrays // 2   # A scratch image and its arrays read back.
        return max(arrays // 2 + bake, arrays + write.prepare_bytes(work),
                   texels * 4 * 6 + write.widest_file_bytes(work))

    def run_set(self, image_format, depth):
        folder = tempfile.mkdtemp()
        budget = int(1.5 * self.needed(image_format, depth))   # Room for some overlap of baking and writing.
        texels = self.SIZE ** 2
        found = {}

        def run():
            rng = numpy.random.default_rng(7)
            with ThreadPoolExecutor(max_workers=write.WORKERS) as pool:
                queue = queue_module.WriteQueue(pool, budget)
                deadline = time.monotonic() + 60
                for index in range(self.TARGETS):
                    sums = {}
                    for step in self.STEPS:
                        cost = texels * 16 + texels * 12   # The scratch image and the arrays read back.
                        while not queue.room(cost) and time.monotonic() < deadline:
                            queue.pump()
                            time.sleep(0.001)
                        queue.reserve_bake(cost)
                        scratch = numpy.ones(texels * 4, dtype=numpy.float32)   # The bake's image.
                        for _ in range(5):   # Files are written while it bakes.
                            queue.pump()
                            time.sleep(0.002)
                        values = rng.random((self.SIZE, self.SIZE, 3), dtype=numpy.float32)
                        read = {key: [(None, values[..., i:i + 1] if len(step) > 1 else values)]
                                for i, key in enumerate(step)}
                        del values
                        queue.hold_baked(index, write.array_bytes(read))
                        sums.update(read)
                        del read, scratch
                        queue.release_bake()
                    sums["WEIGHT"] = (1.0, 1.0, 1.0)
                    work = {"target": "T%d" % index, "stem": "T%d" % index, "tiles": [], "size": (self.SIZE, self.SIZE),
                            "sums": sums, "channels": list(self.CHANNELS), "folder": folder,
                            "request": {"packing": "SEPARATE", "format": image_format, "depth": depth}}
                    del sums
                    queue.hand_over(index, work)
                    del work
                while queue.busy() and time.monotonic() < deadline:
                    queue.pump()
                    time.sleep(0.001)
                found["queue"] = queue
        found["peak"] = traced_peak(run)
        queue = found["queue"]
        self.assertIsNone(queue.error)
        self.assertEqual(sorted(queue.results), list(range(self.TARGETS)))
        self.assertEqual(queue.held, 0)
        self.assertLessEqual(queue.peak, budget)
        self.assertLessEqual(found["peak"], budget, "traced %.1f MiB, budget %.1f MiB" % (
            found["peak"] / 2 ** 20, budget / 2 ** 20))

    def test_a_png_set_stays_within_its_budget(self):
        self.run_set("PNG", 16)

    def test_an_exr_set_stays_within_its_budget(self):
        self.run_set("OPEN_EXR", 32)


if __name__ == "__main__":
    unittest.main()
