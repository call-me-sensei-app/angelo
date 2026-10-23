# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio as a Blender asset library, in a real background Blender (``studio_assets*``).

An isolated Studio library gets a model with one object, a model with several, an image, a file that is no GLB and a
GLB that raises part-way through its import (fixtures this test writes; no provider, account or network). Checked:
the Preferences entry is added once, found again when renamed, stays out once removed, comes back after a real
preference read (Load Factory Preferences) and when the person adds the folder again; the real background writer
makes an Object and a Collection asset holding the model itself and a Material asset, each with catalogue,
description, tags and a preview; a failing GLB fails alone. A model linked, appended or instanced through Blender's
own operators is the model at the GLB's own size, standing on the origin, and stays so through save, reopen and
move. The library references Studio's picture (nothing stored twice) and an appended material's picture is packed
into the person's file, so it survives save, reopen, move and rename, and a second drop keeps one copy; an unchanged
library writes nothing, a folder move rewrites only that item, a removed record removes its file, and a failed item
is retried once per session. Receipt: ``studio-assets.json``.
"""

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

import bpy
from mathutils import Vector

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-assets-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks, details = {}, {}


# The runner sets this where the machine has no working OpenGL: Blender's background render then ends the process.
NO_OPENGL = bool(os.environ.get("ANGELO_QA_NO_OPENGL"))


def writer_report(directory, label):
    """What the writer saw, printed before any check reads its files (a Windows run's writer log and crash file stayed
    on its machine): the manifest (each failed item's error) and a crash file a writer left in the temporary folder.
    The writer's own output, when it ended early, is already on this console (``studio_assets_host._print_log``)."""
    from angelo_agent import studio_assets
    print(f"ANGELO_WRITER_MANIFEST {label}", json.dumps(studio_assets.read_manifest(directory)), flush=True)
    for crash in sorted(Path(tempfile.gettempdir()).glob("*.crash.txt")):
        print(f"ANGELO_WRITER_CRASH {crash}:\n" + crash.read_text("utf-8", "replace")[-6000:], flush=True)


def printed(binary, batch, stop, yield_to):
    """The real writer, its per-item results printed as they come back (a preview's fate is only there)."""
    from angelo_agent import studio_assets_host
    results = studio_assets_host.convert_batch(binary, batch, stop, yield_to)
    print("ANGELO_WRITER_RESULTS", json.dumps(results), flush=True)
    return results


def check(name, value, detail=None):
    """Receipt contract (native_suite_checks): each check is exactly True or False; details are kept apart."""
    checks[name] = bool(value)
    if detail is not None:
        details[name] = detail


def bounds(objects):
    points = [o.matrix_world @ Vector(c) for o in objects if o.type == 'MESH' for c in o.bound_box]
    low = Vector(map(min, *(p.to_tuple() for p in points)))
    high = Vector(map(max, *(p.to_tuple() for p in points)))
    return low, high


def fixtures(folder):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0.3, 0.2, 1.0))
    bpy.context.object.scale = (1, 1, 2)
    bpy.ops.export_scene.gltf(filepath=str(folder / "single.glb"), export_format='GLB')
    bpy.ops.mesh.primitive_cube_add(size=1, location=(2, 0, 0.5))
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.export_scene.gltf(filepath=str(folder / "multi.glb"), export_format='GLB')
    image = bpy.data.images.new("grid", 96, 64)
    image.generated_type = 'COLOR_GRID'
    image.filepath_raw, image.file_format = str(folder / "image-1.png"), 'PNG'
    image.save()
    bpy.ops.wm.read_factory_settings(use_empty=True)


def scene_model():
    """What the evaluated scene shows, however the model got there (object, collection, instance, linked or not):
    ``(mesh instances, vertices, world size)``."""
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points, count, vertices = [], 0, 0
    for instance in depsgraph.object_instances:
        if instance.object.type != 'MESH':
            continue
        count += 1
        vertices += len(instance.object.data.vertices)
        points += [instance.matrix_world @ Vector(c) for c in instance.object.bound_box]
    if not points:
        return 0, 0, None
    low = Vector(map(min, *(p.to_tuple() for p in points)))
    high = Vector(map(max, *(p.to_tuple() for p in points)))
    return count, vertices, tuple(round(v, 4) for v in high - low)


