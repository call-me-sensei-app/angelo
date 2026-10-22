# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a Mantaflow bake will need and write, worked out before it starts. No Blender dependency.

``job_fluid`` reads the domain and calls these; the host suite checks them on
domains of every shape (``test_native_job_fluid_plan``).

* ``grid``: the cells Mantaflow allocates, from the domain's size and its
  resolution, exactly as ``fluid.cc`` (``manta_set_domain_from_mesh``) does: the
  longest side gets the resolution, the others their share, at least 4.
* ``estimate``: memory while baking and disk per frame, from the cell count and
  what the domain simulates. They are estimates of an upper bound, stated as such:
  Mantaflow keeps about ``*_GRIDS`` floats per cell, and OpenVDB stores most caches
  in far less than the dense bound on disk. The job refuses only what cannot
  fit (memory beyond the computer's, or less than ``DISK_MARGIN`` free), states
  the rest in the approval, and watches the real bytes per frame while it bakes
  (``disk_outlook``), stopping before the disk would fill.
* ``expected_files``: a file for every frame and every cache the domain writes,
  named as ``MANTA::getFile`` names them (``<name>_####.<ext>``, more digits past
  9999), with the alternatives Mantaflow itself accepts when it reads a frame, so a
  stopped or failed bake is never published as whole. ``most_files`` bounds how
  many files a bake writes, so a bake whose publication would exceed the job's
  walk bound is refused before it starts, not after hours.
* ``unsafe_path``: Mantaflow rewrites a cache path's characters that
  ``BLI_path_make_safe`` calls unsafe before it writes, so a folder whose name has
  one would send the cache elsewhere; the job refuses such a path instead. It is
  stricter than Blender in one place: a backslash in a POSIX folder name is refused (Blender
  splits paths at it on every platform), never rewritten.
"""

import math
import re

# Floats Mantaflow allocates per cell while it bakes (grids of the solver script, inflow and obstacle grids,
# vectors counted as three). A liquid adds its FLIP particles (two per cell by default, position and velocity).
GAS_GRIDS, LIQUID_GRIDS, NOISE_GRIDS = 40, 30, 15
PARTICLE_BYTES = 28
# Floats per cell a cached frame holds at most (before OpenVDB's compression and sparseness).
GAS_FRAME_GRIDS, LIQUID_FRAME_GRIDS, NOISE_FRAME_GRIDS = 12, 4, 8
GiB = 1024 ** 3
DISK_MARGIN = 2 * GiB          # Never bake a cache into the last 2 GiB of a disk.
MIN_FRAMES_MEASURED = 2        # Bytes per frame are measured once this many frames are on disk.
# (folder, file names) per cache, as DNA_fluid_types.h names them: a frame is cached when one of its names exists,
# the test Mantaflow itself makes before it reads a frame (MANTA::hasData, hasNoise, hasMesh, hasParticles,
# hasGuiding: one file holding every grid, else the single-grid or older naming).
CACHES = {"data": ("data", ("fluid_data", "density", "pp")), "noise": ("noise", ("fluid_noise", "density_noise")),
          "mesh": ("mesh", ("fluid_mesh", "lMesh")), "particles": ("particles", ("fluid_particles", "ppSnd")),
          "guiding": ("guiding", ("fluid_guiding", "guidevel"))}
# Files a frame of a cache may hold at most: one file holding every grid, or one file per grid (Uni format).
FILES_PER_FRAME = {"data": 24, "noise": 6, "mesh": 2, "particles": 6, "guiding": 4}
_UNSAFE = set(chr(code) for code in range(1, 32)) | set('/\\?*:|"<>')
_WINDOWS_NAMES = ("con", "prn", "aux", "null") + tuple("com%d" % n for n in range(1, 10)) + tuple(
    "lpt%d" % n for n in range(1, 10))


def grid(size, resolution):
    """``(x, y, z)`` cells of a domain whose sides are ``size`` (scene units) at ``resolution`` (its longest side)."""
    if min(size) <= 0:
        raise ValueError("a fluid domain needs a size on every axis")
    x, y, z = size
    axis = 0 if x >= max(y, z) else 1 if y >= max(x, z) else 2   # Blender's order when sides tie.
    scale = resolution / size[axis]
    return tuple(resolution if index == axis else max(int(side * scale + 0.5), 4) for index, side in enumerate(size))


def estimate(cells, domain_type, *, noise_scale=0, particles_per_cell=2):
    """``{"memory": bytes while baking, "frame": bytes a cached frame holds at most}``."""
    count = math.prod(cells)
    if domain_type == "LIQUID":
        memory = count * (LIQUID_GRIDS * 4 + particles_per_cell * PARTICLE_BYTES)
        frame = count * (LIQUID_FRAME_GRIDS * 4 + particles_per_cell * PARTICLE_BYTES)
    else:
        memory, frame = count * GAS_GRIDS * 4, count * GAS_FRAME_GRIDS * 4
    if noise_scale > 1:
        noise_cells = count * noise_scale ** 3
        memory += noise_cells * NOISE_GRIDS * 4
        frame += noise_cells * NOISE_FRAME_GRIDS * 4
    return {"memory": memory, "frame": frame}


def file_frame(name, prefix):
    """The frame number of ``<prefix>_<digits>.<ext...>``, or None for any other name."""
    match = re.fullmatch(re.escape(prefix) + r"_(-\d{3,}|\d{4,})\..+", name)   # %04d: "-005" for -5.
    return int(match.group(1)) if match else None


def expected_files(caches, frames):
    """``{cache: (folder, names, [frames])}`` for the caches a bake writes over the frame range ``frames``."""
    start, end = frames
    return {cache: CACHES[cache] + (list(range(start, end + 1)),) for cache in caches}


def most_files(caches, frames, *, one_file_per_frame=True, mesh_files=1):
    """The most files and folders a bake of ``caches`` over ``frames`` writes (its publication walks them all).

    A frame has a config file and, per grid cache (data, noise, particles, guiding), one file holding every grid
    (OpenVDB, Blender's default) or one file per grid (Uni). The mesh has its own format, one file a frame, plus
    one with its speed vectors when they are on (``mesh_files`` 2).
    """
    count = frames[1] - frames[0] + 1
    per_frame = 1 + sum(mesh_files if cache == "mesh" else 1 if one_file_per_frame else FILES_PER_FRAME[cache]
                        for cache in caches)
    return count * per_frame + 2 * len(caches) + 8


def missing(expected, staged):
    """``{cache: [frames]}`` with no file in ``staged``: ``{relative posix path: bytes}`` of the bake's folder.

    An empty file never counts: a frame Mantaflow could not write is missing, not cached.
    """
    found = {}
    for path, size in staged.items():
        if size <= 0:
            continue
        folder, _, name = path.rpartition("/")
        found.setdefault(folder, set()).add(name)
    result = {}
    for cache, (folder, prefixes, frames) in expected.items():
        have = {file_frame(name, prefix) for name in found.get(folder, ()) for prefix in prefixes}
        lacking = [frame for frame in frames if frame not in have]
        if lacking:
            result[cache] = lacking
    return result


def frames_written(staged, cache="data"):
    """How many frames of a cache are on disk (its data files by default)."""
    folder, prefixes = CACHES[cache]
    return len({file_frame(path.rpartition("/")[2], prefix) for path in staged if path.startswith(folder + "/")
                for prefix in prefixes} - {None})


def disk_outlook(written_bytes, frames_done, frames_left, free_bytes):
    """``(needed bytes, fits)`` from the bytes per frame measured so far; fits while not yet measured."""
    if frames_done < MIN_FRAMES_MEASURED or frames_left <= 0:
        return 0, True
    needed = int(written_bytes / frames_done * frames_left)
    return needed, needed + DISK_MARGIN <= free_bytes


def unsafe_path(path, windows=False):
    """The first component of an absolute path that Mantaflow would rewrite (``BLI_path_make_safe``), or None."""
    parts = [part for part in re.split(r"[\\/]" if windows else "/", path) if part]
    if windows and parts and re.fullmatch(r"[A-Za-z]:", parts[0]):
        parts = parts[1:]   # The drive is never rewritten.
    for part in parts:
        if any(char in _UNSAFE for char in part) or set(part) == {"."}:
            return part
        if windows:
            lower = part.lower()
            if part.endswith(".") or any(lower == name or lower.startswith(name + ".") for name in _WINDOWS_NAMES):
                return part
    return None


def gib(value):
    return round(value / GiB, 2)
