# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Own the bounded scene observation API, identities, invalidation and cache lifetimes.

Domain readers use explicit identity/metric dependencies and retain no Blender datablocks.
Every persistent cache reuse requires a fresh fingerprint."""

from collections import OrderedDict
from itertools import islice
import json
import threading
import time
import uuid
import zlib
import bpy

from .scene_spec import validate_scene
from .refusal_codes import RefusalError
from .scene_read_values import (SceneQueryError, ReadBudget, ReadOutput,
                                dump as _dump, hash_value as _hash, label as _name,
                                session_uid as _uid, pointer as _pointer)
from .scene_metadata import MetadataReader
from .scene_geometry import GeometryReader
from .scene_fingerprints import ObjectFingerprinter, dependency_ids, collection_memberships, membership_reader
from .scene_relationships import RelationshipReader
from .scene_query_results import detail_sections, matching_objects, delta_changes, payload_bound, instance_detail, hidden_rows
from .scene_coverage import (SCENE, OBJECTS, OBJECTS_SCOPE, captured_row, covered_objects, discover,
                             match_fingerprint, objects_snapshot)
from .scene_stores import CursorStore, ObservationStore


MAX_OBJECTS = 20000
MAX_ELEMENTS = 2000000
MAX_PACKED_BYTES = 256000000  # Bytes copied by single C calls per read pass.
MAX_METADATA_ITEMS = 12000
MAX_READ_SECONDS = 2.0
MAX_PAYLOAD_BYTES = 128000
MAX_DETAIL_ITEMS = 500
MAX_CACHE_BYTES = 16000000
MAX_OBSERVATIONS = 16
MAX_OBJECT_OBSERVATIONS = 128
MAX_OBJECT_OBSERVATION_BYTES = 8000000
MAX_SNAPSHOT_BYTES = 32000000
MAX_CURSORS = 32
MAX_LIST = 2048
# Links of the pass's scanned objects indexed at once; beyond this, each object
# reads its own membership (counted as membership_fallbacks).
MAX_MEMBERSHIP_LINKS = 4 * MAX_OBJECTS

_epoch = uuid.uuid4().hex
_identities = OrderedDict()
_cache = OrderedDict()
_observations = ObservationStore()
_cursors = CursorStore(MAX_CURSORS)
_cache_bytes = 0
_serial = 0
_counters = {"exact_checks": 0, "fingerprint_bytes": 0, "cache_hits": 0,
             "cache_misses": 0, "invalidations": 0, "epoch_resets": 0,
             "evictions": 0, "depsgraph_updates": 0, "membership_fallbacks": 0}


def _read_budget(deadline=None):
    return ReadBudget(MAX_ELEMENTS, MAX_METADATA_ITEMS, MAX_READ_SECONDS, deadline, MAX_PACKED_BYTES)


def _read_output(limit):
    return ReadOutput(limit, MAX_DETAIL_ITEMS)


def _main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise SceneQueryError("Native scene observations require Blender's main thread")


def _identity(value):
    _observe_identity(type(value).__name__, _uid(value), _pointer(value))


def _observe_identity(type_name, uid, pointer):
    key = (type_name, uid)  # Keep copied identity only, never an RNA wrapper.
    previous = _identities.pop(key, None)
    if previous is not None and previous != pointer:
        invalidate()
    _identities[key] = pointer
    while len(_identities) > MAX_OBJECTS * 8:
        _identities.popitem(last=False)


def object_handle(obj):
    """Stable through renames; load/Undo/Redo replacement changes the epoch."""
    _main_thread()
    _identity(obj)
    return f"scn:{_epoch}:{_uid(obj)}"


def document_epoch():
    """Opaque epoch embedded in every handle; changes on load, Undo/Redo or ID replacement."""
    _main_thread()
    return _epoch


def _scope(window):
    _identity(window.scene)
    return (_epoch, _uid(window.scene), _pointer(window.view_layer))


def resolve_object(window, handle):
    """Resolve only an observed ID in the bound scene, never a name or path."""
    try:
        checked = validate_scene("angelo_scene_object", {"object": handle})["object"]
    except ValueError:
        raise SceneQueryError("Use an exact observed scene object handle") from None
    _main_thread()
    _scope(window)
    _, epoch, uid = checked.split(":")
    if epoch != _epoch:
        raise RefusalError("Scene object handle expired after document load/Undo ID replacement; search again", code="STALE_REVISION", details={"handles": [handle]})
    found = next((o for o in window.scene.objects if _uid(o) == int(uid)), None)
    if found is None:
        raise RefusalError("Observed object was deleted or is not in the bound scene; search again", code="STALE_REVISION", details={"handles": [handle]})
    current = object_handle(found)
    if current != handle:
        raise RefusalError("Scene object handle expired after ID replacement; search again", code="STALE_REVISION", details={"handles": [handle]})
    return found


def _cache_delete(key):
    global _cache_bytes
    payload = _cache.pop(key, None)
    if payload is not None:
        _cache_bytes -= len(payload.encode("utf-8"))


def invalidate(window=None, depsgraph=None):
    """No arguments = document/Undo reset; depsgraph = dependency dirtiness.

    A window-only call denotes an unknown edit. Baseline tokens survive ordinary
    edits so they can describe a delta; document/ID replacement expires them.
    """
    global _epoch, _cache_bytes, _serial
    _main_thread()
    _serial += 1
    _counters["invalidations"] += 1
    if window is None and depsgraph is None:
        _epoch = uuid.uuid4().hex
        _identities.clear()
        _cache.clear()
        _cache_bytes = 0
        _observations.clear()
        _cursors.clear()
        _counters["epoch_resets"] += 1
        return
    if depsgraph is None:
        _cache.clear()
        _cache_bytes = 0
        return
    _counters["depsgraph_updates"] += 1
    dirty = set()
    try:
        for update in depsgraph.updates:
            original = getattr(update.id, "original", update.id)
            if hasattr(original, "session_uid"):
                dirty.add(_uid(original))
    except (ReferenceError, RuntimeError, AttributeError):
        # Unknown edits fail conservatively, not by retaining a clean cache.
        _cache.clear()
        _cache_bytes = 0
        return
    if not dirty:
        _cache.clear()
        _cache_bytes = 0
        return
    for key in list(_cache):
        if dirty.intersection(key[-1]):
            _cache_delete(key)


def _remember_cache(key, value):
    global _cache_bytes
    payload = _dump(value)
    size = len(payload.encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        return
    _cache_delete(key)
    _cache[key] = payload
    _cache_bytes += size
    while _cache and _cache_bytes > MAX_CACHE_BYTES:
        oldest = next(iter(_cache))
        _cache_delete(oldest)
        _counters["evictions"] += 1


def _cached(key):
    payload = _cache.get(key)
    if payload is None:
        _counters["cache_misses"] += 1
        return None
    _counters["cache_hits"] += 1
    _cache.move_to_end(key)
    return json.loads(payload)


def _resource(value, kind=None):
    if value is None:
        return None
    _identity(value)
    kind = kind or type(value).__name__.upper()
    result = {"id": f"dat:{_epoch}:{kind}:{_uid(value)}", "kind": kind,
              "name": _name(value.name), "linked": bool(value.library),
              "editable": bool(getattr(value, "is_editable", not value.library))}
    if kind == "OBJECT":
        result["object"] = object_handle(value)
    asset = getattr(value, "asset_data", None)
    if asset:
        truncated = len(asset.tags) > 32 or len(asset.description) > 512 or len(asset.author) > 128
        result["asset"] = {"catalog_id": _name(asset.catalog_id, 36),
                           "description": _name(asset.description, 512),
                           "author": _name(asset.author),
                           "tag_count": len(asset.tags),
                           "tags": [_name(t.name) for t in islice(asset.tags, 32)],
                           "truncated": truncated, "description_truncated": len(asset.description) > 512,
                           "author_truncated": len(asset.author) > 128,
                           "revision_limitations": ["bounded_asset_metadata"] if truncated else []}
    return result


def _object_ref(obj):
    return {"object": object_handle(obj), "name": _name(obj.name), "type": obj.type}


def _selection(window, obj):
    try:
        return bool(obj.select_get(view_layer=window.view_layer))
    except (RuntimeError, TypeError):
        return False


def _visibility(window, obj):
    try:
        return bool(obj.visible_get(view_layer=window.view_layer))
    except (RuntimeError, TypeError):
        return False


_document_epoch = document_epoch


_metadata = MetadataReader(_resource, MAX_LIST)
_geometry = GeometryReader(_metadata, _counters, MAX_LIST)
_fingerprints = ObjectFingerprinter(_metadata, _geometry, object_handle, _object_ref,
                                   _resource, _document_epoch, _selection, _visibility,
                                   _counters, MAX_LIST)
_relations = RelationshipReader(_object_ref, _resource, object_handle, MAX_LIST)


def _curves(action):
    """Compatibility for the existing disposable native action fixture."""
    return _metadata.action_curves(action)


def object_revision(window, obj):
    """Fail closed for over-budget or untracked mutable dependencies."""
    _bound_target(window, obj)
    return _fingerprints.revision(window, obj, _read_budget())


def object_revision_at_frame(window, obj, frame_current):
    """``obj``'s revision with only the current frame replaced (program_context rebinds), from one exact capture."""
    _bound_target(window, obj)
    return _fingerprints.revision_at_frame(window, obj, _read_budget(), frame_current)


