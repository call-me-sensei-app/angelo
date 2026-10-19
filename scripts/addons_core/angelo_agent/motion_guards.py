# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Why a motion read (``motion_frames.Sampler``) must not evaluate other frames now: model-facing reasons.

``scene.frame_set`` evaluates the depsgraph of every view layer of the scene, building one for a layer that has none,
and an active depsgraph writes the frame's animated values back to the originals (reading one layer only, an
edit seen only in another view layer was lost, a simulation only there was stepped, and six never-shown layers held
2.7 GB after one read). So a read covers every view layer (``graphs``): the channels it keeps and the simulations it
looks for are those of every layer's depsgraph, and a scene with a layer no depsgraph has evaluated is not measured.

Not measured (``unmeasured``): playback, a render, a bake, an export or an Angelo job running; for a read the model
asks for, a Blender interaction in progress (as for a view strip); an object in another mode than Object or Pose; a view
layer without a depsgraph; Python from add-ons or scripts a frame change would run (scripted drivers, frame-change
handlers that do not ship: ``render_extensions``; for a read the model asks for in the Read-only or Ask mode,
depsgraph-update handlers too, and message-bus subscribers to a value the read puts back: ``subscribed``); a
simulation a frame change would step (``stepping``: an unbaked point cache of cloth, soft body, dynamic paint,
emitter particles or hair dynamics, a fluid domain without baked data, an unbaked rigid body world, a Geometry Nodes
simulation zone not baked to disk; a Bake node outside a simulation only passes geometry through, and a packed
bake's state is not readable from Python, so a packed simulation is refused with that reason); a scene whose last
read projected more than ``motion_frames.MAX_SECONDS`` for two frames (``Estimates``), checked before the scans that
grow with the scene.
"""

import time
from pathlib import Path

SIMULATION_NODES = frozenset({"GeometryNodeSimulationInput", "GeometryNodeSimulationOutput"})
STEPPING = frozenset({"CLOTH", "SOFT_BODY"})
OTHER_JOBS = ("POINTCACHE", "BAKE_GEOMETRY_NODES", "USD_EXPORT", "ALEMBIC_EXPORT")
MAX_NODE_SCAN = 4096
MODES = frozenset({"OBJECT", "POSE"})  # Pose mode holds no edit buffer a frame change would bypass.
HOLD_SECONDS = 60.0  # How long a slow scene's frame cost first stands, doubled each time it is slow again,
MAX_HOLD_SECONDS = 3600.0  # up to an hour: a recurring stall that yields no fact stays rare in a long session.


def graphs(scene):
    """``[depsgraph or None]`` of every view layer of ``scene`` (reading one builds none)."""
    return [layer.depsgraph for layer in scene.view_layers]


def unmeasured(window, mode, seconds, estimates):
    """Why the scene's other frames cannot be read now, or None (module docstring). ``mode``: the approval mode of a
    read the model asked for (``view_policy.mode_of``); None for a read after an edit, whose own update ran the
    depsgraph-update handlers already."""
    import bpy
    scene = window.scene
    if getattr(window.screen, "is_animation_playing", False):
        return "the animation is playing"
    if bpy.app.is_job_running("RENDER") or bpy.app.is_job_running("OBJECT_BAKE") or _other_job(scene):
        return "a render, bake or export is running"
    if _angelo_job():
        return "an Angelo job is running"
    if mode is not None:
        from .input_policy import interaction_active
        if interaction_active(window):
            return "a Blender interaction is in progress"
    held = estimates.cost(scene)  # Before the scene-wide scans: a held scene is refused in microseconds.
    if held is not None and held > seconds:
        return "a read of this scene takes about %.2f s, more than a read may" % held
    active = window.view_layer.objects.active
    if active is not None and active.mode not in MODES:
        return "%r is in %s mode" % (active.name[:64], active.mode.replace("_", " ").title())
    missing = [layer.name for layer, graph in zip(scene.view_layers, graphs(scene)) if graph is None]
    if missing:
        return ("%d view layer(s) no window or update has evaluated (%s): a frame change would build and evaluate "
                "each" % (len(missing), ", ".join(repr(name[:64]) for name in missing[:3])))
    from . import render_extensions
    report = render_extensions.inspect(scene, render_extensions.FRAME_BAKE)
    frame_handlers = [label for name, label in report.handlers if name not in render_extensions.DEPSGRAPH]
    asked = mode is not None and report.depsgraph_handlers and mode not in render_extensions.AUTO_MODES
    if report.drivers or frame_handlers or asked:
        return "a frame change runs Python from add-ons or scripts (%s)" % report.summary()[:200]
    what = stepping(scene)
    if what:
        return "%s, which a frame change would step" % what
    return None


def _angelo_job():
    try:
        from . import job_ui
    except ImportError:
        return False
    manager = job_ui.manager(create=False)
    return manager is not None and manager.active() is not None


def _other_job(scene):
    try:
        from _bpy import _angelo_jobs
    except ImportError:
        return False
    return any(_angelo_jobs.progress(scene, kind) is not None for kind in OTHER_JOBS)


def stepping(scene):
    """What a frame change would simulate, or None: in the objects every view layer's depsgraph evaluates (instanced
    ones included), or the scene's rigid body world."""
    world = getattr(scene, "rigidbody_world", None)
    if world is not None and world.enabled and not getattr(getattr(world, "point_cache", None), "is_baked", False):
        return "the unbaked Rigid Body World"
    seen = set()
    for graph in graphs(scene):
        for evaluated in (graph.objects if graph is not None else ()):
            obj = evaluated.original
            if obj.as_pointer() in seen:
                continue
            seen.add(obj.as_pointer())
            for modifier in obj.modifiers:
                what = _modifier_steps(modifier)
                if what:
                    return "%r has %s" % (obj.name[:64], what)
    return None


