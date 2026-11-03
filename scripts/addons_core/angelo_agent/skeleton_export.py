# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The skeleton a game engine receives from a Blender armature: which bones the exporter writes, under which parents,
with which extra bones. No Blender dependency.

Read from Blender 5.2's bundled exporters (``skeleton_profiles.SOURCES``):

* **Deform bones only** is off by default in both. With it on, FBX (``use_armature_deform_only``) keeps every
  ancestor of a deforming bone, deforming or not; glTF (``export_def_bones``) drops non-deforming bones and parents
  each kept bone to its nearest kept ancestor.
* **Leaf bones**: FBX's ``add_leaf_bones`` (on by default) appends ``<bone>_end`` below every chain's last bone; glTF
  adds none by default.
* **The armature object**: in an FBX it is a node above the root bones. Unreal reads that node as one more root
  bone unless it is named ``Armature`` (any case) and has one child; Unity and Godot keep it as a plain node. A
  glTF skin with several root bones gets one joint above them in Unreal, ``<armature>_ProxyTrueRootJoint`` (observed
  in UE 5.8.2, skeleton_engine_e2e).
* **Influences**: the glTF exporter writes 4 per vertex unless ``export_all_influences``; FBX writes all.

The exporter options per engine are the ones Angelo's engine link uses, which mirror each engine's own Blender
import: Godot's (``godot_link_assets.GODOT_DEFAULTS``), Unity's FBX script (``unity_link_assets.UNITY_FBX``) and
the shared glTF profile (``engine_link_assets.GLTF_DURABLE``), over Blender's defaults.
"""

FBX_DEFAULTS = {"add_leaf_bones": True, "use_armature_deform_only": False, "primary_bone_axis": "Y",
                "secondary_bone_axis": "X"}
GLTF_DEFAULTS = {"export_def_bones": False, "export_all_influences": False, "export_influence_nb": 4,
                 "export_leaf_bone": False}
OBJECT_ROOT = -2   # the parent of a root bone under the armature object Unreal reads as a bone
PROXY_ROOT = "_ProxyTrueRootJoint"   # what Unreal names the joint it adds above a glTF skin's several roots


def exporter_options(target, fmt):
    """The exporter options a target's usual export uses (see the module docstring)."""
    if fmt == "FBX":
        options = dict(FBX_DEFAULTS)
        if target == "UNITY":
            from .unity_link_assets import UNITY_FBX
            options.update(UNITY_FBX)
        return options
    options = dict(GLTF_DEFAULTS)
    if target == "GODOT":
        from .godot_link_assets import GODOT_DEFAULTS
        options.update(GODOT_DEFAULTS)
    else:
        from .engine_link_assets import GLTF_DURABLE
        options.update(GLTF_DURABLE)
    return options


def deform_only(fmt, options):
    return bool(options.get("use_armature_deform_only" if fmt == "FBX" else "export_def_bones"))


def exporter_influences(fmt, options):
    """The most influences per vertex the exporter writes, or None for all."""
    if fmt == "FBX" or options.get("export_all_influences"):
        return None
    return int(options.get("export_influence_nb", 4))


class Exported:
    """The engine's skeleton: ``bones`` (rig indices, parents first), ``parent`` (rig index, -1 for a root, or
    ``OBJECT_ROOT``), ``object_root`` (the armature object's name when the engine reads it as a bone), ``leaf_bones``
    (the '_end' bones the exporter adds, by the bone they follow)."""

    def __init__(self, rig, target, fmt, options, armature_name):
        self.rig, self.fmt = rig, fmt
        n = len(rig.names)
        if not deform_only(fmt, options):
            keep = set(range(n))
        elif fmt == "FBX":
            keep = set()
            for i in range(n):
                if rig.deform[i]:
                    keep.add(i)
                    keep.update(rig.ancestors(i))
        else:
            keep = {i for i in range(n) if rig.deform[i]}
        self.parent = {i: next((a for a in rig.ancestors(i) if a in keep), -1) for i in keep}
        self.object_root = None
        roots = [i for i, p in self.parent.items() if p < 0]
        dropped = armature_name.casefold() == "armature" and len(roots) == 1   # Unreal drops it over one child only
        if fmt == "FBX" and target == "UNREAL" and not dropped:
            self.object_root = armature_name
        elif fmt == "GLB" and target == "UNREAL" and len(roots) > 1:   # observed, UE 5.8.2 Interchange glTF
            self.object_root = armature_name + PROXY_ROOT
        if self.object_root is not None:
            self.parent = {i: (OBJECT_ROOT if p < 0 else p) for i, p in self.parent.items()}
        order, stack = [], sorted([i for i, p in self.parent.items() if p < 0], reverse=True)
        children = {i: [] for i in keep}
        for i, p in self.parent.items():
            if p >= 0:
                children[p].append(i)
        while stack:
            i = stack.pop()
            order.append(i)
            stack.extend(sorted(children[i], reverse=True))
        self.bones = order
        self.children = children
        add_leaves = options.get("add_leaf_bones") if fmt == "FBX" else options.get("export_leaf_bone")
        self.leaf_bones = [i for i in order if not children[i]] if add_leaves else []

    def __contains__(self, i):
        return i in self.parent

    def roots(self):
        """Root bones as the engine counts them (the armature object, when it is read as a bone, is the one root)."""
        if self.object_root is not None:
            return [self.object_root]
        return [self.rig.names[i] for i in self.bones if self.parent[i] < 0]

    def ancestors(self, i):
        out, p = [], self.parent.get(i, -1)
        while p >= 0:
            out.append(p)
            p = self.parent[p]
        return out

    def name_of(self, i):
        if i == OBJECT_ROOT:
            return self.object_root
        return None if i < 0 else self.rig.names[i]

    def named(self):
        """[(name, parent name)] of every bone the engine imports, leaf bones and the object root included."""
        out = [] if self.object_root is None else [(self.object_root, None)]
        out += [(self.rig.names[i], self.name_of(self.parent[i])) for i in self.bones]
        out += [(self.rig.names[i] + "_end", self.rig.names[i]) for i in self.leaf_bones]
        return out

    def count(self):
        return len(self.bones) + len(self.leaf_bones) + (1 if self.object_root is not None else 0)
