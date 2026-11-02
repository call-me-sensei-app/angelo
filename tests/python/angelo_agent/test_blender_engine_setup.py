# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Setting up an installed render engine from Director's engine dropdown, all inside Angelo (1.0.8).

Real default startup (Director with the chat) and real input. No CLI, account, network or inference. A vendor's
installer is played by this suite: it writes a small engine add-on (``bl_info`` category Render, an engine that fills
its image with one colour, and an account operator standing in for the vendor's sign-in window) into a private
folder that stands for Blender's add-ons folder (``engine_setup.blender_addons``); no Blender is involved. Checked
against doc/angelo/render_engines.rst, "Setting up an installed engine": the dropdown lists the add-on under its own
name with the set-up line; clicking it links it into Angelo's add-ons folder, turns it on and makes its engine the
scene's; F12 renders through it; for a vendor that needs a licence (a stand-in ``engine_status`` entry) whose sign-in
service is silent at first, Angelo's corner card holds the set-up with Retry (nothing turned on), then asks for a
sign-in, its Sign in button calls the add-on's own operator, and the card then says the engine
is ready with its edition.

Run through ``run_native_suites.py --gui --include test_blender_engine_setup``; check ``engine-setup.json`` for
``success`` and ``phase == "complete"``.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_boot  # noqa: E402
product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-engine-setup-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "engine-setup.json"
VENDOR_FOLDER = Path(tempfile.mkdtemp(prefix="angelo-qa-vendor-"))   # Stands for Blender's add-ons folder.
MODULE, NAME, IDNAME, VENDOR = "angelo_qa_vendor_engine", "QA Vendor Engine", "ANGELO_QA_VENDOR_ENGINE", "QA Vendor Co"
COLOUR = (0.2, 0.6, 0.3)
ADDON = '''bl_info = {"name": "%s", "category": "Render", "blender": (5, 0)}
import bpy
STATE = {"signed_in": False}


class QAVendorEngine(bpy.types.RenderEngine):
    bl_idname, bl_label = "%s", "QA Vendor"

    def render(self, depsgraph):
        scale = depsgraph.scene.render.resolution_percentage / 100.0
        width = int(depsgraph.scene.render.resolution_x * scale)
        height = int(depsgraph.scene.render.resolution_y * scale)
        result = self.begin_result(0, 0, width, height)
        result.layers[0].passes["Combined"].rect = [(%r, %r, %r, 1.0)] * (width * height)
        self.end_result(result)


class QAVENDOR_OT_account(bpy.types.Operator):
    bl_idname, bl_label = "qa_vendor.account", "Account"

    def execute(self, context):
        STATE["signed_in"] = True   # The vendor's own window would sign the person in.
        return {'FINISHED'}


def register():
    bpy.utils.register_class(QAVendorEngine)
    bpy.utils.register_class(QAVENDOR_OT_account)


def unregister():
    bpy.utils.unregister_class(QAVENDOR_OT_account)
    bpy.utils.unregister_class(QAVendorEngine)
''' % (NAME, IDNAME, *COLOUR)
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "measured": {}}
checks = state["checks"]
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def press(x, y):
    qa_input.press(window(), x, y)


def record_top_bar():
    from angelo_agent import chat_toggle
    original = chat_toggle.draw

    def draw(layout, context, *args, **kwargs):
        state["top_bar"] = (context.region.y, context.region.height)
        return original(layout, context, *args, **kwargs)
    chat_toggle.draw = draw


def layout_drawn():
    from angelo_agent import console
    areas = window().screen.areas
    return any(a.type == 'VIEW_3D' for a in areas) and any(console.is_chat(a) for a in areas) and all(
        r.width > 1 for a in areas for r in a.regions if r.type == 'WINDOW')


def engine_point():
    from angelo_agent import chat_toggle, top_bar_fit
    left, _right, bottom = chat_toggle.anchors[window().as_pointer()]
    fitted = top_bar_fit.widths[window().as_pointer()]
    x = left
    for each in ("chat", "studio", "flows", "engine"):
        _, units, unit = fitted[each]
        if each == "engine":
            return x + 0.3 * units * unit, bottom + state["top_bar"][1] / 2
        x += units * unit


