# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The typed wrapped operations' registry: the declaration contract, enforced (doc/angelo/wrapped_ops.rst).

Each lane declares its operations as plain data, ``WRAPPED_OPS`` in its own module, listed in
``wrapped_op_modules.MODULES``. ``registry()`` imports those modules once and registers every declaration through
``register``, which refuses one that breaks the agent-verbs design's Part E (revisions 1-6): unknown or missing
fields; a kind that is already a typed kind or is declared twice; an operator, source, target, mode or ID type that is
not one of Blender's names; writes outside the families the program transaction journals; parameters that are not a
closed JSON object mapped one to one onto operator properties; and the shared turn budget's rules
(``wrapped_op_budget.check_declaration``). A registered declaration is a deep read-only copy taken as it registers
(``read_only.frozen``; functions kept as declared), and the add-on builds the registry as it registers, so nothing an
agent script changes in a declaring module reaches what ``admit`` reads. ``admit`` applies the budget to a call by
its registered kind, before anything is staged.

Pure Python; no Blender access.
"""

import functools
import importlib
import json
import re
from types import MappingProxyType

from . import read_only, wrapped_op_budget
from .action_schemas import VALIDATED_OPERATIONS, _mutations
from .wrapped_op_modules import MODULES

REQUIRED = frozenset({"kind", "operator", "source", "target", "mode", "parameters", "map", "reads_only", "creates",
                      "mutates", "input_bound", "checks", "guide"})
OPTIONAL = frozenset({"new_ids", "creates_if", "receipt", "job_kind", "job_if", "levers"})

KIND = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
OPERATOR = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")        # bpy.ops idname: module.name
SOURCE = re.compile(r"^(?:angelo|bundled:[A-Za-z_][A-Za-z0-9_]*)$")  # A bundled extension's id is an identifier.
PROPERTY = re.compile(r"^[a-z_][a-z0-9_]*$")

# Blender's own names (rna_context.cc, rna_object.cc, rna_ID.cc), pinned by test_native_wrapped_ops.py.
MODES = frozenset({
    "EDIT_MESH", "EDIT_CURVE", "EDIT_CURVES", "EDIT_SURFACE", "EDIT_TEXT", "EDIT_ARMATURE", "EDIT_METABALL",
    "EDIT_LATTICE", "EDIT_GREASE_PENCIL", "EDIT_POINTCLOUD", "POSE", "SCULPT", "PAINT_WEIGHT", "PAINT_VERTEX",
    "PAINT_TEXTURE", "PARTICLE", "OBJECT", "PAINT_GPENCIL", "EDIT_GPENCIL", "SCULPT_GPENCIL", "WEIGHT_GPENCIL",
    "VERTEX_GPENCIL", "SCULPT_CURVES", "PAINT_GREASE_PENCIL", "SCULPT_GREASE_PENCIL", "WEIGHT_GREASE_PENCIL",
    "VERTEX_GREASE_PENCIL"})
OBJECT_TYPES = frozenset({
    "MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME", "GREASEPENCIL", "ARMATURE",
    "LATTICE", "EMPTY", "LIGHT", "LIGHT_PROBE", "CAMERA", "SPEAKER"})
ID_TYPES = frozenset({   # OBJECT is left out: only ``creates`` may add objects.
    "ACTION", "ARMATURE", "BRUSH", "CACHEFILE", "CAMERA", "COLLECTION", "CURVE", "CURVES", "FONT", "GREASEPENCIL",
    "GREASEPENCIL_V3", "IMAGE", "KEY", "LATTICE", "LIBRARY", "LIGHT", "LIGHT_PROBE", "LINESTYLE", "MASK", "MATERIAL", "MESH", "META",
    "MOVIECLIP", "NODETREE", "PAINTCURVE", "PALETTE", "PARTICLE", "POINTCLOUD", "SCENE", "SCREEN", "SOUND",
    "SPEAKER", "TEXT", "TEXTURE", "VOLUME", "WINDOWMANAGER", "WORKSPACE", "WORLD"})

FAMILIES = frozenset(family for families in _mutations.values() for family in families)   # What a journal records.
CREATES = (frozenset({"object"}), frozenset({"children"}), frozenset({"object", "children"}),
           frozenset({"object_with_children"}))
_COLLECTIONS = (set, frozenset, tuple, list)


def _names(value, allowed):
    """``value`` as a frozenset of names drawn from ``allowed``, or None when it is not one."""
    if not isinstance(value, _COLLECTIONS) or not all(isinstance(item, str) for item in value):
        return None
    names = frozenset(value)
    return names if names <= allowed else None


def _check_identity(declaration, taken):
    kind = declaration["kind"]
    if not isinstance(kind, str) or not KIND.match(kind):
        raise ValueError("kind is a lowercase name of up to 48 letters, digits and underscores")
    if kind in taken:
        raise ValueError("%s is already a typed kind" % kind)
    if not isinstance(declaration["operator"], str) or not OPERATOR.match(declaration["operator"]):
        raise ValueError("operator %r is not an operator idname (module.name)" % (declaration["operator"],))
    if not isinstance(declaration["source"], str) or not SOURCE.match(declaration["source"]):
        raise ValueError("source %r is angelo or bundled:<extension id>" % (declaration["source"],))
    if declaration["target"] is not None and declaration["target"] not in OBJECT_TYPES:
        raise ValueError("target %r is an object type or None" % (declaration["target"],))
    if declaration["mode"] not in MODES:
        raise ValueError("mode %r is a context mode (OBJECT, EDIT_MESH, POSE...)" % (declaration["mode"],))


def _check_writes(declaration):
    reads_only, creates = declaration["reads_only"], declaration["creates"]
    if not isinstance(reads_only, bool):
        raise ValueError("reads_only is True or False")
    mutates = _names(declaration["mutates"], FAMILIES)
    if mutates is None:
        raise ValueError("mutates names journaled families only: %s" % ", ".join(sorted(FAMILIES)))
    if creates is not None and (not isinstance(creates, (set, frozenset)) or frozenset(creates) not in CREATES):
        raise ValueError("creates is None or one of %s" % ", ".join(str(sorted(c)) for c in CREATES))
    new_ids = None
    if "new_ids" in declaration:
        new_ids = _names(declaration["new_ids"], ID_TYPES)
        if not new_ids:
            raise ValueError("new_ids names Blender ID types other than OBJECT")
    if reads_only and (mutates or creates or new_ids):
        raise ValueError("a read-only operation writes, creates and adds nothing")
    if not reads_only and not (mutates or creates or new_ids):
        raise ValueError("a mutating operation names what it writes (mutates), creates or adds (new_ids)")
    for member, condition in declaration.get("creates_if", {}).items():
        if member not in (creates or ()):
            raise ValueError("creates_if %r: not a member of creates" % (member,))
        _enum_values("creates_if %r" % member, condition, declaration["parameters"])


def _enum_values(field, condition, parameters):
    if not (isinstance(condition, tuple) and len(condition) == 2 and isinstance(condition[1], _COLLECTIONS)):
        raise ValueError("%s is (enum parameter, {values})" % field)
    name, values = condition
    allowed = parameters["properties"].get(name, {}).get("enum") if isinstance(name, str) else None
    if not allowed or not values or not set(values) <= set(allowed):
        raise ValueError("%s: values of an enum parameter" % field)


def _check_parameters(declaration):
    schema = declaration["parameters"]
    if not (isinstance(schema, dict) and schema.get("type") == "object" and schema.get("additionalProperties") is False
            and isinstance(schema.get("properties"), dict)
            and all(isinstance(spec, dict) for spec in schema["properties"].values())):
        raise ValueError("parameters is a JSON Schema object with additionalProperties false")
    try:
        json.dumps(schema, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("parameters is plain JSON (no sets, NaN or objects)") from None
    properties = schema["properties"]
    if not set(schema.get("required", ())) <= set(properties):
        raise ValueError("required names parameters that are not declared")
    mapping = declaration["map"]
    if not isinstance(mapping, dict) or set(mapping) != set(properties):
        raise ValueError("map names every parameter and nothing else")
    targets = list(mapping.values())
    if not all(isinstance(name, str) and PROPERTY.match(name) for name in targets) or len(set(targets)) != len(targets):
        raise ValueError("map sends each parameter to its own operator property")
    if not callable(declaration["checks"]):
        raise ValueError("checks is a function (parameters, before, after) -> [check]")
    if "receipt" in declaration and not callable(declaration["receipt"]):
        raise ValueError("receipt is a function (after) -> dict")
    if not isinstance(declaration["guide"], str) or not KIND.match(declaration["guide"]):
        raise ValueError("guide is a feature_guide id")


def check(declaration, job_kinds, taken):
    """Refuse a declaration that breaks Part E. ``job_kinds``: the host's Background Job kinds; ``taken``: kinds already
    in use."""
    if not isinstance(declaration, dict):
        raise ValueError("a declaration is a dict of fields")
    # budget_s is refused by the budget's own check, which says why (the budget is shared).
    unknown, missing = set(declaration) - REQUIRED - OPTIONAL - {"budget_s"}, REQUIRED - set(declaration)
    if unknown or missing:
        raise ValueError("fields %s" % "; ".join(part for part in (
            "unknown: " + ", ".join(sorted(unknown)) if unknown else "",
            "missing: " + ", ".join(sorted(missing)) if missing else "") if part))
    _check_identity(declaration, taken)
    _check_parameters(declaration)
    _check_writes(declaration)
    wrapped_op_budget.check_declaration(declaration, job_kinds)


def register(declarations, job_kinds=None, taken=None):
    """``{kind: declaration}``, each a deep read-only copy (``read_only.frozen``: mappings, lists and sets all the way
    down; functions kept); ValueError naming the kind for the first declaration that breaks Part E."""
    if job_kinds is None:
        from .job_spec import KINDS as job_kinds
    taken = set(VALIDATED_OPERATIONS if taken is None else taken)   # The read-only copy (2.0.0's rule).
    operations = {}
    for declaration in declarations:
        kind = declaration.get("kind") if isinstance(declaration, dict) else None
        try:
            check(declaration, job_kinds, taken | set(operations))
        except ValueError as error:
            raise ValueError("wrapped operation %r: %s" % (kind, error)) from None
        operations[kind] = read_only.frozen(declaration)   # Checked and copied in one step: no script runs between.
    return operations


@functools.cache
def registry():
    """The add-on's wrapped operations, registered once from ``wrapped_op_modules.MODULES``: built by the add-on's
    ``register``, before any agent script runs."""
    declarations = []
    for name in MODULES:
        declarations.extend(importlib.import_module("." + name, __package__).WRAPPED_OPS)
    return MappingProxyType(register(declarations))


def model_schema(kind, operations=None):
    """The registered operation's parameters as plain JSON, a fresh copy each call (``read_only.thawed``): what a model
    is sent about it, never the frozen copy (a JSON encoder refuses it) nor the declaring module's own dict.
    ValueError for a kind that is not registered."""
    operations = registry() if operations is None else operations
    if kind not in operations:
        raise ValueError("no wrapped operation %r" % (kind,))
    return read_only.thawed(operations[kind]["parameters"])


def admit(kind, measured, parameters=None, operations=None):
    """Before staging: None when the call runs in the turn, else the budget's ``LIMIT_EXCEEDED`` refusal
    (``wrapped_op_budget.admit``). ValueError for a kind that is not registered."""
    operations = registry() if operations is None else operations
    if kind not in operations:
        raise ValueError("no wrapped operation %r" % (kind,))
    return wrapped_op_budget.admit(operations[kind], measured, parameters)
