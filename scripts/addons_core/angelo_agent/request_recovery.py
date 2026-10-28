# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a refused scene edit leaves for the rest of its request: full visibility, and a hold only where it may have run.

A refusal of a scene edit can open a request-owned recovery (``refused``). What it does depends on what the
refusal's own result says it changed:

* **Nothing changed** (a stale refusal with ``scene_changed`` false and no partial change, started mutation,
  cleanup or rollback failure: ``unchanged``; or one refused at commit preflight, whose typed edit was never
  applied although Blender's preparation callbacks ran: ``not_applied``, its note kept): nothing is held:
  other edits, scripts and jobs run as usual. The result names the targets that changed under the edit
  (``read``) and the tools that read them; a second such refusal adds its own. A corrected edit binds what a
  later read or observation showed; the owner refuses an older binding. Recorded (an Astra bridge trial): a
  cosmetic program refused as stale held the request until a cited correction, the model read every target
  the error named and the hold stayed, and the render job was refused, so the trial ended without its still.
* **May have run** (a stale refusal that does not say nothing changed, an unclassified failure, or any
  refusal that says the scene may have changed: ``may_have_changed``): every scene mutation of the request
  waits (``check`` raises ``RECOVERY_REQUIRED``) until each target has been read again: the named ones and the
  edit's write set, from its arguments or the program it staged (a preview's acceptance names only the
  preview). Only a read the model receives counts, and only for what it shows (``shown_targets``): a detail,
  a search row, the scene settings, a turn observation's row, or a read that finds the target deleted or
  expired; never a capture the host took without showing it, nor a collaborator's read. Once every target is
  read, scene mutations of that same model step are still refused, carrying what changed, and run from the
  next step (``step``): a read's result can go to code the model wrote before seeing it (a Codex exec cell
  that reads and then edits). A render job is not a mutation (``scene_mutation``): it changes nothing in
  the scene. An unclassified or internal failure, or a stale one naming no target, has nothing a read can
  explain and holds until the next request; a classified failure naming no target keeps its own text.
  A refusal never names an empty list of targets: a hold with none says scene edits wait for the next
  request. Every tool a recovery names is named as the chat's model calls it (``naming``,
  ``recovery_reads.callable_name``; ``tools.more__<name>`` for a deferred tool in Codex code mode).

Either way, the read that completes the targets carries ``changes``: each value-backed field that differs
from the host's earlier capture (before and after), the fields whose change no value shows (``unexplained``),
and the targets the host had no earlier capture of (``not_captured``) with the values their fresh capture
holds (``current``), as far as the host can show them. The model then decides; nothing here supplies or
withholds authority beyond the hold. Exact revision preconditions still refuse a stale binding at every
program's bind and commit.

``recovery_observation`` (``FIELD``) stays in the program, recipe and preview-accept schemas so that saved
chats and toolsets keep validating; it grants and requires nothing.

