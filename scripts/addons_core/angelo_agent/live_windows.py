# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_live_windows`` under the agent's name: the one walk over windows and areas (doc/angelo/ui_kit.rst,
"Windows and areas").

The helper lives outside this package (``scripts/modules/angelo_live_windows.py``) because ``angelo_render_device``
and the Console bootstraps walk windows too and must not import the agent package. This name is the same module
object, not a copy.
"""

import sys

try:
    import angelo_live_windows as _module
except ImportError:   # A checkout's tests without Blender: scripts/modules beside scripts/addons_core.
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parents[2] / "modules"))
    import angelo_live_windows as _module

sys.modules[__name__] = _module
