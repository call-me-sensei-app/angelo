# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A small stand-in for ``bpy``, ``blf``, ``gpu`` and ``bl_ui`` so the notices' Blender glue runs on the host.

``Notices`` patches ``sys.modules`` with the fakes for one test, imports the requested ``angelo_agent``
modules afresh, and puts everything back afterwards (including the package's own attributes, so other
host tests keep the modules they imported). It records what the code asked of Blender: registered
classes, timers, appended draw functions, message-bus publishes, operators and opened links. The real
behaviour in a window is proven by ``test_blender_update_crash``.
"""

import importlib
import sys
import types
from types import SimpleNamespace
from unittest import mock

import angelo_agent

MODULES = ("card_draw", "card_host", "update_restart", "update_ready", "update_ui", "crash_ui", "updates_prefs",
           "app_notices", "font_watch")
# Standard-library modules the notices use: imported before the patch, so they stay single copies afterwards.
PURE = ("app_http", "app_version", "card_kit", "crash_card", "crash_flow", "crash_log", "crash_report", "crash_state",
        "crash_upload", "input_policy", "managed_policy", "product_env", "release_notes", "studio_state", "ui_text",
        "update_card", "update_check", "update_feed", "update_state", "whatsnew_card", "update_delta", "update_download",
        "update_flow", "update_hold", "update_install", "update_stage_mac", "update_stage_win", "work_guard")


class Timers:
    def __init__(self):
        self.registered = {}

    def register(self, function, first_interval=0.0, persistent=False):
        self.registered[function] = first_interval

    def is_registered(self, function):
        return function in self.registered

    def unregister(self, function):
        self.registered.pop(function, None)


class Data(SimpleNamespace):
    """``bpy.data`` with Blender's save timing: a save that succeeded leaves ``is_dirty`` True until Blender handles the
    save's notifier, after the save call (``wm_event_system.cc``: ``wm->file_saved``); ``notifier_pass`` is that
    handling, which a test runs where Blender's event loop would, before the next timers."""

    def __init__(self, **fields):
        super().__init__(**fields)
        self.save_pending = False

    def notifier_pass(self):
        if self.save_pending:
            self.save_pending, self.is_dirty = False, False


class Wm(SimpleNamespace):
    """``bpy.ops.wm``: a ``save_mainfile`` that finishes, the stand-in's or one a test puts in its place, marks the
    file saved only at the next ``Data.notifier_pass``, as Blender does."""

    def __init__(self, data, **operators):
        object.__setattr__(self, "_data", data)
        super().__init__(**operators)   # Fills the namespace directly, so the save is wrapped here:
        if "save_mainfile" in operators:
            self.save_mainfile = operators["save_mainfile"]

    def __setattr__(self, name, value):
        if name == "save_mainfile" and callable(value):
            value = self._saving(value)
        object.__setattr__(self, name, value)

    def _saving(self, save):
        data = self._data

        def saving(*args, **kwargs):
            result = save(*args, **kwargs)
            if result == {'FINISHED'} and 'INVOKE_DEFAULT' not in args:
                data.save_pending = True
            return result
        saving.__wrapped__ = save
        return saving


# Every list Blender's ``bpy.app.handlers`` carries (bpy_app_handlers.cc, in its order), so code that registers on
# any of them runs here as in Blender; test_native_update_restart_settle_edits keeps it equal to the source.
APP_HANDLER_LISTS = (
    "frame_change_pre", "frame_change_post", "render_pre", "render_post", "render_write", "render_stats",
    "render_init", "render_complete", "render_cancel", "load_pre", "load_post", "load_post_fail", "save_pre",
    "save_post", "save_post_fail", "undo_pre", "undo_post", "redo_pre", "redo_post", "depsgraph_update_pre",
    "depsgraph_update_post", "version_update", "load_factory_preferences_post", "load_factory_startup_post",
    "xr_session_start_pre", "annotation_pre", "annotation_post", "object_bake_pre", "object_bake_complete",
    "object_bake_cancel", "composite_pre", "composite_post", "composite_cancel", "animation_playback_pre",
    "animation_playback_post", "translation_update_post", "_extension_repos_update_pre",
    "_extension_repos_update_post", "_extension_repos_sync", "_extension_repos_files_clear", "blend_import_pre",
    "blend_import_post", "exit_pre",
)


