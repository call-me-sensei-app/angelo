# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A permissive stand-in for Blender's modules, so operator modules import and their classes run on the host.

``StandIn(names)`` installs an import hook for ``bpy`` and Blender's other built-in modules, imports the requested
``angelo_agent`` modules afresh against it, and on exit puts back exactly what ``sys.modules`` and the package held
before (other host tests keep the modules and stand-ins they imported). ``bpy.types.*`` are plain classes, so an
operator subclass is a real class whose methods run; ``bpy.props.*`` return markers; ``bpy.app`` says it is not in
background mode; anything else is a ``MagicMock``. Behaviour inside Blender is proven by the GUI suites.
"""

import importlib
import importlib.abc
import importlib.machinery
import sys
import types
from unittest.mock import MagicMock

import angelo_agent

ROOTS = frozenset({"bpy", "_bpy", "bpy_types", "bpy_extras", "gpu", "gpu_extras", "blf", "bmesh", "mathutils", "bl_ui",
                   "bl_operators", "bl_math", "aud", "idprop", "imbuf", "freestyle", "addon_utils", "bgl", "cycles",
                   "rna_keymap_ui", "keyingsets_utils", "nodeitems_utils", "bl_keymap_utils", "bl_app_template_utils",
                   "bl_i18n_utils", "bpy_restrict_state", "console_python", "rna_prop_ui", "bl_previews_utils"})


# Built-in parts a stand-in Blender does not have: code that asks for them falls back as it would without them (the
# C guard: script_confinement and grant_authority then use their Python twins; a MagicMock would read as confined).
ABSENT = {"_bpy": {"_angelo_guard"}}


class _Loose(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__") or name in ABSENT.get(self.__name__, ()):
            raise AttributeError(name)
        value = MagicMock(name="%s.%s" % (self.__name__, name))
        setattr(self, name, value)
        return value


class _Types(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        value = type(name, (), {"bl_rna": MagicMock(), "is_registered": False})
        setattr(self, name, value)
        return value


class _Props(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **options: ("property", name, options)


def _identity(message, *_args, **_kwargs):
    return message


def _module(name):
    if name == "bpy.types":
        return _Types(name)
    if name == "bpy.props":
        return _Props(name)
    module = _Loose(name)
    if name == "bpy.app":
        module.background, module.version, module.version_string = False, (5, 3, 0), "5.3.0"
        module.binary_path, module.use_event_simulate = "/stand-in/Angelo", False
    elif name == "bpy.app.handlers":
        module.persistent = lambda function: function
    elif name == "bpy.app.translations":
        module.locale = "en_US"
        for function in ("pgettext", "pgettext_iface", "pgettext_tip", "pgettext_rpt", "pgettext_data", "pgettext_n"):
            setattr(module, function, _identity)
    return module


class _Loader(importlib.abc.Loader):
    def create_module(self, spec):
        return _module(spec.name)

    def exec_module(self, module):
        parent, _, child = module.__name__.rpartition(".")
        if parent:
            setattr(sys.modules[parent], child, module)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] not in ROOTS:
            return None
        return importlib.machinery.ModuleSpec(fullname, _Loader(), is_package=True)


class StandIn:
    """``with StandIn(("external_ui", ...)) as modules: modules["external_ui"]``."""

    def __init__(self, names):
        self.names, self.modules, self._finder = tuple(names), {}, None

    def __enter__(self):
        self._saved = {k: v for k, v in sys.modules.items()
                       if k.split(".")[0] in ROOTS or k.startswith("angelo_agent.")}
        self._attrs = dict(vars(angelo_agent))
        for key in self._saved:
            sys.modules.pop(key, None)
        # And every submodule the package holds as an attribute, whether or not sys.modules still has it: ``from .
        # import x`` takes the attribute before importing, so a leftover made a module here read another copy than
        # the one handed back (2.0.1: two external_setup objects), or one in no sys.modules at all, as an earlier
        # test's import under ``mock.patch.dict(sys.modules)`` leaves (CI of f9f749b4af7: ui.console, alone green).
        for key, value in list(vars(angelo_agent).items()):
            if isinstance(value, types.ModuleType) and value.__name__.startswith("angelo_agent."):
                vars(angelo_agent).pop(key)
        self._finder = _Finder()
        sys.meta_path.insert(0, self._finder)
        try:
            for name in ("bpy.types", "bpy.props", "bpy.app", "bpy.app.translations", "bpy.app.handlers",
                         "bpy.app.timers", "bpy.utils", "bpy.ops"):   # ``bpy.types.Operator`` as an attribute too.
                importlib.import_module(name)
            for name in self.names:
                self.modules[name] = importlib.import_module("angelo_agent." + name)
        except BaseException:
            # Never leave the hook or half-imported modules behind: every later test in the process would see them
            # (VM CI of 5da639e: a failed import here broke ~400 tests after it in its shard).
            self.__exit__(None, None, None)
            raise
        return self.modules

    def __exit__(self, *_exc):
        if self._finder is None or self._finder not in sys.meta_path:
            return False   # Already put back.
        sys.meta_path.remove(self._finder)
        for key in [k for k in sys.modules if k.split(".")[0] in ROOTS or k.startswith("angelo_agent.")]:
            sys.modules.pop(key, None)
        sys.modules.update(self._saved)
        for key in [k for k in vars(angelo_agent) if k not in self._attrs]:
            delattr(angelo_agent, key)
        for key, value in self._attrs.items():
            setattr(angelo_agent, key, value)
        return False
