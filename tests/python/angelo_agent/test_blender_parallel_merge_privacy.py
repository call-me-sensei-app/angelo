# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Nothing of a worker's own reaches the person's file through a merge (``worker_merge_privacy``, break-it E2E-20
F-L-2, privacy).

A part is saved as a worker saves it (its part file opened, then saved): a driver and an ID property targeting the
worker's Scene, which has its own world; images, a font and a sound read from Angelo's private folders (the part's
folder, a registered worker folder, a worker-family folder an earlier process left), one already packed, one by a
path relative to the part; an Ocean cache folder there, which cannot be packed; and a texture of the person's own,
outside every private folder. The lead merges it as an Auto edit into an unsaved file, saves it, and after Undo
merges it again into that file, now saved (where a path could be made relative to it), and saves. The oracle is the
saved file's own bytes: every private folder's name carries one random marker, and no byte sequence of it may be in
the file (absolute or relative, in any datablock, a library, a weak reference or a packed file's path); after Undo
and Redo of the merge (the Undo step autosave writes) too. Also
checked: one scene, the driver and the property read the person's scene, no library, the private images, font and
sound packed and still drawing, the person's own texture where it was, the cache reported, and the answer's
``also_added``. A second part holds what Blender's own path walk misses and what its review found (BREAKIT-C7A): a chain
linked from a .blend in its folder, a bake image never saved, a node's string, a node modifier's input, a custom
property, a point cache and a Dynamic Paint folder, a case spelling of its folder, and a texture in the chat's default
workspace, which stays where it is. A fifth part brings what a worker's files carry besides what Blender reads, and
three more spell a private path as text transports do (one after 9 MiB, BREAKIT-C7A-7 H1); one has a long
text (BREAKIT-C7A-5, ``parallel_merge_privacy_media``). No provider or paid call.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import importlib.util
import json
import os
import sys
import tempfile
import time
import traceback
import uuid
import wave
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
MARKER = "pv" + uuid.uuid4().hex[:12]   # In every private folder's name: found in a saved file, it is a leak.
BASE = Path(os.environ.get("ANGELO_QA_DIR") or tempfile.gettempdir())
# A space, as macOS's Application Support (BREAKIT-C7A-2 H1), a capital letter JSON escapes and only text folds
# (BREAKIT-C7A-3 H2, BREAKIT-C7A-4 H1), and one that folds to two letters ("İ", BREAKIT-C7A-5 M3).
OUT = BASE / ("angelo privacy \u00c4\u0130 " + MARKER)
OUT.mkdir(parents=True, exist_ok=True)
PERSON = Path(tempfile.mkdtemp(prefix="angelo-person-"))   # The person's own folder: never private, no marker.
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (private_roots, ui, worker_merge_media, worker_merge_verify,  # noqa: E402
                          worker_paths, worker_sessions)
try:   # The streamed check's scanner, whose finds the report lists; a check from before it has none (a fail-first run).
    from angelo_agent import worker_merge_scan  # noqa: E402
except ImportError:
    worker_merge_scan = None
import parallel_merge_privacy_links as links  # noqa: E402
import parallel_merge_privacy_media as media  # noqa: E402
import parallel_merge_privacy_saved as saves_parts  # noqa: E402
import parallel_merge_privacy_writers as writers  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import ToolDispatchBudget, pump_controller_tools  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402

report = {"checks": {}, "details": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "platform": sys.platform}
HITS = report["details"]["verifier_hits"] = []   # What the merge's own check found, so a refusal says what it saw.
if worker_merge_scan is not None:   # Recorded where the check has a scanner to record.
    _hit = worker_merge_scan.Scanner.hit

    def _recorded_hit(scanner, reader, start, end):
        found = _hit(scanner, reader, start, end)
        if found is not None and len(HITS) < 12:
            HITS.append(str(found)[-200:])
        return found

    worker_merge_scan.Scanner.hit = _recorded_hit
WORKER_SCENE = "WorkerScene-" + MARKER


def check(name, passed, details=None):
    report["checks"][name] = bool(passed)
    report["details"][name] = details


def lead_merge(window, lead, parts, timeout=120):
    """The lead merges ``parts`` ({number: placements}) as an Auto edit (as the materials suite); its answer."""
    answers = []
    arguments = {"parts": [{"worker": number, "placements": placements} for number, placements in parts.items()]}
    lead.request_tool("call-%d" % time.monotonic_ns(), "angelo_workers_merge", arguments, answers.append)

    def apply_mutation(target, call):
        with bpy.context.temp_override(window=window):
            __import__("angelo_agent.grant_guard").grant_guard.stand_in().approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])
    started = time.perf_counter()
    while not answers and time.perf_counter() - started < timeout:
        lead.pump_events()
        pump_controller_tools([lead], 0, BlenderExecutor(window), ui.document_key(), apply_mutation,
                              budget=ToolDispatchBudget(seconds=60))
    assert answers, "the merge was not answered"
    lead.close_external_read(lead.fence)
    lead.fence = lead.open_external_read()
    return answers[0]


