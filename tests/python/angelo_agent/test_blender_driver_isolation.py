# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Drivers a staged program must not let reach Python or the person's data (1.0.10), measured through Blender.

* **Destination (H4).** A driver writing a Python-defined property (``bpy.props`` with ``get``/``set``) makes its
  object unbindable, and a staged copy holding one refuses before evaluation; the add-on's getter and setter never
  run in a stage or for the add-on's own reads. The same driver into a stored custom property commits.
* **Reads outside the stage (H5).** A driver whose target or path reaches a live ID that is not staged (the scene, an
  object through the scene's custom property, a World) refuses at staging, before anything is evaluated, and the
  person's unkeyed values on that data stay. The same transform of an object whose driver reads only itself commits.

Run in a disposable instance with the exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-driver-isolation-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string, "module": native.__file__}
window = bpy.context.window_manager.windows[0]
NAME = "angelo_scene_program"
OUTSIDE = "would change the person's file"   # The refusal names what the driver reaches (driver_rules).
PY_DESTINATION = "a driver writing a Python-defined property"


def check(name, condition):
    report["checks"][name] = bool(condition)   # Every check runs; the suite fails at the end on any miss.


# ---- Observation hooks (observation only) -----------------------------------------------------------------------
CALLS, STORE, IN_STAGE, SAMPLES, WATCH = [], {}, [False], {}, []


def _caller():
    """The innermost add-on frame on the stack (who made Blender call the property), or None for Blender itself."""
    import traceback as tb
    frames = [frame for frame in tb.extract_stack()[:-2]
              if "addons_core" in frame.filename and "angelo_agent" in frame.filename]
    return "%s:%d" % (Path(frames[-1].filename).name, frames[-1].lineno) if frames else None


def _getter(self):
    CALLS.append(("get", str(self.name), IN_STAGE[0] or str(self.name).startswith("__angelo"), _caller()))
    return STORE.get(str(self.name), 0.0)


def _setter(self, value):
    CALLS.append(("set", str(self.name), IN_STAGE[0] or str(self.name).startswith("__angelo"), _caller()))
    STORE[str(self.name)] = value


def stage_or_addon_calls():
    """Calls made in a stage, on a stage copy, or by the add-on's own reads (not the person's depsgraph)."""
    return [call for call in CALLS if call[2] or call[3]]


_update = native.BlenderProgramBackend._update_stage


def _recording(self, stage):
    IN_STAGE[0] = True
    try:
        return _update(self, stage)
    finally:
        IN_STAGE[0] = False
        for name, read in WATCH:
            SAMPLES.setdefault(name, []).append(read(stage))


def reset(scene_name):
    window.scene = bpy.data.scenes.new(scene_name)
    CALLS.clear()
    STORE.clear()
    SAMPLES.clear()
    WATCH.clear()
    cube = bpy.data.objects.new(scene_name + " Cube", bpy.data.meshes.new(scene_name + " Cube"))
    window.scene.collection.objects.link(cube)
    cube["ctl"] = 0.5
    cube["dest"] = 0.0
    return cube


def empty(name):
    obj = bpy.data.objects.new(name, None)
    window.scene.collection.objects.link(obj)
    return obj


def keyed_live(holder):
    """Key holder["h5k"] 0@1, 10@10 (and an object's location), then the person's unkeyed edit at frame 1: 5."""
    for frame, value in ((1, 0.0), (10, 10.0)):
        holder["h5k"] = value
        holder.keyframe_insert('["h5k"]', frame=frame)
        if isinstance(holder, bpy.types.Object):
            holder.location = (value, 0, 0)
            holder.keyframe_insert("location", frame=frame)
    window.scene.frame_set(1)
    window.view_layer.update()
    holder["h5k"] = 5.0
    if isinstance(holder, bpy.types.Object):
        holder.location = (5, 5, 5)


