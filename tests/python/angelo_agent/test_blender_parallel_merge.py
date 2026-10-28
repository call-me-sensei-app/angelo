# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead merges its workers' parts into its own scene (``angelo_workers_merge``, ``worker_merge``).

Real workers build four held-out parts through their own chats (a knight with a parented neck and head, a vehicle
with four wheels from one design, a furnished room, a character with clothing parts); one whose turn is done parks
first (``worker_queue``: it saves and stops, and merges from its saved file and kept layout). The lead chat then merges
them through Angelo's own path for an Auto edit: admission, approval, the approved-change Undo operator. Checked: one
collection per part; copies of a design share its meshes and keep its hierarchy and modifiers; a per-placement
material variant reaches that placement only, and two parts' materials of one name stay two materials; names taken in
the lead's scene are reported; every object lands where its worker built it, moved by its placement (checked against
each part file's own world matrices, reopened at the end: the oracle is the worker's file, never the merge's code); a
fifth part keeps its collection tree, a collection hidden in viewport and render, an object hidden in the view layer
and an excluded collection, for each placement; the merged workers stop; the whole merge is one Undo step and can be
merged again after Undo; refusals change nothing; the saved file is ordinary (no library) and reopens with every
object. No provider or paid call.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-parallel-merge-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import ui, worker_keys, worker_routing, worker_sessions  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import ToolDispatchBudget, pump_controller_tools  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from parallel_workers_qa import CASES, op, paint, primitive, program, worker_chat  # noqa: E402
from hosted_worker_chat import counted_at_once, hosted, settle_part_counts  # noqa: E402

HOSTED = hosted()   # Design A′: each part is built by its chat's turns in its worker (the "hosted" variant).

report = {"variant": "hosted" if HOSTED else "lanes", "checks": {}, "paid_requests": 0,
          "binary_path": bpy.app.binary_path, "platform": sys.platform,
          "timings": {}}
MERGE = "angelo_workers_merge"
PARTS = {"chess": "Knight", "vehicle": "Car", "room": "Room", "character": "Character", "panel": "Panel"}
IVORY = {"name": "Ivory", "base_color": [0.9, 0.86, 0.78]}


def limit(alias):
    """A world-space Limit Location at x <= 1: only the panel's second placement (at x = 3) crosses it."""
    return op("constraint_add", alias, type="LIMIT_LOCATION", settings={
        "minimum": [0, 0, 0], "maximum": [1, 0, 0], "use_min": [False] * 3, "use_max": [True, False, False]})


def follow(alias, **settings):
    """A typed Child Of of the panel (``op`` takes ``target`` for the owner, so the constraint's own is set here)."""
    return {"kind": "constraint_add", "target": alias,
            "parameters": {"type": "CHILD_OF", "target": "$panel", "settings": settings}}


