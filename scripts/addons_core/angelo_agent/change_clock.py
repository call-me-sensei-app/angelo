# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_change_clock`` under the agent's name: a stat vouches for a file only once a coarse file-system clock
has passed its change time (``doc/angelo/checkpoint_policy.rst``, "Differences and limits").

The helper lives outside this package (``scripts/modules/angelo_change_clock.py``) because ``angelo_render_device``
keys its cache on a stat too and must not import the agent package. This name is the same module object, not a
copy: its counters and patches are shared.
"""

import sys

try:
    import angelo_change_clock as _module
except ImportError:   # A checkout's tests without Blender: the file beside scripts/addons_core, loaded by its path.
    # Never by putting scripts/modules on sys.path: it holds Blender's own ``bpy`` package, which an ``import bpy``
    # there would find and fail on outside Blender (a module that skips itself without bpy would raise instead).
    import importlib.util
    from pathlib import Path
    _spec = importlib.util.spec_from_file_location(
        "angelo_change_clock", Path(__file__).resolve().parents[2] / "modules" / "angelo_change_clock.py")
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["angelo_change_clock"] = _module
    _spec.loader.exec_module(_module)

_module.prime()   # Windows: the volumes are listed on a thread from the first store's import (a headless child too).
sys.modules[__name__] = _module
