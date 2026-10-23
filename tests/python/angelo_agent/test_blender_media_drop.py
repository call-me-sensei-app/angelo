# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where Media Studio's items land, in a real background Blender (``media_place``, ``media_drop``, ``studio_drag``).

An isolated Studio library (a one-object model, a two-object model, an image item with two pictures; fixtures this
test writes, no provider or network) is written by the real background writer. Checked: a model stands at the point
at its GLB's size, appended or linked; several stand side by side without overlapping; a two-object model's base
stands at the point; images become reference images (packed, Blender's reference size) and materials on objects
(the first on the active slot, the rest as more slots, pictures packed); a drop's identifiers map an image's
materials to its pictures; the menu actions (Add to Scene, Apply to Selected) and the drop operator do the same; an
item missing from the library is written at once; with the library off a model comes in through Blender's glTF
import at the same size and an image's material is made from its picture. Receipt: ``media-drop.json``.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-media-drop-qa"))
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
    checks[name] = bool(value)
    if detail is not None:
        details[name] = detail


def bounds(objects):
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    wanted = set()
    for obj in objects:
        wanted.add(obj.name)
        if obj.instance_type == 'COLLECTION' and obj.instance_collection:
            wanted |= {o.name for o in obj.instance_collection.all_objects}
    for instance in depsgraph.object_instances:
        if instance.object.type == 'MESH' and (instance.object.original.name in wanted
                                               or (instance.parent and instance.parent.original.name in wanted)):
            points += [instance.matrix_world @ Vector(c) for c in instance.object.bound_box]
    low = Vector(map(min, *(p.to_tuple() for p in points)))
    high = Vector(map(max, *(p.to_tuple() for p in points)))
    return low, high


def refused(operator, **properties):
    """Run an operator that may refuse: its result, or {'CANCELLED'} with the reason when it reported an error."""
    try:
        return operator(**properties), ""
    except RuntimeError as error:
        return {'CANCELLED'}, str(error)


def size_of(objects):
    low, high = bounds(objects)
    return tuple(round(v, 3) for v in high - low)


def fixtures(folder):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0.3, 0.2, 1.0))
    bpy.context.object.scale = (1, 1, 2)
    bpy.ops.export_scene.gltf(filepath=str(folder / "single.glb"), export_format='GLB')
    bpy.ops.mesh.primitive_cube_add(size=1, location=(2, 0, 0.5))
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.export_scene.gltf(filepath=str(folder / "multi.glb"), export_format='GLB')
    for index, kind in enumerate(('COLOR_GRID', 'UV_GRID'), 1):
        image = bpy.data.images.new(f"grid{index}", 64, 32)
        image.generated_type = kind
        image.filepath_raw, image.file_format = str(folder / f"image-{index}.png"), 'PNG'
        image.save()
    bpy.ops.wm.read_factory_settings(use_empty=True)


