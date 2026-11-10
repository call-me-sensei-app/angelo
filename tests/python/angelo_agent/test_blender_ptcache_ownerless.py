# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A point cache with no owning object (a rigid body world's) is safe from every script path, and stays in memory.

A rigid body world belongs to its scene and is not an ID, so its cache's ``PTCacheID`` has no owner object (or, when
reached through a rigid body object, that object: a different one per object). Naming its disk files dereferenced the
missing owner: ``scene.rigidbody_world.point_cache.use_disk_cache = True`` in a saved file crashed in
``BKE_object_insert_ptcache`` (found by the agent-UX AUTO lane; upstream Blender too). The ``ptcache`` operators read
the scene that owns such a cache as an object.

The contract now (``BKE_pointcache.h``, ``PTCacheID.owner_id``): such a cache has no disk files of its own. Turning its
disk cache on leaves it in memory (the property reads back False, the frames stay), a disk flag it carries anyway
still keeps its frames in memory, and only an external cache (``<filepath>/<name>_<frame>.bphys``) is read from disk
for it. Blender's UI never offers its disk cache, and upstream's 5.0 versioning says rigid body caches should not
have one.

Checks, in the real binary (background; a saved file in ANGELO_QA_DIR; writes results.json there). Each case starts
from a fresh scene; ``ANGELO_PTCACHE_CASE=<name>`` runs one case (the failing-first proof runs each in its own
process, as a crash ends the process):