def add_driver(owner, path, target_type, target, target_path, kind="SUM", expression=""):
    curve = owner.driver_add(path)
    driver = curve.driver
    driver.type = kind
    if expression:
        driver.expression = expression
    variable = driver.variables.new()
    variable.name = "v"
    variable.type = "SINGLE_PROP"
    variable.targets[0].id_type = target_type
    variable.targets[0].id = target
    variable.targets[0].data_path = target_path
    return curve


def revision_error(obj):
    window.view_layer.update()
    queries.invalidate()
    try:
        queries.object_revision(window, obj)
    except ValueError as error:
        return str(error)
    return None


def revision(obj):
    window.view_layer.update()
    queries.invalidate()
    return queries.object_revision(window, obj)


def state(obj):
    """The revision, or why there is none (a constraint setting can make the evaluated matrix non-finite)."""
    try:
        return revision(obj)
    except Exception as error:  # noqa: BLE001 - any refusal is a state that differs from a revision.
        return "error: " + str(error)[:200]


def run(name, cube, reads=()):
    """Stage (and commit when ready) a transform of ``cube``: (status, error text)."""
    window.view_layer.update()
    queries.invalidate()
    try:
        revisions = {obj.name: queries.object_revision(window, obj) for obj in (cube, *reads)}
    except ValueError as error:
        report["cases"][name] = ["not exact", str(error)[:300]]
        return "not exact", str(error)
    handle = queries.object_handle(cube)
    status, error = None, ""
    try:
        arguments = validate_call(NAME, {
            "mode": "APPLY", "read_set": [{"handle": queries.object_handle(o), "revision": revisions[o.name]}
                                          for o in reads],
            "write_set": [{"handle": handle, "revision": revisions[cube.name]}],
            "operations": [{"kind": "transform", "target": handle, "parameters": {"location": [1, 2, 3]}}]})
        job = native.begin(window, NAME, arguments)
        staged = {}
        for _ in range(4096):
            staged = job.poll()
            if staged.get("final") or staged.get("ready_to_commit"):
                break
        status, error = staged.get("status"), str(staged.get("error") or staged.get("message") or "")
        if staged.get("ready_to_commit"):
            bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
            status = (job.result or staged).get("status")
            error = str(report.get("last_commit", {}).get("error") or "")
    except ValueError as error_:
        status, error = "refused before approval", str(error_)
    finally:
        native.cleanup_all()
    report["cases"][name] = [status, error[:300]]
    return status, error


