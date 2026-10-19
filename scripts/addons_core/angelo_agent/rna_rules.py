# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic, RNA-driven rules for natively written settings.

One set of rules serves every generic kind (``property_set``,
``property_keyframe``, modifier, constraint and node settings) and the
``WRITABLE`` description of ``angelo_blender_api``: a dotted path is walked
attribute by attribute through nested settings structs of the installed RNA,
and the value is checked against the leaf property's definition (type, array
length, hard range, enum items by assignment, string length, object pointer)
before it is written and read back.

Deny-list, fail closed:

* path syntax: lowercase identifiers only, no indexes, keys or custom
  properties, at most five segments;
* file paths, scripts, drivers and expressions (by subtype and by name);
* Python-defined (runtime) properties, whose get/set/update callbacks run
  Python, except the few listed in ``PROVEN_RUNTIME``;
* read-only, hidden and collection properties, multi-dimensional arrays,
  datablock names and datablock pointers other than objects;
* editor, tool, animation and library state (``DENIED_SEGMENTS``,
  ``DENIED_STRUCTS``), and other bones (``bone_set`` writes the bone it names).

The only datablock hops are an object's ``data`` and ``active_material`` and
the scene's ``world``. The pure helpers need no ``bpy``; the RNA ones take
structs from their callers, so host tests use stand-ins.
"""

from .refusal_codes import refusal_code, refusal_details

import math
import re
from itertools import islice

MAX_SEGMENTS = 5
MAX_STRING = 1024
PATH_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,4}$"
_PATH = re.compile(PATH_PATTERN)
SCENE, OBJECT = "SCENE", "OBJECT"
# Top-level Scene settings a scene-target path may start with: every writable one of Blender 5.2's Scene RNA except
# ``display`` (also an Object struct, so the Workbench ``display`` settings stay out), ``frame_float`` and
# ``frame_subframe`` (sub-frame forms of the current frame), the ID flags an Object also has (``use_fake_user``,
# ``tag``...) and the datablock pointers below. ``frame_current`` is also every program's bound frame
# (program_context): writing it moves every object's revision, and the settings revision leaves it out
# (scene_settings). Missing, a model's "frame_current" made its scene property_set an object's edit.
SCENE_ROOTS = frozenset({
    "camera", "world", "frame_current", "frame_start", "frame_end", "frame_step", "time_jump_unit", "time_jump_delta",
    "lock_frame_selection_to_range", "allow_preroll", "use_preview_range", "frame_preview_start", "frame_preview_end",
    "show_subframe", "show_keys_from_selected_only", "use_stamp_note", "use_custom_simulation_range",
    "simulation_frame_start", "simulation_frame_end", "sync_mode", "playback_loop_mode", "use_nodes",
    "unit_settings", "gravity", "use_gravity", "render", "safe_areas", "use_audio", "use_audio_scrub",
    "audio_doppler_speed", "audio_doppler_factor", "audio_distance_model", "audio_volume", "view_settings",
    "display_settings", "sequencer_colorspace_settings", "eevee", "grease_pencil_settings", "hydra", "cycles"})
# The datablock pointers a path may cross: (owner, first segment) -> family.
ID_HOPS = {(OBJECT, "data"): "data", (OBJECT, "active_material"): "material", (SCENE, "world"): "world"}
DENIED_SEGMENTS = frozenset({
    "animation_data", "library", "library_weak_reference", "override_library", "asset_data", "preview", "original",
    "rna_type", "id_data", "tool_settings", "sequence_editor", "cursor", "annotation", "active_clip",
    "background_set", "compositing_node_group", "rigidbody_world", "use_autopack", "node_tree", "shape_keys",
    "texture", "image", "font", "parent", "users", "tag", "is_evaluated", "session_uid"})
# Object channels the transform, parent_set and keyframe kinds own (with their rotation-mode rules).
TRANSFORM_PATHS = frozenset({"location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "rotation_mode",
                             "scale", "delta_location", "delta_rotation_euler", "delta_rotation_quaternion",
                             "delta_scale", "parent_type", "parent_bone", "parent_vertices", "track_axis", "up_axis",
                             "dimensions"})
_FILE_WORDS = ("filepath", "file_path", "directory", "filename", "dirpath")
_SCRIPT_WORDS = ("driver", "expression", "script", "python", "text_block")
DENIED_STRUCTS = frozenset({"ToolSettings", "AnimData", "Library", "IDOverrideLibrary", "AssetMetaData",
                            "ImagePreview", "SequenceEditor", "View3DCursor", "RigidBodyWorld", "PointCache",
                            "Preferences", "AddonPreferences", "KeyingSet", "TimelineMarker",
                            # Another bone (B-Bone custom handles, custom-shape transforms): a
                            # bone_set path writes only the bone it names.
                            "Bone", "PoseBone", "EditBone"})
FILE_SUBTYPES = frozenset({"FILE_PATH", "DIR_PATH", "FILE_NAME", "BYTE_STRING", "PASSWORD"})
# Python-defined settings admitted one by one: Cycles' scene render settings as Blender 5.2 defines them
# (cycles/properties.py). Writing one of CYCLES_PLAIN runs no Python: it has no get, set or update callback and
# its enum items are a constant. The three in CYCLES_PASS_UPDATE have Cycles' own update_render_passes, which
# asks the view layer to rebuild its render-pass list and nothing else. test_blender_cycles_settings.py audits
# the installed definitions against this, and settings_revision covers every admitted setting.
# Not admitted: preview_pause (its callback redraws an editor area), shading_system (turns on Open Shading
# Language script nodes), sampling_pattern, denoiser and preview_denoiser (enum items computed from the
# preferences and the device list), dicing_camera (a pointer with a poll callback), bake_type (bake setup) and
# the debug_ switches. Cycles settings of materials, lights, objects, view layers and the World stay refused.
CYCLES_PLAIN = tuple("""
    samples preview_samples use_sample_subset sample_offset sample_subset_length time_limit scrambling_distance
    preview_scrambling_distance auto_scrambling_distance use_layer_samples light_sampling_threshold
    use_adaptive_sampling adaptive_threshold adaptive_min_samples use_preview_adaptive_sampling
    preview_adaptive_threshold preview_adaptive_min_samples direct_light_sampling_type use_light_tree
    min_light_bounces min_transparent_bounces caustics_reflective caustics_refractive blur_glossy
    denoising_prefilter denoising_quality denoising_input_passes denoising_use_gpu use_preview_denoising
    preview_denoising_prefilter preview_denoising_quality preview_denoising_input_passes
    preview_denoising_start_sample preview_denoising_use_gpu use_deterministic_guiding guiding_distribution_type
    guiding_directional_sampling_type use_surface_guiding surface_guiding_probability use_volume_guiding
    guiding_training_samples volume_guiding_probability use_guiding_direct_light use_guiding_mis_weights
    guiding_roughness_threshold max_bounces diffuse_bounces glossy_bounces transmission_bounces volume_bounces
    transparent_max_bounces volume_step_rate volume_preview_step_rate volume_max_steps volume_biased dicing_rate
    preview_dicing_rate max_subdivisions offscreen_dicing_scale film_exposure film_transparent_glass
    film_transparent_roughness pixel_filter_type filter_width use_pixel_jitter seed use_animated_seed
    sample_clamp_direct sample_clamp_indirect use_camera_cull camera_cull_margin use_distance_cull
    distance_cull_margin rolling_shutter_type rolling_shutter_duration texture_resolution texture_resolution_render
    texture_limit texture_limit_render use_fast_gi fast_gi_method ao_bounces ao_bounces_render use_auto_tile
    tile_size""".split())
CYCLES_PASS_UPDATE = ("device", "use_denoising", "use_guiding")
PROVEN_RUNTIME = frozenset("cycles." + name for name in CYCLES_PLAIN + CYCLES_PASS_UPDATE)
LEAF_TYPES = frozenset({"BOOLEAN", "INT", "FLOAT", "ENUM", "STRING", "POINTER"})


_REFUSAL_POLICY = {
    **dict.fromkeys(("EMPTY", "MIXED_OWNERS", "OWNER_TYPE_MISMATCH", "OBJECT_TYPE_MISMATCH", "RNA_TYPE_MISMATCH",
                     "RNA_ARRAY_LENGTH", "RNA_RANGE", "STRING_TOO_LONG", "MATERIAL_SHARED", "ALL_USERS_MATERIAL_ONLY",
                     "ACTIVE_SLOT_CONFLICT"), "VALIDATION"),
    **dict.fromkeys(("CUSTOM_PROPERTY_OR_INDEX", "FILE_PATH", "SCRIPT_OR_DRIVER", "NOT_ALLOWED", "ID_POINTER",
                     "PROPERTY_MISSING_IN_BUILD", "RUNTIME_PROPERTY", "NOT_A_SETTING", "RNA_READONLY", "NOT_ANIMATABLE",
                     "SHARED_OUTSIDE_SCENE", "SHARING_UNKNOWN", "LINKED_MATERIAL"), "UNSUPPORTED"),
    "VALUE_NOT_RETAINED": "INTERNAL_ERROR",
}


class PropertyPathError(ValueError):
    """A natively written setting outside the generic rules or the installed RNA definition.

    ``code`` is stable for callers and tests: FILE_PATH, SCRIPT_OR_DRIVER,
    CUSTOM_PROPERTY_OR_INDEX, ID_POINTER, NOT_ALLOWED, MIXED_OWNERS, EMPTY,
    PROPERTY_MISSING_IN_BUILD, RUNTIME_PROPERTY, NOT_A_SETTING,
    OWNER_TYPE_MISMATCH, RNA_TYPE_MISMATCH, RNA_READONLY, RNA_ARRAY_LENGTH,
    RNA_RANGE, ENUM_ITEM_UNAVAILABLE, STRING_TOO_LONG, OBJECT_TYPE_MISMATCH,
    NOT_ANIMATABLE, VALUE_NOT_RETAINED, and for an active material other
    users share or a library provides (``material_sharing``) MATERIAL_SHARED,
    SHARED_OUTSIDE_SCENE, ALL_USERS_MATERIAL_ONLY, SHARING_UNKNOWN and
    LINKED_MATERIAL, and ACTIVE_SLOT_CONFLICT (a slot change beside them).
    """

    def __init__(self, code, path, detail, *, refusal_policy=None, details=None):
        self.code, self.path = code, path
        self.refusal_code = refusal_policy or _REFUSAL_POLICY.get(code, "UNKNOWN")
        self.details = dict(details or {})
        super().__init__("property_set %s: %s: %s" % (code, str(path)[:96], detail))


NODE_PLACE = "location"  # A node's place in the editor (screen_path(struct="NODE")).


def screen_path(path, struct=False):
    """Raise for a path the deny-list refuses before any RNA is consulted (pure).

    ``struct``: a path on a settings struct (a modifier, constraint, node, particle or F-Curve modifier), not on an
    object: its ``track_axis`` or ``rotation_mode`` is a setting of its own, never the object's transform channel
    (otherwise it was refused as one). ``"NODE"``: a node's, whose ``location`` is its
    place in the editor, editor state like its selection (``NODE_PLACE``)."""
    text = str(path)
    if any(mark in text for mark in "[]\"'"):
        raise PropertyPathError("CUSTOM_PROPERTY_OR_INDEX", path, "custom properties and indexed collections are not addressable")
    segments = text.lower().split(".")
    if any(word in segment for segment in segments for word in _FILE_WORDS) or any(
            segment == "path" or (segment.endswith("_path") and not segment.startswith(("use_", "show_")))
            for segment in segments):
        raise PropertyPathError("FILE_PATH", path, "file paths are external effects; use the file tools or reviewed Python")
    if any(word in segment for segment in segments for word in _SCRIPT_WORDS) or "text" in segments:
        raise PropertyPathError("SCRIPT_OR_DRIVER", path, "scripts, drivers and their expressions are never set natively")
    if not _PATH.match(text) or len(segments) > MAX_SEGMENTS:
        raise PropertyPathError("NOT_ALLOWED", path, "use a dotted settings path of lowercase identifiers")
    denied = [segment for segment in segments if segment in DENIED_SEGMENTS]
    if denied:
        code = "ID_POINTER" if denied[0] in {"texture", "image", "font", "parent", "node_tree", "shape_keys"} else "NOT_ALLOWED"
        raise PropertyPathError(code, path, "%s is editor, animation, library or datablock state" % denied[0])
    if segments[-1] == "name":
        raise PropertyPathError("NOT_ALLOWED", path, "names have their own action kinds")
    if any(segment.startswith("bl_") for segment in segments):
        raise PropertyPathError("NOT_ALLOWED", path, "type registration fields are not settings")
    if struct == "NODE" and text == NODE_PLACE:
        raise PropertyPathError("NOT_ALLOWED", path, "a node's place is editor state; the kernel lays out new nodes")
    if text in TRANSFORM_PATHS and not struct:
        raise PropertyPathError("NOT_ALLOWED", path, "transforms, parenting and matrices use the transform and parent_set kinds")
    if text in {hop for _owner, hop in ID_HOPS}:
        raise PropertyPathError("ID_POINTER", path, "datablock pointers change ownership; use a dedicated action kind")
    return text


def owner_of(path):
    """SCENE for a scene-settings path (by its first segment), OBJECT otherwise."""
    return SCENE if str(path).split(".", 1)[0] in SCENE_ROOTS else OBJECT


def family(path):
    """The datablock a path writes: None (the owner itself), data, material or world."""
    return ID_HOPS.get((owner_of(path), str(path).split(".", 1)[0])) if "." in str(path) else None


def _is_id(value):
    return value is not None and hasattr(value, "users") and hasattr(value, "session_uid") and hasattr(value, "is_evaluated")


def _struct_name(value):
    rna = getattr(value, "bl_rna", None)
    return getattr(rna, "identifier", type(value).__name__)


def resolve(owner, path, owner_kind=None):
    """Walk ``path`` from ``owner`` to (holder struct, leaf RNA property), applying the deny-list."""
    screen_path(path, struct=owner_kind == "STRUCT")
    owner_kind = owner_kind or owner_of(path)
    *parents, name = path.split(".")
    holder = owner
    for index, segment in enumerate(parents):
        if getattr(holder, "bl_rna", None) is None:  # A plain test stand-in: attribute access only.
            holder = getattr(holder, segment)
            continue
        prop = holder.bl_rna.properties.get(segment)
        if prop is None:
            raise PropertyPathError("PROPERTY_MISSING_IN_BUILD", path, "%s has no %s" % (_struct_name(holder), segment))
        if prop.type != "POINTER":
            raise PropertyPathError("NOT_A_SETTING", path, "%s is not a settings struct" % segment)
        if getattr(prop, "is_runtime", False) and path not in PROVEN_RUNTIME:
            raise PropertyPathError("RUNTIME_PROPERTY", path, "Python-defined settings may run Python callbacks")
        child = getattr(holder, segment)
        if child is None:
            raise PropertyPathError("PROPERTY_MISSING_IN_BUILD", path, "the owner has no " + segment)
        if _is_id(child) and not (index == 0 and (owner_kind, segment) in ID_HOPS):
            raise PropertyPathError("ID_POINTER", path, "only an object's data and active material and the scene's World are reachable")
        if _struct_name(child) in DENIED_STRUCTS:
            raise PropertyPathError("NOT_ALLOWED", path, "%s settings are not written natively" % _struct_name(child))
        holder = child
    prop = holder.bl_rna.properties.get(name)
    if prop is None:
        raise PropertyPathError("PROPERTY_MISSING_IN_BUILD", path, "%s has no %s" % (_struct_name(holder), name))
    problem = leaf_problem(holder, prop, path)
    if problem:
        raise PropertyPathError(*problem)
    return holder, prop


def leaf_problem(holder, prop, path):
    """(code, path, detail) when a leaf property is not natively writable, else None."""
    if getattr(prop, "is_runtime", False) and path not in PROVEN_RUNTIME:
        return "RUNTIME_PROPERTY", path, "Python-defined settings may run Python callbacks"
    if prop.type not in LEAF_TYPES:
        return "NOT_A_SETTING", path, "%s is a collection, not a setting" % prop.identifier
    if prop.is_readonly or getattr(prop, "is_hidden", False):
        return "RNA_READONLY", path, "read-only or internal in this build"
    if prop.type == "STRING" and (getattr(prop, "subtype", "NONE") in FILE_SUBTYPES):
        return "FILE_PATH", path, "file paths are external effects"
    if prop.type == "POINTER":
        fixed = getattr(getattr(prop, "fixed_type", None), "identifier", None)
        if fixed != "Object":
            return "ID_POINTER", path, "only object pointers are written natively"
    dimensions = tuple(getattr(prop, "array_dimensions", None) or (0, 0, 0))
    if prop.type in {"BOOLEAN", "INT", "FLOAT"} and len(dimensions) > 1 and dimensions[1] > 0:
        return "NOT_A_SETTING", path, "matrices are not written natively"
    if prop.identifier == "name" and _is_id(holder):
        return "NOT_ALLOWED", path, "names have their own action kinds"
    return None


def json_value(value, path):
    """Raise unless ``value`` is a bounded JSON setting value (before any RNA is consulted)."""
    def scalar(item):
        if type(item) is bool or (type(item) is int and abs(item) < 2 ** 53):
            return True
        if type(item) is float:
            return math.isfinite(item)
        return type(item) is str and len(item) <= MAX_STRING and not any(ord(c) < 32 or ord(c) == 127 for c in item)
    if value is None or object_reference(value) is not None or scalar(value):
        return value
    if isinstance(value, list) and 1 <= len(value) <= 32 and all(scalar(item) for item in value):
        return value
    raise PropertyPathError("RNA_TYPE_MISMATCH", path, "use true/false, a number, text, a list of them, "
                            '{"object": handle} or null')


def object_reference(value):
    """The handle or ``$alias`` an ``{"object": ...}`` value names, else None."""
    if isinstance(value, dict) and set(value) == {"object"} and isinstance(value["object"], str):
        return value["object"]
    return None


def pointer_matches(actual, expected, resolve):
    """``(matched, unresolved)``: whether an object pointer read back is the one a check expects.

    ``None`` expects an empty pointer. Any other value must be a reference
    (``{"object": ...}``, or a handle or ``$alias`` as text) that ``resolve``
    finds: a name, an alias nothing declared or an object that is gone names
    nothing, so it never matches, an empty pointer included, and ``unresolved`` says why.
    """
    if expected is None:
        return actual is None, False
    reference = expected if isinstance(expected, str) else object_reference(expected)
    wanted = resolve(reference) if reference else None
    return wanted is not None and actual == wanted, wanted is None


def check_value(prop, path, value):
    """Raise unless ``value`` (JSON) suits the RNA property; enum items are left to assignment."""
    length = int(getattr(prop, "array_length", 0) or 0)
    kind = prop.type
    if kind == "POINTER":
        if value is None:
            if getattr(prop, "is_never_none", False):
                raise PropertyPathError("RNA_TYPE_MISMATCH", path, "this pointer cannot be cleared")
            return
        if object_reference(value) is None:
            raise PropertyPathError("RNA_TYPE_MISMATCH", path, 'name an object as {"object": handle}')
        return
    if kind in {"BOOLEAN", "INT", "FLOAT"}:
        items = value if length else [value]
        if length and (not isinstance(value, list) or len(value) != length):
            raise PropertyPathError("RNA_ARRAY_LENGTH", path, "expected %d values" % length)
        if not length and isinstance(value, list):
            raise PropertyPathError("RNA_ARRAY_LENGTH", path, "expected one value")
        for item in items:
            if kind == "BOOLEAN" and type(item) is not bool:
                raise PropertyPathError("RNA_TYPE_MISMATCH", path, "expected true or false")
            if kind == "INT" and type(item) is not int:
                raise PropertyPathError("RNA_TYPE_MISMATCH", path, "expected an integer")
            if kind == "FLOAT" and (type(item) not in (int, float) or not math.isfinite(item)):
                raise PropertyPathError("RNA_TYPE_MISMATCH", path, "expected a finite number")
            if kind in {"INT", "FLOAT"} and not prop.hard_min <= item <= prop.hard_max:
                raise PropertyPathError("RNA_RANGE", path, "outside RNA's range %g..%g" % (prop.hard_min, prop.hard_max))
        return
    if kind == "ENUM":
        flag = getattr(prop, "is_enum_flag", False)
        items = value if flag else [value]
        if flag and not isinstance(value, list) or any(type(item) is not str or len(item) > 64 for item in items):
            raise PropertyPathError("RNA_TYPE_MISMATCH", path, "expected %s" % ("a list of items" if flag else "an item"))
        return
    if kind == "STRING":
        limit = int(getattr(prop, "length_max", 0) or 0)
        if type(value) is not str:
            raise PropertyPathError("RNA_TYPE_MISMATCH", path, "expected text")
        if len(value) > MAX_STRING or (limit and len(value) >= limit):
            raise PropertyPathError("STRING_TOO_LONG", path, "at most %d characters" % min(MAX_STRING, (limit or MAX_STRING + 1) - 1))
        return
    raise PropertyPathError("NOT_A_SETTING", path, "unsupported RNA type " + kind)


def write(holder, prop, path, value, resolve_object=None):
    """Assign a checked JSON value; object pointers through ``resolve_object(reference)``."""
    check_value(prop, path, value)
    if prop.type == "POINTER" and value is not None:
        value = resolve_object(object_reference(value))
        if value is None:
            raise PropertyPathError("OBJECT_TYPE_MISMATCH", path, "the object is not part of this program")
    elif prop.type == "ENUM" and getattr(prop, "is_enum_flag", False):
        value = set(value)
    try:
        setattr(holder, prop.identifier, value)
    except (TypeError, ValueError, AttributeError) as error:
        code = "ENUM_ITEM_UNAVAILABLE" if prop.type == "ENUM" else "RNA_REJECTED"
        policy = refusal_code(error)
        if policy == "UNKNOWN" and prop.type == "ENUM":
            try:
                items = list(islice(prop.enum_items, 513))
                choices = {item.identifier for item in items}
                supplied = value if isinstance(value, set) else {value}
                if len(items) <= 512 and choices and not supplied <= choices:
                    policy = "VALIDATION"  # A factual missing enum value, not a guessed exception message.
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
        raise PropertyPathError(code, path, str(error)[:240], refusal_policy=policy,
                                details=refusal_details(error)) from error


def plain(value, reference_of=None):
    """A JSON value for a read setting; objects as ``{"object": reference}``."""
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if hasattr(value, "users") and hasattr(value, "type") and reference_of is not None:
        return {"object": reference_of(value)}
    try:
        return [plain(item, reference_of) for item in value]
    except TypeError:
        return str(value)


def read(holder, prop, reference_of=None):
    return plain(getattr(holder, prop.identifier), reference_of)


def tolerance(value):
    """Float32 read-back tolerance for one requested number or vector."""
    numbers = value if isinstance(value, list) else [value]
    return max(.000001, 4e-7 * max((abs(item) for item in numbers if type(item) in (int, float)), default=0))


def same(expected, actual):
    """Whether a read-back equals the requested value (float32 tolerance for numbers)."""
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return False
        if all(type(item) is str for item in expected):
            return sorted(expected) == sorted(actual)
        return all(same(e, a) for e, a in zip(expected, actual))
    if type(expected) is float or (type(expected) is int and type(actual) is float):
        return type(actual) in (int, float) and abs(actual - expected) <= tolerance(expected)
    return actual == expected and type(actual) is type(expected)


def writable_groups(rna, roots, skip=frozenset(), prefix=(), out=None):
    """Every settings struct under ``rna`` holding a leaf ``resolve`` admits, as (segments, struct, leaves).

    The static half of ``resolve`` for a whole owner type: ``roots`` are the
    owner's first segments to walk (``SCENE_ROOTS``); a leaf is listed exactly
    when ``resolve`` would admit its full path on a holder of these types, and
    a nested settings struct is entered as ``resolve`` would cross it (not an
    ID, a Python-defined property or a denied struct, within five segments).
    Python-defined settings (``PROVEN_RUNTIME``) and the paths in ``skip``
    belong to other owners. ``segments`` is the struct's path from the owner
    (empty for the owner); ``struct`` its RNA identifier, which the caller
    compares with the instance it walks.
    """
    out = [] if out is None else out
    leaves = []
    for prop in rna.properties:
        if not prefix and prop.identifier not in roots or getattr(prop, "is_runtime", False):
            continue
        segments = prefix + (prop.identifier,)
        path = ".".join(segments)
        fixed = getattr(prop, "fixed_type", None) if prop.type == "POINTER" else None
        if fixed is not None and fixed.identifier != "Object":
            if (len(segments) < MAX_SEGMENTS and fixed.identifier not in DENIED_STRUCTS
                    and not any(base.identifier == "ID" for base in _bases(fixed))):
                writable_groups(fixed, roots, skip, segments, out)
            continue
        if path in skip:
            continue
        try:
            screen_path(path)
        except PropertyPathError:
            continue
        if leaf_problem(None, prop, path) is None:
            leaves.append(prop.identifier)
    if leaves:
        out.append((prefix, rna.identifier, tuple(leaves)))
    return out


def writable_settings(rna, prefix="", depth=0, max_depth=3, out=None, limit=600, struct=None):
    """Describe every natively writable setting of an RNA struct definition (static, no instance)."""
    out = [] if out is None else out
    if struct is None:  # An object's (or a pose bone's) transform channels have kinds of their own; a struct's do not.
        struct = ("NODE" if any(getattr(base, "identifier", "") == "Node" for base in _bases(rna))
                  else getattr(rna, "identifier", "") not in ("Object", "PoseBone"))
    for prop in rna.properties:
        if len(out) >= limit:
            break
        path = prefix + prop.identifier
        try:
            screen_path(path, struct=struct)
        except PropertyPathError:
            continue
        if prop.type == "POINTER" and getattr(prop.fixed_type, "identifier", None) not in (None, "Object", "ID"):
            fixed = prop.fixed_type
            is_id = any(base.identifier == "ID" for base in _bases(fixed))
            if (depth < max_depth and not is_id and not getattr(prop, "is_runtime", False)
                    and fixed.identifier not in DENIED_STRUCTS):
                writable_settings(fixed, path + ".", depth + 1, max_depth, out, limit, struct)
            continue
        if leaf_problem(None, prop, path) is None:
            out.append(describe(prop, path))
    return out


def _bases(rna):
    while rna is not None:
        yield rna
        rna = getattr(rna, "base", None)


def describe(prop, path):
    record = {"path": path, "type": prop.type}
    if prop.type in {"INT", "FLOAT"}:
        record.update(min=prop.hard_min, max=prop.hard_max)
    if prop.type in {"BOOLEAN", "INT", "FLOAT"} and getattr(prop, "array_length", 0):
        record["length"] = prop.array_length
    if prop.type == "ENUM":
        record["items"] = [item.identifier for item in list(prop.enum_items)[:48]]
        if getattr(prop, "is_enum_flag", False):
            record["flag"] = True
    if prop.type == "STRING" and getattr(prop, "length_max", 0):
        record["max_length"] = prop.length_max - 1
    if prop.type == "POINTER":
        record["value"] = '{"object": handle}'
    if getattr(prop, "is_animatable", False):
        record["animatable"] = True
    return record
