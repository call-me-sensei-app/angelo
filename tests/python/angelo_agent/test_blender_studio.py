# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual native Studio canvas, synthetic pointer/key events, fixture-only APIs."""

import base64
import hashlib
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy
import gpu

assert not bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-studio-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"), ANGELO_LIBRARY_DIR=str(OUT / "library"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_operator and qa_fixtures, inside the app.
from angelo_agent import ui, studio, console, editor_types
import qa_operator  # noqa: E402
from angelo_agent.editor_registry import EDITOR_GROUPS, GENERATIVE_AI
from angelo_agent.api_connections import CheckResult
from angelo_agent.provider_registry import SPECS

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
state = {"phase":"boot", "checks":{}, "fixture":"Local UI/API fixtures only; no real keys or paid provider calls", "success":False}
pointer, capture_request, handler = None, None, None
before = [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
calls, bodies = [], []


def fixture_jpeg():
    """A picture painted here, encoded as JPEG, Gemini's only documented image output type.

    Painted (qa_fixtures) rather than read from the repository: its artwork is in Git LFS, a pointer in a snapshot or
    a worktree that skips LFS (studio-disc-full on 6ceaef365ff: "Fixture artwork did not decode"). Encoded here on the
    main thread: the fixture transport runs on a worker.
    """
    path = OUT / "gemini-fixture.jpg"
    import qa_fixtures
    qa_fixtures.painted_picture(path, 'JPEG')
    data = path.read_bytes()
    assert data.startswith(b"\xff\xd8\xff"), "The fixture JPEG was not written"
    return data


FIXTURE_JPEG = fixture_jpeg()


def fixture_transport(url, **kwargs):
    calls.append((url, kwargs.get("body") is not None))
    # Use the painted picture as a labelled UI fixture, never claim this is
    # a real model generation or add this fixture to the user's actual library.
    body = json.loads(kwargs.get("body") or b"{}")
    bodies.append(body)
    if body.get("model") == "gemini-3.8-flash":
        return b'{"steps":[{"type":"model_output","content":[{"type":"text","text":"Enhanced fixture prompt: luminous blue sword"}]}]}'
    image = {"type":"image", "mime_type":"image/jpeg", "data":base64.b64encode(FIXTURE_JPEG).decode()}
    return json.dumps({"steps":[{"type":"model_output", "content":[image]}]}).encode()


def window():
    return bpy.context.window_manager.windows[0]


def area():
    return next(a for a in window().screen.areas if a.as_pointer() == pointer)


def region():
    return next(r for r in area().regions if r.type == 'WINDOW')


def drawn_frame():
    request = capture_request
    if request is not None and bpy.context.area.as_pointer() == pointer:
        w, h = bpy.context.region.width, bpy.context.region.height
        pixels = gpu.state.active_framebuffer_get().read_color(0, 0, w, h, 4, 0, 'UBYTE')
        pixels.dimensions = w * h * 4
        request.update(pixels=[v / 255 for v in pixels], width=w, height=h, hash=hashlib.sha256(bytes(pixels)).hexdigest())


frame_handler = bpy.types.SpaceConsole.draw_handler_add(drawn_frame, (), 'WINDOW', 'POST_PIXEL')


def redraw():
    with bpy.context.temp_override(window=window(), area=area(), region=region()):
        area().tag_redraw()
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    assert studio.last_error is None, studio.last_error
    assert editor_types.last_error is None, editor_types.last_error


def capture(name):
    global capture_request
    capture_request = {}
    redraw()
    request, capture_request = capture_request, None
    assert request.get("pixels"), "No native framebuffer capture"
    image = bpy.data.images.new("Angelo Studio QA Frame", width=request["width"], height=request["height"], alpha=True)
    try:
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set(request.pop("pixels"))
        image.filepath_raw, image.file_format = str(OUT / name), 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)
    state.setdefault("captures", {})[name] = request


def event(kind, x=30, y=30, unicode="", ctrl=False):
    r = region()
    value = SimpleNamespace(type=kind, value='PRESS', mouse_x=r.x+x, mouse_y=r.y+y, mouse_prev_y=r.y+y,
        unicode=unicode, ctrl=ctrl, oskey=False, alt=False, shift=False)
    with bpy.context.temp_override(window=window(), area=area(), region=r):
        return studio.ANGELO_OT_studio_input.modal(handler, bpy.context, value)


def click(action, value=None):
    redraw()
    v = studio.view(area())
    rect = next(r for r, a, arg in v["hits"] if a == action and (value is None or arg == value))
    event('LEFTMOUSE', rect[0]+rect[2]/2, rect[1]+rect[3]/2)