def _bound_target(window, obj):
    _main_thread()
    if obj not in window.scene.objects[:]:
        raise SceneQueryError("Native program target must belong to the bound scene")


def _scene_snapshot(window, deadline=None):
    return _scene_pass(window, deadline)[0]


def _scene_pass(window, deadline=None):
    """One bounded read pass: the snapshot plus the membership reader it used."""
    initial = _scope(window)
    work = _read_budget(deadline)
    scanned = list(islice(window.scene.objects, MAX_OBJECTS))
    memberships = collection_memberships({obj.session_uid for obj in scanned}, work.deadline, MAX_MEMBERSHIP_LINKS)
    if memberships is None:
        _counters["membership_fallbacks"] += 1
    collections_of = membership_reader(memberships)
    entries = {}
    total = len(window.scene.objects)
    for obj in scanned:
        if time.monotonic() > work.deadline:
            break
        state = _fingerprints.capture(window, obj, work, collections_of)
        row = {"summary": state["summary"], "fields": state["fields"], "exact": state["exact"]}
        if state["exact"]:
            key = (initial, "row", state["summary"]["object"], state["summary"]["object_revision"], dependency_ids(state))
            cached = _cached(key)
            if cached is None:
                _remember_cache(key, row)
            else:
                row = cached
        entries[state["summary"]["object"]] = row
    if _scope(window) != initial:
        raise SceneQueryError("Document IDs were replaced during observation; search again")
    return {"scope": list(initial), "coverage": SCENE, "objects": entries, "object_count": total,
            "membership_complete": total == len(entries),
            "exact": total == len(entries) and all(e["exact"] for e in entries.values()),
            "uncertain_count": sum(not e["exact"] for e in entries.values()),
            "fingerprint": _hash(entries), "frame": window.scene.frame_current}, collections_of