def panel():
    """A part with a material named as the knight's, a helper collection, a hidden object and an excluded one; each
    hidden one constrained, so a placement cannot hold it (break-it E2E-6 M-1: unseen, it was never checked); and two
    Child Of followers of the panel, one taking every channel, one without its rotation (break-it E2E-10)."""
    return [program([
        primitive("CUBE", "Panel", "panel", size=1, location=[0.5, 1, 0.3], scale=[1, 0.05, 0.6], rotation=[0, 0, 0.4]),
        paint("panel", "Ebony", [0.2, 0.3, 0.1, 1], Roughness=0.9),
        primitive("CYLINDER", "Cutter", "cutter", radius=0.1, depth=0.4, location=[0.5, 1, 0.3]),
        limit("$cutter"),   # In a collection disabled in viewports.
        op("collection_link", "$cutter", collection="Cutters", create=True, mode="MOVE"),
        primitive("CUBE", "Eye Hidden", "eye", size=0.2, location=[0, 0, 2]),
        limit("$eye"),   # Hidden with the view layer's eye.
        op("visibility_set", "$eye", hidden=True),
        primitive("CUBE", "Stashed", "stash", size=0.2, location=[0, 0, 3]),
        limit("$stash"),   # In an excluded collection.
        op("collection_link", "$stash", collection="Stash", create=True, mode="MOVE"),
        primitive("CUBE", "Spinner", "spin", size=0.2, location=[0, 2, 0.5]),   # An animated root.
        op("keyframe_insert", "$spin", property="location", value=[0, 2, 0.5], frame=1),
        op("keyframe_insert", "$spin", property="location", value=[0, 2, 1.5], frame=20),
        # Its rotation keyed, its place held by the world Limit: off in a channel no key sets (E2E-12 H-1).
        primitive("CUBE", "Turning Limited", "turn", size=0.2, location=[0.5, -1, 0.3]),
        op("keyframe_insert", "$turn", property="rotation_euler", value=[0, 0, 0], frame=1),
        op("keyframe_insert", "$turn", property="rotation_euler", value=[0, 0, 1], frame=20),
        limit("$turn"),
        # Mirrored, its rotation keyed: the placed basis keeps its scale's signs, so a key never flips it (E2E-13 H-2).
        primitive("CUBE", "Mirror Door", "door", size=0.2, location=[-0.5, 1.5, 0.3], scale=[-1, 1, 1]),
        op("keyframe_insert", "$door", property="rotation_euler", value=[0, 0, 0], frame=1),
        op("keyframe_insert", "$door", property="rotation_euler", value=[0, 0, 0.5], frame=20),
        primitive("CUBE", "Follower", "follow", size=0.1, location=[0.5, 1.4, 0.3]),
        follow("$follow"),
        primitive("CUBE", "Half Follower", "half", size=0.1, location=[0.5, 1.6, 0.3]),
        follow("$half", use_rotation=[False] * 3),
        op("light_create", name="Look Light", alias="look", location=[2, -2, 3], look_at=[0, 0, 0],
           settings={"light_type": "POINT", "energy": 100}),
    ])]


def collection_edits(chat):
    """The scene's own collections hidden and excluded, through the worker's scene handle (a second program)."""
    settings = chat.call("angelo_scene_settings", {})
    scene = {"handle": settings["handle"], "revision": settings["revision"]}
    return {"mode": "APPLY", "read_set": [], "write_set": [scene], "operations": [
        op("visibility_set", scene["handle"], collection="Cutters", hide_viewport=True, hide_render=True),
        op("visibility_set", scene["handle"], collection="Stash", exclude=True)]}


def placement_matrix(placement):
    return Matrix.LocRotScale(Vector(placement.get("location", (0, 0, 0))),
                              Euler(placement.get("rotation", (0, 0, 0))).to_quaternion(),
                              Vector(placement.get("scale", (1, 1, 1))))


def close(a, b):
    return all(abs(a[i][j] - b[i][j]) < 1e-4 for i in range(4) for j in range(4))


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault("details", {})[name] = detail
    assert condition, name


def counted(touched=False):
    """Hosted: every part's object count known before a merge is asked, from its worker's own report of its save,
    with no wait (``worker_part_counts.note``). ``touched``: the suite itself made a new version no worker reported,
    which is read under its worker's lease on a UI tick (the fallback), as the window's timer does."""
    if HOSTED:
        check("parts_counted_%d" % len(report["checks"]), settle_part_counts() if touched else counted_at_once())


def lead_call(lead, window, arguments, timeout=60.0):
    """The lead's merge as an Auto edit runs in the window: admitted, approved, applied in the Undo operator."""
    answers = []
    lead.request_tool("merge-%d" % time.monotonic_ns(), MERGE, arguments, answers.append)

    def apply_mutation(target, call):
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])
    started = time.perf_counter()
    while not answers and time.perf_counter() - started < timeout:
        lead.pump_events()
        pump_controller_tools([lead], 0, BlenderExecutor(window), ui.document_key(), apply_mutation,
                              budget=ToolDispatchBudget(seconds=60))
    if not answers:
        raise AssertionError("the merge was not answered")
    return answers[0]


def root(obj):
    while obj.parent is not None:
        obj = obj.parent
    return obj


