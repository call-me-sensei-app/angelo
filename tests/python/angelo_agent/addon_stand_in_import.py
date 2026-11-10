# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Import add-on modules afresh against a stand-in ``bpy``, leaving the process as it was (host tests only).

``mock.patch.dict(sys.modules, ...)`` drops what is first imported inside it from sys.modules, but the modules stay
attributes of the ``angelo_agent`` package, and ``from angelo_agent import x`` reads the package's attribute before
sys.modules: a later test would bind a module bound to this test's stand-ins while another import of the same name
loads a fresh one (two classes of one exception: test_native_web_export_wait's VALIDATION read as UNKNOWN in the
full suite). Here every add-on module is imported fresh and the package is put back as it was.
"""

import importlib
import sys
import types
from unittest import mock


def import_against_stand_ins(bpy, names):
    """The modules ``angelo_agent.<name>`` for each of ``names``, imported against ``bpy`` (a module with ``types``
    and ``props`` submodules), as a tuple."""
    package = importlib.import_module("angelo_agent")
    before = {name: value for name, value in vars(package).items() if isinstance(value, types.ModuleType)}
    with mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props}):
        for name in [name for name in sys.modules if name.startswith("angelo_agent.")]:
            del sys.modules[name]   # Fresh, never an earlier test's module bound to its own stand-ins.
        for name in before:
            delattr(package, name)
        imported = tuple(importlib.import_module("angelo_agent." + name) for name in names)
    for name, value in list(vars(package).items()):
        if isinstance(value, types.ModuleType) and before.get(name) is not value:
            delattr(package, name)
    for name, value in before.items():
        setattr(package, name, value)
    return imported