# ---- H4: the destination ----------------------------------------------------------------------------------------
def destination():
    for owner_kind in ("object", "mesh"):
        for kind, expression in (("SUM", ""), ("SCRIPTED", "v * 2")):
            cube = reset("H4 %s %s" % (owner_kind, kind))
            owner = cube if owner_kind == "object" else cube.data
            add_driver(owner, "h4p", "OBJECT", cube, '["ctl"]', kind, expression)
            CALLS.clear()
            error = revision_error(cube)
            check("python_destination_unbindable_%s_%s" % (owner_kind, kind),
                  error is not None and "animation_driver_expressions" in error)
            status, _ = run("h4 %s %s" % (owner_kind, kind), cube)
            check("python_destination_never_commits_%s_%s" % (owner_kind, kind), status != "committed")
            report["cases"]["h4 calls %s %s" % (owner_kind, kind)] = CALLS[:6]
            check("python_destination_no_getter_or_setter_ran_%s_%s" % (owner_kind, kind), not stage_or_addon_calls())
    # The staging scan refuses on its own, whatever the revision said (a staged copy holding one).
    from angelo_agent.action_budget import assert_evaluation_budget
    from angelo_agent.refusal_codes import RefusalError
    cube = reset("H4 scan")
    add_driver(cube, "h4p", "OBJECT", cube, '["ctl"]')
    staged = empty("H4 scan staged")   # Any staged object: the driver scan reads every owned ID.

    class Context:
        owned_ids = [cube]

        @staticmethod
        def assert_staged(obj):
            return None
    try:
        assert_evaluation_budget(Context, staged)
        refused = ""
    except RefusalError as error:
        refused = str(error)
    check("staging_scan_refuses_python_destination", PY_DESTINATION in refused)
    check("staging_scan_ran_no_getter", not stage_or_addon_calls())
    # Control: the same driver into a stored custom property commits.
    cube = reset("H4 control")
    add_driver(cube, '["dest"]', "OBJECT", cube, '["ctl"]')
    status, _ = run("h4 control", cube)
    check("stored_destination_commits", status == "committed")
    # Another datablock: an object's driver into its curve data.
    curve_data = bpy.data.curves.new("H4 Curve", "CURVE")
    curve_obj = bpy.data.objects.new("H4 Curve", curve_data)
    window.scene.collection.objects.link(curve_obj)
    curve_obj["ctl"] = .1
    spanning = add_driver(curve_obj, '["ctl"]', "OBJECT", curve_obj, '["ctl"]')
    spanning.data_path = "data.bevel_depth"   # driver_add refuses a path spanning IDs; a file can still hold one.
    error = revision_error(curve_obj)
    check("writing_another_datablock_unbindable", error is not None and "animation_driver_expressions" in error)
    Context.owned_ids = [curve_obj]
    try:
        assert_evaluation_budget(Context, staged)
        refused = ""
    except RefusalError as error_:
        refused = str(error_)
    check("staging_scan_names_the_datablock_written", "writes the person's curve 'H4 Curve'" in refused)
    # A destination that leaves its owner and ends in an array component (``parent.location.x``): RNA resolves it.
    parent = empty("H4 Parent")
    child = empty("H4 Child")
    child.parent = parent
    child["ctl"] = .5
    component = add_driver(child, '["ctl"]', "OBJECT", child, '["ctl"]')
    component.data_path = "parent.location"
    component.array_index = 0
    component.data_path = "parent.location.x"
    error = revision_error(child)
    report["cases"]["component destination"] = error
    check("component_destination_in_another_datablock_unbindable", error is not None and "driver" in error)
    Context.owned_ids = [child]
    try:
        assert_evaluation_budget(Context, staged)
        refused = ""
    except RefusalError as error_:
        refused = str(error_)
    check("staging_scan_refuses_a_component_destination_elsewhere", "writes the person's object 'H4 Parent'" in refused)
    # The owner's embedded node tree is the owner's.
    material = bpy.data.materials.new("H4 Embedded")
    material.use_nodes = True
    cube = reset("H4 embedded")
    cube.data.materials.append(material)
    material["ctl"] = .25
    node = material.node_tree.nodes["Principled BSDF"]
    # driver_add puts such a driver on the tree itself; a file can hold it on the material, writing through its tree.
    material["ctl2"] = 0.0
    embedded = add_driver(material, '["ctl2"]', "MATERIAL", material, '["ctl"]')
    embedded.data_path = 'node_tree.nodes["%s"].inputs[2].default_value' % node.name
    error = revision_error(cube)
    report["cases"]["embedded destination"] = error
    check("embedded_tree_destination_is_native", error is None or "driver" not in error)
    material.animation_data_clear()
    add_driver(material.node_tree, 'nodes["%s"].inputs[2].default_value' % node.name, "MATERIAL", material, '["ctl"]')
    error = revision_error(cube)
    check("tree_destination_is_native", error is None or "driver" not in error)


# ---- H5: reads outside the stage --------------------------------------------------------------------------------
def refused_outside(status, error, named):
    """Refused before evaluation, naming what the driver reaches; with the stage-links routes their own check refuses
    first, in its own words (stage_routes, stage_paths), and the live values are asserted apart."""
    if status == "committed":
        return False
    return (OUTSIDE in error and named in error) or (routes_available() and "reviewed Python" in error)


def watch(holder):
    WATCH.append((holder.name, lambda stage: round(float(holder["h5k"]), 4)))


