# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Names Blender gave a script's data-blocks in place of the names asked for (``script_renames``), in the compiled app.

Run by ``run_native_suites.py --include test_blender_script_renames`` (BACKGROUND). Every case runs a reviewed
script through the real ``BlenderExecutor`` from Blender's factory scene and reads ``renamed_by_blender``:

* Blender 5.2's own naming: a new camera beside the factory one, a material made twice, an object renamed into
  a taken name, a forced rename that pushes the holder aside, a numbered name made again after its holder was
  deleted, primitives added again without a rename, numbers past 999 (``.1000``), a 63-byte name (numbered) and
  a 255-byte one (cut), and a name longer than 255 bytes made by ``.new`` (cut where it is set: not listed);
* what is never listed: the script's own numbered names (made, or assigned and got), a name freed first, linked
  data-blocks from a library, data-blocks an append, ``wm.append`` or an import brought in under the file's own
  numbered names, a long name the script chose beside a longer one, a script that raised (rolled back) and a
  script that renamed nothing (no field at all);
* names the script assigned: a rename into a taken name that left "Gate.001" as it was, a numbered name taken
  while its base was free, a name cut to 255 bytes; a library override, judged by its reference's name;
* the cap, the model's copy, and the kinds left unread past the read limit (``not_checked``);
* costs (recorded, not asserted): the readings and comparison on the factory scene and on 2,000 and 20,000
  objects (and with a library linked), and the paths of an empty script and of one that sets an attribute on every
  object, with and without the names (doc/angelo/performance.rst).

No provider, account or network.
"""

import gc
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import model_results, script_renames  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
import bpy.ops as operators  # noqa: E402

REAL_CREATE, REAL_LOAD = operators._op_create_function, bpy.types.BlendDataLibraries.__dict__["load"]

FIELD = script_renames.FIELD
bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}


def check(name, condition, **observed):
    report["checks"][name] = bool(condition)
    if observed:
        report["observed"][name] = observed


def factory():
    """Blender's factory scene: Cube, Camera, Light, Material, World, Collection."""
    bpy.ops.wm.read_homefile(use_factory_startup=True, use_empty=False)
    return bpy.context.window


def run(code, window=None):
    """Run ``code`` as a reviewed script through the executor; its result."""
    window = window or bpy.context.window
    return BlenderExecutor(window).execute("angelo_execute_python", {"description": "Renames", "code": code})


def field_of(code, setup=None):
    window = factory()
    if setup is not None:
        setup()
    result = run(code, window)
    return result, result.get(FIELD)


