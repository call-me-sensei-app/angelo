# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The World picture's cache key against Blender's own nodes (``web_world_key``; doc/angelo/web_export.rst, "The
World"): every input that changes what a World renders changes the key, nothing the node editor only shows does.

Run in Angelo, in background (``--factory-startup``)::

    <Angelo> --background --factory-startup --python tests/python/angelo_agent/test_blender_web_world_key.py

Not a list of cases: every shader node type this Blender defines is added to a World, and each of its settings,
its nested data (ramps, curves, mappings), its input values and the values its outputs keep (RGB, Value) is changed
in turn, as are the World's own settings. Blender's own rule decides what must happen: a change that updates the
depsgraph (what a render would see) must change the key and the key must come back when it is undone; a change that
updates no depsgraph (what only the editor shows, such as a collapsed panel) must keep it. Moving, selecting,
labelling, colouring or resizing nodes must keep it. An image's file (rewritten), its packed bytes (repacked from
another file of the same size) and its colour space must change it; so must the engine and a group's inner edit.
What can't be read (a Texture Coordinate's object, a Script node, a Python node, a driver, groups deeper than the
bound) must give a key that changes after a real edit of what it reads and keeps while nothing it reads changes (an
unrelated object moved, a selection, an export's own updates). Writes ``results.json`` in ``ANGELO_QA_DIR``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-web-world-key-qa"))
OUT.mkdir(parents=True, exist_ok=True)

from angelo_agent import web_world, web_world_key  # noqa: E402

checks, observed = {}, {}
report = {"success": False, "checks": checks, "observed": observed}
scene = bpy.context.scene
ID_FIELDS = frozenset(bpy.types.ID.bl_rna.properties.keys())   # Users, library, preview: not the World's look.
updated = set()


@bpy.app.handlers.persistent
def note_updates(_scene, depsgraph):
    updated.update(update.id.original.session_uid for update in depsgraph.updates)   # An update names the copy.


bpy.app.handlers.depsgraph_update_post.append(note_updates)


def check(name, value, detail=None):
    checks[name] = bool(value)
    print(("PASS " if value else "FAIL ") + name + ("" if value or detail is None else ": %s" % (detail,)), flush=True)


def key():
    """The key of the scene's World as an export plans it (a sweep can leave no output reached: then the key
    itself, which the plan would use once one is)."""
    return web_world.plan(scene).get("key") or web_world_key.key(scene.world, scene.render.engine,
                                                                 (scene.frame_current, scene.frame_subframe))


def settle():
    """Evaluate what the last edit tagged; True when it updated the scene's World (what a render would see)."""
    updated.clear()
    bpy.context.view_layer.update()
    return scene.world.session_uid in updated or scene.world.node_tree.session_uid in updated


def fresh_world(name):
    world = bpy.data.worlds.new(name)
    tree = world.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    output = tree.nodes.new("ShaderNodeOutputWorld")
    background = tree.nodes.new("ShaderNodeBackground")
    tree.links.new(background.outputs[0], output.inputs["Surface"])
    sky = tree.nodes.new("ShaderNodeTexSky")   # Something beyond a colour, so the World is baked and keyed.
    tree.links.new(sky.outputs[0], background.inputs["Color"])
    scene.world = world
    settle()
    return world, tree, background


def colour_group(name, inner=None):
    """A node group with a Colour output fed by a Color Ramp, or by a group node of ``inner``."""
    group = bpy.data.node_groups.new(name, "ShaderNodeTree")
    group.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    output = group.nodes.new("NodeGroupOutput")
    source = group.nodes.new("ShaderNodeValToRGB") if inner is None else group.nodes.new("ShaderNodeGroup")
    if inner is not None:
        source.node_tree = inner
    group.links.new(source.outputs[0], output.inputs[0])
    return group, source


def use_group(tree, background, group):
    node = tree.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    tree.links.new(node.outputs[0], background.inputs["Color"])
    return node


def changed_value(prop, value):
    """Another value for ``prop`` (an RNA property) than ``value``, or None when there is none to try."""
    if prop.type == "BOOLEAN" and not prop.is_array:
        return not value
    if prop.type == "ENUM" and not prop.is_enum_flag:
        others = [item.identifier for item in prop.enum_items if item.identifier != value]
        return others[0] if others else None
    if prop.type in {"INT", "FLOAT"}:
        step = 1 if prop.type == "INT" else 0.137
        low, high = prop.soft_min, prop.soft_max
        if prop.is_array:
            items = list(value)
            if not items:
                return None
            items[0] = items[0] + step if items[0] + step <= high else items[0] - step
            return items
        bumped = value + step if value + step <= high else value - step
        return bumped if low <= bumped <= high else None
    return None


class Sweep:
    """Changes tried, and those that break Blender's rule: ``unkeyed`` (a render sees it, the key doesn't move),
    ``editor_moves`` (a node's own setting no render sees, the key moves) and ``not_back`` (undone, the key
    differs, where the key holds no edit count). A node with no sockets reaches no render, whatever Blender tags."""

    def __init__(self):
        self.tried, self.unkeyed, self.editor_moves, self.not_back = [], [], [], []

    def judge(self, label, before, apply, undo, own=False, reaches=True):
        apply()
        renders = settle() and reaches
        after = key()
        undo()
        settle()
        self.tried.append(label)
        if renders and after == before:
            self.unkeyed.append(label)
        if own and not renders and after != before and before[-1] is None:   # Nested data and the World's settings may still render.
            self.editor_moves.append(label)
        if key() != before and before[-1] is None:
            self.not_back.append(label)

    def settings(self, owner, label, depth=0, reaches=True):
        hidden = web_world_key.EDITOR_ONLY if depth == 0 else web_world_key.NESTED_EDITOR_ONLY
        for prop in owner.bl_rna.properties:
            name = prop.identifier
            if name in hidden or (prop.is_readonly and prop.type != "POINTER") or \
                    (isinstance(owner, bpy.types.ID) and name in ID_FIELDS):
                continue
            if prop.type == "POINTER" and depth < 3:
                nested = getattr(owner, name)
                if nested is not None and not isinstance(nested, bpy.types.ID) and hasattr(nested, "bl_rna"):
                    self.settings(nested, label + "." + name, depth + 1, reaches)
                continue
            if prop.type == "COLLECTION" and depth < 3:
                for index, item in enumerate(list(getattr(owner, name))[:2]):
                    self.settings(item, "%s.%s[%d]" % (label, name, index), depth + 1, reaches)
                continue
            try:
                value = getattr(owner, name)
                value = list(value) if prop.type in {"INT", "FLOAT", "BOOLEAN"} and prop.is_array else value
                other = changed_value(prop, value)
                if other is None:
                    continue
                before = key()
                setattr(owner, name, other)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                continue
            now = getattr(owner, name)
            setattr(owner, name, value)
            if (list(now) if getattr(prop, "is_array", False) else now) == value:
                continue   # Clamped or refused: nothing changed.
            self.judge(label + "." + name, before, lambda: setattr(owner, name, other),
                       lambda: setattr(owner, name, value), own=depth == 0 and isinstance(owner, bpy.types.Node),
                       reaches=reaches)

    def sockets(self, node, label):
        for side in ("inputs", "outputs"):
            for socket in getattr(node, side):
                value = getattr(socket, "default_value", None)
                if value is None or isinstance(value, (str, bpy.types.ID)):
                    continue
                original = list(value) if hasattr(value, "__len__") else value
                other = [item + 0.137 for item in original] if isinstance(original, list) else (
                    (not original) if isinstance(original, bool) else original + 1)
                before = key()
                try:
                    socket.default_value = other
                except (TypeError, ValueError, AttributeError):
                    continue
                now = socket.default_value
                socket.default_value = original
                if (list(now) if hasattr(now, "__len__") else now) == original:
                    continue

                def apply(socket=socket, other=other):
                    socket.default_value = other

                def undo(socket=socket, original=original):
                    socket.default_value = original

                self.judge("%s.%s[%s]" % (label, side, socket.identifier), before, apply, undo)


try:
    # Every shader node type, its settings, nested data and values; and the World's own settings.
    kinds = sorted({name for name in dir(bpy.types) if name.startswith("ShaderNode")
                    and issubclass(getattr(bpy.types, name), bpy.types.ShaderNode)} | {"NodeReroute", "NodeFrame"})
    sweep, unreadable, made = Sweep(), [], 0
    for kind in kinds:
        world, tree, _background = fresh_world("Sweep " + kind)
        try:
            node = tree.nodes.new(kind)
        except RuntimeError:
            continue
        made += 1
        settle()
        if key()[-1] is not None and kind != "ShaderNodeScript":
            unreadable.append(kind)
        sweep.settings(node, kind, reaches=bool(len(node.inputs) or len(node.outputs)))
        sweep.sockets(node, kind)
        bpy.data.worlds.remove(world)
    world, tree, background = fresh_world("Settings")
    sweep.settings(world, "World")
    observed.update(node_types=made, changes_tried=len(sweep.tried), unkeyed=sweep.unkeyed,
                    editor_moves=sweep.editor_moves, not_back=sweep.not_back, unreadable=unreadable)
    check("every_node_type_is_swept", made >= 100, made)
    check("depsgraph_updates_were_seen", len(sweep.tried) > len(sweep.editor_moves) and
          any(label.endswith("inputs[Strength]") for label in sweep.tried))
    check("every_rendered_change_moves_the_key", sweep.tried and not sweep.unkeyed, sweep.unkeyed[:40])
    check("editor_only_changes_keep_the_key", not sweep.editor_moves, sweep.editor_moves[:40])
    check("undoing_a_change_brings_the_key_back", not sweep.not_back, sweep.not_back[:40])
    check("built_in_nodes_are_read", not unreadable, unreadable)
    before = key()
    scene.render.engine = "CYCLES" if scene.render.engine != "CYCLES" else "BLENDER_EEVEE"
    check("engine_changes_the_key", key() != before)

    # The audit's cases, among the sweep's: a ramp's colour, and an RGB node feeding a mix.
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    rgb, mix = tree.nodes.new("ShaderNodeRGB"), tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    tree.links.new(rgb.outputs[0], mix.inputs[7])
    tree.links.new(ramp.outputs[0], mix.inputs[6])
    tree.links.new(mix.outputs[2], background.inputs["Color"])
    before = key()
    ramp.color_ramp.elements[1].color = (0.9, 0.2, 0.1, 1.0)
    check("ramp_colour_changes_the_key", key() != before)
    before = key()
    ramp.color_ramp.elements.new(0.4)
    check("ramp_element_added_changes_the_key", key() != before)
    before = key()
    rgb.outputs[0].default_value = (0.1, 0.8, 0.3, 1.0)
    check("rgb_into_mix_changes_the_key", key() != before)
    curves = tree.nodes.new("ShaderNodeRGBCurve")
    before = key()
    curves.mapping.curves[3].points[0].location = (0.0, 0.25)
    check("curve_point_changes_the_key", key() != before)

    # What only the editor shows keeps it.
    stable = key()
    check("same_state_same_key", key() == stable)
    for node in tree.nodes:
        node.location = (node.location[0] + 50, node.location[1] - 20)
        node.select = not node.select
        node.label = "moved"
        node.use_custom_color, node.color = True, (0.3, 0.6, 0.9)
        node.width += 30
        node.hide = True
    frame = tree.nodes.new("NodeFrame")
    frame.label_size, frame.shrink = 30, False
    stable = key()
    frame.label_size = 40
    check("editor_only_edits_keep_the_key", key() == stable)

    # An image's file, packed bytes and colour space.
    folder = tempfile.mkdtemp(prefix="angelo-web-world-key-")

    def write_image(path, value):
        made = bpy.data.images.new("Probe", 8, 4)
        made.pixels = [value] * (8 * 4 * 4)
        made.filepath_raw, made.file_format = path, "BMP"   # Uncompressed: the same size whatever the pixels.
        made.save()
        bpy.data.images.remove(made)

    path = os.path.join(folder, "probe.bmp")
    write_image(path, 0.2)
    image = bpy.data.images.load(path)
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    tree.links.new(texture.outputs[0], mix.inputs[6])
    before = key()
    time.sleep(0.01)
    write_image(path, 0.7)
    check("rewritten_image_file_changes_the_key", key() != before)
    before = key()
    image.colorspace_settings.name = "Non-Color"
    check("image_colour_space_changes_the_key", key() != before)
    image.pack()
    before, size = key(), image.packed_file.size
    write_image(path, 0.4)
    image.unpack(method="REMOVE")
    image.reload()
    image.pack()
    check("repacked_bytes_of_the_same_size_change_the_key", image.packed_file.size == size and key() != before,
          (size, image.packed_file.size))

    # What can't be read: changed by a real edit of what it reads, kept otherwise.
    bystander = bpy.data.objects.new("Bystander", None)
    scene.collection.objects.link(bystander)

    def unreadable_world(label, prepare, edit):
        world, tree, background = fresh_world(label)
        prepare(world, tree, background)
        settle()
        first = key()
        settle()
        bystander.location.x += 1.0    # Something it doesn't read.
        settle()
        kept = key()
        edit(world, tree, background)
        settle()
        check(label + "_follows_its_own_edits_only", first[-1] is not None and kept == first and key() != first,
              {"volatile": first[-1], "kept": kept == first})

    target = bpy.data.objects.new("Target", None)
    scene.collection.objects.link(target)

    def object_coordinates(world, tree, background):
        tree.nodes.new("ShaderNodeTexCoord").object = target

    def move_target(*_args):
        target.location.z += 0.5

    def script(world, tree, background):
        node = tree.nodes.new("ShaderNodeScript")
        node.mode, node.script = "INTERNAL", bpy.data.texts.new("probe.osl")
        node.script.write("shader probe(output color C = 0) {}")

    def edit_script(world, tree, background):
        next(node for node in tree.nodes if node.bl_idname == "ShaderNodeScript").script.write("\n// edited")

    def driver(world, tree, background):
        curve = background.inputs["Strength"].driver_add("default_value")
        variable = curve.driver.variables.new()
        variable.type = "TRANSFORMS"
        variable.targets[0].id = target
        variable.targets[0].transform_type = "LOC_Z"
        curve.driver.expression = variable.name

    def deep_groups(world, tree, background):
        inner = None
        for depth in range(web_world_key.MAX_GROUP_DEPTH + 2):
            inner, _source = colour_group("Depth %d" % depth, inner)
        use_group(tree, background, inner)

    def edit_tree(world, tree, background):
        background.inputs["Strength"].default_value += 0.25

    class AngeloProbeNode(bpy.types.ShaderNodeCustomGroup):
        bl_idname, bl_label = "AngeloProbeNode", "Probe"

    bpy.utils.register_class(AngeloProbeNode)

    def python_node(world, tree, background):
        tree.nodes.new("AngeloProbeNode")

    def retag_tree(world, tree, background):   # What a Python node's own code would change: the tree updates.
        tree.update_tag()

    for label, prepare, edit in (("object_coordinates", object_coordinates, move_target),
                                 ("script_node", script, edit_script), ("driver", driver, move_target),
                                 ("deep_groups", deep_groups, edit_tree), ("python_node", python_node, retag_tree)):
        unreadable_world(label, prepare, edit)
    world, tree, background = fresh_world("Quiet")
    object_coordinates(world, tree, background)
    settle()
    first = key()
    target.select_set(not target.select_get())
    settle()
    check("a_selection_is_no_edit", key() == first)
    with web_world_key.quiet():   # An export's own evaluations are no edit.
        move_target()
        settle()
    check("an_exports_own_updates_are_not_edits", key() == first)

    # A group's inner edit; a group used twice is read once; the key's cost.
    world, tree, background = fresh_world("Grouped")
    group, inner_ramp = colour_group("Inner")
    for _ in range(2):
        use_group(tree, background, group)
    before = key()
    inner_ramp.color_ramp.elements[0].position = 0.3
    check("an_edit_inside_a_group_changes_the_key", key() != before)
    check("a_shared_group_is_read_once", len(key()[5]) == 1, len(key()[5]))
    for index in range(60):
        tree.nodes.new(("ShaderNodeValToRGB", "ShaderNodeTexNoise", "ShaderNodeMath", "ShaderNodeMix")[index % 4])
    started = time.perf_counter()
    for _ in range(20):
        key()
    observed["key_ms_66_nodes"] = round((time.perf_counter() - started) * 1000.0 / 20, 2)
    import numpy as np
    big = bpy.data.images.new("Big", 4096, 2048, float_buffer=True)   # A 4K HDRI's worth of packed bytes.
    big.pixels.foreach_set(np.random.default_rng(7).random(4096 * 2048 * 4, dtype=np.float32))
    big.filepath_raw, big.file_format = os.path.join(folder, "big.exr"), "OPEN_EXR"
    scene.render.image_settings.color_depth = "16"
    big.save()
    big.pack()
    tree.nodes.new("ShaderNodeTexEnvironment").image = big
    started = time.perf_counter()
    for _ in range(5):
        key()
    observed["key_ms_with_packed_exr"] = round((time.perf_counter() - started) * 1000.0 / 5, 2)
    observed["packed_exr_bytes"] = big.packed_file.size
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in checks.items() if not passed)
print("WEB_WORLD_KEY_BACKGROUND failed checks:", json.dumps(failed), json.dumps(observed)[:3000])
assert report["success"], failed