def untouched(holder):
    samples = SAMPLES.get(holder.name, [])
    return all(sample == 5.0 for sample in samples) and round(float(holder["h5k"]), 4) == 5.0


def outside_reads():
    for declared in (False, True):
        cube = reset("H5 scene pointer %s" % declared)
        e = empty("H5 E %s" % declared)
        keyed_live(e)
        window.scene["ctrl"] = e
        add_driver(cube, '["dest"]', "SCENE", window.scene, '["ctrl"]["h5k"]')
        watch(e)
        status, error = run("h5 scene pointer declared=%s" % declared, cube, (e,) if declared else ())
        check("scene_path_refused_at_staging_declared_%s" % declared,
              refused_outside(status, error, "reads the person's scene %r" % window.scene.name))
        check("scene_path_live_values_stay_declared_%s" % declared,
              untouched(e) and tuple(round(v, 4) for v in e.location) == (5, 5, 5))
    cube = reset("H5 world")
    world = bpy.data.worlds.new("H5 World")
    keyed_live(world)
    add_driver(cube, '["dest"]', "WORLD", world, '["h5k"]')
    watch(world)
    status, error = run("h5 world", cube)
    # With the stage-links routes a keyed World is read through a private copy (stage_routes), so the edit may commit;
    # either way the person's World keeps its value (asserted next).
    check("world_target_refused_or_read_through_a_copy",
          routes_available() or refused_outside(status, error, "reads the person's world 'H5 World'"))
    check("world_target_live_value_stays", untouched(world))
    # Controls: a driver reading only its own object; the scene pointer and the active object alone.
    cube = reset("H5 control")
    e = empty("H5 control E")
    keyed_live(e)
    window.scene["ctrl"] = e
    window.view_layer.objects.active = e
    add_driver(cube, '["dest"]', "OBJECT", cube, '["ctl"]')
    watch(e)
    status, _ = run("h5 control", cube)
    check("own_reads_commit", status == "committed")
    check("own_reads_live_values_stay", untouched(e))


# ---- A Python struct subclass's own subscript never runs (break-it L2) ------------------------------------------
SUBSCRIPTS = []


class QATree(bpy.types.NodeTree):
    bl_idname, bl_label, bl_icon = "AngeloQADriverTree", "QA driver tree", "NODETREE"


class QANode(bpy.types.Node):
    bl_idname, bl_label = "AngeloQADriverNode", "QA driver node"

    @classmethod
    def poll(cls, tree):
        return tree.bl_idname == QATree.bl_idname

    def __getitem__(self, key):
        SUBSCRIPTS.append(key)
        return bpy.types.bpy_struct.__getitem__(self, key)


def python_subscript():
    cube = reset("Subscript")
    tree = bpy.data.node_groups.new("QA driver tree", QATree.bl_idname)
    node = tree.nodes.new(QANode.bl_idname)
    node["k"] = 2.0
    add_driver(cube, '["dest"]', "NODETREE", tree, 'nodes["%s"]["k"]' % node.name)
    SUBSCRIPTS.clear()
    revision_error(cube)
    check("python_subscript_never_runs_on_the_revision_path", not SUBSCRIPTS)
    report["cases"]["python subscript"] = list(SUBSCRIPTS)

# ---- Shape-key rigs: drivers whose targets have staged copies (the stage-links routes) ---------------------------
def routes_available():
    try:
        from angelo_agent import stage_links
    except ImportError:
        return False
    return bool(stage_links.available())


def keyed_mesh(name):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, data)
    window.scene.collection.objects.link(obj)
    obj.shape_key_add(name="Basis")
    obj.shape_key_add(name="Corr")
    return obj