def naming():
    """Blender 5.2's own naming, read back through the executor."""
    result, field = field_of(
        "import bpy\n"
        "cam = bpy.data.objects.new('Camera', bpy.data.cameras.new('Camera'))\n"
        "bpy.context.scene.collection.objects.link(cam)\n"
        "result = cam.name\n")
    check("a_new_camera_beside_the_factory_one_is_listed_object_kinds_first",
          result.get("result") == "Camera.001" and field == {"objects": ["Camera.001"], "cameras": ["Camera.001"]}
          and list(field) == ["objects", "cameras"], field=field, error=result.get("error"))
    copy = model_results.model_result("angelo_execute_python", result, observation_follows=True)
    check("the_models_copy_keeps_the_field", copy.get(FIELD) == field, copy=copy.get(FIELD))
    report["observed"]["usual_field_bytes"] = len(json.dumps({FIELD: field}, separators=(",", ":"))) - 2

    result, field = field_of("import bpy\nbpy.data.materials.new('Slate')\n"
                             "result = bpy.data.materials.new('Slate').name\n")
    check("a_material_made_twice", result.get("result") == "Slate.001" and field == {"materials": ["Slate.001"]},
          field=field, error=result.get("error"))

    result, field = field_of("import bpy\nlamp = bpy.data.objects['Light']\nlamp.name = 'Cube'\nresult = lamp.name\n")
    check("an_object_renamed_into_a_taken_name", result.get("result") == "Cube.001"
          and field == {"objects": ["Cube.001"]}, field=field, error=result.get("error"))

    result, field = field_of("import bpy\nresult = str(bpy.data.objects['Light'].rename('Cube', mode='ALWAYS'))\n")
    check("a_forced_rename_lists_the_holder_it_pushed_aside",
          bpy.data.objects.get("Cube") is not None and bpy.data.objects["Cube"].type == "LIGHT"
          and field == {"objects": ["Cube.001"]}, field=field, result=result.get("result"), error=result.get("error"))

    def lamps():
        for name in ("Lamp", "Lamp.001"):
            bpy.context.scene.collection.objects.link(bpy.data.objects.new(name, None))
    result, field = field_of(
        "import bpy\nbpy.data.objects.remove(bpy.data.objects['Lamp.001'])\n"
        "lamp = bpy.data.objects.new('Lamp', None)\nbpy.context.scene.collection.objects.link(lamp)\n"
        "result = lamp.name\n", lamps)
    check("a_numbered_name_made_again_after_its_holder_was_deleted", result.get("result") == "Lamp.001"
          and field == {"objects": ["Lamp.001"]}, field=field, error=result.get("error"))

    result, field = field_of("import bpy\nfor _ in range(5):\n    bpy.ops.mesh.primitive_cube_add()\n"
                             "result = sorted(o.name for o in bpy.context.scene.objects if o.type == 'MESH')\n")
    check("primitives_added_again_are_one_run_per_kind",
          field == {"objects": ["Cube.001 to Cube.005 (5)"], "meshes": ["Cube.001 to Cube.005 (5)"]},
          field=field, names=result.get("result"), error=result.get("error"))

    result, field = field_of(
        "import bpy\nnames = []\nfor _ in range(1001):\n    obj = bpy.data.objects.new('Item', None)\n"
        "    bpy.context.scene.collection.objects.link(obj)\n    names.append(obj.name)\nresult = names[-3:]\n")
    check("blender_numbers_past_999_with_four_digits", result.get("result") == ["Item.998", "Item.999", "Item.1000"]
          and field == {"objects": ["Item.001 to Item.1000 (1000)"]}, field=field, result=result.get("result"))

    sixty_three, longest = "N" * 63, "L" * 255

    def long_names():
        for name in (sixty_three, longest):
            bpy.context.scene.collection.objects.link(bpy.data.objects.new(name, None))
    result, field = field_of(
        "import bpy\nnames = []\nfor name in (%r, %r, %r, %r):\n"
        "    obj = bpy.data.objects.new(name, None)\n    bpy.context.scene.collection.objects.link(obj)\n"
        "    names.append(len(obj.name.encode()))\nresult = names\n" % (sixty_three, longest, longest, "X" * 300),
        long_names)
    check("a_63_byte_name_is_numbered_a_255_byte_one_cut_and_both_listed",
          result.get("result") == [67, 254, 253, 255]
          and sorted((field or {}).get("objects", ())) == sorted([sixty_three + ".001", longest[:-1], longest[:-2]]),
          lengths=result.get("result"), field=[len(name) for name in (field or {}).get("objects", ())])

    def long_holder():
        bpy.context.scene.collection.objects.link(bpy.data.objects.new("B" * 252, None))
    result, field = field_of("import bpy\nobj = bpy.data.objects.new(%r, None)\n"
                             "bpy.context.scene.collection.objects.link(obj)\nresult = len(obj.name)\n" % ("B" * 200),
                             long_holder)
    check("a_long_name_the_script_chose_beside_a_longer_one_is_not_a_cut",
          result.get("result") == 200 and field is None, field=field)


