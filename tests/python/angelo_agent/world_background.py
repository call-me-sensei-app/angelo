# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a World shows: the output the renderer uses, the nodes it reaches, and its colour along a direction.

The scenario checks (``scenario_checks.world_facts``) grade a sky by what the
renderer shows, never by which nodes merely exist. ``WorldGraph(world,
engine)`` reads a World's shader graph the way Cycles and EEVEE do for a
camera ray that meets the background:

* ``output`` is the World Output the renderer picks (``active_output``): one
  targeting the scene's engine before one targeting all engines, the active
  one first;
* ``used_nodes()`` are the nodes that output's Surface reaches through links
  the renderer follows: a muted or invalid link, a link into an unavailable
  socket and a muted node's inputs other than its pass-through are cut;
* ``radiance(view)`` evaluates the graph for one world direction (+Z up, unit
  length) with ``shader_graph``: what a camera ray sees there, the Background
  or Emission colour times its strength, mixed or added as the graph says.
  Nothing linked to the Surface is black; a world without a node graph shows
  its own colour.

In a World shader the Texture Coordinate node's Generated and Object outputs
and the Geometry node's Position are the ray direction; Texture Coordinate's
Normal and Reflection and Geometry's Normal, True Normal and Incoming are the
reversed direction; UV is zero; Light Path's Is Camera Ray is 1 and every
other ray kind 0; a texture node with no Vector link uses Generated. Cycles
and EEVEE renders agree on each of these, and Cycles panoramas of the recorded
benchmark worlds match the evaluation (``doc/angelo/oneshot_tests.rst``,
"How the sky is read").

Texture Coordinate's Camera and Window outputs depend on the camera: with
the scene's perspective camera (``scene_camera_view``) they are evaluated for
the directions it can see, and a direction behind it raises ``Unseen``.

Noise, Voronoi, Brick and Checker textures are computed exactly
(``texture_kernels``); what the evaluator cannot compute (a Sky or image
texture) is ``UNKNOWN``, or the ``Spread`` of colours a ramp or mix downstream
of it can show; the check then judges what is certain and reports the rest as
inconclusive (``shader_graph``). Nothing is written.
"""

import math

import shader_graph
from shader_graph import UNKNOWN, Shader, Spread, Unreadable, Unseen, active_output  # noqa: F401


class WorldContext(shader_graph.Context):
    """A background ray along ``view``: what the coordinate and ray nodes read in a World shader."""

    def __init__(self, view, camera=None):
        self.view = view
        self.back = tuple(-v for v in view)
        self.camera = camera

    def value(self, node, identifier):
        if node.type == "TEX_COORD":
            if identifier in {"Generated", "Object"} and getattr(node, "object", None) is None:
                return self.view
            if identifier in {"Normal", "Reflection"}:
                return self.back
            if identifier == "UV":
                return (0.0, 0.0, 0.0)
            if identifier in {"Camera", "Window"} and self.camera is not None:
                seen = self.camera(self.view)
                if seen is None:
                    raise Unseen()
                return tuple(float(v) for v in seen[identifier])
            return UNKNOWN
        if node.type == "NEW_GEOMETRY":
            if identifier == "Position":
                return self.view
            if identifier in {"Normal", "True Normal", "Incoming"}:
                return self.back
            return 0.0 if identifier == "Backfacing" else UNKNOWN
        return super().value(node, identifier)

    def generated(self):
        return self.view


class WorldGraph:
    """A World's shader graph as the renderer uses it, prepared once and evaluated per view direction.

    ``engine`` is ``scene.render.engine``; it picks the output (``active_output``). ``camera`` maps a view
    direction to Texture Coordinate's Camera and Window outputs, or to None where the camera cannot see it
    (``scene_camera_view``); without it those outputs are unknown.
    """

    def __init__(self, world, engine=None, camera=None):
        self.world = world
        self.camera = camera
        self.tree = getattr(world, "node_tree", None) if world is not None else None
        self.output = active_output(self.tree, engine) if self.tree is not None else None
        self.surface = self.output.inputs["Surface"] if self.output is not None else None
        self.feeds = shader_graph.incoming(self.tree) if self.tree is not None else {}

    def used_nodes(self):
        """Every node the active output's Surface reaches through links the renderer follows, nearest first."""
        if self.surface is None:
            return []
        return shader_graph.upstream(self.feeds, self.output.name, self.surface)[0]

    def radiance(self, view):
        """What a camera ray along ``view`` sees of the world: a ``Shader`` (its linear RGB), a ``Spread`` of
        them or UNKNOWN; raises ``Unseen`` for a camera-relative sky the camera cannot see there."""
        result = self.radiances([view])[0]
        if result is Unseen:
            raise Unseen()
        return result

    def radiances(self, views):
        """``radiance`` for many directions at once (textures are computed for all of them together); a
        direction the camera cannot see gives ``Unseen`` (the class) in its place."""
        if self.world is None:
            return [Shader((0.0, 0.0, 0.0)) for _ in views]
        if self.tree is None:   # A world without a node graph renders its own colour.
            return [Shader(list(self.world.color)[:3]) for _ in views]
        if self.surface is None:   # No output, or nothing linked: the background is black.
            return [Shader((0.0, 0.0, 0.0)) for _ in views]
        batch = shader_graph.Batch()
        graphs = [shader_graph.Graph(self.feeds, WorldContext(tuple(float(v) for v in view), self.camera),
                                     batch=batch) for view in views]
        results = []
        for graph in graphs:
            try:
                results.append(graph.input(self.surface, self.output.name))
            except Unseen:
                results.append(Unseen)
        return results


def direction(elevation_deg, azimuth_deg):
    """A unit world direction (+Z up) from degrees above the horizon and around it from +X."""
    elevation, azimuth = math.radians(elevation_deg), math.radians(azimuth_deg)
    return (math.cos(elevation) * math.cos(azimuth), math.cos(elevation) * math.sin(azimuth), math.sin(elevation))


def scene_camera_view(scene):
    """The scene camera's view of world directions, as Cycles and EEVEE compute camera-relative coordinates.

    For a background ray along ``view`` the renderer uses the point one unit along it from the camera: its
    Camera output is that point in camera space (+X right, +Y up, +Z forward) and its Window output the
    point's normalised frame position (0 to 1 across the frame, Z 0). Returns a function giving
    ``{"Camera", "Window"}`` per direction, or None where the camera looks away from it; None for a scene
    without a perspective camera. Runs in Blender only (``mathutils``, ``bpy_extras``).
    """
    camera = getattr(scene, "camera", None)
    if camera is None or camera.type != "CAMERA" or camera.data.type != "PERSP":
        return None
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector
    matrix = camera.matrix_world.normalized()
    origin, inverse = matrix.translation.copy(), matrix.inverted()

    def view_of(view):
        point = origin + Vector(view)
        local = inverse @ point
        if local.z >= 0.0:   # The camera looks along its -Z: this direction is beside or behind it.
            return None
        frame = world_to_camera_view(scene, camera, point)
        return {"Camera": (local.x, local.y, -local.z), "Window": (frame.x, frame.y, 0.0)}
    return view_of
