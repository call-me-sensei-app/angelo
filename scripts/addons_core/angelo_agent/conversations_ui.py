# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Conversations operators: open the list, rename, delete (with its question), bring a deleted chat back.

The chat header's title opens the Conversations popover (``angelo.conversations``, ``conversations_popover``); the
operators are the same actions for everything else (F3 search, a key binding, a script, a GUI test). Each acts
through ``rename``/``delete``/``restore`` here, which the popover calls too, so both say the same things: a refusal
is its reason (``conversations_delete.blocker``), never a stopped turn.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import conversations, conversations_delete as rules
from .conversations_layout import shown_title
from .display_text import Text, describe_error, shown
from .ui_text import iface_, n_, plural_, rpt_

DELETE_QUESTION = n_("Delete “{title}”?")
DELETED = n_("Moved “{title}” to Recently Deleted.")
RESTORED = n_("Restored “{title}”.")
RENAME_FAILED = n_("Couldn't rename this chat. The details are in the system console.")
DELETE_FAILED = n_("Couldn't move this chat to Recently Deleted. The details are in the system console.")
RESTORE_FAILED = n_("Couldn't restore this chat. The details are in the system console.")


def keep_sentence():
    """How long a deleted chat stays restorable, in the interface language."""
    return plural_(conversations.KEEP_DAYS, "It moves to Recently Deleted, where you can restore it for {count} day.",
                   "It moves to Recently Deleted, where you can restore it for {count} days.",
                   count=conversations.KEEP_DAYS)


def _ui():
    from . import ui
    ui.get_controller()   # The chat list exists from here on.
    return ui


def _switch(ui):
    def switch(session_id):
        from . import document_scene
        if session_id is None:
            ui.create_session()
        else:
            document_scene.select(ui.show_session(session_id), ui.main_window(), ui.document_key())   # Its scene back.
    return switch


def _dismiss_tile(session_id):
    from . import session_rail_input
    session_rail_input.dismiss(session_id)


def title_of(session_id):
    ui = _ui()
    record = ui._sessions.records.get(session_id)
    return shown_title(record) if record else ""


def rename(session_id, text):
    """``(title now shown, None)`` or ``(None, reason)``: the reason is a ``display_text.Text`` (``shown``
    translates it)."""
    from .session_journal import JournalHosted
    ui = _ui()
    try:
        title = conversations.rename(ui.state_directory(), ui._sessions, session_id, text,
                                     ui._controllers.get(session_id))
    except JournalHosted:
        return None, Text(rules.HOSTED, title=title_of(session_id))
    except (OSError, ValueError) as error:
        return None, describe_error(error, RENAME_FAILED)
    _redraw()
    return title, None


def delete(session_id):
    """``(entry, None)`` or ``(None, reason)``: the chat in Recently Deleted (``conversations.delete``), or why not."""
    ui = _ui()
    if session_id not in ui._sessions.records:
        return None, Text(conversations.NOT_LISTED)
    title = title_of(session_id)
    reason = rules.blocker(ui, session_id)
    if reason is not None:
        return None, Text(reason, title=title)
    try:
        entry = rules.delete(ui, session_id, switch=_switch(ui), dismiss_tile=_dismiss_tile)
    except (OSError, ValueError, RuntimeError) as error:
        return None, describe_error(error, DELETE_FAILED)
    _redraw()
    return entry, None


def restore(session_id=""):
    """``(record, None)`` or ``(None, reason)``. No ``session_id``: the chat deleted last (Undo)."""
    ui = _ui()
    if not session_id:
        latest = conversations.entries(ui.state_directory())
        if not latest:
            return None, Text(conversations.NOT_DELETED)
        session_id = latest[0]["record"]["id"]
    try:
        record = rules.restore(ui, session_id, switch=_switch(ui))
    except (OSError, ValueError, LookupError, RuntimeError) as error:
        return None, describe_error(error, RESTORE_FAILED)
    _redraw()
    return record, None


def _redraw():
    from . import console, live_windows
    for window in live_windows.windows(bpy.context.window_manager):
        for area in live_windows.areas(window):
            if console.is_chat(area):
                area.tag_redraw()


class ANGELO_OT_conversations(Operator):
    bl_idname = "angelo.conversations"
    bl_label = "Chats"
    bl_description = "Switch, search, rename or delete chats"

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        from . import conversations_popover
        if conversations_popover.open_popover(context):
            return {'FINISHED'}
        bpy.ops.wm.call_menu(name="ANGELO_MT_sessions")   # No chat canvas to draw on: Blender's own list.
        return {'FINISHED'}


class ANGELO_OT_rename_conversation(Operator):
    bl_idname = "angelo.rename_conversation"
    bl_label = "Rename Chat"
    bl_description = "Give this chat your own name. An empty name uses the automatic title again"
    session_id: StringProperty(options={'SKIP_SAVE'})
    name: StringProperty(name="Name", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        ui = _ui()
        self.session_id = self.session_id or ui.get_controller().state["id"]
        record = ui._sessions.records.get(self.session_id) or {}
        from .sessions import UNTITLED
        self.name = record.get("title", "") if record.get("title") != UNTITLED else ""
        return context.window_manager.invoke_props_dialog(self, title=iface_("Rename Chat"),
                                                          confirm_text=iface_("Rename"), translate=False)

    def execute(self, context):
        title, reason = rename(self.session_id or _ui().get_controller().state["id"], self.name)
        if reason:
            self.report({'ERROR'}, shown(reason))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_delete_conversation(Operator):
    bl_idname = "angelo.delete_conversation"
    bl_label = "Delete Chat"
    bl_description = "Move this chat to Recently Deleted, where you can restore it"
    session_id: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        self.session_id = self.session_id or _ui().get_controller().state["id"]
        return context.window_manager.invoke_confirm(
            self, event, title=iface_(DELETE_QUESTION, title=title_of(self.session_id)), message=keep_sentence(),
            confirm_text=iface_("Delete"), icon='WARNING', translate=False)

    def execute(self, context):
        session_id = self.session_id or _ui().get_controller().state["id"]
        title = title_of(session_id)
        entry, reason = delete(session_id)
        if reason:
            self.report({'ERROR'}, shown(reason))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt_(DELETED, title=title))
        return {'FINISHED'}


class ANGELO_OT_restore_conversation(Operator):
    bl_idname = "angelo.restore_conversation"
    bl_label = "Restore Chat"
    bl_description = "Bring a chat back from Recently Deleted (the last one deleted when none is named)"
    session_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        record, reason = restore(self.session_id)
        if reason:
            self.report({'ERROR'}, shown(reason))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt_(RESTORED, title=shown_title(record)))
        return {'FINISHED'}


CLASSES = (ANGELO_OT_conversations, ANGELO_OT_rename_conversation, ANGELO_OT_delete_conversation,
           ANGELO_OT_restore_conversation)
