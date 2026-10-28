# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A placement's material swaps (``worker_merge``): every place a part's material reaches a placed copy's evaluated
result, the swap there for that copy alone, and the evaluated check that it took.

Only slot swaps run now: ``worker_material_reach.unsafe`` refuses every part with Geometry Nodes or a modifier that
holds a material before anything is swapped, so the node-group and modifier-material paths below are dormant,
unreachable until a render-mode proof exists (kept for 1.1).

A swap turns a material of the part (by datablock: two parts' materials of one name stay two, break-it E2E-1 M-1) into
another material or a variant, for one placement's copies. Where a material reaches what a copy shows, and the swap:

* a **material slot**, of any object type (mesh, curve, text, grease pencil, curves, point cloud, volume): an
  object-linked slot on the copy. A slot is matched by what Blender evaluates in it, never by its data's material:
  Geometry Nodes can add and reorder materials (a Join puts another one at the data's index), and a slot override is
  applied by index;
* a modifier's own material that it **sets** (a Grease Pencil Outline's, a Line Art target): set on the copy's modifier;
* **Geometry Nodes**: a Material input of the modifier, a Set Material node, Replace Material's New material, a
  Material node, in the modifier's node group or a group nested in it (through reroutes, switches and group inputs).
  The copy's modifier gets its own copy of each node group on the way, made once per merge for each distinct set of
  swaps and shared by every placement making the same swaps; the worker's own node groups never change.

Refused before anything is swapped, naming the object, modifier, node group and node (a modifier off in viewports
and renders reads nothing; break-it P2R3 M-1): a node or modifier that **reads**
a material the placement swaps or swaps to (Material Selection, Replace Material's Old, a modifier's material filter,
a value that leaves its node group or enters a node that is not one above), since the swap would change what it finds;
and a copy that instances a collection or objects the placement does not own while those carry a swapped material
that their own placement swaps otherwise (or not at all).

Then each copy a swap concerns is evaluated and checked element by element, its instances included, and a placement
that swaps nothing must not reach what another placement's swap changed (``worker_material_swaps``, break-it P2-3 and
P2); what the check cannot read is refused by name. A refusal after the parts were appended removes every datablock
the merge added (``added_since``): nothing was merged.
"""

ENGINES = {"EEVEE": "EEVEE", "CYCLES": "Cycles"}
ASSIGN = frozenset({("GeometryNodeSetMaterial", "Material"), ("GeometryNodeReplaceMaterial", "New")})
PASS = frozenset({"NodeReroute", "GeometryNodeSwitch", "GeometryNodeIndexSwitch", "GeometryNodeMenuSwitch"})
MODIFIER_SETS = frozenset({"outline_material", "target_material"})   # Modifier pointers that set a material.
DEPTH = 32   # Node groups nested deeper, or instances, are taken as reads (refused when a swap is involved).


def key(value):
    return value if isinstance(value, str) else tuple(sorted((k, tuple(v) if isinstance(v, list) else v)
                                                             for k, v in value.items()))


def check_names(part, names, number):
    """Before anything is appended: every swap names a material of the part, and every target exists."""
    import bpy
    from .refusal_codes import RefusalError
    for placement in part["placements"]:
        for source, value in placement["materials"].items():
            error = (RefusalError("Worker %d's part has no material %s; nothing was merged" % (number, source),
                                  code="VALIDATION") if source not in names
                     else RefusalError("No material %s in this scene or worker %d's part; nothing was merged"
                                       % (value, number), code="VALIDATION")
                     if isinstance(value, str) and value not in names and bpy.data.materials.get(value) is None
                     else None)
            if error is not None:
                error.partial_changes_possible = False   # Nothing was appended: the scene is as it was.
                raise error


def mapping(swaps, materials, variants, made):
    """{part material datablock: the material it becomes} for one placement (``check_names`` passed). A variant is
    made once per merge per part material and values, and reused by every placement naming it."""
    import bpy
    found = {}
    for source, value in swaps.items():
        if isinstance(value, str):
            found[materials[source]] = materials.get(value) or bpy.data.materials[value]
            continue
        made_key = (materials[source], key(value))
        if made_key not in variants:
            variants[made_key], ignored = _variant(materials[source], value)
            made.append({"name": value["name"], "material": variants[made_key].name, "from": source,
                         **({"not_applied": ignored} if ignored else {})})
        found[materials[source]] = variants[made_key]
    return found


def _rendered(tree, target):
    """The Principled BSDF nodes the output ``target`` (EEVEE or CYCLES) renders reaches, through the nodes between: a
    Mix Shader's inputs its unlinked factor weighs, a muted node's pass-through only (break-it E2E-5 L-2, E2E-7 L-5)."""
    output = tree.get_output_node(target)
    found, seen, stack = [], set(), [output.inputs["Surface"]] if output is not None else []
    while stack:
        for link in stack.pop().links:
            node = link.from_node
            if link.is_muted or node.as_pointer() in seen:
                continue
            seen.add(node.as_pointer())
            if node.mute:
                stack.extend(inner.from_socket for inner in node.internal_links if inner.from_socket.is_linked)
            elif node.type == "BSDF_PRINCIPLED":
                found.append(node)
            elif node.type == "MIX_SHADER" and not node.inputs[0].is_linked:   # Only the shaders its factor weighs.
                factor = node.inputs[0].default_value
                stack.extend(socket for socket, weight in ((node.inputs[1], 1 - factor), (node.inputs[2], factor))
                             if weight > 0 and socket.is_linked)
            else:
                stack.extend(socket for socket in node.inputs if socket.is_linked)
    return found


def _driven(socket, depth=0):
    """Another node sets this input: a link that is neither muted nor invalid, from a node that is not a dangling
    reroute or a muted node passing nothing on (break-it E2E-8 L-4, E2E-9 L-7)."""
    for link in socket.links:
        if link.is_muted or not link.is_valid or depth > 32:
            continue
        node = link.from_node
        if node.type == "REROUTE":
            if _driven(node.inputs[0], depth + 1):
                return True
        elif node.mute:
            if any(inner.to_socket == link.from_socket and _driven(inner.from_socket, depth + 1)
                   for inner in node.internal_links):
                return True
        else:
            return True
    return False


def _variant(base, spec):
    """A copy of ``base`` with ``spec``'s values on the Principled shaders each engine renders, and the values an
    engine's shaders do not read: an input another node drives ignores its own value, so that is reported, never
    claimed (E2E-1 L-1): "roughness" when no engine reads it, "roughness (Cycles)" when that engine does not."""
    material = base.copy()
    material.name = spec["name"]
    nodes = {engine: _rendered(material.node_tree, engine) if material.node_tree else [] for engine in ENGINES}
    ignored = []
    for name, socket in (("base_color", "Base Color"), ("roughness", "Roughness"), ("metallic", "Metallic")):
        if name in spec:
            for node in {node.as_pointer(): node for found in nodes.values() for node in found}.values():
                node.inputs[socket].default_value = spec[name]
            missed = [label for engine, label in ENGINES.items()   # None rendered, or each driven elsewhere.
                      if all(_driven(node.inputs[socket]) for node in nodes[engine])]
            ignored += [name] if len(missed) == len(ENGINES) else ["%s (%s)" % (name, label) for label in missed]
            setattr(material, {"base_color": "diffuse_color", "roughness": "roughness", "metallic": "metallic"}[name],
                    spec[name])
    return material, ignored


# Where a material value goes in a node group: "set" (Set Material, Replace Material's New), "read" (anything else).
def _from_output(tree, socket, depth):
    kinds = set()
    for link in socket.links:
        if link.is_valid and not link.is_muted:
            kinds |= _into(tree, link.to_node, link.to_socket, depth)
    return kinds


def _into(tree, node, socket, depth):
    if depth > DEPTH:
        return {"read"}
    if node.mute:   # A muted node passes on only what its internal links carry.
        return set().union(*(_from_output(tree, inner.to_socket, depth + 1) for inner in node.internal_links
                             if inner.from_socket == socket))
    if (node.bl_idname, socket.identifier) in ASSIGN:
        return {"set"}
    if node.bl_idname in PASS:
        return set().union(*(_from_output(tree, out, depth + 1) for out in node.outputs))
    if node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None:
        return set().union(*(_from_output(node.node_tree, out, depth + 1) for inner in node.node_tree.nodes
                             if inner.bl_idname == "NodeGroupInput" for out in inner.outputs
                             if out.identifier == socket.identifier))
    return {"read"}


def _origins(tree, memo):
    """[(node name, socket identifier or "", material, kinds)]: each material value ``tree``'s own nodes give (an
    input's own value where nothing drives it, a Material node's), and where it goes."""
    if tree.as_pointer() not in memo:
        found = memo[tree.as_pointer()] = []
        for node in tree.nodes:
            if node.bl_idname == "GeometryNodeInputMaterial" and node.material is not None:
                found.append((node.name, "", node.material, _from_output(tree, node.outputs[0], 0)))
            for socket in node.inputs:
                if socket.type == "MATERIAL" and socket.default_value is not None and not _driven(socket):
                    found.append((node.name, socket.identifier, socket.default_value, _into(tree, node, socket, 0)))
    return memo[tree.as_pointer()]


def _trees(tree, path, seen=None):
    """``(tree, path)`` for ``tree`` and every group nested in it, each once."""
    seen = set() if seen is None else seen
    if tree.as_pointer() in seen or len(path) > DEPTH:
        return
    seen.add(tree.as_pointer())
    yield tree, path
    for node in tree.nodes:
        if node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None:
            yield from _trees(node.node_tree, path + [node.node_tree.name], seen)


def _inputs(modifier):
    """[(identifier, material, kinds)] of a Geometry Nodes modifier's own Material inputs."""
    tree, found = modifier.node_group, []
    for item in tree.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.socket_type == "NodeSocketMaterial":
            try:
                value = modifier.properties.inputs[item.identifier]["value"]
            except (KeyError, TypeError, AttributeError):
                continue
            outputs = [out for node in tree.nodes if node.bl_idname == "NodeGroupInput" for out in node.outputs
                       if out.identifier == item.identifier]
            found.append((item.identifier, value, set().union(*(_from_output(tree, out, 0) for out in outputs))))
    return found


