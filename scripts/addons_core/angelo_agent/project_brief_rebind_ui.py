# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit human fallback for unverified/large/legacy project-brief continuity."""

import hashlib
import json

from .ui_text import iface_, rpt_


def review_identity(review):
    return hashlib.sha256(json.dumps(review, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def workspace_operators(review_line_type, add_lines):
    import bpy
    from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty
    from bpy.types import Operator
    from . import ui
    from .agent_workspace_ui import control_lines
    from .memory_projects import capture_loaded_project

    class ANGELO_OT_use_project_brief(Operator):
        bl_idname = 'angelo.use_project_brief'
        bl_label = 'Use This Brief for Current Project'
        bl_description = 'Explicitly retain reviewed public notes when automatic continuity cannot be verified; never authorize scene/tool/paid edits'
        session_id: StringProperty(options={'SKIP_SAVE'})
        review_confirmed: BoolProperty(name='I confirm these exact public notes belong to this current project', default=False, options={'SKIP_SAVE'})
        review_lines: CollectionProperty(type=review_line_type, options={'SKIP_SAVE'})
        review_index: IntProperty(default=0, options={'SKIP_SAVE'})

        def invoke(self, context, event):
            controller = ui._controllers.get(self.session_id) if self.session_id else ui.get_controller()
            try:
                if (controller is None or controller.busy or controller.document_invalid
                        or controller.document != ui.document_key()):
                    raise ValueError(rpt_('Open a ready chat bound to this document before confirming public notes'))
                self.session_id = controller.state['id']
                review = controller.project_rebind_review()
                self._review_hash, self._revision = review_identity(review), review['revision']
                self._document = ui.document_key()
                self.review_confirmed = False
                add_lines(self, [iface_('Automatic project continuity was not verified (large, changed, legacy or unsaved project).'),
                    iface_('Read these exact public notes. Confirm only if they belong to this same current project.'),
                    *control_lines(review, iface_('Public brief and historical pointers')),
                    iface_('All prior scene evidence, source receipts and handles remain stale.'),
                    iface_('This grants NO scene edits, tool/file access, paid use, replay or approval-policy changes.')])
            except (OSError, ValueError, TypeError, KeyError) as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            return context.window_manager.invoke_props_dialog(self, width=700, confirm_text=iface_('Use public brief'))

        def draw(self, context):
            self.layout.label(text='Public notes only · no native operation is authorized')
            self.layout.template_list('ANGELO_UL_native_review', '', self, 'review_lines', self, 'review_index', rows=14)
            self.layout.prop(self, 'review_confirmed')

        def execute(self, context):
            controller = ui._controllers.get(self.session_id)
            try:
                if not self.review_confirmed or not getattr(self, '_review_hash', None):
                    raise ValueError(rpt_('Open the exact public-brief review and explicitly confirm it first'))
                if (controller is None or controller.busy or controller.document_invalid or controller.document != ui.document_key()
                        or getattr(self, '_document', None) != ui.document_key()):
                    raise ValueError(rpt_('The document changed during review; reopen the public-brief confirmation'))
                review = controller.project_rebind_review()
                if review_identity(review) != self._review_hash:
                    raise ValueError(rpt_('The public notes changed during review; review them again before confirming'))
                loaded = capture_loaded_project(ui.main_window(), ui.document_key(), str(controller.store.directory.resolve()))
                if not controller.confirm_project_rebind(loaded, self._revision):
                    raise ValueError(rpt_('The host rejected this exact public-brief confirmation'))
            except (OSError, ValueError, TypeError, KeyError) as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            try:
                controller.save()
            except (OSError, ValueError) as error:
                self.report({'WARNING'}, rpt_('Public notes are available in this chat, but could not be saved: {error}. '
                    'No scene edits or tool approvals were granted.', error=error))
            else:
                self.report({'INFO'}, rpt_('Public brief confirmed. Prior evidence stays stale; scene, tool and paid approvals are unchanged.'))
            return {'FINISHED'}

    return (ANGELO_OT_use_project_brief,)
