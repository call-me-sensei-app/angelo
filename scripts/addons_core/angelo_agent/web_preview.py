# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live web preview: the scene re-exported after every change and shown in the person's browser. Main thread.

``start`` exports the scene of the window it is started from, starts the loopback server (``web_preview_server``)
and opens the page in the person's own browser. A depsgraph handler notes each change, from the person or the
agent alike; a timer re-exports once the scene has been still for the debounce delay, never during a modal
interaction (a drag, a brush stroke), animation playback or a background job. The delay grows with the last
export's duration, so the preview never takes more than about a quarter of the main thread however large the scene.
Nothing runs while the preview is off. It stops on ``stop``, when the add-on unloads and when Blender quits.
"""

import os
import threading
import time

import bpy

from . import private_roots
from . import live_windows, scene_changes, web_compat, web_export
from .web_shader_spec import ANGELO_DECLARATIONS
from .ui_text import iface_, is_rtl, n_

POLL_SECONDS = 0.25
QUIET_SECONDS = 0.6        # Stillness before a re-export.
MAX_QUIET_SECONDS = 10.0
SHARE = 3.0                # Wait at least this many times the last export's duration.

PAGE_TEXT = {   # Key -> English; translated when the preview starts (``_strings``).
    "title": n_("Web Preview"),
    "live": n_("Live"), "updating": n_("Updating…"), "offline": n_("Angelo closed the preview"),
    "failed": n_("The last export failed; showing the one before it"),
    "budget": n_("Size budget"), "download": n_("Download"), "triangles": n_("Triangles"),
    "draw_calls": n_("Draw calls"), "textures": n_("Textures"), "merged": n_("Merged objects"),
    "carry": n_("What doesn't carry over"), "none": n_("Nothing found that won't carry over"),
    "fix": n_("Fix"), "settings": n_("Export settings"), "meshes": n_("Meshes"), "images": n_("Images"),
    "disclaimer": n_("This is Three.js in your browser: lighting, shadows and reflections differ from Angelo's "
                     "render. The list says what doesn't carry over."),
    "empty": n_("The scene has nothing the web can show yet"),
    "updated": n_("Updated {time}"),
    # The column's controls (web_preview/panel.js): fold it to its title, or hide it whole.
    "fold": n_("Hide details"), "unfold": n_("Show details"),
    "hide": n_("Hide the report"), "show": n_("Show the report"),
}

_state = {"server": None, "folder": None, "window": None, "dirty_at": None, "exporting": False,
          "last_ms": 0.0, "request": None, "scene": None, "strings": {}, "exports": 0, "materials": [],
          "page": None, "shader_signature": ()}


def running():
    return _state["server"] is not None


def url():
    server = _state["server"]
    return server.url if server is not None else None


def _translate(message):
    # Arabic catalogues hold the visual (left-to-right) form Blender draws; a browser lays text out itself.
    return message if is_rtl() else iface_(message)


def _language():
    from .ui_text import language
    return (language() or "en").replace("_", "-")


def _strings():
    return {key: _translate(value) for key, value in PAGE_TEXT.items()}


def _window():
    """The window the preview follows: the one it started from while it is open, else the first main window."""
    wm = bpy.context.window_manager
    # The start window even between screens (``scene_changes.busy`` reads its tools without one), never a
    # substitute for it.
    return live_windows.find(wm, _state["window"], live=False) or live_windows.first(wm, temporary=False)


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    if _state["server"] is None or _state["exporting"] or threading.current_thread() is not threading.main_thread():
        return
    window = _window()
    if window is None or scene.session_uid != window.scene.session_uid:
        return
    changed, _state["scene"] = scene_changes.matters(depsgraph, scene, window.view_layer, _state["scene"])
    if changed:
        _state["dirty_at"] = time.monotonic()


def _tick():
    if _state["server"] is None:
        return None
    dirty_at = _state["dirty_at"]
    quiet = scene_changes.quiet_seconds(_state["last_ms"], QUIET_SECONDS, SHARE, MAX_QUIET_SECONDS)
    if dirty_at is not None and time.monotonic() - dirty_at >= quiet:
        window = _window()
        if window is not None and window.screen is None:
            return live_windows.RETRY_S   # Between screens: the export waits for it (the scene stays dirty).
        if window is not None and not scene_changes.busy(window):
            refresh(window)
            return POLL_SECONDS
    _shaders_changed()
    return POLL_SECONDS


def _rendered(report):
    page = dict(report)
    page["findings"] = [web_compat.render(item, _translate) for item in report.get("findings", ())]
    page["strings"] = _state["strings"]
    page["lang"] = _language()
    return page


def refresh(window):
    """Export the window's scene now and publish it to the page; returns the report (English findings)."""
    server = _state["server"]
    _state["dirty_at"] = None
    path = os.path.join(_state["folder"], "scene.glb")
    _state["exporting"] = True
    started = time.perf_counter()
    try:
        report = web_export.export(window, window.scene, window.view_layer, path, _state["request"])
    except Exception as error:  # The page keeps the last good export and says this one failed.
        server.publish(None, {"status": "failed", "error": type(error).__name__, "strings": _state["strings"],
                              "updated": time.time()})
        return {"status": "failed", "error": str(error)[:300]}
    finally:
        _state["exporting"] = False
        _state["last_ms"] = (time.perf_counter() - started) * 1000.0
        _state["scene"] = scene_changes.scene_key(window.scene, window.view_layer)
    with open(path, "rb") as handle:
        body = handle.read()
    _state["exports"] += 1
    _state["materials"] = _exported_materials(window)
    page = dict(_rendered(report), status="ok", updated=time.time(), scene_version=_state["exports"])
    _state["page"] = page
    server.publish(body, dict(page, **_shader_state()))
    return report