def scene_names():
    return sorted(obj.name for obj in bpy.context.scene.objects)


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    _register(bpy, ui)
    window = bpy.context.window_manager.windows[0]
    lead = ui.get_controller()
    lead.state["options"] = dict(lead.state.get("options") or {}, approval="AUTO_BOUNDED")
    lead.set_document(ui.document_key())
    lead.fence = lead.open_external_read()

    # Four real workers build their parts with the typed scene tools.
    chats, started = {}, []
    for number, (case, part_name) in enumerate(PARTS.items(), start=1):
        chat = worker_chat(OUT / "state", OUT / "parts" / ("%s.blend" % case), ROOT)
        for edit in (CASES[case]()[0] if case in CASES else panel()) + ([collection_edits] if case == "panel" else []):
            result = chat.call("angelo_scene_program", edit(chat) if callable(edit) else edit)
            check(case + "_part_built", result.get("success") is True or result.get("status") == "committed", result)
        if not HOSTED:   # A hosted chat's turns already ended with its model's replies.
            chat.controller.close_external_read(chat.fence)   # Its turn is over: an idle worker, as merges find them.
        chats[number] = chat
        started.append(chat.session)
        worker_sessions._parts[chat.session] = (number, part_name, Path(chat.worker.part))
    worker_sessions._started[lead.state["id"]] = started
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.active_object.name = "Wheel"   # A name the vehicle part also uses.
    before = scene_names()

    # Refusals change nothing.
    executor = BlenderExecutor(window)
    executor.studio_context = lead
    unsent = None
    if HOSTED:   # Its parts were built by messages to its chat: the never-briefed worker is a fresh one.
        unsent = worker_chat(OUT / "state", OUT / "parts" / "unbriefed.blend", ROOT)
        worker_sessions._parts[unsent.session] = (9 + len(PARTS), "Unbriefed", Path(unsent.worker.part))
        worker_sessions._started[lead.state["id"]] = started + [unsent.session]
    try:   # A worker its brief has not reached is not done, whatever its file holds (break-it E2E-2 M2).
        executor.precondition(MERGE, {"parts": [{"worker": 9 + len(PARTS) if HOSTED else 1}]})
        unbriefed = None
    except RefusalError as refusal:
        unbriefed = refusal.code if "brief" in str(refusal) else str(refusal)
    if unsent is not None:
        worker_sessions._started[lead.state["id"]] = started
        worker_sessions._parts.pop(unsent.session, None)
        unsent.stop()
    check("refused_before_its_brief", unbriefed == "WRITER_BUSY", unbriefed)
    for chat in chats.values():   # Each worker's brief, as its first message (worker_sessions._send).
        chat.controller.emit("message", {"role": "user", "text": "Build your part."})
        chat.controller.pump_events()
    counted()
    for label, arguments, code in (
            ("unknown_worker", {"parts": [{"worker": 9}]}, "VALIDATION"),
            ("unknown_material", {"parts": [{"worker": 1, "placements": [{"materials": {"Nope": "Ebony"}}]}]},
             "VALIDATION")):
        try:
            if label == "unknown_material":
                result = lead_call(lead, window, arguments)
                error = result.get("error_code")
            else:
                executor.precondition(MERGE, arguments)
                error = None
        except RefusalError as refusal:
            error = refusal.code
        check("refused_" + label, error == code, error)
    chats[2].controller.busy = True
    try:
        executor.precondition(MERGE, {"parts": [{"worker": 2}]})
        busy = None
    except RefusalError as refusal:
        busy = refusal.code
    chats[2].controller.busy = False
    check("refused_while_its_worker_works", busy == "WRITER_BUSY", busy)
    check("refusals_changed_nothing", scene_names() == before, scene_names())
    first = executor.precondition(MERGE, {"parts": [{"worker": 3}]})
    os.utime(chats[3].worker.part, ns=(time.time_ns(), time.time_ns() + 10 ** 9))
    counted(touched=True)   # A new version of the file, no worker's save: counted again under its lease.
    check("the_binding_follows_the_saved_file", executor.precondition(MERGE, {"parts": [{"worker": 3}]}) != first)

    # A done worker frees its place once its part is saved (worker_queue): the panel's worker parks, and its part
    # merges below from its saved file and kept layout, every tree, visibility and placement check included.
    from angelo_agent import worker_queue
    resting, others = chats[5], [chat for number, chat in chats.items() if number != 5]
    for chat in others:
        chat.controller.busy = True   # Only the panel's worker is done for this step.
    deadline = time.monotonic() + 60
    while resting.session not in worker_queue.parked and time.monotonic() < deadline:
        worker_queue.tick()
        time.sleep(0.01)
    for chat in others:
        chat.controller.busy = False
    check("a_done_worker_parks_once_its_part_is_saved", resting.session in worker_queue.parked
          and worker_routing.bound(resting.controller) is None and resting.worker.ended.wait(30)
          and resting.session not in worker_sessions.died and os.path.exists(resting.worker.part))

    # The merge: the knight twice (the second turned and in its own finish), the panel twice in the same finish (of
    # its own Ebony), the character turned and scaled, the room where it was built.
    arguments = {"parts": [
        {"worker": 1, "placements": [{"location": [0, 0, 0]},
                                     {"location": [2, 0, 0], "rotation": [0, 0, math.pi], "materials": {"Ebony": IVORY}}]},
        {"worker": 2, "placements": [{"location": [10, 0, 0]}]},
        {"worker": 3},
        {"worker": 4, "placements": [{"location": [-5, 0, 0], "rotation": [0, 0, 0.5], "scale": [1.5, 1.5, 1.5]}]},
        {"worker": 5, "placements": [{"location": [0, -4, 0], "materials": {"Ebony": IVORY}},
                                     {"location": [3, -4, 0], "rotation": [0, 0, 1.0], "materials": {"Ebony": IVORY}}]}]}
    bpy.ops.ed.undo_push(message="Before the merge")   # A background Blender starts its Undo history on request.
    counted()
    started_at = time.perf_counter()
    result = lead_call(lead, window, arguments)
    report["timings"]["merge_native_ms"] = result.get("merge_ms")
    report["timings"]["merge_ms"] = (time.perf_counter() - started_at) * 1000
    check("merge_succeeds", result.get("success") is True, result)
    added = (result.get("changes") or {}).get("added") or []   # As every tool reports them: the chat draws them.
    check("its_changes_are_name_and_type_records", added and all(isinstance(row, dict) and isinstance(
        row.get("name"), str) and isinstance(row.get("type"), str) for row in added), added[:3])
    from angelo_agent import chat_changes
    copies = sum(entry["objects"] * entry["placements"] for entry in result.get("merged") or ())
    check("every_copy_is_listed_with_its_own_type", len(added) == min(copies, 120) and all(
        bpy.data.objects.get(row["name"]) is not None and bpy.data.objects[row["name"]].type == row["type"]
        for row in added), {"listed": len(added), "copies": copies})
    check("the_chat_counts_every_listed_copy", chat_changes.change_counts([{"result": result}])[1] == len(added))
    from angelo_agent.model_results import model_result
    check("the_models_copy_names_them_as_before", (model_result(MERGE, result).get("changes") or {}).get(
        "added") == [row["name"] for row in added])
    collections = {c.name: c for c in bpy.context.scene.collection.children}
    check("one_collection_per_part", set(PARTS.values()) <= set(collections), sorted(collections))
    knights = collections["Knight"].all_objects
    check("the_knight_is_placed_twice", len(knights) == 6, [o.name for o in knights])
    heads = [o for o in knights if o.name.startswith("Knight Head")]
    check("copies_share_their_meshes", len({o.data.as_pointer() for o in heads}) == 1)
    check("copies_keep_their_hierarchy", all(o.parent is not None and o.parent.name.startswith("Knight Neck")
                                             and o.parent.parent.name.startswith("Knight Base") for o in heads))
    bases = sorted((o for o in knights if o.name.startswith("Knight Base")), key=lambda o: o.matrix_world.translation.x)
    check("the_second_placement_moved_and_turned", abs(bases[1].matrix_world.translation.x - 2) < 1e-4
          and abs(abs(bases[1].matrix_world.to_euler().z) - math.pi) < 1e-3, [list(b.matrix_world.translation) for b in bases])
    check("copies_keep_their_modifiers", all(any(m.type == "BEVEL" for m in b.modifiers) for b in bases))
    finishes = {o.name: [s.material.name for s in o.material_slots] for o in knights}
    turned = [o for o in knights if root(o) is bases[1]]
    check("the_variant_reaches_its_placement_only",
          all(s.material.name == "Ivory" for o in turned for s in o.material_slots)
          and all(s.material.name.startswith("Ebony") for o in knights if o not in turned for s in o.material_slots)
          and bases[1].data.materials[0].name.startswith("Ebony"), finishes)
    wheels = [o for o in collections["Car"].all_objects if o.name.startswith("Wheel")]
    check("the_vehicle_keeps_four_wheels_of_one_design", len(wheels) == 4 and len({w.data.as_pointer() for w in wheels}) == 1)
    renamed = {m["worker"]: m["renamed"] for m in result["merged"]}
    check("a_taken_name_is_reported", renamed.get(2, {}).get("Wheel", "Wheel") != "Wheel", renamed)
    check("clothing_parts_stay_separate", {"Shirt", "Hat", "Body", "Head"} <= {o.name.split(".")[0]
                                                                              for o in collections["Character"].all_objects})
    check("the_shirt_keeps_its_thickness", any(m.type == "SOLIDIFY" for o in collections["Character"].all_objects
                                               if o.name.startswith("Shirt") for m in o.modifiers))
    # Two parts' materials of one name stay two: each placement's variant is a copy of its own part's Ebony.
    def roughness(material):
        node = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        return round(node.inputs["Roughness"].default_value, 3)
    panels = [o for o in collections["Panel"].all_objects if o.name.startswith("Panel")]
    knight_ivory = {s.material for o in turned for s in o.material_slots}
    panel_ivory = {s.material for o in panels for s in o.material_slots}
    check("a_variant_copies_its_own_parts_material", len(knight_ivory) == 1 and len(panel_ivory) == 1
          and knight_ivory != panel_ivory and roughness(next(iter(panel_ivory))) == 0.9
          and roughness(next(iter(knight_ivory))) == 0.35, {"knight": [(m.name, roughness(m)) for m in knight_ivory],
                                                            "panel": [(m.name, roughness(m)) for m in panel_ivory]})
    variants = {m["worker"]: m.get("variants") for m in result["merged"]}
    check("each_variant_is_named_as_made", {v["material"] for v in variants[1] + variants[5]}
          == {m.name for m in knight_ivory | panel_ivory}, variants)
    # The panel's tree and its view layer's visibility, for both placements.
    layers = {}
    stack = list(window.view_layer.layer_collection.children)
    while stack:
        item = stack.pop()
        layers[item.collection.name] = item
        stack.extend(item.children)
    tree = {c.name.split(".")[0]: [] for c in collections["Panel"].children}
    for c in collections["Panel"].children:
        tree[c.name.split(".")[0]].append(c)
    cutters = [o for o in collections["Panel"].all_objects if o.name.startswith("Cutter")]
    check("the_panel_keeps_its_collections_per_placement", sorted(tree) == ["Cutters", "Stash"]
          and all(len(v) == 2 for v in tree.values()), sorted(c.name for c in collections["Panel"].children))
    check("a_hidden_collection_stays_hidden_and_unrendered", len(cutters) == 2
          and all(c.hide_viewport and c.hide_render for c in tree["Cutters"])
          and not any(o.visible_get() for o in cutters), [(o.name, o.visible_get()) for o in cutters])
    eyes = [o for o in collections["Panel"].all_objects if o.name.startswith("Eye Hidden")]
    check("an_object_hidden_in_the_view_layer_stays_hidden", len(eyes) == 2 and all(o.hide_get() for o in eyes),
          [(o.name, o.hide_get()) for o in eyes])
    check("an_excluded_collection_stays_excluded", all(layers[c.name].exclude for c in tree["Stash"]),
          {c.name: layers[c.name].exclude for c in tree["Stash"]})
    check("the_panel_itself_shows", len(panels) == 2 and all(o.visible_get() for o in panels))
    # What a placement cannot move exactly is reported, never placed wrong in silence; lights a part brings are named.
    panel_entry = next(m for m in result["merged"] if m["worker"] == 5)
    entries = {w["object"]: w for w in panel_entry.get("placement_warnings") or ()}   # Once per object (E2E-11 M-2).
    warned = {pair for w in entries.values() for pair in zip(w["copies"], w["placements"])}
    later = {o["object"]: o for o in (panel_entry.get("moves_later") or {}).get("objects", ())}
    unmeasured = {o["object"]: o for o in (panel_entry.get("not_measured") or {}).get("objects", ())}
    # Keys that move a root again at the next frame change: listed once, with each placement's copy, outside the
    # warnings and their cap (break-it E2E-9 M-1).
    check("an_animated_root_is_listed_once_with_its_copies", set(later) == {"Spinner", "Turning Limited", "Mirror Door"}
          and later["Spinner"]["copies"] == ["Spinner", "Spinner.001"] and later["Spinner"]["count"] == 2
          and not any(o.get("off_now") for o in later.values()), panel_entry.get("moves_later"))
    # Keys on its rotation never explain a miss in its place: warned with its cause, not counted as off now.
    check("a_keyed_roots_real_miss_is_warned", ("Turning Limited.001", 2) in warned
          and entries["Turning Limited"]["why"].startswith("constrained"), panel_entry.get("placement_warnings"))
    # Eye-hidden: Blender still evaluates it, so it is measured and reported where it moved (decided from the effect).
    # Disabled or excluded: never evaluated (and never made to be: E2E-7 M-1), so listed once as not measured.
    halves = {w for w in warned if w[0].startswith("Half Follower")}
    # Turned, keyed on some channels only: their keys put them in neither pose at the next frame change (E2E-15 H-1).
    mixed = {("Spinner.001", 2), ("Mirror Door.001", 2)}
    check("a_hidden_part_a_placement_cannot_hold_is_reported",
          warned - halves - mixed == {("Eye Hidden.001", 2), ("Turning Limited.001", 2)},
          panel_entry.get("placement_warnings"))
    check("a_turned_root_keyed_on_some_channels_is_warned_and_a_moved_one_is_not", mixed <= warned
          and all(entries[name]["why"] == worker_keys.NEITHER and entries[name]["placements"] == [2]
                  for name in ("Spinner", "Mirror Door")), panel_entry.get("placement_warnings"))
    # A full Child Of follower is carried by its target, as a child by its parent (where it lands is checked below);
    # one that drops a channel is moved as a root and reported where that is not exact (break-it E2E-10).
    check("a_follower_its_target_cannot_carry_is_reported", halves == {("Half Follower", 1), ("Half Follower.001", 2)},
          panel_entry.get("placement_warnings"))
    check("a_warned_object_is_listed_once_with_its_copies",
          sorted(entries) == ["Eye Hidden", "Half Follower", "Mirror Door", "Spinner", "Turning Limited"]
          and entries["Half Follower"]["count"] == 2 and entries["Eye Hidden"]["count"] == 1
          and "placement_warnings_total" not in panel_entry, panel_entry.get("placement_warnings"))
    check("what_cannot_be_measured_is_listed_once", {name: (o["count"], o["copies"]) for name, o in unmeasured.items()}
          == {"Cutter": (2, ["Cutter", "Cutter.001"]), "Stashed": (2, ["Stashed", "Stashed.001"])},
          panel_entry.get("not_measured"))
    check("nothing_placed_exactly_is_reported", not any(m.get("placement_warnings") for m in result["merged"]
                                                          if m["worker"] != 5), result["merged"])
    check("a_parts_lights_are_named_once_with_their_copies", panel_entry.get("lights") == [
        {"object": "Look Light", "copies": ["Look Light", "Look Light.001"], "count": 2}], panel_entry)
    warned_names = {name for name, _placement in warned} | {c for o in later.values() for c in o["copies"]}
    merged_worlds = {o.name: o.matrix_world.copy() for o in bpy.context.scene.objects if o.visible_get()}
    # Its first placement only moves it, so the keys of the lead's frame leave it where the merge put it.
    doors = sorted((o for o in bpy.context.scene.objects if o.name.startswith("Mirror Door")), key=lambda o: o.name)
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)
    check("a_mirrored_keyed_root_keeps_its_signs_and_its_place", len(doors) == 2
          and all(o.scale[0] < 0 < min(o.scale[1], o.scale[2]) for o in doors)
          and close(doors[0].matrix_world, merged_worlds[doors[0].name]), [(o.name, list(o.scale)) for o in doors])
    check("merged_workers_stop", all(worker_routing.bound(chat.controller) is None for chat in chats.values()))
    merged_names = scene_names()

    # One Undo step, and a merge again after it.
    bpy.ops.ed.undo()
    check("one_undo_step_takes_the_whole_merge_back", scene_names() == before, scene_names())
    lead.close_external_read(lead.fence)
    lead.fence = lead.open_external_read()
    counted()
    again = lead_call(lead, window, {"parts": [{"worker": 3}]})
    check("a_part_merges_again_after_undo", again.get("success") is True and "Table Top" in scene_names(), again)
    check("the_first_merge_had_every_part", len(merged_names) > len(before) + 12, len(merged_names))

    # An ordinary file: saved, reopened, everything there, no library.
    path = OUT / "merged.blend"
    count = len(bpy.context.scene.objects)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    check("the_saved_file_has_no_library", len(bpy.data.libraries) == 0, [lib.filepath for lib in bpy.data.libraries])
    check("the_saved_file_reopens_whole", len(bpy.context.scene.objects) == count, len(bpy.context.scene.objects))

    # Every visible object where its worker built it, moved by its placement: each part file's own world matrices
    # (evaluated after it is opened) are the oracle (break-it E2E-1 H-1: roots read identity and lost their own
    # offset, rotation and scale).
    wrong, compared = {}, 0
    for part in arguments["parts"]:
        number = part["worker"]
        bpy.ops.wm.open_mainfile(filepath=str(chats[number].worker.part))
        bpy.context.view_layer.update()
        truth = {o.name: o.matrix_world.copy() for o in bpy.context.scene.objects}
        for index, placement in enumerate(part.get("placements") or [{}]):
            for old, world in truth.items():
                name = renamed.get(number, {}).get(old, old) + ("" if index == 0 else ".%03d" % index)
                if name in warned_names:
                    continue   # Reported as not exact (above), so not checked against its place here.
                if name in merged_worlds:
                    compared += 1
                    if not close(merged_worlds[name], placement_matrix(placement) @ world):
                        wrong[name] = [list(merged_worlds[name].translation), list(merged_worlds[name].to_scale()),
                                       list((placement_matrix(placement) @ world).translation)]
    check("every_object_lands_where_its_worker_built_it", compared >= 25 and not wrong, {"compared": compared,
                                                                                       "wrong": wrong})
    followers = {renamed.get(5, {}).get("Follower", "Follower") + suffix for suffix in ("", ".001")}
    check("a_child_of_follower_lands_with_its_target", followers <= set(merged_worlds)
          and not followers & (set(wrong) | warned_names), sorted(followers))
    signs_kept()
    report["success"] = True