def refusal(objects, owned, swap, memo, name=lambda item: item.name, swap_of=lambda obj: {}):
    """Why ``swap`` ({material: material}) cannot be made on ``objects`` (one placement's copies; ``owned``: every
    object of that placement) for that placement alone, or None. ``name``: what the model calls a datablock.
    ``swap_of(obj)``: the swap of the placement that owns ``obj`` (an instance of it shows that one; break-it P2 M5:
    a swap every placement makes was refused)."""
    sources, targets = set(swap), set(swap.values())
    involved, groups = sources | targets, {}
    for obj in objects:
        for modifier in obj.modifiers:   # Unreachable until a render-mode proof exists (``unsafe`` refuses first).
            if not (modifier.show_viewport or modifier.show_render):
                continue   # Off in both: it reads nothing anything shows (break-it P2R3 M-1).
            where = "modifier %r of %s" % (modifier.name, name(obj))
            if modifier.type == "NODES" and modifier.node_group is not None:
                for identifier, material, kinds in _inputs(modifier):
                    if material in involved and "read" in kinds:
                        return "its input %s (%s) reads %s" % (identifier, where, name(material))
                for tree, path in _trees(modifier.node_group, [modifier.node_group.name]):
                    for node, socket, material, kinds in _origins(tree, memo):
                        if material in involved and "read" in kinds:
                            return "node %r in node group %s (%s) reads %s" % (
                                node, " > ".join(repr(group) for group in path), where, name(material))
                continue
            for prop in modifier.bl_rna.properties:
                if (prop.type == "POINTER" and getattr(prop, "fixed_type", None) is not None
                        and prop.fixed_type.identifier == "Material" and prop.identifier not in MODIFIER_SETS
                        and getattr(modifier, prop.identifier) in involved):
                    return "its %s (%s) reads %s" % (prop.identifier, where, name(getattr(modifier, prop.identifier)))
        collection = obj.instance_collection if obj.instance_type == "COLLECTION" else None
        if collection is not None:   # Its members judged once per collection: many objects instance one.
            key = collection.as_pointer()
            if key not in groups:
                groups[key] = _foreign(collection.all_objects, owned, swap, sources, swap_of)
            if groups[key] is not None:
                return "%s instances %s (collection %r), which this placement does not own and its own placement " \
                       "swaps otherwise" % (name(obj), name(groups[key]), collection.name)
        from .worker_material_check import instanced
        for how, others in instanced(obj, collections=False):
            other = _foreign(others, owned, swap, sources, swap_of)
            if other is not None:
                return "%s instances %s (%s), which this placement does not own and its own placement swaps " \
                       "otherwise" % (name(obj), name(other), how)
    return None


