# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Short scene identifiers for one Claude native context, and their one resolver.

Every native owner (staging, verification, recipes, scene queries, views)
validates and compares only the long forms:

* object handle ``scn:<32-hex document epoch>:<session UID>``; the epoch changes
  on document load, Undo/Redo and ID replacement, so an older handle fails as
  stale in ``scene_queries.resolve_object``;
* object revision: the 64-hex SHA-256 ``object_revision``, compared exactly at
  staging and again before commit.

Repeated in every observation, receipt and program, those hex strings were a
large share of a Claude turn's output and prompt-cache writes. An
``IdentifierTable`` belongs to one native context (a Claude session) and lets
that context see and send short forms instead:

* handle ``o<n>``: the n-th distinct long handle this context was shown. Numbers
  are never reused within the table, so a short handle always denotes exactly
  one long handle, epoch included. After Undo or a reload the same object is
  shown under a new number; the old one still expands to its old long handle,
  which its owner rejects as stale. Nothing is ever re-bound.
* revision: the first ``REVISION_CHARS`` hex digits of a revision the table
  showed for that handle. It expands only to a revision shown for that very
  handle; an unknown or ambiguous prefix is rejected, and a shown revision is
  shortened only when no other revision shown for that handle shares its
  prefix. The expanded revision is then compared exactly, as before.

``expand_call`` is the single inbound resolver. It rewrites short forms to long
ones only where the tool's own input schema types a field as an object
reference, and leaves everything else (names, aliases, long forms) untouched
for the owner's validator. Reference fields are those typed with a handle
pattern (``action_schema_types.HANDLE``, ``scene_spec.HANDLE``), a check's
``validation_spec.TARGET`` fields, and the ``handle`` of a handle/revision
binding; a ``revision`` beside a handle is a revision field. New action kinds
whose parameters use the shared ``HANDLE`` type, and new check kinds using
``TARGET``, are covered without changes here.
``IdentifierTable.compact`` is the outbound side: it shortens every long
handle, and every revision beside one, in a value the model will read.

Tables are bounded and persisted with their native context
(``sessions.clean_runtime_sessions``); a table bound to another native ID is
never used. Claude contexts and Codex chat threads (toolset 27,
``CODEX_FIRST_TOOLSET``) get a table; voice threads, collaborators and the
external CLI keep long forms, which every path still accepts.

