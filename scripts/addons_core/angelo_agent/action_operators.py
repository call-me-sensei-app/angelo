# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``operator_run`` kernel: one allow-listed C operator on one private staged object.

The operator runs with ``EXEC_DEFAULT`` (never invoke or modal) inside an
override of the disposable stage only: its scene and view layer, the staged
target as the only selected and active object, and no editor area or region,
so nothing reads the artist's view (``action_operator_modes``). Edit-mode
modules (and Grease Pencil strokes) enter Edit mode on the target and leave it
before returning, pose operators Pose mode; constraint operators and the
object subset run in Object mode (the subset in Edit mode when its poll needs
it or an element selection is requested). Transform operators alone get a
context window for their call, with the stage's scene and members inside it
and the stage tool settings they would read pinned.

Before running: the allow-list and the installed-RNA admission rules, every
property against the operator's RNA definition (hidden ones only where a
family names them: the transform pivot, the edited constraint), pinned and
default values, integer amplifiers within ``CAPS``, the estimated result
within the native element budget (bones: the evaluation budget) and, for
superlinear operators and proportional editing, the input within its work
bound once the selection is applied. After: Object mode again, every new ID
tracked (a new object or a datablock other than a shape-key block is
refused), no ID freed, bounded finite data (meshes within the element
budget; curves, strokes and hair within the point budget), a changed
``operator_state.data_digest`` and no stack change outside the operator's
families. Blender's selection result is kept.
"""

from array import array

import bpy

from .refusal_codes import RefusalError, refusal_code, refusal_details
from .action_budget import _update_stage, assert_evaluation_budget
from .action_context import _mesh, _readback
from .action_edit_context import require_stage_context
from .action_operator_modes import (call_override, mode_override, modes as _modes, pinned_tool_settings, program_window,
                                    select as _select)
from .action_operator_spec import (ALLOWED, BENIGN_INTS, CAPS, POSE_MODULES, WORK_BOUNDS, OperatorError, admitted_by_name,
                                   bone_estimate, estimate, families, module_of, target_types, within_work_bound)
from . import action_operator_families as _families
from .action_schema_types import MAX_EDGES, MAX_EVALUATED_BONES, MAX_FACES, MAX_VERTICES
from .rna_rules import FILE_SUBTYPES, PropertyPathError, check_value
from . import operator_state

# Blender flags marking interactive, cursor-bound, internal or macro operators.
DENIED_OPTIONS = frozenset({"INTERNAL", "MACRO", "BLOCKING", "GRAB_CURSOR", "GRAB_CURSOR_X", "GRAB_CURSOR_Y",
                            "DEPENDS_ON_CURSOR", "MODAL_PRIORITY"})
# Material-slot buttons Blender flags INTERNAL (hidden from search); reviewed by name, they skip that rule.
INTERNAL_BY_NAME = frozenset(name for name in ALLOWED if name.startswith("object.material_slot_"))
# Transform operators are BLOCKING only when invoked (their modal mouse loop); EXEC applies explicit values.
BLOCKING_BY_MODULE = frozenset({"transform"})
MAX_OPERATOR_ELEMENTS = MAX_VERTICES + MAX_EDGES + MAX_FACES
_FILE_WORDS = ("filepath", "file_path", "directory", "filename", "files")


def operator(idname):
    """The installed operator and its RNA definition, or a MISSING_IN_BUILD refusal."""
    module, name = idname.split(".", 1)
    call = getattr(getattr(bpy.ops, module, None), name, None)
    try:
        rna = call.get_rna_type() if call is not None else None
    except (KeyError, RuntimeError, AttributeError):
        rna = None
    if rna is None:
        raise OperatorError("MISSING_IN_BUILD", "%s is not an operator of this build" % idname)
    return call, rna


def admission(idname):
    """(call, rna, growth spec) for an operator the allow-list and the installed-RNA rules both admit."""
    spec = admitted_by_name(idname)
    call, rna = operator(idname)
    if bpy.types.Operator.bl_rna_get_subclass_py(call.idname()) is not None:
        raise OperatorError("PYTHON_OPERATOR", "%s is defined in Python; use reviewed Python" % idname)
    options = set(call.bl_options)
    denied = (options & DENIED_OPTIONS - ({"INTERNAL"} if idname in INTERNAL_BY_NAME else set())
              - ({"BLOCKING"} if module_of(idname) in BLOCKING_BY_MODULE else set()))
    if denied:
        raise OperatorError("INTERACTIVE", "%s is %s" % (idname, ", ".join(sorted(denied)).lower()))
    if "UNDO" not in options:
        raise OperatorError("NO_UNDO", "%s does not change data undoably" % idname)
    for prop in rna.properties:
        if prop.identifier == "rna_type":
            continue
        if prop.type in {"POINTER", "COLLECTION"}:
            raise OperatorError("DATA_PROPERTY", "%s takes datablocks or collections (%s)" % (idname, prop.identifier))
        if (prop.type == "STRING" and getattr(prop, "subtype", "NONE") in FILE_SUBTYPES) or any(
                word in prop.identifier for word in _FILE_WORDS):
            raise OperatorError("FILE_PROPERTY", "%s reads or writes files (%s)" % (idname, prop.identifier))
    return call, rna, spec


def check_properties(idname, rna, spec, properties):
    """Raise unless every property suits the RNA definition; return the call keywords and amplifier values."""
    module = module_of(idname)
    hidden = _families.HIDDEN_SETTABLE.get(module, frozenset())  # The transform pivot, the edited constraint.
    keywords, settable = {}, sorted(prop.identifier for prop in rna.properties if prop.identifier != "rna_type"
                                    and (not prop.is_hidden or prop.identifier in hidden) and not prop.is_readonly)
    for name, value in properties.items():
        prop = rna.properties.get(name)
        if prop is None or name == "rna_type":
            raise OperatorError("UNKNOWN_PROPERTY", "%s has no %s; settable: %s" % (idname, name, ", ".join(settable)[:600]))
        # Operator properties are all registered at run time; admission proved this one is a C operator.
        if (prop.type not in {"BOOLEAN", "INT", "FLOAT", "ENUM", "STRING"} or prop.is_readonly
                or (prop.is_hidden and name not in hidden)):
            raise OperatorError("PROPERTY_NOT_SETTABLE", "%s.%s is internal or not a setting" % (idname, name))
        if prop.type == "STRING" and getattr(prop, "subtype", "NONE") in FILE_SUBTYPES:
            raise OperatorError("FILE_PROPERTY", "%s.%s is a file path" % (idname, name))
        dimensions = tuple(getattr(prop, "array_dimensions", None) or (0, 0, 0))
        if len(dimensions) > 1 and dimensions[1] > 0:
            raise OperatorError("PROPERTY_NOT_SETTABLE", "%s.%s is a matrix" % (idname, name))
        try:
            check_value(prop, name, value)
        except PropertyPathError as error:
            raise OperatorError("PROPERTY_" + error.code, "%s.%s: %s" % (idname, name, error),
                                refusal_policy=refusal_code(error), details=refusal_details(error)) from error
        if prop.type == "INT" and name not in CAPS and name not in BENIGN_INTS:
            raise OperatorError("PROPERTY_NOT_SETTABLE", "%s.%s keeps its default natively" % (idname, name))
        keywords[name] = set(value) if prop.type == "ENUM" and getattr(prop, "is_enum_flag", False) else value
    # Pinned values always, defaults unless set: nothing falls back to a tool setting the stage did not choose.
    keywords.update({name: value for name, value in _families.PINNED.get(module, {}).items() if name in rna.properties})
    for name, value in _families.DEFAULTS.get(module, {}).items():
        if name in rna.properties:
            keywords.setdefault(name, value)
    amplifiers = {}
    for name, cap in CAPS.items():
        prop = rna.properties.get(name)
        if prop is None or prop.type != "INT" or getattr(prop, "is_array", False):
            continue
        amplifiers[name] = int(properties.get(name, prop.default))
        if amplifiers[name] > cap:
            raise OperatorError("AMPLIFIER_BOUND", "%s.%s is at most %d natively" % (idname, name, cap))
    return keywords, amplifiers


def element_counts(obj):
    """Base elements the growth estimate scales: mesh elements, curve points, bones or lattice points."""
    data = obj.data
    if obj.type == "MESH":
        return {"elements": len(data.vertices) + len(data.edges) + len(data.polygons), "edges": len(data.edges)}
    if obj.type == "CURVE":
        points = sum(len(spline.bezier_points) + len(spline.points) for spline in data.splines) + len(data.splines)
        return {"elements": points, "edges": points}
    if obj.type == "ARMATURE":
        return {"elements": len(data.bones), "edges": len(data.bones)}
    if obj.type == "LATTICE":
        return {"elements": len(data.points), "edges": len(data.points)}
    if obj.type == "GREASEPENCIL":  # Layers plus every stroke point of every drawing (an upper bound).
        points = sum(len(frame.drawing.attributes["position"].data) for layer in data.layers for frame in layer.frames
                     if frame.drawing is not None and frame.drawing.attributes.get("position") is not None)
        return {"elements": len(data.layers) + points, "edges": points}
    if obj.type == "CURVES":
        return {"elements": len(data.points) + len(data.curves), "edges": len(data.points)}
    if obj.type == "FONT":  # Characters (text operators grow the body by at most one insertion).
        return {"elements": len(data.body), "edges": len(data.body)}
    return {"elements": 0, "edges": 0}


def poll_mode(context, obj, idname):
    """The mode an admitted operator's poll passes in on this staged target, or None (coverage metrics)."""
    call, _rna, _spec = admission(idname)
    if obj.type not in target_types(idname):
        return None
    window = program_window()
    for mode in _modes(idname, obj, {"select": None}):
        with mode_override(context, obj, mode) as members, call_override(context, obj, idname, window, members):
            if call.poll():
                return mode
    return None


