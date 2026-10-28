# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which parts a per-placement material swap can be proven on (``worker_materials``): an allow-list, so whatever
Blender can make depend on what is not proven safe and refused by name.

Six break-it rounds (P2 M1 through P2R6) found one more way each time for a swap to reach what another placement
shows, or for renders to show other than the viewport checked: instancing, modifier and node references, render-only
branches, Material inputs, realized Object Info, render levels, drivers on node values and on modifier switches,
driver variables, driven particles. Blender's ways to make one thing depend on another are open-ended, so a swap is
accepted only when every object of its part is of a known-safe kind (``unsafe``):

* a mesh, or an empty, light or camera (which show no material), not linked from a library;
* instancing nothing (no collection, vertices or faces) and with no particle systems;
* modifiers only of types that keep the material list and reach no other datablock (``MODIFIERS``: their own options
  only, from Blender's modifier semantics), each with no datablock pointer set;
* parent and constraint targets only within its own placement, and no other datablock a constraint points at;
* no drivers anywhere on the object, its data (a light's node tree included), its shape keys, its materials or their
  node trees (nested groups included), and no keys but on the object's own transform (keys hold values per frame,
  the same in viewports and renders, and a moved object shows the same materials);
* every material a swap swaps to or from meets the same rules (``destination``; break-it P2R7 H-1);
* nothing in the whole file can read what the swap changes (``reader``; P2R7 H-2): no driver with a variable on a
  datablock the merge added, a Single Property path beyond a plain property of its own owner, a Context Property, or a
  Python expression.
* nothing that was in the file before uses the part's objects, data or materials, or any collection or scene holding
  them up to each root collection (``incoming``, Blender's user map; break-it P2R9 H-1), and no scope-wide material
  reader exists (``scoped``: Line Art, Freestyle; P2R8 H-1), and the scene that shows the part renders each slot's
  material the standard way (``render_unsafe``, an allow-list: engine, view layers, compositor; P2R10, P2R11).

Then nothing reaches anything else: a swap's object-linked slot override shows on its own copy alone, and the material
list is the data's in every mode, so a slot swapped by index is the requested material in viewports and renders
alike. ``rendered`` lists what Blender renders, so a copy the viewport does not evaluate is known to show anyway.
"""

import re

SHOWN = frozenset({"MESH"})                       # Geometry whose material list is its data's slots.
INERT = frozenset({"EMPTY", "LIGHT", "CAMERA"})   # Objects that show no material.
# Modifiers that keep the material list (they may move faces between slots, never change what a slot holds) and use
# no other datablock: Blender's own geometry operations on the mesh they are given.
MODIFIERS = frozenset({"ARRAY", "BEVEL", "DECIMATE", "EDGE_SPLIT", "LAPLACIANSMOOTH", "MIRROR", "REMESH", "SMOOTH",
                       "SOLIDIFY", "SUBSURF", "TRIANGULATE", "WEIGHTED_NORMAL", "WELD", "WIREFRAME"})


# The only keys allowed: the object's own transform. A key holds one value per frame, the same in viewports and
# renders, and moving an object never changes which material it shows (any other key, and any driver, refuses).
MOVES = frozenset({"location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
                   "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale"})


def _keys(action):
    """Every data path ``action`` keys, in any slot, muted channels included (a person can unmute them)."""
    if action is None:
        return set()
    found = {curve.data_path for curve in getattr(action, "fcurves", ())}
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                found.update(curve.data_path for curve in bag.fcurves)
    return found


def _strips(strips):
    for strip in strips:
        yield strip
        yield from _strips(getattr(strip, "strips", ()))


def animated(block, allowed=frozenset(), seen=None):
    """What animates ``block`` (any datablock) beyond keys on the ``allowed`` paths, as words, or None: any driver (a
    driver variable can read any object, an expression the render mode; break-it P2R6), any other key in its action or
    NLA strips; for a node tree, the same of every group nested in it."""
    data = getattr(block, "animation_data", None) if block is not None else None
    if data is not None:
        if len(data.drivers):
            return "drivers"
        keyed = _keys(data.action) | {path for track in data.nla_tracks for strip in _strips(track.strips)
                                      for path in _keys(getattr(strip, "action", None))}
        if keyed - allowed:
            return "keys on %s" % min(keyed - allowed)
    seen = set() if seen is None else seen
    for node in getattr(block, "nodes", ()):
        inner = getattr(node, "node_tree", None)
        if inner is not None and inner.as_pointer() not in seen:
            seen.add(inner.as_pointer())
            found = animated(inner, frozenset(), seen)
            if found:
                return found
    return None


def _pointers(item, memo):
    """Every datablock ``item`` (a modifier or constraint) points at: its pointer properties holding one."""
    import bpy
    key = ("pointers", item.bl_rna.identifier)
    if key not in memo:
        memo[key] = [prop.identifier for prop in item.bl_rna.properties
                     if prop.type == "POINTER" and prop.identifier != "rna_type"]
    return [value for value in (getattr(item, name, None) for name in memo[key]) if isinstance(value, bpy.types.ID)]


def unsafe(obj, placement, memo):
    """The first thing that keeps a swap on ``obj`` (a copy; ``placement``: its placement's copies) from being proven,
    as words for a refusal ("<object> has ..."), or None when ``obj`` is of a known-safe kind."""
    data = obj.data
    if obj.library is not None or obj.override_library is not None or getattr(data, "library", None) is not None:
        return "data linked from a library"
    if obj.type not in SHOWN | INERT:
        return "the object type %s" % obj.type
    if obj.instance_type != "NONE" or obj.instance_collection is not None:
        return "instancing (%s)" % obj.instance_type.lower()
    if len(obj.particle_systems):
        return "particle system %r" % obj.particle_systems[0].name
    for what, block, allowed in (("its object", obj, MOVES), ("its data", data, ()),
                                 ("its shape keys", getattr(data, "shape_keys", None), ()),
                                 ("its data's node tree", getattr(data, "node_tree", None), ())):
        found = animated(block, frozenset(allowed))
        if found:
            return "%s on %s" % (found, what)
    for slot in obj.material_slots:
        material = slot.material
        if material is not None and material.library is not None:
            return "material %r, linked from a library" % material.name
        found = material is not None and (animated(material) or animated(getattr(material, "node_tree", None)))
        if found:
            return "%s on material %r" % (found, material.name)
    for modifier in obj.modifiers:
        if modifier.type not in MODIFIERS:
            return "modifier %r (%s), which is not of a kind known to keep its material list alone" % (
                modifier.name, modifier.type.replace("_", " ").title())
        for found in _pointers(modifier, memo):
            return "modifier %r, which uses %s %r" % (modifier.name, type(found).__name__.lower(), found.name)
    if obj.parent is not None and obj.parent not in placement:
        return "a parent outside its placement"
    for constraint in obj.constraints:
        for found in _pointers(constraint, memo):
            if found not in placement:
                return "constraint %r, which uses %s %r outside its placement" % (
                    constraint.name, type(found).__name__.lower(), found.name)
    return None


def destination(material):
    """Why a material a swap puts on a copy (or swaps away) keeps the swap from being proven, as words, or None: the
    same rules as a slot's material, since after the swap it is one (break-it P2R7 H-1: an unused material with a
    driver reading another placement's object was a destination no check read)."""
    if material.library is not None:
        return "material %r, linked from a library" % material.name
    found = animated(material) or animated(getattr(material, "node_tree", None))
    return "%s on material %r" % (found, material.name) if found else None


SIMPLE_PATH = re.compile(r'^(?:[A-Za-z_]\w*|\["[^"\\]*"\])(?:\[\d+\])?$')   # A property of the owner itself.


def reader(added):
    """The first driver in the whole file that can read what a swap changes, as (its owner, words), or None (break-it
    P2R7 H-2: a driver already in the scene read the incoming object by path through the Scene). Counted: any
    variable on a datablock the merge added (``added``: their pointers); any Single Property variable reading
    anything but a plain property of the driver's own owner (a path can walk to any object: ``objects["Body"]``,
    ``parent.active_material``); any Context Property variable; any Python expression (``use_self``, or one Blender
    cannot evaluate without Python). Not counted: transform, distance and rotation variables, which read where
    objects are, never their materials, and simple expressions over the variables."""
    import bpy
    from .worker_material_check import _kinds
    for kind in _kinds(bpy):
        for block in getattr(bpy.data, kind):
            for owner in (block, getattr(block, "node_tree", None)):
                data = getattr(owner, "animation_data", None) if owner is not None else None
                for curve in (data.drivers if data is not None else ()):
                    why = _reads(curve.driver, owner, added)
                    if why is not None:
                        return owner, "a driver on %s with %s" % (curve.data_path, why)
    return None


def _reads(driver, owner, added):
    if driver.type == "SCRIPTED" and (driver.use_self or not driver.is_simple_expression):
        return "a Python expression"
    for variable in driver.variables:
        for target in variable.targets:
            found = target.id
            if found is not None and found.as_pointer() in added:
                return "variable %r reading %s %r of this merge" % (variable.name, type(found).__name__.lower(),
                                                                    found.name)
        if variable.type == "CONTEXT_PROP":
            return "variable %r reading the context by path" % variable.name
        if variable.type == "SINGLE_PROP":
            target = variable.targets[0]
            if target.id != owner or not SIMPLE_PATH.match(target.data_path or ""):
                return "variable %r reading %r by path" % (variable.name, target.data_path)
    return None


def scoped():
    """The first thing in the file that reads a whole scene or collection by scope, materials included, rather than by
    a reference to the incoming objects, as (its owner, words), or None (break-it P2R8 H-1: a scene's Line Art drew
    other strokes once an incoming mesh's material changed). Counted: any Line Art modifier (its source can be the
    scene, a collection or an object, and its material masks and occlusion read each mesh's materials), and Freestyle
    on in a scene (its line sets select by scope, and its line styles can read materials). These are the scope-wide
    material readers Blender has; a reader by reference is ``incoming``'s."""
    import bpy
    for obj in bpy.data.objects:
        for modifier in list(obj.modifiers) + list(getattr(obj, "grease_pencil_modifiers", ())):
            if modifier.type in ("LINEART", "GP_LINEART"):
                return obj, "Line Art modifier %r, which reads the scene or a collection with its materials" % (
                    modifier.name)
    for scene in bpy.data.scenes:
        if scene.render.use_freestyle and any(layer.use_freestyle for layer in scene.view_layers):
            return scene, "Freestyle on, which reads the scene by scope and can read materials"
    return None


def incoming(blocks, added):
    """The first datablock that was in the file before the merge (not in ``added``) and uses one of ``blocks`` (the
    swapping part's objects, their data and materials) or anything that holds them, by Blender's own user map walked
    up the containment chain, as (it, what it uses), or None (break-it P2R9 H-1: an instancer in another scene showed
    the lead scene's root collection, so the incoming mesh and its swap). A collection or scene holding a block is a
    container, not a consumer: its own users are read in turn, a scene's through its root collection and itself, where
    another scene using it (a background set, a scene strip) is a consumer. The window manager, workspaces and screens
    show scenes, never materials; anything else that was there before refuses, and what the merge added is followed
    too (a mesh's other objects). Blender's user map is read once per merge."""
    import bpy
    shows = (bpy.types.WindowManager, bpy.types.WorkSpace, bpy.types.Screen)
    every = bpy.data.user_map()   # Once per merge, walked here (embedded root collections included).
    seen, frontier = set(), set(blocks)
    while frontier:
        seen |= frontier
        found = set()
        for block in frontier:
            for user in every.get(block, ()):
                if isinstance(user, shows) or user == block:   # A scene lists itself among its users.
                    continue
                # Its own compositor, used by no other datablock, is ``render_unsafe``'s (break-it P2R12 H-1: a tree
                # shared with another scene read this one's render there).
                if user == getattr(block, "compositing_node_group", None) and not any(
                        other != block and not isinstance(other, shows) for other in every.get(user, ())):
                    continue
                if isinstance(user, bpy.types.Scene) and not isinstance(block, bpy.types.Scene):
                    found.update((user, user.collection))   # Its root collection is embedded: read on its own.
                elif isinstance(user, bpy.types.Collection) or user.as_pointer() in added:
                    found.add(user)
                else:
                    return user, block
        frontier = found - seen
    return None


PASSES = (("use_pass_material_index", "the Material Index pass"),
          ("use_pass_cryptomatte_material", "a Cryptomatte material pass"),
          ("use_pass_cryptomatte_object", "a Cryptomatte object pass"),
          ("use_pass_cryptomatte_asset", "a Cryptomatte asset pass"))
ENGINES = frozenset({"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "CYCLES"})   # They render each slot's material.
# Compositor nodes whose output pixel depends only on the same pixel of their inputs (or on nothing): a swap changes
# only the pixels where the part shows. Anything spatial or image-wide (blur, glare, denoise, normalize, levels,
# tonemap), anything reading identity (ID Mask, Cryptomatte) or other images refuses (break-it P2R11 H-1: Normalize
# carried an incoming mesh's brightness into every pixel).
COMPOSITOR = frozenset({
    "CompositorNodeRLayers", "CompositorNodeComposite", "CompositorNodeViewer", "NodeGroupInput", "NodeGroupOutput",
    "NodeReroute", "NodeFrame", "CompositorNodeGroup", "CompositorNodeBrightContrast", "CompositorNodeColorBalance",
    "CompositorNodeColorCorrection", "CompositorNodeCurveRGB", "CompositorNodeExposure", "CompositorNodeGamma",
    "CompositorNodeHueCorrect", "CompositorNodeHueSat", "CompositorNodeInvert", "CompositorNodeMixRGB",
    "CompositorNodeRGBToBW", "CompositorNodeAlphaOver", "CompositorNodeSetAlpha", "CompositorNodeRGB",
    "CompositorNodeValue", "CompositorNodeMath", "CompositorNodeSeparateColor", "CompositorNodeCombineColor",
    "ShaderNodeMix", "ShaderNodeMath", "ShaderNodeSeparateColor", "ShaderNodeCombineColor", "ShaderNodeRGBCurve",
    "ShaderNodeValToRGB", "ShaderNodeClamp", "ShaderNodeMapRange", "ShaderNodeHueSaturation", "ShaderNodeGamma",
    "ShaderNodeInvert", "ShaderNodeBrightContrast", "ShaderNodeRGBToBW"})


def render_unsafe(scene):
    """What keeps renders of ``scene`` from showing each slot's material the standard way, as words, or None: an
    allow-list (break-it P2R10, P2R11: an override, identity passes, Workbench's Single color and image-wide
    compositing each made a verified swap show otherwise, one setting at a time). Allowed: no Sequencer strips to
    render through (P2R12 M-1); EEVEE or Cycles, or Workbench colouring by material; no view layer material
    override, Material Index or Cryptomatte pass, or shader AOV; the compositor off, or holding only nodes of
    ``COMPOSITOR`` (nested groups included)."""
    editor = getattr(scene, "sequence_editor", None)
    strips = getattr(editor, "strips_all", None) if editor is not None else None
    strips = getattr(editor, "sequences_all", ()) if strips is None and editor is not None else strips
    if scene.render.use_sequencer and strips is not None and len(strips):   # It replaces the render (P2R12 M-1).
        return "the Sequencer with strips (%s)" % strips[0].name
    engine = scene.render.engine
    if engine == "BLENDER_WORKBENCH":
        if scene.display.shading.color_type != "MATERIAL":
            return "Workbench coloured by %s" % scene.display.shading.color_type.lower()
    elif engine not in ENGINES:
        return "the render engine %s" % engine
    for layer in scene.view_layers:
        if layer.material_override is not None:
            return "view layer %r's material override (%s)" % (layer.name, layer.material_override.name)
        for name, words in PASSES:
            if getattr(layer, name, False):
                return "%s in view layer %r" % (words, layer.name)
        if len(getattr(layer, "aovs", ())):
            return "shader AOVs in view layer %r" % layer.name
    tree = getattr(scene, "compositing_node_group", None) or getattr(scene, "node_tree", None)
    on = getattr(scene.render, "use_compositing", True) and (getattr(scene, "use_nodes", True) or
                                                            getattr(scene, "compositing_node_group", None))
    stack, seen = [tree] if tree is not None and on else [], set()
    while stack:
        found = stack.pop()
        if found.as_pointer() in seen:
            continue
        seen.add(found.as_pointer())
        for node in found.nodes:
            if node.bl_idname not in COMPOSITOR:
                return "the compositor's %s node %r" % (node.bl_label, node.name)
            if getattr(node, "node_tree", None) is not None:
                stack.append(node.node_tree)
    return None


def rendered(view_layer):
    """The pointers of every object Blender renders in ``view_layer`` (``renders``), in one walk."""
    found, stack = set(), [view_layer.layer_collection]
    while stack:
        layer = stack.pop()
        if layer.exclude or layer.collection.hide_render:
            continue
        found.update(obj.as_pointer() for obj in layer.collection.objects if not obj.hide_render)
        stack.extend(layer.children)
    return found
