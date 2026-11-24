# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live web preview: the scene re-exported after every change and shown in the person's browser. Main thread.

``start`` exports the scene of the window it is started from, starts the loopback server (``web_preview_server``)
and opens the page in the person's own browser. A depsgraph handler notes each change, from the person or the
agent alike; a timer re-exports once the scene has been still for the debounce delay, never during a modal
interaction (a drag, a brush stroke), animation playback or a background job. The delay grows with the last
export's duration, so the preview never takes more than about a quarter of the main thread however large the scene.
Nothing runs while the preview is off. It stops on ``stop``, when the add-on unloads and when Blender quits.

The export carries the scene's look (``web_look``): the page applies its tone mapping and exposure and lights the
scene with its World. The World's picture is made off the main thread (``web_environment``); an export never waits
for it, and the timer exports again once it lands. Until then the export carries the World the page shows (the last
picture or colour this preview published, ``shown``), so a page opened or reloaded during the bake lights the scene
as the open page does. The page's Compare button asks for Angelo's viewport from the page's camera, which the timer
draws (``web_compare``).
"""

import os
import threading
import time

import bpy

from . import edit_tool_hold, owned_paths, private_roots
from . import live_windows, scene_changes, web_compat, web_environment, web_export, web_facts, web_world_key
from .web_shader_spec import ANGELO_DECLARATIONS
from .ui_text import iface_, is_rtl, n_

POLL_SECONDS = 0.25
QUIET_SECONDS = 0.6        # Stillness before a re-export.
MAX_QUIET_SECONDS = 10.0
SHARE = 3.0                # Wait at least this many times the last export's duration.
MAX_FOCUS = 256            # Selected names sent to the page for framing; more say nothing more about where to look.

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
    # The look (web_preview/look.js) and the Compare view (web_preview/compare.js).
    "tone": n_("Tone mapping"), "exposure": n_("Exposure"), "world": n_("World"),
    "world_color": n_("Colour"), "world_hdri": n_("HDRI {size}"), "world_baked": n_("Baked {size}"),
    "world_pending": n_("Making the World's picture…"), "world_none": n_("None"),
    "light_world": n_("World light"), "light_studio": n_("Studio light"), "background": n_("Background"),
    "compare": n_("Compare with Angelo"), "compare_done": n_("Done"), "wipe": n_("Wipe"),
    "difference": n_("Difference"), "capturing": n_("Drawing Angelo's viewport…"),
    "angelo_side": n_("Angelo viewport"), "web_side": n_("Three.js"),
    "average_difference": n_("Average difference {value}%"),
    "compare_failed": n_("Angelo couldn't draw its viewport: {reason}"),
    # Framing (web_preview/app.js): what the page looks at.
    "frame_camera": n_("Camera"), "frame_selection": n_("Selection"), "frame_all": n_("All"),
    "frame_follow": n_("Follow Angelo's selection"),
    "frame_missing": n_("The selection isn't in the preview (hidden, merged or not exported)"),
}

# Who holds the exporter (2.0.1): "person" (the live preview the person or their agent started: the File menu, Stop
# and the browser follow only it) and "pages" (apps' live session pages, ``session_page``). It runs while anyone holds
# it; a holder's release never ends another's.
PERSON, PAGES = "person", "pages"
_holders = set()

_state = {"server": None, "folder": None, "window": None, "dirty_at": None, "queued_at": None, "exporting": False,
          "last_ms": 0.0, "request": None, "scene": None, "strings": {}, "exports": 0, "materials": [],
          "page": None, "shader_signature": (), "pictures": 0, "picture_pending": False, "shown": {}}


def person_server():
    """The person's own preview server while their preview is on, else None: what the File menu, Stop, the agent's
    preview tool and its WEB view read. Apps' pages holding the exporter never make it the person's."""
    server = _state["server"]
    return server if PERSON in _holders and server is not None and not getattr(server, "closed", False) else None


def running():
    """Whether the person's live preview is on (what the File menu, Stop and the agent's preview tool follow)."""
    return person_server() is not None


def exporting():
    """Whether the exporter runs for anyone (the person's preview or apps' live session pages)."""
    return _state["server"] is not None


def url():
    server = person_server()
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


def scene_key(scene, view_layer):
    """What the export reads that an update of the scene alone can change: the frame, camera, World and which
    objects show (``scene_changes.scene_key``), and the scene's own settings the export reads, its look among them
    (``web_facts.scene_settings``)."""
    return scene_changes.scene_key(scene, view_layer) + (web_facts.scene_settings(scene),)


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    if _state["server"] is None or _state["exporting"] or threading.current_thread() is not threading.main_thread():
        return
    window = _window()
    if window is None or scene.session_uid != window.scene.session_uid:
        return
    changed, _state["scene"] = scene_changes.matters(depsgraph, scene, window.view_layer, _state["scene"],
                                                     key=scene_key)
    if changed:
        _dirty(time.monotonic())


def _on_edit(scene):
    """An edit the executor reports (``scene_changes.edited``), which no depsgraph update may show."""
    window = _window()
    if _state["server"] is None or _state["exporting"] or window is None \
            or scene.session_uid != window.scene.session_uid:
        return
    _dirty(time.monotonic())


def _dirty(at):
    """An unsent change at ``at``: ``dirty_at`` keeps the newest (the quiet wait before an export), ``queued_at`` the
    oldest, as the engine link keeps it, so an update queued before the person's operation still counts as waiting
    once their drag changes the scene too (break-it review 8)."""
    _state["dirty_at"] = at
    if _state["queued_at"] is None:
        _state["queued_at"] = at


@edit_tool_hold.guarded(waiting=lambda: edit_tool_hold.before_hold(_state["queued_at"]))   # Queued before it.
def _tick():
    if _state["server"] is None:
        return None
    if _state["picture_pending"] and web_environment.ready_since(_state["pictures"]):
        _state["picture_pending"] = False
        _dirty(_state["dirty_at"] or 0.0)   # The World's picture landed: export it now.
    _compare()
    _focus()
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


def focus_of(view_layer, scene, limit=MAX_FOCUS):
    """What the person looks at in Angelo, by object name: the selection (at most ``limit`` names, the active
    object first), the active object and the scene camera. Names only: the page finds them in the exported scene."""
    active = view_layer.objects.active
    selected = [active.name] if active is not None and active.select_get(view_layer=view_layer) else []
    for obj in view_layer.objects.selected:
        if len(selected) >= limit:
            break
        if obj is not active:
            selected.append(obj.name)
    camera = scene.camera
    return {"selected": selected, "active": active.name if active is not None else None,
            "camera": camera.name if camera is not None else None}


def _focus():
    """Tell the page what Angelo shows (the server ignores an unchanged focus), so it can frame it."""
    window = _window()
    if window is None or window.screen is None:
        return
    _state["server"].publish_focus(focus_of(window.view_layer, window.scene))


def _compare():
    """Draw Angelo's viewport from the page's camera when the person asked to compare (``web_compare``)."""
    request = _state["server"].take_compare()
    if request is None:
        return
    window = _window()
    from . import web_compare
    try:
        if window is None or window.screen is None:
            raise RuntimeError("no Angelo window is open")
        png = web_compare.capture(window, request)
        _state["server"].publish_viewport(png, {"ok": True, "request": request.get("id")})
    except Exception as error:  # Said on the page; the preview's timer must keep running whatever the draw raised.
        reason = str(error)[:200] or type(error).__name__
        _state["server"].publish_viewport(None, {"ok": False, "request": request.get("id"), "error": reason})


def _rendered(report):
    page = dict(report)
    page["findings"] = [web_compat.render(item, _translate) for item in report.get("findings", ())]
    page["strings"] = _state["strings"]
    page["lang"] = _language()
    return page


def refresh(window):
    """Export the window's scene now and publish it to the page; returns the report (English findings)."""
    server = _state["server"]
    _state["dirty_at"] = _state["queued_at"] = None
    path = os.path.join(_state["folder"], "scene.glb")
    _state["exporting"] = True
    started = time.perf_counter()
    _state["pictures"] = web_environment.landed()
    try:
        report = web_export.export(window, window.scene, window.view_layer, path, _state["request"],
                                   require_environment=False, shown=_state["shown"])
    except Exception as error:  # The page keeps the last good export and says this one failed.
        server.publish(None, {"status": "failed", "error": type(error).__name__, "strings": _state["strings"],
                              "updated": time.time()})
        return {"status": "failed", "error": str(error)[:300]}
    finally:
        _state["exporting"] = False
        _state["last_ms"] = (time.perf_counter() - started) * 1000.0
        _state["scene"] = scene_key(window.scene, window.view_layer)
    _state["picture_pending"] = report["settings"].get("environment", {}).get("status") == "pending"
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