Inferred bindings (toolset 20). A table also remembers, per handle, the
revision the context's most recent observation showed as current
(``observe``). A program may then omit the handle/revision bindings of the
objects it touches, a recipe its revision and a verification its read set:
``expand_call`` binds each such object to that observed revision, mutated
targets in the write set and every other reference in the read set. An
object the context has no observed revision for is rejected with the reason
and the lookup that returns one (``binding_refusals``). Owners then compare the bound revision exactly, as before, so a
change since the model's last view of the object fails closed.
"""

from collections import OrderedDict
import re
import threading
from .binding_refusals import not_a_handle, omission, refusal
from .scene_binding import implicit_targets, named_targets
from .open_values import open_reference

TABLE_VERSION = 1
# The toolset that introduced short identifiers (routing.TOOLSET_VERSION). A
# native context recorded under an older toolset never saw a short form.
FIRST_TOOLSET = 19
# Codex threads keep long forms before this toolset (doc/angelo/gpt_launch.rst).
CODEX_FIRST_TOOLSET = 27
LONG_HANDLE = re.compile(r"scn:[a-f0-9]{32}:[1-9][0-9]{0,19}\Z")
SHORT_HANDLE = re.compile(r"o[1-9][0-9]{0,8}\Z")
FULL_REVISION = re.compile(r"[a-f0-9]{64}\Z")
REVISION_CHARS = 8
SHORT_REVISION = re.compile(r"[a-f0-9]{%d,63}\Z" % REVISION_CHARS)
MAX_SHORT_NUMBER = 999999999
# Distinct handles one native context keeps; the least recently shown is
# forgotten first (its short form then fails as unknown, never re-bound).
MAX_HANDLES = 1024
# Revisions remembered per handle, most recent last.
MAX_REVISIONS = 8
SHORT_HANDLE_SCHEMA = {"type": "string", "pattern": "^o[1-9][0-9]{0,8}$"}  # Model-facing (plan_schema).
SHORT_REVISION_SCHEMA = {"type": "string", "pattern": "^[a-f0-9]{%d,64}$" % REVISION_CHARS}
# Outbound: a revision is shortened only beside a handle in the same object.
_HANDLE_KEYS = ("handle", "object", "target")
_REVISION_KEYS = ("object_revision", "revision", "source_revision")
# Inbound: the sibling of a ``revision`` field that names its object.
_BINDING_KEYS = ("handle", "target")


class IdentifierError(ValueError):
    """A short identifier this native context cannot resolve. Nothing ran."""


def _reference_schemas():
    """Handle-patterned schema types, and the check target type.

    The generic check ``TARGET`` string type is a reference only inside a
    check (``_CHECK_FIELDS``); elsewhere an equal schema can be a name.
    """
    from .action_schema_types import HANDLE
    from .scene_spec import HANDLE as SCENE_HANDLE
    from .validation_spec import TARGET
    return (HANDLE, SCENE_HANDLE), (TARGET,)


# Tool argument fields whose items are closed checks (validation_spec).
_CHECK_FIELDS = ("expected_checks", "checks")


class IdentifierTable:
    """Short handle numbers and shown revisions of one native context. Thread-safe."""

    def __init__(self, native_id=None):
        self.native_id = native_id
        self._next = 1
        self._handles = OrderedDict()  # short -> long, least recently shown first
        self._shorts = {}  # long -> short
        self._revisions = {}  # long handle -> [full revision, ...], most recent last
        self._observed = {}  # long handle -> the revision its latest observation showed as current
        self._missing = {}  # long handle -> why its latest observation showed none (binding_refusals.omission)
        self._lock = threading.Lock()

    # Outbound -----------------------------------------------------------------

    def short_handle(self, handle):
        """The short form of a long handle, numbering it on first sight."""
        with self._lock:
            short = self._shorts.get(handle)
            if short is not None:
                self._handles.move_to_end(short)
                return short
            if self._next > MAX_SHORT_NUMBER:
                return handle
            short = "o%d" % self._next
            self._next += 1
            self._handles[short], self._shorts[handle] = handle, short
            while len(self._handles) > MAX_HANDLES:
                _, forgotten = self._handles.popitem(last=False)
                del self._shorts[forgotten]
                for held in (self._revisions, self._observed, self._missing):
                    held.pop(forgotten, None)
            return short

    def short_revision(self, handle, revision):
        """Record a revision shown for ``handle``; its unambiguous short form."""
        with self._lock:
            if handle not in self._shorts:
                return revision  # Only revisions of numbered handles can expand.
            shown = self._revisions.setdefault(handle, [])
            if revision in shown:
                shown.remove(revision)
            shown.append(revision)
            del shown[:-MAX_REVISIONS]
            prefix = revision[:REVISION_CHARS]
            if any(other != revision and other.startswith(prefix) for other in shown):
                return revision  # Two shown revisions share the prefix: show it whole.
            return prefix

    def compact(self, value):
        """A copy of a JSON value with long handles and their revisions shortened.

        Revisions are shortened only beside a long handle in the same object
        (``handle``, ``object`` or ``target``); free text is never rewritten.
        """
        if isinstance(value, dict):
            output = {}
            for key, item in value.items():
                if isinstance(key, str) and LONG_HANDLE.fullmatch(key):
                    key = self.short_handle(key)
                output[key] = self.compact(item)
            handle = next((value[key] for key in _HANDLE_KEYS
                           if isinstance(value.get(key), str) and LONG_HANDLE.fullmatch(value[key])), None)
            if handle is not None:
                for key in _REVISION_KEYS:
                    revision = value.get(key)
                    if isinstance(revision, str) and FULL_REVISION.fullmatch(revision):
                        output[key] = self.short_revision(handle, revision)
            return output
        if isinstance(value, list):
            return [self.compact(item) for item in value]
        if isinstance(value, str) and LONG_HANDLE.fullmatch(value):
            return self.short_handle(value)
        return value

    def observe(self, observation, latest=False):
        """Record what an observation (long forms) shows as each object's current revision.

        Call after ``compact`` numbered its handles. An entry without a
        revision (``revision_omitted``) forgets the earlier one: the context's
        latest view of that object has no exact state to bind. ``latest``: a
        whole turn observation, after which an earlier row's reason no longer applies.
        """
        entries = observation.get("objects") if isinstance(observation, dict) else None
        with self._lock:
            if latest:
                self._missing.clear()
            for entry in entries if isinstance(entries, list) else ():
                handle = entry.get("handle") if isinstance(entry, dict) else None
                if not isinstance(handle, str) or handle not in self._shorts:
                    continue
                revision = entry.get("object_revision")
                if isinstance(revision, str) and FULL_REVISION.fullmatch(revision):
                    self._observed[handle] = revision
                    self._missing.pop(handle, None)
                else:
                    self._observed.pop(handle, None)
                    self._missing[handle] = omission(entry)

    def known_handle(self, short):
        """The long handle this table numbered ``short``, or None (never an error)."""
        with self._lock:
            return self._handles.get(short)

    def observed(self, handle):
        """The revision the latest observation showed for ``handle``, or None."""
        with self._lock:
            return self._observed.get(handle)

    def bound_revision(self, handle):
        """The observed revision an omitted binding gets; raises if there is none."""
        revision = self.observed(handle)
        if revision is None and not (isinstance(handle, str) and LONG_HANDLE.fullmatch(handle)):
            raise IdentifierError(not_a_handle(handle))
        if revision is None:
            raise IdentifierError(self.why_missing(handle))
        return revision

    def why_missing(self, handle):
        """Why ``handle`` has no observed revision, and the lookup that returns one (binding_refusals.refusal)."""
        short = self.short_handle(handle)  # Numbered now if the context never saw it (a dependency).
        with self._lock:
            missing = self._missing.get(handle)
        return refusal(short, missing, scene=handle == getattr(self, "scene_handle", None))

    # Inbound ------------------------------------------------------------------

    def handle(self, value):
        """The long handle a short one denotes; any other value unchanged."""
        if not isinstance(value, str) or not SHORT_HANDLE.fullmatch(value):
            return value
        with self._lock:
            handle = self._handles.get(value)
        if handle is None:
            raise IdentifierError("Unknown object handle %s in this conversation: use a handle from the current "
                                  "observation or a result; never guess one" % value)
        return handle

    def revision(self, handle, value):
        """The full revision a short one denotes for ``handle``; any other value unchanged."""
        if not isinstance(value, str) or FULL_REVISION.fullmatch(value) or not SHORT_REVISION.fullmatch(value):
            return value
        if not isinstance(handle, str) or not LONG_HANDLE.fullmatch(handle):
            raise IdentifierError("A short revision needs its object's handle beside it")
        with self._lock:
            matches = [shown for shown in self._revisions.get(handle, ()) if shown.startswith(value)]
            short = self._shorts.get(handle, "this object")
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise IdentifierError("Revision %s of %s was not shown in this conversation: use the object_revision "
                                  "from the current observation or the latest result" % (value, short))
        raise IdentifierError("Revision %s of %s is ambiguous: use more of its digits" % (value, short))

    # Persistence --------------------------------------------------------------

    def to_dict(self, native_id):
        with self._lock:
            return {"version": TABLE_VERSION, "native_id": native_id, "next": self._next,
                    "handles": [[short, handle] for short, handle in self._handles.items()],
                    "revisions": [[handle, list(shown)] for handle, shown in self._revisions.items()],
                    "observed": [[handle, revision] for handle, revision in self._observed.items()]}

    @classmethod
    def load(cls, value, native_id):
        """A table persisted for exactly ``native_id``, or None (never a partial one)."""
        value = clean(value)
        if value is None or not native_id or value["native_id"] != native_id:
            return None
        table = cls(native_id)
        table._next = value["next"]
        for short, handle in value["handles"]:
            table._handles[short], table._shorts[handle] = handle, short
        for handle, shown in value["revisions"]:
            if handle in table._shorts:
                table._revisions[handle] = list(shown)
        for handle, revision in value["observed"]:
            table._observed[handle] = revision
        return table


def clean(value):
    """Persistence whitelist: a valid table record or None.

    Any malformed part rejects the whole record rather than keeping a subset
    whose numbering could no longer be trusted.
    """
    if type(value) is not dict or value.get("version") != TABLE_VERSION:
        return None
    native, number = value.get("native_id"), value.get("next")
    handles, revisions = value.get("handles"), value.get("revisions")
    if (type(native) is not str or not 1 <= len(native) <= 256 or type(number) is not int
            or not 1 <= number <= MAX_SHORT_NUMBER + 1 or type(handles) is not list or len(handles) > MAX_HANDLES
            or type(revisions) is not list or len(revisions) > MAX_HANDLES):
        return None
    shorts, longs = set(), set()
    for pair in handles:
        if (type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str or type(pair[1]) is not str
                or not SHORT_HANDLE.fullmatch(pair[0]) or not LONG_HANDLE.fullmatch(pair[1])
                or int(pair[0][1:]) >= number or pair[0] in shorts or pair[1] in longs):
            return None
        shorts.add(pair[0])
        longs.add(pair[1])
    for pair in revisions:
        if (type(pair) is not list or len(pair) != 2 or pair[0] not in longs or type(pair[1]) is not list
                or len(pair[1]) > MAX_REVISIONS
                or any(type(item) is not str or not FULL_REVISION.fullmatch(item) for item in pair[1])):
            return None
    observed = value.get("observed", [])  # Absent in toolset-19 tables: nothing observed yet.
    if type(observed) is not list or len(observed) > MAX_HANDLES or any(
            type(pair) is not list or len(pair) != 2 or pair[0] not in longs or type(pair[1]) is not str
            or not FULL_REVISION.fullmatch(pair[1]) for pair in observed):
        return None
    return {"version": TABLE_VERSION, "native_id": native, "next": number,
            "handles": [list(pair) for pair in handles], "revisions": [[pair[0], list(pair[1])] for pair in revisions],
            "observed": [list(pair) for pair in observed]}


class _NoTable:
    """Resolution for a context without a table: long forms only.

    Codex threads and a resumed context whose table is gone never saw a short
    form; one there is rejected before validation instead of reaching staging.
    """

    @staticmethod
    def handle(value):
        if isinstance(value, str) and SHORT_HANDLE.fullmatch(value):
            raise IdentifierError("Short handle %s is not available in this conversation: use the full scn: "
                                  "handle from the observation" % value)
        return value

    @staticmethod
    def revision(handle, value):
        if isinstance(value, str) and SHORT_REVISION.fullmatch(value):
            raise IdentifierError("Use the full 64-digit object_revision in this conversation")
        return value


_NO_TABLE = _NoTable()


def _same_schema(schema, references):
    return any(schema == reference for reference in references)


def _const_matches(value, branch):
    properties = branch.get("properties") if isinstance(branch, dict) else None
    if not isinstance(properties, dict) or branch.get("type", "object") != "object":
        return False
    return all(value.get(key) == child["const"] for key, child in properties.items()
               if isinstance(child, dict) and "const" in child)


def _expand(value, schema, table, references, check_types):
    if not isinstance(schema, dict):
        return value
    if isinstance(value, str):
        union = schema.get("oneOf") or schema.get("anyOf") or ()
        if any(_same_schema(candidate, references) for candidate in (schema, *union)):
            return table.handle(value)
        return open_reference(value, schema, table)  # An object in an open map (open_values).
    if isinstance(value, list):
        items = schema.get("items")
        return ([_expand(item, items, table, references, check_types) for item in value]
                if isinstance(items, dict) else value)
    if not isinstance(value, dict):
        return value
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        # An object-valued union: follow the one branch its const fields select.
        branches = [branch for branch in schema.get("oneOf") or schema.get("anyOf") or ()
                    if _const_matches(value, branch)]
        if len(branches) > 1:  # A typed variant whose const matched wins over a generic one.
            typed = [b for b in branches if any("const" in c for c in b.get("properties", {}).values() if isinstance(c, dict))]
            branches = typed if len(typed) == 1 else branches
        return _expand(value, branches[0], table, references, check_types) if len(branches) == 1 else value
    output = {}
    extra = schema.get("additionalProperties")
    for key, item in value.items():
        if key not in properties:
            # An open settings map: every value follows one schema ({"object": handle} included).
            output[key] = (_expand(item, extra, table, references, check_types) if isinstance(extra, dict)
                           else open_reference(item, extra, table))
        elif key == "handle" and "revision" in properties:
            output[key] = table.handle(item)  # A handle/revision binding.
        else:
            inner = references + check_types if key in _CHECK_FIELDS else references
            output[key] = _expand(item, properties[key], table, inner, check_types)
    if "revision" in output and "revision" in properties:
        handle = next((output[key] for key in _BINDING_KEYS if isinstance(output.get(key), str)), None)
        output["revision"] = table.revision(handle, output["revision"])
    return output


def _infer_program(arguments, table):
    """Bind each object a program touches but does not declare to its observed revision."""
    from .action_catalog import affected_references, written_references
    from .program_spec import at_operation, observed_check_references, operation_error
    declared = {entry.get("handle") for key in ("read_set", "write_set")
                for entry in arguments.get(key) or () if isinstance(entry, dict)}
    writes, reads = [], []
    for index, operation in enumerate(arguments.get("operations") or ()):
        target = operation.get("target") if isinstance(operation, dict) else None
        if (isinstance(target, str) and not target.startswith("$") and target not in declared
                and not LONG_HANDLE.fullmatch(target)):
            raise operation_error(IdentifierError(not_a_handle(target)), index, operation)
        written = at_operation(index, operation, written_references)  # The owner's own validation runs here.
        for reference in affected_references(operation):
            if reference is not None and not reference.startswith("$"):
                (writes if reference in written else reads).append(reference)
    reads.extend(observed_check_references(arguments))  # Not a created object's name (program_spec).
    missing = [handle for handle in dict.fromkeys(writes + reads) if handle not in declared]
    if not missing:
        return arguments
    output, written = dict(arguments), set(writes)
    for handle in missing:
        key = "write_set" if handle in written else "read_set"
        output[key] = list(output.get(key) or ()) + [{"handle": handle, "revision": table.bound_revision(handle)}]
    return output


def _infer_verification(arguments, table):
    from .validation_spec import check_references
    if "read_set" in arguments or not isinstance(arguments.get("checks"), list):
        return arguments
    handles = dict.fromkeys(reference for check in arguments["checks"] for reference in check_references(check))
    return dict(arguments, read_set=[{"handle": handle, "revision": table.bound_revision(handle)} for handle in handles])


def _object_or_handle(value, table):
    """A known short handle in a name-or-handle field, as its long handle; anything else unchanged.

    ``angelo_set_transform`` and the sculpt tools name their object, while a
    Claude context targets objects by handle: in recorded runs Sonnet passed
    ``o4`` to the first and Opus to angelo_inspect_sculpt. Only
    a short form this table numbered is rewritten; an unknown one stays text, and
    the executor resolves a long handle exactly (``blender_tools.transform_object``).
    """
    if not isinstance(value, str) or not SHORT_HANDLE.fullmatch(value):
        return value
    return table.known_handle(value) or value


def _named_objects(name, arguments, table):
    """Named objects (set_transform, alone or in ``angelo_apply_batch``, and sculpt tools), short handles resolved."""
    from .sculpt_spec import SCULPT_NAMES
    if not isinstance(table, IdentifierTable):
        return arguments
    if (name == "angelo_set_transform" or name in SCULPT_NAMES) and "object" in arguments:
        return dict(arguments, object=_object_or_handle(arguments["object"], table))
    if name == "angelo_apply_batch" and isinstance(arguments.get("operations"), list):
        operations = []
        for operation in arguments["operations"]:
            if (isinstance(operation, dict) and operation.get("tool") == "angelo_set_transform"
                    and isinstance(operation.get("arguments"), dict)):
                operation = dict(operation, arguments=_named_objects("angelo_set_transform", operation["arguments"], table))
            operations.append(operation)
        return dict(arguments, operations=operations)
    return arguments


def _infer(name, arguments, table):
    """Inferred bindings (module docstring); only where the model left them out."""
    arguments = _named_objects(name, arguments, table)
    if name == "angelo_scene_program":
        return _infer_program(implicit_targets(arguments, table), table)  # No target: the observed scene.
    if name == "angelo_scene_verify":
        return _infer_verification(arguments, table)
    if name in INFERRED_FIELDS and "revision" not in arguments and isinstance(arguments.get("target"), str):
        return dict(arguments, revision=table.bound_revision(arguments["target"]))
    return arguments


# Fields a model may leave to inference, by tool (``model_facing`` drops them
# from ``required``; programs' read_set/write_set are optional anyway).
INFERRED_FIELDS = {"angelo_apply_recipe": ("revision",), "angelo_expand_recipe": ("revision",),
                   "angelo_scene_verify": ("read_set",)}


def expand_call(name, arguments, table):
    """Model-submitted tool arguments with short identifiers resolved to long ones.

    The one inbound resolver for every tool, called before validation. Long
    forms and every non-identifier field pass through unchanged. Raises
    IdentifierError for a short form the table does not know, or for any short
    form when there is no table (Codex, or a context whose table is gone);
    nothing has run at that point.
    """
    from .tool_specs import TOOL_MAP
    tool = TOOL_MAP.get(name) if isinstance(name, str) else None
    if tool is None or not isinstance(arguments, dict):
        return arguments
    table = _NO_TABLE if table is None else table
    handles, check_types = _reference_schemas()
    expanded = _expand(named_targets(name, arguments), tool["inputSchema"], table, handles, check_types)
    return expanded if table is _NO_TABLE else _infer(name, expanded, table)


def model_facing(schema, inferred=()):
    """A copy of a tool schema whose strict identifier fields show only the short forms.

    Fields whose owners pin the long patterns (scene handles, 64-hex revisions)
    accept ``o<n>`` and revision prefixes in Claude's plan schema; generic
    reference fields already accept both. ``inferred`` names fields the host
    can infer (``INFERRED_FIELDS``); they are dropped from ``required``.
    Runtime validators keep the original schemas and see only expanded long
    forms with every binding filled in.
    """
    from .scene_spec import HANDLE as SCENE_HANDLE
    if isinstance(schema, list):
        return [model_facing(item, inferred) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if schema == SCENE_HANDLE:
        return dict(SHORT_HANDLE_SCHEMA)
    output = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            output[key] = {name: (dict(SHORT_REVISION_SCHEMA) if name == "revision" and isinstance(child, dict)
                                  and child.get("pattern") == "^[a-f0-9]{64}$" else model_facing(child, inferred))
                           for name, child in value.items()}
        elif key == "required" and isinstance(value, list):
            output[key] = [name for name in value if name not in inferred]
        elif key in ("enum", "const", "default"):
            output[key] = value
        else:
            output[key] = model_facing(value, inferred)
    return output