Records are memory-only, bounded (``recovery_evidence``) and cleared with the request.
"""

from collections import OrderedDict
import copy
import json
import re
import threading
from itertools import islice
import math
from types import MappingProxyType

from .job_spec import RENDERS
from .refusal_codes import RefusalError, STALE_CODES, refusal_code, refusal_details
from .recovery_evidence import MAX_BYTES, MAX_RECORDS

FIELD = "recovery_observation"
CITATION_SCHEMA = {"type": "string", "pattern": "^recovery:[a-f0-9]{32}$"}
READ_TOOLS = ("angelo_scene_object", "angelo_scene_settings")
# Tools whose authority concerns this scene, rather than account/memory/file work.
EXEMPT_MUTATIONS = frozenset({"angelo_discard_preview", "angelo_set_workspace_mode",
                              "angelo_workers_start"})   # Starts workers on their own files; this scene is untouched.
UNCLASSIFIED = frozenset({"UNKNOWN", "INTERNAL_ERROR"})
# Fields of a refusal that say the edit may have changed the scene after all.
MAY_HAVE_CHANGED = ("partial_changes_possible", "mutation_started", "cleanup_pending", "cleanup_error",
                    "cleanup_warning", "rollback_error")
MAX_CHANGES, MAX_CHANGE_BYTES = 64, 16384


def scene_mutation(name, arguments=None):
    from .tool_specs import TOOL_MAP, FILE_TOOLS, MEMORY_NAMES, FLOW_NAMES, TEAM_NAMES, STUDIO_NAMES
    tool = TOOL_MAP.get(name)
    if tool is None or tool["annotations"]["readOnlyHint"] or name in EXEMPT_MUTATIONS | FILE_TOOLS | MEMORY_NAMES:
        return False
    if name == "angelo_job_start":  # A render reads the scene and writes a new file; bakes and exports are held.
        return (arguments or {}).get("kind") not in RENDERS
    if name == "angelo_team_library_import":
        return (arguments or {}).get("into", "SCENE") != "STUDIO"
    if name in FLOW_NAMES | TEAM_NAMES | STUDIO_NAMES:
        return name in {"angelo_studio_import_3d", "angelo_flow_run"}
    return True


def unchanged(result):
    """Whether a refusal states, by its own fields, that it changed nothing in the scene."""
    return result.get("scene_changed") is False and not any(result.get(key) for key in MAY_HAVE_CHANGED)


def not_applied(result):
    """A refusal at commit preflight: the typed edit was never applied, though preparation callbacks ran.

    ``ProgramCommitController._preparation_result`` marks it ``mutation_started: false`` beside
    ``partial_changes_possible`` and a note: Blender callbacks may have changed the scene, the edit did not.
    """
    return (result.get("mutation_started") is False and result.get("scene_changed") is not True
            and not any(result.get(key) for key in MAY_HAVE_CHANGED if key not in ("partial_changes_possible",
                                                                                 "mutation_started")))


def may_have_changed(result):
    """Whether a result states, by its own fields, that the scene may have changed (whatever its code)."""
    return result.get("scene_changed") is True or any(result.get(key) for key in MAY_HAVE_CHANGED)


def record_model_step(context):
    """A model response of the lead request ended: ``RecoveryBarrier.step`` (never a collaborator's context)."""
    if not context.cancelled:
        context.host.recovery_barrier.step()


def citation(value):
    """The schema's ``recovery_observation`` value, validated; it has no effect (module docstring)."""
    if not isinstance(value, str) or re.fullmatch(r"recovery:[a-f0-9]{32}", value) is None:
        raise RefusalError("recovery_observation is a recovery:<32 hex> id; it is optional and has no effect",
                           code="VALIDATION")
    return value


def _bindings(arguments):
    found = {row["handle"]: row["revision"] for key in ("read_set", "write_set")
             for row in arguments.get(key, ()) if isinstance(row, dict) and "handle" in row and "revision" in row}
    if isinstance(arguments.get("target"), str) and isinstance(arguments.get("revision"), str):
        found[arguments["target"]] = arguments["revision"]
    return found


def _writes(arguments):
    rows = [row["handle"] for row in arguments.get("write_set", ()) if isinstance(row, dict) and "handle" in row]
    return rows + ([arguments["target"]] if isinstance(arguments.get("target"), str) else [])


def shown_targets(tool, result):
    """The targets a read showed the model, and the ones it found deleted or expired.

    Only what the result itself lists counts: a detail's object, a search's rows, the settings' scene, a turn
    observation's object rows and scene entry. Captures the host took while reading (a search scans every
    object) supply before/after values, never the fact that the model saw a target.
    """
    shown, gone = set(), set()
    if not isinstance(result, dict):
        return shown, gone
    if result.get("error"):
        # A read of a deleted or expired handle (scene_queries.resolve_object) is that target's current state.
        if tool in READ_TOOLS and refusal_code(result) == "STALE_REVISION":
            gone.update(handle for handle in refusal_details(result).get("handles") or () if isinstance(handle, str))
        return shown, gone
    if tool == "angelo_scene_object":
        shown.add(result.get("object"))
    elif tool == "angelo_scene_settings":
        shown.add(result.get("handle"))
    elif tool in ("angelo_scene_search", None):  # None: a turn observation (turn_context).
        key = "object" if tool else "handle"
        shown.update(row.get(key) for row in result.get("objects") or () if isinstance(row, dict))
        if tool is None and isinstance(result.get("scene_settings"), dict):
            shown.add(result["scene_settings"].get("handle"))
    return {handle for handle in shown if isinstance(handle, str)}, gone


class RecoveryBarrier:
    def __init__(self, naming=None, restorable=None):
        self._lock = threading.RLock()
        self.naming = naming  # name -> the name the chat's model calls (recovery_reads.namer); None: as is.
        self.restorable = restorable  # () -> whether Restore takes the chat's request snapshots; None: it does.
        self.clear()

    def _restorable(self):
        """Whether to name the pre-request checkpoint: never for a chat Restore refuses (a worker chat, D10's rule),
        nor when that cannot be told (fail closed)."""
        try:
            return self.restorable is None or self.restorable() is True
        except Exception:   # noqa: BLE001 - unknown: no advice to restore.
            return False

    def _names(self, names):
        return [self.naming(name) if self.naming else name for name in names]

    def clear(self):
        with self._lock:
            self.records = OrderedDict()
            self.pending = None
            self.generation = 0
            self.steps = 0  # Model steps of this request (``step``).

    def step(self):
        """A model step ended: a later call was written by a model that had this step's results.

        Codex: each ``thread/tokenUsage/updated`` of the lead request's turn (``provider_usage``), which Codex
        sends only after every tool call of that response, an exec cell's included, has resolved, and each turn
        end (also ``provider_usage``). Claude: each round (``Controller.record_model_round``), submitted after the
        previous round's plan ran and before its own plan runs. So two calls with the same count came from one
        step. Known limit: a Codex exec cell that yields keeps running past its response's usage report, and
        app-server reports usage only when the provider does; closing both needs the runtime to name a call's
        exec cell.
        """
        with self._lock:
            self.steps += 1

    def _keep(self, records):
        for handle, row in records.items():
            key = (handle, row["revision"])
            self.records[key] = copy.deepcopy(row)
            self.records.move_to_end(key)
            while (len(self.records) > MAX_RECORDS or
                   sum(value["bytes"] for value in self.records.values()) > MAX_BYTES):
                self.records.popitem(last=False)

    def _prior(self, handle, revision):
        """The capture of what the edit bound (its revision), else the latest one of an unbound target."""
        if revision is not None:
            return self.records.get((handle, revision))
        return next((row for (key, _revision), row in reversed(self.records.items()) if key == handle), None)

    def refused(self, name, arguments, result, *, credit=True, program=None):
        """A finished call's result, with the recovery its refusal opens (module docstring).

        ``credit`` is false for a call the model does not read itself (a collaborator's): its read clears
        nothing. ``program`` is the program the call staged or committed when its arguments do not carry it
        (a preview's acceptance names only the preview): its write set is where a partial change can be.
        """
        if result.get("script_error") is True:
            return result  # Reviewed-script corrections have their own disclosed partial-recovery contract.
        if name in READ_TOOLS:
            # A read that found a target deleted or expired has read it.
            return self.read(name, {}, result) if credit else result
        # A classified wrapper of an unclassified cause (CLEANUP_FAILED after an UNKNOWN hydration failure,
        # program_storage) is as unexplained as its cause: the barrier reads the cause, not only the wrapper.
        details = refusal_details(result)
        code = details.get("cause_code") if details.get("cause_code") in UNCLASSIFIED else refusal_code(result)
        if not result.get("error") or not scene_mutation(name, arguments):
            return result
        # A stale or unclassified refusal holds unless it states that nothing changed; any other code (a live
        # check that failed after writes whose rollback failed) holds when it states that something may have.
        # A preflight refusal whose edit was never applied holds nothing either; its note stays in the result.
        ran = not unchanged(result) and not not_applied(result)
        holding = code in UNCLASSIFIED or ran and (code in STALE_CODES or may_have_changed(result))
        if code not in STALE_CODES | UNCLASSIFIED and not holding:
            return result  # A classified refusal that changed nothing: the model corrects it as usual.
        with self._lock:
            sources = [arguments] + ([program] if isinstance(program, dict) else [])
            bindings = {handle: revision for source in sources for handle, revision in _bindings(source).items()}
            named = [handle for handle in details.get("handles") or () if isinstance(handle, str)] or list(bindings)
            writes = [handle for source in sources for handle in _writes(source)] if holding else []
            targets = list(dict.fromkeys(named + writes))
            if not targets and (not holding or code not in STALE_CODES | UNCLASSIFIED):
                # Nothing to show; a classified failure that names no target keeps its own recovery text.
                return result
            closed = holding and (code in UNCLASSIFIED or not targets)  # Nothing a read can explain.
            pending = self.pending
            if pending is not None and pending["holding"] and not holding:
                return self._annotated(result)  # A hold in force is never relaxed, nor widened, by a refusal that ran nothing.
            if pending is None or holding and not pending["holding"]:
                pending = self.pending = {"code": code, "holding": holding, "targets": [], "read": set(), "gone": set(),
                                          "closed": False, "before": {}, "changes": [], "current": [], "unexplained": [],
                                          "not_captured": [], "bytes": 0}
            # A second refusal of the same kind adds its targets: neither one's unread targets are lost.
            pending["closed"] = pending["closed"] or closed
            if code in UNCLASSIFIED or pending["code"] not in UNCLASSIFIED:
                pending["code"] = code
            for handle in targets[:MAX_RECORDS]:
                if handle not in pending["targets"]:
                    pending["targets"].append(handle)
                    pending["before"][handle] = self._prior(handle, bindings.get(handle))
                pending["read"].discard(handle)  # Named again: read it again.
            pending.pop("complete_step", None)
            self.generation += 1
            return self._annotated(result)

    def _annotated(self, result):
        # Native failure recovery remains an independent fact. The barrier's
        # description must not overwrite it or become authority supplied by a receipt.
        field = "recovery_requirements" if "recovery" in result else "recovery"
        holding = self.pending["holding"]
        return dict(result, **{field: self.description()}, **({"recovery_required": True} if holding else {}))

    def observe(self, records, shown=(), gone=()):
        """Keep bounded native captures already gathered by observation owners; count what a read showed."""
        with self._lock:
            self._keep(records)
            pending = self.pending
            if pending is None or pending["closed"]:
                return
            for handle in [handle for handle in pending["targets"] if handle not in pending["read"]]:
                if handle in gone:
                    pending["gone"].add(handle)
                elif handle in shown:
                    self._explain(handle, records.get(handle))
                else:
                    continue
                pending["read"].add(handle)
            if self._complete() and "complete_step" not in pending:
                pending["complete_step"] = self.steps  # When the last target was read; later reads change nothing.

    def _explain(self, handle, after):
        """One target's change since the host's earlier capture, kept within the change budget.

        Without an earlier capture there is nothing to compare: the target is named in ``not_captured`` and the
        values its fresh capture holds are shown as ``current``.
        """
        pending, before = self.pending, self.pending["before"].get(handle)
        if before is None or after is None:
            pending["not_captured"].append(handle)
            rows = [{"handle": handle, "field": field, "value": value} for field, value in (after or {}).get(
                "values", {}).items()]
            self._keep_rows("current", rows)
            return
        changed = sorted(field for field in before["fields"].keys() | after["fields"].keys()
                         if before["fields"].get(field) != after["fields"].get(field))
        rows = [{"handle": handle, "field": field, "before": before["values"][field], "after": after["values"][field]}
                for field in changed if field in before["values"] and field in after["values"]]
        hidden = [field for field in changed if field not in before["values"] or field not in after["values"]]
        if hidden or not changed and before["revision"] != after["revision"]:
            pending["unexplained"].append({"handle": handle, "fields": hidden})  # [] : only the revision moved.
        self._keep_rows("changes", rows)

    def _keep_rows(self, key, rows):
        """Add value rows to ``changes`` or ``current`` while both stay within one row and byte budget."""
        pending = self.pending
        size = len(json.dumps(rows, ensure_ascii=False).encode("utf-8")) if rows else 0
        if (len(pending["changes"]) + len(pending["current"]) + len(rows) > MAX_CHANGES
                or pending["bytes"] + size > MAX_CHANGE_BYTES):
            pending["changes_omitted"] = pending.get("changes_omitted", 0) + len(rows)
            return
        pending[key].extend(copy.deepcopy(rows))
        pending["bytes"] += size

    def _complete(self):
        pending = self.pending
        return pending is not None and not pending["closed"] and len(pending["read"]) == len(pending["targets"])

    def description(self):
        with self._lock:
            pending = self.pending
            if pending is None:
                return {"holding": False, "reason": "No recovery is open."}
            unread = [handle for handle in pending["targets"] if handle not in pending["read"]]
            if pending["closed"]:
                reason = ("An unclassified or internal failure may have changed the scene in ways no read can show. "
                          "Scene edits wait for the next request; read tools stay available" + (
                              ", and the pre-request checkpoint can be restored." if self._restorable() else
                              ". This chat's request snapshots cannot be restored.")
                          if pending["code"] in UNCLASSIFIED else
                          "The scene changed under this edit, or the edit may have changed it, and the refusal names "
                          "no target to read. Scene edits wait for the next request; read tools stay available.")
                return {"holding": True, "reason": reason, "read_tools": self._names(READ_TOOLS)}
            if not unread:
                result = {"holding": pending["holding"], "changes": copy.deepcopy(pending["changes"]), "reason": (
                    "You have read every target. changes lists what differs from the host's earlier capture. Scene "
                    "edits run from your next step, not in the step that read it." if pending["holding"] else
                    "You have read every target this recovery named; it is closed. changes lists what differs "
                    "from the host's earlier capture.")}
                for key in ("current", "unexplained", "not_captured", "changes_omitted"):
                    if pending.get(key):
                        result[key] = copy.deepcopy(pending[key])
                if pending["gone"]:
                    result["deleted_or_expired"] = sorted(pending["gone"])
                return result
            if pending["holding"]:
                reason = ("This edit may have changed the scene before it was refused. Scene edits wait until you "
                          "read each target in read again; each read shows its current values. Objects it created "
                          "may remain: %s lists them." % self._names(("angelo_scene_search",))[0])
            else:
                reason = ("Nothing was changed. These targets changed after you last read them: read them again to "
                          "see their current values and bind their current revisions. Nothing else waits.")
            return {"holding": pending["holding"], "reason": reason, "read": unread, "read_tools": self._names(READ_TOOLS)}

    def check(self, name, arguments):
        """Refuse a scene mutation while a hold is open; False (admitted) otherwise.

        Checked before inferred binding, admission, dispatch and live commit, and free of side effects a
        refused call could leave (Codex builds its activity summary through the same check). Once every target
        is read, scene mutations of that same model step are still refused, carrying what changed: a read's
        result can go to code the model wrote before seeing it (a Codex exec cell that reads and then edits).
        From the next step (``step``) they run, and the hold closes.
        """
        with self._lock:
            pending = self.pending
            if pending is None or not pending["holding"] or not scene_mutation(name, arguments):
                return False
            if self._complete() and self.steps > pending.get("complete_step", self.steps):
                self.pending = None  # Permanent from here on: closing it twice changes nothing.
                return False
            description = self.description()
            if self._complete():
                raise RefusalError("Every target was read again in this step. Scene edits wait for your next step, "
                                   "so that what changed (recovery.changes) reaches you before any edit runs.",
                                   code="RECOVERY_REQUIRED", details={"recovery": description})
            unread = description.get("read") or ()
            again = (" Read again: " + ", ".join(unread[:16]) + " (" + ", ".join(description["read_tools"]) + ")."
                     if unread else "")   # A hold that names no target says what it waits for in its reason.
            raise RefusalError("Scene edits wait for an informed look after a refused edit that may have run. "
                               + description["reason"] + again, code="RECOVERY_REQUIRED", details={"recovery": description})

    def read(self, tool, records, result, *, credit=True):
        """A read-only tool's result after it ran: what it showed counts as read, and it carries the recovery.

        ``credit`` is false for a read the model does not receive itself (a collaborator's).
        """
        shown, gone = shown_targets(tool, result)
        if not isinstance(result, dict) or not credit:
            return result
        with self._lock:
            self.observe(records if not result.get("error") else {}, shown, gone)
            return self.annotate_read(result, shown | gone)

    def annotate_read(self, result, shown=()):
        """A read's result with the open recovery: always while holding, else when it read a target.

        The read that completes the targets carries the changes; it ends a recovery that holds nothing.
        """
        with self._lock:
            pending = self.pending
            if pending is None:
                return result
            complete = self._complete()
            if not pending["holding"] and not complete and not set(shown) & set(pending["targets"]):
                return result
            field = "recovery_requirements" if "recovery" in result else "recovery"
            annotated = dict(result, **{field: self.description()})
            if complete and not pending["holding"]:
                self.pending = None  # A hold closes at the first edit of a later step (check).
            return annotated


def recovery_value(value, depth=0, remaining=None):
    """Keep useful JSON recovery metadata without invoking arbitrary reprs."""
    if remaining is None:
        remaining = [2048]
    if depth > 8 or remaining[0] <= 0:
        return None
    remaining[0] -= 1
    if value is None or type(value) in (str, bool):
        return value
    if type(value) is int:
        return value if value.bit_length() <= 4096 else None
    if type(value) is float:
        return value if math.isfinite(value) else None
    if isinstance(value, (dict, MappingProxyType)):
        return {key: recovery_value(item, depth + 1, remaining)
                for key, item in islice(value.items(), 256) if isinstance(key, str)}
    if isinstance(value, (tuple, list)):
        return [recovery_value(item, depth + 1, remaining) for item in value[:256]]
    return None
