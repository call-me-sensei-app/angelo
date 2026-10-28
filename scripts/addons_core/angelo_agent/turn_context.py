# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turn context v2: the scene observation supplied with each user turn.

``prepare(window, chat_id)`` runs on Blender's main thread when a message is
sent (or voice starts). It returns the observation to append to the prompt and
a ``commit()`` that marks the planning boundary once the send succeeded.

What it contains, in order, at most ``MAX_OBJECTS`` entries:

1. the active object, then objects the user's message names literally, then
   selected objects in view-layer order, then objects this chat's change feed
   recorded since its last turn (most recent first), then the remaining objects
   in scene order (the legacy order; at most ``MAX_FILL_OBJECTS`` of them when
   the scene has more than ``MAX_OBJECTS``), so the objects an artist or the
   agent is working on are never cut off by the entry cap;
2. the legacy fields (name, type, handle, location, selected) plus ``active``
   per entry (``mentioned``/``recently_changed`` when true) and the active
   handle, mode, current frame and selected count; entries that get a revision
   (below) also state Blender's ``dimensions`` and their ``scale`` and Euler
   ``rotation`` when not the identity, so a question about size or a scale to
   exact dimensions needs no lookup round, and ``modifiers`` (name: type, in
   stack order, at most ``MAX_ENTRY_MODIFIERS``) so an edit of an existing
   modifier binds its name instead of adding another;
   ``scene_settings`` states the bound scene's handle and settings revision
   (``scene_settings.settings_revision``, 2 to 33 ms, never left out for the
   budget: ``revision_budget``), so a program that edits scene settings or the World binds the scene without reading
   ``angelo_scene_settings`` first (a model round each time);
3. exact ``object_revision`` values for the active object, at most
   ``MAX_MENTIONED_OBJECTS`` named objects, ``MAX_SELECTED_REVISIONS`` selected
   objects and ``MAX_CHANGED_REVISIONS`` recently changed objects
   and, in a scene of at most ``SMALL_SCENE_OBJECTS`` objects, every object, each
   attempted only while the cooperative ``REVISION_BUDGET_SECONDS`` of the
   main thread's own CPU work lasts (``work_clock.WorkClock``: waiting for a
   CPU or the GIL does not spend it; checked between objects; a started
   revision is not interrupted; a small revision may use an allowance
   past it: ``revision_budget``). An omitted revision states its reason: ``BUDGET``, ``UNSUPPORTED`` (the revision fails
   closed) or ``OVER_LIMIT`` (beyond the revision count or the program
   input-mesh limits). ``mentions`` and ``recently_changed`` report what their
   bounds left out;
4. ``changes_since_last_turn``: the chat's bounded change-feed hints; a parallel-edit lead's, while any of its
   finished worker parts is not in this scene, ``worker_facts.OBSERVED`` too (none: no bytes).

A name match is a case-sensitive literal occurrence of a scene object's exact
name, delimited from adjoining word characters; a name inside a longer named
object's occurrence does not count again. Names are untrusted data: a match
only selects bounded observation rows and grants nothing. Recently changed
objects are found by the feed's session UID, never by name alone.

Revisions come from ``scene_queries.object_revision``, the same function that
``angelo_scene_program`` and ``angelo_scene_verify`` re-run at staging and
commit. A supplied revision therefore lets a model bind a program without a
discovery query, but it is evidence, never authority: any intervening edit makes
the program fail closed. The whole scene is never fingerprinted here.