def _modifier_steps(modifier):
    kind = modifier.type
    if kind in STEPPING:
        return None if modifier.point_cache.is_baked else "an unbaked %s cache" % kind.lower().replace("_", " ")
    if kind == "PARTICLE_SYSTEM":
        system = modifier.particle_system
        steps = system.settings.type == "EMITTER" or getattr(system, "use_hair_dynamics", False)
        return "unbaked particles" if steps and not system.point_cache.is_baked else None
    if kind == "FLUID":
        domain = getattr(modifier, "domain_settings", None) if modifier.fluid_type == "DOMAIN" else None
        return "a fluid domain without baked data" if domain is not None and not domain.has_cache_baked_data else None
    if kind == "DYNAMIC_PAINT":
        canvas = getattr(modifier, "canvas_settings", None) if modifier.ui_type == "CANVAS" else None
        for surface in getattr(canvas, "canvas_surfaces", ()) or ():
            if not surface.point_cache.is_baked:
                return "an unbaked dynamic paint surface"
        return None
    if kind == "NODES":
        found = _simulations(modifier.node_group, set(), [MAX_NODE_SCAN])
        if found is None:
            return "a node group too large to scan for simulation zones"
        if found and not _disk_baked(modifier):
            return "a Geometry Nodes simulation zone not baked to disk (a packed bake's state cannot be read)"
    return None


def _simulations(tree, seen, budget):
    """Whether a node tree (its groups too) holds a simulation zone; None past the scan budget."""
    if tree is None or tree.as_pointer() in seen:
        return False
    seen.add(tree.as_pointer())
    for node in tree.nodes:
        budget[0] -= 1
        if budget[0] < 0:
            return None
        if node.bl_idname in SIMULATION_NODES:
            return True
        group = getattr(node, "node_tree", None)
        found = _simulations(group, seen, budget) if group is not None else False
        if found or found is None:
            return found
    return False


def _disk_baked(modifier):
    """Whether every simulation bake of a Geometry Nodes modifier is stored on disk with data written."""
    import bpy
    found = False
    for bake in modifier.bakes:
        if getattr(bake.node, "bl_idname", "") != "GeometryNodeSimulationOutput":
            continue
        found = True
        target = bake.bake_target if bake.bake_target != "INHERIT" else modifier.bake_target
        directory = bake.directory if bake.use_custom_path else modifier.bake_directory
        if target != "DISK" or not directory:
            return False
        meta = Path(bpy.path.abspath(directory)) / str(bake.bake_id) / "meta"
        if not meta.is_dir() or next(meta.iterdir(), None) is None:
            return False
    return found


def subscribed(channels, mode):
    """For a read the model asks for in the Read-only or Ask mode: why putting the animated values back would run
    Python from add-ons (``bpy.msgbus`` subscribers to a channel, or to the scene camera, that ``close`` may write
    back), or None. The frame itself is set without a message-bus change (``frame_set``)."""
    from . import render_extensions
    if mode is None or mode in render_extensions.AUTO_MODES:
        return None
    from .dependency_paths import path_steps
    keys = {("Scene", "camera")}
    for owner, path in channels.entries:
        text = path_steps(path)[-1][0]
        parent = path[:len(path) - len(text)].rstrip(".")
        try:
            holder = owner.path_resolve(parent) if parent else owner
        except (ValueError, AttributeError, ReferenceError):
            continue
        keys.add((holder.bl_rna.identifier, None if text.startswith("[") else text.lstrip(".")))
    found = render_extensions.subscribers(keys)
    if found:
        return ("putting the frame's values back runs Python from add-ons or scripts (%d message-bus subscriber(s): "
                "%s)" % (len(found), ", ".join(found[:3])[:200]))
    return None


class Estimates:
    """The projected cost of the least read of each scene that tells motion (its work beside the frames plus two
    frames, ``Sampler.close``), kept while it is over the budget and holds (``HOLD_SECONDS``, doubled each time a
    read after it is slow again, at most ``MAX_HOLD_SECONDS``): Blender states no frame cost before one is
    evaluated, and a time-to-live alone would make a heavy scene stall every minute for no fact."""

    def __init__(self, clock=time.monotonic):
        self.clock, self.found = clock, {}  # scene session uid -> (seconds, when, hold)

    def cost(self, scene):
        found = self.found.get(int(scene.session_uid))
        if found is None or self.clock() - found[1] > found[2]:
            return None
        return found[0]

    def note(self, scene, seconds, limit):
        """A read of ``scene`` projects ``seconds`` for two frames; ``limit``: the read's budget."""
        uid = int(scene.session_uid)
        if seconds <= limit:
            self.found.pop(uid, None)
            return
        previous = self.found.get(uid)
        hold = HOLD_SECONDS if previous is None else min(MAX_HOLD_SECONDS, 2 * previous[2])
        self.found[uid] = (seconds, self.clock(), hold)

    def clear(self):
        self.found.clear()