def png(path, colour):
    image = bpy.data.images.new(path.stem, 4, 4)
    image.pixels = list(colour) * 16
    image.filepath_raw, image.file_format = str(path), "PNG"
    image.save()
    bpy.data.images.remove(image)
    return path


def wav(path):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\0\0" * 800)
    return path


def leaks(path, kept=()):
    """Where the saved file holds a private folder's marker (in any case) or the worker's scene name, besides the
    ``kept`` paths, which are the person's: up to 4 places, each with the bytes around it, so a failure says what it
    found."""
    data, found = Path(path).read_bytes().lower(), []
    for allowed in list(kept) + [SIBLING]:   # The person's folder beside the state's is theirs in every check.
        for spelled in {allowed, allowed.replace("\\", "/")}:
            data = data.replace(spelled.encode("utf-8").lower(), b"")   # Folded as the bytes are (ASCII).
    for word in (MARKER.lower().encode("utf-8"), WORKER_SCENE.lower().encode("utf-8")):
        at = data.find(word)
        while at >= 0 and len(found) < 4:
            found.append(data[max(0, at - 120):at + 40].decode("latin-1"))
            at = data.find(word, at + 1)
    return found


def quad(name, materials=()):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    for material in materials:
        mesh.materials.append(material)
    return mesh


def textured(name, image):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.new("ShaderNodeTexImage").image = image
    return material


SIBLING = str(OUT / "state backup" / "x.png")   # A person's folder named like a private one, a space after it.
SECRET = "angelo-secret-" + MARKER   # A private file's contents that must never be packed into the person's file.


def _secret(path):
    path.write_text(SECRET)
    return path


writers.bind(png=png, wav=wav, quad=quad, textured=textured, _secret=_secret, OUT=OUT, MARKER=MARKER, SIBLING=SIBLING,
             WORKER_SCENE=WORKER_SCENE)


# A sound is kept only decoded and written again, by Blender's audio library; a build without one (Lite) detaches it.
AUDIO = importlib.util.find_spec("aud") is not None
NAMES = {1: "Lamp", 2: "Kit", 3: "Notes", 4: "Stale", 5: "Media", 6: "Plus", 7: "Wide", 8: "Long", 9: "Big",
         10: "Tails", 11: "Bake", 12: "Variable", 13: "Sound", 14: "Grass",
         15: "Packed Link", 16: "Override Link", 17: "Cycle Link", 18: "Theirs Link", 19: "Mixed Link", 20: "Named Link"}


def lead(paths):
    """The lead's chat in a new file, with ``paths`` ({number: part file}) as its workers' parts."""
    bpy.ops.wm.read_factory_settings()
    bpy.context.preferences.edit.use_global_undo = True
    _register(bpy, ui)
    window = bpy.context.window_manager.windows[0]
    controller = ui.get_controller()
    controller.state["options"] = dict(controller.state.get("options") or {}, approval="AUTO_BOUNDED")
    controller.set_document(ui.document_key())
    controller.fence = controller.open_external_read()
    worker_sessions._started[controller.state["id"]] = ["qa-%d" % number for number in paths]
    for number, path in paths.items():
        worker_sessions._parts["qa-%d" % number] = (number, NAMES[number], path)
    bpy.ops.ed.undo_push(message="Before the merge")
    return window, controller