``ANGELO_TURN_CONTEXT=legacy`` restores the previous observation unchanged.
"""

from itertools import islice
import json
import time

from . import change_feed, object_bounds, visibility_facts, worker_facts
from .legacy_names import WORKSPACE_DIRECTOR, read_property
from .scene_read_values import label, revision_omission
from .revision_budget import left_out
from .work_clock import WorkClock

CONTEXT_VERSION = 2
MAX_OBJECTS = 32
# Scene-order rows of a scene with more than MAX_OBJECTS objects. In the 1,969-object heavy-castle scene 30
# of the first observation's 32 rows (2,598 B) were houses the request never named; eight keep 1,909 B out of
# the prompt cache a session writes once and reads in every later request (doc/angelo/performance.rst,
# "Heavy-scene payloads"). angelo_scene_search finds any other object; a scene of MAX_OBJECTS or fewer is listed whole.
MAX_FILL_OBJECTS = 8
MAX_SELECTED_REVISIONS = 8
MAX_MENTIONED_OBJECTS = 8
MAX_CHANGED_REVISIONS = 8
# In a scene of at most this many objects every one gets a revision (within the same budget), so the first
# edit of a nearly empty scene binds any of them: removing the default Light and Camera cost a lookup round.
SMALL_SCENE_OBJECTS = 8
# Name matching reads at most this many scene object names (scene_queries scans
# the same number) and considers at most MAX_NAME_CANDIDATES names that occur
# anywhere in the message, longest first.
MAX_NAME_SCAN = 20000
MAX_NAME_CANDIDATES = 64
REVISION_BUDGET_SECONDS = 0.025
# Modifier names listed per entry. A rebased context that could not see the
# cube's "Bevel" added a second bevel instead of changing it; a lookup would
# have cost a round.
MAX_ENTRY_MODIFIERS = 8
MAX_PAYLOAD_CHARS = 16384  # As the controller encodes it: compact, ASCII-escaped JSON.
RICH_QUERIES = ["angelo_scene_search", "angelo_scene_object", "angelo_scene_relationships", "angelo_scene_delta"]
REVISION_NOTE = ("Exact handle/object_revision pairs in objects may bind angelo_scene_program read_set/write_set "
                 "or angelo_scene_verify directly, without a discovery query. The host re-checks them at staging "
                 "and commit; a changed object fails closed. Revisions are offered only for the active object, "
                 "objects the user's message names exactly (mentioned), up to %d selected objects, objects "
                 "changed since the last turn (recently_changed) and every object of a scene with at most %d; "
                 "revision_omitted states why one is missing." % (MAX_SELECTED_REVISIONS, SMALL_SCENE_OBJECTS))


class TurnContext:
    """One prepared observation. ``commit()`` acknowledges its change feed."""

    def __init__(self, observation, stats, chat_id=None, baseline=None, through=0):
        self.observation = observation
        self.recovery_evidence = {}
        self.stats = stats
        self._chat_id, self._baseline, self._through = chat_id, baseline, through

    def commit(self):
        if self._baseline is not None and self._chat_id is not None:
            change_feed.acknowledge(self._chat_id, self._through, self._baseline)
            self._baseline = None


def prepare(window, chat_id=None, clock=None, message=""):
    from .recovery_evidence import collect
    with collect() as records:
        turn = _prepare(window, chat_id, clock, message)
    turn.recovery_evidence = records
    return turn


def _prepare(window, chat_id=None, clock=None, message=""):
    """``message`` is the user's own text (empty for voice); names in it select rows.

    ``clock`` measures the revision budget (``build``).
    """
    from .job_host import refuse_turn_while_locked
    refuse_turn_while_locked()  # A bake or export that locks Blender owns the scene until it ends.
    if change_feed.legacy_turn_context():
        return TurnContext(legacy_observation(window), {"version": 1})
    from . import scene_queries
    epoch = scene_queries.document_epoch()
    changed = change_feed.recent_objects(chat_id, int(window.scene.session_uid), epoch) if chat_id is not None else []
    observation, stats, current = build(window, clock, message=message, changed=changed)
    if scene_queries.document_epoch() != epoch:
        # An ID replacement found while minting handles rotated the epoch; one
        # rebuild keeps every handle in the payload from a single epoch. Feed
        # rows from before it name objects of the replaced document.
        observation, stats, current = build(window, clock, message=message)
    if chat_id is None:
        observation["changes_since_last_turn"] = {"status": "UNTRACKED"}
        return TurnContext(_bounded(observation, stats), stats)
    report, through = change_feed.report(chat_id, current)
    observation["changes_since_last_turn"] = report
    observation.update(worker_facts.observed(chat_id))   # A state field: a delta sends it when it changes.
    return TurnContext(_bounded(observation, stats), stats, chat_id, current, through)


def legacy_observation(window):
    """The observation sent before turn context v2: inspect_scene, first 32 objects."""
    from .blender_tools import BlenderExecutor
    observation = BlenderExecutor(window).execute("angelo_inspect_scene", {})
    observation["objects"] = observation["objects"][:MAX_OBJECTS]
    observation["truncated"] = observation["object_count"] > len(observation["objects"])
    return observation


def _ordered_objects(scene, layer, active, mentioned=(), changed=()):
    """Active, mentioned, selected (view-layer order), recently changed, then
    scene order; deduplicated. Selected objects leave room for the (at most
    MAX_CHANGED_REVISIONS) recently changed ones."""
    chosen, seen = [], set()

    def take(obj):
        key = obj.as_pointer()
        if key not in seen:
            seen.add(key)
            chosen.append(obj)

    if active is not None:
        take(active)
    for obj in mentioned:
        take(obj)
    room = MAX_OBJECTS - sum(obj.as_pointer() not in seen for obj in changed)
    # Lazy iteration: a large selection is never materialized past the cap.
    for obj in islice(layer.objects.selected, MAX_OBJECTS + 1):
        if len(chosen) >= room:
            break
        take(obj)
    for obj in changed:
        take(obj)
    # A scene the list cannot hold whole gets at most MAX_FILL_OBJECTS scene-order rows besides these.
    limit = MAX_OBJECTS if len(scene.objects) <= MAX_OBJECTS else min(MAX_OBJECTS, len(chosen) + MAX_FILL_OBJECTS)
    for obj in scene.objects:
        if len(chosen) >= limit:
            break
        take(obj)
    return chosen


def _word(character):
    return character.isalnum() or character == "_"


def _delimited(text, start, end):
    """No word character adjoins a word-character edge of text[start:end]."""
    return (not (_word(text[start]) and start > 0 and _word(text[start - 1]))
            and not (_word(text[end - 1]) and end < len(text) and _word(text[end])))


def mentioned_names(message, names):
    """Names that ``message`` mentions literally, in order of first mention.

    Returns (names, candidates_truncated). Matching is case-sensitive and
    longest-name first: an occurrence counts only when delimited and not inside
    an occurrence already matched for a longer name, so "Cube" inside
    "Eval Cube" is not a second mention. Pure; ``names`` is any iterable.
    """
    candidates = sorted({name for name in names if name and name in message}, key=lambda name: (-len(name), name))
    covered, first = [], {}
    for name in candidates[:MAX_NAME_CANDIDATES]:
        start = message.find(name)
        for _ in range(64):  # Bounded occurrences per name.
            if start < 0:
                break
            end = start + len(name)
            if _delimited(message, start, end) and not any(start < right and left < end for left, right in covered):
                covered.append((start, end))
                first[name] = start
                break
            start = message.find(name, start + 1)
    return sorted(first, key=first.get), len(candidates) > MAX_NAME_CANDIDATES


def _mentioned_objects(scene, message):
    """Scene objects the message names, and a report of what bounds left out."""
    if not message:
        return [], None
    names = scene.objects.keys()
    scanned = names[:MAX_NAME_SCAN]
    matches, truncated = mentioned_names(message, scanned)
    objects, ambiguous = [], []
    for name in matches:
        if scanned.count(name) > 1:
            ambiguous.append(label(name))  # A local and a linked object share it.
            continue
        obj = scene.objects.get(name)
        if obj is not None and obj.name == name:
            objects.append(obj)
    report = {"matched": len(objects), "included": min(len(objects), MAX_MENTIONED_OBJECTS)}
    if len(objects) > MAX_MENTIONED_OBJECTS:
        report["omitted"] = {"reason": "OVER_LIMIT", "count": len(objects) - MAX_MENTIONED_OBJECTS}
    if ambiguous:
        report["ambiguous_names"] = ambiguous[:MAX_MENTIONED_OBJECTS]
    if truncated or len(names) > MAX_NAME_SCAN:
        report["scan_truncated"] = True
    return objects[:MAX_MENTIONED_OBJECTS], report


def _changed_objects(scene, rows):
    """Resolve feed rows (session UID, full name) to live objects.

    The name only locates a candidate; its session UID must match the row, so a
    different object that took the name is never revised in the row's place.
    """
    objects, unresolved = [], 0
    for uid, name in rows:
        if len(objects) >= MAX_CHANGED_REVISIONS:
            break
        obj = scene.objects.get(name) if name else None
        if obj is not None and int(obj.session_uid) == uid:
            objects.append(obj)
        else:
            unresolved += 1  # Removed, or its name was reused by another object.
    if not rows:
        return objects, None
    report = {"included": len(objects)}
    if len(rows) > len(objects) + unresolved:
        report["omitted"] = {"reason": "OVER_LIMIT", "count": len(rows) - len(objects) - unresolved}
    if unresolved:
        report["unresolved"] = unresolved
    return objects, report


def _over_program_limits(obj):
    """Each count over the typed programs' mesh limits, as text, or None (action_schema_types.mesh_over_limits)."""
    if obj.type != "MESH" or obj.data is None:
        return None
    from .action_schema_types import MAX_VERTICES, MAX_EDGES, MAX_FACES, mesh_over_limits
    return mesh_over_limits(obj.data, (MAX_VERTICES, MAX_EDGES, MAX_FACES))


