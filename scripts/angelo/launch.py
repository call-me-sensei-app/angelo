# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Launch a scratch scene, not a scratch account store. No source build needed.

ANGELO_STATE_DIR isolates chats/checkpoints only. Account metadata and new
native profiles live in the persistent Angelo data directory. Only explicit
ANGELO_CONNECTIONS_DIR isolates accounts (for automated test fixtures).
"""

import sys
from pathlib import Path

import addon_utils
import bpy

bpy.context.preferences.view.show_splash = False

source = Path(__file__).resolve().parents[1] / "addons_core"
sys.path.insert(0, str(source))
if not addon_utils.check("angelo_agent")[1]:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)

# A stock-binary preview cannot hot-replace Blender's compiled SVG enum data.
# Use the same Angelo artwork in native Python menus; never patch system Blender.
from angelo_agent import editor_types, preview_branding
if not editor_types.native_picker():
    preview_branding.install()


def open_chat():
    from angelo_agent import ui, console
    window = bpy.context.window_manager.windows[0]
    existing = next((area for area in window.screen.areas if console.is_chat(area)), None)
    if existing:
        ui._main_window_pointer = window.as_pointer()
        ui._dock_area_pointer = existing.as_pointer()
        ui._dock_metadata[existing.as_pointer()] = True
        ui.setup_chat_area(window, existing)
    else:
        bpy.ops.angelo.open_chat(side="RIGHT")
    return None


bpy.app.timers.register(open_chat, first_interval=1)