* ``use_disk_cache``: turning it on and off keeps the cache in memory, writes no file, and the simulation still matches;
* ``name_and_index``: renaming the cache and setting its index, then the disk cache, stays safe and file-free;
* ``external``: an external cache read from a folder (an empty frame file in it), then switched off with its disk flag
  left on (as a file from elsewhere or an animated flag leaves it): it still simulates in memory, the effective storage policy stays in memory,
  and Bake All (which reaches the world's cache through each rigid body object) bakes into memory, survives saving and
  reloading, plays back, and writes nothing to disk;
* ``operators``: ptcache.add/remove/bake/bake_from_cache/free_bake with the world's cache as the context point cache;
* held out, ``object_cache``: an object's (cloth) disk cache still writes ``<hex of the object name>_<frame>_00.bphys``
  next to the file, as before.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-ptcache-ownerless-qa"))
OUT.mkdir(parents=True, exist_ok=True)
FRAMES = 12


def bphys_files(case):
    folder = OUT / case
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*.bphys"))


def memory_frames(cache):
    """Frames the cache holds in memory, from its info ("12 frames in memory (1.2 KiB)."), or None if not there."""
    info = cache.info
    head = info.split(" frames in memory", 1)[0] if " frames in memory" in info else None
    return int(head.replace(",", "")) if head and head.replace(",", "").isdigit() else None


def fresh_scene(case):
    """A saved file (disk caches need one) with a falling active cube over a passive ground."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    folder = OUT / case
    folder.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "ownerless.blend"))
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 40
    bpy.ops.rigidbody.world_add()
    bpy.ops.mesh.primitive_plane_add(size=10.0)
    bpy.ops.rigidbody.object_add(type="PASSIVE")
    bpy.ops.mesh.primitive_cube_add(location=(0.0, 0.0, 3.0), rotation=(0.3, 0.2, 0.1))
    bpy.ops.rigidbody.object_add(type="ACTIVE")
    return scene, bpy.context.active_object


def simulate(scene, body):
    for frame in range(scene.frame_start, scene.frame_start + FRAMES):
        scene.frame_set(frame)
    return [round(value, 4) for value in body.matrix_world.translation]


def case_use_disk_cache(checks, observed):
    scene, body = fresh_scene("use_disk_cache")
    cache = scene.rigidbody_world.point_cache
    reference = simulate(scene, body)
    cache.use_disk_cache = True
    checks["disk_cache_reads_back_false"] = cache.use_disk_cache is False
    after = simulate(scene, body)
    observed["memory_info_after"] = cache.info
    checks["frames_cached_in_memory"] = (memory_frames(cache) or 0) > 0
    checks["simulation_unchanged"] = after == reference and reference[2] < 2.9
    cache.use_disk_cache = False
    checks["switching_off_stays_off"] = cache.use_disk_cache is False
    checks["no_disk_file"] = bphys_files("use_disk_cache") == []
    observed["use_disk_cache"] = {"reference": reference, "after": after}


def case_name_and_index(checks, observed):
    scene, body = fresh_scene("name_and_index")
    cache = scene.rigidbody_world.point_cache
    reference = simulate(scene, body)
    cache.name = "Rigid"
    cache.index = 2
    cache.use_disk_cache = True
    checks["named_disk_cache_reads_back_false"] = cache.use_disk_cache is False
    cache.name = "Renamed"
    cache.index = -1
    checks["named_simulation_unchanged"] = simulate(scene, body) == reference
    checks["named_no_disk_file"] = bphys_files("name_and_index") == []


def case_external(checks, observed):
    scene, body = fresh_scene("external")
    body_name = body.name
    cache = scene.rigidbody_world.point_cache
    reference = simulate(scene, body)
    folder = OUT / "external" / "frames"   # Beside the case's blend file (and its blendcache_ folder).
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Rigid_000001.bphys").write_bytes(b"")   # An external frame file that holds nothing.
    cache.name = "Rigid"
    cache.filepath = str(folder)
    cache.use_external = True
    observed["external_info"] = cache.info
    observed["external_flags"] = {"is_baked": cache.is_baked, "use_disk_cache": cache.use_disk_cache}
    for frame in (1, 2, 3):
        scene.frame_set(frame)
    checks["external_read_is_safe"] = True
    cache.use_external = False
    # The disk flag the external cache set stays on (as for any cache): the state a file from elsewhere,
    # an animated or an overridden flag also gives. Free the bake it left and restore its frame range.
    checks["external_left_its_disk_flag_on"] = cache.use_disk_cache is True
    with bpy.context.temp_override(point_cache=cache):
        bpy.ops.ptcache.free_bake()
    cache.frame_start, cache.frame_end = 1, 40
    checks["left_on_flag_still_simulates_in_memory"] = simulate(scene, body) == reference
    checks["evaluation_keeps_shared_configuration"] = cache.use_disk_cache is True
    bpy.ops.ptcache.bake_all(bake=True)
    checks["bake_all_bakes_the_world"] = cache.is_baked
    observed["baked_info"] = cache.info
    checks["bake_all_frames_in_memory"] = (memory_frames(cache) or 0) >= FRAMES
    # Save/Undo serialize the effective memory policy even while the raw disk flag stays on.
    bpy.ops.wm.save_mainfile()
    bpy.ops.wm.revert_mainfile()
    scene = bpy.context.scene
    cache, body = scene.rigidbody_world.point_cache, scene.objects[body_name]
    observed["reloaded_info"] = cache.info
    checks["reloaded_bake_kept_its_frames"] = cache.is_baked and (memory_frames(cache) or 0) >= FRAMES
    checks["reloaded_bake_plays_back"] = simulate(scene, body) == reference
    bpy.ops.ptcache.free_bake_all()
    checks["no_file_besides_the_external_one"] = bphys_files("external") == ["frames/Rigid_000001.bphys"]


def case_operators(checks, observed):
    scene, body = fresh_scene("operators")
    cache = scene.rigidbody_world.point_cache
    reference = simulate(scene, body)
    with bpy.context.temp_override(point_cache=cache):
        bpy.ops.ptcache.add()
        caches = scene.rigidbody_world.point_cache.point_caches
        checks["add_adds_a_world_cache"] = len(caches) == 2
        with bpy.context.temp_override(point_cache=scene.rigidbody_world.point_cache):
            bpy.ops.ptcache.remove()
        checks["remove_removes_it"] = len(scene.rigidbody_world.point_cache.point_caches) == 1
    cache = scene.rigidbody_world.point_cache
    with bpy.context.temp_override(point_cache=cache):
        bpy.ops.ptcache.bake(bake=True)
        checks["bake_bakes_the_world"] = cache.is_baked
        bpy.ops.ptcache.free_bake()
        checks["free_bake_frees_it"] = not cache.is_baked
        bpy.ops.ptcache.bake_from_cache()
        checks["bake_from_cache_marks_it"] = cache.is_baked
        bpy.ops.ptcache.free_bake()
    checks["operators_simulation_unchanged"] = simulate(scene, body) == reference


def case_object_cache(checks, observed):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    folder = OUT / "object_cache"
    folder.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "cloth.blend"))
    bpy.ops.mesh.primitive_plane_add(size=2.0)
    sheet = bpy.context.active_object
    sheet.name = "Sheet"
    cloth = sheet.modifiers.new("Cloth", "CLOTH")
    cloth.point_cache.use_disk_cache = True
    checks["object_disk_cache_reads_back_true"] = cloth.point_cache.use_disk_cache is True
    for frame in range(1, 5):
        bpy.context.scene.frame_set(frame)
    files = sorted(path.name for path in (folder / "blendcache_cloth").glob("*.bphys"))
    observed["object_cache_files"] = files
    prefix = "".join("%02X" % ord(char) for char in "Sheet")
    checks["object_cache_named_after_its_object"] = bool(files) and all(
        name.startswith(prefix + "_") and name.endswith("_00.bphys") for name in files)


CASES = {"use_disk_cache": case_use_disk_cache, "name_and_index": case_name_and_index, "external": case_external,
         "operators": case_operators, "object_cache": case_object_cache}


def main():
    selected = os.environ.get("ANGELO_PTCACHE_CASE")
    names = [selected] if selected else list(CASES)
    checks, observed = {}, {}
    for name in names:
        CASES[name](checks, observed)
        print("ANGELO_PTCACHE_CASE_DONE", name, flush=True)
    observed["bphys_files"] = {name: bphys_files(name) for name in names}
    state = {"success": bool(checks) and all(checks.values()), "cases": names, "checks": checks,
             "observed": observed}
    (OUT / "results.json").write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    print("ANGELO_PTCACHE_OWNERLESS", json.dumps({"success": state["success"],
                                                  "failed": [k for k, v in checks.items() if not v]}), flush=True)


main()