def _objects_observation(window, objects, work, collections_of=None):
    """Capture exactly these objects as one OBJECTS observation."""
    if collections_of is None:
        memberships = collection_memberships({o.session_uid for o in objects}, work.deadline, MAX_MEMBERSHIP_LINKS)
        collections_of = membership_reader(memberships)
    rows = {}
    for obj in objects:
        state = _fingerprints.capture(window, obj, work, collections_of)
        rows[state["summary"]["object"]] = captured_row(state)
    return objects_snapshot(_scope(window), rows, len(window.scene.objects), window.scene.frame_current)


def _admit(snapshot):
    token = f"obs:{_epoch}:{uuid.uuid4().hex}"
    raw = _dump(snapshot).encode("utf-8")
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise SceneQueryError("Scene snapshot exceeds the bounded admission byte budget; reduce scene complexity")
    limits = ((MAX_OBJECT_OBSERVATIONS, MAX_OBJECT_OBSERVATION_BYTES) if snapshot["coverage"] == OBJECTS
              else (MAX_OBSERVATIONS, MAX_CACHE_BYTES))
    _counters["evictions"] += _observations.admit(token, snapshot["coverage"], zlib.compress(raw, 1), *limits)
    return token


def _observation(window, token):
    payload = _observations.get(token)
    if payload is None:
        raise SceneQueryError("Observation token is unadmitted, expired or evicted; make a fresh scene query")
    value = json.loads(zlib.decompress(payload))
    if value["scope"] != list(_scope(window)):
        raise SceneQueryError("Observation token belongs to another document, scene or view layer")
    return value


def _query_key(name, args):
    return _hash([name, {k: v for k, v in args.items() if k != "cursor"}])


def _cursor(window, name, args, fingerprint):
    return _cursors.resume(args.get("cursor"), list(_scope(window)), _query_key(name, args), fingerprint)