def _ids():
    """(Main collection, pointer) -> session UID of every ID in the file; old wrappers are never dereferenced."""
    data, found = bpy.data, {}
    for prop in data.bl_rna.properties:
        if prop.type == "COLLECTION" and prop.identifier != "all_ids":  # all_ids repeats every other list.
            for block in getattr(data, prop.identifier):
                found[(prop.identifier, block.as_pointer())] = block.session_uid
    return found


def _guard_frees(idname, obj, keywords):
    """Refuse the known paths that free a staged datablock (a freed ID would leave a dangling wrapper)."""
    key = getattr(obj.data, "shape_keys", None)
    if idname == "object.shape_key_remove" and key is not None and (keywords.get("all") or len(key.key_blocks) <= 1):
        raise OperatorError("SHAPE_KEY_FREE", "removing every shape key deletes the shape-key datablock; remove keys "
                                              "while the basis remains, or use reviewed Python")
    animation = obj.animation_data
    if module_of(idname) in POSE_MODULES and animation is not None and (animation.action or animation.nla_tracks
                                                                         or animation.drivers):
        raise OperatorError("ANIMATED_POSE", "the armature's pose is animated or driven; edit its Action instead")
    if idname == "object.forcefield_toggle" and getattr(obj, "field", None) is None:
        raise OperatorError("FIELD_SETTINGS_MISSING", "this object has no force-field settings; creating them on the "
                                                      "live object is reviewed Python")
    bound = _families.RECURSION_BOUNDS.get(idname)
    if bound is not None and obj.type == "MESH" and len(obj.data.vertices) > bound:
        raise OperatorError("RECURSION_BOUND", "%s recurses once per connected vertex: at most %d vertices natively "
                                               "(a deeper recursion would crash Blender)" % (idname, bound))
    if module_of(idname) == "constraint" and obj.constraints.get(keywords["constraint"]) is None:
        raise OperatorError("CONSTRAINT_MISSING", "this object has no constraint named %r (bone constraints are "
                                                  "reviewed Python)" % keywords["constraint"][:64])
    if ("constraints" in _families.OPERATOR_FAMILIES.get(idname, ())
            and any(item.type == "ARMATURE" for item in obj.constraints)):
        # Commit and its rollback rebuild constraints from RNA settings, which hold no target list.
        raise OperatorError("CONSTRAINT_TARGETS", "this stack has an Armature constraint, whose target list neither "
                                                  "commit nor its rollback carries; use reviewed Python")


