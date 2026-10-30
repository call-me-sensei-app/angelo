# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Settings a call leaves out stay as they are (action_schemas.SETTINGS_NOTE), measured through Blender.

Each typed setting below is stated alone in its own committed program, and every RNA value is read before and
after, compared exactly (``repr`` of the stored floats):

* ``modifier_configure`` on every typed modifier type, its settings first set away from every default: only
  the stated setting's RNA property (``MODIFIER_RNA_NAMES``) and what it implies (``also_writes``) change;
  ``program_frames`` names those paths too. A canonical default once overwrote the rest (a Bevel's
  width 0.2 became 0.05 when a call set its segments).
* ``modifier_add`` and ``constraint_add`` on every typed type: equal to what ``modifiers.new`` and
  ``constraints.new`` make, but for the stated setting.
* ``constraint_configure`` on generic types: every other setting kept.
* ``material_principled`` on a held material (a red metal with a bump graph): one input changes; every other
  input, node, link and viewport display value stay. The one display value Solid view draws for that input follows
  it (``material_display_sync``: Base Color and Alpha the viewport colour, Metallic and Roughness their own). A
  linked input or two Principled BSDFs are refused before approval, the material unchanged; a new material keeps Blender's defaults (``material_rules.NEW_INPUTS``).
* ``keyframe_insert`` without interpolation keeps an existing key's own; a new key takes the preference.

The report separates the types the fix was developed against (``DEVELOPED``) from the held-out rest. Run in a
disposable instance with the exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-kept-settings-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import material_rules, native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.action_node_types import _principled_inputs  # noqa: E402
from angelo_agent.action_object import CONSTRAINT_AXES  # noqa: E402
from angelo_agent.action_schemas import (CONSTRAINT_SETTINGS, MODIFIER_OPERANDS, MODIFIER_RNA_NAMES,  # noqa: E402
                                         MODIFIER_SETTINGS, TARGETLESS_CONSTRAINTS, also_writes)
from angelo_agent.program_frames import _setting_paths  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "unexpected": {}, "held_out": {}, "developed": {}, "paid_requests": 0,
          "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
NAME = "angelo_scene_program"
DEVELOPED = {"BEVEL", "ARRAY", "TRACK_TO", "COPY_LOCATION", "LIMIT_LOCATION"}  # Measured while writing the fix.
# Runtime state and identity: an edited material is a copy that commit swaps in under the same name.
SKIPPED = {"rna_type", "execution_time", "persistent_uid", "is_active", "error_location", "error_rotation", "session_uid",
           "users"}
NOT_LOOPED = {"adaptive", "clear", "mirror_object", "set_inverse"}  # Pinned elsewhere (subdivision, mirror tests).


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def record(kind, name, unexpected):
    """Every case's unexpected changes, under the developed or the held-out types."""
    (report["developed"] if kind in DEVELOPED else report["held_out"]).setdefault(kind, {})[name] = unexpected
    if unexpected:
        report["unexpected"]["%s.%s" % (kind, name)] = unexpected


def rna(struct):
    """Every stored RNA value of ``struct`` but collections, exactly: pointers by name, arrays and matrices as lists."""
    values = {}
    for prop in struct.bl_rna.properties:
        key = prop.identifier
        if key in SKIPPED or prop.type == "COLLECTION":
            continue
        value = getattr(struct, key, None)
        if prop.type == "POINTER":
            value = getattr(value, "name", type(value).__name__) if value is not None else None
        elif hasattr(value, "__len__") and not isinstance(value, str):
            value = [list(row) if hasattr(row, "__len__") else row for row in value]
        values[key] = repr(value)
    return values


def changed(before, after):
    return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))


def blender_writes(struct, props):
    """What writing ``props`` ({name: value}) on ``struct`` directly changes: Blender's own coupling (a Bevel's
    width_pct is its width, Copy Rotation's use_offset its mix mode)."""
    before = rna(struct)
    for name, value in props.items():
        setattr(struct, name, value)
    return set(changed(before, rna(struct)))


def settle():
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="kept settings")
    queries.invalidate()