def _page(window, name, args, total, selected, offset, fingerprint, token, metadata):
    return _cursors.page(args["limit"], list(_scope(window)), _query_key(name, args), total, selected, offset,
                         fingerprint, token, metadata)


def _pinned_page(window, name, args, items, fingerprint, admit, metadata):
    """Later pages reuse page 1's admitted token; only a first page admits one."""
    offset, token = _cursor(window, name, args, fingerprint)
    if token:
        _observation(window, token)
    else:
        token = admit()
    return _page(window, name, args, len(items), items[offset:offset+args["limit"]], offset, fingerprint, token, metadata)


def _search(window, args):
    if args["coverage"] == "PAGE":
        return _discovery_search(window, args)
    snapshot, collections_of = _scene_pass(window)
    matches = matching_objects(window, args, snapshot, object_handle, MAX_OBJECTS, collections_of)
    result = _pinned_page(window, "angelo_scene_search", args, matches, snapshot["fingerprint"], lambda: _admit(snapshot),
                          {"scene": _name(window.scene.name), "object_count": snapshot["object_count"],
                           "matching_count_exact": snapshot["membership_complete"], "snapshot_exact": snapshot["exact"],
                           "uncertain_count": snapshot["uncertain_count"], "truncated": not snapshot["membership_complete"],
                           "scan_limit": MAX_OBJECTS, "scanned_count": len(snapshot["objects"]),
                           "sort": "SESSION_UID_ASCENDING", "observation_coverage": SCENE})
    result["objects"] = hidden_rows(window, result.pop("_items"), object_handle)  # How a hidden one is.
    return result


def _discovery_search(window, args):
    """Live filters over every object; fingerprints and the token cover the page only."""
    work = _read_budget()
    scanned = list(islice(window.scene.objects, MAX_OBJECTS))
    collections_of = membership_reader(collection_memberships({o.session_uid for o in scanned}, work.deadline, MAX_MEMBERSHIP_LINKS))
    matches, scanned_count, complete = discover(window, args, MAX_OBJECTS, work.deadline, _selection, _visibility, collections_of)
    fingerprint = match_fingerprint(object_handle(obj) for obj in matches)
    offset, _ = _cursor(window, "angelo_scene_search", args, fingerprint)
    snapshot = _objects_observation(window, matches[offset:offset+args["limit"]], work, collections_of)
    rows = sorted((row["summary"] for row in snapshot["objects"].values()), key=lambda o: int(o["object"].rsplit(":", 1)[1]))
    result = _page(window, "angelo_scene_search", args, len(matches), rows, offset, fingerprint, _admit(snapshot),
                   {"scene": _name(window.scene.name), "object_count": snapshot["object_count"],
                    "matching_count_exact": complete, "snapshot_exact": snapshot["exact"],
                    "uncertain_count": snapshot["uncertain_count"], "truncated": not complete,
                    "scan_limit": MAX_OBJECTS, "scanned_count": scanned_count,
                    "sort": "SESSION_UID_ASCENDING", "observation_coverage": OBJECTS})
    result["objects"] = hidden_rows(window, result.pop("_items"), object_handle)  # How a hidden one is.
    return result


def _detail(window, args):
    obj = resolve_object(window, args["object"])
    deadline = time.monotonic() + MAX_READ_SECONDS
    state = _fingerprints.capture(window, obj, _read_budget(deadline))
    output = _read_output(args["limit"])
    dependencies = dependency_ids(state)
    # A target/outgoing revision cannot prove incoming users or their labels.
    # Read once before lookup; the exact edge hash permits rich-detail reuse
    # without making depsgraph delivery (or an incomplete scan) its proof.
    relationships, complete, relationship_revision = None, True, None
    if "RELATIONSHIPS" in args["sections"]:
        relationships, complete = _relations.read(window, obj, "BOTH", None, _read_budget(deadline), MAX_OBJECTS)
        relationship_revision = _hash([relationships, complete])
    key = (_scope(window), "detail", args["object"], state["summary"]["object_revision"], tuple(args["sections"]), args["limit"], relationship_revision, dependencies)
    cacheable = state["exact"] and complete
    result = _cached(key) if cacheable else None
    if result is None:
        result = dict(state["summary"], truncations=[],
                      sections=detail_sections(obj, args["sections"], state, (relationships, complete), output,
                                               lambda: _read_budget(deadline), _metadata, _geometry))
        result["truncations"] = output.omitted
        result["truncated"] = bool(output.omitted) or not state["exact"]
        result["revision_scope"] = "ORIGINAL_OR_LIVE_EDIT_STATE_AND_ALLOWLISTED_DEPENDENCIES"
        if cacheable and not any(t.get("reason") == "work_budget" for t in output.omitted):
            _remember_cache(key, result)
    # The token covers exactly the capture that produced this revision.
    snapshot = objects_snapshot(_scope(window), {state["summary"]["object"]: captured_row(state)},
                                len(window.scene.objects), window.scene.frame_current)
    result.update(observation_token=_admit(snapshot), observation_coverage=OBJECTS, snapshot_exact=snapshot["exact"])
    return dict(result, **instance_detail(window, obj))  # What its instances show, live (object_bounds).


