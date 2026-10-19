# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Particle systems in staged programs: private settings, the particle_set kernel, budget, commit and revision.

Ownership: ParticleSettings is a shared datablock. Staging gives every particle
system of a staged copy its own copy of its settings, marked with the live
datablock's session UID (``SOURCE``, the material clones' marker), so the stage
graph never points at a live ParticleSettings; a system ``modifier_add``
creates gets new settings the stage owns. ``particle_set`` writes only those
private copies (copy on write if a duplicate shares one) and removes the
marker, so at commit an unmarked staged copy is an edited or new one:

* each written object's live system takes that copy, as staged mesh data
  replaces live data, and the copy takes the old settings' name when nothing
  uses them any more (they stay in the file as orphan data until it is saved);
  rollback gives the system its old settings back. Settings another system
  also uses are refused (``PARTICLE_SETTINGS_SHARED``) rather than edited for
  objects outside the program;
* a marked copy is unedited: written objects keep their live settings, and an
  object the program creates (a duplicate) gets the live settings back;
* every writable per-system setting (``particle_system.*`` of the modifier)
  that differs is copied to the live system, journaled.

Settings are re-pointed only by Blender's own copy (``particle.new``) and
``ID.user_remap``: assigning ``ParticleSystem.settings`` through RNA trips
Blender's reference-count assertion, because that property's setter counts
users while the property is not tagged as counting them.

A system's settings, its instance and force-field objects and its staging
refusals join the object's revision (``systems_revision``), so a stale plan is
refused. The budget and refusal rules are ``particle_rules``. Host tests drive
these functions with RNA stand-ins; ``bpy`` is imported only where needed.
"""

from . import particle_rules
from .refusal_codes import refusal_code, refusal_details, RefusalError

SOURCE = "_angelo_stage_source_uid"  # program_objects.MATERIAL_SOURCE: one stage-only marker for every clone.
SHARED = "particle_shared"  # stage.extras: session UIDs of live settings other systems also use.
MAX_SETTINGS_ITEMS = 50000  # Recursive RNA items of one ParticleSettings fingerprint (a default one has ~1,200).


def copy_settings(obj, psys):
    """Give ``psys`` a copy of its settings with Blender's own operator; return the copy."""
    import bpy
    previous = psys.settings
    with bpy.context.temp_override(object=obj, active_object=obj, particle_system=psys):
        result = bpy.ops.particle.new()
    if "FINISHED" not in result or psys.settings is None or psys.settings == previous:
        raise RefusalError("Blender did not give the particle system its own settings", code='INTERNAL_ERROR')
    return psys.settings


def clone_settings(backend, stage, duplicate):
    """Give each particle system of a staged copy a private, marked copy of its live settings."""
    from .program_jobs import ProgramError
    for psys in duplicate.particle_systems:
        live = psys.settings
        if live is None:
            continue
        if live.library is not None or getattr(live, "override_library", None) is not None:
            raise ProgramError("Linked particle settings cannot be staged natively", code='UNSUPPORTED')
        try:
            private = backend._track(stage, copy_settings(duplicate, psys))
        except ValueError as error:
            raise ProgramError(str(error), code=refusal_code(error), details=refusal_details(error)) from error
        from .stage_names import hide
        hide(stage.extras, private, live.name)
        animation = private.animation_data
        if animation is not None and animation.action is not None:
            backend._track(stage, animation.action)  # The operator copies Actions too.
        private[SOURCE] = int(live.session_uid)
        if live.users > 1:  # Other live systems (or a fake user) keep these settings.
            stage.extras.setdefault(SHARED, []).append(int(live.session_uid))


def adopt_new(context, modifier):
    """Own the settings ``modifiers.new`` created for a new particle system."""
    if modifier.type == "PARTICLE_SYSTEM" and modifier.particle_system.settings is not None:
        context.track_id(modifier.particle_system.settings)


def _system(obj, name):
    modifier = obj.modifiers.get(name)
    if modifier is None or modifier.type != "PARTICLE_SYSTEM":
        names = [repr(item.name[:64]) for item in obj.modifiers if item.type == "PARTICLE_SYSTEM"][:8]
        raise RefusalError("particle_set: no PARTICLE_SYSTEM modifier is named %r on this object; its particle systems: %s"
                         % (name[:64], ", ".join(names) or "none (add one with modifier_add type PARTICLE_SYSTEM)"), code='VALIDATION')
    return modifier


