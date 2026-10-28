# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo GUI startup: Director layout and Coding Agent by default.

Director uses a duplicate workspace with a decluttered, fully navigable viewport and chat. Manual
layouts, accounts and scene data are preserved; startup performs no inference.
Stock Blender and background jobs skip this.
ANGELO_DISABLE_DEFAULT_CHAT=1 (or a deprecated spelling, see product_env) is the
explicit acceptance-test/automation opt-out.

Blender's startup splash: Blender builds it before its main loop, while ``angelo_agent`` is enabled only later
(``open_default``), so the splash would draw Blender's own Quick Setup form until Director closes it. ``load_post``
runs after every startup script and before the splash opens (``WM_init``, then ``WM_init_splash_on_startup``), so
the first call registers stand-ins for Blender's two splash menus (``STAND_INS``). While Angelo's add-on is about
to load they count the splash as drawn; ``angelo_agent.splash_setup`` takes the menus over when the add-on registers.
They never draw Blender's Quick Setup form or New File page, in any launch mode: when Angelo's startup will not run
(``ANGELO_DISABLE_DEFAULT_CHAT``, or a scheduled start that ended without loading the add-on) the splash keeps its
art alone too, as Blender's own classes in ``bl_operators.wm`` do in Angelo.

Angelo's cover: the startup file is Blender's own (the stock Layout workspace), and Angelo's layout is applied only once
the add-on has loaded (``open_default``) and Director has rebuilt the window over several timer steps, so the first
frames showed Blender's stock editors under the splash (1.0.5 screenshot review). From the first frame until that
settles (``covering``), the window's editor areas are painted with Angelo's empty-viewport colour and the top bar
leaves out the workspace tabs (``bl_ui.space_topbar``); the splash, a popup, draws above it. The paint is the
window manager's own (``WindowManager.angelo_startup_cover``, ``wm_angelo_cover.cc``): one rectangle drawn after
everything the areas draw, region overlays (the timeline's playhead), blended overlapping regions and action-zone
arrows included, which a region's POST_PIXEL handler draws beneath. A binary without it gets those handlers on
every region of every editor instead (``_cover_draw``). A file opened later is covered the
same way until Angelo's layout is back. Never when Angelo's startup will not run (``opted_out``, a failed start),
and never longer than ``COVER_LIMIT``.
"""

import sys
import time

import angelo_live_windows as live_windows
import bpy
from bpy.app.handlers import persistent
from bpy.types import Menu

_timer = None
_booted = False
_startup = True  # Until Angelo's startup card has been decided; a file load that interrupts it decides again.

_splash_draws = 0  # Times a stand-in drew the startup splash's art alone; splash_setup takes the count over.
_stand_ins_offered = False  # Once per session: only the startup splash needs them.
COVER_LIMIT = 20.0   # Seconds: a startup that has not settled by then shows whatever it has.
COVER_SPACES = ("SpaceView3D", "SpaceOutliner", "SpaceProperties", "SpaceDopeSheetEditor", "SpaceGraphEditor",
                "SpaceNLA", "SpaceImageEditor", "SpaceNodeEditor", "SpaceSequenceEditor", "SpaceClipEditor",
                "SpaceTextEditor", "SpaceConsole", "SpaceInfo", "SpaceFileBrowser", "SpacePreferences",
                "SpaceSpreadsheet")
COVER_REGIONS = ("WINDOW", "HEADER", "TOOL_HEADER", "TOOLS", "UI", "CHANNELS", "FOOTER", "NAVIGATION_BAR",
                 "ASSET_SHELF", "ASSET_SHELF_HEADER", "PREVIEW", "TOOL_PROPS", "EXECUTE")
_cover = {"since": None, "handlers": [], "opted_out": None}


def supported():
    return not bpy.app.background and "is_coding_agent" in bpy.types.SpaceConsole.bl_rna.properties


def opted_out():
    # Call from the timer or a draw only: the bundled add-on directory joins sys.path after
    # startup modules register. Importing the package runs no registration.
    from angelo_agent import product_env
    return product_env.flag("DISABLE_DEFAULT_CHAT")


def schedule():
    global _timer
    if not supported() or _timer is not None:
        return
    settle = live_windows.Wait()   # This run's own wait (bounded) while a window is between screens.

    def open_default():
        global _timer, _booted, _startup
        if opted_out():
            _timer = None
            return None
        if settle(not live_windows.settled(bpy.context.window_manager)):
            return .2   # A window between screens could be the main one or hold the chat: decide once settled.
        windows = live_windows.windows(bpy.context.window_manager, temporary=False)
        if not windows:
            return .2
        import addon_utils
        # Respect a user disabling the host after initial app startup.
        if _booted and not addon_utils.check("angelo_agent")[1]:
            _timer = None
            return None
        if not addon_utils.check("angelo_agent")[1]:
            addon_utils.enable("angelo_agent", default_set=False, persistent=False)
        from angelo_agent import ui, console, director
        _booted = True
        window = windows[0]
        from angelo_agent.input_policy import interaction_active
        if interaction_active(window):
            return .2
        if director._timer is not None:
            return .2
        from angelo_agent import studio_tab_build
        if studio_tab_build.startup_build(window):
            return .05   # Manual's tabs first: built before Director's chat exists, nothing sees the switches it needs.
        # Handed on: ``intro`` gives it back (``file_loaded``) when a file load interrupts the startup card.
        startup, _startup = _startup, False
        if director.preferred_mode() == "DIRECTOR":
            from angelo_agent import intro
            if director.is_director(window):
                from angelo_agent.director_layout import configure
                configure(window)
                intro.schedule_first_launch(startup=startup)
                _timer = None
                return None
            if any(a.type == 'VIEW_3D' for a in live_windows.areas(window)):
                try:
                    director.request_mode(window, "DIRECTOR", persist=False)
                except RuntimeError as error:
                    print("Angelo default Director:", str(error))
                # Setting a workspace closes popups, Blender's startup splash included. It is never reopened:
                # once Director has settled, Angelo's setup card (first launch) or start panel follows, then
                # the welcome, each once (angelo_agent.splash_setup).
                intro.schedule_first_launch(startup=startup)
                _timer = None
                return None
        if director.preferred_mode() == "MANUAL" and director.is_director(window):
            try:
                director.request_mode(window, "MANUAL", persist=False)
            except RuntimeError as error:
                print("Angelo remembered Manual mode:", str(error))
            from angelo_agent import intro
            intro.schedule_first_launch(startup=startup)
            _timer = None
            return None
        if startup:  # No Angelo card follows on these paths: Blender's own splash menus draw again.
            from angelo_agent import splash_setup
            splash_setup.startup_settled()
        if any(console.is_chat(a) for w in windows for a in live_windows.areas(w)):
            _timer = None
            return None
        if ui._layout_timer is not None:
            return .2
        if not any(a.type == 'VIEW_3D' for a in live_windows.areas(window)):
            _timer = None  # Don't replace a saved non-modeling editor layout.
            return None
        try:
            ui.request_chat_dock(window, "RIGHT")
        except RuntimeError as error:
            print("Angelo default Coding Agent:", str(error))
        _timer = None
        return None

    _timer = open_default
    # Short: until it runs and Angelo's layout settles, Angelo's cover hides the stock layout (Blender's splash shows
    # its art alone above it). It retries while windows are not ready.
    bpy.app.timers.register(_timer, first_interval=0.1)
    start_cover()


# -- Angelo's cover until its layout settles -------------------------------------------------------------------------
def covering():
    """Angelo's startup is still putting its layout in place: its timer, Director's rebuild, the chat dock or Manual's
    Media Studio and Generation Flows tabs (``studio_workspaces.startup_build_pending``)."""
    if _cover["since"] is None or time.monotonic() - _cover["since"] > COVER_LIMIT:
        return False
    if _cover["opted_out"] is None:
        try:
            _cover["opted_out"] = opted_out()
        except ImportError:   # The add-on's folder is not on sys.path yet: ask again at the next draw.
            return False
    if _cover["opted_out"]:
        return False
    if _timer is not None and bpy.app.timers.is_registered(_timer):
        return True
    director, ui = sys.modules.get("angelo_agent.director"), sys.modules.get("angelo_agent.ui")
    tabs = sys.modules.get("angelo_agent.studio_workspaces")   # Manual's tabs, built as part of the start-up.
    return bool((director and director._timer is not None) or (ui and ui._layout_timer is not None)
                or (tabs and tabs.startup_build_pending()))


def _native_cover():
    """The window manager's cover (``WindowManager.angelo_startup_cover``), or None in a binary without it."""
    wm = getattr(getattr(bpy, "context", None), "window_manager", None)
    return wm if wm is not None and hasattr(wm, "angelo_startup_cover") else None


def start_cover():
    """Paint the cover from the next frame on, until ``covering`` ends (``_cover_watch`` then removes it)."""
    _cover["since"] = time.monotonic()
    native = _native_cover()
    if native is not None:
        native.angelo_startup_cover = cover_color()
    elif not _cover["handlers"]:
        for name in COVER_SPACES:
            space = getattr(bpy.types, name, None)
            for region in COVER_REGIONS if space is not None else ():
                try:
                    _cover["handlers"].append((space, region, space.draw_handler_add(_cover_draw, (), region,
                                                                                     'POST_PIXEL')))
                except (TypeError, ValueError, AttributeError):   # A region type this editor does not have.
                    pass
    if not bpy.app.timers.is_registered(_cover_watch):
        bpy.app.timers.register(_cover_watch, first_interval=0.25, persistent=True)   # Outlives a file load.


def _remove_cover():
    native = _native_cover()
    if native is not None:
        native.angelo_startup_cover = (0.0, 0.0, 0.0, 0.0)
    for space, region, handle in _cover["handlers"]:
        try:
            space.draw_handler_remove(handle, region)
        except (ValueError, RuntimeError):
            pass
    _cover["handlers"] = []
    _cover["since"] = None


def _cover_watch():
    if covering():
        return 0.25
    _remove_cover()
    for _window, area in live_windows.window_areas(bpy.context.window_manager):   # Tabs back with their top bar.
        area.tag_redraw()
    return None


def cover_color():
    """An empty Angelo viewport: the 3D view's background in the current theme."""
    try:
        return tuple(bpy.context.preferences.themes[0].view_3d.space.gradients.high_gradient) + (1.0,)
    except (AttributeError, IndexError):
        return (0.157, 0.157, 0.157, 1.0)


def _cover_draw():
    if not covering():
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    region = bpy.context.region
    if region is None:
        return
    w, h = region.width, region.height
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": ((0, 0), (w, 0), (w, h), (0, 0), (w, h), (0, h))})
    gpu.state.blend_set('NONE')
    shader.uniform_float("color", cover_color())
    batch.draw(shader)


# -- Blender's startup splash until the add-on takes it over --------------------------------------------------------
def startup_pending():
    """Angelo's add-on is about to load, and its startup card will follow Blender's splash.

    False once the add-on has loaded (``angelo_agent.splash_setup`` draws the menus from then on), when automation
    opted out of Angelo's startup, or when the scheduled start ended without loading it (an error in
    ``open_default`` removes its timer).
    """
    if _booted or _timer is None or not bpy.app.timers.is_registered(_timer):
        return False
    try:
        return not opted_out()
    except ImportError:   # No bundled add-on: nothing will take the splash over.
        return False


def take_splash_draws():
    """The stand-ins' draw count, once: ``splash_setup`` records it as Blender's splash having shown."""
    global _splash_draws
    count, _splash_draws = _splash_draws, 0
    return count


def _stock(idname):
    from bl_operators import wm
    return getattr(wm, idname, None)


def _draw_stand_in():
    """The splash's art alone, always: Angelo's setup card or start panel follows, or (Angelo's startup will not run)
    nothing does. Never Blender's form."""
    global _splash_draws
    if startup_pending():
        _splash_draws += 1


class ANGELO_MT_startup_quick_setup(Menu):
    bl_idname = "WM_MT_splash_quick_setup"
    bl_label = "Quick Setup"

    def draw(self, _context):
        _draw_stand_in()


class ANGELO_MT_startup_splash(Menu):
    bl_idname = "WM_MT_splash"
    bl_label = "Splash"

    def draw(self, _context):
        _draw_stand_in()


STAND_INS = (ANGELO_MT_startup_quick_setup, ANGELO_MT_startup_splash)


def offer_stand_ins():
    """Put the stand-ins in place of Blender's own splash menus, once, before the startup splash opens.

    Only where Blender's own menu is registered: on a returning launch whose preferences list ``angelo_agent``,
    the add-on loaded before this runs and its menus are already in place.
    """
    global _stand_ins_offered
    if _stand_ins_offered:
        return
    _stand_ins_offered = True
    for cls in STAND_INS:
        stock = _stock(cls.bl_idname)
        if stock is not None and stock.is_registered and getattr(bpy.types, cls.bl_idname, None) is stock:
            bpy.utils.unregister_class(stock)
            bpy.utils.register_class(cls)


def _withdraw_stand_ins():
    for cls in STAND_INS:
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
            stock = _stock(cls.bl_idname)
            if stock is not None and not stock.is_registered:
                bpy.utils.register_class(stock)


@persistent
def load_post(_unused):
    global _timer, _startup
    # Non-persistent timers are discarded on file load by Blender itself (the startup file's read, before
    # ``register`` scheduled, keeps the one it scheduled).
    if _timer is not None and not bpy.app.timers.is_registered(_timer):
        _timer = None
    import sys
    intro = sys.modules.get("angelo_agent.intro")
    if intro is not None and intro.file_loaded():
        _startup = True   # The load dropped (or closed) Angelo's startup card before it was decided: decide again.
    offer_stand_ins()   # The first call is the startup file's, before Blender opens its splash.
    schedule()


def register():
    if supported():
        bpy.app.handlers.load_post.append(load_post)
        schedule()


def unregister():
    global _timer
    if _timer and bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    _timer = None
    if load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(load_post)
    if bpy.app.timers.is_registered(_cover_watch):
        bpy.app.timers.unregister(_cover_watch)
    _remove_cover()
    _withdraw_stand_ins()