def settle(bpy, passes=4):
    """Blender's event-loop passes until the update hand-over has its settled document (or gave up): each a notifier
    pass, then the hand-over's settle timer (``update_restart_handoff.launch_when_settled``), as Blender runs them."""
    for _ in range(passes):
        bpy.data.notifier_pass()
        for function in list(bpy.app.timers.registered):
            if function.__qualname__.startswith("launch_when_settled."):
                bpy.app.timers.registered.pop(function)
                again = function()
                if again is not None:
                    bpy.app.timers.registered[function] = again


class Header:
    """A Blender UI class that draw functions are appended to."""

    def __init__(self):
        self.appended = []

    def append(self, function):
        self.appended.append(function)

    def remove(self, function):
        self.appended.remove(function)


class SpaceView3D:
    """``bpy.types.SpaceView3D``: an area's active space is an instance, and its class takes the draw handlers."""

    @staticmethod
    def draw_handler_add(*args):
        return "handler"

    @staticmethod
    def draw_handler_remove(*args):
        return None


class Area:
    def __init__(self, kind, pointer, width=1200, height=800, x=0, y=0):
        self.type, self.ui_type, self._pointer = kind, kind, pointer
        self.width, self.height, self.redraws = width, height, 0
        self.regions = [SimpleNamespace(type='WINDOW', x=x, y=y, width=width, height=height)]
        self.spaces = SimpleNamespace(active=SpaceView3D())

    def as_pointer(self):
        return self._pointer

    def tag_redraw(self):
        self.redraws += 1


class Window:
    def __init__(self, pointer=1, areas=None):
        self._pointer = pointer
        self.screen = SimpleNamespace(areas=areas if areas is not None else [Area('VIEW_3D', 11)], is_temporary=False)
        self.workspace = SimpleNamespace(path_resolve=lambda name, coerce=True: ("workspace", name))
        self.modal_operators = []
        self.cursors = []

    def as_pointer(self):
        return self._pointer

    def cursor_set(self, name):
        self.cursors.append(name)


class WindowManager:
    def __init__(self):
        self.windows = [Window()]
        self.clipboard = ""
        self.modal_handlers, self.timers, self.removed_timers, self.file_selects = [], [], [], []

    def modal_handler_add(self, operator):
        self.modal_handlers.append(operator)

    def event_timer_add(self, interval, window=None):
        timer = SimpleNamespace(interval=interval, window=window)
        self.timers.append(timer)
        return timer

    def event_timer_remove(self, timer):
        self.removed_timers.append(timer)

    def fileselect_add(self, operator):
        self.file_selects.append(operator)