def _exported_materials(window):
    """Names of the materials on what the export includes (the web shaders the page may swap in)."""
    from .web_facts import exported_objects
    names = set()
    for obj in exported_objects(window.scene, window.view_layer, _state["request"]["selection_only"]):
        names.update(slot.material.name for slot in obj.material_slots if slot.material is not None)
    return sorted(names)


def _shader_state():
    """The page's web shaders now: each material's extras and the files' text, and what a reload watches."""
    from . import web_shader
    materials = [bpy.data.materials[name] for name in _state["materials"] if name in bpy.data.materials]
    _state["shader_signature"] = web_shader.signature(materials)
    shaded = {material.name: web_shader.extras(material)["angelo_web_shader"] for material in materials
              if web_shader.settings_of(material) is not None}
    return {"web_shaders": shaded, "shader_sources": web_shader.sources(materials),
            "shader_header": ANGELO_DECLARATIONS}


def _shaders_changed():
    """A uniform edit or a shader file save: the page swaps the shaders at once, without a new export."""
    from . import web_shader
    materials = [bpy.data.materials[name] for name in _state["materials"] if name in bpy.data.materials]
    if not materials or _state["page"] is None or web_shader.signature(materials) == _state["shader_signature"]:
        return
    _state["server"].publish(None, dict(_state["page"], updated=time.time(), **_shader_state()))


def open_page(address):
    """Open ``address`` in the person's default browser (a scripted check replaces this to stay unfocused)."""
    bpy.ops.wm.url_open(url=address)


def start(window, request, open_browser=True):
    """Start (or refresh) the preview of ``window``'s scene; returns {"url", "report"}."""
    from .web_preview_server import PreviewServer
    if _state["server"] is None:
        _state["folder"] = private_roots.mkdtemp("angelo-web-preview-")
        from .team_page_art import WORDMARK
        _state["server"] = PreviewServer(extra_files=_fonts(), marks={"<!--WORDMARK-->": WORDMARK})
        _state["strings"] = _strings()
        if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
        if not bpy.app.timers.is_registered(_tick):
            bpy.app.timers.register(_tick, first_interval=POLL_SECONDS, persistent=True)
        opened = False
    else:
        opened = True
    _state["window"] = window.as_pointer()
    _state["request"] = dict(request)
    report = refresh(window)
    if open_browser and not opened:
        open_page(_state["server"].url)
    return {"url": _state["server"].url, "report": report}


def stop():
    """Stop the preview: server, handler, timer and its export folder. True when one was running."""
    server, _state["server"] = _state["server"], None
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if server is None:
        return False
    server.close()
    folder, _state["folder"] = _state["folder"], None
    if folder:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
    _state.update(window=None, dirty_at=None, request=None, scene=None, materials=[], page=None, shader_signature=())
    return True


def _fonts():
    """Blender's bundled Inter, served as the page's text face when present (no web font download)."""
    folder = bpy.utils.system_resource("DATAFILES", path="fonts") or ""
    path = os.path.join(folder, "Inter.woff2")
    return {"fonts/Inter.woff2": path} if os.path.isfile(path) else {}


@bpy.app.handlers.persistent
def _exit_pre(*_args):
    stop()


def _atexit():
    server = _state["server"]
    if server is not None:   # Sockets and threads only: Blender's data is already freed at atexit.
        server.close()


def register():
    import atexit
    if _exit_pre not in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.append(_exit_pre)
    atexit.register(_atexit)


def unregister():
    import atexit
    stop()
    if _exit_pre in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.remove(_exit_pre)
    atexit.unregister(_atexit)
