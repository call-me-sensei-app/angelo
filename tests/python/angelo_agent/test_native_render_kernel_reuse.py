# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""test_blender_render_kernels judges its third render, a child, the same on every Cycles backend
(render_kernel_reuse.reused): Cycles' own kernel load time, and the archives the render added to Cycles' cache.

On 78d279707a1's Windows T4 (OptiX) the child loaded in 0.0 s and Cycles archived nothing, as it never does for OptiX,
yet the check counted Metal archives only and required some, so it could never pass there. A child that really
compiles again still fails on every backend; the GUI suite's forced recompile (ANGELO_RENDER_KERNELS_FORCE_RECOMPILE=1)
proves that in the app. No Blender needed.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "scripts/modules"), str(HERE)]
from angelo_render_device import kernels  # noqa: E402
import render_kernel_reuse as reuse  # noqa: E402

METAL = "GPU (Metal: Apple M4 Pro (GPU - 20 cores))"
OPTIX = "GPU (OptiX: Tesla T4)"


def record(compile_s=0.0, status="done", process="child", seconds=2.57):
    """An ended job record as the GUI suite reads it (job_deadline.observe_kernels drops a load under the minimum)."""
    detail = {"process": process}
    if compile_s:
        detail["kernel_compile_s"] = compile_s
    return {"status": status, "detail": detail, "started": 1000.0,
            "ended": None if seconds is None else 1000.0 + seconds}


def files(*names, identity=(1, 0.0)):
    """Archives as ``reuse.archives`` lists them: name -> file identity."""
    return {name: identity for name in names}


def judge(job, device, before, after, earlier=None):
    earlier = record() if earlier is None else earlier
    return reuse.reused(job, earlier, reuse.is_metal(device), before, after, kernels.MIN_COMPILE_S)[0]


