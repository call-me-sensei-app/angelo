# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's notice when the person presses Stop (``ANGELO_OT_stop``).

It says what Stop does to the work the request has in flight, read before the
request stops: a native preview or job stops between steps; a running agent
script (``angelo_execute_python``) cannot be interrupted, while queued work is
cancelled. A request with neither (a reply streaming, thinking, a call waiting
in the queue or for approval) simply stops, and the notice says only that.

``text`` stays English in the chat history (a later context reads it in the
handoff), ``i18n`` lets the canvas translate it (``ui_text.notice``).
"""

from .ui_text import notice

SCRIPT_TOOL = "angelo_execute_python"


def stop_notice(descriptors, script_running=False):
    """The notice for the native cards ``descriptors`` (``agent_workspace_ui.native_cards``) and a running script."""
    if any(str(item.get('state', '')).upper() == 'PREVIEW' for item in descriptors):
        return notice('Stopping. Previews are not applied to your scene; work in progress stops between steps.')
    if descriptors:
        return notice('Stopping between steps. Changes already applied can still be undone.')
    if script_running:
        return notice('Stop requested. A running legacy script cannot be interrupted; queued work is cancelled.')
    return notice('Stopped')


def stop_message(descriptors, script_running=False):
    return stop_notice(descriptors, script_running)['text']


def stop_notice_for(controller):
    """The notice for Stop in ``controller``'s chat; call it before ``Controller.stop``."""
    from .agent_workspace_ui import native_cards
    running = any(call.executing and call.name == SCRIPT_TOOL for call in tuple(controller.pending.values()))
    return stop_notice(native_cards(controller), running)