def shape_key_rigs():
    """A corrective key driven by a control object's channel, a key driven by its own mesh, a key driven by another
    mesh's key. With the stage-links routes (every link of a staged ID names its staged or reference copy) each binds
    and commits, its driver still reading the person's data afterwards; without them each refuses at staging."""
    routed = routes_available()
    report["cases"]["stage links routes"] = routed
    cases = []
    window.scene = bpy.data.scenes.new("Shape keys")
    rig = bpy.data.objects.new("Key rig", None)
    window.scene.collection.objects.link(rig)
    rig.location.x = 0.5
    mesh = keyed_mesh("Corrective")
    curve = mesh.data.shape_keys.driver_add('key_blocks["Corr"].value')
    variable = curve.driver.variables.new()
    curve.driver.type, variable.type = "SUM", "TRANSFORMS"
    variable.targets[0].id, variable.targets[0].transform_type = rig, "LOC_X"
    cases.append(("control_object_channel", mesh, (rig,), lambda: rig, 0.5))
    own = keyed_mesh("Own data")
    own.data["ctl"] = 0.25
    add_driver(own.data.shape_keys, 'key_blocks["Corr"].value', "MESH", own.data, '["ctl"]')
    cases.append(("own_mesh_data", own, (), lambda: own.data, 0.25))
    other = keyed_mesh("Other face")
    other.data.shape_keys.key_blocks["Corr"].value = 0.75
    cross = keyed_mesh("Cross")
    add_driver(cross.data.shape_keys, 'key_blocks["Corr"].value', "KEY", other.data.shape_keys,
               'key_blocks["Corr"].value')
    cases.append(("cross_mesh_key", cross, (), lambda: other.data.shape_keys, 0.75))
    for name, obj, reads, target, value in cases:
        status, error = run("shape keys " + name, obj, reads)
        window.view_layer.update()
        driver = obj.data.shape_keys.animation_data.drivers[0].driver
        if routed:
            check("shape_key_%s_commits" % name, status == "committed")
            check("shape_key_%s_moved" % name, tuple(round(v, 4) for v in obj.location) == (1, 2, 3))
            check("shape_key_%s_reads_the_persons_data" % name, driver.variables[0].targets[0].id == target())
            check("shape_key_%s_value" % name,
                  abs(obj.data.shape_keys.key_blocks["Corr"].value - value) < 1e-6)
            check("shape_key_%s_no_stage_copy_left" % name,
                  not any(item.name.startswith("__angelo") for item in (*bpy.data.meshes, *bpy.data.shape_keys)))
        else:
            check("shape_key_%s_refused_without_routes" % name, status != "committed" and OUTSIDE in error
                  or status == "committed" and driver.variables[0].targets[0].id == target())
    check("shape_key_other_mesh_untouched", abs(other.data.shape_keys.key_blocks["Corr"].value - 0.75) < 1e-6)



