# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Import add-on modules afresh against a stand-in ``bpy``, leaving the process as it was (host tests only).

``mock.patch.dict(sys.modules, ...)`` drops what is first imported inside it from sys.modules, but the modules stay
attributes of the ``angelo_agent`` package, and ``from angelo_agent import x`` reads the package's attribute before
sys.modules: a later test would bind a module bound to this test's stand-ins while another import of the same name
loads a fresh one (two classes of one exception: test_native_web_export_wait's VALIDATION read as UNKNOWN in the
full suite). ``import_against_stand_ins`` imports every add-on module fresh and puts the package back as it was.

A suite whose tests then call code that imports more of the add-on as it runs (``engine_link.publish`` imports
``engine_link_live``) needs those to be the very modules it drove, so it keeps what it imported instead: ``snapshot``
before its own ``mock.patch.dict`` block, ``imported_since`` inside it, ``settle`` after it. Each module the block
imported is then either the same object again in sys.modules (it holds no stand-in ``bpy``) or unreachable (it holds
it: its package attribute goes too). Without it a later ``import angelo_agent.x`` made a second ``x`` beside the one
other modules held: two ``engine_link_facts``, two process ids, and test_native_engine_link_save_report failed after
test_native_engine_link_state (a Windows shard, 2026-11-11; the same order fails on 1.0.10).
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


def snapshot():
    """sys.modules as it is now, for ``imported_since``."""
    return dict(sys.modules)


def imported_since(before, package="angelo_agent"):
    """The package's modules in sys.modules now that weren't there, as these objects, at ``before``."""
    return {name: module for name, module in sys.modules.items()
            if name.startswith(package + ".") and before.get(name) is not module}


def settle(found, stand_in_bpy, package="angelo_agent"):
    """After the block: each module in ``found`` is importable as the same object again, or not reachable at all."""
    parent = sys.modules.get(package)
    for name, module in found.items():
        if getattr(module, "bpy", None) is stand_in_bpy:
            attribute = name.rsplit(".", 1)[1]
            if parent is not None and getattr(parent, attribute, None) is module:
                delattr(parent, attribute)
        else:
            sys.modules.setdefault(name, module)
