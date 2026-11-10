# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every value that computes a shader's output makes a baked material stale; display-only edits don't.

The 1.0.9 audit found shader edits that left the bake digest unchanged, so the export reused an old bake without a
stale warning: an RGB node's colour and a Value node's value (they live on the node's OUTPUT socket, which the
digest skipped) and a Color Ramp stop's colour (the node-level display exclusion, ``color``, applied inside the
ramp too). Those three exposed it; everything else here is held out.

The sweep, in real Blender (background; writes results.json under ANGELO_QA_DIR): one material holding every
shader node type Blender can make in a material tree, plus a group, a script node and two outputs. One edit at a
time against a fresh binding: every writable property of each node (other than the display-only ones listed in
``UI_ONLY``, decided from Blender's own UI, not from the digest), every writable property of the structs inside it
(ramps, curves, mappings, image users) to three levels, and every input and output socket's value. Each must make
the binding stale (a computed output's stored value goes unused, but only the RGB and Value nodes show theirs, so
all outputs count). Before the fix, on the 1.0.9 cut-4 app: 177 of 1,038 edits missed (curve points, every
texture's colour-mapping ramp, the active output and the output sockets among them), and so did a group's inner
colour, a ramp's last stop, the script's text and file. Then content edits no property sweep reaches: a ramp stop and a curve point added, a group's
inner value, the internal script's text, an external script file rewritten, the active output switched. Then
display-only edits, which must leave it current: a node moved, relabelled, resized, collapsed, recoloured,
selected, and a curve point selected.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background instance"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_blender_baked_export import ensure_addon, procedural, shape  # noqa: E402
from test_blender_baked_export_stale import bind, stale  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-baked-shading-qa"))
OUT.mkdir(parents=True, exist_ok=True)
# A node's own display: where it sits and how it looks in the editor (Blender's node UI), never what it computes.
UI_ONLY = {"name", "label", "location", "location_absolute", "width", "height", "select", "hide", "show_options",
           "show_preview", "show_texture", "use_custom_color", "color", "color_tag", "parent", "warning_propagation",
           "description"}
NESTED_UI_ONLY = {"select"}   # A curve point's selection.
SKIP_NODES = {"NodeGroupInput", "NodeGroupOutput", "NodeFrame", "NodeReroute", "ShaderNodeGroup", "ShaderNodeScript"}
EXPOSED = {("ShaderNodeRGB", "out Color"), ("ShaderNodeValue", "out Value"),
           ("ShaderNodeValToRGB", "color_ramp.elements[0].color")}


def _changed(prop, value):
    """A different valid value for ``prop`` (currently ``value``), or None when there is none to try."""
    kind = prop.type
    if kind == "BOOLEAN":
        return not value
    if kind == "ENUM":
        if prop.is_enum_flag:
            return None
        other = [item.identifier for item in prop.enum_items if item.identifier != value]
        return other[0] if other else None
    if kind == "STRING":
        return None if prop.subtype in {"FILE_PATH", "DIR_PATH", "BYTE_STRING"} else value + "_x"
    if kind in {"INT", "FLOAT"}:
        step = 1 if kind == "INT" else max(abs(value) * 0.25, 0.125)
        low, high = prop.hard_min, prop.hard_max
        for candidate in (value + step, value - step):
            if low <= candidate <= high:
                return type(value)(candidate)
    return None


def _edits(struct, path, depth, nested):
    """(label, struct, name, new value) for each writable computing value of ``struct``, and of its structs to
    ``depth`` levels."""
    skip = NESTED_UI_ONLY if nested else UI_ONLY
    for prop in struct.bl_rna.properties:
        name = prop.identifier
        if name in skip or name.startswith("bl_") or name in {"rna_type", "inputs", "outputs", "internal_links"}:
            continue
        label = "%s.%s" % (path, name) if path else name
        try:
            value = getattr(struct, name)
        except (AttributeError, RuntimeError, TypeError):
            continue
        if prop.type == "POINTER":
            if value is not None and not isinstance(value, bpy.types.ID) and depth > 0:
                yield from _edits(value, label, depth - 1, True)
            continue
        if prop.type == "COLLECTION":
            if depth > 0 and len(value):
                yield from _edits(value[0], label + "[0]", depth - 1, True)
            continue
        if prop.is_readonly:
            continue
        if getattr(prop, "is_array", False) or (hasattr(value, "__len__") and not isinstance(value, str)):
            items = list(value)
            if not items:
                continue
            new = _changed(prop, items[0])
            if new is None:
                continue
            items[0] = new
            yield label, struct, name, items
        else:
            new = _changed(prop, value)
            if new is not None:
                yield label, struct, name, new


def _socket_edits(node):
    for side, sockets in (("in", node.inputs), ("out", node.outputs)):
        for socket in sockets:
            prop = socket.bl_rna.properties.get("default_value")
            if prop is None or prop.is_readonly:
                continue
            value = socket.default_value
            if hasattr(value, "__len__") and not isinstance(value, str):
                items = list(value)
                new = _changed(prop, items[0]) if items else None
                if new is not None:
                    items[0] = new
                    yield "%s %s" % (side, socket.identifier), socket, "default_value", items
            else:
                new = _changed(prop, value)
                if new is not None:
                    yield "%s %s" % (side, socket.identifier), socket, "default_value", new


def _snapshot(struct, name):
    value = getattr(struct, name, None)
    return tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value


def _node_types():
    found = []
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name)
        if isinstance(cls, type) and issubclass(cls, bpy.types.ShaderNode) and cls is not bpy.types.ShaderNode:
            found.append(cls.bl_rna.identifier)
    return sorted(set(found) - SKIP_NODES)


def _material():
    material = procedural("Sweep", "noise")
    tree = material.node_tree
    made = []
    for kind in _node_types():
        try:
            node = tree.nodes.new(kind)
        except RuntimeError:   # Not available in a material tree (another tree type's node).
            continue
        node.location = (len(made) * 40.0, -600.0)
        made.append(node)
    return material, made


def run():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.engine = "CYCLES"
    ensure_addon()
    material, nodes = _material()
    obj = shape("Swept", "cube", material)
    tree = material.node_tree
    results = {"nodes": len(nodes), "edits": 0, "unsettable": 0, "misses": [], "exposed": [], "content": {},
               "display": {}}
    bind(obj)
    for node in nodes:
        edits = list(_edits(node, "", 3, False)) + list(_socket_edits(node))
        for label, struct, name, new in edits:
            before = _snapshot(struct, name)
            try:
                setattr(struct, name, new)
            except (TypeError, ValueError, RuntimeError, AttributeError):
                before = None
            if before is None or _snapshot(struct, name) == before:
                results["unsettable"] += 1   # Refused or ignored by Blender (a dependent value, a fixed state).
                continue
            results["edits"] += 1
            if not stale(obj):
                results["misses"].append("%s: %s" % (node.bl_idname, label))
            if (node.bl_idname, label) in EXPOSED:
                results["exposed"].append(label)
            bind(obj)

    def content(label, apply):
        apply()
        results["content"][label] = stale(obj)
        bind(obj)

    ramp = next(node for node in nodes if node.bl_idname == "ShaderNodeValToRGB")
    curves = next(node for node in nodes if node.bl_idname == "ShaderNodeRGBCurve")
    content("a ramp stop added", lambda: ramp.color_ramp.elements.new(0.37))
    content("a ramp's last stop recoloured", lambda: setattr(ramp.color_ramp.elements[-1], "color", (0.2, 0.4, 0.9, 1)))
    content("a curve point added", lambda: (curves.mapping.curves[3].points.new(0.4, 0.6), curves.mapping.update()))
    group = bpy.data.node_groups.new("Inner", "ShaderNodeTree")
    group.interface.new_socket("Out", in_out="OUTPUT", socket_type="NodeSocketColor")
    inner = group.nodes.new("ShaderNodeRGB")
    group.links.new(inner.outputs[0], group.nodes.new("NodeGroupOutput").inputs[0])
    holder = tree.nodes.new("ShaderNodeGroup")
    holder.node_tree = group
    bind(obj)
    content("a group's inner colour", lambda: setattr(inner.outputs[0], "default_value", (0.9, 0.1, 0.1, 1)))
    script = tree.nodes.new("ShaderNodeScript")
    text = bpy.data.texts.new("shade.osl")
    text.write("shader shade(output color C = color(0.5)) { C = color(0.5); }\n")
    script.mode, script.script = "INTERNAL", text
    bind(obj)
    content("the internal script's text", lambda: text.write("// edited\n"))
    external = Path(tempfile.mkdtemp(prefix="angelo-osl-")) / "shade.osl"
    external.write_text("shader shade(output color C = color(0.5)) { C = color(0.5); }\n")
    script.mode, script.filepath = "EXTERNAL", str(external)
    bind(obj)
    content("an external script file rewritten",
            lambda: (external.write_text("shader shade(output color C = color(0.25)) { C = color(0.25); }\n"),
                     os.utime(external, ns=(external.stat().st_mtime_ns + 10 ** 9,) * 2)))
    first = next(node for node in tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial")
    second = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(ramp.outputs[0], second.inputs["Surface"])
    first.is_active_output = True
    bind(obj)
    content("the active output switched", lambda: setattr(second, "is_active_output", True))

    def display(label, apply):
        apply()
        results["display"][label] = not stale(obj)
        bind(obj)

    node = nodes[0]
    display("a node moved", lambda: setattr(node, "location", (node.location.x + 300, node.location.y)))
    display("a node relabelled", lambda: setattr(node, "label", "Relabelled"))
    display("a node resized", lambda: setattr(node, "width", node.width + 40))
    display("a node collapsed", lambda: setattr(node, "hide", True))
    display("a node recoloured", lambda: (setattr(node, "use_custom_color", True),
                                         setattr(node, "color", (0.3, 0.6, 0.2))))
    display("a node selected", lambda: setattr(node, "select", not node.select))
    display("a curve point selected", lambda: setattr(curves.mapping.curves[3].points[0], "select", True))

    results["success"] = (not results["misses"] and results["edits"] > 200
                          and all(results["content"].values()) and all(results["display"].values())
                          and len(results["exposed"]) == len(EXPOSED))
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("ANGELO_BAKED_SHADING", json.dumps({key: results[key] for key in
                                              ("success", "nodes", "edits", "unsettable", "exposed", "content",
                                               "display")}), flush=True)
    for miss in results["misses"]:
        print("ANGELO_BAKED_SHADING_MISS", miss, flush=True)
    if not results["success"]:
        raise AssertionError("A shader edit left the baked material current, or a display edit made it stale")


run()
