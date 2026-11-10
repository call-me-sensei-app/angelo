# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""IDs a stage's animation reaches along RNA paths, checked before every stage evaluation (doc/angelo/
agent_execution.rst, "Links between staged copies"; H5).

``foreach_ID_link`` reports every pointer an ID holds, but a path is not a pointer: Blender resolves a driver
variable's path from its target and builds the ID that owns the property at its end into the depsgraph
(``build_driver_id_property``), and the animation system writes an F-Curve's or a driver's destination through its
path from the animated ID. A path can cross into data no link names (``view_layers[...].objects.active``,
``children``, ``users_collection``, an ID-valued custom property of a borrowed object), so the stage graph would
evaluate it and write its animation back to the person's file, or a staged channel would write it directly.

``check`` resolves every such path step by step from where Blender starts it (a variable's target, or the stage's
own scene or view layer for a context variable; the animated ID for destinations and F-Curves) and collects every ID
a step lands in:

* a read (a driver variable's path) may cross only owned, ``live`` or ``borrowed`` data (``stage_routes``); a scene
  that is not the stage's own is refused, never remapped;
* a write (a driver's destination, a keyed F-Curve of an Action or NLA strip) may cross only the stage's own IDs.

A path that does not resolve builds and writes nothing in Blender either, and is skipped.
"""

from .program_jobs import ProgramError
from .program_dependencies import _bpy
from .scene_read_values import label

READ_REFUSAL = ("A driver on this edit's data reads \"{path}\" through the {kind} \"{name}\", which a staged edit "
                "cannot read without changing the person's file. Use reviewed Python for this edit.")
WRITE_REFUSAL = ("Animation or a driver on this edit's data writes \"{path}\" through the {kind} \"{name}\", which "
                 "the edit does not stage. Use reviewed Python for this edit.")
DRIVER_REFUSAL = ("A driver on this edit's data (\"{path}\": {reason}) is not one a staged edit can evaluate on its "
                  "own. Use reviewed Python for this edit.")
PYTHON_REFUSAL = ("Animation, a driver or an Attribute node on this edit's data uses \"{path}\", which an add-on's "
                  "Python defines and would run while staging. Use reviewed Python for this edit.")
UNPARSED_REFUSAL = ("A driver or animation path on this edit's data (\"{path}\") cannot be checked, so a staged edit "
                    "cannot keep it separate from the person's file. Use reviewed Python for this edit.")


def cuts(path):
    """End offsets of every step of an RNA ``path`` (Blender's ``rna_path_token`` grammar: names split by ``.``,
    ``[...]`` keys with double-quoted, backslash-escaped strings), the whole path last; None when unbalanced."""
    result, index, size = [], 0, len(path)
    while index < size:
        char = path[index]
        if char == "[":
            if index:
                result.append(index)
            index += 1
            while index < size and path[index] != "]":
                if path[index] == "\"":
                    index += 1
                    while index < size and path[index] != "\"":
                        index += 2 if path[index] == "\\" else 1
                    if index >= size:
                        return None
                index += 1
            if index >= size:
                return None
        elif char == ".":
            if index:
                result.append(index)
        index += 1
    result.append(size)
    return [cut for cut in dict.fromkeys(result) if cut]


class Paths:
    """One check's memo of resolved steps: {(start pointer, prefix): ID pointer or None, ID}."""

    def __init__(self):
        self.memo = {}

    def crossed(self, start, path):
        """Every ID resolving ``path`` from ``start`` lands in, ``start``'s own ID first; [] when the whole path does
        not resolve (Blender builds and writes nothing through it); None when ``path`` cannot be split."""
        steps = cuts(path)
        if steps is None:
            return None
        owner = getattr(start, "id_data", None)
        base = start.as_pointer()
        found = [owner] if owner is not None else []
        for cut in steps:
            key = (base, path[:cut])
            if key not in self.memo:
                self.memo[key] = _landing(start, path[:cut])
            value = self.memo[key]
            if value is False:
                return []
            if value is not None:
                found.append(value)
        return found


def _landing(start, prefix):
    """The ID a resolved ``prefix`` lands in (None: a plain value), or False when it does not resolve."""
    bpy = _bpy()
    try:
        value = start.path_resolve(prefix, False)   # Only after metadata rejects Python callbacks.
    except (ValueError, TypeError, AttributeError):
        return False
    if isinstance(value, bpy.types.ID):
        return value
    try:
        value = value.id_data   # A struct, collection or property of one: the ID that owns it.
    except AttributeError:
        return None
    return value if isinstance(value, bpy.types.ID) else None


def _animated(blocks):
    """Every owned ID and the embedded IDs and shape keys it carries (its own: copied with it), once each."""
    seen, result = set(), []
    for block in blocks:   # An object's data is a block of its own when owned (its keys come with it).
        tree = getattr(block, "node_tree", None)
        for item in (block, tree if tree is not None and tree.is_embedded_data else None,
                     getattr(block, "shape_keys", None)):
            if item is not None and item.as_pointer() not in seen and hasattr(item, "animation_data"):
                seen.add(item.as_pointer())
                result.append(item)
    return result


def _actions(animation):
    """(Action, its slot for this ID) of the assigned Action and of every NLA strip, meta strips' children included
    (Blender evaluates them all)."""
    if animation.action is not None:
        yield animation.action, getattr(animation, "action_slot", None)
    strips = [strip for track in animation.nla_tracks for strip in track.strips]
    while strips:
        strip = strips.pop()
        if strip.action is not None:
            yield strip.action, getattr(strip, "action_slot", None)
        strips.extend(strip.strips)


def _curves(action, slot):
    """The F-Curves of a layered Action this ID evaluates: its slot's channelbags (every slot when none is known)."""
    for layer in action.layers:
        for strip in layer.strips:
            if slot is not None and hasattr(strip, "channelbag"):
                bag = strip.channelbag(slot)
                yield from (bag.fcurves if bag is not None else ())
                continue
            for bag in getattr(strip, "channelbags", ()):
                yield from bag.fcurves


ATTRIBUTE_PATHS = frozenset({"OBJECT", "INSTANCER", "VIEW_LAYER"})   # Resolved as RNA paths (object_dupli.cc).


def _constraint_actions(owner):
    """(Action, slot) of every Action constraint on ``owner`` and its pose bones: Blender evaluates their F-Curves
    from the owner (what_does_obaction)."""
    constraints = list(getattr(owner, "constraints", ()))
    pose = getattr(owner, "pose", None)
    for bone in (pose.bones if pose is not None else ()):
        constraints.extend(bone.constraints)
    for constraint in constraints:
        if constraint.type == 'ACTION' and getattr(constraint, "action", None) is not None:
            yield constraint.action, getattr(constraint, "action_slot", None)


def _attribute_paths(owner, stage, objects):
    """Read paths of Attribute nodes Blender resolves as RNA paths: from each owned object (OBJECT, INSTANCER) or
    the stage's view layer (VIEW_LAYER)."""
    for node in getattr(owner, "nodes", ()):
        if node.bl_idname != "ShaderNodeAttribute" or node.attribute_type not in ATTRIBUTE_PATHS:
            continue
        # VIEW_LAYER names are looked up on the view layer, then its scene, then the scene's World (Blender).
        scene = getattr(stage, "scene", None)
        layered = [stage.view_layer, scene, getattr(scene, "world", None)]
        starts = [start for start in layered if start is not None] if node.attribute_type == "VIEW_LAYER" else objects
        for start in starts:
            yield start, node.attribute_name, False


def _paths(owner, stage, objects=()):
    """(start, path, is_write, driver curve or None) of every path Blender resolves for ``owner``'s evaluation:
    its Action, NLA and Action-constraint F-Curves and drivers (destinations written), driver variables and
    Attribute nodes (read)."""
    yield from ((start, path, False, None) for start, path, _write in _attribute_paths(owner, stage, objects))
    for action, slot in _constraint_actions(owner):
        for curve in _curves(action, slot):
            yield owner, curve.data_path, True, None
    animation = owner.animation_data
    if animation is None:
        return
    for action, slot in _actions(animation):
        for curve in _curves(action, slot):
            yield owner, curve.data_path, True, None
    for curve in animation.drivers:
        yield owner, curve.data_path, True, curve
        for variable in curve.driver.variables:
            for target in variable.targets:
                path = target.data_path
                if not path:
                    continue
                if variable.type == 'CONTEXT_PROP':
                    start = stage.view_layer if target.context_property == 'ACTIVE_VIEW_LAYER' else stage.scene
                else:
                    start = target.id
                if start is not None:
                    yield start, path, False, None


def check(stage, router, blocks, walk=True):
    """Refuse (UNSUPPORTED) before an evaluation when a path crosses data its use may not reach (module docstring);
    returns the live IDs read paths reach, for ``router.allow_live``."""
    paths, shared, structs = Paths(), [], {}
    animated = _animated(blocks)
    owned = set(router.owned) | {block.as_pointer() for block in animated}  # With their embedded trees and keys.
    objects = [block for block in animated if isinstance(block, _bpy().types.Object)]
    for owner in animated:
        for start, path, write, curve in _paths(owner, stage, objects):
            if curve is not None:
                reason = _driver_problem(curve)   # A scripted or context driver runs Python, whoever owns it.
                if reason is not None:
                    raise ProgramError(DRIVER_REFUSAL.format(path=label(curve.data_path, 256), reason=reason),
                                       code='UNSUPPORTED')
            if not path:
                continue
            # Resolving even with coerce=False can invoke pointer getters or array-index reads.
            # Inspect each property's metadata before resolving that step or any later prefix.
            if _runs_python(start, path, structs):
                raise ProgramError(PYTHON_REFUSAL.format(path=label(path, 256)), code='UNSUPPORTED')
            crossed = paths.crossed(start, path)
            if crossed is None:
                raise ProgramError(UNPARSED_REFUSAL.format(path=label(path, 256)), code='UNSUPPORTED')
            for block in crossed:
                pointer = block.as_pointer()
                if pointer in owned or (not write and pointer in router.allowed):
                    continue   # A borrowed or live closure's own data (a still widget's mesh) reads in place.
                text = WRITE_REFUSAL if write else READ_REFUSAL
                refusal = text.format(path=label(path, 256), kind=_kind(block), name=label(block.name))
                if write:
                    raise ProgramError(refusal, code='UNSUPPORTED')
                route, _reason = router.route(block, walk)
                if route == "live":
                    if walk:
                        router.allow_live([block])   # Now: the path's next step may be its own (embedded) data.
                    else:
                        shared.append(block)
                elif route != "borrowed":
                    raise ProgramError(refusal, code='UNSUPPORTED')
    return shared


def _driver_problem(curve):
    """Why Blender would not evaluate ``curve``'s driver natively (driver_rules, the one rule), or None."""
    from .driver_rules import problem
    return problem(curve)


def _runs_python(start, path, memo):
    """Whether a step of ``path`` from ``start`` is a Python-defined property (``is_runtime``) with a getter or
    setter (``python_callbacks``), which Blender's evaluation would call. Stored ones (Cycles' settings, an add-on's
    plain values) and custom properties (``["name"]``) are data: nothing runs."""
    steps = cuts(path)
    begin = 0
    for cut in steps or ():
        token = path[begin:cut].lstrip(".")
        holder = start if begin == 0 else _struct(start, path[:begin], memo)
        begin = cut
        if not token or token.startswith("[") or holder is None:
            continue
        rna = getattr(holder, "bl_rna", None)
        prop = rna.properties.get(token) if rna is not None else None
        if prop is not None and getattr(prop, "is_runtime", False):
            if python_callbacks(rna, token):
                return True
            # A named lookup runs the collection item's RNA nameproperty getter implicitly.
            # A Python-defined collection does not prove that lookup callback-free merely
            # because the collection itself has no get/set. Refuse before resolving its key.
            if getattr(prop, "type", None) == "COLLECTION" and path[cut:].startswith('["'):
                return True
    return False


def python_callbacks(rna, name):
    """Whether the Python-defined property ``name`` of RNA type ``rna`` has a getter, setter or value-transform callback: read from the
    ``bpy.props`` definition its class keeps (a class annotation, or the deferred property a type was given);
    True when that definition cannot be found (it is not proven stored)."""
    owner = rna
    while owner is not None:
        cls = getattr(getattr(_bpy(), "types", None), owner.identifier, None)
        definition = (getattr(cls, "__annotations__", None) or {}).get(name) if cls is not None else None
        if definition is None and cls is not None:
            definition = cls.__dict__.get(name) if hasattr(cls, "__dict__") else None
        keywords = getattr(definition, "keywords", None)
        if isinstance(keywords, dict):
            return any(keywords.get(key) for key in ("get", "set", "get_transform", "set_transform"))
        owner = getattr(owner, "base", None)
    return True


def _struct(start, prefix, memo):
    key = (start.as_pointer(), prefix)
    if key not in memo:
        try:
            value = start.path_resolve(prefix)
        except (ValueError, TypeError, AttributeError):
            value = None
        memo[key] = value if hasattr(value, "bl_rna") else None
    return memo[key]


def _kind(block):
    from .stage_routes import _kind
    return _kind(block)
