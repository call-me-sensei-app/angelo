# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Unreal side of unreal_link_e2e/editor_run.py, run inside a headless Unreal Editor as the test project's own
``Content/Python/init_unreal.py`` (``-ExecutePythonScript`` would quit the editor once the script returns).

Imports ``SourceArt/crate.glb`` as a static mesh, starts the shipped Angelo Link plugin (an unattended editor doesn't
start it by itself) and asks Angelo to edit the mesh, as Edit in Angelo does; then, from the editor's tick, waits for
Angelo's save to be re-imported by the plugin, checks the mesh holds the edit and its package was saved, stops the
link and quits the editor. Writes what it saw to ``ANGELO_UNREAL_REPORT``. Test only; never shipped.

With ``ANGELO_UNREAL_LIVE=1`` (the Angelo Link Live plugin enabled) it also places the mesh twice in a saved level
(at the origin, and moved, turned and scaled), and checks Angelo's live scenes show in place of both instances before
any save (the first matching the instances, the edit then showing while the asset, its source file, the level and
the asset's package stay unchanged), that the previews' world bounds agree with the instances' after the re-import of
Angelo's save, and that the previews end once that re-import is saved. Headless Unreal never throttles, so the
throttle checks read the live layer's session flag only.
"""

import json
import os
import sys
import time
import traceback

import unreal

REPORT = os.environ["ANGELO_UNREAL_REPORT"]
LIMIT = float(os.environ.get("ANGELO_UNREAL_SECONDS", "240"))
SOURCE = os.environ["ANGELO_UNREAL_SOURCE"]
ANGELO_REPORT = os.environ["ANGELO_UNREAL_ANGELO_REPORT"]   # The Angelo side's angelo.json: what its save exported.
CM = 100.0   # Blender metres -> Unreal centimetres; Interchange keeps Z up.
LIVE = os.environ.get("ANGELO_UNREAL_LIVE") == "1"
state = {"phase": "start", "steps": [], "checks": {}, "engine": unreal.SystemLibrary.get_engine_version()}
started = time.monotonic()


def step(phase):
    state["phase"] = phase
    state["steps"].append({"phase": phase, "at": round(time.monotonic() - started, 2)})


def bounds(mesh):
    box = mesh.get_bounding_box()
    return [round(value, 3) for value in (box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z)]


def matches_export(mesh, now):
    """Whether the re-imported asset is the whole scene Angelo saved: its height range, its two horizontal extents (in
    either order: the axis convention is the importer's) and its triangle count, against the Angelo side's record of
    the export. A re-import that kept only some meshes fails here (UE 5.8.2 once kept only the added cube)."""
    try:
        with open(ANGELO_REPORT, encoding="utf-8") as handle:
            export = json.load(handle).get("export")
    except (OSError, ValueError):
        export = None
    state["export"] = export
    if not export:
        return False
    low, high = export["min"], export["max"]
    triangles = mesh.get_num_triangles(0)
    state["triangles"] = triangles
    close = lambda a, b: abs(a - b) <= 0.5   # Half a centimetre.
    height = close(now[2], low[2] * CM) and close(now[5], high[2] * CM)
    across = sorted((now[3] - now[0], now[4] - now[1]))
    expected = sorted(((high[0] - low[0]) * CM, (high[1] - low[1]) * CM))
    return height and all(close(a, b) for a, b in zip(across, expected)) and triangles == export["triangles"]
def box_list(box):
    return [round(value, 3) for value in (box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z)]


def close_to(first, second, tolerance=1.0):
    return len(first) == len(second) and all(abs(a - b) <= tolerance for a, b in zip(first, second))


def unchanged_while_live():
    """What a live preview must leave alone: the asset, its source file, the level and the asset's package."""
    mesh = unreal.EditorAssetLibrary.load_asset(state["package"])
    dirty_maps = [package.get_name() for package in unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages()]
    dirty = [package.get_name() for package in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()]
    return {"asset_unchanged": bounds(mesh) == state["bounds_before"],
            "source_unsaved": os.stat(SOURCE).st_mtime_ns == state["source_mtime"],
            "level_clean": not dirty_maps, "asset_package_clean": state["package"] not in dirty}


def instances_box():
    """World bounds of the placed instances (their mesh components, hidden or not)."""
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    found = [actor for actor in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.StaticMeshActor)
             if actor.get_name() in state["instances"]]
    low, high = [float("inf")] * 3, [float("-inf")] * 3
    for actor in found:
        origin, extent = actor.get_actor_bounds(False, True)
        for axis, name in enumerate("xyz"):
            low[axis] = min(low[axis], getattr(origin, name) - getattr(extent, name))
            high[axis] = max(high[axis], getattr(origin, name) + getattr(extent, name))
    return [round(value, 3) for value in low + high]


def watch_live():
    """Phase "linked" with the live layer: the first preview, then Angelo's edit, both before any save."""
    native = unreal.AngeloLinkLiveLibrary
    if native.preview_count() < 1:
        return False
    shown = box_list(native.preview_bounds())
    if "first_preview" not in state:
        state["first_preview"] = shown
        state["instances_before"] = instances_box()
        state["checks"]["first_preview_matches_instances"] = close_to(shown, state["instances_before"])
        state["checks"]["every_instance_previewed"] = native.preview_count() == len(state["instances"])
        return False
    if close_to(shown, state["first_preview"]):
        return False   # Still the shape Angelo opened: wait for its edit.
    state["live_preview"] = shown
    state["live_triangles"] = native.preview_triangles()
    for name, ok in unchanged_while_live().items():
        state["checks"]["live_" + name] = ok
    state["checks"]["live_session_flag_set"] = native.is_session_live()
    step("live_edit_shown")
    state["phase"] = "linked"   # Then Angelo saves: the durable path below.
    state["live_seen"] = True
    return True


def finish(problem=None):
    if state.get("tick") is not None:
        unreal.unregister_slate_post_tick_callback(state.pop("tick"))
    state["problem"] = problem or ""
    state["ok"] = problem is None and bool(state["checks"]) and all(state["checks"].values())
    state.pop("tick", None)
    state.pop("editor", None)
    with open(REPORT, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, default=str)
    unreal.SystemLibrary.quit_editor()


def import_mesh(source=None, folder="/Game/Props"):
    task = unreal.AssetImportTask()
    for key, value in (("filename", source or SOURCE), ("destination_path", folder), ("automated", True),
                       ("save", True), ("replace_existing", True)):
        task.set_editor_property(key, value)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    paths = [str(path) for path in task.get_editor_property("imported_object_paths")]
    state.setdefault("imported", []).extend(paths)
    library = unreal.EditorAssetLibrary
    meshes = [library.load_asset(path) for path in paths]
    return [mesh for mesh in meshes if isinstance(mesh, unreal.StaticMesh)]


def tick(_delta):
    try:
        editor = state["editor"]
        if time.monotonic() - started > LIMIT:
            finish("timed out in phase %s" % state["phase"])
            return
        if state["phase"] == "asked" and editor._state["linked"] == state["package"]:
            state["checks"]["linked"] = True
            step("linked")
        elif state["phase"] == "linked" and LIVE and not state.get("live_seen"):
            watch_live()
        elif state["phase"] == "linked":
            mesh = unreal.EditorAssetLibrary.load_asset(state["package"])
            now = bounds(mesh)
            if now != state["bounds_before"]:   # The plugin re-imported Angelo's save.
                state["bounds_after"] = now
                state["checks"]["reimport_holds_every_mesh"] = matches_export(mesh, now)
                data = mesh.get_editor_property("asset_import_data")
                state["import_data"] = type(data).__name__
                state["checks"]["source_unchanged"] = os.path.samefile(list(data.extract_filenames())[0], SOURCE)
                if LIVE:
                    state["instances_after"] = instances_box()
                    state["checks"]["preview_matched_reimport"] = close_to(state["live_preview"],
                                                                            state["instances_after"])
                step("reimported")
                return
        elif state["phase"] == "reimported":
            dirty = [package.get_name() for package in
                     unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()]
            waited = time.monotonic() - started - state["steps"][-1]["at"]
            if state["package"] in dirty and waited < 15.0:
                return   # The plugin saves once the re-imported mesh is built.
            if LIVE and unreal.AngeloLinkLiveLibrary.preview_count() > 0 and waited < 15.0:
                return   # ... and then shows the asset itself again.
            state["dirty_packages"] = dirty
            state["seconds_to_save"] = round(waited, 2)
            state["checks"]["package_saved"] = state["package"] not in dirty
            if LIVE:
                state["checks"]["preview_ended_after_save"] = unreal.AngeloLinkLiveLibrary.preview_count() == 0
            editor.stop()   # The person turns the link off: Angelo hears close and ends.
            state["checks"]["stopped"] = editor._state["client"] is None
            if LIVE:
                state["checks"]["live_session_flag_cleared"] = not unreal.AngeloLinkLiveLibrary.is_session_live()
            step("stopped")
            finish()
    except Exception:
        state["traceback"] = traceback.format_exc()
        finish("error")


def main(_delta):
    """Once, on the editor's first tick: everything has started by then."""
    unreal.unregister_slate_post_tick_callback(state.pop("start"))
    try:
        state["checks"]["python_plugin_path"] = any(path.replace("\\", "/").endswith("AngeloLink/Content/Python")
                                                    for path in sys.path)
        from angelo_link import editor   # The shipped plugin, from its Content/Python.
        state["editor"] = editor
        # A source imported as several assets (Interchange's default: each glTF mesh its own static mesh) can't be
        # written back into one of them: Edit in Angelo refuses it before asking Angelo.
        pair = import_mesh(os.path.join(os.path.dirname(SOURCE), "pair.glb"), "/Game/Pair")
        state["checks"]["pair_imported_as_two"] = len(pair) == 2
        state["pair_assets_from_source"] = editor.describe(pair[0])["assets_from_source"] if pair else None
        state["checks"]["split_source_counted"] = state["pair_assets_from_source"] == 2
        state["checks"]["split_source_refused"] = bool(pair) and editor.edit_asset(pair[0], "Leg") is False
        meshes = import_mesh()
        state["checks"]["mesh_imported"] = len(meshes) == 1
        if len(meshes) != 1:
            finish("expected one static mesh, got %s" % state.get("imported"))
        else:
            mesh = meshes[0]
            state["package"] = mesh.get_outermost().get_name()
            state["bounds_before"] = bounds(mesh)
            state["checks"]["one_mesh_source_counted_once"] = editor.describe(mesh)["assets_from_source"] == 1
            state["source_mtime"] = os.stat(SOURCE).st_mtime_ns
            if LIVE:
                state["checks"]["live_layer_loaded"] = editor.live.available()
                world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
                actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
                placed = [actors.spawn_actor_from_object(mesh, unreal.Vector(0.0, 0.0, 0.0)),
                          actors.spawn_actor_from_object(mesh, unreal.Vector(500.0, -300.0, 40.0),
                                                         unreal.Rotator(0.0, 30.0, 90.0))]
                placed[1].set_actor_scale3d(unreal.Vector(1.5, 0.5, 2.0))
                state["instances"] = [actor.get_name() for actor in placed]
                state["checks"]["level_saved"] = unreal.EditorLoadingAndSavingUtils.save_map(world, "/Game/Maps/LinkTest")
            editor.start()
            menus = unreal.ToolMenus.get()
            state["checks"]["menus_registered"] = all(menus.find_menu(name) is not None for name in
                                                      ("ContentBrowser.AssetContextMenu", "LevelEditor.ActorContextMenu"))
            state["checks"]["asked_angelo"] = editor.edit_asset(mesh, "Crate")
            step("asked")
            state["tick"] = unreal.register_slate_post_tick_callback(tick)
    except Exception:
        state["traceback"] = traceback.format_exc()
        finish("error")


state["start"] = unreal.register_slate_post_tick_callback(main)