def inspect(label, answer, saved):
    check(label + "_merged", answer.get("success") is True, answer)
    check(label + "_saved_file_holds_nothing_private", not leaks(saved), leaks(saved))
    check(label + "_one_scene", [s.name for s in bpy.data.scenes] == ["Scene"], [s.name for s in bpy.data.scenes])
    check(label + "_no_library", not list(bpy.data.libraries), [l.filepath for l in bpy.data.libraries])
    check(label + "_no_worker_world", "WorkerWorld" not in bpy.data.worlds, list(bpy.data.worlds.keys()))
    base = bpy.data.objects.get("Lamp Base")
    target = base.animation_data.drivers[0].driver.variables[0].targets[0].id if base else None
    check(label + "_driver_reads_the_persons_scene", target == bpy.data.scenes["Scene"], getattr(target, "name", None))
    check(label + "_property_names_the_persons_scene",
          base is not None and base.get("home") == bpy.data.scenes["Scene"])
    weak = [item.name for kind in ("meshes", "materials", "images", "worlds", "sounds", "fonts", "curves")
            for item in getattr(bpy.data, kind)
            if item.library_weak_reference is not None and MARKER in item.library_weak_reference.filepath]
    check(label + "_no_weak_reference_path", not weak, weak)
    private = sorted(i.name for i in bpy.data.images if i.name in ("reference.png", "kept.png", "baked.png"))
    check(label + "_private_images_packed", private == ["baked.png", "kept.png", "reference.png"]
          and all(bpy.data.images[n].packed_file for n in private), [(n, bool(bpy.data.images[n].packed_file))
                                                                     for n in private])
    own = bpy.data.images.get("own.png")
    check(label + "_persons_own_texture_where_it_was", own is not None and not own.packed_file and Path(
        bpy.path.abspath(own.filepath)).resolve() == (PERSON / "textures" / "own.png").resolve(),
          getattr(own, "filepath", None))
    also = answer.get("also_added") or {}
    report["details"][label + "_not_kept"] = list(worker_merge_media.failures)   # Why a file was not kept.
    check(label + "_answer_lists_what_else_came", also.get("datablocks", {}).get("meshes") == 2
          and also.get("datablocks", {}).get("images") == 4 and "scenes" not in also.get("datablocks", {})
          and also.get("scene_users") == ["Lamp Base"]
          and "Lamp Base" in [item["name"] for item in answer["changes"]["added"]]
          and ("hum.wav" in also.get("packed", [])) == AUDIO and "Lamp Base" in also.get("detached", []), also)


