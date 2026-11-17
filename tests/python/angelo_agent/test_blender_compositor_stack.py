# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The compositor effects a render runs, read from real effects: more than two of them, any order, and a flag a key,
a driver or an NLA strip can turn on at a frame admission does not read. Render Layers in a group reach another scene
as Blender's own render does. Background, local only; writes results.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy

try:
    from angelo_agent import compositor_stack, render_reach
except ImportError:   # A stock Blender run from the checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
    from angelo_agent import compositor_stack, render_reach

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-compositor-stack-qa"))
OUT.mkdir(parents=True, exist_ok=True)
checks, notes = {}, {}


def case(name):
    def wrap(function):
        try:
            checks[name] = bool(function())
        except Exception:
            checks[name] = False
            print(traceback.format_exc())
        return function
    return wrap


def stack(count):
    """A new scene with ``count`` effects, each with its own compositor node group, all enabled for render."""
    scene = bpy.data.scenes.new("Stack")
    scene.compositor_effects.clear()
    for index in range(count):
        effect = scene.compositor_effects.new("Effect %d" % index)
        effect.node_group = bpy.data.node_groups.new("Effect %d" % index, "CompositorNodeTree")
    scene.render.use_compositing = True
    return scene


def names(scene):
    return [tree.name.split(".")[0] for _path, tree in compositor_stack.render_trees(scene)]   # Stacks repeat names.


@case("every_effect_is_read_in_stack_order_and_a_disabled_one_is_not")
def _():
    scene = stack(4)
    scene.compositor_effects[1].enable_for_render = False
    return names(scene) == ["Effect 0", "Effect 2", "Effect 3"]


@case("a_keyed_flag_counts_whatever_the_current_frame_says")
def _():
    scene = stack(3)
    target = scene.compositor_effects[2]
    target.enable_for_render = False
    target.keyframe_insert("enable_for_render", frame=1)
    target.enable_for_render = True
    target.keyframe_insert("enable_for_render", frame=2)
    target.enable_for_render = False   # As the current frame says.
    return names(scene) == ["Effect 0", "Effect 1", "Effect 2"]


@case("a_driven_flag_counts")
def _():
    scene = stack(3)
    target = scene.compositor_effects[0]
    target.enable_for_render = False
    target.driver_add("enable_for_render").driver.expression = "frame >= 2"
    target.enable_for_render = False
    return names(scene) == ["Effect 0", "Effect 1", "Effect 2"]


@case("a_flag_animated_only_by_an_nla_strip_counts")
def _():
    scene = stack(3)
    target = scene.compositor_effects[1]
    target.enable_for_render = False
    target.keyframe_insert("enable_for_render", frame=1)
    data = scene.animation_data
    action = data.action
    track = data.nla_tracks.new()
    track.strips.new("Flag", 1, action)
    data.action = None
    return names(scene) == ["Effect 0", "Effect 1", "Effect 2"]


@case("an_effect_path_resolves_by_name_after_the_stack_changes")
def _():
    scene = stack(3)
    path = compositor_stack.render_trees(scene)[2][0]
    tree = scene.path_resolve(path)
    with bpy.context.temp_override(scene=scene):
        bpy.ops.scene.move_compositor_effect_to_index(name="Effect 2", index=0)
    return "[2]" not in path and scene.path_resolve(path) == tree and scene.compositor_effects[0].node_group == tree


@case("render_layers_in_a_group_reach_their_scene_unless_the_group_is_muted")
def _():
    scene, other = stack(1), bpy.data.scenes.new("Other")
    inner = bpy.data.node_groups.new("Inner", "CompositorNodeTree")
    layers = inner.nodes.new("CompositorNodeRLayers")
    layers.scene = other
    tree = scene.compositor_effects[0].node_group
    node = tree.nodes.new("CompositorNodeGroup")
    node.node_tree = inner
    budget = render_reach._Budget()
    listed = other in render_reach._compositor_scenes(scene, budget)
    node.mute = True
    budget = render_reach._Budget()
    return listed and other not in render_reach._compositor_scenes(scene, budget)


report = {"success": bool(checks) and all(checks.values()), "checks": checks, "notes": notes}
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ANGELO_COMPOSITOR_STACK", json.dumps(report))