def _stacks(obj):
    """The object's modifier and constraint stacks by name and type (committed only by their families)."""
    return ([(item.name, item.type) for item in obj.modifiers] if hasattr(obj, "modifiers") else [],
            [(item.name, item.type) for item in obj.constraints])


def _kept(block, pointers):
    """False for an ID the operator freed. Blender invalidates a freed ID's wrapper, so even its
    ``as_pointer()`` raises ReferenceError; pointers identify the rest."""
    try:
        return block.as_pointer() not in pointers
    except ReferenceError:
        return False


def _account_ids(context, before, after):
    """Track every new ID and drop freed ones; return the refusal they imply, if any."""
    freed = [key for key, uid in before.items() if after.get(key) != uid]
    if freed:
        pointers = {pointer for _collection, pointer in freed}
        context.owned_ids = {block for block in context.owned_ids if _kept(block, pointers)}
        context.created_ids = [block for block in context.created_ids if _kept(block, pointers)]
    created = [(collection, pointer) for (collection, pointer) in after if (collection, pointer) not in before]
    blocks = {collection: {block.as_pointer(): block for block in getattr(bpy.data, collection)}
              for collection in {collection for collection, _pointer in created}}
    unexpected = []
    for collection, pointer in created:
        context.track_id(blocks[collection][pointer])  # Owned by the stage, so cleanup removes it.
        if collection != "shape_keys":
            unexpected.append(collection)
    if freed:
        return OperatorError("FREED_DATA", "the operator removed %d datablock(s)" % len(freed))
    if unexpected:
        return OperatorError("NEW_OBJECTS" if "objects" in unexpected else "NEW_DATABLOCKS",
                             "the operator created %s; operator_run edits one object's data" % ", ".join(sorted(set(unexpected))))
    return None