def damaged_mid_import(good):
    """A GLB that raises part-way through its import: an accessor claims more vertices than its buffer holds."""
    import struct
    length = struct.unpack_from("<I", good, 12)[0]
    doc = json.loads(good[20:20 + length])
    doc["accessors"][doc["meshes"][-1]["primitives"][0]["attributes"]["POSITION"]]["count"] *= 1000
    text = json.dumps(doc).encode()
    text += b" " * ((4 - len(text) % 4) % 4)
    rest = good[20 + length:]
    return (struct.pack("<III", 0x46546C67, 2, 20 + len(text) + len(rest)) + struct.pack("<II", len(text), 0x4E4F534A)
            + text + rest)


def imported_size(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    objects = [o for o in bpy.data.objects if o not in before]
    low, high = bounds(objects)
    bpy.data.batch_remove(objects)
    return tuple(round(v, 4) for v in high - low)


def main():
    from angelo_agent import studio_assets, studio_assets_host
    from angelo_agent.studio_state import StudioLibrary
    bpy.context.preferences.use_preferences_save = False
    with tempfile.TemporaryDirectory(prefix="angelo-studio-assets-") as scratch:
        scratch = Path(scratch)
        os.environ["ANGELO_LIBRARY_DIR"] = str(scratch / "media-studio")
        (scratch / "fx").mkdir()
        fixtures(scratch / "fx")
        library, directory = studio_assets_host.directories()
        libraries = bpy.context.preferences.filepaths.asset_libraries
        others = [(lib.name, lib.path) for lib in libraries]

        # Registration.
        state = studio_assets_host._state
        check("added_once", studio_assets_host.ensure_registered() and studio_assets_host.ensure_registered()
              and sum(studio_assets.same_directory(lib.path, directory) for lib in libraries) == 1)
        entry = studio_assets.find_library(libraries, directory)
        check("named", entry.name == studio_assets.LIBRARY_NAME, entry.name)
        check("appends", entry.import_method == 'APPEND', entry.import_method)   # A drop is local: its picture packs.
        entry.name = "My models"
        state["registered"] = True
        check("rename_kept", studio_assets_host.observe() and studio_assets_host.ensure_registered()
              and len(libraries) == len(others) + 1)
        libraries.remove(entry)
        state["registered"] = studio_assets_host.observe()
        check("removal_recorded", not state["registered"]
              and studio_assets.read_registration(directory).get("removed") is True)
        check("removal_stays", not studio_assets_host.ensure_registered() and len(libraries) == len(others))
        libraries.new(name="Again", directory=str(directory))
        state["registered"] = studio_assets_host.observe()
        check("readded_resumes", state["registered"] and not studio_assets.read_registration(directory)["removed"])
        check("others_untouched", [(lib.name, lib.path) for lib in libraries][:len(others)] == others)
        libraries.remove(studio_assets.find_library(libraries, directory))
        libraries.new(name=studio_assets.LIBRARY_NAME, directory=str(directory))
        # A real preference read (Load Factory Preferences) through Angelo's wrapper of Blender's add-on reset.
        from angelo_agent import prefs_reload
        prefs_reload.install()
        prefs_reload.READ_LISTENERS.append(studio_assets_host.preferences_read)
        try:
            bpy.ops.wm.read_factory_userpref()
        finally:
            prefs_reload.READ_LISTENERS.remove(studio_assets_host.preferences_read)
            prefs_reload.uninstall(force=True)
        libraries = bpy.context.preferences.filepaths.asset_libraries
        bpy.context.preferences.use_preferences_save = False
        check("read_drops_entry", studio_assets.find_library(libraries, directory) is None)
        state["registered"] = studio_assets_host.observe()
        check("factory_restores", state["registered"] and studio_assets.find_library(libraries, directory) is not None
              and not studio_assets.read_registration(directory)["removed"])

        # Studio records.
        studio = StudioLibrary(library)
        records = {}
        for key, kind, source, prompt in (("single", "3D", "single.glb", "A tall stone column, weathered"),
                                          ("multi", "3D", "multi.glb", "A sphere beside a crate"),
                                          ("image", "IMAGE", "image-1.png", "A colour grid poster"),
                                          ("broken", "3D", "image-1.png", "Not really a model"),
                                          ("halfway", "3D", "multi.glb", "Fails part-way through")):
            record = studio.add(kind, prompt, "Fixture model", status="ready")
            name = "model.glb" if kind == "3D" else "image-1.png"
            data = (scratch / "fx" / source).read_bytes()
            (studio.result_directory(record["id"]) / name).write_bytes(damaged_mid_import(data) if key == "halfway" else data)
            record.update(files=[name], thumbnail="" if kind == "3D" else name, progress=100)
            studio.update(record)
            records[key] = record

        calls = []

        def counted(binary, batch, stop, yield_to):
            calls.append([item["id"] for item in batch])
            return printed(binary, batch, stop, yield_to)
        if NO_OPENGL:
            state["previews"] = False   # As after a render ended a writer: this machine writes models without one.
        names = {"3D": "Models", "IMAGE": "Images"}
        sync = lambda session="one": studio_assets_host.sync(library, directory, names, bpy.app.binary_path,
                                                              session, threading.Event(), counted)
        first = sync()
        writer_report(directory, "first sync")
        check("first_sync_writes", first and len(calls) == 1 and len(calls[0]) == 5, list(calls))
        manifest = studio_assets.read_manifest(directory)
        check("broken_failed", all(manifest[records[k]["id"]].get("failed") is True for k in ("broken", "halfway")),
              [manifest[records[k]["id"]].get("error") for k in ("broken", "halfway")])
        failing = ("broken", "halfway")
        blend = {k: Path(studio_assets.item(library, r, {})["blend"]) for k, r in records.items() if k not in failing}
        check("files_written", all(p.is_file() for p in blend.values()))
        cats = (directory / studio_assets.CATS_FILE).read_text()
        check("catalogue_file", cats.count(f":{studio_assets.TAB_PATH}/Models:Models") == 1
              and f"{studio_assets.PARENT_CATALOG}:{studio_assets.TAB_PATH}:" in cats)

        calls.clear()
        check("unchanged_writes_nothing", not sync() and not calls)
        sync("two")
        check("failure_retried_next_session", sorted(calls[0]) == sorted(records[k]["id"] for k in failing)
              and len(calls) == 1, list(calls))

        # The assets as Blender reads them.
        found = {}
        for key, path in blend.items():
            with bpy.data.libraries.load(str(path), assets_only=True) as (source, target):
                found[key] = {kind: list(getattr(source, kind)) for kind in ("objects", "collections", "materials")}
        check("object_asset", found["single"]["objects"] == ["A tall stone column, weathered"], found["single"])
        check("collection_asset", found["multi"]["collections"] == ["A sphere beside a crate"], found["multi"])
        check("material_asset", found["image"]["materials"] == ["A colour grid poster"], found["image"])
        sizes = {key: imported_size(library / "outputs" / records[key]["id"] / "model.glb") for key in ("single", "multi")}
        with bpy.data.libraries.load(str(blend["single"]), link=False) as (source, target):
            target.objects = list(source.objects)
        column = target.objects[0]
        bpy.context.scene.collection.objects.link(column)
        check("asset_file_holds_the_model", len(column.data.vertices) > 8 and column.library is None,
              len(column.data.vertices))
        bpy.context.view_layer.update()
        low, high = bounds([column])
        data = column.asset_data
        check("asset_metadata", data and data.catalog_id == studio_assets.CATALOGS["3D"][0]
              and data.description.startswith("A tall stone") and "Fixture model" in [t.name for t in data.tags])
        if NO_OPENGL:   # Never passed here: a machine with OpenGL checks the render (the runner's needs_opengl rule).
            details["preview"] = "not checked: no working OpenGL on this machine (ANGELO_QA_NO_OPENGL)"
        else:
            check("preview", column.preview is not None and tuple(column.preview.image_size) != (0, 0),
                  tuple(column.preview.image_size) if column.preview else None)
        size = tuple(round(v, 4) for v in high - low)
        check("same_size_as_glb_import", size == sizes["single"], [size, sizes["single"]])
        check("stands_on_origin", abs(low.z) < 1e-4 and abs((low.x + high.x) / 2) < 1e-4
              and abs((low.y + high.y) / 2) < 1e-4, [low.to_tuple(), high.to_tuple()])
        with bpy.data.libraries.load(str(blend["multi"]), link=False) as (source, target):
            target.collections = list(source.collections)
        group = target.collections[0]
        bpy.context.scene.collection.children.link(group)
        bpy.context.view_layer.update()
        low, high = bounds(list(group.objects))
        base = Vector(((low.x + high.x) / 2, (low.y + high.y) / 2, low.z))
        check("collection_holds_the_model", len(group.objects) == 2
              and all(len(o.data.vertices) >= 8 for o in group.objects if o.type == 'MESH')
              and (group.instance_offset - base).length < 1e-4,
              [[o.name for o in group.objects], group.instance_offset.to_tuple(4), base.to_tuple(4)])

        # Link, append and instance, through Blender's own operators (what a drop and File > Link/Append run), each
        # the model at the GLB's size through save, reopen and move. A file that links is moved as Blender moves one
        # (Save As remaps its relative library paths); a local model is moved on disk.
        models = scratch / "person-models"
        for mode, key, kind, options in (("link", "single", "Object", {}),
                                         ("append", "single", "Object", {}),
                                         ("link", "multi", "Collection", {"instance_collections": True}),
                                         ("append", "multi", "Collection", {"instance_collections": False}),
                                         ("instance", "multi", "Collection", {"instance_collections": True})):
            case = f"{mode}_{key}"
            folder = models / case
            (folder / "first").mkdir(parents=True)
            (folder / "moved" / "deeper").mkdir(parents=True)
            bpy.ops.wm.read_homefile(use_empty=True)
            path, name = str(blend[key]), records[key]["prompt"]
            operator = bpy.ops.wm.link if mode == "link" else bpy.ops.wm.append
            operator(filepath=f"{path}/{kind}/{name}", directory=f"{path}/{kind}/", filename=name, **options)
            parts = 1 if key == "single" else 2
            usable = lambda shown: shown[0] == parts and shown[1] > 8 * parts and shown[2] == sizes[key]
            shown = scene_model()
            check(f"model_{case}_lands", usable(shown), shown)
            first = folder / "first" / "scene.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(first))
            for step, where in (("reopened", first), ("moved", folder / "moved" / "deeper" / "renamed.blend")):
                if where != first:
                    if mode == "link":
                        bpy.ops.wm.save_as_mainfile(filepath=str(where))
                    else:
                        first.rename(where)
                bpy.ops.wm.open_mainfile(filepath=str(where), load_ui=False)
                shown = scene_model()
                check(f"model_{case}_{step}", usable(shown), [shown, sizes[key]])

        bpy.ops.wm.read_homefile(use_empty=True)
        source_file = (library / "outputs" / records["image"]["id"] / "image-1.png").resolve()
        with bpy.data.libraries.load(str(blend["image"]), link=True) as (source, target):
            target.materials = list(source.materials)
        stored = next(n.image for n in target.materials[0].node_tree.nodes if n.type == 'TEX_IMAGE')
        check("library_references_studio_file", stored.packed_file is None
              and Path(stored.filepath).resolve() == source_file
              and stored.get(studio_assets_host.STUDIO_IMAGE) == records["image"]["id"], stored.filepath)
        with bpy.data.libraries.load(str(blend["image"]), link=False) as (source, target):
            target.materials = list(source.materials)
        appended = next(n.image for n in target.materials[0].node_tree.nodes if n.type == 'TEX_IMAGE')
        studio_assets_host.pack_landed_images()
        check("appended_image_loads", appended.packed_file is not None and len(appended.pixels) > 0
              and appended.has_data, appended.filepath)

        # Changes in the Studio flow one way into the files.
        stamps = {k: p.stat().st_mtime_ns for k, p in blend.items()}
        folder = studio.folder("Props")
        records["multi"]["folder"] = folder["id"]
        studio.update(records["multi"])
        calls.clear()
        check("move_rewrites_one", sync("two") and calls == [[records["multi"]["id"]]]
              and blend["single"].stat().st_mtime_ns == stamps["single"], list(calls))
        (library / "assets" / (records["image"]["id"] + ".json")).unlink()
        calls.clear()
        check("removed_record_removes_file", sync("two") and not blend["image"].exists() and not calls)
        check("manifest_forgets", records["image"]["id"] not in studio_assets.read_manifest(directory))

        # The texture of an appended image material through save, reopen, move and rename of the person's file,
        # appended into an unsaved file and into one saved elsewhere. Pixels, not the path string.
        poster = studio.add("IMAGE", "A poster to keep", "Fixture model", status="ready")
        (studio.result_directory(poster["id"]) / "image-1.png").write_bytes((scratch / "fx" / "image-1.png").read_bytes())
        poster.update(files=["image-1.png"], thumbnail="image-1.png", progress=100)
        studio.update(poster)
        sync("two")
        poster_blend = studio_assets.item(library, poster, {})["blend"]
        studio_assets_host.add_landing_handlers()   # The app's own handlers: save_pre packs what landed.
        for case in ("unsaved", "saved_elsewhere"):
            folder = scratch / ("person-" + case)
            (folder / "first").mkdir(parents=True)
            (folder / "moved" / "deeper").mkdir(parents=True)
            bpy.ops.wm.read_homefile(use_empty=True)
            if case == "saved_elsewhere":
                bpy.ops.wm.save_as_mainfile(filepath=str(folder / "first" / "scene.blend"))
            bpy.ops.wm.append(filepath=poster_blend + "/Material/A poster to keep", directory=poster_blend + "/Material/",
                              filename="A poster to keep")   # Blender's own default: relative_path=True.
            target = folder / "first" / "scene.blend"
            if case == "unsaved":
                bpy.ops.wm.save_as_mainfile(filepath=str(target))
            else:
                bpy.ops.wm.save_mainfile()
            for step, path in (("reopened", target), ("moved_and_renamed", folder / "moved" / "deeper" / "renamed.blend")):
                if path != target:
                    target.rename(path)
                bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
                material = bpy.data.materials.get("A poster to keep")
                image = next((n.image for n in material.node_tree.nodes if n.type == 'TEX_IMAGE'), None) if material else None
                loaded = (bool(image) and image.packed_file is not None and len(image.pixels) > 0 and image.has_data
                          and tuple(image.size) == (96, 64))
                check(f"texture_{case}_{step}", loaded, image.filepath if image else None)

        # The same material dropped twice: one packed copy, used by both.
        bpy.ops.wm.read_homefile(use_empty=True)
        for _ in range(2):
            bpy.ops.wm.append(filepath=poster_blend + "/Material/A poster to keep", directory=poster_blend + "/Material/",
                              filename="A poster to keep")
        studio_assets_host.pack_landed_images()
        from angelo_agent import studio_assets_landing
        studio_assets_landing._free_unused()   # What its timer runs (no timers in the background).
        pictures = [i for i in bpy.data.images if i.get(studio_assets_host.STUDIO_PACKED) == poster["id"]]
        used = {n.image.name for m in bpy.data.materials if m.node_tree for n in m.node_tree.nodes if n.type == 'TEX_IMAGE'}
        check("dropped_twice_one_copy", len(bpy.data.materials) == 2 and len(pictures) == 1
              and pictures[0].packed_file is not None and used == {pictures[0].name},
              [(i.name, bool(i.packed_file)) for i in pictures])
        # A second drop through a script: libraries.load hands back the very IDs the import listed, so the handler
        # never frees one there (the duplicate picture is freed later, from a timer).
        with bpy.data.libraries.load(poster_blend, link=False) as (source, target):
            target.materials = list(source.materials)
            target.images = list(source.images)
        returned = [i for i in target.images if i is not None]
        check("script_load_keeps_returned_ids_alive", len(returned) == 1 and bool(returned[0].name)
              and returned[0].users >= 0, [i.name for i in returned])
        # Review 4 (2): packed once, then the person's. An Unpack survives the next save.
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.ops.wm.append(filepath=poster_blend + "/Material/A poster to keep", directory=poster_blend + "/Material/",
                          filename="A poster to keep")
        unpack_file = scratch / "person-unpacks" / "scene.blend"
        unpack_file.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(unpack_file))
        bpy.ops.file.unpack_all(method='WRITE_LOCAL')
        bpy.ops.wm.save_mainfile()
        mine = [i for i in bpy.data.images if i.get(studio_assets_host.STUDIO_PACKED) == poster["id"]]
        check("an_unpack_stays_unpacked", len(mine) == 1 and mine[0].packed_file is None,
              [(i.name, bool(i.packed_file), i.filepath) for i in bpy.data.images])
        # Review 4 (3): the person paints the packed picture; a second drop brings Studio's own, untouched.
        bpy.ops.file.pack_all()
        painted = mine[0]
        painted.pixels.foreach_set([1.0, 0.0, 0.0, 1.0] * (painted.size[0] * painted.size[1]))
        painted.pack()
        bpy.ops.wm.append(filepath=poster_blend + "/Material/A poster to keep", directory=poster_blend + "/Material/",
                          filename="A poster to keep")
        from angelo_agent import studio_assets_landing
        studio_assets_landing._free_unused()
        images = {n.image for m in bpy.data.materials if m.node_tree for n in m.node_tree.nodes if n.type == 'TEX_IMAGE'}
        fresh = [i for i in images if i != painted]
        check("a_second_drop_keeps_studios_picture", len(images) == 2 and len(fresh) == 1
              and tuple(round(v, 3) for v in fresh[0].pixels[:4]) != (1.0, 0.0, 0.0, 1.0)
              and tuple(round(v, 3) for v in painted.pixels[:4]) == (1.0, 0.0, 0.0, 1.0),
              [(i.name, tuple(round(v, 3) for v in i.pixels[:4])) for i in images])
        studio_assets_host.remove_landing_handlers()
        # A collection model dropped with Instance off: Blender appends with autoselect, selects the first object and
        # moves the selected ones; the whole model goes.
        bpy.ops.wm.read_homefile(use_empty=True)
        multi_blend = studio_assets.item(library, records["multi"], studio.folders)["blend"]
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.wm.append(directory=multi_blend + "/Collection/", filename="A sphere beside a crate", autoselect=True,
                          instance_collections=False)
        dropped = next(c for c in bpy.data.collections if c.name.startswith("A sphere beside a crate"))
        first = dropped.objects[0]
        first.select_set(True)
        bpy.context.view_layer.objects.active = first
        placed = {o.name: o.matrix_world.translation.copy() for o in dropped.objects}
        bpy.ops.object.collection_external_asset_drop(session_uid=dropped.session_uid, use_instance=False,
                                                      location=(5.0, 5.0, 0.0))
        shifts = {o.name: (o.matrix_world.translation - placed[o.name]).to_tuple(3) for o in dropped.objects}
        check("instance_off_drop_moves_the_whole_model", len(shifts) == 2 and len(set(shifts.values())) == 1
              and set(shifts.values()) != {(0.0, 0.0, 0.0)}, shifts)

        # The library's disk: the size Media Studio shows counts Angelo's files; Turn off and Free up space delete
        # only them; a scene that appended keeps everything, one that linked a model loses it (the dialog says so);
        # Turn on writes the copies again.
        from angelo_agent import studio_assets_space as space, studio_library_ops
        measured = space.measure(directory)
        on_disk = sum(p.stat().st_size for p in directory.rglob("*.blend"))
        check("size_counts_the_copies", measured >= on_disk > 0 and studio_library_ops.size_text(measured),
              [measured, on_disk, studio_library_ops.size_text(measured)])
        bpy.ops.wm.read_homefile(use_empty=True)
        libraries = bpy.context.preferences.filepaths.asset_libraries
        studio_assets_host.turn_off()
        check("turn_off_removes_and_records", studio_assets.find_library(libraries, directory) is None
              and studio_assets.read_registration(directory)["removed"])
        freed = space.free(directory, active=False)
        check("free_deletes_the_copies", freed and freed[0] == measured and not list(directory.rglob("*.blend"))
              and (directory / studio_assets.REGISTRATION).is_file() and studio_assets_host.sync(
                  library, directory, names, bpy.app.binary_path, "three", threading.Event()) is False,
              [freed, [p.name for p in directory.rglob("*")]])
        bpy.ops.wm.open_mainfile(filepath=str(scratch / "person-saved_elsewhere" / "moved" / "deeper" / "renamed.blend"),
                                 load_ui=False)
        image = next((i for i in bpy.data.images if i.get(studio_assets_host.STUDIO_PACKED)), None)
        check("appended_scene_keeps_its_picture", image is not None and image.packed_file is not None
              and tuple(image.size) == (96, 64))
        bpy.ops.wm.open_mainfile(filepath=str(models / "link_single" / "moved" / "deeper" / "renamed.blend"),
                                 load_ui=False)
        check("linked_scene_loses_its_model", scene_model()[0] == 0, scene_model())
        bpy.ops.wm.open_mainfile(filepath=str(models / "append_single" / "moved" / "deeper" / "renamed.blend"),
                                 load_ui=False)
        check("appended_model_stays", scene_model()[2] == sizes["single"], scene_model())
        bpy.ops.wm.read_homefile(use_empty=True)
        studio_assets_host.turn_on()
        calls.clear()
        rewrote = studio_assets_host.sync(library, directory, names, bpy.app.binary_path, "three", threading.Event(),
                                          counted)
        check("turn_on_writes_again", rewrote and studio_assets.find_library(
            bpy.context.preferences.filepaths.asset_libraries, directory) is not None
            and len(list(directory.rglob("*.blend"))) >= 3, [len(c) for c in calls])

    report = {"success": all(checks.values()), "checks": checks, "details": details}
    (OUT / "studio-assets.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_STUDIO_ASSETS", json.dumps(report), flush=True)


main()