def kit_checks(answer, saved, brick):
    """The kit merged into the saved file, then saved again: what Blender's path walk misses is cut too."""
    check("kit_merged", answer.get("success") is True, answer)
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    check("kit_saved_file_holds_nothing_private", not leaks(saved, [brick]), leaks(saved, [brick]))
    linked = sorted(item.name for kind in ("objects", "meshes", "materials", "images")
                    for item in getattr(bpy.data, kind) if item.library is not None)
    check("kit_nothing_linked_from_its_folder", not linked and not list(bpy.data.libraries), linked)
    chain = {name: name in bpy.data.objects for name in ("Linked 0", "Linked 2", "Kit Local")}
    chain.update({name: bool(getattr(bpy.data.images.get(name), "packed_file", None))
                  for name in ("lib0.png", "lib1.png", "lib2.png")})
    check("kit_linked_chain_is_ordinary_data", all(chain.values()), chain)
    bake = bpy.data.images.get("bake.png")
    check("kit_bake_image_kept_its_pixels", bake is not None and bake.size[0] == 8 and MARKER not in bake.filepath_raw,
          getattr(bake, "filepath_raw", None))
    kit = bpy.data.objects["Kit"]
    texts = [bpy.data.node_groups["Importer"].nodes["String"].string, kit["source_file"],
             kit.modifiers["Cloth"].point_cache.filepath,
             kit.modifiers["Paint"].canvas_settings.canvas_surfaces[0].image_output_path]
    modifier = kit.modifiers["Importer"]
    stack = [modifier.properties.bl_system_properties_get()]   # Its inputs' values, Blender's own ID properties.
    while stack:
        group = stack.pop()
        for key in group.keys() if group is not None else ():
            value = group[key]
            texts.append(value) if isinstance(value, str) else stack.append(value) if hasattr(value, "keys") else None
    check("kit_strings_keep_only_file_names", all(MARKER not in text.lower() for text in texts)
          and "node.obj" in texts[0] and texts[1] == "Built from kit.fbx, by hand", texts)
    variant = bpy.data.images.get("variant.png")
    check("kit_case_spelling_packed", variant is not None and bool(variant.packed_file),
          getattr(variant, "filepath", None))
    own = bpy.data.images.get("brick.png")
    check("kit_default_workspace_texture_where_it_was", own is not None and not own.packed_file
          and own.filepath == brick, getattr(own, "filepath", None))
    also = answer.get("also_added") or {}
    cleared = also.get("cleared", [])
    check("kit_answer_names_what_was_cleared", "Kit" in cleared and "Importer" in cleared, also)


def layouts():
    return len(bpy.data.workspaces), len(bpy.data.screens)


def notes_checks(answer, saved, layouts_before):
    """The third part merged and saved: every path its text named cut, nothing of its folder in the file."""
    check("notes_merged", answer.get("success") is True, answer)
    check("notes_worker_workspace_left_behind", layouts() == layouts_before, (layouts(), layouts_before))
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    workspace = str(OUT / "state" / "workspace" / "brick.png")
    check("notes_saved_file_holds_nothing_private", not leaks(saved, [workspace]), leaks(saved, [workspace]))
    notes = bpy.data.objects["Notes"]
    unread = bpy.data.images.get("token.png")
    check("notes_unreadable_private_file_never_packed", unread is not None and not unread.packed_file
          and SECRET.encode() not in Path(saved).read_bytes() and MARKER not in unread.filepath.lower(),
          getattr(unread, "filepath", None))
    check("notes_doubled_separators_keep_file_names", notes["doubled"] == "cache at d.json", notes["doubled"])
    check("notes_workspace_detours_keep_file_names", notes["workspace_space"] == "saved to ws.json today"
          and notes["workspace_quote"] == 'open("wq.json")' and notes["workspace_bracket"] == "at wb.json",
          (notes["workspace_space"], notes["workspace_quote"], notes["workspace_bracket"]))
    check("notes_many_detours_cut_and_kept", notes["hops"] == "see h.json"
          and notes["hops_elsewhere"] == "a/" * 30 + "../" * 30 + "state", (notes["hops"], notes["hops_elsewhere"]))
    check("notes_persons_folder_beside_a_private_one_kept", notes["sibling"] == "see %s" % SIBLING, notes["sibling"])
    check("notes_detour_and_encoded_backslash_keep_file_names", notes["detour"] == "see dd.json"
          and notes["url_backslashed"] == "file://ub.png", (notes["detour"], notes["url_backslashed"]))
    check("notes_escaped_paths_keep_file_names", json.loads(notes["json"]) == {"src": "j.png"}
          and json.loads(notes["json_backslashed"]) == {"src": "w.png"} and "Renamed" in bpy.data.objects,
          (notes["json"], notes["json_backslashed"]))
    check("notes_text_keeps_file_names", notes["note"] == "reference image: ref.png, from the worker"
          and notes["after_own"] == "/tmp/a.png;b.png" and bytes(notes["raw"]) == b"raw.png"
          and list(notes["listed"]) == ["kept", "listed.png"] and notes["family"] == "see h.png",
          {key: str(notes[key])[-60:] for key in ("note", "after_own", "raw", "listed", "family", "url")})
    moved = bpy.data.images.get("moved.png")
    check("notes_moved_private_folder_packed", moved is not None and bool(moved.packed_file),
          getattr(moved, "filepath", None))


