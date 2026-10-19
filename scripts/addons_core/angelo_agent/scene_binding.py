# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bound scene in a model context: observed once per message, bound without a read.

Scene settings and the World are edited through the scene's handle and its
settings revision (``scene_settings``). A model used to get both from an
``angelo_scene_settings`` call: one model round, and about 3 KB of values, each
time a turn touched the render settings, the active camera or the World. The
turn context now states them (``turn_context``: ``scene_settings``), and this
module is what makes that entry usable:

* ``observe`` records the entry in the context's ``identifiers.IdentifierTable``
  like an object row, so a program that targets the scene and leaves its
  binding out is bound to the observed settings revision (inferred bindings).
  Staging and commit recompute that revision, so a setting changed since the
  model's last view still fails closed.
* ``implicit_targets`` fills in the scene for every operation that can only
  mean it (``scene_only``: the World, markers, a ``property_set`` of scene
  settings, a collection's visibility) when its target is null or left out,
  and refuses one that names anything else, with what to write, before a
  stage is built. Nothing is guessed: which datablock such an operation edits
  follows from its kind and paths alone, and without an observed scene the
  missing target reaches the owner's validator, which says what to pass.

A kind that creates its object has no target to choose either
(``TARGETLESS_KINDS``: its schema allows only null), so a plan may leave the
field out and the validator (``action_catalog.validate_operation``) reads it
as null: 14 bytes of every created object that no longer have to be written.

Pure Python; no Blender access.
"""

import re

from .action_schemas import OPERATION_SCHEMAS
from .rna_rules import SCENE, owner_of

# Kinds whose only possible target is the bound scene: a null or missing target means it.
SCENE_KINDS = frozenset({"world_configure", "marker_edit"})
# Kinds that edit the scene or an object depending on their parameters: a plan may leave the scene's target out.
SCENE_OR_OBJECT_KINDS = frozenset({"property_set", "visibility_set", "node_edit"})
_HANDLE = re.compile(r"^(?:o\d+|scn:[0-9a-f]{32}:\d+|\$[A-Za-z][A-Za-z0-9_]{0,31})$")
# Kinds whose target can only be null (they create their object): the field may be left out.
TARGETLESS_KINDS = frozenset(kind for kind, schema in OPERATION_SCHEMAS.items()
                             if schema["properties"]["target"] == {"type": "null"})
NULL_TARGET = ("%s edits the bound scene: pass the scene handle as target (scene_settings.handle in the observation, "
               "or the handle angelo_scene_settings returns)")
WRONG_TARGET = ("%s OWNER_TYPE_MISMATCH: this edits the bound scene, which needs no target: leave target out (or pass "
                "scene_settings.handle from the observation)")
OBJECT_PATHS = ("property_set without a target edits the bound scene, and %s: an object's settings take its "
                "handle as target, in a property_set of their own")
SCENE_TARGET = ("property_set OWNER_TYPE_MISMATCH: its target is the bound scene, and %s: an object's settings "
                "take its handle as target, in a property_set of their own")
CREATES = "%s creates its object, so it takes no target: leave target out"


def scene_only(operation):
    """Whether an operation, by its kind and parameters alone, can only edit the bound scene.

    The World and markers always; ``property_set`` when every path is a scene
    setting (the same rule its kernel applies: ``rna_rules.owner_of``);
    ``visibility_set`` when it names a collection; ``node_edit`` when it
    edits the World's tree.
    """
    kind = operation.get("kind") if isinstance(operation, dict) else None
    if kind in SCENE_KINDS:
        return True
    parameters = operation.get("parameters") if kind in SCENE_OR_OBJECT_KINDS else None
    if not isinstance(parameters, dict):
        return False
    if kind == "visibility_set":
        return parameters.get("collection") is not None
    if kind == "node_edit":
        return parameters.get("world") is True
    values = parameters.get("values")
    return isinstance(values, dict) and bool(values) and all(
        isinstance(path, str) and owner_of(path) == SCENE for path in values)


def refuse_target(operation):
    """Raise for a scene-only operation whose target cannot be the scene: none, or an object this program made."""
    target = operation.get("target")
    if target is None:
        raise ValueError(NULL_TARGET % operation["kind"])
    if isinstance(target, str) and target.startswith("$"):
        raise ValueError(WRONG_TARGET % operation["kind"])


def _object_paths(operation):
    """Which of a property_set's paths are not scene settings, as an error says it; empty when none (or no such set)."""
    parameters = operation.get("parameters") if operation.get("kind") == "property_set" else None
    values = parameters.get("values") if isinstance(parameters, dict) else None  # Malformed: the schema's refusal.
    if not isinstance(values, dict) or not values or not all(isinstance(path, str) for path in values):
        return ""
    paths = ["'%s'" % "".join(c if " " <= c else " " for c in path[:64]) for path in values if owner_of(path) != SCENE]
    if not paths:
        return ""
    if len(paths) > 3:
        paths = paths[:2] + ["%d more" % (len(paths) - 2)]
    named = " and ".join([", ".join(paths[:-1]), paths[-1]] if len(paths) > 1 else paths)
    return named + (" is not a scene setting" if len(paths) == 1 else " are not scene settings")


def check_target(operation):
    """Raise, before the schema's generic error, for a target the operation's kind and paths decide.

    A scene-only operation needs the scene (``refuse_target``). A property_set
    without a target edits the bound scene only when every path is a scene
    setting, so one without names the paths that are not: told only that a
    target was missing, GPT-6.1 Sol wrote ``frame_current`` among a scene's
    settings in all six recorded bridge one-shots with typed programs, before the
    host counted it as the scene's, and re-sent one program four times without
    finding it.
    """
    if scene_only(operation):
        refuse_target(operation)
    elif operation.get("target") is None and _object_paths(operation):
        raise ValueError(OBJECT_PATHS % _object_paths(operation))


def named_targets(name, arguments):
    """``arguments`` unchanged, or the remedy for a scene-only operation whose target is a word, not a handle.

    Recorded: ``"target": "scene"`` on the property_set that makes a camera the scene's. The
    identifier resolver would answer that it is not an object handle, which says nothing about what to write.
    """
    operations = arguments.get("operations") if name == "angelo_scene_program" and isinstance(arguments, dict) else None
    for operation in operations if isinstance(operations, list) else ():
        target = operation.get("target") if isinstance(operation, dict) else None
        if isinstance(target, str) and not _HANDLE.match(target) and scene_only(operation):
            raise ValueError(WRONG_TARGET % operation["kind"])
    return arguments


def observe(table, observation):
    """Record the scene handle and settings revision that ``observation`` (long forms) shows.

    Call after ``table.compact`` numbered the handle. An entry without a
    revision forgets the earlier one, as for an object.
    """
    entry = observation.get("scene_settings") if isinstance(observation, dict) else None
    handle = entry.get("handle") if isinstance(entry, dict) else None
    if not isinstance(handle, str):
        return
    table.scene_handle = handle
    table.observe({"objects": [{"handle": handle, "object_revision": entry.get("revision"),
                                "revision_omitted": entry.get("revision_omitted")}]})


def implicit_targets(arguments, table):
    """Program arguments with the targets a plan may leave out written in.

    A scene-only operation without a target names the observed scene; a
    creating operation without one gets its null, so a program reads the same
    however it was written (``codex_plan._repeats`` compares resolved calls).
    """
    from .program_spec import at_operation
    scene = getattr(table, "scene_handle", None)
    operations = arguments.get("operations") if isinstance(arguments, dict) else None
    if not isinstance(operations, list):
        return arguments

    def filled(operation):
        kind = operation.get("kind") if isinstance(operation, dict) else None
        if not isinstance(kind, str):
            return operation
        target = operation.get("target")
        if scene is not None and scene_only(operation):
            if target is not None and target != scene:
                raise ValueError(WRONG_TARGET % kind)  # Refused here, before a stage is built to refuse it.
            return dict(operation, target=scene)
        if scene is not None and target == scene and _object_paths(operation):
            raise ValueError(SCENE_TARGET % _object_paths(operation))  # Else the kernel's, after approval.
        if kind in TARGETLESS_KINDS and target is not None:
            raise ValueError(CREATES % kind)  # Not "not an object handle" for the scene's handle.
        return dict(operation, target=None) if kind in TARGETLESS_KINDS and "target" not in operation else operation
    return dict(arguments, operations=[at_operation(index, operation, filled) for index, operation in enumerate(operations)])