def asked():
    """Names the script assigned: the name asked for decides, also where Blender changed nothing."""
    def gates():
        for name in ("Gate", "Gate.001", "Post.001"):
            bpy.context.scene.collection.objects.link(bpy.data.objects.new(name, None))
    result, field = field_of(
        "import bpy\nbpy.data.objects['Gate.001'].name = 'Gate'\n"
        "thing = bpy.data.objects.new('Thing', None)\nbpy.context.scene.collection.objects.link(thing)\n"
        "setattr(thing, 'name', 'Post.001')\n"
        "long = bpy.data.objects.new('Long', None)\nbpy.context.scene.collection.objects.link(long)\n"
        "long.name = 'Z' * 300\nresult = [thing.name, len(long.name)]\n", gates)
    check("an_assigned_name_blender_kept_numbered_or_cut_is_listed",
          result.get("result") == ["Post.002", 255]
          and sorted((field or {}).get("objects", ())) == sorted(["Gate.001", "Post.002", "Z" * 255]),
          field=field, result=result.get("result"), error=result.get("error"))

    def post():
        bpy.context.scene.collection.objects.link(bpy.data.objects.new("Post", None))
    result, field = field_of(
        "import bpy\nthing = bpy.data.objects.new('Thing', None)\nbpy.context.scene.collection.objects.link(thing)\n"
        "thing.name = 'Post.001'\nresult = thing.name\n", post)
    check("a_numbered_name_the_script_assigned_and_got_is_its_own", result.get("result") == "Post.001"
          and field is None, field=field)


def never_listed():
    """What the field never states."""
    result, field = field_of(
        "import bpy\nfor name in ('Post.001', 'Post.002'):\n"
        "    bpy.context.scene.collection.objects.link(bpy.data.objects.new(name, None))\nresult = 2\n")
    check("the_scripts_own_numbered_names", result.get("success") is True and field is None, field=field)

    result, field = field_of(
        "import bpy\nbpy.data.objects.remove(bpy.data.objects['Camera'])\n"
        "cam = bpy.data.objects.new('Camera', bpy.data.cameras.new('Lens'))\n"
        "bpy.context.scene.collection.objects.link(cam)\n"
        "bpy.data.materials['Material'].name = 'Material Old'\n"
        "result = [cam.name, bpy.data.materials.new('Material').name]\n")
    check("a_name_freed_first", result.get("result") == ["Camera", "Material"] and field is None, field=field,
          result=result.get("result"))

    result, field = field_of("import bpy\nbpy.data.materials.new('Slate')\nbpy.data.materials.new('Slate')\n"
                             "raise RuntimeError('stop here')\n")
    check("a_script_that_raised_gets_nothing", result.get("error") and FIELD not in result,
          error=result.get("error"), restored="Slate" not in bpy.data.materials)

    result, field = field_of("import bpy\nbpy.context.scene.collection.objects.link(bpy.data.objects.new("
                             "'Brand New Thing', None))\nresult = 1\n")
    check("a_script_that_renamed_nothing_has_no_field", result.get("success") is True and FIELD not in result,
          keys=sorted(result))

    library = OUT / "renames-library.blend"
    factory()
    blocks = {bpy.data.materials.new("Brick.001"), bpy.data.materials.new("Clay")}
    bpy.data.libraries.write(str(library), blocks, fake_user=True)
    window = factory()
    bpy.data.materials.new("Brick")
    code = ("import bpy\nwith bpy.data.libraries.load(%r, link=True) as (source, target):\n"
            "    target.materials = ['Brick.001', 'Clay']\n"
            "result = [m.name for m in bpy.data.materials if m.library] + [bpy.data.materials.new('Clay').name]\n"
            % str(library))
    linked = run(code, window)
    again = run("import bpy\nresult = bpy.data.materials.new('Clay').name\n", window)
    check("linked_data_blocks_are_never_listed_and_never_hold_a_local_name",
          sorted(linked.get("result") or ()) == ["Brick.001", "Clay", "Clay"] and FIELD not in linked
          and again.get("result") == "Clay.001" and again.get(FIELD) == {"materials": ["Clay.001"]},
          linked=linked.get("result"), field=linked.get(FIELD), again=again.get(FIELD), error=linked.get("error"))