def _rounded(values):
    return [round(float(value), 6) for value in values]


def _shape(obj, entry, instances=None):
    """Blender's dimensions (evaluated bounds along the object's axes, scale
    applied), plus scale and Euler rotation when they are not the identity, and
    ``instances`` for an object that makes them (``object_bounds.InstanceFacts``).
    No dimensions when they would be its data's box: no geometry of its own, or a
    curve the depsgraph does not evaluate (its points padded by their radius).

    Cheap RNA reads of cached bounds; an object that cannot report them simply
    lacks the fields (the model can still look them up).
    """
    fact = instances.of(obj) if instances is not None else None
    if fact is not None:
        entry["instances"] = fact
    try:
        if object_bounds.states_own_box(fact) and (instances is None or instances.own_box_known(obj)):
            entry["dimensions"] = _rounded(obj.dimensions)
        scale = _rounded(obj.scale)
        if scale != [1.0, 1.0, 1.0]:
            entry["scale"] = scale
        if getattr(obj, "rotation_mode", "XYZ") in {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}:
            rotation = _rounded(obj.rotation_euler)
            if any(rotation):
                entry["rotation"] = rotation
    except (AttributeError, TypeError, ValueError, ReferenceError):
        entry.pop("dimensions", None)
    _modifiers(obj, entry)


