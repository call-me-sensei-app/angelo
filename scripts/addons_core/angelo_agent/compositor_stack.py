# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The compositor node groups of a scene, on either of Blender's compositor models.

Blender 5.3 made the compositor a stack of effects (``Scene.compositor_effects``): each effect has its own node group
and ``enable_for_render``, and a render runs every enabled one in stack order. ``Scene.compositing_node_group`` became
a deprecated alias of the ACTIVE effect's group (``rna_Scene_compositing_node_group_get``), so code that reads it
alone misses every other effect's File Output, Render Layers and image-wide nodes. Blender 5.2 has the one group.
Readings are duck-typed and need no Blender import, as the callers' host-test fakes need.

``enable_for_render`` is the one compositor gate that can be keyed or driven (``Node.mute`` and
``render.use_compositing`` cannot), so a render reads it per frame: an animated flag counts as on, whatever it says on
the current frame. An effect's path is Blender's own, by name: an index names another effect once the stack changes.
"""

from . import job_animated_settings as animated


def groups(scene):
    """Every compositor node group ``scene`` holds, enabled or not, in stack order, each once."""
    return [tree for _path, tree in _trees(scene, render_only=False)]


def render_trees(scene):
    """``[(RNA path from the scene, node group)]`` a render of ``scene`` composites through, in stack order: the
    effects enabled for render on 5.3, the one group before. It says nothing of ``render.use_compositing``, which
    callers check themselves."""
    return _trees(scene, render_only=True)


def _path(effect, index):
    finder = getattr(effect, "path_from_id", None)
    return finder() if finder is not None else "compositor_effects[%d]" % index


def _trees(scene, render_only):
    effects = getattr(scene, "compositor_effects", None)
    if effects is None:   # Blender 5.2: one group. Any other ID (a Material's node_tree) has neither.
        tree = getattr(scene, "compositing_node_group", None)
        return [("compositing_node_group", tree)] if tree is not None else []
    found, seen = [], set()
    for index, effect in enumerate(effects):
        tree = getattr(effect, "node_group", None)
        # Python wrappers of one data-block are distinct objects: its pointer is its identity.
        key = tree.as_pointer() if hasattr(tree, "as_pointer") else id(tree)
        path = _path(effect, index)
        runs = getattr(effect, "enable_for_render", True) or animated.channels(scene, (path + ".enable_for_render",))
        if tree is None or render_only and not runs or key in seen:
            continue
        seen.add(key)
        found.append((path + ".node_group", tree))
    return found
