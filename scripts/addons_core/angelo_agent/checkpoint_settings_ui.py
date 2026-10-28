# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Human-owned snapshot policy and exact, separately confirmed retention UI."""


def protected_snapshot_refs(controllers):
    from .checkpoint_policy import checkpoint_ref
    return sorted({checkpoint_ref(controller.state['id'], controller.request_checkpoint)
                   for controller in controllers if controller.request_checkpoint})


def workspace_operators(review_line_type):
    from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty
    from bpy.types import Operator
    from . import ui
    from .checkpoint_policy import CheckpointPolicyStore
    from .document_scene import busy_message   # A busy chat waiting for its scene is named (R5-M-1).

    def lines(operator, values):
        from . import chat_view
        operator.review_lines.clear()
        for value in values:  # Measured wrapping, so translated text without spaces still fits the list.
            for fragment, _ in chat_view.wrap(value, 640, 13, chat_view._font) or (('', 0),):
                operator.review_lines.add().body = fragment

    class ANGELO_OT_checkpoint_settings(Operator):
        bl_idname = 'angelo.checkpoint_settings'
        bl_label = 'Snapshot Settings'
        bl_description = 'Explicitly configure request snapshots and their automatically applied limits'
        capture_mode: EnumProperty(name='Capture', items=[
            ('EVERY_REQUEST', 'Every Request (default)', 'Capture the complete document before every agent request'),
            ('BEFORE_FIRST_EDIT', 'Before First Approved Edit (opt-in)', 'Read-only requests may lack a snapshot; capture before the first approved edit')],
            default='EVERY_REQUEST', options={'SKIP_SAVE'})
        _GIB = 1024 ** 3
        # The default (checkpoint_policy.DEFAULT_MAX_BYTES) is explained beside the field in draw().
        max_storage_gib: FloatProperty(name='Storage limit in GiB · 0 is unlimited', min=0.0, max=1000000.0,
                                       default=2.0, precision=2, step=50, options={'SKIP_SAVE'},
                                       description='Disk space for restore points. Default 2 GiB; unchanged scene data is stored once')
        max_count: IntProperty(name='Maximum snapshots · 0 is unlimited', min=0, max=1000000, options={'SKIP_SAVE'})
        max_age_days: IntProperty(name='Maximum age in days · 0 is unlimited', min=0, max=36500, options={'SKIP_SAVE'})
        user_confirmed: BoolProperty(name='I understand and confirm this exact snapshot policy', default=False, options={'SKIP_SAVE'})

        def invoke(self, context, event):
            from .ui_text import iface_
            self._store = CheckpointPolicyStore(ui.state_directory())
            self._initial = self._store.load()
            self.capture_mode, self.max_count = self._initial['capture_mode'], self._initial['max_count']
            self.max_storage_gib, self.max_age_days = self._initial['max_bytes'] / self._GIB, self._initial['max_age_days']
            self.user_confirmed = False
            return context.window_manager.invoke_props_dialog(self, width=680, confirm_text=iface_('Save Snapshot Policy'))

        def draw(self, context):
            self.layout.prop(self, 'capture_mode')
            self.layout.label(text='Every Request preserves the full pre-request snapshot guarantee.')
            self.layout.label(text='Opt-in: read-only requests may have no snapshot; the first approved edit does.')
            self.layout.prop(self, 'max_storage_gib')
            from . import chat_view
            from .ui_text import iface_
            note = iface_('Default 2 GiB. Parts of your scene that did not change are stored only once, so this '
                          'usually holds many restore points. Raise it for very large scenes, or set 0 for no limit.')
            for line in chat_view.wrap(note, 620, 12, chat_view._font):  # Translate, then wrap.
                self.layout.label(text=line[0], translate=False)
            for field in ('max_count', 'max_age_days'):
                self.layout.prop(self, field)
            limits = iface_('Limits are applied automatically after each snapshot: the oldest restore points go first. '
                            'Active requests in every chat, safety snapshots and required file-restore chains are kept, '
                            'even if that leaves storage over the limit. Removed points keep an unavailable Restore link. '
                            'Points captured before a git commit identical to the saved project are removed first.')
            for line in chat_view.wrap(limits, 620, 12, chat_view._font):
                self.layout.label(text=line[0], translate=False)
            self.layout.prop(self, 'user_confirmed')

        def execute(self, context):
            from .ui_text import rpt_
            try:
                if not self.user_confirmed or not hasattr(self, '_initial'):
                    raise ValueError(rpt_('Review and explicitly confirm the exact snapshot policy first'))
                if self._store.load() != self._initial:
                    raise ValueError(rpt_('Snapshot settings changed; reopen and review the current policy'))
                from .checkpoint_policy import POLICY_VERSION
                self._store.save({'version': POLICY_VERSION, 'capture_mode': self.capture_mode, 'max_count': self.max_count,
                    'max_bytes': min(2**63 - 1, round(self.max_storage_gib * self._GIB)), 'max_age_days': self.max_age_days},
                    user_confirmed=True)
            except (OSError, ValueError, TypeError) as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            from . import snapshot_service
            snapshot_service.request_maintenance(ui)
            return {'FINISHED'}

    class ANGELO_OT_checkpoint_retention(Operator):
        bl_idname = 'angelo.checkpoint_retention'
        bl_label = 'Review Snapshot Retention'
        bl_description = 'Inspect exact snapshot IDs and the bytes their removal releases; removal requires a separate explicit confirmation'
        user_confirmed: BoolProperty(name='Permanently remove only the exact snapshots listed above', default=False, options={'SKIP_SAVE'})
        review_lines: CollectionProperty(type=review_line_type, options={'SKIP_SAVE'})
        review_index: IntProperty(default=0, options={'SKIP_SAVE'})

        def invoke(self, context, event):
            from .ui_text import iface_, plural_, rpt_
            try:
                if any(controller.busy for controller in ui._controllers.values()):
                    raise ValueError(busy_message(ui._controllers.values(), rpt_, rpt_(
                        'Finish or Stop requests in every chat before reviewing snapshot removals')))
                controller = ui.get_controller()
                self._store = controller.checkpoints
                policy = CheckpointPolicyStore(ui.state_directory()).load()
                self._plan = self._store.prune_plan(policy, protected_refs=protected_snapshot_refs(ui._controllers.values()))
                if self._plan['blocked']:
                    raise ValueError(rpt_('Snapshot storage is incomplete or malformed; no history will be deleted'))
                if not self._plan['candidates']:
                    self.report({'INFO'}, rpt_('No snapshots match the current retention limits; protected recovery history was kept'))
                    return {'CANCELLED'}
                self.user_confirmed = False
                values = [plural_(len(self._plan['candidates']), '{count} exact snapshot · {size} measured bytes',
                                  '{count} exact snapshots · {size} measured bytes', size=self._plan['candidate_bytes']),
                          iface_('These restore points become unavailable after removal. The user scene and Undo are unchanged.')]
                values += [plural_(item['bytes'], '{ref} · {count} byte', '{ref} · {count} bytes', ref=item['ref'])
                           for item in self._plan['candidates']]
                values += [iface_('Protected recovery points and required file restore chains are retained. '
                                  'Shared snapshot data is counted once; each listed size is what its removal releases.')]
                values += [iface_('Protected: {ref} · {reason}', ref=item['ref'], reason=item['reason']) for item in self._plan['protected']]
                if not self._plan['budget_met']:
                    values += [iface_('The requested budget cannot currently be met; protected recovery history and audit metadata remain.')]
                lines(self, values)
            except (OSError, ValueError, TypeError) as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            return context.window_manager.invoke_props_dialog(self, width=700, confirm_text=iface_('Remove Listed Snapshots'))

        def draw(self, context):
            self.layout.template_list('ANGELO_UL_native_review', '', self, 'review_lines', self, 'review_index', rows=12)
            self.layout.prop(self, 'user_confirmed')
            self.layout.label(text='The complete inventory and active recovery references are checked again before deletion.')

        def execute(self, context):
            from .ui_text import rpt_
            try:
                if not self.user_confirmed or not hasattr(self, '_plan'):
                    raise ValueError(rpt_('Review and explicitly confirm the exact snapshot removal plan first'))
                if any(controller.busy for controller in ui._controllers.values()):
                    raise ValueError(busy_message(ui._controllers.values(), rpt_, rpt_(
                        'A request started during review; inspect a fresh removal plan after it finishes')))
                self._store.prune(self._plan, user_confirmed=True,
                                  protected_refs=protected_snapshot_refs(ui._controllers.values()))
            except (OSError, RuntimeError, ValueError, TypeError) as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            from . import snapshot_service
            snapshot_service.request_maintenance(ui)  # Releases the removed points' shared data.
            return {'FINISHED'}

    return (ANGELO_OT_checkpoint_settings, ANGELO_OT_checkpoint_retention)