def build():
    """``(modules for sys.modules, recorder)``."""
    record = SimpleNamespace(classes=[], published=[], links=[], operators=[])
    bpy = types.ModuleType("bpy")
    bpy_types = types.ModuleType("bpy.types")
    bpy_types.Operator = bpy_types.Panel = bpy_types.Menu = object
    bpy_types.Window = "Window"
    bpy_types.WindowManager = SimpleNamespace()
    bpy_types.SpaceView3D = SpaceView3D
    for name in ("STATUSBAR_HT_header", "TOPBAR_MT_help", "TOPBAR_HT_upper_bar", "ANGELO_MT_workspace_mode"):
        setattr(bpy_types, name, Header())
    bpy_props = types.ModuleType("bpy.props")
    for name in ("StringProperty", "BoolProperty", "EnumProperty", "IntProperty"):
        setattr(bpy_props, name, lambda **kwargs: kwargs)
    bpy.types, bpy.props = bpy_types, bpy_props
    bpy.app = types.ModuleType("bpy.app")
    bpy.app.__dict__.update(background=False, timers=Timers(), online_access=False, online_access_override=False,
                            tempdir="", version_string="5.2.2 LTS", build_hash=b"311bbe4efa04", build_date=b"2026-10-09",
                            cachedir="", binary_path="/nonexistent/Angelo.app/Contents/MacOS/Angelo",
                            is_job_running=lambda kind: False)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    for name in APP_HANDLER_LISTS:
        setattr(handlers, name, [])
    bpy.app.handlers = handlers
    bpy.utils = SimpleNamespace(register_class=record.classes.append,
                                unregister_class=lambda cls: record.classes.remove(cls),
                                resource_path=lambda kind: "/Applications/Angelo.app/Contents/Resources/5.2")
    bpy.context = SimpleNamespace(window_manager=WindowManager(),
                                  preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1.0)),
                                  temp_override=lambda **kwargs: mock.MagicMock())
    bpy.msgbus = SimpleNamespace(publish_rna=lambda key: record.published.append(key))

    def operator(name):
        return lambda *args, **kwargs: record.operators.append((name, args, kwargs)) or {'FINISHED'}

    data = Data(filepath="", is_dirty=False, images=[], scenes=[])
    bpy.ops = SimpleNamespace(
        angelo=SimpleNamespace(card_host=operator("angelo.card_host"), crash_save_report=operator("angelo.crash_save_report"),
                               crash_consent_ask=operator("angelo.crash_consent_ask")),
        wm=Wm(data, url_open=lambda url: record.links.append(url) or {'FINISHED'},
              quit_blender=operator("wm.quit_blender"), save_mainfile=operator("wm.save_mainfile"),
              save_as_mainfile=operator("wm.save_as_mainfile")),
        image=SimpleNamespace(save_all_modified=operator("image.save_all_modified")))
    bpy.data = data
    blf = types.ModuleType("blf")
    blf.size = lambda font, size: None
    blf.dimensions = lambda font, text: (len(text) * 7.0, 12.0)
    blf.load = lambda path: 5
    gpu = types.ModuleType("gpu")
    gpu.state = SimpleNamespace(blend_set=lambda mode: None, scissor_test_set=lambda on: None,
                                scissor_set=lambda *a: None)
    bl_ui = types.ModuleType("bl_ui")
    userpref = types.ModuleType("bl_ui.space_userpref")
    userpref.CenterAlignMixIn = type("CenterAlignMixIn", (), {})
    bl_ui.space_userpref = userpref
    modules = {"bpy": bpy, "bpy.types": bpy_types, "bpy.props": bpy_props, "bpy.app": bpy.app,
               "bpy.app.handlers": handlers, "blf": blf, "gpu": gpu,
               "bl_ui": bl_ui, "bl_ui.space_userpref": userpref}
    return modules, record


class Notices:
    """Context for one test: ``with Notices() as n: n.update_ui ...``. ``extra`` adds stand-in angelo_agent modules."""

    def __init__(self, **extra):
        self.extra = extra

    def __enter__(self):
        for name in PURE:
            importlib.import_module("angelo_agent." + name)
        modules, self.record = build()
        self.bpy = modules["bpy"]
        self.blf = modules["blf"]
        for name, module in self.extra.items():
            modules["angelo_agent." + name] = module
        self._attributes = {name: getattr(angelo_agent, name) for name in list(MODULES) + list(self.extra)
                            if hasattr(angelo_agent, name)}
        self._patch = mock.patch.dict(sys.modules, modules)
        self._patch.start()
        for name in MODULES:
            sys.modules.pop("angelo_agent." + name, None)
        for name in MODULES:
            setattr(self, name, importlib.import_module("angelo_agent." + name))
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        for name in list(MODULES) + list(self.extra):
            if name in self._attributes:
                setattr(angelo_agent, name, self._attributes[name])
            elif hasattr(angelo_agent, name):
                delattr(angelo_agent, name)
        return False
