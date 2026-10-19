# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The removed-API table (script_api_removed) against Blender's own RNA and operators.

Every attribute in the table is a property or function of no RNA type, every removed enum value is refused by its
property and its replacement taken, every removed operator is absent and its replacement present, and the Bump node
has an ``invert`` property and no Invert input. A table entry that Blender still has would refuse working scripts:
this suite fails first. Background, local only; writes results.json under ANGELO_QA_DIR.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

try:
    from angelo_agent import script_api_removed as removed
except ImportError:   # A stock Blender run from the checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
    from angelo_agent import script_api_removed as removed

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-script-api-removed-qa"))
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


def rna_names():
    names = set()
    for attribute in dir(bpy.types):
        rna = getattr(getattr(bpy.types, attribute), "bl_rna", None)
        if rna is not None:
            names.update(p.identifier for p in rna.properties)
            names.update(f.identifier for f in rna.functions)
    return names


@case("no_removed_attribute_is_in_blender")
def _():
    present = sorted(set(removed.ATTRIBUTES) & rna_names())
    notes["attributes_present"] = present
    return not present


@case("removed_values_are_refused_and_their_replacements_taken")
def _():
    tree = bpy.data.node_groups.new("Removed API check", "ShaderNodeTree")
    sky = tree.nodes.new("ShaderNodeTexSky")
    results = []
    for (attribute, value), _instead in removed.VALUES.items():
        holder = sky if attribute == "sky_type" else bpy.context.scene.render
        try:
            setattr(holder, attribute, value)
            results.append(False)
        except TypeError:
            results.append(True)
    sky.sky_type = "MULTIPLE_SCATTERING"
    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    sky.aerosol_density = 1.0
    bpy.data.node_groups.remove(tree)
    return all(results)


@case("removed_operators_are_absent_and_their_replacements_present")
def _():
    def exists(path):
        module, name = path.split(".")
        return name in dir(getattr(bpy.ops, module))
    gone = [path for path in removed.OPERATORS if not exists(path)]
    replacements = ["wm.obj_export", "wm.obj_import", "wm.stl_export", "wm.stl_import", "wm.ply_export", "wm.ply_import",
                    "wm.usd_export", "wm.usd_import", "export_scene.gltf", "import_scene.gltf"]
    return len(gone) == len(removed.OPERATORS) and all(exists(path) for path in replacements)


@case("the_bump_node_inverts_by_a_property")
def _():
    tree = bpy.data.node_groups.new("Bump check", "ShaderNodeTree")
    bump = tree.nodes.new(removed.BUMP)
    ok = "Invert" not in [socket.name for socket in bump.inputs] and hasattr(bump, "invert")
    bump.invert = True
    bpy.data.node_groups.remove(tree)
    return ok


@case("the_named_replacements_exist")
def _():
    names = rna_names()
    return all(name in names for name in ("aerosol_density", "set_sharp_from_angle", "corner_normals",
                                          "use_raytracing"))


report = {"success": bool(checks) and all(checks.values()), "checks": checks, "notes": notes}
(OUT / "results.json").write_text(json.dumps(report, indent=2))
print("ANGELO_SCRIPT_API_REMOVED", json.dumps(report))