class Judge(unittest.TestCase):
    def test_a_warm_child_reuses_on_every_backend(self):
        metal = files(*("k/a/PSO_GENERIC/%d.bin" % n for n in range(68)))   # 3c6e11afcc8's Mac round: 68 and 68.
        for device, archives in ((OPTIX, {}), ("GPU (CUDA: Tesla T4)", files("cycles_kernel_sm_75.cubin")),
                                 (METAL, metal), ("CPU", {})):
            with self.subTest(device=device):
                self.assertTrue(judge(record(), device, archives, dict(archives)))

    def test_a_child_that_compiles_again_fails_on_every_backend(self):
        for device in (OPTIX, METAL, "GPU (HIP: Radeon)", "CPU"):
            with self.subTest(device=device):
                self.assertFalse(judge(record(kernels.MIN_COMPILE_S), device, files("a"), files("a")))
                self.assertFalse(judge(record(16.8), device, files("a"), files("a")))   # 116c309: built again.

    def test_a_child_that_archives_new_kernels_fails(self):
        self.assertFalse(judge(record(), "GPU (CUDA: Tesla T4)", {}, files("cycles_kernel_sm_75.cubin")))
        self.assertFalse(judge(record(), METAL, files("a/b/PSO_GENERIC/1.bin"),
                               files("a/b/PSO_GENERIC/1.bin", "a/b/PSO_GENERIC/2.bin")))

    def test_a_child_that_builds_the_same_archives_again_fails_even_when_quick(self):
        # 7f9dd3b3c5e, Mac forced run: the child rebuilt all 71 generic archives under their old names from macOS's
        # Metal cache, its load under the minimum. The archives are new files: that is a build, not a reuse.
        names = ["k/a/PSO_GENERIC/%d.bin" % n for n in range(71)]
        before, after = files(*names, identity=(1, 1.0)), files(*names, identity=(2, 9.0))
        self.assertFalse(judge(record(), METAL, before, after))
        self.assertIn("built 71 kernel archives again",
                      reuse.reused(record(), record(), True, before, after, kernels.MIN_COMPILE_S)[1])

    def test_a_child_that_builds_kernels_too_quickly_to_count_still_fails_by_its_time(self):
        # 7f9dd3b3c5e, T4 (OptiX) forced run: the third child built its OptiX modules again in about 1.5 s, under the
        # minimum a load must last to count as a compile, so it reported none, and OptiX archives nothing in Cycles'
        # cache. Its job took that much longer than the identical second child's: 2.568 -> 4.308 s. Two children
        # that both reuse differ by milliseconds (2.570 / 2.564 in the same run as is).
        self.assertFalse(judge(record(seconds=4.308), OPTIX, {}, {}, earlier=record(seconds=2.568)))
        self.assertTrue(judge(record(seconds=2.564), OPTIX, {}, {}, earlier=record(seconds=2.570)))
        # A second child that itself built (nothing archived yet for it) is slower; the third is then quicker.
        self.assertTrue(judge(record(seconds=1.2), METAL, files("a"), files("a"), earlier=record(seconds=16.8)))

    def test_a_counted_compile_is_named_as_one_even_when_the_job_also_took_longer(self):
        # 0756ab342a1 T4 forced run: the third child reported a 2.1 s compile and took 4.563 s against 2.831 s.
        # Its reason must name the compile, never call it a build too quick to count as one.
        ok, why = reuse.reused(record(2.1, seconds=4.563), record(seconds=2.831), False, {}, {},
                               kernels.MIN_COMPILE_S)
        self.assertFalse(ok)
        self.assertIn("2.1 s", why)
        self.assertNotIn("too quick", why)

    def test_the_comparison_needs_two_ended_child_jobs(self):
        # Not the seconds to the first progress: a render quicker than the manager's poll ends before any progress
        # is seen (e6813de1543 on Mac 1: a 0.6 s child recorded none). Every ended job has its start and end.
        for job, earlier in ((record(seconds=None), record()), (record(), record(seconds=None)),
                             (record(), record(process="app"))):
            with self.subTest(job=job, earlier=earlier):
                self.assertFalse(judge(job, OPTIX, {}, {}, earlier=earlier))

    def test_the_setup_slack_is_half_the_shortest_counted_compile(self):
        self.assertEqual(reuse.SETUP_SLACK_S, kernels.MIN_COMPILE_S / 2)

    def test_on_metal_the_earlier_renders_must_have_archived_something(self):
        self.assertFalse(judge(record(), METAL, {}, {}))

    def test_only_a_finished_child_render_counts(self):
        self.assertFalse(judge(record(process="app"), OPTIX, {}, {}))
        self.assertFalse(judge(record(status="failed"), OPTIX, {}, {}))

    def test_every_refusal_says_why(self):
        for job, device, before, after, words in (
                (record(16.8), OPTIX, {}, {}, "16.8 s"),
                (record(), METAL, {}, {}, "no Metal kernel archive"),
                (record(), "CPU", {}, files("x.cubin"), "x.cubin"),
                (record(), "CPU", files("x.cubin"), files("x.cubin", identity=(5, 0.0)), "x.cubin"),
                (record(seconds=9.0), OPTIX, {}, {}, "9.0 s"),
                (record(process="app"), OPTIX, {}, {}, "not a finished child")):
            with self.subTest(words=words):
                ok, why = reuse.reused(job, record(), reuse.is_metal(device), before, after, kernels.MIN_COMPILE_S)
                self.assertFalse(ok)
                self.assertIn(words, why)


