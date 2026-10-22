# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_render_device.device`` under the agent's name (doc/angelo/long_jobs.rst, "Render device").

The GPU device setup lives outside this package (``scripts/modules/angelo_render_device``) because Cycles imports
it and must never import the agent package. This name is the same module object, not a copy: state, patches and
``import angelo_agent.render_device`` all reach it.
"""

import sys

try:
    from angelo_render_device import device as _module
except ImportError:   # A checkout's tests without Blender: scripts/modules beside scripts/addons_core.
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parents[2] / "modules"))
    from angelo_render_device import device as _module

sys.modules[__name__] = _module
