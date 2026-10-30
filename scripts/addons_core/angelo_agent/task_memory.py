# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded host-owned project notes, not a runtime or authorization ledger.

All state-changing helpers are pure: they return a new session state. Only the
host supplies provenance/evidence and native results; these arguments are not
in a model-callable schema. Persisted provenance is historical attribution,
never a capability. Reload/Undo/restore drops live handles but retains the brief.
"""

import copy
import json
import math
import re
from itertools import islice

from .memory_evidence import (
    AdmittedEvidence, PROVENANCE_KINDS, SCOPE_TOKEN_PATTERN, admit_evidence,
    checked_handles, scope_token, validate_provenance,
)
from .legacy_names import current_tool_name
from .memory_spec import PATCH, MAX_POINTER, UNSAFE_CHARACTER, pointer, text, unsafe_text, validate_memory, validate_value

MEMORY_VERSION = 1
MAX_RESULTS = 24
MAX_HISTORY = 12
MAX_MEMORY_BYTES = 65536
MAX_CONTEXT = 32768
RESTORE_REASONS = frozenset({"undo", "redo", "restore", "restart", "document_change", "external_edit"})
_TOOL = re.compile(r"angelo_[a-z0-9_]{1,80}\Z")
_HANDLE_KEYS = frozenset({"id", "object_id", "object_ids", "object_handle", "object_handles", "session_uid",
                          "mesh_revision", "mesh_revision_before", "topology_revision", "view_reference", "baseline"})


def new_memory(profile_id=None, document_identity=None):
    return {"version": MEMORY_VERSION,
            "scope": {"profile": scope_token(profile_id), "document": scope_token(document_identity),
                      "project": None, "project_revision": None},
            "revision": 0, "generation": 0,
            "brief": {"objective": "", "style": "", "units": {"system": "NONE", "scale_length": 1},
                      "dimensions": [], "constraints": []},
            "references": [], "assets": [], "planned_steps": [], "provenance": {},
            "verified_results": [], "history": [], "omitted_results": 0, "omitted_history": 0,
            "stale": True, "stale_reason": "No native scene read-back yet"}


def _counter(value):
    return min(value, 2147483647) if type(value) is int and value >= 0 else 0


def _safe_text(value, limit=192):
    if not isinstance(value, str):
        return None
    clipped = value[:limit]
    if unsafe_text(clipped):
        return "[REDACTED]"
    # Escaped controls remain data and cannot forge a new context/approval line.
    value = UNSAFE_CHARACTER.sub(lambda match: "\\u%04x" % ord(match.group()), clipped)
    return value[:limit - 1] + "…" if len(value) > limit or len(clipped) == limit else value


_RESULT_FIELDS = frozenset({
    "success", "scene", "object", "name", "type", "object_count", "object_count_before", "object_count_after",
    "objects", "operations", "location", "dimensions", "selected", "verified", "changes", "file_changes",
    "added", "removed", "modified", "fields", "written", "deleted", "path", "bytes", "journaled", "undoable",
    "truncated", "uncertain", "partial_changes_possible", "serialization_failed", "scene_changed", "mode",
    "workspace_mode", "bounds_world", "min", "max", "center_world", "diagonal_world", "vertices", "faces",
    "masked_points", "fully_masked_points", "hidden_faces", "mask_mean", "multires", "levels", "sculpt_levels",
    "total_levels", "added_levels", "displacement", "max_displacement", "mean_displacement", "changed_vertices",
    "preserves_base_topology", "asset_id", "status", "progress", "kind", "mesh_revision", "mesh_revision_before",
    "topology_revision", "object_id", "object_ids", "session_uid", "object_handles", "id", "generation",
    "view_reference", "views", "landmarks", "region", "face_set", "position", "normal", "hits", "face",
    "radius", "distance", "locations_read_back", "revision_scope", "requires_lower_working_level",
})


def _result_summary(value, depth=0, budget=None):
    """Whitelisted measured public fields; never raw output/error/reasoning/code."""
    if budget is None:
        budget = [160]
    if depth > 5 or budget[0] <= 0:
        return None
    budget[0] -= 1
    if value is None or type(value) is bool:
        return value
    if type(value) in {int, float}:
        return value if abs(value) <= 1e18 and math.isfinite(value) else None
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, dict):
        result = {}
        for key in sorted(key for key in value if isinstance(key, str) and key in _RESULT_FIELDS):
            item = value[key]
            if key == "path":
                try:
                    result[key] = pointer("WORKSPACE", item)
                except (ValueError, TypeError):
                    continue
            else:
                result[key] = _result_summary(item, depth + 1, budget)
        return result
    if isinstance(value, (tuple, list)):
        return [_result_summary(item, depth + 1, budget) for item in value[:16]]
    return None


class KeptSummaries:
    """``_result_summary`` of summaries read from JSON (``_drop_handles`` of it for a stale entry), kept by their JSON
    text while the reader still holds them.

    The key is the stored summary's JSON (order, ``1`` vs ``1.0`` vs ``true`` all kept apart) and each answer is a
    fresh object decoded from the whitelisted summary's JSON, which holds only JSON values. Its values must be decoded
    JSON (a file's, as the mirror's are): an int or float subclass encodes as its base type and would share its key,
    though ``_result_summary`` treats it apart. ``turn`` starts a read:
    what the previous read did not use is dropped, so a reader keeps at most one memory's summaries.
    """

    def __init__(self):
        self._previous, self._current = {}, {}

    def turn(self):
        self._previous, self._current = self._current, {}
        return self

    def summary(self, value, stale=False):
        try:
            key = ("stale:" if stale else ":") + json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError, RecursionError, OverflowError):   # Not JSON: whitelisted afresh.
            summary = _result_summary(value)
            return _drop_handles(summary) if stale else summary
        found = self._current.get(key) or self._previous.get(key)
        if found is None:
            summary = _result_summary(value)
            found = json.dumps(_drop_handles(summary) if stale else summary, ensure_ascii=False, separators=(",", ":"))
        self._current[key] = found
        return json.loads(found)


def _drop_handles(value):
    if isinstance(value, dict):
        return {key: _drop_handles(item) for key, item in value.items() if key not in _HANDLE_KEYS
                and not (key == "object" and isinstance(item, str) and item.startswith("scn:"))}
    if isinstance(value, list):
        return [_drop_handles(item) for item in value]
    return value


def _invalid_measurement(value, depth=0, budget=None):
    if budget is None:
        budget = [2048]
    if depth > 8 or budget[0] <= 0:
        return True
    budget[0] -= 1
    if value is None or type(value) in {str, bool}:
        return False
    if type(value) is int:
        return value.bit_length() > 128
    if type(value) is float:
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_invalid_measurement(item, depth + 1, budget) for key, item in value.items()
                   if isinstance(key, str) and key in _RESULT_FIELDS)
    if isinstance(value, (list, tuple)):
        return any(_invalid_measurement(item, depth + 1, budget) for item in value)
    return True


def _stale(memory, reason, dropped=False):
    """``memory`` and every entry stale, without live handles (``dropped``: its summaries already have none)."""
    memory["stale"] = True
    memory["stale_reason"] = reason
    for entry in memory["verified_results"]:
        entry["stale"] = True
        entry["summary"] = entry["summary"] if dropped else _drop_handles(entry["summary"])
        entry["object_handles"] = []
    for field in ("references", "assets"):
        for entry in memory[field]:
            entry["stale"] = True
    return memory


def _checked_field(value, schema, default):
    try:
        return validate_value(value, schema)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return copy.deepcopy(default)


def clean_memory(value, profile_id=None, rehydrating=False, moved_from_profile_id=None, summaries=None):
    """Strict persistence whitelist, forward-safe migration and profile isolation.

    ``summaries`` (``KeptSummaries``): a reader of JSON (a worker chat's mirror) passes its own, so each stored result
    summary it still holds is whitelisted once, not on every read of the memory.

    ``moved_from_profile_id`` is the identity this same profile had before the
    host moved its folder (the pre-rename state folder): memory scoped to it is
    re-scoped to ``profile_id`` instead of being dropped. Memory from any other
    profile is still never rehydrated.

    Rehydration is never verification: all retained native evidence is stale and
    live handles/revisions are removed even if JSON claims ``stale: false``.
    """
    output = new_memory(profile_id)
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != MEMORY_VERSION:
        return output
    scope = value.get("scope")
    if not isinstance(scope, dict):
        return output
    supplied_profile = scope.get("profile")
    if supplied_profile is not None and (not isinstance(supplied_profile, str) or not SCOPE_TOKEN_PATTERN.fullmatch(supplied_profile)):
        return output
    expected_profile = scope_token(profile_id)
    if expected_profile is not None and supplied_profile != expected_profile:
        if moved_from_profile_id is None or supplied_profile != scope_token(moved_from_profile_id):
            return output
        supplied_profile = expected_profile
    document = scope.get("document")
    if document is not None and (not isinstance(document, str) or not SCOPE_TOKEN_PATTERN.fullmatch(document)):
        return output
    project, revision = scope.get("project"), scope.get("project_revision")
    if any(item is not None and (not isinstance(item, str) or not SCOPE_TOKEN_PATTERN.fullmatch(item))
           for item in (project, revision)) or (project is None) != (revision is None):
        project = revision = None
    output["scope"] = {"profile": supplied_profile, "document": document,
                       "project": project, "project_revision": revision}
    output["revision"] = _counter(value.get("revision"))
    output["generation"] = _counter(value.get("generation"))
    brief = value.get("brief") if isinstance(value.get("brief"), dict) else {}
    for field, default in output["brief"].items():
        output["brief"][field] = _checked_field(brief.get(field, default), PATCH["properties"][field], default)
    output["planned_steps"] = _checked_field(value.get("planned_steps", []), PATCH["properties"]["planned_steps"], [])
    sources = value.get("provenance") if isinstance(value.get("provenance"), dict) else {}
    for field in PATCH["properties"]:
        try:
            output["provenance"][field] = validate_provenance(sources[field])
        except (KeyError, ValueError, TypeError):
            continue
    for field in ("references", "assets"):
        entries = value.get(field, [])
        if not isinstance(entries, list):
            continue
        limit = PATCH["properties"][field]["maxItems"]
        seen = set()
        for entry in entries[:limit]:
            if not isinstance(entry, dict):
                continue
            try:
                record = validate_value({key: entry[key] for key in ("source", "id", "label")},
                                        PATCH["properties"][field]["items"])
                origin = validate_provenance(entry.get("provenance"), {"user_message", "approved_update"})
            except (KeyError, ValueError, TypeError):
                continue
            key = (record["source"], record["id"])
            if key in seen:
                continue
            seen.add(key)
            output[field].append(dict(record, provenance=origin, stale=entry.get("stale") is not False))
    results = value.get("verified_results", [])
    if isinstance(results, list):
        for entry in results[-MAX_RESULTS:]:
            if not isinstance(entry, dict):
                continue
            try:
                identity = text(entry["id"], MAX_POINTER, 1)
                # Results recorded before the rename name tools by their pre-rename names.
                tool = current_tool_name(text(entry["tool"], 96, 1))
                if not _TOOL.fullmatch(tool):
                    continue
                origin = validate_provenance(entry.get("provenance"), {"host_result"})
                entry_document = entry.get("document")
                if (not isinstance(entry_document, str) or not SCOPE_TOKEN_PATTERN.fullmatch(entry_document)
                        or entry.get("status") not in {"verified", "failed", "unverified"}):
                    continue
                stale = (entry.get("stale") is not False or entry.get("generation") != output["generation"]
                         or entry_document != document)
                summary = (_result_summary(entry.get("summary", {})) if summaries is None
                           else summaries.summary(entry.get("summary", {}), stale or rehydrating))   # Handle-free.
                if not isinstance(summary, dict):
                    continue
                handles = checked_handles(entry.get("object_handles"), entry_document, output["generation"])
                output["verified_results"].append({"id": identity, "tool": tool, "document": entry_document,
                    "generation": _counter(entry.get("generation")), "status": entry["status"],
                    "summary": _drop_handles(summary) if stale and summaries is None else summary,
                    "object_handles": [] if stale else handles, "provenance": origin, "stale": stale})
            except (KeyError, ValueError, TypeError, OverflowError, RecursionError):
                continue
    history = value.get("history", [])
    if isinstance(history, list):
        for entry in history[-MAX_HISTORY:]:
            try:
                origin = validate_provenance(entry["provenance"])
                fields = entry["fields"]
                if (not isinstance(fields, list) or len(fields) > 8
                        or any(field not in PATCH["properties"] and field not in {"verified_results", "stale"} for field in fields)):
                    continue
                output["history"].append({"revision": _counter(entry.get("revision")), "fields": list(fields), "provenance": origin})
            except (KeyError, TypeError, ValueError):
                continue
    for field in ("omitted_results", "omitted_history"):
        output[field] = _counter(value.get(field))
    output["stale"] = value.get("stale") is not False
    reason = _safe_text(value.get("stale_reason", "Unverified historical data"), 160)
    output["stale_reason"] = reason if reason is not None and (reason or not output["stale"]) else "Unverified historical data"
    if rehydrating:
        output["generation"] = min(output["generation"] + 1, 2147483647)
        _stale(output, "Restart: reobserve the native scene and revalidate source availability", summaries is not None)
    try:
        return _bound_memory(output)
    except ValueError:
        # Corrupt/oversized memory must not discard the rest of an old transcript.
        return new_memory(profile_id)


def _bound_memory(memory):
    while len(json.dumps(memory, ensure_ascii=True, allow_nan=False).encode("utf-8")) > MAX_MEMORY_BYTES:
        if memory["verified_results"]:
            memory["verified_results"].pop(0)
            memory["omitted_results"] += 1
        elif memory["history"]:
            memory["history"].pop(0)
            memory["omitted_history"] += 1
        else:
            # Schemas bound the entire brief below this limit, even with escaped
            # Unicode. Keep core constraints, never silently trim their text.
            raise ValueError("Task brief exceeds its durable memory budget")
    return memory


def _memory(state, profile_id=None):
    if not isinstance(state, dict):
        raise ValueError("Task memory requires a session state")
    return clean_memory(state.get("task_memory"), profile_id=profile_id)


def _scope(memory, document_identity, bind=False):
    token = scope_token(document_identity)
    current = memory["scope"]["document"]
    if current is None and bind and token is not None:
        memory["scope"]["document"] = token
        return True
    return token is not None and current == token


def read_memory(state, document_identity, profile_id=None):
    memory = _memory(state, profile_id)
    if not _scope(memory, document_identity):
        empty = new_memory(memory["scope"]["profile"], document_identity)
        empty.update(scope_mismatch=True, authority="none")
        return empty
    return dict(memory, authority="none", scope_mismatch=False)


def _history(memory, fields, origin):
    memory["revision"] = min(memory["revision"] + 1, 2147483647)
    memory["history"].append({"revision": memory["revision"], "fields": list(fields), "provenance": dict(origin)})
    if len(memory["history"]) > MAX_HISTORY:
        memory["omitted_history"] += len(memory["history"]) - MAX_HISTORY
        del memory["history"][:-MAX_HISTORY]


def update_memory(state, arguments, document_identity, *, provenance, evidence=(), profile_id=None):
    """Apply a precisely approved patch, using only runtime-admitted receipts.

    ``evidence`` is an iterable of AdmittedEvidence or a host resolver callable
    receiving (source, id) and returning one. JSON approval claims are refused.
    Neither the patch nor the resulting memory can authorize anything else.
    """
    args = validate_memory("angelo_memory_update", arguments)
    origin = validate_provenance(provenance, {"user_message", "approved_update"})
    memory = _memory(state, profile_id)
    if not _scope(memory, document_identity, bind=True):
        raise ValueError("Task memory belongs to a different document; start a new chat")
    if args["expected_revision"] != memory["revision"]:
        raise ValueError("Task memory changed; read its current revision and review a new exact patch")
    receipts = None if callable(evidence) else tuple(evidence)
    for field, value in args["patch"].items():
        if field in {"references", "assets"}:
            admitted = []
            for record in value:
                receipt = (evidence(record["source"], record["id"]) if callable(evidence)
                           else next((entry for entry in receipts if isinstance(entry, AdmittedEvidence)
                                      and entry.source == record["source"] and entry.id == record["id"]), None))
                if (not isinstance(receipt, AdmittedEvidence) or receipt.source != record["source"] or receipt.id != record["id"]
                        or receipt.profile != memory["scope"]["profile"] or receipt.document != memory["scope"]["document"]):
                    raise ValueError("Reference/asset lacks current host-checked existence and actual user approval evidence")
                source = validate_provenance({"kind": receipt.origin_kind, "id": receipt.origin_id}, {"user_message", "approved_update"})
                admitted.append(dict(record, provenance=source, stale=False))
            memory[field] = admitted
        elif field in memory["brief"]:
            memory["brief"][field] = copy.deepcopy(value)
        else:
            memory[field] = copy.deepcopy(value)
        memory["provenance"][field] = dict(origin)
    _history(memory, args["patch"], origin)
    return dict(state, task_memory=_bound_memory(memory))


def on_verified_result(state, document_identity, *, call_id, tool, result, profile_id=None, object_handles=()):
    """Host-only actual native callback. Raw stdout/custom output is not memory.

    Existing entries remain a bounded history. Failed/partial/uncertain results
    are labelled, not promoted to success. Only fresh read-backs for this exact
    document/generation can carry live object handles.
    """
    identity = text(call_id, MAX_POINTER, 1)
    if not isinstance(tool, str) or not _TOOL.fullmatch(tool):
        raise ValueError("Use the actual native host tool name")
    if not isinstance(result, dict):
        raise ValueError("Native verified-result receipts require an actual result object")
    if tool in {"angelo_memory_read", "angelo_memory_update"}:
        # Memory isn't evidence about itself. Recording a read would advance
        # the returned revision and make the next exact approved patch stale.
        return dict(state)
    memory = _memory(state, profile_id)
    if not _scope(memory, document_identity, bind=True):
        raise ValueError("Native result belongs to another document")
    if any(entry["id"] == identity for entry in memory["verified_results"]):
        return dict(state, task_memory=memory)  # Replayed callbacks don't rewrite history.
    summary = _result_summary(result) or {}
    failed = bool(result.get("error")) or result.get("success") is False
    uncertain = bool(result.get("partial_changes_possible") or result.get("serialization_failed")
                     or result.get("uncertain") or _invalid_measurement(result)
                     or (isinstance(result.get("changes"), dict) and result["changes"].get("uncertain")))
    measured = any(key in summary for key in {"verified", "scene", "object", "objects", "changes", "file_changes", "asset_id", "mesh_revision", "bounds_world"})
    status = "failed" if failed else "unverified" if uncertain or not measured else "verified"
    origin = {"kind": "host_result", "id": identity}
    handles = []
    if status == "verified":
        for handle in islice(object_handles, 16):
            try:
                handle = str(handle) if type(handle) is int else text(handle, 128, 1)
                handles.append({"id": handle, "document": memory["scope"]["document"], "generation": memory["generation"]})
            except (TypeError, ValueError):
                continue
    entry = {"id": identity, "tool": tool, "document": memory["scope"]["document"], "generation": memory["generation"],
             "status": status, "summary": summary if status == "verified" else _drop_handles(summary),
             "object_handles": handles, "provenance": origin, "stale": status != "verified"}
    memory["verified_results"].append(entry)
    if len(memory["verified_results"]) > MAX_RESULTS:
        memory["omitted_results"] += len(memory["verified_results"]) - MAX_RESULTS
        del memory["verified_results"][:-MAX_RESULTS]
    # A fresh observation doesn't revive older stale entries or source receipts.
    scene_readback = any(key in summary for key in {"scene", "object", "objects", "mesh_revision", "bounds_world"})
    scene_readback |= isinstance(summary.get("verified"), dict) and "object_count" in summary["verified"]
    if status == "verified" and scene_readback:
        memory["stale"] = False
        memory["stale_reason"] = ""
    elif uncertain:
        _stale(memory, "Native result is partial or unverified; inspect or restore before continuing")
    _history(memory, ["verified_results"], origin)
    return dict(state, task_memory=_bound_memory(memory))


def on_restore(state, document_identity=None, *, reason="restore", profile_id=None, evidence_id=""):
    """Invalidate native handles after Undo/redo/reload/restore/document edits.

    Snapshot restore/restart may explicitly rebind the same project's runtime
    document identity. A *different* document never gets the old brief: the
    document_change reason keeps its original scope, so read/render omit it.
    """
    if reason not in RESTORE_REASONS:
        raise ValueError("Choose an explicit native invalidation reason")
    memory = _memory(state, profile_id)
    memory["generation"] = min(memory["generation"] + 1, 2147483647)
    if document_identity is not None and reason != "document_change":
        memory["scope"]["document"] = scope_token(document_identity)
        # Historical receipts retain the document they actually observed.
    _stale(memory, {"undo": "Undo: prior native state is stale", "redo": "Redo: prior native state is stale",
                    "restore": "Snapshot restore: prior native state is stale", "restart": "Restart: prior native state is stale",
                    "document_change": "Document changed: this brief remains scoped to its original project",
                    "external_edit": "Document edited outside this agent: prior native state is stale"}[reason])
    origin = {"kind": "host_result", "id": text(evidence_id, MAX_POINTER, 1) if evidence_id else "host:" + reason}
    _history(memory, ["stale"], origin)
    return dict(state, task_memory=_bound_memory(memory))


def _json(value):
    # Keep ordinary Unicode compact, while escaping all line/markup boundaries.
    # ASCII-only encoding expands emoji to 12 characters and could otherwise
    # displace the very constraints this section is required to preserve.
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u0085", "\\u0085").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render_context(state, document_identity, limit=MAX_CONTEXT, *, exclude_results=frozenset()):
    """Deterministic provider-independent section; never replaces native resume.

    Stable field order and full bounded objective/constraints win over recent
    result history. Optional items are added whole and omission is disclosed.
    User/scene names stay escaped JSON data, not prompt instructions. Receipts
    of calls in ``exclude_results`` (whose tool results the same message's
    handoff carries) are counted instead of repeated. ``memory_context`` owns
    the rendering, including the per-context deltas.
    """
    from .memory_context import render_full
    return render_full(state, document_identity, limit, exclude_results=exclude_results)