def from_files():
    """Data-blocks brought in under the file's own numbered names: never judged by their form."""
    library, planks = OUT / "rocks.blend", OUT / "planks.obj"
    factory()
    rocks = set()
    for name in ("Rock", "Rock.001", "Rock.002"):
        rocks.add(bpy.data.objects.new(name, bpy.data.meshes.new(name)))
    bpy.data.libraries.write(str(library), rocks, fake_user=True)
    planks.write_text("o Plank\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\no Plank.001\nv 0 0 1\nv 1 0 1\nv 0 1 1\n"
                      "f 4 5 6\n")
    result, field = field_of(
        "import bpy\nwith bpy.data.libraries.load(%r, link=False) as (source, target):\n"
        "    target.objects = ['Rock', 'Rock.001', 'Rock.002']\n"
        "for obj in target.objects:\n    bpy.context.scene.collection.objects.link(obj)\n"
        "result = sorted(o.name for o in bpy.data.objects if o.name.startswith('Rock'))\n" % str(library))
    check("an_append_under_the_files_own_numbered_names", result.get("result") == ["Rock", "Rock.001", "Rock.002"]
          and field is None, field=field, result=result.get("result"), error=result.get("error"))
    result, field = field_of(
        "import bpy\nbpy.ops.wm.append(directory=%r, files=[{'name': 'Rock'}, {'name': 'Rock.001'}])\n"
        "result = sorted(o.name for o in bpy.data.objects if o.name.startswith('Rock'))\n"
        % (str(library) + "/Object/"))
    check("an_append_operator_under_the_files_own_numbered_names", result.get("result") == ["Rock", "Rock.001"]
          and field is None, field=field, result=result.get("result"), error=result.get("error"))
    result, field = field_of(
        "import bpy\nbpy.ops.wm.obj_import(filepath=%r)\nbpy.ops.mesh.primitive_cube_add()\n"
        "result = sorted(o.name for o in bpy.data.objects if o.name.startswith('Plank'))\n" % str(planks))
    check("an_import_under_the_files_own_numbered_names_and_a_clash_after_it",
          result.get("result") == ["Plank", "Plank.001"]
          and field == {"objects": ["Cube.001"], "meshes": ["Cube.001"]}, field=field, result=result.get("result"),
          error=result.get("error"))

    def local_rock():
        bpy.context.scene.collection.objects.link(bpy.data.objects.new("Rock.001", None))
    result, field = field_of(
        "import bpy\nwith bpy.data.libraries.load(%r, link=True) as (source, target):\n"
        "    target.objects = ['Rock', 'Rock.001']\n"
        "made = [obj.override_hierarchy_create(bpy.context.scene, bpy.context.view_layer) for obj in target.objects]\n"
        "result = [(o.name, o.override_library.reference.name) for o in made]\n" % str(library), local_rock)
    check("an_override_is_judged_by_its_references_name",
          result.get("result") == [["Rock", "Rock"], ["Rock.002", "Rock.001"]] and field == {"objects": ["Rock.002"]},
          field=field, result=result.get("result"), error=result.get("error"))


def bounds():
    """The cap, and the kinds left unread past the read limit."""
    result, field = field_of(
        "import bpy\nfor index in range(20):\n    for _ in range(2):\n"
        "        bpy.data.materials.new('Tile %02d' % index)\nresult = 1\n")
    check("at_most_twelve_entries_and_the_rest_counted", len((field or {}).get("materials", ())) == 12
          and field.get("omitted") == {"materials": 8}, field=field)
    limit = script_renames.MAX_NAMES
    script_renames.MAX_NAMES = 1  # Below the factory objects alone: they are read all the same, object data is not.
    try:
        result, field = field_of("import bpy\nbpy.ops.mesh.primitive_cube_add()\nresult = 1\n")
    finally:
        script_renames.MAX_NAMES = limit
    check("kinds_past_the_read_limit_are_named_and_the_rest_still_checked",
          (field or {}).get("objects") == ["Cube.001"] and "meshes" in field.get("not_checked", ())
          and "objects" not in field.get("not_checked", ()) and "meshes" not in field, field=field)
    check("every_observer_is_put_back", operators._op_create_function is REAL_CREATE
          and bpy.types.BlendDataLibraries.__dict__["load"] is REAL_LOAD and "__setattr__" not in vars(bpy.types.ID))


def sample(function):
    """``(CPU ms, wall ms)`` of one call, with the collector off (a collection inside one sample is not its cost)."""
    gc.collect()
    gc.disable()
    try:
        cpu, wall = time.process_time(), time.perf_counter()
        function()
        return (time.process_time() - cpu) * 1000, (time.perf_counter() - wall) * 1000
    finally:
        gc.enable()


