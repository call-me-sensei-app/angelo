# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a child render reused the kernels earlier renders prepared, judged the same on every Cycles backend.

test_blender_render_kernels renders three stills (the application, then two children) and judges the third. Reuse,
as a person meets it, is three facts, any of which a child that really builds its kernels again breaks:

- Cycles never said it was loading render kernels for ``MIN_COMPILE_S`` or longer (``kernel_compile_s``: Cycles' own
  "Loading render kernels" status, on every backend, as angelo_render_device.kernels counts a compile).
- The render added no kernel archive to Cycles' cache and built none again (``archives``, by file identity: a load
  only sets an archive's write time). On Metal Cycles archives every kernel it builds
  (``<cache>/kernels/*/*/PSO_GENERIC/*.bin``, generic kernels only: specialized ones are built in the background
  whenever a scene asks, so their number is no compile of the render's own), so earlier renders must have left some.
  A Metal archive built again can take under a second when macOS's own Metal cache still holds the pipelines, so
  only the archive, not the load time, shows it. CUDA and HIP archive the kernels they compile at run time there
  too (any file in that folder). OptiX and precompiled CUDA kernels write nothing there (the driver's OptiX cache is
  its own), so for them an empty folder that stays empty is reuse, and the load time is the fact that fails a
  compile.

- Its job took no longer than the second render's, an identical child render of the same scene, by
  ``SETUP_SLACK_S`` or more (``ended - started``, which every ended job has; not the seconds to its first progress,
  which a render quicker than the job manager's poll never records). A build too quick to count as a compile still
  shows here: on the T4 (OptiX) a child that built its modules again took about 1.7 s longer (2.568 -> 4.308 s,
  7f9dd3b3c5e's forced run), reported no compile (under ``MIN_COMPILE_S``) and archived nothing in Cycles' cache;
  two children that both reuse differ by milliseconds (2.570 / 2.564).

Cycles' cache (``util/path.cpp`` ``path_cache_get``): ``$XDG_CACHE_HOME/cycles``, else ``~/.cache/cycles``, on
macOS and Linux; on Windows ``<Blender's user folder>/cache`` (the folder above the user config folder,
``intern/cycles/blender/addon/engine.py``).

Before 2026-11-12 the check counted Metal archives only and required some, so on Windows (OptiX, T4: no archive ever)
it failed while the child loaded in 0.0 s (78d279707a1 T4). Standard library only; no ``bpy``.
"""

import contextlib
import os
import shutil
import sys
import time
from pathlib import Path

# The variable that turns the OptiX disk cache off for a process (NVIDIA's OptiX programming guide, "Cache"): the
# forced recompile below sets it for the child it starts, so OptiX builds its modules again.
OPTIX_CACHE_OFF = {"OPTIX_CACHE_MAXSIZE": "0"}
# Set to 1 for the fail-first proof: the third render then finds no kernel archive and no OptiX cache.
FORCE_RECOMPILE = "ANGELO_RENDER_KERNELS_FORCE_RECOMPILE"
# How much longer the third child's job may take than the second's. Half the shortest load the product counts as a
# compile (angelo_render_device.kernels.MIN_COMPILE_S): a build that escapes that count but takes over half of it
# fails here, while two identical warm child jobs differ by milliseconds.
SETUP_SLACK_S = 1.0


def kernel_root(environ=None, home=None, user_config=None, platform=None):
    """Cycles' kernel cache folder for a process with ``environ`` (and ``user_config``, Blender's user config folder,
    on Windows)."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform.startswith("win"):
        return Path(os.path.dirname(os.path.abspath(user_config))) / "cache" / "kernels"
    cache = environ.get("XDG_CACHE_HOME") or str(Path(home or Path.home()) / ".cache")
    return Path(cache) / "cycles" / "kernels"


def is_metal(device):
    """Whether ``device`` (render_device.facts' "GPU (Metal: ...)") renders on Metal."""
    return "(Metal" in (device or "")


def archives(root, metal):
    """The kernel archives in ``root`` (Metal's generic ones, else every file), each with its file identity.

    The identity is the file's inode (file index on Windows) and birth time where the system keeps one: Cycles
    marks an archive it loads by setting its write time only (``path_cache_kernel_mark_used``), so a loaded archive
    keeps its identity, while one Cycles built again is a new file, even under the same name (on 7f9dd3b3c5e's Mac
    forced run a child rebuilt all 71 generic archives from macOS's own Metal cache in under a second: names alone
    could not see it)."""
    root = Path(root)
    if not root.is_dir():
        return {}
    found = root.glob("*/*/PSO_GENERIC/*.bin") if metal else (p for p in root.rglob("*") if p.is_file())
    result = {}
    for path in found:
        info = path.stat()
        result[str(path.relative_to(root))] = (info.st_ino, getattr(info, "st_birthtime", None))
    return dict(sorted(result.items()))


def reused(record, earlier, metal, before, after, min_compile_s):
    """``(ok, why)``: whether the ended child ``record`` loaded what earlier renders prepared (module docstring);
    ``earlier`` is the identical child render before it."""
    detail, earlier_detail = record.get("detail") or {}, earlier.get("detail") or {}
    compiled = float(detail.get("kernel_compile_s") or 0.0)
    for job, facts in ((record, detail), (earlier, earlier_detail)):
        if job.get("status") != "done" or facts.get("process") != "child":
            return False, "a render compared was not a finished child render (%s, %s)" % (job.get("status"),
                                                                                         facts.get("process"))
    if compiled >= min_compile_s:
        return False, "Cycles loaded render kernels for %.1f s (a compile is %.1f s or more)" % (compiled,
                                                                                               min_compile_s)
    took = [job.get("ended") - job.get("started") if all(isinstance(job.get(key), (int, float))
                                                       for key in ("started", "ended")) else None
            for job in (record, earlier)]
    if None in took:
        return False, "a child job's start or end was not recorded (%r, %r)" % (took[1], took[0])
    setup, earlier_setup = took
    if setup - earlier_setup >= SETUP_SLACK_S:
        return False, ("its job took %.1f s against the identical earlier child's %.1f s: a kernel build too quick "
                       "to count as a compile" % (setup, earlier_setup))
    if metal and not before:
        return False, "no Metal kernel archive was left by the earlier renders, so nothing could be reused"
    added = sorted(set(after) - set(before))
    if added:
        return False, "the render added %d kernel archives: %s" % (len(added), added[:5])
    rebuilt = sorted(name for name in after if after[name] != before[name])
    if rebuilt:
        return False, "the render built %d kernel archives again: %s" % (len(rebuilt), rebuilt[:5])
    return True, ("loaded in %.1f s, %d archives before and after, none built again, job %.2f s after the "
                  "earlier child's %.2f s" % (compiled, len(before), setup, earlier_setup))


@contextlib.contextmanager
def recompile(root, environ=None):
    """For the fail-first proof: while open, ``root``'s archives are moved aside and a child started from
    ``environ`` (default ``os.environ``) has no OptiX cache, so its render must build its kernels again. Puts
    everything back on exit (the archives the render wrote are removed)."""
    environ = os.environ if environ is None else environ
    root = Path(root)
    aside = root.with_name("%s.aside-%d-%d" % (root.name, os.getpid(), int(time.time())))
    moved = root.is_dir()
    if moved:
        root.rename(aside)
    saved = {name: environ.get(name) for name in OPTIX_CACHE_OFF}
    environ.update(OPTIX_CACHE_OFF)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                environ.pop(name, None)
            else:
                environ[name] = value
        shutil.rmtree(root, ignore_errors=True)   # What the forced render archived.
        if moved:
            aside.rename(root)