def private_settings(context, obj, psys):
    """The system's own staged settings, copied first when a staged duplicate shares them."""
    settings = psys.settings
    if settings is None:
        raise RefusalError("PARTICLE_SETTINGS_MISSING: the particle system has no settings", code='VALIDATION')
    if settings not in context.owned_ids:
        raise RefusalError("particle_set writes only staged particle settings", code='INTERNAL_ERROR')
    if SOURCE in settings.keys() and int(settings[SOURCE]) in context.extras.get(SHARED, ()):
        raise RefusalError("PARTICLE_SETTINGS_SHARED: other particle systems (or a fake user) keep these settings; make "
                         "them single-user (their user count) first, or edit them with reviewed Python", code='UNSUPPORTED')
    if settings.users > 1:
        settings = context.track_id(copy_settings(obj, psys))
    return context.require_private(settings)


def apply(context, obj, operation):
    """particle_set: write the settings by RNA path, read each back, then bound the result before evaluation."""
    from . import generic_settings
    from .action_budget import _modifier_bound
    from .action_context import _mesh, _readback
    params = operation["parameters"]
    _mesh(context, obj)
    modifier = _system(obj, params["modifier"])
    psys = modifier.particle_system
    if psys.is_edited:
        raise RefusalError("PARTICLE_SYSTEM_EDITED: combed hair would be reset; edit it with reviewed Python", code='UNSUPPORTED')
    if psys.point_cache.is_baked:
        raise RefusalError("PARTICLE_CACHE_BAKED: a baked system ignores its settings; free the bake first", code='UNSUPPORTED')
    settings = private_settings(context, obj, psys)
    generic_settings.apply(context, obj, settings, params["settings"], "particle system")
    if SOURCE in settings.keys():
        del settings[SOURCE]  # Edited: commit gives the live system this copy.
    bound = _modifier_bound(context, obj)  # Refuses an over-budget or unsupported system before evaluation.
    summary = particle_rules.summary(settings, lambda target: generic_settings._reference_of(context, target))
    return _readback(obj, operation, modifier=modifier.name, settings=summary, evaluated_vertex_bound=bound)


def modifier_points(context, obj, modifier, bound_of):
    """Evaluated points of one PARTICLE_SYSTEM modifier; raise when its system is refused or over budget.

    ``bound_of(object)`` is the evaluated size of an instanced mesh object. Every
    object the system or its settings point at must be part of the program.
    """
    from .action_schema_types import MAX_EVALUATED_VERTICES
    from .generic_settings import object_pointers
    psys = modifier.particle_system
    settings = psys.settings
    for target in object_pointers(modifier) + (object_pointers(settings) if settings is not None else []):
        if target == obj:
            raise RefusalError("PARTICLE_SELF_REFERENCE: a particle system cannot point at its own object", code='VALIDATION')
        context.assert_staged(target)
    instance = 0
    if settings is not None and settings.render_type == "OBJECT" and settings.instance_object is not None:
        target = settings.instance_object
        instance = bound_of(target) if target.type == "MESH" else 8
    reason, points = particle_rules.refusal(psys, settings, MAX_EVALUATED_VERTICES, instance)
    if reason:
        raise RefusalError(reason, code='UNSUPPORTED')
    return points


def _plain(holder, prop):
    value = getattr(holder, prop.identifier)
    if prop.type == "POINTER":
        return value
    if isinstance(value, (set, frozenset)):
        return set(value)
    return tuple(value) if prop.type in {"BOOLEAN", "INT", "FLOAT"} and getattr(prop, "array_length", 0) else value


def _transfer_values(live, private, mapping, journal):
    """Copy every writable per-system setting that differs from the staged system, journaling each."""
    from .program_jobs import ProgramError
    from .rna_rules import PropertyPathError, resolve, writable_settings
    for entry in writable_settings(private.bl_rna, limit=2000):
        try:
            source, prop = resolve(private, entry["path"], owner_kind="STRUCT")
            holder, _ = resolve(live, entry["path"], owner_kind="STRUCT")
        except (PropertyPathError, AttributeError):
            continue  # An absent optional struct (hair dynamics never enabled).
        value = _plain(source, prop)
        if prop.type == "POINTER" and value is not None:
            value = mapping.get(value.as_pointer(), value)
        current = _plain(holder, prop)
        if current == value:
            continue
        journal.append((holder, prop.identifier, current))
        setattr(holder, prop.identifier, value)
        if _plain(holder, prop) != value:
            raise ProgramError("A committed particle system setting did not retain its staged value: " + entry["path"],
                               code='INTERNAL_ERROR')


