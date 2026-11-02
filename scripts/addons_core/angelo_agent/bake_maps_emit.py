# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The temporary Emit graph one texture-set bake adds to a material, and its removal.

Contract: doc/angelo/long_jobs.rst, "Texture sets". What a channel is comes from
``bake_maps_graph``; this module wires it into an Emission shader on a Material
Output for Cycles only. Every bake is a Cycles session of its own (scene sync, BVH,
kernels: about 0.3 s each at 2K on an M4 Pro, plus its texels' own setup), so
scalar channels are packed three to a bake (``PACK``: one in each of R, G and B),
and ambient occlusion is read in the same bake through Cycles' Ambient Occlusion
node (``AO``) instead of an AO bake of its own. Main thread only.
"""

import bpy

from .bake_maps_graph import BLACK, Const, Graph, cycles_output, new_record  # noqa: F401  (new_record: steps)

PACK = "PACK"


def _scalar(graph, tree, key, ao):
    """One packed component: a scalar sum, ``AO``, or None (zero)."""
    if key is None:
        return Const(BLACK)
    if key == "AO":
        node = graph._new(tree, "ShaderNodeAmbientOcclusion")
        distance, samples = ao
        node.samples = samples
        node.inputs["Distance"].default_value = distance
        return node.outputs["AO"]
    if key == "HEIGHT":
        value = graph.height()
        return Const(BLACK) if value is None else value
    return graph.surface(key)


def _value(graph, tree, channel, ao):
    if isinstance(channel, tuple) and channel[0] == PACK:
        parts = [_scalar(graph, tree, key, ao) for key in channel[1]]
        if all(isinstance(part, Const) for part in parts):
            return Const(tuple(part.rgb[0] for part in parts))
        combine = graph._new(tree, "ShaderNodeCombineColor")
        for socket, part in zip(combine.inputs, parts):
            graph._link(tree, part, socket)   # A colour into a float input: its value (the sums are grey).
        return combine.outputs[0]
    if isinstance(channel, tuple):
        return graph._new(tree, "ShaderNodeNewGeometry").outputs[channel[1]]
    return _scalar(graph, tree, channel, ao)


def emit(material, channel, record, ao=(1.0, 16)):
    """Add the temporary graph that makes Cycles emit ``channel`` (a material channel, a sum, "HEIGHT", "AO", a
    ``("GEOMETRY", output)`` reading Cycles' Geometry node, or ``(PACK, (key, key, key))``: three scalars in R, G
    and B, None for zero) and make its output the one Cycles renders. ``ao``: the AO node's distance and samples.
    The changes go into ``record`` before each is made, so ``restore`` undoes a partial one."""
    tree = material.node_tree
    graph = Graph(material, build=True, record=record)
    record["active"] = [node.name for node in tree.nodes
                        if node.bl_idname == "ShaderNodeOutputMaterial" and node.is_active_output]
    value = _value(graph, tree, channel, ao)
    emission = graph._new(tree, "ShaderNodeEmission")
    graph._link(tree, value, emission.inputs["Color"])
    emission.inputs["Strength"].default_value = 1.0
    output = graph._new(tree, "ShaderNodeOutputMaterial")
    output.target = 'CYCLES'
    tree.links.new(emission.outputs[0], output.inputs["Surface"])
    output.is_active_output = True
    return record


def restore(record):
    """Remove what ``emit`` added to one material and give its outputs back their active flags."""
    material = bpy.data.materials.get(record.get("material") or "")
    tree = material.node_tree if material is not None else None
    if tree is not None:
        for name in record.get("nodes", ()):
            node = tree.nodes.get(name)
            if node is not None:
                tree.nodes.remove(node)
        for name in record.get("active") or ():
            node = tree.nodes.get(name)
            if node is not None:
                node.is_active_output = True
    for name in record.get("groups", ()):
        group = bpy.data.node_groups.get(name)
        if group is not None and group.users == 0:
            bpy.data.node_groups.remove(group)
