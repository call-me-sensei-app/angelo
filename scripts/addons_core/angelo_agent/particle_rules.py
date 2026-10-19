# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Particle systems: the ``particle_set`` contract and the evaluation bound of a system (pure, no bpy).

A particle system is added like any generic modifier (``modifier_add`` with
type ``PARTICLE_SYSTEM``); its per-system settings (seed, vertex groups) are
``particle_system.*`` paths of that modifier. Its ParticleSettings, a shared
datablock, are written only by ``particle_set``: RNA paths on the settings of
one PARTICLE_SYSTEM modifier, checked by ``rna_rules`` and written to the
private copy staging gives every system (``action_particles``). Object
pointers (``instance_object``) are ``{"object": handle}`` values, declared read
dependencies like any generic setting.

Bounded before any evaluation, from the settings alone (``system_bound``):

* points: parents (``count``, or the grid's cells) times one plus the children
  (the larger of the viewport and render amounts), times the points of each
  path for hair or path display (``2^step + 1``, the larger step, plus spiral
  kink steps) or the trail length, plus one instance of the instanced object's
  evaluated size per particle;
* emitter steps: parents times the substeps of one frame (``subframes + 1``,
  101 when adaptive) times the frames simulated at the start frame (Blender
  steps from a negative ``frame_start`` up to frame 1 there), the work of one
  depsgraph update. Hair is not simulated without hair dynamics.

Refused outright: keyed, boids and fluid physics; particles acting as force
fields; collections (instancing, collision, effector weights), textures and
animated settings; hair dynamics; disk or external caches; and, for
``particle_set``, edited (combed) hair and baked caches.
"""

import copy

from .refusal_codes import RefusalError
from .action_schema_types import obj, text
from .action_properties import VALUE
from .rna_rules import PATH_PATTERN, object_reference

MAX_SETTINGS = 64
SUPPORTED_PHYSICS = frozenset({"NO", "NEWTON"})
# Blender's adaptive time step never goes below 1/101 of a frame (particle_system.cc MIN_TIMESTEP).
MAX_ADAPTIVE_SUBSTEPS = 101
SETTINGS_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": VALUE,
                   "propertyNames": {"pattern": PATH_PATTERN}, "minProperties": 1, "maxProperties": MAX_SETTINGS}
# One PARTICLE_SYSTEM modifier by its exact name; its ParticleSettings by RNA path.
PARTICLE_SET_SCHEMA = obj({"modifier": text(), "settings": SETTINGS_SCHEMA}, ["modifier", "settings"])
SUMMARY_FIELDS = ("type", "count", "emit_from", "physics_type", "render_type", "child_type", "frame_start", "frame_end",
                  "lifetime", "hair_length", "display_step", "render_step", "child_percent", "rendered_child_count")


def canonical(operation, result):
    """Screen the raw settings (typed reasons first) and keep their JSON types; refuse self-instancing."""
    from .generic_settings import screen_settings
    raw = (operation.get("parameters") or {}).get("settings", {})
    screen_settings(raw)
    result["parameters"]["settings"] = copy.deepcopy(raw)  # RNA, not the schema, types each value.
    if result["target"] in references(result["parameters"]):
        raise RefusalError("particle_set: a particle system cannot instance its own object", code="VALIDATION")


def references(params):
    """Object handles the settings name (instance objects, force-field sources)."""
    return [object_reference(value) for value in params["settings"].values() if object_reference(value) is not None]


def derived_check(target, params):
    """The ``particle_values`` check a particle_set states: every written path, read back after commit.

    No tolerance: each number is compared at its own float32 precision, so a
    later particle_set of the same system can merge its values in.
    """
    return {"kind": "particle_values", "target": target, "name": params["modifier"],
            "values": copy.deepcopy(params["settings"])}


def _set(value):
    return value is not None


def dependency_problems(settings, cache=None):
    """Closed reasons a system's evaluation reads state outside its declared objects, or files."""
    problems = []
    if _set(getattr(settings, "instance_collection", None)) or _set(getattr(settings, "collision_collection", None)):
        problems.append("particle_collection_dependency")
    weights = getattr(settings, "effector_weights", None)
    if _set(getattr(weights, "collection", None)):
        problems.append("particle_collection_dependency")
    if any(slot is not None and _set(getattr(slot, "texture", None)) for slot in getattr(settings, "texture_slots", ())):
        problems.append("particle_texture_dependency")
    animation = getattr(settings, "animation_data", None)
    if animation is not None and (animation.action is not None or len(animation.drivers) or len(animation.nla_tracks)):
        problems.append("particle_settings_animation")
    if cache is not None and (getattr(cache, "use_disk_cache", False) or getattr(cache, "use_external", False)):
        problems.append("particle_disk_cache")
    return sorted(set(problems))


def system_bound(settings, instance_bound=0):
    """(points, emitter particle-steps) of one system with these settings, read without evaluating."""
    hair = settings.type == "HAIR"
    parents = settings.count
    if not hair and settings.distribution == "GRID":
        parents = max(parents, settings.grid_resolution ** 3)
    children = 0 if settings.child_type == "NONE" else max(settings.child_percent, settings.rendered_child_count)
    particles = parents * (1 + children)
    if hair or "PATH" in (settings.render_type, settings.display_method):
        per_particle = 2 ** max(settings.display_step, settings.render_step) + 1
        if settings.kink == "SPIRAL":
            per_particle += settings.kink_extra_steps
    else:
        per_particle = max(1, settings.trail_count)
    points = particles * per_particle + (parents * (settings.hair_step + 1) if hair else 0)
    if settings.render_type == "OBJECT":
        points += particles * max(0, instance_bound)
    steps = 0
    if not hair:
        substeps = MAX_ADAPTIVE_SUBSTEPS if settings.use_adaptive_subframes else settings.subframes + 1
        steps = parents * substeps * (1 + max(0, 1 - int(settings.frame_start)))
    return points, steps


def refusal(psys, settings, limit, instance_bound=0):
    """A coded reason this system cannot be staged or evaluated natively, else None (its points)."""
    if settings is None:
        return "PARTICLE_SETTINGS_MISSING: the particle system has no settings", 0
    if settings.physics_type not in SUPPORTED_PHYSICS:
        return ("PARTICLE_PHYSICS_UNSUPPORTED: %s physics reads other systems, rules or neighbours; use No or "
                "Newtonian physics" % settings.physics_type), 0
    fields = [getattr(getattr(settings, name, None), "type", "NONE") for name in ("force_field_1", "force_field_2")]
    if any(field != "NONE" for field in fields):
        return "PARTICLE_FORCE_FIELD: particles acting as force fields affect every other particle", 0
    problems = dependency_problems(settings, getattr(psys, "point_cache", None))
    if problems:
        return "PARTICLE_DEPENDENCY_UNSUPPORTED: " + ", ".join(problems), 0
    if getattr(psys, "use_hair_dynamics", False):
        return "PARTICLE_HAIR_DYNAMICS: hair dynamics simulate cloth on every frame", 0
    points, steps = system_bound(settings, instance_bound)
    if points > limit or steps > limit:
        return ("PARTICLE_BUDGET: %d evaluated points and %d emitter particle-steps exceed %d; lower count, the "
                "children amounts, display_step/render_step, subframes or a negative frame_start"
                % (points, steps, limit)), points
    return None, points


def summary(settings, name_of=None):
    """A bounded read-back of the main settings (observation, kernel result, preview delta).

    ``name_of(object)`` names the instanced object (a staged copy by its handle or live name).
    """
    if settings is None:
        return None
    result = {}
    for name in SUMMARY_FIELDS:
        value = getattr(settings, name, None)
        result[name] = round(value, 6) if isinstance(value, float) else value
    instance = getattr(settings, "instance_object", None)
    result["instance_object"] = None if instance is None else (name_of or (lambda item: item.name[:64]))(instance)
    return result
