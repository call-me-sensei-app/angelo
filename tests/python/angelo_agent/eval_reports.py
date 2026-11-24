# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A report as Blender shows one, for a first-hour case's starting state (``eval_firsthour_tasks``): a running
(modal) operator's ``report``, so it lands where the person saw it (the status bar and the Info log). An operator
called from Python keeps its reports to itself (``pyop_call``: "Own so these don't move into global reports"); a
modal one's reach the window's (``wm_operator_reports``). Test-only: never part of the shipped add-on.
``test_blender_firsthour_cases.py`` reads the Info log back to prove it."""

_registered = []


def _operator(bpy):
    if _registered:
        return
    from bpy.props import StringProperty

    class ANGELO_EVAL_OT_report(bpy.types.Operator):
        """Write one report on its first timer tick, as a running operator that failed would."""
        bl_idname = "angelo_eval.report"
        bl_label = "Report"
        bl_options = {'INTERNAL'}
        kind: StringProperty(default='ERROR')
        text: StringProperty()

        def invoke(self, context, event):
            self._timer = context.window_manager.event_timer_add(0.01, window=context.window)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}

        def modal(self, context, event):
            if event.type != 'TIMER':
                return {'PASS_THROUGH'}
            context.window_manager.event_timer_remove(self._timer)
            self.report({self.kind}, self.text)
            return {'FINISHED'}

    bpy.utils.register_class(ANGELO_EVAL_OT_report)
    _registered.append(ANGELO_EVAL_OT_report)


def add(bpy, kind, text):
    _operator(bpy)
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.angelo_eval.report('INVOKE_DEFAULT', kind=kind, text=text)   # Reports on the next timer tick.


def info_log(bpy):
    """The Info log's text (every report), read the way a person copies it."""
    window = bpy.context.window_manager.windows[0]
    area = window.screen.areas[0]
    previous = area.type
    area.type = 'INFO'
    try:
        region = next(r for r in area.regions if r.type == 'WINDOW')   # The report list, whatever the order.
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.info.select_all(action='SELECT')
            bpy.ops.info.report_copy()
        return bpy.context.window_manager.clipboard
    finally:
        area.type = previous
