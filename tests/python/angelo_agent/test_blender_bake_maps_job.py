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
nothing and leaves nothing behind; and the Cycles bake ran on the GPU where this machine has one.

Run through ``run_native_suites.py --gui --include test_blender_bake_maps_job``; check ``bake-maps-job.json`` for
``success`` and ``phase == "complete"``. The screenshots are the GUI proof (Director, the default view).
"""

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
    region = region or chat()[1]
    return int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)


def card_hit(action, value=None):
    from angelo_agent import card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    for (x, y, w, h), name, found in reversed(active.plan.hits):
        if name == action and (value is None or found == value):
            return region.x + x + w / 2, region.y + y + h / 2
    return None


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
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def record():
    from angelo_agent import card_host, job_ui
    card = card_host.active_card()
    return job_ui.manager().record(card.job) if card is not None and getattr(card, "job", None) else None


def tick():
    from angelo_agent import card_host, director
    phase = state["phase"]
    now = time.monotonic()
    if ticks["last"] is not None and phase in {"running", "stopping"}:
        ticks["longest"] = max(ticks["longest"], now - ticks["last"])
        ticks["count"] += 1
    ticks["last"] = now
    try:
        assert time.monotonic() - started < 420, "Suite timed out"
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
            if wait(phase, lambda: qa_plus_menu.is_open(view()), limit=6):
                # The Render group's tile opens its page, then the row is clicked, as a person does (1.0.8).
                kind, rect = qa_plus_menu.step(view(), "popup_add_open", "bake_maps")
                if kind in ("open", "back", "scroll", "scroll_up", "wait"):
                    wait("plus_row", lambda: False, limit=6)   # Raises once the row has not come in 6 s.
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
            checks["the_main_thread_never_stalled_half_a_second"] = ticks["longest"] < 0.5
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
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


import qa_input  # noqa: E402  (synthetic clicks; after_clicks)
bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)