def _selected_stroke_points(obj):
    """(points, selected points) over every Grease Pencil drawing; a drawing without selection counts as selected."""
    points = selected = 0
    for layer in obj.data.layers:
        for frame in layer.frames:
            attributes = frame.drawing.attributes if frame.drawing is not None else None
            count = len(attributes["position"].data) if attributes is not None and "position" in attributes else 0
            flags = array("b", [1]) * count
            selection = attributes.get(".selection") if count else None
            if selection is not None and selection.domain == "POINT" and selection.data_type == "BOOLEAN":
                selection.data.foreach_get("value", flags)
            points, selected = points + count, selected + flags.count(1)
    return points, selected


def _check_work(idname, obj, keywords):
    """Refuse a WORK_BOUNDS operator whose elements x selected elements exceed its measured bound: mesh V+E+F,
    Grease Pencil stroke points."""
    if idname not in WORK_BOUNDS or obj.type not in {"MESH", "GREASEPENCIL"}:
        return
    if obj.type == "GREASEPENCIL":
        elements, selected = _selected_stroke_points(obj)
        if not within_work_bound(idname, elements, selected):
            raise OperatorError("WORK_BOUND", "%s takes time growing with the square of its input: %d stroke points "
                                              "with %d selected exceed its native bound" % (idname, elements, selected))
        return
    mesh = obj.data
    domains = (mesh.vertices, mesh.edges, mesh.polygons)
    elements, selected = sum(len(domain) for domain in domains), 0
    for domain in domains:
        flags = array("b", [0]) * len(domain)
        if len(domain):
            domain.foreach_get("select", flags)
        selected += flags.count(1)
    if not within_work_bound(idname, elements, selected):
        raise OperatorError("WORK_BOUND", "%s takes time growing with the square of its input: %d elements with %d "
                                          "selected exceed its native bound; select fewer elements" % (idname, elements, selected))


_MODES = {"VERT": (True, False, False), "EDGE": (False, True, False), "FACE": (False, False, True)}
_DOMAINS = {"vertex_indices": "VERT", "edge_indices": "EDGE", "face_indices": "FACE"}


