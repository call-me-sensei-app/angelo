# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plan mode's operators (doc/angelo/plan_mode.rst, "Go", "Revise and Stop planning").

``angelo.plan_go`` (a plan card's Go with this) and ``angelo.plan_stop`` (the planning chip's close) are grant
operators (``grant_guard.guard``, never listed for Angelo's automatic decision): each runs only in the C scope of the
person's ticketed press, never from a script, an add-on, a keymap entry or a timer. Their bodies call
``planning_controls``, whose functions are grant entries that ask C again. A refusal is said in the chat."""

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Operator

from . import grant_guard  # noqa: E402  (only the person grants: grant_guard.grant)


def _controller(session_id):
    from . import ui
    return ui._controllers.get(session_id) if session_id else ui.get_controller()


def _refused(operator, controller, error):
    """Say why the press did nothing, in the chat (its own sentence) and in the operator's report."""
    from .display_text import error_notice, shown
    first = error.args[0] if error.args else error
    operator.report({'WARNING'}, shown(first) if not isinstance(error, PermissionError) else str(error)[:300])
    if controller is not None:
        controller.emit("message", {"role": "error", **error_notice(error)})


class ANGELO_OT_plan_go(Operator):
    """A plan card's Go with this: the person's press only (grant_guard)."""
    bl_idname = "angelo.plan_go"
    bl_label = "Go with This Plan"
    bl_description = "Leave planning in the setting shown and send this approach as your next message"
    bl_options = {'INTERNAL'}
    session_id: StringProperty(options={'SKIP_SAVE'})   # The card's own chat.
    call_id: StringProperty(options={'SKIP_SAVE'})      # Its plan (the angelo_plan PROPOSE call).
    approach: IntProperty(default=-1, options={'SKIP_SAVE'})
    approval: StringProperty(options={'SKIP_SAVE'})     # "ASK" or "AUTO_BOUNDED": the card's Then.

    def execute(self, context):
        from . import planning_card, planning_controls
        controller = _controller(self.session_id)
        if controller is None:
            return {'CANCELLED'}
        try:
            notes = planning_card._drafts(context.area)   # What this chat's message box holds goes with the choice.
            planning_controls.go(controller, self.call_id, self.approach, self.approval, notes)
        except (ValueError, RuntimeError, PermissionError) as error:
            _refused(self, controller, error)
            return {'CANCELLED'}
        planning_card._drafts(context.area, ("", 0))   # Taken with the choice: that message box is empty, as after Send.
        return {'FINISHED'}


class ANGELO_OT_plan_stop(Operator):
    """The planning chip's close: the person's press only (grant_guard)."""
    bl_idname = "angelo.plan_stop"
    bl_label = "Stop Planning"
    bl_description = "Stop planning: the chat asks before changes, and nothing is sent"
    bl_options = {'INTERNAL'}
    session_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import planning_controls
        controller = _controller(self.session_id)
        if controller is None:
            return {'CANCELLED'}
        try:
            planning_controls.stop(controller)
        except (ValueError, RuntimeError, PermissionError) as error:
            _refused(self, controller, error)
            return {'CANCELLED'}
        return {'FINISHED'}


CLASSES = (ANGELO_OT_plan_go, ANGELO_OT_plan_stop)
grant_guard.guard(*CLASSES)   # Only the person's press grants (grant_guard); never Angelo's own decision.


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
