# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Errors for names Blender 4.x or 5.x renamed or removed name the 5.2 replacement; nothing else changes.

Agent scripts keep writing names that Blender 5.0 renamed or removed: ``sky_type = 'NISHITA'``,
``render.engine = 'BLENDER_EEVEE_NEXT'``, ``sky.dust_density`` and ``action.fcurves`` were the names of 36
of 93 recorded scripts that raised. The stock errors list the valid enum items but do not say which one
replaced the old name, and Python's spelling guess for ``dust_density`` is the unrelated ``air_density``.
In Angelo (``bpy_angelo_renamed_api.cc``) such an error gains one sentence naming the replacement.

For every listed name this suite checks, in the compiled app:

* the old name still fails exactly where stock Blender fails, with the same exception type, and changes
  nothing (no alias);
* the message is stock's message plus ". " and the replacement, and the replacement really exists in 5.2;
* an ``AttributeError`` keeps its ``name`` and gets no "Did you mean" guess; ``obj`` is None;

and, for names and places that are not listed, that the message, ``obj`` and Python's guess are stock's.
Where the tree also has the host's ``api_notes`` table (``script_api_notes``), each of its rows must name
only replacements that Blender's error for that name also names.

Background only. No add-on, provider, account or paid call. Nothing is saved.
"""

import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
from bpy_extras import anim_utils

assert "--factory-startup" in sys.argv, "Use a disposable factory-startup Blender instance"
assert bpy.app.background, "Run with --background"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-renamed-api-errors"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "checks": {}, "messages": {}, "paid_requests": 0, "blender": bpy.app.version_string,
          "binary": bpy.app.binary_path}

# The sentence each listed name's error gains (bpy_angelo_renamed_api.cc), and the 5.2 names it gives.
NISHITA_HINT = ("Blender 5.0 renamed 'NISHITA' to 'SINGLE_SCATTERING' (the same model: files saved with 'NISHITA' "
                "open with it); the new default is 'MULTIPLE_SCATTERING'")
EEVEE_HINT = "Blender 5.0 renamed 'BLENDER_EEVEE_NEXT' to 'BLENDER_EEVEE'"
DUST_HINT = "Blender 5.0 renamed 'dust_density' to 'aerosol_density'"
CHANNELBAG_HINT = ("Blender 5.0 removed Action.{0}: {1} in a slot's channelbag, for example "
                   "bpy_extras.anim_utils.action_get_channelbag_for_slot(action, slot).{0}, where slot is the "
                   "animated ID's animation_data.action_slot")
FCURVES_HINT = CHANNELBAG_HINT.format("fcurves", "F-Curves are")
GROUPS_HINT = CHANNELBAG_HINT.format("groups", "F-Curve groups are")
PRINCIPLED_RENAMES = {
    "Subsurface": "Subsurface Weight",
    "Specular": "Specular IOR Level",
    "Transmission": "Transmission Weight",
    "Clearcoat": "Coat Weight",
    "Clearcoat Roughness": "Coat Roughness",
    "Clearcoat Normal": "Coat Normal",
    "Sheen": "Sheen Weight",
    "Emission": "Emission Color",
}


def principled_hint(old):
    return f"Blender 4.0 renamed the Principled BSDF input '{old}' to '{PRINCIPLED_RENAMES[old]}'"


def check(name, value):
    REPORT["checks"][name] = bool(value)  # Every case runs; the suite fails at the end.


def raised(name, call):
    """The exception ``call()`` raised (None when it returned), its message recorded under ``name``."""
    try:
        call()
    except Exception as error:  # noqa: BLE001 - the type is what is checked.
        REPORT["messages"][name] = f"{type(error).__name__}: {error}"
        return error
    REPORT["messages"][name] = None
    return None


def shown(error):
    """The exception as Python prints it, with its spelling guess when it computes one."""
    return "".join(traceback.format_exception_only(error)).strip()


def stock_enum_text(error, unlisted, value):
    """Stock's text for ``value``, from the error of an unlisted value on the same property.

    The item list comes from Blender itself (the render engine's items are dynamic), so the listed value's
    text can be compared with stock's exactly.
    """
    return str(error).replace(f'"{unlisted}"', f'"{value}"', 1)


def assign(owner, attribute, value):
    return lambda: setattr(owner, attribute, value)


def enum_cases(sky, scene):
    unlisted = raised("sky_type_unlisted", assign(sky, "sky_type", "NISHITA_TYPO"))
    items = sky.bl_rna.properties["sky_type"].enum_items
    listing = ", ".join(repr(item.identifier) for item in items)
    check("unlisted_enum_item_is_stock", type(unlisted) is TypeError and str(unlisted)
          == f'bpy_struct: item.attr = val: enum "NISHITA_TYPO" not found in ({listing})')
    error = raised("sky_type_nishita", assign(sky, "sky_type", "NISHITA"))
    check("nishita_raises_type_error", type(error) is TypeError)
    check("nishita_message_is_stock_plus_hint", error is not None and unlisted is not None and str(error)
          == f"{stock_enum_text(unlisted, 'NISHITA_TYPO', 'NISHITA')}. {NISHITA_HINT}")
    check("nishita_changes_nothing", sky.sky_type == "MULTIPLE_SCATTERING")
    # The hint's facts: no 'NISHITA' item; 'SINGLE_SCATTERING' holds NISHITA's stored value (2 in Blender 4.x),
    # so a 4.x file opens with it; a new Sky Texture uses 'MULTIPLE_SCATTERING' (checked just above).
    check("nishita_hint_facts", "NISHITA" not in items.keys() and items["SINGLE_SCATTERING"].value == 2)
    sky.sky_type = "SINGLE_SCATTERING"
    check("nishita_replacement_assigns", sky.sky_type == "SINGLE_SCATTERING")

    render = scene.render
    engine = render.engine
    unlisted = raised("engine_unlisted", assign(render, "engine", "BLENDER_EEVEE_NEXT_TYPO"))
    check("unlisted_engine_is_stock", type(unlisted) is TypeError and "Blender 5.0" not in str(unlisted)
          and str(unlisted).startswith('bpy_struct: item.attr = val: enum "BLENDER_EEVEE_NEXT_TYPO" not found in (')
          and "'BLENDER_EEVEE'" in str(unlisted))
    error = raised("engine_eevee_next", assign(render, "engine", "BLENDER_EEVEE_NEXT"))
    check("eevee_next_raises_type_error", type(error) is TypeError)
    check("eevee_next_message_is_stock_plus_hint", error is not None and unlisted is not None and str(error)
          == f"{stock_enum_text(unlisted, 'BLENDER_EEVEE_NEXT_TYPO', 'BLENDER_EEVEE_NEXT')}. {EEVEE_HINT}")
    check("eevee_next_changes_nothing", render.engine == engine)
    render.engine = "BLENDER_EEVEE"
    check("eevee_replacement_assigns", render.engine == "BLENDER_EEVEE")
    render.engine = engine

    # A listed name is bound to its property: 'NISHITA' given to the render engine is just unknown there.
    error = raised("engine_nishita", assign(render, "engine", "NISHITA"))
    check("listed_name_on_another_property_is_stock", type(error) is TypeError and unlisted is not None
          and str(error) == stock_enum_text(unlisted, "BLENDER_EEVEE_NEXT_TYPO", "NISHITA"))


def attribute_error_checks(prefix, error, owner, name, hint):
    stock = f"'{type(owner).__name__}' object has no attribute '{name}'"
    check(f"{prefix}_raises_attribute_error", type(error) is AttributeError)
    check(f"{prefix}_message_is_stock_plus_hint", error is not None and str(error) == f"{stock}. {hint}")
    check(f"{prefix}_keeps_name", getattr(error, "name", None) == name)
    check(f"{prefix}_no_spelling_guess", error is not None and getattr(error, "obj", "unset") is None
          and "Did you mean" not in shown(error) and shown(error) == f"AttributeError: {stock}. {hint}")


def attribute_cases(sky):
    attribute_error_checks("dust_get", raised("dust_density_get", lambda: sky.dust_density), sky, "dust_density",
                           DUST_HINT)
    before = sky.aerosol_density
    attribute_error_checks("dust_set", raised("dust_density_set", assign(sky, "dust_density", 4.0)), sky,
                           "dust_density", DUST_HINT)
    check("dust_set_changes_nothing", sky.aerosol_density == before)
    check("dust_hasattr_false", not hasattr(sky, "dust_density") and getattr(sky, "dust_density", None) is None)
    check("dust_replacement_exists", "aerosol_density" in sky.bl_rna.properties.keys())

    # Not listed: stock text, stock ``obj`` and Python's own guess (here the right one, air_density).
    error = raised("unlisted_attribute", lambda: sky.air_densty)
    check("unlisted_attribute_is_stock", type(error) is AttributeError
          and str(error) == "'ShaderNodeTexSky' object has no attribute 'air_densty'"
          and error.obj is sky and "Did you mean: 'air_density'?" in shown(error))
    error = raised("unlisted_attribute_set", assign(sky, "air_densty", 1.0))
    check("unlisted_attribute_set_is_stock", type(error) is AttributeError
          and str(error) == "'ShaderNodeTexSky' object has no attribute 'air_densty'" and error.obj is sky)


def action_cases():
    obj = bpy.data.objects["Cube"]
    obj.keyframe_insert("location", frame=1)
    action = obj.animation_data.action
    attribute_error_checks("fcurves", raised("action_fcurves", lambda: action.fcurves), action, "fcurves",
                           FCURVES_HINT)
    attribute_error_checks("groups", raised("action_groups", lambda: action.groups), action, "groups", GROUPS_HINT)
    check("action_hasattr_false", not hasattr(action, "fcurves") and not hasattr(action, "groups"))
    bag = anim_utils.action_get_channelbag_for_slot(action, obj.animation_data.action_slot)
    check("hinted_channelbag_route_works", bag is not None and len(bag.fcurves) == 3 and len(bag.groups) == 1)
    # Bound to Action: a type without ``fcurves`` that is not an Action keeps the stock error.
    error = raised("object_fcurves", lambda: obj.fcurves)
    check("listed_attribute_on_another_type_is_stock", type(error) is AttributeError
          and str(error) == "'Object' object has no attribute 'fcurves'" and error.obj is obj)
    obj.animation_data_clear()
    bpy.data.actions.remove(action)


def collection_cases(tree):
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    inputs = principled.inputs
    for old, new in PRINCIPLED_RENAMES.items():
        key = old.lower().replace(" ", "_")
        error = raised(f"principled_{key}", lambda: inputs[old])
        check(f"principled_{key}_raises_key_error", type(error) is KeyError)
        check(f"principled_{key}_message_is_stock_plus_hint", error is not None and error.args
              == (f'bpy_prop_collection[key]: key "{old}" not found. {principled_hint(old)}',))
        check(f"principled_{key}_replacement_exists", new in inputs.keys() and old not in inputs.keys()
              and inputs.get(old) is None)
    error = raised("principled_unlisted", lambda: inputs["Specular Typo"])
    check("unlisted_key_is_stock", type(error) is KeyError
          and error.args == ('bpy_prop_collection[key]: key "Specular Typo" not found',))
    diffuse = tree.nodes.new("ShaderNodeBsdfDiffuse")
    error = raised("diffuse_specular", lambda: diffuse.inputs["Specular"])
    check("listed_key_on_another_node_is_stock", type(error) is KeyError
          and error.args == ('bpy_prop_collection[key]: key "Specular" not found',))
    error = raised("principled_outputs_specular", lambda: principled.outputs["Specular"])
    check("listed_key_in_another_collection_is_stock", type(error) is KeyError
          and error.args == ('bpy_prop_collection[key]: key "Specular" not found',))
    tree.nodes.remove(diffuse)
    tree.nodes.remove(principled)


def host_notes_cases(sky, scene):
    """The host's ``api_notes`` table, where this tree has it, names replacements Blender's error also names.

    ``angelo_agent.script_api_notes.RENAMED`` holds a note for a listed name a
    script uses even inside try/except, where no error is shown. Blender's table (``bpy_angelo_renamed_api.cc``)
    is the reference for 5.2's replacements: every row of the host's must be a name Blender's error explains,
    and every name after the note's "use" must appear in that error. Skipped where the module is absent.
    """
    if not (ROOT / "scripts/addons_core/angelo_agent/script_api_notes.py").exists():
        REPORT["host_api_notes"] = "not in this tree"
        return
    sys.path.insert(0, str(ROOT / "scripts/addons_core"))
    from angelo_agent import script_api_notes  # noqa: E402
    obj = bpy.data.objects["Cube"]
    obj.keyframe_insert("location", frame=1)
    action = obj.animation_data.action
    owners = {"ShaderNodeTexSky": sky, "RenderSettings": scene.render, "Action": action}
    rows = []
    for row in script_api_notes.RENAMED:
        type_name, _, prop = row["owner"].partition(".")
        owner = owners.get(type_name)
        if "string" in row:
            old = row["string"]
            error = raised(f"host_note_{old}", assign(owner, prop, old)) if owner is not None and prop else None
        else:
            old = row["attribute"]
            error = raised(f"host_note_{old}", lambda: getattr(owner, old)) if owner is not None else None
        # Only the sentence Blender's error gained counts: stock's item list already holds the enum's names.
        message = str(error) if error is not None else ""
        hint = message[message.find(". Blender "):] if ". Blender " in message else ""
        _, _, clause = row["note"].partition(": use ")
        names = re.findall(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", clause)
        missing = [name for name in names if not re.search(rf"(?<!\w){re.escape(name)}(?!\w)", hint)]
        rows.append({"name": old, "names": names, "missing": missing})
        check(f"host_note_{old}_explained_by_blender", owner is not None and bool(hint) and names and not missing)
    REPORT["host_api_notes"] = rows
    obj.animation_data_clear()
    bpy.data.actions.remove(action)


def run():
    scene = bpy.context.scene
    world = bpy.data.worlds.new("Renamed API errors")
    material = bpy.data.materials.new("Renamed API errors")
    for owner in (world, material):
        if owner.node_tree is None:
            owner.use_nodes = True
    sky = world.node_tree.nodes.new("ShaderNodeTexSky")
    steps = ((enum_cases, (sky, scene)), (attribute_cases, (sky,)), (action_cases, ()),
             (collection_cases, (material.node_tree,)), (host_notes_cases, (sky, scene)))
    for step, args in steps:
        try:
            step(*args)
        except Exception:  # noqa: BLE001 - recorded, and the suite fails.
            REPORT.setdefault("error", {})[step.__name__] = traceback.format_exc()
    bpy.data.worlds.remove(world)
    bpy.data.materials.remove(material)


def finish():
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values()) and "error" not in REPORT
    REPORT["failed"] = sorted(name for name, ok in REPORT["checks"].items() if not ok)
    (OUT / "renamed-api-errors.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_RENAMED_API_ERRORS", json.dumps({"success": REPORT["success"], "checks": len(REPORT["checks"]),
                                                   "failed": REPORT["failed"]}), flush=True)


run()
finish()