def _execute(context, obj, idname, call, keywords, params, modes, window):
    """Run the operator in the first mode whose poll passes; return (that mode or None, baseline digest).

    The requested Edit-mode selection is applied in its own Edit-mode pass
    first, so the baseline the no-effect check compares with already has it:
    an operator that changes nothing is refused even when the selection did.
    """
    baseline = None
    for mode in modes:
        if mode in {"EDIT", "POSE"} and (params["select"] or "ALL") != "KEEP":
            with mode_override(context, obj, mode):
                _select(obj, params)
        baseline = operator_state.data_digest(obj)
        _check_work(idname, obj, keywords)
        with mode_override(context, obj, mode) as members, call_override(context, obj, idname, window, members):
            if not call.poll():
                continue
            require_stage_context(context)  # The stage's scene and view layer, never the artist's.
            try:
                result = call("EXEC_DEFAULT", **keywords)
            except RuntimeError as error:
                raise OperatorError("OPERATOR_FAILED", str(error).strip()[:300], refusal_policy=refusal_code(error), details=refusal_details(error)) from error
            except TypeError as error:
                raise OperatorError("PROPERTY_REJECTED", str(error).strip()[:300], refusal_policy=refusal_code(error), details=refusal_details(error)) from error
            if "FINISHED" not in result:
                raise OperatorError("NOT_FINISHED", "%s returned %s" % (idname, ", ".join(sorted(result))))
            return mode, baseline
    return None, baseline


