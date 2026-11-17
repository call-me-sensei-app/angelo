# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Preferences: Forget Agent Changes in a Folder (doc/angelo/script_confinement.rst, "What scripts wrote").

Code a confined agent script wrote, moved or linked never loads as Angelo's own or as an add-on's
(``_angelo_guard``'s record, kept across sessions). When the person has reviewed such a folder and wants its code
to load as theirs, they choose it here: the record forgets the folder's paths, and the identity and content of the
files now in it.

Only the person's own click does this: the operator is a grant (``grant_guard``), so C refuses it to an agent
script and to Angelo's automatic decisions, and ``forget_changes`` in C refuses any caller outside the person's own
grant scope.
"""

import bpy
from bpy.props import BoolProperty, StringProperty

from . import grant_guard  # noqa: E402  (only the person grants: grant_guard.grant)
from .ui_text import iface_, n_, rpt_

FORGOTTEN = n_("Angelo now loads the code in {folder} as your own.")
NOT_A_FOLDER = n_("Choose a folder to forget agent changes in.")
NOT_SAVED = n_("Angelo couldn't update its record of agent changes: {reason}")
EXPLAINED = n_("Code agent scripts wrote never loads as Angelo's own or as an add-on's.")


class ANGELO_OT_forget_agent_changes(bpy.types.Operator):
    bl_idname = "angelo.forget_agent_changes"
    bl_label = "Forget Agent Changes in a Folder…"
    bl_description = ("Load the code in a folder you choose as your own again: Angelo forgets which of its files "
                      "agent scripts wrote, moved or linked. Only your own click can do this")
    grant_browse = True   # Its invoke opens a file browser: the person's pick holds consent longer (grant_guard).

    directory: StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE'})
    filter_folder: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    def consent_target(self, context):
        """Nothing beyond its own field (the folder the person picks)."""
        return None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        import os
        folder = self.directory.rstrip("/\\") or self.directory
        if not folder or not os.path.isdir(folder):
            self.report({'ERROR'}, rpt_(NOT_A_FOLDER))
            return {'CANCELLED'}
        try:
            forget(folder)
        except NotADirectoryError:
            self.report({'ERROR'}, rpt_(NOT_A_FOLDER))
            return {'CANCELLED'}
        except OSError as error:   # PermissionError included: not the person's own scope.
            self.report({'ERROR'}, rpt_(NOT_SAVED, reason=str(error)))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt_(FORGOTTEN, folder=folder))
        return {'FINISHED'}


def forget(folder):
    """Forget what agent scripts changed in ``folder`` (C: only the person's own grant scope); how many records went.
    Without the C module there is no record (the Python stand-in keeps none)."""
    from . import script_confinement
    guard = script_confinement._guard
    return guard.forget_changes(folder) if guard is not None else 0


def draw_preferences(layout):
    """The add-on's own preferences (Add-ons ▸ Angelo Agent Chat) and the page-less AI Providers fallback, after
    Always allow scripts: what the record does (wrapped, never clipped in any language), then the action. The
    interactive AI Providers canvas page (providers_page_model) has no such section."""
    from .ui_kit import paragraph
    column = layout.column(align=True)
    paragraph(column, iface_(EXPLAINED), icon='INFO')
    column.operator(ANGELO_OT_forget_agent_changes.bl_idname, icon='FILE_REFRESH')


forget = grant_guard.entry(forget)   # Refused while agent code runs, whoever calls it.
grant_guard.guard(ANGELO_OT_forget_agent_changes)   # Only the person's click (never a script or Auto: grant_guard).
CLASSES = (ANGELO_OT_forget_agent_changes,)