def _foreign(objects, owned, swap, sources, swap_of):
    """The first of ``objects`` outside ``owned`` whose slots hold a swapped material its own placement swaps
    otherwise (or not at all), or None."""
    return next((other for other in objects if other not in owned and any(
        slot.material in sources and swap_of(other).get(slot.material) is not swap[slot.material]
        for slot in other.material_slots)), None)



def _needs(tree, sources, memo, needs):
    """Whether ``tree`` or a group nested in it sets one of ``sources``."""
    pointer = tree.as_pointer()
    if pointer not in needs:
        needs[pointer] = False   # A group loop never recurses forever.
        needs[pointer] = (any(material in sources and "set" in kinds
                              for _node, _socket, material, kinds in _origins(tree, memo))
                          or any(node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None
                                 and _needs(node.node_tree, sources, memo, needs) for node in tree.nodes))
    return needs[pointer]


def _tree_copy(tree, swap, memo, state):
    """``tree`` for ``swap``: itself when nothing in it sets a swapped material, else its copy (one per merge per tree
    and swap) with those values swapped and its nested groups so copied."""
    sources = set(swap)
    swap_key = tuple(sorted((a.as_pointer(), b.as_pointer()) for a, b in swap.items()))
    needs = state.setdefault(("needs", swap_key), {})
    if not _needs(tree, sources, memo, needs):
        return tree
    copies = state.setdefault("copies", {})
    if (tree.as_pointer(), swap_key) not in copies:
        copy = copies[(tree.as_pointer(), swap_key)] = tree.copy()
        state.setdefault("made", []).append(copy)
        for name, identifier, material, kinds in _origins(tree, memo):
            if material in sources and "set" in kinds:
                node = copy.nodes[name]
                if identifier:
                    next(s for s in node.inputs if s.identifier == identifier).default_value = swap[material]
                else:
                    node.material = swap[material]
        for node in copy.nodes:
            if node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None:
                node.node_tree = _tree_copy(node.node_tree, swap, memo, state)
    return copies[(tree.as_pointer(), swap_key)]