# ---- A path that continues past a stored custom property (break-it L1/L2: H4 class) ----------------------------
def stored_through():
    """A stored custom-property group holding an ID, read on as ``["grp"]["ptr"].h4p``: no walk proves what lies past
    the stored value, so the driver is not native (refused at the revision, before staging) and the add-on's getter
    never runs in a stage or for the add-on's own reads. Defence in depth: admission already refuses an ID-valued
    custom property on a declared object, and a world edit refuses a driven World; the staging scan's own refusal on
    a staged copy is pinned directly. Blender's evaluation of the person's own scene still runs the getter, as the
    UI does (calls with no add-on frame)."""
    cube = reset("Stored through")
    holder, far = empty("Stored holder"), empty("Stored far")
    holder["grp"] = {"ptr": far}
    add_driver(cube, '["dest"]', "OBJECT", holder, '["grp"]["ptr"].h4p')
    CALLS.clear()
    status, error = run("stored through object", cube, (holder,))
    check("stored_through_refused_before_any_evaluation", status not in ("ready", "committed"))
    check("stored_through_not_native_at_the_revision", status == "not exact" and "animation_driver_expressions" in error)
    check("stored_through_no_getter_in_stage_or_by_the_addon", not stage_or_addon_calls())
    report["cases"]["stored through calls"] = CALLS[:6]
    # Only stored keys after a stored value: stored data all the way, native as before.
    keys = reset("Stored keys")
    keys["grp"] = {"inner": {"value": 0.5}}
    add_driver(keys, '["dest"]', "OBJECT", keys, '["grp"]["inner"]["value"]')
    check("stored_keys_only_bind", revision_error(keys) is None)
    # The stage's private World copy (a world edit): its driver reads past a stored value.
    from angelo_agent import scene_settings
    reset("Stored world")
    world = bpy.data.worlds.new("Stored World")
    window.scene.world = world
    world.use_nodes = True
    world["dest"] = 0.0
    world["grp"] = {"ptr": empty("Stored world far")}
    add_driver(world, '["dest"]', "WORLD", world, '["grp"]["ptr"].h4p')
    window.view_layer.update()
    queries.invalidate()
    CALLS.clear()
    status, error = None, ""
    try:
        settings = scene_settings.execute(window, {})
        arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [], "write_set": [
            {"handle": settings["handle"], "revision": settings["revision"]}], "operations": [
            {"kind": "world_configure", "target": settings["handle"], "parameters": {"strength": 2}}]})
        job = native.begin(window, NAME, arguments)
        staged = {}
        for _ in range(4096):
            staged = job.poll()
            if staged.get("final") or staged.get("ready_to_commit"):
                break
        status, error = ("ready" if staged.get("ready_to_commit") else staged.get("status")), str(staged.get("error") or "")
    except ValueError as error_:
        status, error = "refused before approval", str(error_)
    finally:
        native.cleanup_all()
    report["cases"]["stored through world"] = [status, error[:300]]
    check("stored_through_world_refused", status not in ("ready", "committed"))
    report["cases"]["stored through world calls"] = CALLS[:6]
    check("stored_through_world_no_getter", not stage_or_addon_calls())
    # The staging scan on a staged copy that holds such a driver (whatever admitted it): refused, no getter.
    from angelo_agent.action_budget import assert_evaluation_budget
    from angelo_agent.refusal_codes import RefusalError

    class Staged:
        owned_ids = [world]
        objects, extras = {}, {}

        @staticmethod
        def assert_staged(obj):
            return None
    CALLS.clear()
    try:
        assert_evaluation_budget(Staged, empty("Stored scan staged"))
        refused = ""
    except RefusalError as error_:
        refused = str(error_)
    check("stored_through_staging_scan_refuses_a_staged_copy", "past a stored custom property" in refused)
    check("stored_through_staging_scan_no_getter", not stage_or_addon_calls())


CASES = (destination, outside_reads, shape_key_rigs, python_subscript, stored_through)

try:
    native.register()
    bpy.types.Object.h4p = bpy.props.FloatProperty(get=_getter, set=_setter)
    bpy.types.Mesh.h4p = bpy.props.FloatProperty(get=_getter, set=_setter)
    native.BlenderProgramBackend._update_stage = _recording
    bpy.utils.register_class(commit_operator(report))
    bpy.utils.register_class(QATree)
    bpy.utils.register_class(QANode)
    for case in CASES:
        try:
            case()
        except Exception:
            report["checks"][case.__name__ + "_ran"] = False
            report.setdefault("errors", {})[case.__name__] = traceback.format_exc()[-2000:]
    check("no_stage_left", no_stages())
finally:
    native.BlenderProgramBackend._update_stage = _update
    native.cleanup_all()
    failed = sorted(name for name, passed in report["checks"].items() if not passed)
    report["success"] = not failed and not report.get("errors")
    (OUT / "driver-isolation.json").write_text(json.dumps(report, indent=1, default=str))
print("ANGELO_DRIVER_ISOLATION", json.dumps({"failed": failed, "checks": len(report["checks"]),
                                              "errors": sorted(report.get("errors", {}))}), flush=True)
assert not failed and not report.get("errors"), failed
