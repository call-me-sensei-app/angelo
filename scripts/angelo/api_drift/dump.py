# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Record the Python API one Blender release exposes, as JSON (run INSIDE that Blender).

    blender --background --factory-startup --python scripts/angelo/api_drift/dump.py -- --out api-4.2.json

Runs unchanged on Blender 3.6 to 5.x (Python 3.10 or newer, no third-party modules). It records:

* every RNA struct in ``bpy.types``, including the bundled add-ons' property groups (Cycles, glTF):
  its base, and each property's type and fixed type, enum items (identifier, stored value, name,
  description) and UI name and description, and each function's parameters;
* every operator: idname, label, description and its properties;
* every node type's input and output sockets (name and identifier), read from a node created in a
  scratch tree of the right kind with its default settings;
* the public members of the Python modules scripts use beside ``bpy.types`` (mathutils, bmesh, bpy_extras,
  bpy.app, bpy.utils, bpy.props, gpu).

Nothing is saved; the scratch node trees are removed. Only names and their descriptions are recorded, never
a path, user or machine name.
"""

import argparse
import importlib
import json
import sys

import bpy

DUMP_SCHEMA = 1
NODE_TREE_PREFIXES = (
    ("ShaderNode", "ShaderNodeTree"),
    ("GeometryNode", "GeometryNodeTree"),
    ("FunctionNode", "GeometryNodeTree"),
    ("CompositorNode", "CompositorNodeTree"),
    ("TextureNode", "TextureNodeTree"),
)
PYTHON_MODULES = ("mathutils", "mathutils.geometry", "mathutils.noise", "mathutils.bvhtree", "mathutils.kdtree",
                  "bmesh", "bmesh.ops", "bmesh.types", "bmesh.utils", "bpy_extras", "bpy_extras.anim_utils",
                  "bpy_extras.object_utils", "bpy_extras.mesh_utils", "bpy_extras.io_utils",
                  "bpy_extras.view3d_utils", "bpy_extras.image_utils", "bpy_extras.node_shader_utils",
                  "bpy.app", "bpy.utils", "bpy.props", "gpu", "gpu.types", "gpu.shader", "gpu.state",
                  "gpu_extras.batch", "gpu_extras.presets")


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="JSON file to write")
    return parser.parse_args(argv)


def enum_items(prop):
    try:
        return [[item.identifier, item.value, item.name, item.description] for item in prop.enum_items]
    except (AttributeError, TypeError):
        return []


def property_record(prop):
    record = {"type": prop.type, "name": prop.name, "description": prop.description}
    if prop.type in {"POINTER", "COLLECTION"} and getattr(prop, "fixed_type", None) is not None:
        record["fixed_type"] = prop.fixed_type.identifier
    if prop.type == "ENUM":
        record["items"] = enum_items(prop)
        record["is_enum_flag"] = bool(prop.is_enum_flag)
    if prop.type in {"INT", "FLOAT", "BOOLEAN"}:
        record["array_length"] = getattr(prop, "array_length", 0)
    return record


def function_record(function):
    return {"description": function.description,
            "parameters": [parameter.identifier for parameter in function.parameters]}


def struct_records():
    structs = {}
    for attribute in dir(bpy.types):
        cls = getattr(bpy.types, attribute, None)
        rna = getattr(cls, "bl_rna", None)
        if rna is None or getattr(rna, "identifier", None) != attribute:
            continue
        try:
            structs[attribute] = {
                "base": rna.base.identifier if rna.base else None,
                "name": rna.name,
                "properties": {prop.identifier: property_record(prop) for prop in rna.properties
                               if prop.identifier != "rna_type"},
                "functions": {function.identifier: function_record(function) for function in rna.functions},
            }
        except (AttributeError, RuntimeError, TypeError) as error:
            structs[attribute] = {"error": "%s: %s" % (type(error).__name__, error)}
    return structs


def operator_records():
    operators = {}
    for module_name in dir(bpy.ops):
        module = getattr(bpy.ops, module_name, None)
        if module is None or module_name.startswith("_"):
            continue
        for name in dir(module):
            if name.startswith("_"):
                continue
            try:
                rna = getattr(module, name).get_rna_type()
            except (AttributeError, KeyError, RuntimeError):
                continue
            operators["%s.%s" % (module_name, name)] = {
                "label": rna.name, "description": rna.description,
                "properties": {prop.identifier: property_record(prop) for prop in rna.properties
                               if prop.identifier != "rna_type"},
            }
    return operators


def socket_list(sockets):
    return [[socket.name, socket.identifier] for socket in sockets]


def node_records():
    trees, nodes = {}, {}
    for attribute in dir(bpy.types):
        cls = getattr(bpy.types, attribute, None)
        if not isinstance(cls, type) or not issubclass(cls, bpy.types.Node):
            continue
        tree_type = next((tree for prefix, tree in NODE_TREE_PREFIXES if attribute.startswith(prefix)), None)
        if tree_type is None:
            continue
        if tree_type not in trees:
            try:
                trees[tree_type] = bpy.data.node_groups.new("api drift " + tree_type, tree_type)
            except (RuntimeError, TypeError) as error:
                trees[tree_type] = None
                nodes["<tree " + tree_type + ">"] = {"error": str(error)}
        tree = trees[tree_type]
        if tree is None:
            continue
        try:
            node = tree.nodes.new(attribute)
        except (RuntimeError, TypeError):
            continue  # Abstract bases and nodes this tree does not accept.
        nodes[attribute] = {"name": node.bl_rna.name, "tree": tree_type,
                            "inputs": socket_list(node.inputs), "outputs": socket_list(node.outputs)}
        tree.nodes.remove(node)
    for tree in trees.values():
        if tree is not None:
            bpy.data.node_groups.remove(tree)
    return nodes


def module_records():
    modules = {}
    for module_name in PYTHON_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError as error:
            modules[module_name] = {"error": str(error)}
            continue
        members = {}
        for name in dir(module):
            if name.startswith("_"):
                continue
            value = getattr(module, name, None)
            members[name] = sorted(member for member in dir(value) if not member.startswith("_")) \
                if isinstance(value, type) else None
        modules[module_name] = {"members": members}
    return modules


def main():
    args = parse_args()
    version = ".".join(str(part) for part in bpy.app.version[:2])
    dump = {
        "schema": DUMP_SCHEMA,
        "version": version,
        "version_string": bpy.app.version_string,
        "build_hash": bpy.app.build_hash.decode("ascii", "replace") if isinstance(bpy.app.build_hash, bytes)
        else str(bpy.app.build_hash),
        "structs": struct_records(),
        "operators": operator_records(),
        "nodes": node_records(),
        "modules": module_records(),
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(dump, handle, indent=0, sort_keys=True)
    print("ANGELO_API_DUMP", json.dumps({"version": version, "structs": len(dump["structs"]),
                                         "operators": len(dump["operators"]), "nodes": len(dump["nodes"])}))


main()