def _relationships(window, args):
    obj = resolve_object(window, args["object"])
    snapshot = _objects_observation(window, [obj], _read_budget())
    relationships, complete = _relations.read(window, obj, args["direction"], set(args["kinds"]), _read_budget(), MAX_OBJECTS)
    # Pages are edges: a changed edge or label rejects the cursor, unrelated edits do not.
    result = _pinned_page(window, "angelo_scene_relationships", args, relationships, _hash([relationships, complete]),
                          lambda: _admit(snapshot),
                          {"object": args["object"], "relationships_complete": complete, "truncated": not complete,
                           "scan_limit": MAX_OBJECTS, "snapshot_exact": snapshot["exact"], "observation_coverage": OBJECTS,
                           "relationship_scope": "ALLOWLISTED_DEPENDENCIES_AND_BOUND_SCENE_NATIVE_OBJECT_USERS"})
    result["relationships"] = result.pop("_items")
    return result


def _delta(window, args):
    before = _observation(window, args["observation_token"])
    coverage = before.get("coverage", SCENE)
    if coverage == SCENE:
        after = _scene_snapshot(window)
    else:
        after = _objects_observation(window, covered_objects(window, before["objects"], object_handle), _read_budget())
    changes, unchanged, uncertain = delta_changes(before, after)
    result = _pinned_page(window, "angelo_scene_delta", args, changes, after["fingerprint"], lambda: _admit(after),
                          {"baseline_token": args["observation_token"], "object_count_before": before["object_count"],
                           "object_count_after": after["object_count"], "unchanged_count": unchanged,
                           "uncertain_count": uncertain, "delta_exact": before["exact"] and after["exact"],
                           "truncated": not (before["membership_complete"] and after["membership_complete"]),
                           "observation_coverage": coverage, "covered_count": len(before["objects"]),
                           "scope": (OBJECTS_SCOPE + " " if coverage == OBJECTS else "") + "BOUND_SCENE_ORIGINAL_GEOMETRY_AND_ALLOWLISTED_CONFIGURATION; no custom-property/evaluated/simulation/external-effect claims"})
    result["changes"] = result.pop("_items")
    return result


def execute(window, name, args):
    """Closed main-thread dispatch. Safe validation errors have no mutation."""
    checked = validate_scene(name, args)
    _main_thread()
    started = time.monotonic()
    before = dict(_counters)
    initial = _scope(window)
    if name == "angelo_scene_search":
        result = _search(window, checked)
    elif name == "angelo_scene_object":
        result = _detail(window, checked)
    elif name == "angelo_scene_relationships":
        result = _relationships(window, checked)
    elif name == "angelo_scene_delta":
        result = _delta(window, checked)
    else:
        raise SceneQueryError("Unknown read-only scene query")
    if _scope(window) != initial:
        raise SceneQueryError("Document IDs were replaced during observation; search again")
    result.update(document_epoch=_epoch, read_only=True,
                  cache={key: value-before[key] for key, value in _counters.items()})
    result["cache"].update(entries=len(_cache), bytes=_cache_bytes, observations=len(_observations),
                           observation_bytes=_observations.size(), totals=dict(_counters),
                           validation="EXACT_ORIGINAL_STATE_CHECK_BEFORE_REUSE")
    result["budgets"] = {"object_scan": MAX_OBJECTS, "elements_per_read_pass": MAX_ELEMENTS,
                         "metadata_items_per_read_pass": MAX_METADATA_ITEMS, "seconds_per_read_pass": MAX_READ_SECONDS,
                         "payload_bytes": MAX_PAYLOAD_BYTES, "detail_items": MAX_DETAIL_ITEMS}
    result["query_ms"] = round((time.monotonic()-started)*1000, 3)
    return payload_bound(result, MAX_PAYLOAD_BYTES)
