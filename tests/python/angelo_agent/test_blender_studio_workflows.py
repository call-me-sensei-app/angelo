# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's Workflows tab in the running app (1.0.5): Character in parts from its input to placed parts.

Real default startup and the native Studio canvas; clicks go through Studio's own input handler at the drawn hit
rectangles (as ``test_blender_studio`` does), dialogs are answered with ``Window.event_simulate``. Fixture accounts
only: no provider is called and nothing is paid. The two paid steps (the parts sheet and each part's 3D model) are
stood in for by library outputs carrying those steps' own input keys, exactly what a finished generation leaves
(flow reuse), so everything after them is the shipped path: the local Split, the review stop and its board, the
per-part expansion, Place Character and Open in Generation Flow.

Covered, each with a screenshot: **Workflows** (``angelo.open_media_studio`` WORKFLOW, the chat bar's + menu row) opens
Media Studio in its own window on the Workflows tab and leaves Director's layout as it was; the tab strip is Image,
3D, Workflows; the card resolves the sheet and part roles to the connected accounts; the parts check isn't offered
(``parts_check.READY``); Make parts sheet asks to approve exactly the sheet and runs nothing when it is declined; a
sheet split into parts shows the review stop with each part outlined; the body can't be turned off, and a part turned
off starts unticked in Make 3D's approval (declined: nothing runs); ready part models reach the starting-point card,
which lists the parts to check; Place in scene, pressed in the Studio window (which has no 3D view), places one root
with one object per part, and Undo removes it but keeps the run and its models (a re-split reuses its crops); Open in
Generation Flow shows the run's flow in the flow window; the Runs column lists the run, and opening it after a new
character's input was chosen shows the run's own input and never rewrites the run.

``ANGELO_QA_CHARACTER``, ``ANGELO_QA_SHEET`` and ``ANGELO_QA_PARTS`` (a folder of ``<slot>.glb``) replace the
synthetic character, sheet and part models drawn here (the held-out characters' evidence runs).
``ANGELO_QA_GROUNDING`` (``{"size", "body", "boxes"}`` in the character image's pixels, as ``parts_ground.parse``
returns them) stands in for an approved Fit to My Image: it is stored with the split before Place, so Place puts the
parts where the image shows them. Fit to My Image itself isn't offered until a runtime is measured
(``parts_ground.MEASURED``).

Run through ``run_native_suites.py --gui --include test_blender_studio_workflows``; check
``studio-workflows.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-workflows-qa"))
OUT.mkdir(parents=True, exist_ok=True)
for name, folder in (("ANGELO_STATE_DIR", "state"), ("ANGELO_CONNECTIONS_DIR", "accounts"), ("ANGELO_LIBRARY_DIR", "library")):
    os.environ.setdefault(name, str(OUT / folder))   # run_native_suites sets them before the add-on starts.
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_operator  # noqa: E402  (a stand-in self for an operator's method, never a bare namespace)

sys.path.insert(0, str(Path(__file__).resolve().parent))   # studio_workflows_evidence

product_env.clear('DISABLE_DEFAULT_CHAT')
REPORT = OUT / "studio-workflows.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [],
         "fixture": "Fixture accounts and stand-in outputs only; no provider call, nothing paid"}
checks = state["checks"]
started = time.monotonic()
phase_started = {}
SHEET_SIZE, COLUMNS = (3840, 1920), 8


def log(*parts):
    print("ANGELO_WORKFLOWS_QA", *parts, flush=True)


def wait(phase, ready, limit=20.0):
    """True once ``ready()``; raises when ``phase`` has waited ``limit`` seconds."""
    phase_started.setdefault(phase, time.monotonic())
    if ready():
        return True
    if time.monotonic() - phase_started[phase] > limit:
        raise TimeoutError(f"{phase}: timed out after {limit:.0f} s")
    return False


# --- Fixture pictures and models (drawn here unless the environment names real ones) -------------------------------

def save_png(name, rgba):
    height, width = rgba.shape[:2]
    image = bpy.data.images.new("Angelo Workflows QA " + name, width=width, height=height, alpha=True)
    try:
        image.pixels.foreach_set(np.flipud(rgba).astype(np.float32).ravel())
        path = OUT / name
        image.filepath_raw, image.file_format = str(path), 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)
    return path


def ellipse(canvas, cx, cy, rx, ry, colour):
    h, w = canvas.shape[:2]
    y, x = np.ogrid[:h, :w]
    canvas[((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0] = colour


def box(canvas, x0, y0, x1, y1, colour):
    canvas[int(y0):int(y1), int(x0):int(x1)] = colour


def figure(canvas, cx, top, height, parts):
    """A standing figure (y down) drawn as the parts named: body, hair, outer, footwear, accessories."""
    skin, cloth = (0.82, 0.70, 0.62, 1), (0.25, 0.42, 0.62, 1)
    head = height * 0.065
    if "body" in parts:
        ellipse(canvas, cx, top + head * 1.2, head * 0.8, head, skin)
        box(canvas, cx - height * 0.11, top + head * 2.3, cx + height * 0.11, top + height * 0.52, (0.7, 0.7, 0.7, 1))
        box(canvas, cx - height * 0.10, top + height * 0.52, cx - height * 0.015, top + height * 0.96, (0.7, 0.7, 0.7, 1))
        box(canvas, cx + height * 0.015, top + height * 0.52, cx + height * 0.10, top + height * 0.96, (0.7, 0.7, 0.7, 1))
    if "hair" in parts:
        ellipse(canvas, cx, top + head * 0.7, head * 0.95, head * 0.6, (0.35, 0.2, 0.1, 1))
    if "outer" in parts:
        box(canvas, cx - height * 0.13, top + head * 2.4, cx + height * 0.13, top + height * 0.62, cloth)
    if "footwear" in parts:
        box(canvas, cx - height * 0.11, top + height * 0.95, cx - height * 0.01, top + height * 0.99, (0.15, 0.1, 0.08, 1))
        box(canvas, cx + height * 0.01, top + height * 0.95, cx + height * 0.11, top + height * 0.99, (0.15, 0.1, 0.08, 1))
    if "accessories" in parts:
        box(canvas, cx + height * 0.14, top + height * 0.40, cx + height * 0.19, top + height * 0.50, (0.8, 0.6, 0.1, 1))
        ellipse(canvas, cx - height * 0.17, top + height * 0.30, height * 0.025, height * 0.025, (0.6, 0.1, 0.2, 1))


def character_png():
    if os.environ.get("ANGELO_QA_CHARACTER"):
        return Path(os.environ["ANGELO_QA_CHARACTER"])
    canvas = np.full((1536, 1024, 4), (0.86, 0.86, 0.86, 1), dtype=np.float32)
    figure(canvas, 512, 90, 1360, {"body", "hair", "outer", "footwear", "accessories"})
    return save_png("character.png", canvas)


def sheet_png():
    """An 8 x 2 parts sheet (flow_templates.PARTS_SHEET_PROMPT's layout): thin grey lines, one shared frame."""
    if os.environ.get("ANGELO_QA_SHEET"):
        return Path(os.environ["ANGELO_QA_SHEET"])
    width, height = SHEET_SIZE
    canvas = np.ones((height, width, 4), dtype=np.float32)
    cell_w, cell_h = width // COLUMNS, height // 2
    columns = {0: {"body"}, 1: {"hair"}, 4: {"outer"}, 6: {"footwear"}, 7: {"accessories"}}
    for row in range(2):
        for column, parts in columns.items():
            figure(canvas, column * cell_w + cell_w / 2, row * cell_h + 40, cell_h - 80, parts)
    for column in range(1, COLUMNS):
        box(canvas, column * cell_w - 2, 0, column * cell_w + 2, height, (0.8, 0.8, 0.8, 1))
    box(canvas, 0, cell_h - 2, width, cell_h + 2, (0.8, 0.8, 0.8, 1))
    return save_png("parts-sheet.png", canvas)


def part_glb(part):
    """A stand-in model for one part: a box the shape of its sheet box, or ANGELO_QA_PARTS/<slot or kind>.glb."""
    folder = os.environ.get("ANGELO_QA_PARTS")
    for name in ((part["slot"], part["kind"].lower()) if folder else ()):
        if (Path(folder) / f"{name}.glb").is_file():
            return Path(folder) / f"{name}.glb"
    x0, y0, x1, y1 = part["box"]
    mesh = bpy.data.meshes.new("QA part")
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    s = 1.0 / max(w, h)
    verts = [(x * w * s / 2, y * min(w, h) * s / 4, z * h * s / 2) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new("QA part", mesh)
    bpy.context.scene.collection.objects.link(obj)
    path = OUT / f"part-{part['id']}.glb"
    try:
        for other in bpy.context.scene.objects:
            other.select_set(other == obj)
        bpy.ops.export_scene.gltf(filepath=str(path), use_selection=True, export_format='GLB')
    finally:
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
    return path


# --- The app --------------------------------------------------------------------------------------------------------

def main_window():
    return bpy.context.window_manager.windows[0]


def studio_area():
    from angelo_agent import studio_toggle
    target = studio_toggle.opened_window(bpy.context.window_manager.windows)
    return target, (target.screen.areas[0] if target else None)


def region():
    return next(r for r in studio_area()[1].regions if r.type == 'WINDOW')


def view():
    from angelo_agent import studio
    return studio.view(studio_area()[1])


def redraw():
    from angelo_agent import studio
    target, area = studio_area()
    with bpy.context.temp_override(window=target, area=area, region=region()):
        area.tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    assert studio.last_error is None, studio.last_error


def capture(name, target=None):
    path = OUT / name
    if target is None:
        redraw()   # Studio's area tagged first: a screenshot taken right after a click shows what the click changed.
    with bpy.context.temp_override(window=target or studio_area()[0]):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def hits(action):
    return [(rect, value) for rect, a, value in view().get("hits", []) if a == action]


def click(action, value=None):
    """A real click at the centre of a drawn control, through Studio's own input handler."""
    from angelo_agent import studio
    redraw()
    rect = next(r for r, v in hits(action) if value is None or v == value)
    target, area = studio_area()
    r = region()
    event = SimpleNamespace(type='LEFTMOUSE', value='PRESS', mouse_x=r.x + rect[0] + rect[2] / 2,
                            mouse_y=r.y + rect[1] + rect[3] / 2, mouse_prev_y=r.y + rect[1], unicode="", ctrl=False,
                            oskey=False, alt=False, shift=False)
    handler = qa_operator.stand_in(studio.ANGELO_OT_studio_input, window_pointer=target.as_pointer(), focus_area=None,
                                   dragging=False)   # The operator's own methods, as its modal reaches them.
    with bpy.context.temp_override(window=target, area=area, region=r):
        studio.ANGELO_OT_studio_input.modal(handler, bpy.context, event)


def fit_gate():
    """{(connection mode, model): whether the real Fit operator's poll passes with AI Enhance bound to it}. Poll gates
    every way in (the card's button, its dialog's OK, a script or the agent calling bpy.ops)."""
    from angelo_agent import studio
    real = studio.enhancement_binding
    pairs = (("codex_subscription", "gpt-6.1-sol"), ("codex_api", "gpt-6.1-sol"), ("grok_api", "gpt-6.1-sol"),
             ("codex_subscription", "gpt-6-astra"), ("claude_subscription", "claude-opus-5-5"))
    found = {}
    try:
        for mode, model in pairs:
            studio.enhancement_binding = lambda: ({"id": "qa", "name": "QA", "mode": mode}, {"model": model})
            found[(mode, model)] = bpy.ops.angelo.studio_workflow_fit.poll()
    finally:
        studio.enhancement_binding = real
    return found


def bound_enhancers():
    """{binding: (fit offered, Studio's draw error)} with AI Enhance bound as people bind it: one Gemini key (the
    default, AUTO_GEMINI, picks it) and a Claude API key (1.0.6 slot: any bound account made the merged gate raise while
    the card drew). Real fixture connections; they and AI Enhance's settings are restored after."""
    from angelo_agent import studio, studio_workflow_ops, ui
    library, store, catalog, added, found = studio.library(), ui.connections(), ui.catalogs(), [], {}
    before, active = dict(library.enhancer), store.active
    refreshes = ui.refresh_connected_models, catalog.refresh
    ui.refresh_connected_models = catalog.refresh = lambda *args, **kwargs: None   # Fixture keys never leave the app.
    target, area = studio_area()
    try:
        for provider, group, label in (("GOOGLE", "GENAI", "QA Gemini"), ("ANTHROPIC", "CODING", "QA Claude")):
            record = store.add(provider, "API_KEY", label, group=group)
            store.active = active   # Never the chat's account: only AI Enhance is bound to it.
            ui._memory_keys[record["id"]] = "qa-fixture-key-not-a-real-account"
            added.append(record["id"])
        for binding, connection, model in (("gemini_key_auto", "AUTO_GEMINI", "gemini-3.8-flash"),
                                           ("claude_api_key", added[1], "claude-opus-5-5")):
            library.configure_enhancer(connection, model)
            studio.last_error = None
            with bpy.context.temp_override(window=target, area=area, region=region()):
                area.tag_redraw()
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            found[binding] = (studio_workflow_ops.fit_offered(), studio.last_error)
    finally:
        library.configure_enhancer(before["connection"], before["model"])
        for identity in added:
            store.remove(identity)
            ui._memory_keys.pop(identity, None)
        store.active = active
        ui.refresh_connected_models, catalog.refresh = refreshes
    return found


def key(target, kind):
    for value in ('PRESS', 'RELEASE'):
        target.event_simulate(type=kind, value=value, x=target.width // 2, y=target.height // 2)


def current():
    from angelo_agent import studio_workflows
    return studio_workflows.current(view())


def add_output(kind, path, step_key, node, connection, model):
    """A finished generation's library record, as the runner leaves it: the step's input key in its lineage."""
    from angelo_agent import flow_host
    store = flow_host.library()
    asset = store.add(kind, "[QA stand-in] " + node, model, connection=connection, status="ready",
                      lineage={"key": step_key, "variant": 0, "node": node})
    name = ("image" if kind == "IMAGE" else "model") + path.suffix
    (store.result_directory(asset["id"]) / name).write_bytes(path.read_bytes())
    asset.update(files=[name], thumbnail=name if kind == "IMAGE" else "", progress=100)
    store.update(asset)
    return asset


def finish(error=None):
    if error is not None:
        state.update(phase="failed", error=str(error), traceback=traceback.format_exc())
        log(state["traceback"])
    else:
        state.update(phase="complete", success=bool(checks) and all(checks.values()))
    state["seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    log(json.dumps({"phase": state["phase"], "success": state["success"],
                    "failed": [k for k, v in checks.items() if not v]}))
    bpy.ops.wm.quit_blender()


def tick():
    from angelo_agent import (flow_host, flow_open, flow_templates, parts_check, studio, studio_toggle,
                              studio_workflow_run as run, studio_workflows as wf, ui)
    from angelo_agent.api_connections import CheckResult
    from angelo_agent.provider_registry import SPECS
    phase = state["phase"]
    try:
        if phase == "boot":
            # This script imported angelo_agent before the add-on was enabled, so enabling it (here, or the Generative
            # AI editor's lazy start) reloads the package and resets every module's state. Do it now, before any.
            import addon_utils
            if not addon_utils.check("angelo_agent")[1]:
                addon_utils.enable("angelo_agent", default_set=False, persistent=False)
            state["phase"] = "settle"
            return .5
        if phase == "settle":
            if not wait(phase, lambda: qa_boot.started() and any(a.type == 'VIEW_3D' for a in main_window().screen.areas),
                        limit=60):   # Angelo's start-up is over (the shared boot gate), Director's 3D view shown.
                state["waiting_on"] = qa_boot.waiting_on()
                return .3
            state["character"], state["sheet"] = str(character_png()), str(sheet_png())
            accounts = {}
            for provider, label in (("OPENAI", "QA OpenAI Images"), ("MESHY", "QA Meshy")):
                record = ui.connections().add(provider, "API_KEY", label, group="GENAI")
                ui._memory_keys[record["id"]] = "qa-fixture-key-not-a-real-account"
                ui._api_connections.results[record["id"]] = CheckResult("verified", "Fixture", True,
                                                                         SPECS[record["mode"]]["models"])
                accounts[record["mode"]] = record["id"]
            state["accounts"] = accounts
            state["objects_before"] = sorted(o.name for o in bpy.context.scene.objects)
            state["layout"] = sorted((a.type, a.width, a.height) for a in main_window().screen.areas)
            with bpy.context.temp_override(window=main_window()):
                bpy.ops.angelo.open_media_studio(kind='WORKFLOW')   # The chat bar's + ▸ Workflows (open_media_studio).
            state["phase"] = "opened"
            return .5
        if phase == "opened":
            if not wait(phase, lambda: studio_area()[1] is not None):
                return .2
            v = view()
            redraw()
            checks["workflows_opens_studio_in_its_own_window_on_the_tab"] = (
                studio_toggle.is_studio(studio_area()[1]) and v["kind"] == "WORKFLOW")
            state["layout_after"] = sorted((a.type, a.width, a.height) for a in main_window().screen.areas)
            checks["directors_layout_is_unchanged"] = state["layout_after"] == state["layout"]
            checks["the_tabs_are_image_3d_workflows"] = [value for _, a, value in v["hits"] if a == "tab"] == [
                "IMAGE", "3D", "WORKFLOW"]
            value, tree, shown, _ = current()
            checks["a_new_run_starts_at_its_input"] = shown["stage"] == "FILL" and tree is None
            entries = flow_open.template_entries()
            template = flow_templates.template(value["template"])
            chosen = {role: (flow_templates._choose(spec, entries) or {}).get("id") for role, spec in template["roles"].items()}
            checks["roles_resolve_to_the_connected_accounts"] = chosen == {"sheet": "gpt-image-2.5-sunburst", "mesh": "meshy-7"}
            checks["the_parts_check_is_not_offered_until_measured"] = parts_check.READY or not hits("wf_check")
            checks["the_fit_operator_runs_only_on_the_measured_account_and_model"] = fit_gate() == {
                ("codex_subscription", "gpt-6.1-sol"): True, ("codex_api", "gpt-6.1-sol"): False,
                ("grok_api", "gpt-6.1-sol"): False, ("codex_subscription", "gpt-6-astra"): False,
                ("claude_subscription", "claude-opus-5-5"): False}
            capture("workflows-1-input.png")
            click("wf_template", "character-in-parts-from-text")
            redraw()
            checks["the_description_variant_asks_for_text"] = (wf.workflow(v)["template"] == "character-in-parts-from-text"
                                                               and bool(hits("focus")))
            capture("workflows-2-description.png")
            click("wf_template", "character-in-parts")
            run.attach(v, [Path(state["character"])])
            redraw()
            checks["the_character_image_is_the_input"] = bool(wf.workflow(v)["image"])
            capture("workflows-3-image.png")
            click("wf_edit")   # Edit in Generation Flow: no Node Editor beside this Studio, so the flow window.
            state["phase"] = "flow_window"
            return .5
        if phase == "flow_window":
            if not wait(phase, lambda: flow_open.flow_window(bpy.context.window_manager.windows) is not None):
                return .2
            target = flow_open.flow_window(bpy.context.window_manager.windows)
            value, tree, _, _ = current()
            checks["edit_in_generation_flow_shows_the_run"] = (tree is not None
                                                               and target.screen.areas[0].spaces.active.node_tree == tree)
            capture("workflows-3b-flow-window.png", target)
            with bpy.context.temp_override(window=target, area=target.screen.areas[0]):
                bpy.ops.wm.window_close()
            click("wf_start")   # Make parts sheet: the itemized approval opens; declining runs nothing.
            state["phase"] = "decline_sheet"
            return 1.0
        if phase == "decline_sheet":
            lines = [line.node for line in bpy.context.window_manager.angelo_flow_run_lines]
            checks["make_parts_sheet_asks_to_approve_the_sheet"] = lines == [flow_templates.SHEET_STEP]
            checks["make_parts_sheet_asks_without_an_error"] = not view().get("notice")
            state["notice_after_start"] = view().get("notice")
            key(studio_area()[0], 'ESC')
            state["phase"] = "declined_sheet"
            return .6
        if phase == "declined_sheet":
            value, tree, shown, running = current()
            checks["declining_the_sheet_runs_nothing"] = (tree is not None and not running and not studio._jobs.active
                                                          and not flow_host.active_runs())
            checks["the_run_is_an_ordinary_flow_with_the_template_steps"] = tree is not None and {
                flow_templates.SHEET_STEP, flow_templates.SPLIT_STEP, flow_templates.PARTS_STEP,
                flow_templates.CHARACTER_STEP} <= {n.name for n in tree.nodes}
            entry = flow_host.refresh(tree)
            sheet = entry["states"][flow_templates.SHEET_STEP]
            state["sheet_state"] = sheet.get("state")
            output = add_output("IMAGE", Path(state["sheet"]), sheet["key"], flow_templates.SHEET_STEP,
                                state["accounts"]["openai_images_api"], "gpt-image-2.5-sunburst")
            state["sheet_asset"] = output["id"]
            flow_host.tree_changed(tree)
            state["phase"] = "split"
            return .5
        if phase == "split":
            value, tree, shown, _ = current()
            sheet = (flow_host.cached(tree.name) or {}).get("states", {}).get(flow_templates.SHEET_STEP) or {}
            if sheet.get("state") not in (None, "READY", "RUNNING") and not sheet.get("chosen"):
                flow_host.set_chosen(tree, flow_templates.SHEET_STEP, state["sheet_asset"])   # The review stop's pick.
            if not wait(phase, lambda: current()[2]["stage"] == "REVIEW", limit=30):
                return .3
            value, tree, shown, _ = current()
            parts = shown["parts"]
            state["parts"] = [{k: p[k] for k in ("id", "label", "kind")} for p in parts]
            checks["the_sheet_splits_into_parts_with_one_body"] = sum(p["kind"] == "BODY" for p in parts) == 1 and len(parts) >= 3
            redraw()
            from angelo_agent import studio_workflow_view as wv
            board = view()["geometry"].get("assets") if "geometry" in view() else None
            boxes = wv.outlines(shown["split"], flow_templates.CHARACTER_SLOTS, (0, 0, 1000, 500))
            checks["every_part_is_outlined_on_the_sheet"] = len(boxes) == len(parts)
            state["board"] = board
            capture("workflows-4-review.png")
            state["bound_enhancers"] = {k: [v[0], repr(v[1]) if v[1] else None] for k, v in bound_enhancers().items()}
            checks["the_card_draws_and_offers_no_fit_with_ai_enhance_bound"] = state["bound_enhancers"] == {
                "gemini_key_auto": [False, None], "claude_api_key": [False, None]}
            card, chips = view()["geometry"]["wf_card"], view()["geometry"]["wf_chips"]
            checks["the_step_chips_fit_inside_the_card"] = len(chips) == 4 and all(
                card[0] < x and x + w < card[0] + card[2] and card[1] < y and y + h < card[1] + card[3]
                for x, y, w, h in chips)
            body = next(p["id"] for p in parts if p["kind"] == "BODY")
            checks["the_body_cannot_be_turned_off"] = bool(hits("wf_skip")) and all(v != body for _, v in hits("wf_skip"))
            skipped = next((p["id"] for p in parts if p["kind"] != "BODY"), None)
            click("wf_skip", skipped)
            checks["a_part_can_be_left_out"] = wf.workflow(view())["skip"] == [skipped]
            state["skipped"] = skipped
            capture("workflows-5-review-one-off.png")
            checks["the_screen_shows_the_part_left_out"] = (OUT / "workflows-5-review-one-off.png").read_bytes() != (
                OUT / "workflows-4-review.png").read_bytes()
            click("wf_parts")   # Make 3D for N parts: the run dialog; declining runs nothing.
            state["phase"] = "decline_parts"
            return 1.0
        if phase == "decline_parts":
            lines = {line.node: line.include for line in bpy.context.window_manager.angelo_flow_run_lines}
            state["parts_dialog"] = lines
            value, tree, shown, _ = current()
            steps = {p["step"] for p in shown["parts"]}
            skipped_step = next(p["step"] for p in shown["parts"] if p["id"] == state["skipped"])
            checks["make_3d_lists_every_part_with_the_left_out_one_unticked"] = (
                set(lines) == steps and lines.get(skipped_step) is False and sum(lines.values()) == len(steps) - 1)
            key(studio_area()[0], 'ESC')
            state["phase"] = "models"
            return .6
        if phase == "models":
            value, tree, shown, running = current()
            checks["declining_the_parts_runs_nothing"] = not running and not flow_host.active_runs() and not studio._jobs.active
            states = flow_host.refresh(tree)["states"]
            for part in shown["parts"]:
                if part["id"] == state["skipped"] or not part["step"]:
                    continue
                split = next(p for p in shown["split"]["parts"] if p["id"] == part["id"])
                add_output("3D", part_glb(split), states[part["step"]]["key"], part["step"],
                           state["accounts"]["meshy_api"], "meshy-7")
            flow_host.tree_changed(tree)
            state["phase"] = "place_ready"
            return .5
        if phase == "place_ready":
            if not wait(phase, lambda: current()[2]["stage"] == "PLACE", limit=30):
                return .3
            value, tree, shown, _ = current()
            redraw()
            done = [p for p in shown["parts"] if p["status"] == "DONE"]
            checks["ready_parts_reach_the_starting_point"] = len(done) == len(shown["parts"]) - 1
            checks["the_card_asks_to_check_hair_headwear_and_accessories"] = all(
                p["check"] == (p["kind"] in {"HAIR", "HEADWEAR", "ACCESSORY"}) for p in shown["parts"])
            checks["place_and_new_character_are_offered"] = bool(hits("wf_place")) and bool(hits("wf_new"))
            capture("workflows-6-starting-point.png")
            # A placement that fails part-way (a model file gone after two imports) leaves the scene as it was.
            from angelo_agent import character_compose
            from angelo_agent.studio_host import import_glb
            entry = flow_host.refresh(tree)["states"][flow_templates.CHARACTER_STEP]
            store, seen, collections = flow_host.library(), [], len(bpy.data.collections)

            def failing_import(path):
                seen.append(path)
                if len(seen) > 2:
                    raise character_compose.ComposeError("[QA] a model file went missing")
                return import_glb(main_window(), path)
            try:
                character_compose.compose(main_window(), entry["value"], failing_import,
                                          lambda identity: store.checked_file(store.assets[identity], store.assets[identity]["files"][0]),
                                          name="QA refused")
            except character_compose.ComposeError:
                pass
            checks["a_place_that_fails_part_way_adds_nothing"] = (
                len(seen) == 3 and sorted(o.name for o in bpy.context.scene.objects) == state["objects_before"]
                and len(bpy.data.collections) == collections)
            if os.environ.get("ANGELO_QA_GROUNDING"):   # An approved fit's result, stored as the fit stores it.
                from studio_workflows_evidence import store_fit
                state["grounding"] = store_fit(tree, shown, checks)
                redraw()
                checks["the_card_says_the_parts_were_fitted"] = bool(current()[2]["split"].get("grounding"))
                capture("workflows-6b-fitted-to-the-image.png")
            click("wf_place")   # In the Studio window, which has no 3D view of its own.
            state["phase"] = "placed"
            return .5
        if phase == "placed":
            value, tree, shown, _ = current()
            state["tree"] = tree.name
            receipt = flow_host.last_placement.get(tree.name) or {}
            added = [o for o in bpy.context.scene.objects if o.name not in state["objects_before"]]
            roots = [o for o in added if o.parent is None]
            meshes = [o for o in added if o.type == 'MESH']
            state["placed"] = {"receipt": receipt, "objects": sorted(o.name for o in added)}
            checks["place_in_scene_works_from_the_studio_window"] = (
                receipt.get("parts") == len(meshes) == len(shown["parts"]) - 1 and len(roots) == 1)
            checks["each_part_is_its_own_object"] = len({o.data.name for o in meshes}) == len(meshes)
            view3d = next(a for a in main_window().screen.areas if a.type == 'VIEW_3D')
            for obj in bpy.context.scene.objects:   # Framed for the screenshot (the 3D cursor sits in the default cube).
                obj.select_set(obj in added)
                if obj not in added and obj.type == 'MESH':
                    obj.hide_set(True)
            with bpy.context.temp_override(window=main_window(), area=view3d,
                                           region=next(r for r in view3d.regions if r.type == 'WINDOW')):
                bpy.ops.view3d.view_selected()
                view3d.spaces.active.shading.type = 'MATERIAL'
            if state.get("grounding"):
                grounded = [p for p, info in (receipt.get("placements") or {}).items() if info.get("grounded")]
                checks["fitted_parts_are_placed_from_the_image"] = set(grounded) == set(state["grounding"]["boxes"]) & set(
                    receipt.get("placements") or {})
            capture("workflows-7-placed-in-the-scene.png", main_window())
            capture("workflows-8-studio-after-placing.png")
            if state.get("grounding"):   # The evidence runs: the parts are separate objects a person can swap or hide.
                from studio_workflows_evidence import swap_and_hide
                checks["a_part_can_be_hidden_and_another_swapped"] = swap_and_hide(meshes, main_window())
                redraw()
                capture("workflows-7b-part-hidden-and-swapped.png", main_window())
            with bpy.context.temp_override(window=main_window()):
                bpy.ops.ed.undo()
            checks["undo_removes_the_placed_character"] = sorted(o.name for o in bpy.context.scene.objects) == state["objects_before"]
            checks["undo_keeps_the_run"] = bpy.data.node_groups.get(state["tree"]) is not None and current()[1] is not None
            state["models_before_undo"] = sorted(p["model"] for p in shown["parts"] if p["model"])
            state["phase"] = "after_undo"
            return .5
        if phase == "after_undo":
            # The run comes back to its starting point with the same models: a re-split reuses its crops, so every
            # part's input key, and the models made for it, still match. Nothing would be paid for again.
            if not wait(phase, lambda: current()[2]["stage"] == "PLACE", limit=30):
                return .3
            shown = current()[2]
            checks["after_undo_the_parts_already_made_are_reused"] = sorted(
                p["model"] for p in shown["parts"] if p["model"]) == state["models_before_undo"]
            value, tree, _, _ = current()
            redraw()
            checks["the_run_is_listed_under_runs"] = any(v == tree.name for _, v in hits("wf_open"))
            image = wf.workflow(view())["image"]
            click("wf_new")
            checks["new_character_starts_over"] = current()[1] is None and current()[2]["stage"] == "FILL"
            run.attach(view(), [Path(state["sheet"])])   # The next character's input, not yet run.
            click("wf_open", tree.name)
            reopened = current()
            checks["a_run_opened_from_runs_shows_its_own_input"] = (reopened[0]["image"] == image
                                                                   and reopened[2]["stage"] == "PLACE")
            run.ensure_tree(view())   # What every input-writing click goes through (Make parts sheet, Edit in Generation Flow).
            from angelo_agent import flow_tree
            source = next(n for n in flow_tree.tree_flow(tree)["nodes"] if n["id"] == "Character image").get("source") or {}
            checks["open_in_generation_flow_never_rewrites_a_run"] = source.get("id") == image   # The run's own flow.
            checks["no_studio_or_canvas_errors"] = studio.last_error is None
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=1.0)
