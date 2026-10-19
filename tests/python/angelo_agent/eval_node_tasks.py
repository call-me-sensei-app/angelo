# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The coverage set's node_edit tasks, graded by ``node_graph`` checks (``eval_live_tasks.build_coverage_tasks``).

Dependency-free. Checks match nodes and links by type, so the model's node
names do not matter; each prompt names only what the grade needs.
"""


def after_scatter(copy_quoted):
    """Edit the scattered points' Geometry Nodes graph (after ``scatter_points``)."""
    return [
        {"id": "scatter_edit", "kind": "edit", "requires_object": True,
         "prompt": ("In the existing 'Eval Scatter' Geometry Nodes modifier of %s, lower the Distribute Points on Faces "
                    "density to 2 and lift the points 0.1 m: insert a Set Position node with an offset of (0, 0, 0.1) "
                    "between the Distribute Points on Faces node and the group output.") % copy_quoted,
         "checks": [{"kind": "node_graph", "target": "$copy", "modifier": "Eval Scatter",
                     "nodes": [{"type": "GeometryNodeDistributePointsOnFaces", "inputs": {"Density": 2}},
                               {"type": "GeometryNodeSetPosition", "inputs": {"Offset": [0, 0, 0.1]}}],
                     "links": [{"from_type": "GeometryNodeDistributePointsOnFaces", "from_socket": "Points",
                                "to_type": "GeometryNodeSetPosition", "to_socket": "Geometry"},
                               {"from_type": "GeometryNodeSetPosition", "from_socket": "Geometry",
                                "to_type": "NodeGroupOutput", "to_socket": "Geometry"}], "tolerance": 0.0001}],
         "source": "native coverage: node_edit (Geometry Nodes: set an input, insert a node on a link)"},
    ]


def before_sketch(quoted):
    """A World graph edit, then a procedural material made and edited (before ``grease_pencil_sketch``)."""
    return [
        {"id": "world_nodes", "kind": "edit", "requires_object": True,
         "prompt": "In the World's node graph, drive the Background strength from a new Value node named 'Eval Gain' "
                   "set to 1.5.",
         "checks": [{"kind": "node_graph", "target": "$scene", "world": True,
                     "nodes": [{"name": "Eval Gain", "type": "ShaderNodeValue", "outputs": {"Value": 1.5}}],
                     "links": [{"from_node": "Eval Gain", "from_socket": "Value", "to_type": "ShaderNodeBackground",
                                "to_socket": "Strength"}], "tolerance": 0.0001}],
         "source": "native coverage: node_edit (World graph: add a node, set a value, link)"},
        {"id": "procedural_material", "kind": "edit", "requires_object": True,
         "prompt": ("Give every face of %s a new material named 'Eval Paint' whose Principled BSDF Base Color comes "
                    "from a Noise Texture's Color output.") % quoted,
         "checks": [{"kind": "material_assignment", "target": "$eval", "material": "Eval Paint", "all_faces": True},
                    {"kind": "node_graph", "target": "$eval", "material": "Eval Paint",
                     "links": [{"from_type": "ShaderNodeTexNoise", "from_socket": "Color",
                                "to_type": "ShaderNodeBsdfPrincipled", "to_socket": "Base Color"}]}],
         "source": "native coverage: shader_nodes (procedural material)"},
        {"id": "material_node_edit", "kind": "edit", "requires_object": True,
         "prompt": ("Edit the existing material 'Eval Paint' on %s: feed the Noise Texture's Fac into a new Color Ramp "
                    "whose Color drives the Base Color instead, and put the Noise Texture and the Color Ramp in a "
                    "frame named 'Pattern'.") % quoted,
         "checks": [{"kind": "node_graph", "target": "$eval", "material": "Eval Paint",
                     "nodes": [{"name": "Pattern", "type": "NodeFrame"},
                               {"type": "ShaderNodeTexNoise", "parent": "Pattern"},
                               {"type": "ShaderNodeValToRGB", "parent": "Pattern"}],
                     "links": [{"from_type": "ShaderNodeTexNoise", "from_socket": "Fac", "to_type": "ShaderNodeValToRGB",
                                "to_socket": "Fac"},
                               {"from_type": "ShaderNodeValToRGB", "from_socket": "Color",
                                "to_type": "ShaderNodeBsdfPrincipled", "to_socket": "Base Color"}]}],
         "source": "native coverage: node_edit (material: add, insert on a link, frame)"},
    ]