class _RemapBack:
    """A settings-journal entry that gives a live system its previous settings back on rollback.

    ``program_commit`` restores its journal with ``setattr(holder, name, value)``;
    restoring ``settings`` here re-points every user of the committed copy (the
    live system and the stage's own, which is discarded next) with ``user_remap``.
    """

    def __init__(self, committed):
        self.committed = committed

    @property
    def settings(self):
        return self.committed

    @settings.setter
    def settings(self, previous):
        self.committed.user_remap(previous)


def transfer_systems(original, staged, created, mapping, journal, replaced):
    """Give a written object's live particle systems their staged settings (see the module contract)."""
    import bpy
    from .program_jobs import ProgramError
    for source in staged.modifiers:
        if source.type != "PARTICLE_SYSTEM":
            continue
        target = original.modifiers.get(source.name)
        if target is None or target.type != "PARTICLE_SYSTEM":
            raise ProgramError("A particle system changed identity before commit", code='STALE_DEPENDENCY')
        live, private = target.particle_system, source.particle_system
        settings = private.settings
        if settings is not None and SOURCE not in settings.keys() and live.settings != settings:
            previous = live.settings
            if previous.users > 1:
                raise ProgramError("PARTICLE_SETTINGS_SHARED: other particle systems started using these settings",
                                   code='STALE_DEPENDENCY')
            previous.user_remap(settings)  # Only this live system used them.
            if live.settings != settings:
                raise ProgramError("A committed particle system did not take its staged settings", code='INTERNAL_ERROR')
            if any(item == target for _owner, item in created):
                bpy.data.particles.remove(previous)  # The default settings the new modifier created.
            else:
                journal.append((_RemapBack(settings), "settings", previous))
                replaced.append((previous, settings))
        _transfer_values(live, private, mapping, journal)


def restore_sources(objects, stage):
    """Created objects: an unedited staged copy goes back to the live settings it copies."""
    import bpy
    owned = {block.as_pointer() for block in list(stage.owned_ids) + list(stage.context.created_ids)}
    for obj in objects:
        for psys in getattr(obj, "particle_systems", ()):
            settings = psys.settings
            if settings is None or settings.as_pointer() not in owned or SOURCE not in settings.keys():
                continue
            uid = int(settings[SOURCE])
            live = next((item for item in bpy.data.particles
                         if int(item.session_uid) == uid and item.as_pointer() not in owned), None)
            if live is not None:
                settings.user_remap(live)  # Every user is a staged or created object.
            else:
                del settings[SOURCE]  # The original is gone: the copy stays, unmarked.


def systems_revision(obj, work, unsupported):
    """One revision field for every particle system: its settings' complete RNA, by identity (bounded)."""
    from .program_dependencies import _rna_values
    from .program_jobs import ProgramError
    from .scene_read_values import hash_value, label, session_uid
    systems = []
    for psys in obj.particle_systems:
        work.take(items=1)
        settings = psys.settings
        if psys.is_edited:
            unsupported.append("particle_edited_hair")
        if settings is None:
            systems.append([label(psys.name), None])
            continue
        unsupported.extend(particle_rules.dependency_problems(settings, psys.point_cache))
        budget = [MAX_SETTINGS_ITEMS, MAX_SETTINGS_ITEMS * 8]
        try:
            value = _rna_values(settings, budget=budget)
        except ProgramError:
            unsupported.append("particle_settings_budget")
            value = None
        work.take(items=MAX_SETTINGS_ITEMS - max(0, budget[0]))
        systems.append([label(psys.name), session_uid(settings), value])
    return hash_value(systems)


def evaluate_check(obj, check, resolve_target):
    """(passed, observed) for a ``particle_values`` check on a live or staged object."""
    from .generic_settings import evaluate
    modifier = obj.modifiers.get(check["name"])
    if modifier is None or modifier.type != "PARTICLE_SYSTEM":
        return False, {"present": modifier is not None, "type": getattr(modifier, "type", None)}
    settings = modifier.particle_system.settings
    if settings is None:
        return False, {"present": True, "settings": None}
    return evaluate(settings, check["values"], check.get("tolerance"), resolve_target)


def delta(before, after, name_of):
    """Preview rows: each particle system's main settings, live and staged (``name_of`` names staged objects)."""
    rows = []
    for modifier in after.modifiers:
        if modifier.type == "PARTICLE_SYSTEM":
            live = before.modifiers.get(modifier.name)
            old = live.particle_system.settings if live is not None and live.type == "PARTICLE_SYSTEM" else None
            rows.append({"modifier": modifier.name[:64], "before": particle_rules.summary(old),
                         "after": particle_rules.summary(modifier.particle_system.settings, name_of)})
    return rows
