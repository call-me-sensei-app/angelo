# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed, host-derived domain labels for admitted Blender Python fallbacks.

A ``angelo_execute_python`` invocation means no bounded native tool covered the
request. Counting the Blender API areas those scripts reference shows which
native capabilities are missing. Labels come from an ``ast`` walk of imports,
``bpy.ops``/``bpy.data`` paths, attribute names and node-type strings. They are
a heuristic, not the model's stated intent, and only the fixed label is kept:
never code, names or the script's description. A script counts once in every
category it touches; ``objects`` applies only when nothing more specific
matched, and ``other`` when nothing matched at all.
"""

import ast
import functools

CATEGORIES = ("geometry", "modifiers", "materials", "geometry_nodes", "rigging", "animation",
              "camera_lighting", "render_world", "external_io", "objects", "other")

_IMPORTS = {"bmesh": "geometry", **dict.fromkeys(
    ("os", "pathlib", "shutil", "subprocess", "socket", "urllib", "http", "requests", "tempfile", "glob", "zipfile"),
    "external_io")}
_DATA = {"meshes": "geometry", "curves": "geometry", "hair_curves": "geometry", "pointclouds": "geometry",
         "materials": "materials", "textures": "materials", "node_groups": "geometry_nodes",
         "armatures": "rigging", "actions": "animation", "cameras": "camera_lighting",
         "lights": "camera_lighting", "lightprobes": "camera_lighting", "worlds": "render_world",
         "libraries": "external_io"}
_OPERATORS = {"mesh": "geometry", "uv": "geometry", "curve": "geometry", "sculpt": "geometry",
              "pose": "rigging", "armature": "rigging", "anim": "animation", "action": "animation",
              "graph": "animation", "nla": "animation", "render": "render_world", "world": "render_world",
              "material": "materials", "node": "materials", "geometry": "geometry_nodes",
              "import_scene": "external_io", "export_scene": "external_io", "import_mesh": "external_io",
              "export_mesh": "external_io", "import_curve": "external_io", "import_image": "external_io"}
_ATTRIBUTES = {
    "geometry": {"vertices", "polygons", "edges", "loops", "from_pydata", "uv_layers", "vertex_colors",
                 "color_attributes", "loop_triangles", "splines", "bezier_points", "shape_keys", "key_blocks"},
    "modifiers": {"modifiers"},
    "materials": {"material_slots", "active_material", "use_nodes"},
    "rigging": {"pose", "bones", "edit_bones", "vertex_groups"},
    "animation": {"keyframe_insert", "keyframe_delete", "animation_data", "animation_data_create", "fcurves",
                  "keyframe_points", "driver_add", "driver_remove", "drivers", "nla_tracks", "frame_set"},
    "camera_lighting": {"lens", "sensor_width", "energy", "shadow_soft_size", "spot_size", "dof"},
    "render_world": {"render", "cycles", "eevee", "view_settings", "display_settings", "world", "film_transparent",
                     "resolution_x", "resolution_y"},
    "external_io": {"filepath", "save_as_mainfile", "save_mainfile"},
}
_OBJECT_ATTRIBUTES = {"objects", "collections", "location", "rotation_euler", "rotation_quaternion", "scale",
                      "parent", "matrix_world", "select_set", "hide_set", "hide_viewport", "hide_render",
                      "dimensions", "children", "constraints"}
_NODE_PREFIXES = (("ShaderNode", "materials"), ("GeometryNode", "geometry_nodes"),
                  ("FunctionNode", "geometry_nodes"), ("CompositorNode", "render_world"))
_IO_OPERATOR_WORDS = ("import", "export", "save", "open", "append", "link", "revert")


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return parts[::-1]
    return []


def _path_category(path):
    if len(path) >= 3 and path[0] == "bpy" and path[1] == "data":
        return _DATA.get(path[2])
    if len(path) >= 4 and path[0] == "bpy" and path[1] == "ops":
        module, operator = path[2], path[3]
        if module == "object" and operator.startswith("modifier_"):
            return "modifiers"
        if module == "wm" and any(word in operator for word in _IO_OPERATOR_WORDS):
            return "external_io"
        return _OPERATORS.get(module, "objects" if module == "object" else None)
    return None


def fallback_categories(code):
    """Return the sorted fixed categories a reviewed script references."""
    return _scan(code)[0]


def uses_preferences(code):
    """Whether a reviewed script refers to application preferences (``context.preferences``,
    ``bpy.ops.preferences`` or the user-preference file operators).

    Preferences are outside the document: neither Blender Undo nor a snapshot
    restores them, so the approval card says so. The same heuristic as the
    categories: a reference, not proof of a write, and code that hides the
    name is covered only by the card's general warning.
    """
    return _scan(code)[1]


# Admission parses a script once on the provider thread; the approval card then
# reads the cached answer instead of parsing up to 32 KB while it draws.
@functools.lru_cache(maxsize=16)
def _scan(code):
    try:
        tree = ast.parse(code, "<angelo-reviewed-script>", "exec")
    except (SyntaxError, ValueError, RecursionError, MemoryError, TypeError):
        return ("other",), False
    found, object_level, preferences = set(), False, False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(_IMPORTS[a.name.split(".")[0]] for a in node.names if a.name.split(".")[0] in _IMPORTS)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in _IMPORTS:
            found.add(_IMPORTS[node.module.split(".")[0]])
        elif isinstance(node, ast.Attribute):
            path = _dotted(node)
            preferences |= node.attr == "preferences" or (path[:2] == ["bpy", "ops"] and "userpref" in node.attr)
            category = _path_category(path)
            if category == "objects":
                object_level = True
            elif category:
                found.add(category)
            for name, attributes in _ATTRIBUTES.items():
                if node.attr in attributes:
                    found.add(name)
            object_level |= node.attr in _OBJECT_ATTRIBUTES
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            found.add("external_io")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(category for prefix, category in _NODE_PREFIXES if node.value.startswith(prefix))
    if not found:
        found.add("objects" if object_level else "other")
    return tuple(category for category in CATEGORIES if category in found), preferences
