# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bake every map at once in the running app: the + menu row, the Bake Maps card and the job behind it.

Real default startup (Director Mode with the chat) and real input (``Window.event_simulate``); no provider,
account or network. Checked against doc/angelo/long_jobs.rst, "Texture sets": the chat bar's + menu lists Bake
maps; the card opens on the selected meshes; Bake starts one background job (the card moves to the corner and the
view stays usable: the main thread's longest gap while it runs is measured); the job ends with every file of the
Unreal preset in the chat's files and a baked material that is not assigned but bound to the target for Angelo's
exports (doc/angelo/long_jobs.rst, "Baked materials at export"); Use Baked Material assigns it; the
person's materials, images and node groups are exactly as they were; a second bake stopped mid-way publishes
nothing and leaves nothing behind; a third, stopped while its bake check renders a picture (Blender's render job),
keeps its set, says the check was stopped and leaves no check scene or picture behind (doc/angelo/long_jobs.rst,
"Bake check"); and the Cycles bake ran on the GPU where this machine has one.

Run through ``run_native_suites.py --gui --include test_blender_bake_maps_job``; check ``bake-maps-job.json`` for
``success`` and ``phase == "complete"``. The screenshots are the GUI proof (Director, the default view).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import bake_maps_fixtures as fx  # noqa: E402
import qa_plus_menu  # noqa: E402  (the + menu's pages, read from the chat's canvas state)
import qa_hit
import qa_waits  # noqa: E402  (the runner's --timeout-scale and machine factor)
product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-bake-maps-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "bake-maps-job.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "measured": {}}
checks = state["checks"]
started = time.monotonic()
ticks = {"last": None, "longest": 0.0, "count": 0}


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def gltf_groups():
    """The glTF Material Output groups in the file: a baked material with ambient occlusion uses one (long_jobs.rst)."""
    return len([tree for tree in bpy.data.node_groups if tree.name.startswith("glTF Material Output")])


def press(x, y):
    import qa_input
    qa_input.press(window(), x, y)


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def at(rect, region=None):
    from qa_input import chat_point   # The chat's points as window pixels (its scale, right to left mirrored).
    return chat_point(region or chat()[1], rect)


def card_hit(action, value=None):
    from angelo_agent import card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    for rect, name, found in reversed(active.plan.hits):
        if name == action and (value is None or found == value):
            return qa_hit.point(qa_hit.PIXELS, region, rect)
    return None


def plus_dump(error):
    """What the + menu showed when a step did not come: the steps taken, the hits, the page's rows, the sizes and a
    screenshot (state["plus_dump"]), so a timeout says whether events, geometry or the hit list kept the row away."""
    v = view()
    menu = qa_plus_menu.popup(v)
    area, region = chat()
    win = window()
    state["plus_dump"] = {
        "error": str(error), "steps": state.get("plus_steps", [])[-40:],
        "window": [win.width, win.height], "chat_region": [region.x, region.y, region.width, region.height],
        "chat_area": [area.x, area.y, area.width, area.height], "is_open": qa_plus_menu.is_open(v),
        "page": qa_plus_menu.page(v), "drawn_page": qa_plus_menu.drawn_page(v),
        "chat_hits": [[list(r), a, str(val)] for r, a, val in v.get("hits", [])][:60],
        "popup_hits": [[list(h[0]), h[1], str(h[2])] for h in qa_plus_menu.hits(v)][:80],
        "popup_rect": list(v.get("popup_rect") or []),
        "rows": [{key: (str(value) if key == "value" else value) for key, value in row.items()
                  if key in ("action", "value", "kind", "depth")} for row in (menu.get("rows") or [])][:60],
        "scroll": menu.get("scroll"), "layout": {key: value for key, value in (menu.get("layout") or {}).items()
                                                  if key in ("rect", "max_scroll")}}
    try:
        capture("bake-maps-plus-timeout.png")
    except Exception as capture_error:   # The dump is still worth having without the picture.
        state["plus_dump"]["screenshot_error"] = str(capture_error)


def build_scene():
    """A crate (UV cube) with a procedural, mixed material and a tiled image; a bolt object it does not bake."""
    scene = bpy.context.scene
    for obj in list(scene.objects):
        if obj.type == 'MESH':
            bpy.data.objects.remove(obj)
    bpy.ops.mesh.primitive_cube_add(size=1.5)
    crate = bpy.context.active_object
    crate.name = "Crate"
    tree = fx.Tree("CrateWood")
    wave, noise = tree.nodes.new("ShaderNodeTexWave"), tree.nodes.new("ShaderNodeTexNoise")
    wood = fx.principled(tree, **{"Base Color": wave.outputs["Color"], "Roughness": noise.outputs["Fac"]})
    metal = fx.principled(tree, **{"Base Color": (0.6, 0.6, 0.62, 1.0), "Metallic": 1.0, "Roughness": 0.25})
    bump = tree.node("ShaderNodeBump", Height=noise.outputs["Fac"], Strength=0.4)
    tree.links.new(bump.outputs[0], wood.node.inputs["Normal"])
    mask = tree.node("ShaderNodeMath", Value=wave.outputs["Fac"])
    mask.operation = 'GREATER_THAN'
    mask.inputs[1].default_value = 0.85
    crate.data.materials.append(tree.surface(fx.mix_shader(tree, mask.outputs[0], wood, metal)))
    crate.select_set(True)
    bpy.context.view_layer.objects.active = crate
    return crate


def scratch_left():
    return any(image.name.startswith("Angelo Bake") for image in bpy.data.images)


def check_left():
    """What a bake check leaves: its scene, or a picture folder of its family in a private scratch root."""
    from angelo_agent import bake_check_host, private_roots
    folders = [folder / name for folder, prefix in private_roots.families() if prefix == "angelo-bake-check-"
               and folder.is_dir() for name in os.listdir(folder) if name.startswith(prefix)]
    return bake_check_host.SCENE_NAME in bpy.data.scenes, folders


def tree_states():
    return {material.name: fx.tree_state(material) for material in bpy.data.materials}


def finish(error=None):
    if error:
        trace = "".join(traceback.format_exception(error))
        state["error"] = trace.replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_BAKE_MAPS_JOB", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=30):
    """True once ``condition`` holds; raises after ``limit`` seconds times the runner's --timeout-scale."""
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit * qa_waits.timeout_scale():
        raise AssertionError("Timed out in " + phase)
    return False


def record():
    from angelo_agent import card_host, job_ui
    card = card_host.active_card()
    return job_ui.manager().record(card.job) if card is not None and getattr(card, "job", None) else None


def probe():
    """The main thread's longest gap while the bake runs or stops, by a timer of its own every 50 ms. The suite's tick
    can't measure it: ``qa_input.after_clicks`` holds the tick while a synthetic press is on its way (the ARM rule's
    half second), which is the harness waiting, not the main thread; counted there it read as a 0.57 s stall on every
    app (3e36393's app with 3fc1f94's harness fails, 3fc1f94's app with 3e36393's passes: Mac, 2026-11-17)."""
    now = time.monotonic()
    if ticks["last"] is not None and state["phase"] in {"running", "stopping", "check_stopping"}:
        ticks["longest"] = max(ticks["longest"], now - ticks["last"])
        ticks["count"] += 1
    ticks["last"] = now
    return .05


def tick():
    from angelo_agent import card_host, director
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 420 * qa_waits.timeout_scale(), "Suite timed out"
        if phase == "boot":
            if not (qa_boot.started() and director.is_director(window())):
                return .3
            state["crate"] = build_scene().name
            state["before"] = (tree_states(), len(bpy.data.images), len(bpy.data.node_groups))
            state["gltf_groups_before"] = gltf_groups()
            from angelo_agent import chat_destinations
            group = qa_plus_menu.group_of("bake_maps")   # Bake maps… is on the Render group's page (PARENTS).
            checks["the_plus_menu_lists_bake_maps"] = "bake_maps" in [
                row["value"] for row in chat_destinations.group_rows(group)]
            rect = next((r for r, a, v in view().get("hits", []) if a == "attach"), None)
            if rect is not None:
                press(*at(rect))
            state["phase"] = "plus"
            return .6
        if phase == "plus":
            try:
                opened = wait(phase, lambda: qa_plus_menu.is_open(view()), limit=6)
                # The Render group's tile opens its page, then the row is clicked, as a person does (1.0.8).
                kind, rect = qa_plus_menu.step(view(), "popup_add_open", "bake_maps") if opened else (None, None)
                if opened:
                    state.setdefault("plus_steps", []).append(
                        [round(time.monotonic() - started, 2), kind, list(rect) if rect else None])
                if opened and kind in ("open", "back", "scroll", "scroll_up", "wait"):
                    wait("plus_row", lambda: False, limit=6)   # Raises once the row has not come in 6 s.
            except AssertionError as error:
                plus_dump(error)
                raise
            if opened:
                if kind in ("open", "back", "scroll", "scroll_up", "wait"):
                    if kind in ("scroll", "scroll_up"):
                        wheel = 'WHEELUPMOUSE' if kind == "scroll_up" else 'WHEELDOWNMOUSE'
                        for value in ('PRESS', 'RELEASE'):
                            window().event_simulate(type=wheel, value=value, x=at(rect)[0], y=at(rect)[1])
                    elif rect is not None:
                        press(*at(rect))
                    return .4
                capture("bake-maps-0-plus-menu.png")
                checks["the_plus_menu_row_opens_the_card"] = kind == "click"
                if kind == "click":
                    press(*at(rect))
            state["phase"] = "card"
            return .8
        if phase == "card":
            if not wait(phase, lambda: card_host.is_active("bake_maps") and card_host._active.plan is not None):
                return .2
            card = card_host.active_card()
            checks["the_card_opens_on_the_selection"] = card.objects == [state["crate"]] and card.phase == "compose"
            defaults = (card.size, card.image_format, card.material)
            checks["the_card_defaults_to_2k_png_with_a_baked_material"] = defaults == (2048, "PNG", True)
            card.packing = "UNREAL"   # As its Packing dropdown sets it; the size stays the default 2K.
            card._version += 1
            card_host.refresh("bake_maps")
            state["phase"] = "compose"
            return .5
        if phase == "compose":
            capture("bake-maps-1-compose.png")
            press(*card_hit("bake"))
            state["phase"] = "running"
            state["bake_clicked"] = time.monotonic()
            return .1
        if phase == "running":
            current = record()
            if current is None:
                wait(phase, lambda: False, limit=10)
                return .1
            if current["status"] == "running" and not state.get("running_shot") and current.get("progress", 0) > 0:
                card = card_host.active_card()
                checks["bake_moves_the_card_to_the_corner_and_frees_the_view"] = not card.modal
                capture("bake-maps-2-running.png")
                state["running_shot"] = True
            if current["status"] not in {"done", "failed", "cancelled", "interrupted"}:
                wait(phase, lambda: False, limit=300)
                return .1
            state["measured"]["job_seconds"] = round(time.monotonic() - state["bake_clicked"], 2)
            state["measured"]["longest_main_thread_gap_s"] = round(ticks["longest"], 3)
            state["measured"]["record"] = {key: current[key] for key in ("status", "reason", "receipt")}
            state["measured"]["device"] = current["detail"].get("device_shown")
            state["record"] = current
            state["phase"] = "done"
            return .6
        if phase == "done":
            from angelo_agent import job_ui
            current = state["record"]
            card = card_host.active_card()
            receipt = current["receipt"]
            names = sorted(entry["map"] for entry in receipt.get("outputs", []))
            folder = job_ui.output_path(current)
            checks["the_job_is_done"] = current["status"] == "done"
            checks["unreal_preset_files_are_written"] = names == ["BaseColor", "Normal", "ORM"] and folder is not None \
                and all((folder / entry["file"]).is_file() for entry in receipt["outputs"])
            materials = [name for name in receipt.get("materials", []) if isinstance(name, str)]
            crate = bpy.data.objects[state["crate"]]
            checks["a_baked_material_is_made_and_not_assigned"] = len(materials) == 1 and \
                crate.material_slots[0].material.name == "CrateWood"
            # Bound for Angelo's exports (export_baked): the made material, the bake's packing and a digest that still
            # matches the crate (the job took it before its first bake changed anything). The Unreal preset is one
            # glTF can't carry, so a glTF export keeps the crate's own materials and says why.
            from angelo_agent import export_baked
            binding = crate.angelo_baked
            found = export_baked.plan([crate], bpy.context.evaluated_depsgraph_get())
            checks["the_target_is_bound_for_exports"] = binding.material is not None and \
                binding.material.name == (materials or [None])[0] and binding.packing == "UNREAL" and \
                binding.ao and not binding.sources and binding.cage is None and \
                binding.digest == export_baked.digest(crate, bpy.context.evaluated_depsgraph_get(), ao=True)
            checks["a_glTF_export_says_the_unreal_packing_stays_out"] = found["packing"] == [(crate.name, "UNREAL")]
            # Calibrated on the reference Mac and scaled to this machine (qa_waits.machine_limit).
            checks["the_main_thread_never_stalled_half_a_second"] = ticks["longest"] < qa_waits.machine_limit(0.5)
            # A probe that stopped (or never ran) would pass the bound above: it ran at least twice a second.
            checks["the_probe_measured_the_whole_bake"] = ticks["count"] >= 2 * state["measured"]["job_seconds"]
            shown = state["measured"]["device"] or {}
            from angelo_agent import render_device
            checks["the_bake_ran_on_the_gpu_where_one_is_set_up"] = bool(shown.get("gpu")) or \
                render_device.status().get("state") in {"no_gpu", "failed", "unavailable"}
            capture("bake-maps-3-done.png")
            press(*card_hit("use"))
            state["materials"] = materials
            state["phase"] = "used"
            return .6
        if phase == "used":
            crate = bpy.data.objects[state["crate"]]
            if not wait(phase, lambda: crate.material_slots[0].material.name == state["materials"][0], limit=5):
                return .2
            checks["use_baked_material_assigns_it"] = True
            for area in window().screen.areas:
                if area.type == 'VIEW_3D':
                    area.spaces[0].shading.type = 'MATERIAL'
            state["phase"] = "compiled"
            return .5
        if phase == "compiled":   # Material Preview draws the object once EEVEE has compiled its shader.
            if not wait(phase, lambda: not bpy.app.is_job_running('SHADER_COMPILATION'), limit=60):
                return .3
            if time.monotonic() - state["since"][phase] < 1.5:
                return .3
            crate = bpy.data.objects[state["crate"]]
            capture("bake-maps-4-baked-material.png")
            crate.material_slots[0].material = bpy.data.materials["CrateWood"]
            before, images, groups = state["before"]
            new_material = state["materials"][0]
            now = {name: value for name, value in tree_states().items() if name != new_material}
            checks["the_persons_materials_are_exactly_as_they_were"] = now == before
            # The baked material's glTF Material Output group (its baked occlusion, for glTF) is the one group a
            # bake adds; nothing temporary stays.
            other_groups = len(bpy.data.node_groups) - gltf_groups()
            checks["only_the_baked_images_were_added"] = other_groups == groups - state["gltf_groups_before"] and \
                len(bpy.data.images) == images + 3 and not scratch_left()
            press(*card_hit("close"))
            state["phase"] = "second"
            return .5
        if phase == "second":
            bpy.ops.angelo.bake_maps()
            if not wait(phase, lambda: card_host.is_active("bake_maps") and card_host._active.plan is not None):
                return .2
            card = card_host.active_card()
            card.size, card.material = 4096, False
            card_host.refresh("bake_maps")
            state["phase"] = "second_bake"
            return .4
        if phase == "second_bake":
            press(*card_hit("bake"))
            state["phase"] = "stopping"
            return .3
        if phase == "stopping":
            current = record()
            if current is not None and current["status"] == "running" and current.get("progress", 0) > 0.05 and \
                    not state.get("stop_sent"):
                capture("bake-maps-5-second-running.png")
                press(*card_hit("stop"))
                state["stop_sent"] = time.monotonic()
            if current is None or current["status"] not in {"cancelled", "done", "failed"}:
                wait(phase, lambda: False, limit=240)
                return .1
            state["measured"]["stop_seconds"] = round(time.monotonic() - state.get("stop_sent", time.monotonic()), 2)
            from angelo_agent import job_ui
            checks["stop_cancels_and_publishes_nothing"] = current["status"] == "cancelled" and \
                job_ui.output_path(current) is None
            before, images, groups = state["before"]
            new_material = state["materials"][0]
            now = {name: value for name, value in tree_states().items() if name != new_material}
            checks["a_stopped_bake_leaves_nothing_behind"] = now == before and \
                len(bpy.data.node_groups) - gltf_groups() == groups - state["gltf_groups_before"] and not scratch_left()
            state["measured"]["main_thread_gap_while_stopping_s"] = round(ticks["longest"], 3)
            capture("bake-maps-6-stopped.png")
            card_host.close()   # Not a synthetic press: one still on its way could land on the next card.
            state["phase"] = "third"
            return .5
        if phase == "third":   # A small set with a baked material, stopped while its check renders a picture.
            if not state.get("third_opened"):
                if not wait("third_close", lambda: not card_host.is_active("bake_maps")):
                    return .2
                state["third_opened"] = True
            bpy.ops.angelo.bake_maps()
            if not wait(phase, lambda: card_host.is_active("bake_maps") and card_host._active.plan is not None):
                return .2
            card = card_host.active_card()
            card.size, card.material = 512, True
            card_host.refresh("bake_maps")
            state["phase"] = "third_bake"
            return .4
        if phase == "third_bake":   # Once the card is drawn: its Bake button has a place.
            if not card_host.is_active("bake_maps"):   # Closed under it after all: open it again.
                state["phase"] = "third"
                return .2
            if not wait(phase, lambda: card_hit("bake") is not None):
                return .2
            press(*card_hit("bake"))
            state["phase"] = "check_stopping"
            return .3
        if phase == "check_stopping":
            from angelo_agent import bake_check_host
            current = record()
            rendering = bake_check_host.SCENE_NAME in bpy.data.scenes and bpy.app.is_job_running("RENDER")
            if current is not None and current["status"] == "running" and rendering and not state.get("check_stop"):
                state["check_stop"] = time.monotonic()
                state["measured"]["check_render_running_at_stop"] = True
                press(*card_hit("stop"))
            if current is None or current["status"] not in {"cancelled", "done", "failed"}:
                wait(phase, lambda: False, limit=240)
                return .05
            receipt = current.get("receipt") or {}
            check = (receipt.get("facts") or {}).get(state["crate"], {}).get("bake_check")
            state["measured"]["stopped_check"] = check
            scene_left, folders = check_left()
            state["measured"]["check_left"] = {"scene": scene_left, "folders": len(folders)}
            checks["a_check_stopped_mid_picture_keeps_the_set_and_says_so"] = current["status"] == "done" and \
                check == {"skipped": "stopped"} and bool(state.get("check_stop"))
            checks["a_stopped_check_leaves_no_scene_or_picture"] = not scene_left and not folders
            state["measured"]["main_thread_gap_with_check_s"] = round(ticks["longest"], 3)
            checks["the_main_thread_never_stalled_half_a_second_with_the_check"] = \
                ticks["longest"] < qa_waits.machine_limit(0.5)
            capture("bake-maps-7-check-stopped.png")
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


import qa_input  # noqa: E402  (synthetic clicks; after_clicks)
bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)
bpy.app.timers.register(probe, first_interval=.5)   # Never held by a press on its way (after_clicks).