def summary(samples):
    cpu, wall = sorted(row[0] for row in samples), sorted(row[1] for row in samples)
    return {"cpu_min_ms": round(cpu[0], 3), "cpu_median_ms": round(cpu[len(cpu) // 2], 3),
            "wall_min_ms": round(wall[0], 3), "wall_median_ms": round(wall[len(wall) // 2], 3)}


def paired(executor, arguments):
    """``(without, with)``: a script's path without the names and with them, seven runs each, alternating after
    two warm-up runs (the first restore point holds the whole file)."""
    real, runs = script_renames.before, {"with": [], "without": []}
    for turn in range(2 + 2 * 7):
        side = "with" if turn % 2 else "without"
        script_renames.before = real if side == "with" else (lambda: None)  # No names: no reading, no watch.
        try:
            taken = sample(lambda: executor.execute("angelo_execute_python", arguments))
        finally:
            script_renames.before = real
        if turn >= 2:
            runs[side].append(taken)
    return summary(runs["without"]), summary(runs["with"])


def costs():
    """Recorded, not asserted: the factory scene, 2,000 and 20,000 objects with their own cube meshes (and 300 and
    3,000 materials), and the 20,000 with a library linked. CPU time is this process's own; the load average is
    recorded beside it where the platform has one (not Windows)."""
    cube = ([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)],
            [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    report["observed"]["costs"] = {"load_average": os.getloadavg() if hasattr(os, "getloadavg") else None}   # none on Windows
    for label, objects, materials in (("factory", 0, 0), ("2000_objects", 2000, 300), ("20000_objects", 20000, 3000)):
        window = factory()
        data = bpy.data
        shared = [data.materials.new("Heavy Material %04d" % index) for index in range(materials)]
        for index in range(objects):
            mesh = data.meshes.new("Heavy Mesh %05d" % index)
            mesh.from_pydata(cube[0], [], cube[1])
            mesh.materials.append(shared[index % len(shared)])
            window.scene.collection.objects.link(data.objects.new("Heavy Object %05d" % index, mesh))
        window.view_layer.update()
        observed = {"data_blocks": sum(len(getattr(data, name)) for name in script_renames.COLLECTIONS)}
        names = script_renames.before()
        observed["before"] = summary([sample(script_renames.before) for _ in range(9)])
        added = data.objects.new("Heavy Object 00001", data.meshes.new("Heavy Mesh 00001"))
        window.scene.collection.objects.link(added)
        shown = {}
        observed["attach_one_object_added"] = summary(
            [sample(lambda: shown.update(script_renames.attach({}, names))) for _ in range(9)])
        observed["field"] = shown.get(FIELD)
        data.objects.remove(added)
        executor = BlenderExecutor(window)
        for case, code in (("empty_script", "result = 1"),
                           ("script_setting_every_object", "import bpy\nfor obj in bpy.data.objects:\n"
                                                           "    obj.hide_render = False\nresult = 1\n")):
            without, with_ = paired(executor, {"description": "Probe", "code": code})
            observed[case + "_without"], observed[case + "_with"] = without, with_
        if objects == 20000:  # A file that links a library: each data-block's library is read one by one.
            with data.libraries.load(str(OUT / "rocks.blend"), link=True) as (_source, target):
                target.objects = ["Rock"]
            observed["linked_library_data_blocks"] = sum(len(getattr(data, name))
                                                         for name in script_renames.COLLECTIONS)
            names = script_renames.before()
            observed["linked_library_before"] = summary([sample(script_renames.before) for _ in range(9)])
            observed["linked_library_attach"] = summary(
                [sample(lambda: script_renames.attach({}, names)) for _ in range(9)])
        report["observed"]["costs"][label] = observed
    heavy = report["observed"]["costs"]["20000_objects"]["field"]
    check("the_heavy_scene_is_read_whole_and_its_clash_listed",
          heavy == {"objects": ["Heavy Object 00001.001"], "meshes": ["Heavy Mesh 00001.001"]}, field=heavy)


try:
    naming()
    asked()
    never_listed()
    from_files()
    bounds()
    costs()
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "script-renames.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("SCRIPT_RENAMES failed checks:", json.dumps(failed))
assert report["success"], failed or report.get("error")
