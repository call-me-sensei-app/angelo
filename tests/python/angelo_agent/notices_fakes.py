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
        "update_flow", "update_install", "update_stage_mac", "update_stage_win", "work_guard")


class Timers:
    def __init__(self):
        self.registered = {}

    def register(self, function, first_interval=0.0, persistent=False):
        self.registered[function] = first_interval

    def is_registered(self, function):
        return function in self.registered

    def unregister(self, function):
        self.registered.pop(function, None)


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
    handlers.load_pre, handlers.save_post, handlers.save_post_fail = [], [], []
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

    bpy.ops = SimpleNamespace(
        angelo=SimpleNamespace(card_host=operator("angelo.card_host"), crash_save_report=operator("angelo.crash_save_report"),
                               crash_consent_ask=operator("angelo.crash_consent_ask")),
        wm=SimpleNamespace(url_open=lambda url: record.links.append(url) or {'FINISHED'},
                           quit_blender=operator("wm.quit_blender"), save_mainfile=operator("wm.save_mainfile"),
                           save_as_mainfile=operator("wm.save_as_mainfile")),
        image=SimpleNamespace(save_all_modified=operator("image.save_all_modified")))
    bpy.data = SimpleNamespace(filepath="", is_dirty=False, images=[], scenes=[])
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