def run(context, obj, operation):
    """Run one admitted operator on ``obj`` (a resolved staged target); return a JSON read-back."""
    params = operation["parameters"]
    idname = params["operator"]
    call, rna, spec = admission(idname)
    if obj.type not in target_types(idname):
        raise OperatorError("TARGET_TYPE", "%s runs on %s objects, not %s" % (idname, "/".join(target_types(idname)), obj.type))
    data = obj.data
    if data is not None:
        context.require_private(data)
    keywords, amplifiers = check_properties(idname, rna, spec, params["properties"])
    if idname == "transform.resize" and (params["select"] or "ALL") == "ALL" and obj.type == "MESH" and (
            any(vertex.hide for vertex in data.vertices) or any(face.hide for face in data.polygons)):
        # Selecting all leaves hidden elements out, so the resize changes the shape too (derived_geometry counts
        # it as extent only): an unverified effect of this program (program_obligation_lifecycle).
        context.extras.setdefault("unverified_effects", []).append({
            "operation": None, "root": {"target": operation["target"]}, "reason": "unchecked_geometry_edit",
            "affected": [{"kind": "operator_run", "unobserved": ["shape"], "hidden_elements": True}]})
    if estimate(spec, element_counts(obj), amplifiers) > MAX_OPERATOR_ELEMENTS:
        raise OperatorError("GROWTH_BOUND", "%s could exceed the native element budget on this object" % idname)
    if obj.type == "ARMATURE" and bone_estimate(spec, len(obj.data.bones), amplifiers) > MAX_EVALUATED_BONES:
        raise OperatorError("GROWTH_BOUND", "%s could make more than %d bones on this armature (refused before it "
                                            "runs: naming new bones slows superlinearly)" % (idname, MAX_EVALUATED_BONES))
    if params["select"] == "INDICES":
        generation = context.topology_versions.get(obj, 0)
        if generation != (0 if params["index_scope"] == "INITIAL" else params["topology_step"]):
            raise RefusalError("Stale topology indexes: bind CURRENT indexes to the exact observed topology_step",
                               code='STALE_REVISION')
    _guard_frees(idname, obj, keywords)
    modes = _modes(idname, obj, params)
    if not modes:
        raise OperatorError("SELECTION", "element selection needs an object type with Edit mode")
    if (params["select_mode"] is not None or params["select"] == "INDICES") and obj.type != "MESH":
        raise OperatorError("SELECTION", "select_mode and element indexes choose mesh vertices, edges or faces")
    shape = operator_state.topology(data) if obj.type == "MESH" else None
    stacks = _stacks(obj)
    if module_of(idname) == "armature" and obj.pose is None:
        _update_stage(context)  # A resumed stage rebuilds its poses on the next evaluation.
    pose = operator_state.pose_extras(obj) if module_of(idname) == "armature" else None
    settings = context.scene.tool_settings  # The private stage scene's; later operations inherit the select mode.
    sync, select_mode = settings.use_uv_select_sync, tuple(settings.mesh_select_mode)
    wanted = params["select_mode"] or next((_DOMAINS[key] for key in _DOMAINS if params[key]), None)
    if obj.type == "MESH" and wanted:
        settings.mesh_select_mode = _MODES[wanted]
    settings.use_uv_select_sync = True  # Mesh selection is the UV selection for uv operators.
    before, ran, refusal, window = _ids(), None, None, program_window()
    try:
        with pinned_tool_settings(context, idname):
            ran, digest = _execute(context, obj, idname, call, keywords, params, modes, window)
    finally:
        settings.use_uv_select_sync = sync
        if ran is None:
            settings.mesh_select_mode = select_mode
        refusal = _account_ids(context, before, _ids())
    if refusal is not None:
        raise refusal
    if ran is None:
        raise OperatorError("POLL_FAILED", "%s cannot run on this staged %s object (its poll failed)" % (idname, obj.type))
    context.assert_staged(obj)  # Back in Object mode, still private.
    if obj.data != data:
        raise OperatorError("DATA_REPLACED", "%s replaced the object's data" % idname)
    if obj.type == "MESH":
        _mesh(context, obj)
    elif obj.type in {"CURVE", "GREASEPENCIL", "CURVES"} and element_counts(obj)["elements"] > MAX_VERTICES:
        # Points (and splines, layers or curves): growth the estimate allowed must still fit the budget.
        raise OperatorError("GROWTH_BOUND", "%s left more than %d points on this staged %s object (the native point "
                                            "budget)" % (idname, MAX_VERTICES, obj.type))
    assert_evaluation_budget(context, obj)
    try:
        changed = operator_state.data_digest(obj) != digest
    except RuntimeError as error:  # A ProgramError: non-finite or unbounded values (deleting every curve point).
        raise OperatorError("UNREADABLE_RESULT", "%s left data the host cannot read exactly: %s" % (idname, error),
                            refusal_policy=refusal_code(error), details=refusal_details(error)) from error
    if not changed:
        raise OperatorError("NO_EFFECT", "%s changed nothing on this object; check its selection and properties" % idname)
    committed = families(params)
    if any(after != before and family not in committed
           for before, after, family in zip(stacks, _stacks(obj), ("modifiers", "constraints"))):
        # Shade Flat/Smooth remove a "Smooth by Angle" modifier, a force field's shape adds or removes a Surface
        # modifier: on the stage only, since only the modifiers and constraints families carry a stack.
        raise OperatorError("UNCOMMITTED_STATE", "%s changed the object's modifier or constraint stack, which "
                                                 "this operator does not commit; edit the stack first" % idname)
    if pose is not None:
        lost = operator_state.untransferred_pose_changes(pose, operator_state.pose_extras(obj))
        if lost:
            raise OperatorError("POSE_DATA", "%s copied or changed bone constraints, custom properties or custom shape "
                                             "transforms (%s), which commit does not carry; use reviewed Python"
                                % (idname, ", ".join(lost[:4])[:200]))
    extra = {"operator": idname, "mode": ran}
    if module_of(idname) in POSE_MODULES or module_of(idname) == "armature":
        # Commit copies every bone's pose channels and settings (bone_state.transfer_pose): after a pose
        # operator, and after an armature operator, whose duplicated or mirrored bones copy their sources'.
        context.extras.setdefault("posed_bones", {})[operation["target"]] = [bone.name for bone in obj.pose.bones]
        context.extras.setdefault("posed_channels", {})[operation["target"]] = None  # Every channel, any bone.
    if shape is not None and operator_state.topology(obj.data) != shape:
        context.topology_versions[obj] = context.topology_versions.get(obj, 0) + 1
        extra["topology_step"] = context.topology_versions[obj]
    return _readback(obj, operation, **extra)