def _modifiers(obj, entry):
    """``modifiers``: name -> type in stack order, bounded; ``modifiers_omitted`` counts the rest."""
    try:
        stack = obj.modifiers
        count = len(stack)
        if not count:
            return
        entry["modifiers"] = {label(modifier.name, 64): modifier.type for modifier in islice(stack, MAX_ENTRY_MODIFIERS)}
        if count > MAX_ENTRY_MODIFIERS:
            entry["modifiers_omitted"] = count - MAX_ENTRY_MODIFIERS
    except (AttributeError, TypeError, ReferenceError):
        entry.pop("modifiers", None)


def _revision(window, obj, entry, started, clock, stats, budget=REVISION_BUDGET_SECONDS,
              detail="cooperative_turn_context_budget", read=None):   # read: a revision_pass.RevisionPass.
    from . import scene_queries
    over = _over_program_limits(obj)
    if over:  # Typed programs cannot bind it: the counts, the route and that the mesh may stay as it is.
        from .action_schema_types import OVER_LIMIT_NOTE
        entry["revision_omitted"] = {"reason": "OVER_LIMIT", "detail": "program_input_mesh_limits", "counts": over,
                                     "route": "angelo_execute_python", "note": OVER_LIMIT_NOTE}
        return
    if left_out(clock, started, budget, obj):  # A small revision has an allowance past it (revision_budget).
        entry["revision_omitted"] = {"reason": "BUDGET", "detail": detail}
        return
    try:
        entry["object_revision"] = scene_queries.object_revision(window, obj) if read is None else read(obj)
        stats["revisions"] += 1
    except scene_queries.SceneQueryError as error:
        # object_revision reports its closed limitation codes after the colon.
        entry["revision_omitted"] = revision_omission(error, closed_codes=True)
    except (ReferenceError, RuntimeError, AttributeError, TypeError, ValueError) as error:
        # A revision is optional evidence: an unexpected reader failure is shown
        # to the model and the binding stays fail-closed, but the send proceeds.
        entry["revision_omitted"] = revision_omission(error, closed_codes=False)