def entry(obj):
    return {"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}


def op(kind, owner, /, **parameters):
    return {"kind": kind, "target": queries.object_handle(owner), "parameters": parameters}


def handles(values):
    """``values`` with each object as its handle, read when the program is built."""
    return {key: queries.object_handle(value) if isinstance(value, bpy.types.Object) else value
            for key, value in values.items()}


def commit(build, writes, reads=()):
    """Approve, stage and commit ``build()`` as the controller does (handles read after the undo step that binds
    them); the result's status and error."""
    settle()
    operations = build()
    try:
        arguments = validate_call(NAME, {"mode": "APPLY", "read_set": [entry(obj) for obj in reads],
                                         "write_set": [entry(obj) for obj in writes], "operations": operations})
        approved = native.precondition(window, NAME, arguments)
    except ValueError as error:
        return "refused before approval", str(error)
    job = native.begin(window, NAME, arguments, approved_obligations=approved.obligations)
    for _ in range(4096):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    if staged.get("ready_to_commit"):
        bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
        staged = job.result or staged
    bpy.context.view_layer.update()
    return staged.get("status"), str(staged.get("error") or "")


def value_for(schema, current, fraction, key=""):
    """A valid value of a typed setting's ``schema`` other than ``current`` (lists: per item)."""
    if "enum" in schema:
        options = [option for option in schema["enum"] if option != current]
        return options[int(fraction * 10) % len(options)]
    kind = schema.get("type")
    if kind == "boolean":
        return not current
    if kind == "array":
        if schema["items"].get("type") == "boolean":
            return [True, False, True] if list(current or ()) != [True, False, True] else [False, True, True]
        lows = [value_for(schema["items"], item, fraction) for item in (current or [None] * schema["minItems"])]
        return lows
    low, high = schema["minimum"], schema["maximum"]
    if key == "minimum":
        fraction = fraction / 4  # Below every maximum this test writes: a limit stays open.
    elif key == "maximum":
        fraction = 1 - fraction / 4
    value = low + (high - low) * fraction
    if kind == "integer":
        value = int(round(value))
        return value if value != current else (value + 1 if value < high else value - 1)
    value = round(value, 4)
    return value if current is None or abs(value - float(current)) > 1e-6 else round(value + (high - low) / 7, 4)


def mesh(name, location=(0, 0, 0)):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], [],
                     [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
    obj = bpy.data.objects.new(name, data)
    obj.location = location
    window.scene.collection.objects.link(obj)
    return obj


def curve(name):
    data = bpy.data.curves.new(name, "CURVE")
    data.splines.new("POLY").points.add(1)
    obj = bpy.data.objects.new(name, data)
    window.scene.collection.objects.link(obj)
    return obj


def operand_of(kind):
    if kind not in MODIFIER_OPERANDS:
        return None
    return curve("Path " + kind) if kind == "CURVE" else mesh("Operand " + kind, (3, 0, 0))


def typed_rna(kind, key):
    return MODIFIER_RNA_NAMES.get(kind, {}).get(key, key)


def owner_with(kind, label, operand):
    """An object with a typed modifier "M" whose settings are all away from Blender's and the schema's defaults."""
    obj = mesh("%s %s" % (label, kind))
    modifier = obj.modifiers.new("M", kind)
    if operand is not None:
        setattr(modifier, typed_rna(kind, MODIFIER_OPERANDS[kind]), operand)
    for key, spec in MODIFIER_SETTINGS[kind]["properties"].items():
        if key not in NOT_LOOPED and key not in MODIFIER_OPERANDS.values():
            setattr(modifier, typed_rna(kind, key), value_for(spec, getattr(modifier, typed_rna(kind, key)), .37, key))
    return obj


def modifier_configure():
    """Each setting configured alone ends the modifier exactly where writing that one property in Blender does."""
    for kind, schema in MODIFIER_SETTINGS.items():
        window.scene = bpy.data.scenes.new("Configure " + kind)
        operand = operand_of(kind)
        obj, twin = owner_with(kind, "Owner", operand), owner_with(kind, "Twin", operand)
        required = {MODIFIER_OPERANDS[kind]: operand} if operand is not None else {}
        for key, spec in schema["properties"].items():
            if key in NOT_LOOPED or key in MODIFIER_OPERANDS.values():
                continue
            prop = typed_rna(kind, key)
            value = value_for(spec, getattr(obj.modifiers["M"], prop), .61, key)
            settings = dict(required, **{key: value})
            coupled = blender_writes(twin.modifiers["M"], {prop: value})
            status, error = commit(lambda: [op("modifier_configure", obj, modifier="M", type=kind,
                                              settings=handles(settings))], [obj], [operand] if operand else [])
            difference = changed(rna(twin.modifiers["M"]), rna(obj.modifiers["M"]))
            canonical = validate_call(NAME, {"mode": "APPLY", "write_set": [entry(obj)], "read_set": [entry(operand)]
                                             if operand else [], "operations": [op(
                                                 "modifier_configure", obj, modifier="M", type=kind,
                                                 settings=handles(settings))]})
            named = {path.split(".", 1)[1] for path in _setting_paths("modifier_configure",
                                                                      canonical["operations"][0]["parameters"])}
            expected = {prop} | set(also_writes(kind, settings)) | {typed_rna(kind, field) for field in required}
            record(kind, "configure " + key, difference + ([] if status == "committed" else [status + ": " + error[:160]]))
            report.setdefault("blender_coupling", {})["%s.%s" % (kind, key)] = sorted(coupled - {prop})
            check("configure_%s_%s_is_blenders_write_of_it" % (kind, key), status == "committed" and not difference
                  and prop in coupled and named == expected)


def modifier_add():
    """A modifier added with one setting is Blender's new modifier with that one property written."""
    for kind, schema in MODIFIER_SETTINGS.items():
        window.scene = bpy.data.scenes.new("Add " + kind)
        obj, twin, operand = mesh("Owner " + kind), mesh("Twin " + kind), operand_of(kind)
        required = {MODIFIER_OPERANDS[kind]: operand} if operand is not None else {}
        for key, spec in schema["properties"].items():
            if key in NOT_LOOPED or key in MODIFIER_OPERANDS.values():
                continue
            prop = typed_rna(kind, key)
            reference = twin.modifiers.new("Added", kind)
            writes = {typed_rna(kind, field): value for field, value in required.items()}
            value = value_for(spec, getattr(reference, prop), .61, key)
            blender_writes(reference, dict(writes, **{prop: value}))
            settings = dict(required, **{key: value})
            status, error = commit(lambda: [op("modifier_add", obj, name="Added", type=kind, settings=handles(settings))],
                                   [obj], [operand] if operand else [])
            added = obj.modifiers.get("Added")
            difference = changed(rna(reference), rna(added)) if added is not None else ["(not added)"]
            record(kind, "add " + key, difference + ([] if status == "committed" else [status + ": " + error[:160]]))
            check("add_%s_%s_is_blenders_modifier_but_for_it" % (kind, key), status == "committed" and not difference)
            twin.modifiers.remove(reference)
            if added is not None:
                obj.modifiers.remove(added)


def constraint_writes(key, value):
    """The RNA properties and values a typed constraint setting writes (action_object.constraint_writes)."""
    if key in CONSTRAINT_AXES:
        return {CONSTRAINT_AXES[key] % axis: item for axis, item in zip("xyz", value)}
    return {key: value}


def constraint_add():
    """A constraint added with one setting is Blender's new constraint with that one property written."""
    spaces = {"influence": {"type": "number", "minimum": 0, "maximum": 1},
              "owner_space": {"enum": ["WORLD", "LOCAL"]}, "target_space": {"enum": ["WORLD", "LOCAL"]}}
    for kind, schema in CONSTRAINT_SETTINGS.items():
        window.scene = bpy.data.scenes.new("Constraint " + kind)
        obj, twin = mesh("Owner " + kind), mesh("Twin " + kind)
        target = None if kind in TARGETLESS_CONSTRAINTS else mesh("Target " + kind, (0, 4, 0))
        cases = {key: spec for key, spec in schema["properties"].items() if key not in NOT_LOOPED}
        cases.update({key: spec for key, spec in spaces.items() if key != "target_space" or target is not None})
        for key, spec in cases.items():
            reference = twin.constraints.new(kind)
            reference.name = "Added"
            if target is not None:
                reference.target = target
            if kind == "TRACK_TO" and key in ("track_axis", "up_axis"):
                value = {"track_axis": "TRACK_X", "up_axis": "UP_X"}[key]  # Apart from the other axis's default.
            elif key in CONSTRAINT_AXES:
                value = value_for(spec, [getattr(reference, CONSTRAINT_AXES[key] % axis) for axis in "xyz"], .61, key)
            else:
                value = value_for(spec, getattr(reference, key), .61, key)
            blender_writes(reference, constraint_writes(key, value))
            parameters = {"type": kind, "name": "Added", "target": target,
                          **({"settings": {key: value}} if key in schema["properties"] else {key: value})}
            if kind == "CHILD_OF":
                parameters.setdefault("settings", {})["set_inverse"] = False  # The inverse is the operation's, not a setting.
            status, error = commit(lambda: [op("constraint_add", obj, **handles(parameters))], [obj],
                                   [target] if target else [])
            added = obj.constraints.get("Added")
            difference = changed(rna(reference), rna(added)) if added is not None else ["(not added)"]
            record(kind, "add " + key, difference + ([] if status == "committed" else [status + ": " + error[:160]]))
            check("constraint_add_%s_%s_is_blenders_but_for_it" % (kind, key), status == "committed" and not difference)
            twin.constraints.remove(reference)
            if added is not None:
                obj.constraints.remove(added)


GENERIC = {"LIMIT_DISTANCE": {"distance": (2.0, 3.5), "limit_mode": ("LIMITDIST_INSIDE", "LIMITDIST_OUTSIDE"),
                              "use_transform_limit": (False, True), "influence": (.8, .4)},
           "STRETCH_TO": {"rest_length": (1.5, 2.5), "bulge": (1.2, 2.0), "volume": ("VOLUME_X", "NO_VOLUME"),
                          "keep_axis": ("PLANE_X", "PLANE_Z")},
           "MAINTAIN_VOLUME": {"volume": (1.3, .7), "free_axis": ("SAMEVOL_Y", "SAMEVOL_Z"), "mode": ("STRICT", "UNIFORM")},
           "FLOOR": {"offset": (.3, .6), "floor_location": ("FLOOR_Z", "FLOOR_NEGATIVE_Y"), "use_rotation": (False, True)},
           "DAMPED_TRACK": {"influence": (.7, .2)}, "COPY_SCALE": {"power": (1.5, 2.5), "use_add": (False, True)}}


def constraint_configure():
    """Each setting configured alone (generic types) ends the constraint where writing that property in Blender does."""
    for kind, values in GENERIC.items():
        window.scene = bpy.data.scenes.new("Configure " + kind)
        target = None if kind == "MAINTAIN_VOLUME" else mesh("Target " + kind, (0, 4, 0))
        owners = []
        for label in ("Owner", "Twin"):
            obj = mesh("%s %s" % (label, kind))
            constraint = obj.constraints.new(kind)
            constraint.name = "C"
            if target is not None:
                constraint.target = target
            for key, (first, _second) in values.items():
                setattr(constraint, key, first)
            owners.append(obj)
        obj, twin = owners
        for key, (_first, second) in values.items():
            blender_writes(twin.constraints["C"], {key: second})
            parameters = {"constraint": "C", **({"influence": second} if key == "influence" else
                                               {"settings": {key: second}})}
            status, error = commit(lambda: [op("constraint_configure", obj, **parameters)], [obj], [target] if target else [])
            difference = changed(rna(twin.constraints["C"]), rna(obj.constraints["C"]))
            record(kind, "configure " + key, difference + ([] if status == "committed" else [status + ": " + error[:160]]))
            check("constraint_configure_%s_%s_is_blenders_write_of_it" % (kind, key), status == "committed"
                  and not difference)


def graph(material):
    """A material's whole graph, exactly: every node's inputs and settings, every link, the viewport colour."""
    tree = material.node_tree
    return {"nodes": {node.name: dict(rna(node), inputs={socket.identifier: repr(list(socket.default_value)
                                      if hasattr(socket.default_value, "__len__") else socket.default_value)
                                      for socket in node.inputs if hasattr(socket, "default_value")})
                      for node in tree.nodes},
            "links": sorted((link.from_node.name, link.from_socket.identifier, link.to_node.name,
                             link.to_socket.identifier) for link in tree.links),
            "material": rna(material)}


# The display value Solid view draws for a Principled input: it follows that input's edit (material_display_sync).
FOLLOWS = {"Base Color": "diffuse_color", "Alpha": "diffuse_color", "Metallic": "metallic", "Roughness": "roughness"}


def red_metal(name, extra=None):
    material = bpy.data.materials.new(name)
    tree = material.node_tree
    shader = next(node for node in tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
    for key, value in (("Base Color", (.8, .1, .1, 1)), ("Metallic", 1.0), ("Roughness", .2), ("IOR", 1.33),
                       ("Alpha", .9), ("Emission Color", (.2, .3, .4, 1)), ("Emission Strength", .5)):
        shader.inputs[key].default_value = value
    noise, bump = tree.nodes.new("ShaderNodeTexNoise"), tree.nodes.new("ShaderNodeBump")
    tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    material.diffuse_color = (.7, .1, .1, 1)
    if extra is not None:
        extra(tree, shader)
    return material


def materials():
    window.scene = bpy.data.scenes.new("Materials")
    for index, (key, spec) in enumerate(_principled_inputs.items()):
        # One held material per input: a second program's edit of one material is refused as shared, before and
        # after this fix (the mesh copy an earlier commit left still counts as its user; reported, not this class).
        obj = mesh("Painted %d" % index)
        obj.data.materials.append(red_metal("Red Metal %d" % index))
        material = obj.material_slots[0].material
        shader = material.node_tree.nodes["Principled BSDF"]
        current = list(shader.inputs[key].default_value) if spec.get("type") == "array" else shader.inputs[key].default_value
        value = value_for(spec, current, .3 + index / 20)
        before, identifier = graph(material), shader.inputs[key].identifier
        status, error = commit(lambda: [op("material_principled", obj, name=material.name, inputs={key: value})], [obj])
        material = obj.material_slots[0].material
        after = graph(material)
        sockets = sorted("%s.%s" % (name, socket) for name in before["nodes"] for socket in before["nodes"][name]["inputs"]
                         if before["nodes"][name]["inputs"][socket] != after["nodes"].get(name, {}).get("inputs", {}).get(socket))
        nodes = [name for name in set(before["nodes"]) | set(after["nodes"]) if name not in after["nodes"]
                 or name not in before["nodes"] or dict(before["nodes"][name], inputs=None) != dict(after["nodes"][name], inputs=None)]
        viewport = changed(before["material"], after["material"])
        unexpected = ([item for item in sockets if item != "Principled BSDF." + identifier] + nodes
                      + (["links"] if before["links"] != after["links"] else [])
                      + [name for name in viewport if FOLLOWS.get(key) != name])
        record("PRINCIPLED", "edit " + key, unexpected + ([] if status == "committed" else [status + ": " + error[:160]]))
        check("principled_%s_changes_only_it_in_place" % key.replace(" ", "_").lower(), status == "committed"
              and not unexpected and sockets == ["Principled BSDF." + identifier] and material.name == "Red Metal %d" % index)

    def linked(tree, shader):
        value = tree.nodes.new("ShaderNodeValue")
        tree.links.new(value.outputs[0], shader.inputs["Metallic"])

    def doubled(tree, shader):
        tree.nodes.new("ShaderNodeBsdfPrincipled")
    def muted(tree, shader):
        shader.mute = True

    def elsewhere(tree, shader):  # Another output is the active one.
        tree.nodes.new("ShaderNodeOutputMaterial").is_active_output = True
    for name, extra, inputs, code in (("Linked", linked, {"Metallic": .3}, "INPUT_LINKED"),
                                      ("Two", doubled, {"Roughness": .3}, "PRINCIPLED_NOT_SINGLE"),
                                      ("Muted", muted, {"Roughness": .3}, "PRINCIPLED_NOT_SHOWN"),
                                      ("Elsewhere", elsewhere, {"Roughness": .3}, "PRINCIPLED_NOT_SHOWN")):
        holder = mesh("Holder " + name)
        holder.data.materials.append(red_metal(name, extra))
        before = graph(holder.material_slots[0].material)
        status, error = commit(lambda: [op("material_principled", holder, name=name, inputs=inputs)], [holder])
        report.setdefault("refusals", {})[name] = error[:240]
        check("principled_%s_%s_is_refused_unchanged" % (name.lower(), code.lower()), status != "committed" and code in error
              and graph(holder.material_slots[0].material) == before)

    def through_mute(tree, shader):  # A muted Mix Shader passes its first shader through.
        mix = tree.nodes.new("ShaderNodeMixShader")
        mix.mute = True
        tree.links.new(shader.outputs["BSDF"], mix.inputs[1])
        tree.links.new(mix.outputs[0], tree.nodes["Material Output"].inputs["Surface"])

    def engine_outputs(tree, shader):  # The active ALL output shows an Emission; each engine's own, the shader.
        output = tree.nodes["Material Output"]
        tree.links.new(tree.nodes.new("ShaderNodeEmission").outputs[0], output.inputs["Surface"])
        for target in ("EEVEE", "CYCLES"):
            node = tree.nodes.new("ShaderNodeOutputMaterial")
            node.target = target
            tree.links.new(shader.outputs["BSDF"], node.inputs["Surface"])
        output.is_active_output = True
    for name, extra in (("Through mute", through_mute), ("Engine outputs", engine_outputs)):
        holder = mesh("Shown " + name)
        holder.data.materials.append(red_metal(name, extra))
        status, error = commit(lambda: [op("material_principled", holder, name=name, inputs={"Roughness": .35})],
                               [holder])
        shader = holder.material_slots[0].material.node_tree.nodes["Principled BSDF"]
        report.setdefault("shown", {})[name] = [status, error[:200]]
        check("principled_shown_%s_is_edited" % name.lower().replace(" ", "_"), status == "committed"
              and abs(shader.inputs["Roughness"].default_value - .35) < 1e-6)

    fresh = mesh("Fresh holder")
    status, error = commit(lambda: [op("material_principled", fresh, name="Fresh", inputs={"Roughness": .3})], [fresh])
    report["fresh"] = [status, error[:300]]
    check("a_new_material_commits", status == "committed" and len(fresh.material_slots) == 1)
    made = fresh.material_slots[0].material.node_tree.nodes["Principled BSDF"]
    reference = bpy.data.materials.new("Reference").node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    def stored(node):
        return {socket.identifier: repr(list(socket.default_value) if hasattr(socket.default_value, "__len__")
                                        else socket.default_value) for socket in node.inputs if hasattr(socket, "default_value")}
    made_values, reference_values = stored(made), stored(reference)
    differing = sorted(key for key in set(made_values) | set(reference_values)
                       if made_values.get(key) != reference_values.get(key))
    report["new_material_differs_from_blender_in"] = differing
    check("a_new_material_keeps_blenders_defaults_but_the_stated_input", status == "committed" and differing == ["Roughness"])
    defaults = {key: list(reference.inputs[key].default_value) if hasattr(reference.inputs[key].default_value, "__len__")
                else reference.inputs[key].default_value for key in _principled_inputs}
    check("new_inputs_are_blenders_principled_defaults", all(
        (all(abs(a - b) < 1e-6 for a, b in zip(value, defaults[key])) if isinstance(value, list)
         else abs(value - defaults[key]) < 1e-6) for key, value in material_rules.NEW_INPUTS.items()))


def keys():
    window.scene = bpy.data.scenes.new("Keys")
    obj = bpy.data.objects.new("Keyed", None)
    window.scene.collection.objects.link(obj)
    obj.keyframe_insert("location", frame=5)
    for curve_ in obj.animation_data.action.layers[0].strips[0].channelbags[0].fcurves:
        curve_.keyframe_points[0].interpolation = "LINEAR"

    def interpolation(frame):
        found = {key.interpolation for bag in obj.animation_data.action.layers[0].strips[0].channelbags
                 for curve_ in bag.fcurves if curve_.data_path == "location" for key in curve_.keyframe_points
                 if abs(key.co.x - frame) < 1e-5}
        return sorted(found)
    status, _ = commit(lambda: [op("keyframe_insert", obj, property="location", value=[1, 2, 3], frame=5)], [obj])
    check("a_key_at_the_frame_keeps_its_interpolation", status == "committed" and interpolation(5) == ["LINEAR"]
          and list(obj.location) == [1, 2, 3])
    # At another frame than the current one: committed as when an interpolation is written (it re-evaluates).
    status, error = commit(lambda: [op("keyframe_insert", obj, property="location", value=[2, 2, 3], frame=9)], [obj])
    report["new_key"] = {"status": status, "error": error[:200], "interpolation": interpolation(9),
                         "preference": bpy.context.preferences.edit.keyframe_new_interpolation_type}
    check("a_new_key_takes_the_preference", status == "committed"
          and interpolation(9) == [bpy.context.preferences.edit.keyframe_new_interpolation_type])
    status, _ = commit(lambda: [op("keyframe_insert", obj, property="location", value=[3, 2, 3], frame=5,
                           interpolation="CONSTANT")], [obj])
    check("a_stated_interpolation_is_written", status == "committed" and interpolation(5) == ["CONSTANT"])


try:
    bpy.data.batch_remove(ids=list(bpy.data.objects) + list(bpy.data.meshes) + list(bpy.data.materials))
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    for part in (modifier_configure, modifier_add, constraint_add, constraint_configure, materials, keys):
        try:
            part()
        except AssertionError:
            report.setdefault("failed_parts", []).append(part.__name__ + ": " + traceback.format_exc()[-400:])
    check("every_part_passed", not report.get("failed_parts"))
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
report.pop("last_commit", None)
(OUT / "kept-settings.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_KEPT_SETTINGS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
