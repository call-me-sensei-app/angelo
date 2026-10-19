# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Open the Generative AI editor using durable Angelo accounts and local assets.

Use --factory-startup for a separate preview. This never modifies an installed
Blender app, clears connections, imports a model, or starts paid provider usage.
"""

import sys
from pathlib import Path

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons_core"))
if not addon_utils.check("angelo_agent")[1]:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
from angelo_agent import studio, editor_types, preview_branding

if not editor_types.native_picker():
    preview_branding.install()


def open_studio():
    window = bpy.context.window_manager.windows[0]
    from angelo_agent import ui, console, director
    from angelo_agent.input_policy import interaction_active
    if interaction_active(window):
        return .2
    if director._timer is not None:
        return .2
    if director.is_director(window):
        director.request_mode(window, "MANUAL")
        return .2
    if ui._layout_timer is not None:
        return .2
    if not any(console.is_chat(a) for a in window.screen.areas):
        ui.request_chat_dock(window, "RIGHT")
        return .2
    # This is an editor, not a new top-level workspace/tab. The dock operation
    # has finished, so this pointer belongs to the visible current screen.
    target = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    with bpy.context.temp_override(window=window, area=target):
        studio.setup_area(window, target)
    studio.view(target)["collapsed"] = False
    return None


bpy.app.timers.register(open_studio, first_interval=.5)