def _open(window, request):
    """The exporter's server, handlers and timer, started once for whoever holds it first."""
    from .web_preview_server import PreviewServer
    if _state["server"] is None:
        _state["folder"] = private_roots.mkdtemp("angelo-web-preview-")
        from .team_page_art import WORDMARK
        try:
            _state["server"] = PreviewServer(extra_files=_fonts(), marks={"<!--WORDMARK-->": WORDMARK})
        except BaseException:   # Not serving (its port or thread could not open): no folder left behind either.
            import shutil
            if owned_paths.may_write(_state["folder"]):   # A cleanup: only what C says Angelo may remove.
                shutil.rmtree(_state["folder"], ignore_errors=True)
            _state["folder"] = None
            raise
        _state["strings"] = _strings()
        scene_changes.watch(_on_depsgraph)
        scene_changes.on_edit(_on_edit)
        if not bpy.app.timers.is_registered(_tick):
            bpy.app.timers.register(_tick, first_interval=POLL_SECONDS, persistent=True)
    if _state["window"] is None or PERSON not in _holders:   # The person's start follows its own window.
        _state["window"] = window.as_pointer()
        _state["request"] = dict(request)


def start(window, request, open_browser=True):
    """Start (or refresh) the person's preview of ``window``'s scene; returns {"url", "report"}. Their browser opens
    when their preview starts, whether or not apps' pages already keep the exporter running."""
    opened = person_server() is not None
    _open(window, request)
    if _state["server"].closed:   # The person stopped it while pages held the exporter: a new tab, a new token.
        _state["server"].reopen()
    _holders.add(PERSON)
    _state["window"] = window.as_pointer()
    _state["request"] = dict(request)
    report = refresh(window)
    if open_browser and not opened:
        open_page(_state["server"].url)
    return {"url": _state["server"].url, "report": report}


