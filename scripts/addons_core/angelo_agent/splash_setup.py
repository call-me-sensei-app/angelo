# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's startup sequence: Blender's splash art once, then Angelo's own card, then the welcome screen.

Blender opens its splash at startup (art and version over a menu). Angelo keeps the art and empties the menu
while its own startup is pending, so no Blender form shows: before this add-on loads the startup module's
stand-ins do it (``angelo_default_layout``, registered before Blender builds the splash), and ``register`` takes the
menus over from them. Director's first workspace switch closes the
splash (Blender frees every popup when a workspace is set; ``close_popups`` does the same where no switch
happens) and it is never opened again. Once the layout has settled (``intro.schedule_first_launch``):

* a **first launch** (no saved preferences) shows the setup card (``setup_card``): language, look and
  "Use my Blender settings", the same effects as Blender's Quick Setup; Continue saves the preferences (or
  imports the earlier settings ``settings_import`` found: an earlier Angelo's or stock Blender's) and the
  welcome screen follows, once;
* a **returning user** whose splash would show gets the start panel (``start_card``) instead of Blender's
  New File page: recent scenes, New scene, Open… and What's new.

Blender's splash opened later (Help > Splash Screen, the app menu) is replaced the same way: its menus schedule
Angelo's card and close the splash (``_redirect``), so Blender's templates, Recover Last Session and Donate page
never shows while Angelo runs.

Under Blender's own startup rule (``first_run.splash_on_startup``). ``sequence`` records what opened, in order,
for window fixtures: ``splash`` (Blender drew its startup splash), ``setup``, ``start`` and ``welcome``.
"""

import os
from pathlib import Path

import bpy
from bpy.app.handlers import persistent
from bpy.types import Menu

from . import card_host, first_run, live_target, live_windows

sequence = []   # What the startup showed, in order; window fixtures read it.
drawn = 0       # Times Blender drew its splash menu while Angelo's startup was pending.
reopened = 0    # Kept for fixtures: Angelo never reopens Blender's splash.
_settled = False   # Until Angelo's startup decided what follows Blender's splash, the splash shows its art only.
_setup_lost = False   # The setup card closed unanswered (a file load or a layout change took its view).
_effects = None    # setup_card.Effects override for fixtures.


def take_setup_lost():
    """Whether the setup card closed unanswered since the last call (``intro.file_loaded`` shows it again)."""
    global _setup_lost
    lost, _setup_lost = _setup_lost, False
    return lost


def preferences_saved():
    return os.path.isfile(os.path.join(bpy.utils.user_resource('CONFIG'), "userpref.blend"))


def shows_on_startup():
    """Whether Blender itself opened the splash when this session started."""
    return first_run.splash_on_startup(
        bpy.context.preferences.view.show_splash, bool(bpy.data.filepath), preferences_saved())


def close_popups(window):
    """Close the window's popups, the splash included.

    Blender frees popup handlers whenever a workspace is set, and setting the
    active one again changes nothing else (``ED_workspace_change`` returns early).
    """
    window.workspace = window.workspace


def startup_settled():
    """Angelo's startup has decided what follows the splash; Blender's own menus draw again from now on."""
    global _settled
    _settled = True


def pending():
    """Angelo's own startup card will follow Blender's splash (read when the splash draws)."""
    import sys
    from . import product_env
    layout = sys.modules.get("angelo_default_layout")
    return (not _settled and bool(layout and layout.supported())
            and not (product_env.flag("DISABLE_INTRO") or product_env.flag("DISABLE_DEFAULT_CHAT")))



def after_close(window, function):
    """Run ``function`` in ``window`` on the next main-loop turn, once the card's modal operator has ended (nothing when
    the window is gone: callers pass the window read live, ``live_target``)."""
    if window is None:
        return
    pointer = window.as_pointer()

    def run(target):
        if target is not None:
            with bpy.context.temp_override(window=target):
                function()
        return None

    bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, run), first_interval=0.05)


def _welcome(window):
    from . import intro
    intro.open_after_setup(window)


def open_setup(window, welcome):
    """The first-launch setup card; ``welcome``: the welcome screen follows it."""
    from . import setup_card
    effects, held = _effects or setup_card.Effects(), live_target.window_ref(window)   # The card closes later.

    def finished(outcome):
        global _setup_lost
        if outcome == "lost":
            _setup_lost = True   # Nothing was chosen or saved: the setup is still owed.
            return
        if outcome == "import":
            # The import reads the copied preferences (an earlier Angelo's import also reloads the start-up file),
            # keeps the language and look chosen on the card, saves, and opens the welcome (settings_import_ui).
            def run():
                from . import settings_import_ui
                settings_import_ui.after_setup(card.source, card.keymap_choices, welcome)
            after_close(held(), run)
            return
        if outcome == "save":
            bpy.ops.wm.save_userpref()
        if welcome and outcome in {"save", "skip"}:
            after_close(held(), lambda: _welcome(bpy.context.window))

    card = setup_card.SetupCard(effects, finished)
    return _show(card, window, "setup")


def open_start(window):
    """The returning user's start panel."""
    from . import start_card
    scenes = start_card.recent_scenes(os.path.join(bpy.utils.user_resource('CONFIG'), "recent-files.txt"))
    from . import firstlaunch_controls, welcome_screen
    welcome_screen.prepare_banner()
    for path in scenes:
        firstlaunch_controls.thumbnail(path)
    held = live_target.window_ref(window)   # The card answers later: its window read live then.

    def act(action, path):
        window = held()
        if action == "scene":
            after_close(window, lambda: bpy.ops.wm.open_mainfile(filepath=path))
        elif action == "open":
            after_close(window, lambda: bpy.ops.wm.open_mainfile('INVOKE_DEFAULT'))
        elif action == "whats_new":
            after_close(window, lambda: bpy.ops.angelo.whats_new())

    card = start_card.StartCard(scenes, act, scene_chat_titles(), start_card.modified_times(scenes))
    return _show(card, window, "start")