def apply(objects, swap, shown, memo, state):
    """``swap`` on ``objects`` (one placement's copies): slots by what each shows as built (``shown``: {object: the
    material evaluated in each of its slots}, absent where Blender did not evaluate it), modifiers' own, node groups."""
    for obj in objects:
        evaluated = shown.get(obj)
        for index, slot in enumerate(obj.material_slots):
            material = evaluated[index] if evaluated is not None and index < len(evaluated) else slot.material
            if material in swap:
                slot.link = "OBJECT"
                slot.material = swap[material]
        for modifier in obj.modifiers:   # Unreachable until a render-mode proof exists (``unsafe`` refuses first).
            if modifier.type == "NODES" and modifier.node_group is not None:
                for identifier, material, kinds in _inputs(modifier):
                    if material in swap and "set" in kinds:
                        modifier.properties.inputs[identifier]["value"] = swap[material]
                modifier.node_group = _tree_copy(modifier.node_group, swap, memo, state)   # Also tags the inputs set.
                continue
            for name in MODIFIER_SETS:
                if swap.get(getattr(modifier, name, None)) is not None:
                    setattr(modifier, name, swap[getattr(modifier, name)])


def evaluated_slots(objects, depsgraph):
    """{object: [the material Blender evaluates in each slot]} for the objects it evaluates."""
    found = {}
    for obj in objects:
        copy = obj.evaluated_get(depsgraph)
        if copy.as_pointer() != obj.as_pointer():
            found[obj] = [slot.material.original if slot.material is not None else None   # Not its evaluated copy.
                          for slot in copy.material_slots][:len(obj.material_slots)]
    return found


from .worker_material_swaps import SHOWN_UNCHECKED, Swaps  # noqa: E402,F401  (its own module, size limit)