def signs_kept():
    """A placed basis is the same matrix written near its channels as built, in every rotation mode, sign and
    placement; mirrored, a root whose rotation goes back to its keyed value stays upright (E2E-13 H-2, E2E-14 H-1)."""
    from angelo_agent import worker_placement
    changed, upside_down = [], []
    for mode in ("XYZ", "ZXY", "YXZ", "QUATERNION", "AXIS_ANGLE"):
        for built_scale in ((1, 1, 1), (-1, 1, 1)):
            for mirror in (False, True):
                obj = bpy.data.objects.new("Signs", None)
                bpy.context.scene.collection.objects.link(obj)
                obj.rotation_mode, obj.scale = mode, built_scale
                turn = Euler((0.3, 0.2, 0.1)).to_quaternion()
                obj.rotation_quaternion, obj.rotation_euler = turn, turn.to_euler(mode if len(mode) == 3 else "XYZ")
                angle, axis = turn.angle, turn.axis
                obj.rotation_axis_angle = (angle, *axis)
                built, as_built = obj.matrix_basis.copy(), worker_placement.as_built(obj)
                placed = Matrix.LocRotScale((2, 0, 0), Euler((0, 0, 0.5)), (-1, 1, 1) if mirror else (1, 1, 1)) @ built
                obj.matrix_basis = placed
                worker_placement.keep_signs(obj, as_built)
                if not close(obj.matrix_basis, placed):
                    changed.append((mode, built_scale, mirror))
                if mirror and mode == "XYZ":   # A key on its rotation puts the built value back: still upright.
                    obj.rotation_euler = as_built["channels"]["rotation_euler"]
                    if obj.matrix_basis.col[2].to_3d().normalized().dot(placed.col[2].to_3d().normalized()) <= 0:
                        upside_down.append(built_scale)
                bpy.data.objects.remove(obj)
    check("a_placed_basis_is_the_same_matrix_near_its_built_channels", not changed and not upside_down,
          {"changed": changed, "upside_down": upside_down})


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    try:
        worker_routing.stop_all()
    except Exception:
        report.setdefault("cleanup_error", traceback.format_exc())
(OUT / "parallel-merge.json").write_text(json.dumps(report, indent=2, default=str))
print("PARALLEL-MERGE-DONE", report.get("success"))
if not report.get("success"):
    sys.stdout.flush()   # os._exit skips it: the DONE line must reach a piped log too.
    os._exit(1)   # Report written and workers stopped above: a failed run never exits 0.