def main():
    from angelo_agent import media_drop, media_place as place, studio_assets, studio_assets_host as host, studio_drag
    from angelo_agent.studio_state import StudioLibrary
    bpy.context.preferences.use_preferences_save = False
    for cls in media_drop.classes:
        bpy.utils.register_class(cls)
    with tempfile.TemporaryDirectory(prefix="angelo-media-drop-") as scratch:
        scratch = Path(scratch)
        os.environ["ANGELO_LIBRARY_DIR"] = str(scratch / "media-studio")
        (scratch / "fx").mkdir()
        fixtures(scratch / "fx")
        library, directory = host.directories()
        host.ensure_registered()
        entry = studio_assets.find_library(bpy.context.preferences.filepaths.asset_libraries, directory)
        studio = StudioLibrary(library)
        records = {}
        for key, kind, sources, prompt in (("single", "3D", ["single.glb"], "A tall stone column"),
                                           ("multi", "3D", ["multi.glb"], "A sphere beside a crate"),
                                           ("image", "IMAGE", ["image-1.png", "image-2.png"], "Two posters")):
            record = studio.add(kind, prompt, "Fixture model", status="ready")
            names = ["model.glb"] if kind == "3D" else sources
            for source, name in zip(sources, names):
                (studio.result_directory(record["id"]) / name).write_bytes((scratch / "fx" / source).read_bytes())
            record.update(files=names, thumbnail="" if kind == "3D" else names[0], progress=100)
            studio.update(record)
            records[key] = record
        imported = {}
        for key in ("single", "multi"):
            before = set(bpy.data.objects)
            bpy.ops.import_scene.gltf(filepath=str(studio.result_directory(records[key]["id"]) / "model.glb"))
            made = [o for o in bpy.data.objects if o not in before]
            imported[key] = size_of(made)
            bpy.data.batch_remove(made)
        if NO_OPENGL:
            host._state["previews"] = False   # As after a render ended a writer: models are written without one.
        check("writer_wrote_all", host.sync(library, directory, {"3D": "Models", "IMAGE": "Images"}, bpy.app.binary_path,
                                            "one", threading.Event(), printed))
        writer_report(directory, "first sync")
        ids = {k: r["id"] for k, r in records.items()}

        # Models: standing at the point at the GLB's size, appended or linked; several side by side.
        bpy.ops.wm.read_homefile(use_empty=True)
        point = Vector((2.0, 3.0, 0.0))
        column = place.add_models(bpy.context, place.from_ids([ids["single"]]), point)
        low, high = bounds(column)
        check("model_stands_at_the_point", abs(low.z) < 1e-3 and (Vector(((low.x + high.x) / 2, (low.y + high.y) / 2, 0))
                                                                  - point).length < 1e-3, [low[:], high[:]])
        check("model_at_its_size", size_of(column) == imported["single"], [size_of(column), imported["single"]])
        group = place.add_models(bpy.context, place.from_ids([ids["multi"]]), Vector((-4.0, 0.0, 0.0)))
        low, high = bounds(group)
        check("collection_base_at_the_point", abs(low.z) < 1e-3 and abs((low.x + high.x) / 2 + 4.0) < 1e-3
              and size_of(group) == imported["multi"], [low[:], high[:], size_of(group)])
        bpy.ops.wm.read_homefile(use_empty=True)
        row = place.add_models(bpy.context, place.from_ids([ids["single"], ids["multi"]]), Vector((0.0, 0.0, 0.0)))
        first, second = bounds(row[:1]), bounds(row[1:])
        check("several_side_by_side", first[1].x <= second[0].x + 1e-3 and abs(first[0].z) < 1e-3
              and abs(second[0].z) < 1e-3, [first[0][:], first[1][:], second[0][:], second[1][:]])
        bpy.ops.wm.read_homefile(use_empty=True)
        entry = studio_assets.find_library(bpy.context.preferences.filepaths.asset_libraries, directory)
        entry.import_method = 'LINK'
        linked = place.add_models(bpy.context, place.from_ids([ids["single"], ids["multi"]]), Vector((1.0, 1.0, 0.0)))
        check("linked_models_land_whole", len(linked) == 2 and linked[0].library is not None
              and linked[1].instance_type == 'COLLECTION' and size_of(linked[:1]) == imported["single"]
              and size_of(linked[1:]) == imported["multi"], [(o.name, bool(o.library)) for o in linked])
        entry.import_method = 'APPEND'

        # A linked object stays where Blender links it, before and after a save and reopen (its place is the
        # library's: a move would not be saved).
        bpy.ops.wm.read_homefile(use_empty=True)
        entry = studio_assets.find_library(bpy.context.preferences.filepaths.asset_libraries, directory)
        entry.import_method = 'LINK'
        place.add_models(bpy.context, place.from_ids([ids["single"]]), Vector((4.0, 5.0, 0.0)))
        saved = scratch / "linked.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(saved))
        before = [o.location[:] for o in bpy.data.objects if o.library]
        bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False)
        after = [o.location[:] for o in bpy.data.objects if o.library]
        check("linked_object_keeps_its_place_on_reopen", before and before == after, [before, after])
        entry = studio_assets.find_library(bpy.context.preferences.filepaths.asset_libraries, directory)
        entry.import_method = 'APPEND'

        # The Outliner's targets: the scene's own collection, never the active one; an image needs an object.
        bpy.ops.wm.read_homefile(use_empty=True)
        sub = bpy.data.collections.new("Sub")
        bpy.context.scene.collection.children.link(sub)
        bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection.children["Sub"]
        result = bpy.ops.angelo.media_drop(target="OUTLINER", library=entry.name, into="SCENE",
                                           items=f"models/{ids['single']}.blend/Object/A tall stone column\n")
        landed = [o for o in bpy.data.objects if o.type == 'MESH']
        check("outliner_scene_collection", result == {'FINISHED'} and landed
              and [c.name for c in landed[0].users_collection] == [bpy.context.scene.collection.name],
              [c.name for c in landed[0].users_collection] if landed else None)
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.object.select_set(True)
        selected = {o.name for o in bpy.context.selected_objects}
        result, reason = refused(bpy.ops.angelo.media_drop, target="OUTLINER", library=entry.name,
                                 items=f"images/{ids['image']}.blend/Material/Two posters\n")
        check("outliner_image_without_object_changes_nothing", result == {'CANCELLED'} and reason
              and {o.name for o in bpy.context.selected_objects} == selected, [result, reason])

        # All or nothing: a drop with one file gone adds nothing.
        bpy.ops.wm.read_homefile(use_empty=True)
        missing = studio_assets.item(library, records["multi"], studio.folders)["blend"]
        Path(missing).rename(missing + ".away")
        result, reason = refused(bpy.ops.angelo.media_drop, target="VIEW3D", library=entry.name,
                                 items=f"models/{ids['single']}.blend/Object/A tall stone column\n"
                                       f"models/{ids['multi']}.blend/Collection/A sphere beside a crate\n")
        Path(missing + ".away").rename(missing)
        check("a_drop_with_a_missing_file_adds_nothing", result == {'CANCELLED'} and reason and not bpy.data.objects,
              [result, reason])

        # One picture lands once, however often: a reference, then the same picture as a material.
        bpy.ops.wm.read_homefile(use_empty=True)
        first = place.from_ids([ids["image"]])[:1]
        place.add_references(bpy.context, first, Vector())
        bpy.ops.mesh.primitive_cube_add()
        place.apply_materials([bpy.context.object], first)
        from angelo_agent import studio_assets_landing
        studio_assets_landing._free_unused()
        packed = [i.name for i in bpy.data.images if i.packed_file and i.users]
        check("one_picture_packed_once", len(packed) == 1, packed)

        # Images: references, materials, the identifiers' mapping.
        bpy.ops.wm.read_homefile(use_empty=True)
        images = place.from_ids([ids["image"]])
        refs = place.add_references(bpy.context, images, Vector((0.0, 0.0, 1.0)))
        check("references_packed_at_blenders_size", len(refs) == 2 and all(
            r.empty_display_type == 'IMAGE' and r.data.packed_file is not None and r.empty_display_size == 5.0
            for r in refs) and refs[0].location != refs[1].location, [(r.name, r.location[:]) for r in refs])
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        applied = place.apply_materials([cube], images)
        textures = [n.image for s in cube.material_slots for n in s.material.node_tree.nodes if n.type == 'TEX_IMAGE']
        check("materials_on_the_object", applied == 1 and len(cube.material_slots) == 2
              and all(t.packed_file is not None for t in textures)
              and Path(textures[0].filepath).name == "image-1.png", [Path(t.filepath).name for t in textures])
        mapped = place.from_identifiers(f"images/{ids['image']}.blend/Material/Two posters 2\n")
        check("identifier_maps_to_its_picture", len(mapped) == 1 and Path(mapped[0].picture).name == "image-2.png",
              [m.picture for m in mapped])

        # The drop operator (exec: at the 3D cursor) and the menu actions.
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.scene.cursor.location = (5.0, 0.0, 0.0)
        result = bpy.ops.angelo.media_drop(target="VIEW3D", library=entry.name,
                                           items=f"models/{ids['single']}.blend/Object/A tall stone column\n")
        dropped = [o for o in bpy.data.objects if o.type == 'MESH']
        check("drop_operator_places_the_model", result == {'FINISHED'} and len(dropped) == 1
              and abs(bounds(dropped)[0].z) < 1e-3, result)
        bpy.ops.mesh.primitive_cube_add(location=(-3, 0, 0))
        cube = bpy.context.object
        result = bpy.ops.angelo.media_drop(target="VIEW3D", library=entry.name, object=cube.name,
                                           items=f"images/{ids['image']}.blend/Material/Two posters\n")
        check("drop_on_an_object_gives_its_material", result == {'FINISHED'} and cube.active_material is not None
              and cube.active_material.name.startswith("Two posters"), result)
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.scene.cursor.location = (0.0, -2.0, 0.0)
        result = bpy.ops.angelo.media_add_to_scene(item_ids=f"{ids['single']},{ids['image']}")
        meshes = [o for o in bpy.data.objects if o.type == 'MESH']
        empties = [o for o in bpy.data.objects if o.type == 'EMPTY']
        check("add_to_scene_menu", result == {'FINISHED'} and len(meshes) == 1 and len(empties) == 2
              and size_of(meshes) == imported["single"], result)
        bpy.ops.mesh.primitive_cube_add()
        result = bpy.ops.angelo.media_apply_to_selected(item_ids=ids["image"])
        check("apply_to_selected_menu", result == {'FINISHED'} and bpy.context.object.active_material is not None,
              result)

        # Written at once when missing; the library off: Blender's glTF import and a material from the picture.
        blend = studio_assets.item(library, records["single"], studio.folders)["blend"]
        Path(blend).unlink()
        check("missing_item_written_at_once", studio_drag.ensure_written([ids["single"]]) and Path(blend).is_file())
        own = directory / "models" / "my-own-kitbash.blend"
        own.write_bytes(b"a person's own file")
        host.turn_off()
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.scene.cursor.location = (0.0, 0.0, 0.0)
        result = bpy.ops.angelo.media_add_to_scene(item_ids=ids["multi"])
        meshes = [o for o in bpy.data.objects if o.type == 'MESH']
        low, _high = bounds(meshes)
        check("library_off_imports_the_model", result == {'FINISHED'} and len(meshes) == 2
              and size_of(meshes) == imported["multi"] and abs(low.z) < 1e-3, [result, size_of(meshes)])
        bpy.ops.mesh.primitive_cube_add()
        result = bpy.ops.angelo.media_apply_to_selected(item_ids=ids["image"])
        material = bpy.context.object.active_material
        texture = next((n.image for n in material.node_tree.nodes if n.type == 'TEX_IMAGE'), None) if material else None
        check("library_off_material_from_the_picture", result == {'FINISHED'} and texture is not None
              and texture.packed_file is not None, result)
        from angelo_agent import studio_assets_space
        studio_assets_space.free(directory, active=False)
        check("free_keeps_a_persons_own_file", own.read_bytes() == b"a person's own file"
              and not list((directory / "models").glob("*-*-*-*-*.blend")), [p.name for p in directory.rglob("*")])
        host.turn_on()
    for cls in reversed(media_drop.classes):
        bpy.utils.unregister_class(cls)
    report = {"success": all(checks.values()), "checks": checks, "details": details}
    (OUT / "media-drop.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_MEDIA_DROP", json.dumps(report, default=str), flush=True)


main()