def refused_checks(label, window, controller, number, saved, named, says="can't be cleared"):
    """A part refused with nothing of it added, WorkSpaces and Screens included, the refusal naming ``named``; the
    file still clean."""
    def every():
        kinds = ("objects", "meshes", "materials", "images", "workspaces", "screens")
        return sorted((kind, item.name) for kind in kinds for item in getattr(bpy.data, kind))
    before = every()
    answer = lead_merge(window, controller, {number: [{}]})
    check(label + "_refused", answer.get("error_code") == "UNSUPPORTED" and says in answer.get("error", "")
          and answer.get("scene_changed") is False
          and all(name in answer.get("error", "") for name in ([named] if isinstance(named, str) else named)), answer)
    check(label + "_refusal_leaves_every_datablock", every() == before, sorted(set(every()) ^ set(before)))
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    workspace = str(OUT / "state" / "workspace" / "brick.png")
    check(label + "_saved_file_holds_nothing_private", not leaks(saved, [workspace]), leaks(saved, [workspace]))


def saved_checks(window, controller, saved, bake_target, link_packed):
    """BREAKIT-C7A-9: what a save writes beyond a copy refused; a packed bake refused; a folder spelled through a
    variable cut, with a save's own effects armed and not done by the check, and the person's save holding no text the
    check did not read; a long sound kept."""
    refused_checks("tails", window, controller, 10, saved, ("Tail Mod", "Tail FX", "Tail Export", "Tail Export Back",
                                                            "Tail Back Alone"))
    check("bake_part_packs_its_bake", bake_target == "PACKED", bake_target)
    refused_checks("bake", window, controller, 11, saved, "Baked", says="can't write again")
    os.environ[saves_parts.VARIABLE] = str(OUT)
    with saves_parts.Saves(worker_merge_verify, PERSON / "check copies") as saves:
        saves.arm(PERSON, PERSON / "textures" / "own.png")
        before = saves_parts.datablocks()
        answer = lead_merge(window, controller, {12: [{}]})
        facts = saves.contained()
        saves.disarm()
    saves_parts.variable_checks(check, answer)
    check("check_does_nothing_a_save_does_but_write", all(facts.values()), facts)
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    added = {pointer: item for pointer, item in saves_parts.datablocks().items() if pointer not in before}
    missing, compared = saves.compare(saved, added) if saves.copies else ({}, 0)
    check("check_reads_what_a_save_writes", compared >= 4 and not missing, (compared, missing))
    others = saves.others(added) if saves.copies else ["no copy"]
    check("check_copy_holds_only_what_the_merge_keeps", not others, others[:8])
    saves_parts.long_sound_checks(check, lead_merge(window, controller, {13: [{}]}))
    saves_parts.link_grass(PERSON)   # The person's own packed linked "Grass", named as the worker's.
    answer = lead_merge(window, controller, {14: [{}]})
    person = next((image for image in bpy.data.images if image.name == "Grass" and image.library), None)
    check("person_linked_packed_namesake_never_the_workers", answer.get("success") is True
          and person is not None and person.is_linked_packed, (answer, getattr(person, "is_linked_packed", None)))
    links.checks(check, lambda number: lead_merge(window, controller, {number: [{}]}), link_packed, _saved_and_read)


def _saved_and_read():
    """The file saved as it is now, read back: where it holds anything private, and why it could not be read. The
    last merge is taken back and done again first (Blender 5.2 crashed on that Redo for packed linked data a merge kept
    linked, BREAKIT-C7A-13)."""
    bpy.ops.ed.undo()
    bpy.ops.ed.redo()
    path = PERSON / "linked-parts.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False, copy=True)
    found = leaks(path, [str(OUT / "state" / "workspace" / "brick.png")])   # The person's, kept where it was.
    try:
        bpy.ops.wm.open_mainfile(filepath=str(path))
    except RuntimeError as error:
        found.append("read: %s" % error)
    return found


