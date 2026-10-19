# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The UI side of ``runtime_prewarm``: the open chat editor's draft, read on every UI pump tick.

``pump(ui)`` (``ui.pump``, every 100 ms when idle) hands ``runtime_prewarm.observe``
the active chat, the draft its open chat editors show (they all show the active
chat) and every chat. A defect here is printed and never keeps the pump from
dispatching tools: a prewarm is only a head start.
"""

import time

from . import console, runtime_prewarm
from .pump_guard import report_defect


def open_draft(window_manager):
    """The draft in the open chat editors: its text, "" when empty, None when no chat editor is open."""
    from .editor_types import read_chat_draft
    found = None
    for window in window_manager.windows:
        for area in window.screen.areas:
            if console.is_chat(area):
                draft = read_chat_draft(area.spaces.active)
                if draft is not None:
                    if draft[0].strip():
                        return draft[0]
                    found = ""
    return found


def pump(ui):
    controller = ui._controller
    if controller is None:
        return
    try:
        import bpy
        runtime_prewarm.observe(controller, open_draft(bpy.context.window_manager), list(ui._controllers.values()),
                                time.monotonic())
    except Exception as error:  # noqa: BLE001 - only a head start: never keeps the pump from dispatching tools.
        report_defect("the runtime prewarm", error, controller.redact)  # i18n: console log line
