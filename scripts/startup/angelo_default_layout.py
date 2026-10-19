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
to load they draw nothing, so the splash shows its art alone; ``angelo_agent.splash_setup`` takes the menus over
when the add-on registers. When Angelo's startup will not run (``ANGELO_DISABLE_DEFAULT_CHAT``, or the scheduled
start ended without loading the add-on) they draw Blender's own menus.
"""

import bpy
from bpy.app.handlers import persistent
from bpy.types import Menu

_timer = None
_booted = False
_startup = True  # Until Angelo's startup card has been decided; a file load that interrupts it decides again.
_splash_draws = 0  # Times a stand-in drew the startup splash's art alone; splash_setup takes the count over.
_stand_ins_offered = False  # Once per session: only the startup splash needs them.


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

    def open_default():
        global _timer, _booted, _startup
        if opted_out():
            _timer = None
            return None
        windows = [w for w in bpy.context.window_manager.windows if w.screen and not w.screen.is_temporary]
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
            if any(a.type == 'VIEW_3D' for a in window.screen.areas):
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
        if any(console.is_chat(a) for w in windows for a in w.screen.areas):
            _timer = None
            return None
        if ui._layout_timer is not None:
            return .2
        if not any(a.type == 'VIEW_3D' for a in window.screen.areas):
            _timer = None  # Don't replace a saved non-modeling editor layout.
            return None
        try:
            ui.request_chat_dock(window, "RIGHT")
        except RuntimeError as error:
            print("Angelo default Coding Agent:", str(error))
        _timer = None
        return None

    _timer = open_default
    # Short: until it runs, the stock layout (and Blender's splash, its art alone) shows. It retries while windows
    # are not ready.
    bpy.app.timers.register(_timer, first_interval=0.1)


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


def _draw_stand_in(menu, context, idname):
    global _splash_draws
    if startup_pending():
        _splash_draws += 1
        return   # The splash's art alone: Angelo's setup card or start panel follows.
    stock = _stock(idname)
    if stock is not None:
        stock.draw(menu, context)   # Angelo's startup will not run: Blender's own menu.


class ANGELO_MT_startup_quick_setup(Menu):
    bl_idname = "WM_MT_splash_quick_setup"
    bl_label = "Quick Setup"

    def draw(self, context):
        _draw_stand_in(self, context, self.bl_idname)


class ANGELO_MT_startup_splash(Menu):
    bl_idname = "WM_MT_splash"
    bl_label = "Splash"

    def draw(self, context):
        _draw_stand_in(self, context, self.bl_idname)


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
    _withdraw_stand_ins()