def menu_row_point(value):
    from angelo_agent import flow_menu
    area = next(a for a in window().screen.areas if a.as_pointer() == flow_menu._open["area"])
    region = next(r for r in area.regions if r.type == 'WINDOW')
    scale = flow_menu._open["scale"]
    index = next(i for i, row in enumerate(flow_menu._open["rows"]) if row.get("value") == value)
    rx, ry, rw, rh = flow_menu._open["layout"]["rows"][index]
    return region.x + (rx + rw / 2) * scale, region.y + (ry + rh / 2) * scale


def card_hit(action):
    """Window pixels of the visible card's control ``action`` (card_host's plan, region pixels)."""
    from angelo_agent import card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    for (x, y, w, h), name, _value in active.plan.hits:
        if name == action:
            return region.x + x + w / 2, region.y + y + h / 2
    return None


def cleanup():
    import addon_utils
    from angelo_agent import engine_status
    engine_status.VENDORS.pop(MODULE, None)
    if MODULE in bpy.context.preferences.addons:
        addon_utils.disable(MODULE, default_set=True)
    link = Path(bpy.utils.user_resource('SCRIPTS', path="addons")) / MODULE
    if link.is_symlink() or link.exists():
        link.unlink() if link.is_symlink() else shutil.rmtree(link, ignore_errors=True)
    shutil.rmtree(VENDOR_FOLDER, ignore_errors=True)


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    try:
        cleanup()
    except Exception as problem:
        state["cleanup_error"] = repr(problem)
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_ENGINE_SETUP", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def install():
    """The vendor's installer: its add-on in Blender's add-ons folder, which Angelo reads (no Blender needed)."""
    from angelo_agent import engine_setup, engine_status
    (VENDOR_FOLDER / MODULE).mkdir()
    (VENDOR_FOLDER / MODULE / "__init__.py").write_text(ADDON, encoding="utf-8")
    engine_setup.blender_addons = lambda *args, **kwargs: VENDOR_FOLDER

    def read():
        vendor = sys.modules.get(MODULE)
        return bool(vendor and vendor.STATE["signed_in"]), engine_status.PAID
    def service():   # Checked on a worker thread, never Blender's, so no draw or click waits on it.
        state.setdefault("service_threads", set()).add(threading.current_thread() is threading.main_thread())
        return state["service_up"]
    # Its sign-in service is down at first: Angelo holds the set-up in its own card instead of the vendor's dialog.
    engine_status.VENDORS[MODULE] = engine_status.Vendor(VENDOR, read, "qa_vendor.account", service=service)
    state["service_up"] = False
    engine_status.GRACE = 0.0   # Its stand-in server is up at once: the card asks for the sign-in straight away.