def _scene_camera(scene, entry):
    """``camera``: the scene camera's handle, world position and unit view direction (three decimals).

    What a render and the person's framing look from: "behind it", "facing the
    camera" and "in the shot" are all relative to this, and the camera's own
    row may be far down a long object list or cut from it. Two cached RNA
    reads; a scene without a camera, or one whose camera cannot report its
    matrix, simply has no entry.
    """
    from . import scene_queries
    try:
        camera = scene.camera
        matrix = camera.matrix_world
        axis = [-float(matrix[row][2]) for row in range(3)]
        length = sum(value * value for value in axis) ** .5
        entry["camera"] = {"handle": scene_queries.object_handle(camera), "location": _rounded(matrix.translation),
                           "direction": [round(value / length, 3) for value in axis]}
    except (AttributeError, TypeError, ValueError, ReferenceError, ZeroDivisionError, IndexError):
        entry.pop("camera", None)


def _scene_settings(scene):
    """The bound scene's handle and settings revision, never left out for a budget (revision_budget)."""
    from . import scene_settings
    from .scene_read_values import SceneQueryError
    entry = {"handle": scene_settings.scene_handle(scene)}
    engine = getattr(getattr(scene, "render", None), "engine", None)
    if isinstance(engine, str) and engine:
        entry["engine"] = engine[:64]  # Its identifier as this build spells it: what a render setting is read against.
    if engine == "CYCLES":  # The device its renders use now (render_device; read only, never lists devices).
        from . import render_device
        try:
            entry.update(render_device.facts(scene) or {})
        except Exception:
            pass
    _scene_camera(scene, entry)
    try:
        entry["revision"] = scene_settings.settings_revision(scene)
    except SceneQueryError as error:
        entry["revision_omitted"] = revision_omission(error, closed_codes=True)
    except (ReferenceError, RuntimeError, AttributeError, TypeError, ValueError) as error:
        entry["revision_omitted"] = revision_omission(error, closed_codes=False)  # Optional evidence, as for objects.
    return entry