def hold(holder, window, request):
    """``holder`` (``PAGES``) keeps the exporter running, never the person's preview: no export now, only the scene
    marked changed, so the timer exports it under its own gates and budget (never in the caller's tick)."""
    if holder == PERSON:
        raise ValueError("The person's preview starts with start()")
    first = _state["server"] is None
    _open(window, request)
    _holders.add(holder)
    if first:
        _dirty(time.monotonic() - MAX_QUIET_SECONDS)   # Exported at the timer's next turn its gates allow.


def release(holder):
    """``holder`` lets go; the exporter stops once nobody holds it. True when it held."""
    held = holder in _holders
    _holders.discard(holder)
    if not _holders:
        _shutdown()
    return held


def stop():
    """The person's Stop: their preview ends, their tab with it (its path answers 404); the exporter goes on while
    apps' pages hold it. True when theirs ran."""
    held = release(PERSON)
    if held and _state["server"] is not None:   # Pages still hold: only the person's own site goes.
        _state["server"].withdraw()
    return held


def _shutdown():
    """Server, handler, timer and the export folder: nobody holds the exporter."""
    _holders.clear()
    server, _state["server"] = _state["server"], None
    scene_changes.unwatch(_on_depsgraph)
    scene_changes.off_edit(_on_edit)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if server is None:
        return False
    server.close()
    web_environment.stop()
    folder, _state["folder"] = _state["folder"], None
    if folder:
        from . import owned_paths
        owned_paths.remove_tree(folder)   # Only the folder Angelo made (owned_paths).
    _state.update(window=None, dirty_at=None, queued_at=None, request=None, scene=None, materials=[], page=None,
                  shader_signature=(), picture_pending=False, shown={})
    return True


def _fonts():
    """Blender's bundled Inter, served as the page's text face when present (no web font download)."""
    folder = bpy.utils.system_resource("DATAFILES", path="fonts") or ""
    path = os.path.join(folder, "Inter.woff2")
    return {"fonts/Inter.woff2": path} if os.path.isfile(path) else {}


@bpy.app.handlers.persistent
def _exit_pre(*_args):
    _shutdown()


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
    _shutdown()
    web_environment.stop()   # An explicit export's pictures too: no bake outlives the add-on.
    web_world_key.stop()     # Its depsgraph handler, if a World it can't read was keyed.
    if _exit_pre in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.remove(_exit_pre)
    atexit.unregister(_atexit)
