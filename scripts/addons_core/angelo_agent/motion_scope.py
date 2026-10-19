# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which objects a motion read covers (``motion_facts``): what animates, the top of each moving hierarchy, and the
hierarchy's rendering parts. Duck-typed reads; no Blender import.

``revision_time.still`` answers "can this not change with the frame" conservatively by type, which suits a
revision's frame guard and not this: a curve, a text, any Boolean or Displace modifier, any constraint and any node
group it cannot prove still count as moving there. Used here, a static road curve became the top of a keyed car's
group (its 200 m box stated instead of the car's) and an edit of a static wall paid a whole read. ``Scope`` asks
what can actually change an object in time, exactly on animation and conservatively only where a reference cannot be
followed:

* ``moves``: its world transform (or an armature's pose): keys, drivers or NLA on the object (an armature's pose
  keys live there too); an active rigid body; a constraint that reads time (a cache or motion track) or whose
  target animates; a bone constraint whose target animates; a parent that moves (a vertex parent's geometry, a
  path curve's evaluation, too);
* ``animates``: it moves, or its evaluated geometry changes: keys, drivers or NLA on its data or shape keys; a
  modifier that reads time (Wave, Build, caches, physics, emitter particles or hair dynamics); a modifier,
  constraint, data or node-group reference (any Object or Collection pointer, read from Blender's own property
  list) that animates; a Geometry Nodes group with a time, simulation, bake, image or camera node, or keys or
  drivers; a collection instance of animated objects.

Past ``MAX_SCAN`` objects for one question, ``MAX_NODES`` nodes or ``MAX_BONES`` bones a question answers True: the
read costs time, a miss costs the fact; ``top`` stops climbing at a parent whose motion is unknown. Cycles add no
motion of their own.
"""

from .object_bounds import GEOMETRY_TYPES

MAX_SCAN = 256  # Objects one question may visit.
MAX_NODES = 2048  # Nodes of one modifier's groups.
MAX_BONES = 1024
MAX_COLLECTION = 256  # Objects of one referenced collection followed.
MAX_MEMBERS = 4096  # Objects of one hierarchy read.
TIME_MODIFIERS = frozenset({"WAVE", "BUILD", "MESH_CACHE", "MESH_SEQUENCE_CACHE", "CLOTH", "SOFT_BODY", "FLUID",
                            "DYNAMIC_PAINT", "EXPLODE"})
TIME_CONSTRAINTS = frozenset({"TRANSFORM_CACHE", "FOLLOW_TRACK", "CAMERA_SOLVER", "OBJECT_SOLVER"})
TIME_NODES = frozenset({"GeometryNodeInputSceneTime", "GeometryNodeSimulationInput", "GeometryNodeSimulationOutput",
                        "GeometryNodeBake", "GeometryNodeImageTexture", "GeometryNodeInputImage",
                        "GeometryNodeImageInfo", "GeometryNodeInputActiveCamera"})
_POINTERS = {}  # RNA struct identifier -> names of its Object and Collection pointer properties


class Scope:
    """Remembered answers for one read (``moves``, ``animates``, ``top``). Each question asked from outside has its
    own ``MAX_SCAN`` (one budget for the whole edit ran out at about 130 objects, after which a
    still parent became the top and a static edit paid a read); an answer past it is unknown (``exhausted``) and never
    remembered."""

    def __init__(self):
        self.memo, self.visits, self.depth, self.exhausted = {}, 0, 0, False

    def moves(self, obj):
        return self._ask("moves", obj, self._moves)

    def animates(self, obj):
        """Whether ``obj`` animates; True when unknown (the read costs time, a miss costs the fact)."""
        return self._ask("animates", obj, self._animates)

    def top(self, obj):
        """The highest object of ``obj``'s parent chain that moves (or ``obj``): a keyed car under a still road
        curve or scene root is the car; a mesh on an animated rig's bone is the rig. A parent whose motion is
        unknown ends the climb."""
        top, seen = obj, {obj.as_pointer()}
        parent = obj.parent
        while parent is not None and parent.as_pointer() not in seen and len(seen) < 64:
            if not self.moves(parent) or self.exhausted:
                break
            seen.add(parent.as_pointer())
            top, parent = parent, parent.parent
        return top

    def _ask(self, kind, obj, test):
        key = (kind, obj.as_pointer())
        if key in self.memo:
            return self.memo[key]
        if self.depth == 0:
            self.visits, self.exhausted = 0, False
        self.visits += 1
        if self.visits > MAX_SCAN:
            self.exhausted = True
            return True
        self.memo[key] = False  # A cycle adds no motion of its own: each object's own checks decide.
        self.depth += 1
        try:
            found = bool(test(obj))
        except (AttributeError, TypeError, ValueError, KeyError, ReferenceError, RuntimeError):
            found = True  # Unreadable: counted as moving (the read states what it finds).
        finally:
            self.depth -= 1
        if self.exhausted:
            del self.memo[key]  # Unknown: asked again, never answered from memory.
        else:
            self.memo[key] = found
        return found

    def _moves(self, obj):
        if animated(obj) or getattr(getattr(obj, "rigid_body", None), "type", None) == "ACTIVE":
            return True
        parent = obj.parent
        if parent is not None:
            follows_shape = obj.parent_type in ("VERTEX", "VERTEX_3") or (
                parent.type == "CURVE" and getattr(parent.data, "use_path", False))
            if self.animates(parent) if follows_shape else self.moves(parent):
                return True
        if any(self._constraint_moves(constraint) for constraint in obj.constraints):
            return True
        if obj.type == "ARMATURE":
            if animated(obj.data):
                return True
            bones = getattr(obj.pose, "bones", ())
            if len(bones) > MAX_BONES:
                return any(len(bone.constraints) for bone in bones)
            return any(self._constraint_moves(c) for bone in bones for c in bone.constraints)
        return False

    def _constraint_moves(self, constraint):
        if not getattr(constraint, "enabled", True) or not constraint.influence:
            return False  # A keyed influence is the owner's animation.
        if constraint.type in TIME_CONSTRAINTS:
            return True
        targets = pointers(constraint) + [item.target for item in getattr(constraint, "targets", ()) or ()
                                          if getattr(item, "target", None) is not None]
        return any(self.animates(target) for target in targets)

    def _animates(self, obj):
        if self.moves(obj):
            return True
        data = obj.data
        if data is not None and (animated(data) or animated(getattr(data, "shape_keys", None))):
            return True
        if data is not None and any(self.animates(item) for item in pointers(data)):
            return True  # A curve's bevel or taper object.
        collection = getattr(obj, "instance_collection", None) if obj.instance_type == "COLLECTION" else None
        if collection is not None and any(self.animates(item) for item in collection.all_objects[:MAX_COLLECTION]):
            return True
        if obj.instance_type in ("VERTS", "FACES") and any(
                self.animates(child) for child in obj.children[:MAX_COLLECTION]):
            return True  # Its children, placed at each vertex or face.
        return any(self._modifier_animates(obj, modifier) for modifier in obj.modifiers if modifier.show_viewport)

    def _modifier_animates(self, obj, modifier):
        kind = modifier.type
        if kind in TIME_MODIFIERS:
            return True
        if kind == "PARTICLE_SYSTEM":
            system, settings = modifier.particle_system, modifier.particle_system.settings
            return (settings.type == "EMITTER" or getattr(system, "use_hair_dynamics", False) or animated(settings)
                    or any(self.animates(item) for item in pointers(settings)))  # What it instances.
        if animated(getattr(modifier, "texture", None)):
            return True
        if kind == "NODES" and self._nodes_animate(obj, modifier):
            return True
        return any(self.animates(item) for item in pointers(modifier))

    def _nodes_animate(self, obj, modifier):
        """A time, simulation, bake, image or camera node, keys or drivers in the modifier's groups, or an object
        its inputs or node sockets name that animates."""
        referenced, seen, budget, stack = [], set(), [MAX_NODES], [modifier.node_group]
        tree = modifier.node_group
        for item in (getattr(getattr(tree, "interface", None), "items_tree", ()) if tree is not None else ()):
            if getattr(item, "in_out", None) == "INPUT" and getattr(item, "socket_type", "") in (
                    "NodeSocketObject", "NodeSocketCollection"):
                referenced.append(modifier.get(item.identifier) if hasattr(modifier, "get") else None)
        while stack:
            tree = stack.pop()
            if tree is None or tree.as_pointer() in seen:
                continue
            seen.add(tree.as_pointer())
            if animated(tree):
                return True
            for node in tree.nodes:
                budget[0] -= 1
                if budget[0] < 0 or node.bl_idname in TIME_NODES:
                    return True
                if getattr(node, "node_tree", None) is not None:
                    stack.append(node.node_tree)
                for socket in node.inputs:
                    if socket.type in ("OBJECT", "COLLECTION") and not socket.is_linked:
                        referenced.append(getattr(socket, "default_value", None))
        for item in referenced:
            objects = list(item.all_objects[:MAX_COLLECTION]) if hasattr(item, "all_objects") else [item]
            if any(other is not None and other != obj and self.animates(other) for other in objects):
                return True
        return False


def animated(block):
    """Keys, drivers or NLA on ``block`` (``program_frames._animated``)."""
    animation = getattr(block, "animation_data", None) if block is not None else None
    return animation is not None and (animation.action is not None or len(animation.drivers) > 0
                                      or len(animation.nla_tracks) > 0)


def pointers(owner):
    """The objects ``owner``'s Object pointer properties name, and those of its Collection pointers (at most
    ``MAX_COLLECTION`` each), from Blender's property list (remembered per struct type)."""
    rna = getattr(owner, "bl_rna", None)
    if rna is None:
        return []
    names = _POINTERS.get(rna.identifier)
    if names is None:
        names = _POINTERS[rna.identifier] = tuple(
            prop.identifier for prop in rna.properties if prop.type == "POINTER"
            and getattr(getattr(prop, "fixed_type", None), "identifier", None) in ("Object", "Collection"))
    found = []
    for name in names:
        value = getattr(owner, name, None)
        if value is None:
            continue
        found.extend(value.all_objects[:MAX_COLLECTION] if hasattr(value, "all_objects") else [value])
    return found


def parts(view_layer, tops, include_self=False):
    """``{top pointer: {"parts", "unread", "more"}}``: each top's hierarchy objects that render (a rendering
    collection of the view layer, not hidden for render) and are no boolean operand, at most ``MAX_MEMBERS``
    (``more`` when there were more); ``unread`` the rendering ones the view layer does not evaluate (disabled in
    viewports), which no read can measure. Children come from one pass over the view layer's objects
    (``children_recursive`` scans every object per call: 500 rigs in a 20,000-object scene took seconds)."""
    rendering, operands, children = _rendering(view_layer)
    depsgraph, found = view_layer.depsgraph, {}
    for top in tops:
        members, queue, seen = [top], [top], {top.as_pointer()}
        while queue and len(members) <= MAX_MEMBERS:
            for child in children.get(queue.pop().as_pointer(), ()):
                if child.as_pointer() not in seen:
                    seen.add(child.as_pointer())
                    members.append(child)
                    queue.append(child)
        group = {"parts": [], "unread": [], "more": len(members) > MAX_MEMBERS}
        for member in members[:MAX_MEMBERS]:
            renders = (member.as_pointer() in rendering and member.as_pointer() not in operands
                       and not member.hide_render)
            if not renders and not (include_self and member == top):
                continue
            if member.evaluated_get(depsgraph).is_evaluated:
                group["parts"].append(member)
            elif renders and member.type in GEOMETRY_TYPES:
                group["unread"].append(member)
        found[top.as_pointer()] = group
    return found


def _rendering(view_layer):
    """``(pointers of the objects in a rendering collection of the view layer, pointers of boolean operands,
    {parent pointer: [children]})``."""
    found, stack, operands, children = set(), [view_layer.layer_collection], set(), {}
    while stack:
        layer = stack.pop()
        if layer.exclude or layer.collection.hide_render:
            continue
        found.update(obj.as_pointer() for obj in layer.collection.objects)
        stack.extend(layer.children)
    for obj in view_layer.objects:
        if obj.parent is not None:
            children.setdefault(obj.parent.as_pointer(), []).append(obj)
        for modifier in obj.modifiers:  # A boolean's cutter shapes another object; it is no part of its own.
            if modifier.type == "BOOLEAN":
                if getattr(modifier, "operand_type", "OBJECT") == "COLLECTION" and modifier.collection is not None:
                    operands.update(item.as_pointer() for item in modifier.collection.all_objects)
                elif getattr(modifier, "object", None) is not None:
                    operands.add(modifier.object.as_pointer())
    return found, operands, children
