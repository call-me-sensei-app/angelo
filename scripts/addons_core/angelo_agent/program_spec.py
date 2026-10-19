# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed staged action-program contract. No code, expression or RNA escape hatch."""

import copy
import uuid
from .request_recovery import FIELD as RECOVERY_FIELD, CITATION_SCHEMA, citation

MAX_OPERATIONS = 32
MAX_INPUT_OBJECTS = 64
MAX_CHECKS = 32

REFERENCE_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 160}
REVISION_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 128}
SET_SCHEMA = {"type": "array", "maxItems": MAX_INPUT_OBJECTS, "items": {
    "type": "object", "properties": {"handle": REFERENCE_SCHEMA, "revision": REVISION_SCHEMA},
    "required": ["handle", "revision"], "additionalProperties": False}}


def _operation_schema():
    from .action_catalog import OPERATION_SCHEMAS
    return {"oneOf": [copy.deepcopy(schema) for schema in OPERATION_SCHEMAS.values()]}


def _checks_schema():
    from .validation_spec import CHECK_SCHEMA
    return copy.deepcopy(CHECK_SCHEMA)


def tools():
    return [{"name": "angelo_scene_program", "description":
             "Run 1-32 closed native Blender actions on isolated staged copies. Declare every existing object once "
             "by its document-scoped handle and exact revision: write_set for objects the program changes (also "
             "checked as read), read_set for the others; both default to empty. APPLY validates then requests one undoable "
             "commit; PREVIEW retains an isolated visual preview without live edits. Stop discards only uncommitted "
             "staged work at bounded quantum boundaries (at most 8 actions or 4 ms action time; "
             "individual kernels may exceed that time). No scripts, external calls, billing or automatic recovery.",
             "inputSchema": {"type": "object", "properties": {
                 "mode": {"type": "string", "enum": ["APPLY", "PREVIEW"]},
                 RECOVERY_FIELD: CITATION_SCHEMA,
                 "read_set": SET_SCHEMA, "write_set": SET_SCHEMA,
                 "operations": {"type": "array", "minItems": 1, "maxItems": MAX_OPERATIONS,
                                "items": _operation_schema()},
                 "expected_checks": {"type": "array", "maxItems": MAX_CHECKS, "items": _checks_schema(),
                                     "description": "Only requested results no operation states (dimensions "
                                                    "after a scale or modifier, counts, relations). The host checks "
                                                    "what the operations imply itself: never restate it."}},
                 "required": ["mode", "operations"], "additionalProperties": False},
             "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}}] + [
        {"name": name, "description": description,
         "inputSchema": {"type": "object", "properties": {"preview_id": {"type": "string", "format": "uuid"},
                                                              **({RECOVERY_FIELD: CITATION_SCHEMA} if name == "angelo_accept_preview" else {})},
                         "required": ["preview_id"], "additionalProperties": False},
         "annotations": {"readOnlyHint": readonly, "destructiveHint": False, "openWorldHint": False}}
        for name, readonly, description in (
            ("angelo_inspect_preview", True, "Inspect this conversation's retained staged preview and its delta; no live edits."),
            ("angelo_accept_preview", False, "Accept this conversation's exact retained preview only after fresh revision checks. Commit with one Blender Undo step; a stale preview is discarded instead of overwriting manual edits."),
            ("angelo_discard_preview", False, "Discard this conversation's retained preview and release temporary data. Does not undo any committed work."))]


PROGRAM_TOOLS = tools()
PROG_TOOLS = PROGRAM_TOOLS
PROGRAM_NAMES = frozenset({"angelo_scene_program", "angelo_inspect_preview", "angelo_accept_preview", "angelo_discard_preview"})


def refresh_tools():
    PROGRAM_TOOLS[:] = tools()
    return PROGRAM_TOOLS


def _printable(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(label + " must be a bounded nonempty printable identifier")
    return value


def _shown(value):
    """A bounded quoted echo of model-supplied text inside an error message."""
    text = "".join(character if ord(character) >= 32 else " " for character in str(value))
    return "'" + (text if len(text) <= 64 else text[:63] + "…") + "'"


def operation_error(error, index, operation):
    """``error`` with the program operation it is about named first, ``operations[i] (kind): ``.

    Its type, refusal code and details are kept. A model told only "operation
    is missing required fields" for one of nine operations re-sent the program
    four times (recorded GPT-6.1 Sol bridge one-shots). A kind is named only
    when it is a documented one; an error already naming its operation is kept.
    """
    from .action_catalog import OPERATION_SCHEMAS
    kind = operation.get("kind") if isinstance(operation, dict) else None
    label = "operations[%d]" % index + (" (%s)" % kind if isinstance(kind, str) and kind in OPERATION_SCHEMAS else "")
    if len(error.args) == 1 and isinstance(error.args[0], str) and not error.args[0].startswith("operations["):
        error.args = (label + ": " + error.args[0],)
    return error


def at_operation(index, operation, check):
    """``check(operation)``, a ValueError it raises naming that program operation (``operation_error``)."""
    try:
        return check(operation)
    except ValueError as error:
        raise operation_error(error, index, operation)


def observed_check_references(arguments):
    """The existing-object references of a program's checks, for inferred bindings (``identifiers``).

    A check target equal to the ``name`` of exactly one target-less operation is
    left out: validation binds it to that creation's alias, or rejects it with
    the fix (``_created_name_targets``). In a context with short identifiers,
    inference tried to bind it as an observed object first and refused it as
    "not an object handle" (in recorded runs a "create a cube named X"
    plan checking ``target: "X"`` lost a round).
    """
    from .validation_spec import check_references
    counts = {}
    for operation in arguments.get("operations") if isinstance(arguments.get("operations"), list) else ():
        parameters = operation.get("parameters") if isinstance(operation, dict) else None
        if isinstance(parameters, dict) and operation.get("target") is None and isinstance(parameters.get("name"), str):
            counts[parameters["name"]] = counts.get(parameters["name"], 0) + 1
    created = {name for name, count in counts.items() if count == 1}
    return [reference for check in arguments.get("expected_checks") or () for reference in check_references(check)
            if not reference.startswith("$") and not (reference in created and reference == check.get("target"))]


def _created_name_targets(checks, operations, revisions, aliases):
    """Checks naming an object this program creates, bound to that creation's alias.

    "Create a cube named X" plans often check ``target: "X"``, which used to
    cost a rejected round (6 of 6 extra Sonnet rounds in recorded runs). A check
    target that is neither a declared handle nor an alias, and equals the
    ``name`` of exactly one creating operation, can only mean that object: an
    undeclared existing object cannot be checked at all. The creation gets a
    host alias if it had none, and the program an ``object_name`` check that it
    got that name: Blender suffixes a taken name (``Rig.001``), and not every
    creating kind derives a name check (``armature_create`` derives none), so
    without it the bound check could pass on a differently named object.
    Anything else is left for the caller to reject.
    """
    created, named = {}, []
    for operation in operations:
        parameters = operation["parameters"]
        if operation["target"] is None and "alias" in parameters and isinstance(parameters.get("name"), str):
            created.setdefault(parameters["name"], []).append(operation)
    for check in checks:
        target = check.get("target")
        if not target or target in revisions or target in aliases or len(created.get(target, ())) != 1:
            continue
        parameters = created[target][0]["parameters"]
        if not parameters.get("alias"):
            number = 1
            while "$created_%d" % number in aliases:
                number += 1
            parameters["alias"] = "created_%d" % number
        alias = "$" + parameters["alias"].lstrip("$")
        aliases.add(alias)
        check["target"] = alias
        if not any(item["kind"] == "object_rename" and item["target"] == alias for item in operations):
            named.append({"kind": "object_name", "target": alias, "expected": target})  # Unless it is renamed.
    from .validation_spec import validate_checks
    for check in validate_checks(named) if named else ():
        if check not in checks:
            if len(checks) >= MAX_CHECKS:
                raise ValueError("A check names %s, which this program creates, and the program has no room for the "
                                 "check that it gets that name: target it by its $alias" % _shown(check["expected"]))
            checks.append(check)
    return checks


def validate_program(name, arguments=None, *, derive_checks=False):
    """Return the canonical program, or raise ValueError.

    Canonical arguments retain only the <=32 authored checks. Complete host
    obligations are admitted here and owned separately by ``program_checks``.
    ``derive_checks`` remains a compatibility keyword; every typed program,
    including recipe expansions, now has the same complete admission rules.
    """
    if arguments is None:
        name, arguments = "angelo_scene_program", name
    if name not in PROGRAM_NAMES:
        raise ValueError("Unknown native program tool")
    if not isinstance(arguments, dict):
        raise ValueError("Native program arguments must be an object")
    if name != "angelo_scene_program":
        if set(arguments) - ({"preview_id", RECOVERY_FIELD} if name == "angelo_accept_preview" else {"preview_id"}) or "preview_id" not in arguments:
            raise ValueError("Preview tools require only preview_id")
        try:
            identity = str(uuid.UUID(arguments["preview_id"]))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Choose an existing preview UUID, not a name or path") from error
        return {"preview_id": identity, **({RECOVERY_FIELD: citation(arguments[RECOVERY_FIELD])}
                                          if RECOVERY_FIELD in arguments else {})}
    required = {"mode", "operations"}
    from .camera_guards import FIELD as MEASURED, validate as validate_measured  # Host-written (orbits), unpublished.
    if set(arguments) - (required | {"read_set", "write_set", "expected_checks", RECOVERY_FIELD, MEASURED}) or required - set(arguments):
        raise ValueError("Declare mode and operations (with read_set/write_set for existing objects); "
                         "no unexpected program fields")
    if type(arguments["mode"]) is not str or arguments["mode"] not in {"APPLY", "PREVIEW"}:
        raise ValueError("A native program mode is APPLY or PREVIEW")
    normalized = {"mode": arguments["mode"]}
    if RECOVERY_FIELD in arguments:
        normalized[RECOVERY_FIELD] = citation(arguments[RECOVERY_FIELD])
    revisions = {}
    for key in ("read_set", "write_set"):
        # A write_set entry is bound and revision-checked exactly like a read_set
        # one (native_programs.bind), so an object is declared once, in either.
        entries = arguments.get(key, [])
        if not isinstance(entries, list) or len(entries) > MAX_INPUT_OBJECTS:
            raise ValueError("Program object sets are limited to 64 exact revision preconditions")
        seen, result = set(), []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"handle", "revision"}:
                raise ValueError("An object precondition requires exactly handle and revision")
            handle = _printable(entry["handle"], "Object handle", 160)
            revision = _printable(entry["revision"], "Object revision", 128)
            if handle.startswith("$") or handle in seen:
                raise ValueError("Object sets require unique existing document handles, not aliases")
            if handle in revisions and revisions[handle] != revision:
                raise ValueError("Read and write preconditions for the same object must have identical revisions")
            seen.add(handle)
            revisions[handle] = revision
            result.append({"handle": handle, "revision": revision})
        normalized[key] = result
    if len(revisions) > MAX_INPUT_OBJECTS:
        raise ValueError("A program may bind at most 64 existing objects in total")
    operations = arguments["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
        raise ValueError("A native program requires 1-32 bounded operations")
    from .action_catalog import OPERATION_SCHEMAS, validate_operation, affected_references, written_references
    from .batch_spec import program_aliases as batch_aliases
    write_handles = {entry["handle"] for entry in normalized["write_set"]}
    aliases, canonical, batches = set(), [], {}
    from .open_values import wrapped
    def admitted(operation):
        # A $alias in an open map; a kind that is not a string (unhashable too) is validate_operation's to refuse.
        if isinstance(operation, dict) and isinstance(operation.get("kind"), str) and operation["kind"] in OPERATION_SCHEMAS:
            operation = wrapped(operation, OPERATION_SCHEMAS[operation["kind"]], lambda text: text if text in aliases else None)
        operation = validate_operation(operation)
        # Every dependency has to have been observed and declared before work.
        for reference in affected_references(operation):
            if reference is None:
                continue
            if reference.startswith("$"):
                if reference not in aliases:
                    raise ValueError("An operation references a missing or forward-created alias (%s): a $alias "
                                     "names only an object created earlier in this program; target an existing "
                                     "object by its handle and declare it in write_set" % _shown(reference))
            elif reference not in revisions:
                raise ValueError("Every operation target/dependency must appear in the read or write set (%s is "
                                 "not declared): use an object's handle from the observation, never its name"
                                 % _shown(reference))
        unwritten = [reference for reference in written_references(operation)
                     if not reference.startswith("$") and reference not in write_handles]
        if unwritten:
            raise ValueError("Every mutated existing target (and object_join source) must appear in the write set (%s)"
                             % _shown(unwritten[0]))
        alias = operation["parameters"].get("alias")
        # A batch defines one alias per copy and counts as one operation; its copies have their own bound.
        for alias in batch_aliases(operation, batches) if operation["kind"] == "batch" else [alias] if alias else []:
            reference = alias if alias.startswith("$") else "$" + alias
            if reference in aliases:
                raise ValueError("A created alias cannot be redefined within a program")
            aliases.add(reference)
        return operation

    for index, operation in enumerate(operations):
        canonical.append(at_operation(index, operation, admitted))
    from .program_rules import check as program_rules
    program_rules(canonical)  # Linked data and the active material, across operations (before derived checks).
    normalized["operations"] = canonical
    checks = arguments.get("expected_checks", [])
    if not isinstance(checks, list) or len(checks) > MAX_CHECKS:
        raise ValueError("A program may request at most 32 expected checks")
    from .validation_spec import CHECK_SCHEMA, validate_checks
    if checks:
        checks = [wrapped(check, CHECK_SCHEMA, lambda text: text if text in aliases else None) for check in checks]
        checks = _created_name_targets(validate_checks(checks), canonical, revisions, aliases)
        from .program_names import private_name
        if any(check["kind"] == "object_name" and private_name(check["expected"]) for check in checks):
            raise ValueError("An object_name check cannot expect a private staged name (__angelo_...): expect the "
                             "name the object has after commit, as results report it")
    normalized["expected_checks"] = copy.deepcopy(checks)
    if MEASURED in arguments:
        normalized[MEASURED] = validate_measured(arguments[MEASURED], aliases)
    from .program_checks import admit_obligations
    for check in admit_obligations(normalized):
        target = check.get("target")
        if target and target not in revisions and target not in aliases:
            raise ValueError("Expected checks must target declared objects or existing staged aliases (%s is "
                             "neither): check an object this program creates through its $alias, an existing one "
                             "through its declared handle" % _shown(target))
    return copy.deepcopy(normalized)