def select(identity):
    with bpy.context.temp_override(window=window(), area=area(), region=region()):
        bpy.ops.angelo.editor_type(ui_type=identity)


def tick():
    global pointer, handler
    try:
        if state["phase"] == "boot":
            import qa_boot   # Angelo's start-up rebuilds the window: an area taken before it ends is gone after.
            if not qa_boot.started():
                return .2
            pointer = next(a for a in window().screen.areas if a.type == 'VIEW_3D').as_pointer()
            select("CONSOLE")
            state["phase"] = "python_initial"
            return .3
        if state["phase"] == "python_initial":
            with bpy.context.temp_override(window=window(), area=area(), region=region()):
                area().spaces.active.history[-1].body = "studio_native_python_probe=23; print('PYTHON_STUDIO_OK',studio_native_python_probe)"
                bpy.ops.console.execute()
            area().spaces.active.history[-1].body = "print('separate Python draft')"
            select(GENERATIVE_AI)
            handler = qa_operator.stand_in(studio.ANGELO_OT_studio_input, window_pointer=window().as_pointer(),
                                           focus_area=None, dragging=False)
            studio.library()
            state["checks"]["entry_below_coding_agent"] = dict(EDITOR_GROUPS)["AI"][1][0] == GENERATIVE_AI
            state["checks"]["studio_not_python_or_chat"] = studio.is_studio(area()) and not console.is_chat(area())
            state["phase"] = "canvas"
            return .3
        if state["phase"] == "canvas":
            v = studio.view(area())
            v["collapsed"] = False
            redraw()
            state["checks"]["native_canvas_draws_without_error"] = studio.draw_count > 0 and studio.last_error is None
            state["checks"]["only_image_and_3d_tabs"] = [value for _, action, value in v["hits"] if action == "tab"] == ["IMAGE", "3D"]
            state["checks"]["no_cms_or_character_reference_buttons"] = not any(a in {"cms", "character"} for _, a, _ in v["hits"])
            state["checks"]["one_compact_model_dropdown"] = sum(a == "model" for _, a, _ in v["hits"]) == 1
            capture("studio-expanded-empty.png")
            expanded_width = v["geometry"]["assets"][2]
            click("collapse")
            redraw()
            state["checks"]["collapse_frees_asset_space"] = v["collapsed"] and v["geometry"]["assets"][2] > expanded_width
            capture("studio-collapsed-empty.png")
            click("collapse")
            click("focus", "prompt")
            event('A', unicode='A')
            event('RET')
            event('B', unicode='B')
            state["checks"]["prompt_native_multiline_and_no_python_execution"] = v["prompt"] == "A\nB" and before == [(o.name,tuple(o.location)) for o in bpy.context.scene.objects]
            click("model")
            redraw()
            capture("studio-model-dropdown.png")
            state["checks"]["model_menu_not_click_and_hold"] = bool(v.get("popover")) and len(v["popup_hits"]) >= 5
            rect, choice = next((r, c) for r, c in v["popup_hits"] if c[1] == "gemini-3.1-flash-image")
            event('LEFTMOUSE', rect[0]+10, rect[1]+10)
            state["checks"]["single_click_selects_model"] = studio.selected(v)["id"] == "gemini-3.1-flash-image" and not v.get("popover")
            record = ui.connections().add("GOOGLE", "API_KEY", "Fixture Gemini", group="GENAI")
            ui._memory_keys[record["id"]] = "fixture-key-not-a-real-account"
            ui._api_connections.results[record["id"]] = CheckResult("verified", "Fixture", True, SPECS[record["mode"]]["models"])
            v["models"]["IMAGE"] = (record["id"], "gemini-3.1-flash-image")
            studio._jobs.transport = fixture_transport
            folder = studio.library().folder("Local UI fixtures")
            v["folder"] = folder["id"]
            state["checks"]["enhancer_uses_shared_gemini_connection"] = studio.enhancement_binding()[0]["id"] == record["id"] and studio.library().enhancer["model"] == "gemini-3.8-flash"
            v["prompt"] = "[Local UI fixture] Native gallery result"
            click("generate")
            state["checks"]["generate_dialog_alone_never_calls_provider"] = not calls and not studio._jobs.active
            state["phase"] = "confirm_generation"
            return .3
        if state["phase"] == "confirm_generation":
            window().event_simulate(type='RET', value='PRESS')
            state["phase"] = "job"
            return .5
        if state["phase"] == "job":
            studio._jobs.pump()
            if studio._jobs.active:
                return .2
            asset = next(a for a in studio.library().assets.values() if a["folder"] == studio.view(area())["folder"])
            state["asset"] = asset["id"]
            state["checks"]["async_fixture_generation_saved_to_library"] = asset["status"] == "ready" and studio.library().checked_file(asset, asset["files"][0]).is_file()
            state["checks"]["exactly_one_fixture_post_no_live_network"] = len(calls) == 1 and calls[0][1]
            state["checks"]["gemini_requests_documented_jpeg"] = bodies[0].get("response_format", {}).get("mime_type") == "image/jpeg"
            saved = studio.library().checked_file(asset, asset["files"][0])
            state["checks"]["gemini_jpeg_kept_as_jpg_output"] = (asset["files"] == ["image-1.jpg"] and asset["thumbnail"] == "image-1.jpg"
                and saved.read_bytes() == FIXTURE_JPEG)
            capture("studio-expanded-gallery.png")
            entry = studio._textures.get((asset["id"], "image-1.jpg"))
            state["checks"]["jpeg_thumbnail_drawn_in_library"] = bool(entry) and min(entry[0].size) > 0
            v = studio.view(area())
            v["collapsed"] = True
            capture("studio-collapsed-gallery.png")
            v["collapsed"] = False
            click("enhance")
            state["phase"] = "confirm_enhance"
            return .3
        if state["phase"] == "confirm_enhance":
            window().event_simulate(type='RET', value='PRESS')
            state["phase"] = "enhanced"
            return .5
        if state["phase"] == "enhanced":
            studio.pump()
            v = studio.view(area())
            if v.get("enhancing"):
                return .1
            state["checks"]["enhancement_preserves_original_until_applied"] = v["enhanced"] == "Enhanced fixture prompt: luminous blue sword" and v["prompt"] == "[Local UI fixture] Native gallery result"
            click("review_enhance")
            capture("studio-enhanced-prompt-review.png")
            state["checks"]["expanded_multiline_prompt_editor"] = v.get("prompt_dialog", {}).get("enhanced") and v["focus"] == "expanded_prompt"
            click("prompt_apply")
            state["checks"]["apply_and_undo_prompt_enhancement"] = v["prompt"] == "Enhanced fixture prompt: luminous blue sword"
            click("undo_enhance")
            state["checks"]["apply_and_undo_prompt_enhancement"] &= v["prompt"] == "[Local UI fixture] Native gallery result"
            state["checks"]["enhancer_exact_default_and_shared_key"] = len(calls) == 2 and calls[1][0].endswith("/interactions")
            v["prompt"] = "A\nB"
            click("tab", "3D")
            v["collapsed"] = False
            capture("studio-3d-tab.png")
            state["checks"]["3d_dropdown_contains_only_3d_models"] = all(SPECS[m["mode"]]["kind"] == "3D" for m in studio.options(v))
            config = studio.library().enhancer
            studio.library().configure_enhancer(config["connection"], config["model"])
            state["checks"]["enhancer_setting_saved_without_key"] = "fixture-key" not in studio.library().enhancer_path.read_text()
            select("CONSOLE")
            state["phase"] = "python_restored"
            return .2
        if state["phase"] == "python_restored":
            state["checks"]["python_input_and_history_restored"] = area().spaces.active.history[-1].body == "print('separate Python draft')" and any('PYTHON_STUDIO_OK 23' in l.body for l in area().spaces.active.scrollback)
            select(GENERATIVE_AI)
            state["checks"]["studio_draft_survives_editor_switch"] = studio.view(area())["prompt"] == "A\nB"
            state["checks"]["scene_is_unchanged"] = before == [(o.name,tuple(o.location)) for o in bpy.context.scene.objects]
            state["checks"]["no_header_or_canvas_errors"] = studio.last_error is None and editor_types.last_error is None
            addon_utils.disable("angelo_agent", default_set=False)
            state["checks"]["disable_returns_python_console"] = area().spaces.active.language == "python"
            state["success"] = all(state["checks"].values())
            state["phase"] = "complete"
            (OUT / "studio-qa.json").write_text(json.dumps(state, indent=2))
            print("ANGELO_STUDIO_GUI", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
    except Exception as error:
        state.update(phase="failed", error=str(error), traceback=traceback.format_exc())
        (OUT / "studio-qa.json").write_text(json.dumps(state, indent=2))
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return .2


bpy.app.timers.register(tick, first_interval=1)