class Cache(unittest.TestCase):
    def test_the_archives_counted_are_the_backends_own(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("a/b/PSO_GENERIC/1.bin", "a/b/PSO_SPECIALIZED_INTERSECT/2.bin", "cycles_kernel_sm_75.cubin"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(b"x")
            # Metal specializes kernels in the background whenever a scene asks: only generic ones are a compile.
            self.assertEqual(list(reuse.archives(root, metal=True)), [str(Path("a/b/PSO_GENERIC/1.bin"))])
            self.assertEqual(len(reuse.archives(root, metal=False)), 3)
            self.assertEqual(reuse.archives(root / "missing", metal=False), {})

    def test_a_loaded_archive_keeps_its_identity_and_a_rebuilt_one_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a/b/PSO_GENERIC/1.bin"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"x")
            before = reuse.archives(folder, metal=True)
            os.utime(path, (1, 2_000_000_000))   # Cycles' path_cache_kernel_mark_used: the write time only.
            self.assertEqual(reuse.archives(folder, metal=True), before)
            keep = Path(folder) / "kept"   # Holds the old file, as the forced run's aside folder does.
            path.rename(keep)
            path.write_bytes(b"x")   # Built again under the same name.
            self.assertNotEqual(reuse.archives(folder, metal=True), before)
            path_cpp = (ROOT / "intern/cycles/util/path.cpp").read_text(encoding="utf-8")
            used = path_cpp[path_cpp.index("static void path_cache_kernel_mark_used"):]
            self.assertIn("OIIO::Filesystem::last_write_time(path, current_time);", used[:300])

    def test_the_cache_folder_is_cycles_own_on_each_platform(self):
        self.assertEqual(reuse.kernel_root({"XDG_CACHE_HOME": "/v/c"}, "/Users/a", platform="darwin"),
                         Path("/v/c/cycles/kernels"))
        self.assertEqual(reuse.kernel_root({}, "/home/a", platform="linux"), Path("/home/a/.cache/cycles/kernels"))
        base = Path(tempfile.gettempdir()) / "Angelo" / "5.3"
        self.assertEqual(reuse.kernel_root({}, None, str(base / "config"), platform="win32"),
                         base / "cache" / "kernels")
        # The sources the rule above is read from: Cycles' cache path, and the user path Blender's Cycles passes it.
        path_cpp = (ROOT / "intern/cycles/util/path.cpp").read_text(encoding="utf-8")
        self.assertIn('return path_user_get(path_join("cache", sub));', path_cpp)
        self.assertIn('const string result = path_join(xdg_cache_path, "cycles");', path_cpp)
        engine = (ROOT / "intern/cycles/blender/addon/engine.py").read_text(encoding="utf-8")
        self.assertIn("os.path.dirname(os.path.abspath(bpy.utils.user_resource('CONFIG', path='')))", engine)

    def test_the_suite_takes_the_forced_recompile_from_the_caller(self):
        import native_suite_manifest as manifest
        suite = next(s for s in manifest.SUITES if s.name == "test_blender_render_kernels")
        self.assertEqual(suite.options_env, (reuse.FORCE_RECOMPILE,))

    def test_the_forced_recompile_hides_every_cache_and_puts_it_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "kernels"
            (root / "a").mkdir(parents=True)
            (root / "a" / "1.bin").write_bytes(b"x")
            environ = {"OPTIX_CACHE_MAXSIZE": "7"}
            with reuse.recompile(root, environ):
                self.assertFalse(root.exists())
                self.assertEqual(environ["OPTIX_CACHE_MAXSIZE"], "0")
                (root / "b").mkdir(parents=True)
                (root / "b" / "2.bin").write_bytes(b"y")   # What the forced render archived.
            self.assertEqual(list(reuse.archives(root, metal=False)), [str(Path("a/1.bin"))])
            self.assertEqual(environ, {"OPTIX_CACHE_MAXSIZE": "7"})
            environ = {}
            with reuse.recompile(Path(folder) / "none", environ):
                self.assertEqual(environ, reuse.OPTIX_CACHE_OFF)
                (Path(folder) / "none" / "c").mkdir(parents=True)   # A first archive, where there was no cache.
            self.assertEqual(environ, {})
            self.assertFalse((Path(folder) / "none").exists())


if __name__ == "__main__":
    unittest.main()