def step(name, run):
    """One merge and its checks after the first merge: an error in it fails that step (``<name>_ran``) and the steps
    after it still run, each merging its own part into the saved lead, so one class's failure never hides another's."""
    try:
        run()
    except Exception:
        check(name + "_ran", False, traceback.format_exc()[-1500:])


def run_checks():
    lead_id = "qa-lead"
    part = worker_paths.part_folder(OUT / "state", lead_id, 1, "lamp", MARKER[:8]) / "part.blend"
    (OUT / "scratch").mkdir(exist_ok=True)
    registered = private_roots.allocate(private_roots.WORKER_PREFIX, OUT / "scratch")
    family = Path(tempfile.gettempdir()) / (private_roots.WORKER_PREFIX + MARKER)   # An earlier process's.
    family.mkdir(exist_ok=True)
    (PERSON / "textures").mkdir()
    Path(SIBLING).parent.mkdir(parents=True, exist_ok=True)
    Path(SIBLING).write_bytes(b"")
    own = png(PERSON / "textures" / "own.png", (0.5, 0.5, 0.5, 1))
    has_font = writers.write_part(part, Path(registered), family, own)
    report["details"]["font"] = has_font
    check("the_part_file_holds_the_private_paths_unmerged", MARKER.encode() in part.read_bytes())
    kit_part = worker_paths.part_folder(OUT / "state", lead_id, 2, "kit", MARKER[:8]) / "part.blend"
    workspace = OUT / "state" / "workspace"   # The chat's default workspace (``Controller.workspace``).
    workspace.mkdir(parents=True, exist_ok=True)
    brick = writers.write_kit(kit_part, workspace)
    notes_part = worker_paths.part_folder(OUT / "state", lead_id, 3, "notes", MARKER[:8]) / "part.blend"
    moved = Path(private_roots.allocate(private_roots.WORKER_PREFIX, OUT / "scratch"))
    moved = moved.rename(moved.with_name("moved " + MARKER))   # Its owner file goes with it.
    writers.write_notes(notes_part, moved)
    stale_part = worker_paths.part_folder(OUT / "state", lead_id, 4, "stale", MARKER[:8]) / "part.blend"
    writers.write_stale(stale_part, OUT / "state")
    folder = lambda number, name: worker_paths.part_folder(OUT / "state", lead_id, number, name, MARKER[:8])
    media_part = folder(5, "media") / "part.blend"
    expected = media.write_media(media_part, media_part.parent, quad, textured, SECRET)
    sessions = OUT / "state" / "sessions" / "chat.json"
    encoded = {number: folder(number, how) / "part.blend" for number, how in ((6, "plus"), (7, "wide"), (9, "big"))}
    for number, how in ((6, "plus"), (7, "wide"), (9, "big")):
        media.write_encoded(encoded[number], quad, sessions, how)
    long_part = folder(8, "long") / "part.blend"
    media.write_long(long_part, quad, long_part.parent)
    later = {number: folder(number, name) / "part.blend" for number, name in (
        (10, "tails"), (11, "bake"), (12, "variable"), (13, "sound"), (14, "grass"), *links.PARTS.items())}
    saves_parts.write_tails(later[10], quad, sessions)
    bake_target = saves_parts.write_bake(later[11], quad)
    saves_parts.write_variable(later[12], quad, sessions, OUT)
    saves_parts.write_long_sound(later[13])
    saves_parts.write_grass(later[14], quad)
    link_packed = links.write(later, quad, sessions, BASE / "person library")

    # A file never saved: Blender has no folder to make a path relative to.
    window, controller = lead({1: part, 2: kit_part, 3: notes_part, 4: stale_part, 5: media_part, 6: encoded[6],
                               7: encoded[7], 8: long_part, 9: encoded[9], **later})
    started = time.perf_counter()
    answer = lead_merge(window, controller, {1: [{}]})
    report["details"]["merge_seconds"] = round(time.perf_counter() - started, 3)
    unsaved = PERSON / "unsaved-then-saved.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(unsaved), compress=False)
    inspect("unsaved_lead", answer, unsaved)
    # The merge's Undo step, written again by Redo (what memory Undo and autosave hold).
    bpy.ops.ed.undo()
    check("undo_takes_the_merge_back", "Lamp Base" not in bpy.data.objects, list(bpy.data.objects.keys()))
    bpy.ops.ed.redo()
    again = PERSON / "after-redo.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(again), compress=False, copy=True)
    check("redo_saved_file_holds_nothing_private", "Lamp Base" in bpy.data.objects and not leaks(again), leaks(again))

    # The same file, saved now, merged again after Undo: paths into the part's folder could be relative to it.
    bpy.ops.ed.undo()
    answer = lead_merge(window, controller, {1: [{}]})
    saved = PERSON / "saved-before-the-merge.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), compress=False)
    inspect("saved_lead", answer, saved)
    if has_font:   # A font cannot be written again: a worker's is never kept, its path cut (BREAKIT-C7A-6).
        font = bpy.data.objects["Lamp Label"].data.font
        check("saved_lead_private_font_never_packed", not font.packed_file and MARKER not in font.filepath.lower(),
              (font.filepath, bool(font.packed_file)))
    step("kit", lambda: kit_checks(lead_merge(window, controller, {2: [{}]}), saved, brick))

    def notes():
        before = layouts()   # Before its merge.
        notes_checks(lead_merge(window, controller, {3: [{}]}), saved, before)
    step("notes", notes)
    step("stale", lambda: refused_checks("stale", window, controller, 4, saved, "eyes.png"))
    persons = lambda path: leaks(path, [str(OUT / "state" / "workspace" / "brick.png")])   # Kept where it was.
    step("media", lambda: media.media_checks(check, lead_merge(window, controller, {5: [{}]}), saved, persons, SECRET,
                                             expected, MARKER))
    step("form_encoded", lambda: refused_checks("form_encoded", window, controller, 6, saved, "Encoded plus"))
    step("utf16", lambda: refused_checks("utf16", window, controller, 7, saved, "Encoded wide"))
    step("big_spellings", lambda: refused_checks("big_spellings", window, controller, 9, saved,
                                                 ("Big query", "Big wide", "Big log")))
    step("long", lambda: media.long_checks(check, *media.timed(lambda: lead_merge(window, controller, {8: [{}]})),
                                           saved, persons))
    step("saved", lambda: saved_checks(window, controller, saved, bake_target, link_packed))
    step("flush", lambda: saves_parts.flush_checks(check, worker_merge_verify, PERSON / "flush"))
    step("archive", lambda: saves_parts.archive_checks(check, worker_merge_verify, PERSON / "archive"))
    step("thread", lambda: saves_parts.thread_checks(check, PERSON / "thread"))
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    check("reopened_one_scene_no_library", [s.name for s in bpy.data.scenes] == ["Scene"]
          and not list(bpy.data.libraries), ([s.name for s in bpy.data.scenes], list(bpy.data.libraries)))
    check("reopened_packed_images_read", all(bpy.data.images[n].size[0] == 4 for n in ("reference.png", "kept.png",
                                                                                       "baked.png")))
    media.reopened_media_checks(check, expected)
    report["success"] = all(report["checks"].values())


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(BASE / "parallel-merge-privacy.json").write_text(json.dumps(report, indent=2, default=str))   # Where the runner reads.
print("ANGELO_PARALLEL_MERGE_PRIVACY", json.dumps({"success": report.get("success"), "error": report.get("error"),
                                                  "failed": [k for k, v in report["checks"].items() if not v]}))
sys.exit(0 if report.get("success") else 1)