def build(window, clock=None, message="", changed=()):
    """Return (observation, stats, feed baseline). Reads only bounded state.

    ``changed`` holds the chat feed's (session UID, name) rows, most recent first.
    ``clock`` measures ``REVISION_BUDGET_SECONDS`` (a new ``WorkClock`` by default).
    """
    from . import scene_queries
    clock = WorkClock() if clock is None else clock
    started, wall_started, cpu_started = clock(), time.perf_counter(), time.process_time()
    scene, layer = window.scene, window.view_layer
    active = layer.objects.active
    stats = {"version": CONTEXT_VERSION, "revisions": 0}
    mentioned, mentions = _mentioned_objects(scene, message)
    recent, recent_report = _changed_objects(scene, changed)
    named, changed_keys = {obj.as_pointer() for obj in mentioned}, {obj.as_pointer() for obj in recent}
    stats.update(mentioned=len(mentioned), recently_changed=len(recent))
    entries, revision_slots = [], MAX_SELECTED_REVISIONS
    instances, hidden = object_bounds.InstanceFacts(getattr(layer, "depsgraph", None)), visibility_facts.Pass(layer)
    small = len(scene.objects) <= SMALL_SCENE_OBJECTS
    for obj in _ordered_objects(scene, layer, active, mentioned, recent):
        selected = bool(obj.select_get(view_layer=layer))
        entry = {"name": label(obj.name), "type": obj.type, "handle": scene_queries.object_handle(obj),
                 "location": [round(float(v), 6) for v in obj.location], "selected": selected,
                 "active": obj == active, **hidden.entry(obj)}  # How it is hidden, if it is (visibility_facts).
        if obj.mode != "OBJECT":
            entry["mode"] = obj.mode
        key = obj.as_pointer()
        if key in named:
            entry["mentioned"] = True
        if key in changed_keys:
            entry["recently_changed"] = True
        if entry["active"] or key in named or key in changed_keys:
            # Mentioned and changed objects are already bounded by their caps.
            _shape(obj, entry, instances)
            _revision(window, obj, entry, started, clock, stats)
        elif (selected and revision_slots) or small:
            revision_slots = max(0, revision_slots - selected)
            _shape(obj, entry, instances)
            _revision(window, obj, entry, started, clock, stats)
        elif selected:
            entry["revision_omitted"] = {"reason": "OVER_LIMIT", "detail": "selected_revision_count"}
        entries.append(entry)
    object_count = len(scene.objects)
    selected_count = len(layer.objects.selected)
    active_handle = entries[0]["handle"] if entries and entries[0]["active"] else None
    workspace = getattr(window, "workspace", None)
    observation = {
        "context_version": CONTEXT_VERSION, "scene": label(scene.name),
        "scene_settings": _scene_settings(scene), "object_count": object_count,
        "objects": entries, "truncated": object_count > len(entries),
        "order": "ACTIVE_THEN_MENTIONED_THEN_SELECTED_VIEW_LAYER_ORDER_THEN_RECENTLY_CHANGED_THEN_SCENE_ORDER",
        "active_object": active_handle, "mode": active.mode if active is not None else "OBJECT",
        "frame_current": scene.frame_current, "selected_count": selected_count,
        "workspace_mode": "DIRECTOR" if workspace is not None and read_property(workspace, WORKSPACE_DIRECTOR) else "MANUAL",
        "rich_queries": list(RICH_QUERIES), "revision_note": REVISION_NOTE,
    }
    if mentions is not None:
        observation["mentions"] = mentions
    if recent_report is not None:
        observation["recently_changed"] = recent_report
    current = {"scene_uid": int(scene.session_uid), "epoch": scene_queries.document_epoch(),
               "active_handle": active_handle, "mode": observation["mode"], "frame": scene.frame_current,
               "object_count": object_count, "selected_count": selected_count,
               "selected": tuple(int(o.session_uid) for o in islice(layer.objects.selected, change_feed.MAX_COMPARED_SELECTION))}
    stats.update(elapsed_ms=round((time.perf_counter() - wall_started) * 1000, 3),
                 cpu_ms=round((time.process_time() - cpu_started) * 1000, 3), entries=len(entries))
    return observation, stats, current


def _size(value):
    return len(json.dumps(value, separators=(",", ":")))


def _bounded(observation, stats):
    """Keep the encoded payload under MAX_PAYLOAD_CHARS, dropping least relevant
    entries first: objects that are not selected, mentioned or recently changed,
    then feed rows, then the remaining non-active objects. The active object and
    every top-level field always remain."""
    total = _size(observation)
    if total <= MAX_PAYLOAD_CHARS:
        return observation
    target = MAX_PAYLOAD_CHARS - 64  # Room for the flags set below.
    entries = observation["objects"]
    feed = observation.get("changes_since_last_turn", {})
    droppable = [(entries, lambda e: not (e["selected"] or e["active"] or e.get("mentioned") or e.get("recently_changed")), None),
                 (feed.get("objects", []), lambda _: True, "objects_overflow"),
                 (feed.get("datablocks", []), lambda _: True, "datablocks_overflow"),
                 (entries, lambda e: not e["active"], None)]
    for rows, removable, flag in droppable:
        index = len(rows) - 1
        while total > target and index >= 0:
            if removable(rows[index]):
                total -= _size(rows.pop(index)) + 1
                if flag:
                    feed[flag] = True
            index -= 1
    observation["truncated"] = observation["object_count"] > len(entries)
    observation["payload_limited"] = True
    stats["payload_limited"] = True
    return observation
