# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Piece 10 MUST-A, engine link group (p10c-2-engine), for test_blender_confinement_escapes: a background child of
the engine link (the write-back) writes where its parent's spec says, in a process where C answers yes for any path
(no script region ever begins there). A session whose asset object names a file of the person's (as a script that
reached the object could make it) must start no child that writes it: the parent asks C about the child's targets
(``engine_link_writeback.child_spec``). Failed before that fix: the real child exported over the person's file."""

import os
import shutil
import tempfile
import time


def write_back_named_elsewhere(run, check, plain):
    import bpy
    from angelo_agent import engine_link_assets, engine_link_writeback
    run("result = 1")   # A script region has begun in this process: C answers by its facts from here on.
    project = tempfile.mkdtemp(prefix="engine-children-person-", dir=str(plain))   # A folder of the person's.
    try:
        person = os.path.join(project, "thesis.glb")
        with open(person, "wb") as handle:
            handle.write(b"the person's model")
        asset = engine_link_assets.Asset("godot", project, "res://thesis.glb", person, ".glb", "thesis.glb")
        blend = os.path.join(tempfile.mkdtemp(prefix="engine-children-blend-", dir=str(plain)), "scene.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True, check_existing=False)   # A real save for the child.
        try:   # The program is read from C at each start; a tree from before that takes it here (fail-first run).
            writer = engine_link_writeback.WriteBack()
        except TypeError:
            writer = engine_link_writeback.WriteBack(bpy.app.binary_path)
        # What engine_link_saves.saved does for a save of this session: the asset's path, its stamp beside it.
        writer.request("escape", blend, asset.path, "gltf", {}, stamp={
            "path": engine_link_assets.stamp_path(asset), "asset": asset.ident, "save": 1, "link": 1,
            "writer": engine_link_assets.WRITER}, saved=os.stat(blend))
        done, deadline = [], time.monotonic() + 300
        while not done and time.monotonic() < deadline:
            done = writer.finished()
            time.sleep(0.1)
        with open(person, "rb") as handle:
            kept = handle.read() == b"the person's model"
        stamped = os.path.exists(engine_link_assets.stamp_path(asset))
        check("engine_child_writes_no_file_its_state_names", kept and not stamped and done == [
            ("escape", "PermissionError")], (done, kept, stamped))
    finally:
        shutil.rmtree(project, ignore_errors=True)


def run_all(run, check, plain):
    write_back_named_elsewhere(run, check, plain)