def tick():
    from angelo_agent import card_host, director, engine_status, flow_menu, render_engines
    phase = state["phase"]
    scene = bpy.context.scene
    try:
        assert time.monotonic() - started < 180, "Suite timed out"
        if phase == "boot":
            if not state.get("recording"):
                record_top_bar()
                state["recording"] = True
            if not ("top_bar" in state and qa_boot.started() and director.is_director(window()) and layout_drawn()):
                return .3
            seen = state.setdefault("since", {}).setdefault("layout_drawn", time.monotonic())
            if time.monotonic() - seen < 1.0:
                return .2
            install()
            scene.render.engine = render_engines.EEVEE
            press(*engine_point())
            state["phase"] = "menu"
            return .6
        if phase == "menu":
            if not wait(phase, lambda: flow_menu.is_open(window(), flow_menu.ENGINES) and flow_menu._open.get("layout")):
                return .2
            rows = [row for row in flow_menu._open["rows"] if row["kind"] == "row"]
            row = next((row for row in rows if row["value"] == "setup:" + MODULE), None)
            state["measured"]["rows"] = [r["value"] for r in rows]
            checks["the_dropdown_offers_the_installed_add_on_to_set_up"] = bool(row) and row["label"] == NAME
            checks["it_is_not_on_before_the_click"] = MODULE not in bpy.context.preferences.addons
            capture("engine-setup-1-dropdown.png")
            press(*menu_row_point("setup:" + MODULE))
            state["phase"] = "held"
            return .6
        if phase == "held":
            if not wait(phase, lambda: card_host.is_active("engine_status") and card_host._active.plan is not None
                        and card_host.active_card().shown[0] == engine_status.SERVICE):
                return .2
            card = card_host.active_card()
            checks["a_silent_service_holds_the_set_up_in_angelos_card"] = card.shown[0] == engine_status.SERVICE
            checks["the_service_is_checked_off_blenders_thread"] = state.get("service_threads") == {False}
            checks["nothing_is_turned_on_while_held"] = MODULE not in bpy.context.preferences.addons
            checks["the_held_card_never_takes_the_viewport"] = not card.modal
            capture("engine-setup-2-service-card.png")
            state["service_up"] = True   # The person restarts it; Retry reads it again.
            press(*card_hit("retry"))
            state["phase"] = "set_up"
            return .6
        if phase == "set_up":
            if not wait(phase, lambda: scene.render.engine == IDNAME and card_host.is_active("engine_status")
                        and card_host._active.plan is not None):
                return .2
            link = Path(bpy.utils.user_resource('SCRIPTS', path="addons")) / MODULE
            checks["the_click_linked_it_into_angelos_add_ons"] = link.is_symlink() or link.is_dir()
            checks["the_add_on_is_on"] = MODULE in bpy.context.preferences.addons
            checks["its_engine_is_the_scenes"] = scene.render.engine == IDNAME
            card = card_host.active_card()
            checks["the_card_asks_for_the_vendors_sign_in"] = card.shown[0] == engine_status.SIGN_IN
            capture("engine-setup-3-sign-in-card.png")
            press(*card_hit("sign_in"))
            state["phase"] = "signed_in"
            return .6
        if phase == "signed_in":
            if not wait(phase, lambda: card_host.is_active("engine_status")
                        and card_host.active_card().shown[0] == engine_status.READY, limit=8):
                return .3
            checks["the_vendors_own_operator_signed_in"] = sys.modules[MODULE].STATE["signed_in"]
            checks["the_card_says_ready_with_the_edition"] = card_host.active_card().shown == (
                engine_status.READY, engine_status.PAID)
            capture("engine-setup-4-ready-card.png")
            scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 64, 48, 100
            scene.view_settings.view_transform, scene.view_settings.look = 'Standard', 'None'
            scene.view_settings.exposure, scene.view_settings.gamma = 0.0, 1.0
            bpy.ops.render.render()
            image = bpy.data.images.get("Render Result")
            pixel = None
            if image is not None:
                path = OUT / "engine-setup-render.png"
                image.save_render(str(path))
                loaded = bpy.data.images.load(str(path))
                pixel = [round(v, 2) for v in loaded.pixels[:3]]
                bpy.data.images.remove(loaded)
            srgb = [12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055 for c in COLOUR]
            state["measured"].update(pixel=pixel, expected=[round(c, 2) for c in srgb])
            checks["f12_renders_through_the_set_up_engine"] = pixel is not None and all(
                abs(a - b) < 0.03 for a, b in zip(pixel, srgb))
            press(*card_hit("close"))
            state["phase"] = "closed"
            return .6
        if phase == "closed":
            if not wait(phase, lambda: not card_host.is_active("engine_status"), limit=6):
                return .2
            checks["close_dismisses_the_card"] = True
            press(*engine_point())
            state["phase"] = "listed"
            return .6
        if phase == "listed":
            if not wait(phase, lambda: flow_menu.is_open(window(), flow_menu.ENGINES) and flow_menu._open.get("layout")):
                return .2
            values = [row["value"] for row in flow_menu._open["rows"] if row["kind"] == "row"]
            checks["once_on_it_is_an_engine_row_not_a_set_up"] = ("engine:" + IDNAME in values
                                                                 and "setup:" + MODULE not in values)
            capture("engine-setup-5-dropdown-after.png")
            flow_menu.close("suite")
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


import qa_input  # noqa: E402  (synthetic clicks; after_clicks)
# Never a phase while a click it sent is still on its way (qa_input.after_clicks).
bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)