SCENE_CHATS = "scene-chats.json"   # {scene path: the title of the chat active when it was last saved}
SCENE_CHATS_KEPT = 50


def _scene_chats_path():
    from .ui import state_directory
    return Path(state_directory()) / SCENE_CHATS


def scene_chat_titles():
    """Each saved scene's last chat title (local, beside the conversations index), for the start panel."""
    from .studio_state import read_json
    value = read_json(_scene_chats_path(), {})
    if not isinstance(value, dict):
        return {}
    return {path: title for path, title in value.items() if isinstance(path, str) and isinstance(title, str)}


@persistent
def _remember_scene_chat(*_args):
    """On save: the scene's path and the active chat's title, so "Continue where you left off" can name it."""
    from . import sessions, ui
    from .studio_state import atomic_json
    collection, path = ui._sessions, bpy.data.filepath
    record = collection.records.get(collection.active) if collection is not None else None
    if not path or not record or record.get("title") in {None, "", sessions.UNTITLED}:
        return
    titles = scene_chat_titles()
    titles.pop(path, None)
    titles[path] = record["title"]
    try:
        atomic_json(_scene_chats_path(), dict(list(titles.items())[-SCENE_CHATS_KEPT:]))
    except OSError:
        pass   # The tile then shows its folder; nothing else depends on it.


def _show(card, window, name):
    from . import welcome_screen
    welcome_screen.prepare_banner()
    card_host.request(card)
    if card_host.pump(lambda: True) and card_host.is_active(card.kind):
        sequence.append(name)
        return True
    return False


class ANGELO_MT_splash_quick_setup(Menu):
    bl_idname = "WM_MT_splash_quick_setup"
    bl_label = "Quick Setup"

    def draw(self, context):
        if pending():
            _note_drawn()
            return   # Angelo's setup card follows: the splash shows its art alone until Director closes it.
        _redirect(context, "setup")


class ANGELO_MT_splash(Menu):
    bl_idname = "WM_MT_splash"
    bl_label = "Splash"

    def draw(self, context):
        if pending():
            _note_drawn()
            return   # Angelo's start panel follows.
        _redirect(context, "start")


def _note_drawn(count=1):
    global drawn
    drawn += count
    if "splash" not in sequence:
        sequence.append("splash")


_redirecting = set()   # Windows whose splash is being replaced by Angelo's card.


def _redirect(context, kind):
    """Blender's splash opened after startup (Help > Splash Screen, the app menu): close it and show Angelo's card
    instead (the start panel, or the setup card while no preferences are saved). Drawing only schedules this.

    Automation that turned Angelo's startup off keeps the art alone, so no card covers a fixture.
    """
    from . import product_env
    window = getattr(context, "window", None)
    if window is None or product_env.flag("DISABLE_INTRO") or product_env.flag("DISABLE_DEFAULT_CHAT"):
        return
    pointer = window.as_pointer()
    if pointer in _redirecting:
        return
    _redirecting.add(pointer)

    def run(target):
        _redirecting.discard(pointer)
        if target is not None:
            with bpy.context.temp_override(window=target):
                close_popups(target)
                if kind == "setup":
                    open_setup(target, False)
                else:
                    open_start(target)
        return None

    bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, run), first_interval=0)


OVERRIDES = (ANGELO_MT_splash_quick_setup, ANGELO_MT_splash)


def _stock(cls):
    from bl_operators import wm
    return getattr(wm, cls.bl_idname)


def register():
    import sys
    bpy.app.handlers.save_post.append(_remember_scene_chat)
    for cls in OVERRIDES:
        current = getattr(bpy.types, cls.bl_idname, None)   # Blender's own menu or the startup stand-in.
        if current is not None and current is not cls and getattr(current, "is_registered", False):
            bpy.utils.unregister_class(current)
        bpy.utils.register_class(cls)
    layout = sys.modules.get("angelo_default_layout")
    count = layout.take_splash_draws() if layout is not None and hasattr(layout, "take_splash_draws") else 0
    if count:
        _note_drawn(count)   # The stand-ins drew Blender's startup splash before this add-on loaded.


def unregister():
    if _remember_scene_chat in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(_remember_scene_chat)
    for cls in OVERRIDES:
        bpy.utils.unregister_class(cls)
        if not _stock(cls).is_registered:
            bpy.utils.register_class(_stock(cls))  # Blender's own menu again.
