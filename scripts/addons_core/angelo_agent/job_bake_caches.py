# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A geometry-nodes simulation bake written to disk inside a job's output folder.

Blender bakes a simulation zone to ``<modifier's bake directory>/<bake id>``
(``meta/`` and ``blobs/``, one file per frame each). A job with an ``output``
sends every simulation of its target there: while it runs, each modifier's
bake target is Disk and its directory a folder of its own in the job's staging
folder (``settings``, recorded and restored like every job setting). Nothing is
written at a directory stored in the scene.

When the bake is complete (``missing_frames`` finds every frame of every
simulation) the staging folder is published and each modifier is pointed at
its published folder by a path relative to the .blend (``point_at``): the cache
is found again when the file is reopened or the project folder is moved, and
no absolute path is stored in the scene. That needs a saved file whose folder
holds the output (``blend_relative``). A bake that stops or fails makes
Blender forget what it cached (``forget_partial``, before the settings go
back), removes what it staged and leaves the modifier's settings exactly as
they were; an earlier cache on disk is neither written nor deleted and is read
again.

Refusals are model-facing English. Main thread only.
"""

from pathlib import Path, PurePosixPath

import bpy

from . import job_render_outputs
from .job_spec import JobRequestError


def blend_relative(path):
    """``//…``: ``path`` as Blender stores a path beside the .blend (raises JobRequestError when it can't)."""
    if not bpy.data.filepath:
        raise JobRequestError("Save the file first: a disk cache is found by its path from the .blend, so it "
                              "survives moving the project. Without output the bake is packed into the file")
    try:
        relative = Path(path).resolve().relative_to(Path(bpy.data.filepath).resolve().parent)
    except ValueError:
        raise JobRequestError("This chat's file workspace is not the saved file's folder, so a cache here could "
                              "not be found by a path from the .blend. The workspace follows the file on the next "
                              "message; or leave output out to pack the bake into the file") from None
    return "//" + str(PurePosixPath(*relative.parts))


def plan(target, simulations, frame_range):
    """Where each simulation of ``target`` is cached inside the job's output.

    ``simulations`` are ``(modifier, bake)`` pairs; ``frame_range(bake)`` the
    frames Blender bakes for one. Returns one entry per modifier: its
    ``modifier`` name, its ``folder`` in the output and its ``bakes``
    (``index`` among the modifier's bakes, ``id`` and ``frames``).
    """
    entries, taken = {}, set()
    for modifier, bake in simulations:
        if modifier.name not in entries:
            entries[modifier.name] = {"modifier": modifier.name, "bakes": [],
                                      "folder": job_render_outputs.folder_name(modifier.name, taken)}
        index = next(i for i, other in enumerate(modifier.bakes) if other.bake_id == bake.bake_id)
        start, end = frame_range(bake)
        entries[modifier.name]["bakes"].append({"index": index, "id": bake.bake_id, "frames": [start, end]})
    return list(entries.values())


def _modifier_path(target, entry):
    return 'objects["%s"].modifiers["%s"]' % (bpy.utils.escape_identifier(target.name),
                                              bpy.utils.escape_identifier(entry["modifier"]))


def settings(target, entries, staging):
    """The ``Overrides`` values that make Blender bake every planned simulation into ``staging``."""
    values = {}
    for entry in entries:
        path = _modifier_path(target, entry)
        values[path + ".bake_target"] = 'DISK'
        values[path + ".bake_directory"] = str(Path(staging) / entry["folder"])
        for bake in entry["bakes"]:
            values["%s.bakes[%d].bake_target" % (path, bake["index"])] = 'INHERIT'
            values["%s.bakes[%d].use_custom_path" % (path, bake["index"])] = False
    return values


def missing_frames(entries, staged):
    """The simulations whose cache is not whole in ``staged`` (relative posix paths), as ``modifier/bake id``.

    Blender writes one ``meta`` file per frame, and the frame's data in ``blobs``.
    """
    staged, missing = set(staged), []
    for entry in entries:
        for bake in entry["bakes"]:
            prefix = "%s/%d/" % (entry["folder"], bake["id"])
            frames = sum(path.startswith(prefix + "meta/") and path.endswith(".json") for path in staged)
            blobs = any(path.startswith(prefix + "blobs/") for path in staged)
            if frames != bake["frames"][1] - bake["frames"][0] + 1 or not blobs:
                missing.append("%s/%d" % (entry["modifier"], bake["id"]))
    return missing


def forget_partial(window, target, entries):
    """Make Blender forget what a stopped bake cached, without Blender deleting anything by path.

    Blender keeps a stopped bake's frames as the simulation's bake. Its own
    Delete Bake resets that, and also deletes the bake's folder; with the
    directory cleared first it finds no folder to delete. The staged files are
    removed by the job itself, through the folders it holds
    (``job_outputs.Staging.discard``). The cleared directory is one of the
    job's recorded settings, so the restore that follows puts the person's back.
    """
    with bpy.context.temp_override(window=window, active_object=target, object=target):
        for entry in entries:
            modifier = target.modifiers.get(entry["modifier"])
            if modifier is None:
                continue  # Removed meanwhile: nothing of it is left to reset.
            modifier.bake_directory = ""
            for bake in entry["bakes"]:
                bpy.ops.object.geometry_node_bake_delete_single(session_uid=target.session_uid,
                                                                modifier_name=entry["modifier"], bake_id=bake["id"])


def point_at(target, entries, published):
    """Make the scene read each simulation's bake from the published folder, by a path relative to the .blend."""
    base = blend_relative(published)
    for entry in entries:
        modifier = target.modifiers[entry["modifier"]]
        modifier.bake_target, modifier.bake_directory = 'DISK', "%s/%s" % (base, entry["folder"])
        for bake in entry["bakes"]:
            modifier.bakes[bake["index"]].bake_target = 'INHERIT'
            modifier.bakes[bake["index"]].use_custom_path = False


def outputs(target, entries):
    """What a finished disk bake reports for each cache: its folder in the output and what it is."""
    return [{"file": "%s/%d" % (entry["folder"], bake["id"]), "cache": "%s/%d" % (entry["modifier"], bake["id"]),
             "name": target.name, "frames": list(bake["frames"])}
            for entry in entries for bake in entry["bakes"]]
