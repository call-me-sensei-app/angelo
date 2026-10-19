# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Readable native reviews; human authorization never comes from memory.

Bpy-independent helpers test exact controls and staging without a user document.
"""

from collections.abc import Mapping
import hashlib
import json
import math

from .ui_text import iface_, n_, plural_
from .detail_keys import translated as translated_key


def readable_label(value):
    label = str(value).replace('_', ' ').strip()
    return translated_key(label[:1].upper() + label[1:])  # Known keys read in the interface language (detail_keys).


def exact_number(value):
    return repr(value) if isinstance(value, float) else str(value)


def _line(prefix, heading, value):
    return prefix + (iface_('{label}: {value}', label=heading, value=value) if heading else value)


def control_lines(value, label=None, indent=0, target_labels=None):
    """Enumerate bounded controls in order, keeping exact numbers, empty collections
    and false controls visible without slicing values or dumping JSON."""
    return list(iter_control_lines(value, label, indent, target_labels))


def iter_control_lines(value, label=None, indent=0, target_labels=None):
    """Yield the same exact controls incrementally for request-owned UI preparation."""
    prefix = '  ' * indent
    heading = readable_label(label) if label is not None else ''
    if isinstance(value, Mapping):
        if not value:
            yield _line(prefix, heading, iface_('None'))
            return
        if heading:
            yield prefix + iface_('{label}:', label=heading)
        child = indent + bool(heading)
        for key, item in value.items():
            yield from iter_control_lines(item, key, child, target_labels)
        return
    if isinstance(value, (tuple, list)):
        if not value:
            yield _line(prefix, heading, iface_('None'))
            return
        if all(item is None or type(item) in (str, bool, int, float) for item in value):
            shown = tuple(_scalar(item, target_labels) for item in value)
            # Keep long arrays in ordered item rows: joining maximal strings
            # otherwise makes one shaping/font call monopolize the UI timer.
            if sum(map(len, shown)) + 2 * (len(shown) - 1) <= 1024:
                yield _line(prefix, heading, ', '.join(shown))
                return
        yield _line(prefix, heading, plural_(len(value), '{count} ordered item', '{count} ordered items'))
        for index, item in enumerate(value, 1):
            yield from iter_control_lines(item, iface_('Item {number}', number=index), indent + 1, target_labels)
        return
    yield _line(prefix, heading, _scalar(value, target_labels))


def _scalar(value, target_labels=None):
    if value is None:
        return iface_('None')
    if type(value) is bool:
        return iface_('Yes') if value else iface_('No')
    if type(value) in (int, float):
        return exact_number(value)
    if isinstance(value, str) and target_labels and value in target_labels:
        name = str(target_labels[value]).replace('\n', '\\n').replace('\r', '\\r')
        return iface_('{handle} · “{name}” (display name only)', handle=value, name=name)
    return str(value)


def readable_controls(value, title=None, target_labels=None):
    return '\n'.join([title or iface_('Exact settings'), *control_lines(value, target_labels=target_labels)])


def review_identity(descriptor):
    """A UI fingerprint, not an authorization grant or a substitute for gating."""
    fields = {key: descriptor.get(key) for key in
              ('call_id', 'stage_id', 'preview_token', 'arguments', 'changes', 'checks', 'summary', 'target_labels')}
    encoded = json.dumps(fields, ensure_ascii=False, sort_keys=True, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _check_status(status):
    if status is None:
        return iface_('Not verified')
    if isinstance(status, bool):
        return iface_('Passed') if status else iface_('Failed')
    return str(status).replace('_', ' ').capitalize()


def check_lines(checks, exact=True):
    if not checks:
        return [iface_('Checks: No verified checks were supplied.')]
    if isinstance(checks, Mapping):
        checks = [{'name': key, **value} if isinstance(value, Mapping)
                  else {'name': key, 'status': value} for key, value in checks.items()]
    lines = [iface_('Checks on the preview:')]
    for check in checks:
        if not isinstance(check, Mapping):
            lines.append('  ' + str(check))
            continue
        name = readable_label(check.get('name') or check.get('label') or check.get('kind') or iface_('Unnamed check'))
        lines.append('  ' + iface_('{label}: {value}', label=name, value=_check_status(check.get('status', check.get('passed')))))
        for key, value in check.items() if exact else ():
            if key not in {'name', 'label', 'status', 'passed'}:
                lines.extend(control_lines(value, key, 2))
    return lines


_CHANGE_GROUPS = (('added', n_('Added')), ('removed', n_('Removed')), ('modified', n_('Modified', 'Angelo')))


def staged_change_lines(changes, exact=True):
    """Do not claim pending staged object counts are edits to the user scene."""
    if not changes:
        return [iface_('Staged changes: No native change read-back was supplied.')]
    heading = iface_('Staged changes (not applied to the document):')
    if exact or not isinstance(changes, Mapping):
        return [heading, *control_lines(changes, indent=1)]
    lines = [heading]
    for group, label in _CHANGE_GROUPS:
        entries = changes.get(group, [])
        lines.append('  ' + iface_('{label}: {value}', label=iface_(label), value=len(entries)))
        for entry in entries:
            name = str(entry.get('name') or entry.get('handle') or iface_('Unnamed object'))
            lines.append('    ' + (iface_('{name} · {fields}', name=name, fields=', '.join(entry['fields'])) if entry.get('fields') else name))
    if changes.get('truncated') or changes.get('uncertain'):
        lines.append(iface_('Read-back is bounded or incomplete; inspect the exact retained details before accepting.'))
    return lines


def preview_lines(descriptor, exact=False):
    state = str(descriptor.get('state', '')).upper()
    lines = [iface_('Preview · built on a copy of your scene') if state == 'PREVIEW' else iface_('Working · {state}', state=state.capitalize())]
    if descriptor.get('summary'):
        lines.extend(control_lines(descriptor['summary'], iface_('Summary')))
    lines.extend(staged_change_lines(descriptor.get('changes'), exact=exact))
    lines.extend(check_lines(descriptor.get('checks'), exact=exact))
    if exact:
        lines.extend(['', *control_lines(descriptor.get('arguments', {}), iface_('Exact submitted controls'), target_labels=descriptor.get('target_labels'))])
        if descriptor.get('stage_id'):
            lines.append(iface_('Stage identity: {identity}', identity=descriptor['stage_id']))
        if descriptor.get('preview_token'):
            lines.append(iface_('Exact preview identity: {identity}', identity=descriptor['preview_token']))
    lines.extend(['', iface_('Accept applies this preview as one Undo step, after checking your scene has not changed since.'),
                  iface_('Discard throws the preview away. Neither overwrites changes you made in the meantime.')])
    return lines


def job_progress_text(descriptor):
    progress = descriptor.get('progress')
    label = str(descriptor.get('summary') or iface_('Preparing isolated native stage'))
    if isinstance(progress, Mapping):
        label = str(progress.get('label') or progress.get('phase') or label).replace('_', ' ')
        completed = progress.get('completed', progress.get('completed_units'))
        total = progress.get('total', progress.get('total_units'))
        if type(completed) in (int, float) and type(total) in (int, float) and total > 0:
            return (iface_('Step {current} of {total}', current=exact_number(completed), total=exact_number(total)) if label.replace(' ', '').isupper() else iface_('{label} · {completed} / {total}', label=label, completed=exact_number(completed), total=exact_number(total)))
    elif type(progress) in (int, float) and math.isfinite(progress):
        label = iface_('{label} · {percent}%', label=label, percent=exact_number(progress))
    return label


def performance_lines(summary):
    if not summary:
        return [iface_('No native performance samples recorded for this chat yet.')]
    return [iface_('Measured native execution (not model claims)'), *control_lines(summary),
            '', iface_('Local bounded telemetry excludes prompt text, source code and credentials.')]


def normalized_preview(value):
    descriptor = dict(value)
    descriptor['state'] = str(value.get('state') or value.get('status') or 'PREVIEW').upper()
    descriptor.setdefault('stage_id', value.get('preview_id') or value.get('id'))
    descriptor.setdefault('preview_id', value.get('stage_id') or value.get('id'))
    descriptor.setdefault('arguments', value.get('program', {}))
    descriptor.setdefault('changes', value.get('delta', {}))
    descriptor.setdefault('summary', value.get('readable_summary') or descriptor['changes'].get('summary', ''))
    return descriptor


def native_cards(controller):
    previews = [preview_descriptor(controller, value['preview_id']) for value in controller.native_previews()]
    identities = set(controller.native_jobs) | {call.id for call in tuple(controller.pending.values()) if getattr(call, 'staging', False)}
    jobs = [controller.native_job_status(identity) for identity in sorted(identities)]
    return previews + [dict(value, state=str(value.get('state', 'RUNNING')).upper()) for value in jobs if value]


def preview_descriptor(controller, identity):
    from .agent_target_labels import labels_for
    descriptor = normalized_preview(controller.inspect_native_preview(identity))
    descriptor.setdefault('target_labels', labels_for(controller, descriptor.get('call_id')))
    return descriptor


def preview_available(descriptor):
    return (descriptor.get('state') == 'PREVIEW' and bool(descriptor.get('preview_id'))
            and bool(descriptor.get('stage_id')) and bool(descriptor.get('preview_token')))


_approval_reviews = {}
_classes = ()

from .native_preview_images import draw as draw_native_preview


def approval_preview_reviewed(controller, call):
    try:
        descriptor = preview_descriptor(controller, call.arguments['preview_id'])
        return (preview_available(descriptor) and _approval_reviews.get((controller.state['id'], call.id))
                == review_identity(descriptor))
    except (KeyError, RuntimeError, ValueError, TypeError):
        return False


def memory_snapshot(controller):
    from .task_memory import read_memory
    return read_memory(controller.state, controller.document)


def workspace_operators():
    """Import bpy only when registering the application UI, not in host tests."""
    import uuid
    import bpy
    from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
    from bpy.types import Operator, PropertyGroup, UIList
    from . import ui
    from .blender_tools import BlenderExecutor
    from . import program_undo
    undo_bridge = program_undo.native_bridge()

    def controller_for(session_id):
        return ui._controllers.get(session_id) if session_id else ui.get_controller()

    def add_lines(operator, lines):
        from . import chat_view
        operator.review_lines.clear()
        for line in lines:
            for fragment, _ in chat_view.wrap(line, 620, 13, chat_view._font):
                operator.review_lines.add().body = fragment

    class ANGELO_PG_native_review_line(PropertyGroup):
        body: StringProperty(options={'SKIP_SAVE'})

    class ANGELO_UL_native_review(UIList):
        def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
            layout.label(text=item.body)

    class ANGELO_OT_commit_native_job(Operator):
        bl_idname = 'angelo.commit_native_job'
        bl_label = 'Commit Validated Native Program'
        bl_description = 'Internal final commit; fresh exact document/revision checks and one Undo'
        bl_options = program_undo.operator_flags(undo_bridge)
        call_id: StringProperty(options={'SKIP_SAVE'})
        session_id: StringProperty(options={'SKIP_SAVE'})

        def execute(self, context):
            from .ui_text import rpt_
            controller = controller_for(self.session_id)
            call = controller.pending.get(self.call_id) if controller else None
            if call is None or controller.expire_tool(call):
                return {'CANCELLED'}
            if not context.preferences.edit.use_global_undo:
                controller.stop()
                self.report({'ERROR'}, rpt_('Enable Global Undo before accepting native scene changes; uncommitted staging was stopped'))
                return {'CANCELLED'}
            result = program_undo.commit(controller, call, BlenderExecutor(ui.main_window()), ui.document_key(),
                                         context.mode, undo_bridge, undo_steps=context.preferences.edit.undo_steps)
            if not isinstance(result, dict):
                return {'CANCELLED'}
            if result.get('error'):
                self.report({'ERROR'}, str(result['error']))
            # Preserve an Undo boundary for reported partial live mutations.
            return {'FINISHED'} if result.get('scene_changed') or result.get('partial_changes_possible') else {'CANCELLED'}

    class ANGELO_OT_accept_native_preview(Operator):
        bl_idname = 'angelo.accept_native_preview'
        bl_label = 'Review and Accept Preview'
        bl_description = 'Review exact staged controls, changes and checks, then explicitly confirm; reject stale work'
        preview_id: StringProperty(options={'SKIP_SAVE'})
        session_id: StringProperty(options={'SKIP_SAVE'})
        call_id: StringProperty(options={'SKIP_SAVE'})
        review_confirmed: BoolProperty(name='I reviewed this exact staged result, its controls and check results', default=False, options={'SKIP_SAVE'})
        review_lines: CollectionProperty(type=ANGELO_PG_native_review_line, options={'SKIP_SAVE'})
        review_index: IntProperty(default=0, options={'SKIP_SAVE'})

        def invoke(self, context, event):
            from .ui_text import iface_, rpt_
            controller = controller_for(self.session_id)
            if controller is None:
                return {'CANCELLED'}
            self.session_id = controller.state['id']
            try:
                descriptor = preview_descriptor(controller, self.preview_id)
                if not preview_available(descriptor):
                    raise ValueError(rpt_('No retained exact preview is available; inspect or request a fresh preview'))
                self._review_hash = review_identity(descriptor)
                self._preview_token = descriptor['preview_token']
                self._bound_document = ui.document_key()
                self.review_confirmed = False
                add_lines(self, preview_lines(descriptor, exact=True))
            except (RuntimeError, ValueError, KeyError, TypeError) as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            from . import review_card  # An Angelo card, not a stock dialog; the operator waits for its decision.
            return review_card.start(self, context, descriptor)

        def modal(self, context, event):
            from . import review_card
            return review_card.wait(self, context, event)

        def execute(self, context):
            from .ui_text import rpt_
            controller = controller_for(self.session_id)
            if controller is None:
                return {'CANCELLED'}
            try:
                if not self.review_confirmed or not getattr(self, '_review_hash', None):
                    raise ValueError(rpt_('Open the exact preview review and explicitly confirm it before accepting'))
                if getattr(self, '_bound_document', None) != ui.document_key() or controller.document != ui.document_key():
                    raise ValueError(rpt_('Document changed during preview review; no staged changes were applied'))
                descriptor = preview_descriptor(controller, self.preview_id)
                if not preview_available(descriptor) or review_identity(descriptor) != self._review_hash:
                    raise ValueError(rpt_('Preview, controls or checks changed during review; reopen the exact preview'))
                if not context.preferences.edit.use_global_undo:
                    raise ValueError(rpt_("Enable Blender's Global Undo before accepting a native preview"))
                if self.call_id:
                    call = controller.pending.get(self.call_id)
                    if call is None or call.name != 'angelo_accept_preview' or call.arguments['preview_id'] != self.preview_id:
                        raise ValueError(rpt_('The exact preview approval expired; no change was applied'))
                    _approval_reviews[(self.session_id, call.id)] = self._review_hash
                    while len(_approval_reviews) > 64:
                        _approval_reviews.pop(next(iter(_approval_reviews)))
                    return bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=self.session_id)
                executor = BlenderExecutor(ui.main_window())
                executor.studio_context = controller  # The retained stage belongs to this exact chat, not just a window.
                if not controller.accept_native_preview(self.preview_id, executor, ui.document_key(), self._preview_token):
                    raise ValueError(rpt_('Preview acceptance was rejected; no staged change was applied'))
            except (RuntimeError, ValueError, KeyError, TypeError) as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            return {'FINISHED'}

    class ANGELO_OT_discard_native_preview(Operator):
        bl_idname = 'angelo.discard_native_preview'
        bl_label = 'Discard Preview'
        bl_description = 'Throw away this preview; changes already applied stay'
        preview_id: StringProperty(options={'SKIP_SAVE'})
        session_id: StringProperty(options={'SKIP_SAVE'})

        def execute(self, context):
            from .ui_text import rpt_
            controller = controller_for(self.session_id)
            try:
                if controller is None or not controller.discard_native_preview(self.preview_id,
                        BlenderExecutor(ui.main_window()), ui.document_key()):
                    raise ValueError(rpt_('The retained preview is no longer available'))
            except (RuntimeError, ValueError, KeyError) as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            return {'FINISHED'}

    class ANGELO_OT_project_brief(Operator):
        bl_idname = 'angelo.project_brief'
        bl_label = 'Project Brief'
        bl_description = 'Edit public task notes without changing scene/tool authorization or the composer'
        session_id: StringProperty(options={'SKIP_SAVE'})
        objective: StringProperty(name='Objective', maxlen=1024, options={'SKIP_SAVE'})
        style: StringProperty(name='Style', maxlen=512, options={'SKIP_SAVE'})
        constraints: StringProperty(name='Constraints · one per line', options={'SKIP_SAVE'})
        planned_steps: StringProperty(name='Planned steps · one per line', options={'SKIP_SAVE'})
        unit_system: EnumProperty(name='Units', items=[(name, name.title(), '') for name in ('NONE', 'METRIC', 'IMPERIAL')], options={'SKIP_SAVE'})
        scale_length: FloatProperty(name='Unit scale', min=.000001, max=1000, default=1, options={'SKIP_SAVE'})
        review_lines: CollectionProperty(type=ANGELO_PG_native_review_line, options={'SKIP_SAVE'})
        review_index: IntProperty(default=0, options={'SKIP_SAVE'})

        def invoke(self, context, event):
            from .ui_text import iface_, rpt_
            controller = controller_for(self.session_id)
            if controller is None or getattr(controller, 'document_invalid', False) or controller.document != ui.document_key():
                self.report({'ERROR'}, rpt_('This Project Brief belongs to an earlier document; start a new chat for the current project'))
                return {'CANCELLED'}
            self.session_id = controller.state['id']
            memory = memory_snapshot(controller)
            if memory.get('scope_mismatch') and controller.state.get('task_memory', {}).get('scope', {}).get('document'):
                result = bpy.ops.angelo.use_project_brief('INVOKE_DEFAULT', session_id=self.session_id)
                # The child owns its dialog/handler. Keeping this wrapper modal
                # would orphan a native operator with no handler to finish it.
                return {'FINISHED'} if result == {'RUNNING_MODAL'} else result
            self._revision, self._bound_document = memory['revision'], ui.document_key()
            brief = memory['brief']
            self.objective, self.style = brief['objective'], brief['style']
            self.constraints, self.planned_steps = '\n'.join(brief['constraints']), '\n'.join(memory['planned_steps'])
            self.unit_system, self.scale_length = brief['units']['system'], brief['units']['scale_length']
            lines = [iface_('Historical scene evidence is stale: {reason}', reason=memory.get('stale_reason'))] if memory['stale'] else []
            lines += control_lines(brief['dimensions'], iface_('Target dimensions'))
            lines += control_lines(memory['references'], iface_('References')) + control_lines(memory['assets'], iface_('Assets', 'Angelo'))
            add_lines(self, lines)
            return context.window_manager.invoke_props_dialog(self, width=700, confirm_text=iface_('Save Brief'))

        def draw(self, context):
            for field in ('objective', 'style', 'constraints', 'planned_steps', 'unit_system', 'scale_length'):
                self.layout.prop(self, field)
            self.layout.label(text='Dimensions and admitted evidence pointers · read-only')
            self.layout.template_list('ANGELO_UL_native_review', '', self, 'review_lines', self, 'review_index', rows=5)
            self.layout.label(text='These notes never approve scene edits, paid use or external access.')

        def execute(self, context):
            from .task_memory import update_memory
            from .ui_text import rpt_
            controller = controller_for(self.session_id)
            try:
                if (controller is None or getattr(controller, 'document_invalid', False) or controller.document != ui.document_key()
                        or getattr(self, '_bound_document', None) != ui.document_key()):
                    raise ValueError(rpt_('The document changed during brief editing; reopen Project Brief'))
                patch = {'objective': self.objective, 'style': self.style,
                         'constraints': self.constraints.splitlines() if self.constraints else [],
                         'planned_steps': self.planned_steps.splitlines() if self.planned_steps else [],
                         'units': {'system': self.unit_system, 'scale_length': self.scale_length}}
                with controller.lock:
                    controller.state = update_memory(controller.state, {'expected_revision': self._revision, 'patch': patch},
                        controller.document, provenance={'kind': 'approved_update', 'id': 'ui:' + str(uuid.uuid4())})
                    controller.version += 1
                    controller.save()
            except (RuntimeError, ValueError, KeyError, TypeError) as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}
            return {'FINISHED'}

    class ANGELO_OT_performance_summary(Operator):
        bl_idname = 'angelo.performance_summary'
        bl_label = 'Performance Summary'
        bl_description = 'Read local recorded phase timings and native tool counts; no prompt text or credentials'
        scope: EnumProperty(name='Scope', items=[('SESSION', 'This chat', ''), ('ALL', 'All local chats', '')], default='SESSION', options={'SKIP_SAVE'})
        review_lines: CollectionProperty(type=ANGELO_PG_native_review_line, options={'SKIP_SAVE'})
        review_index: IntProperty(default=0, options={'SKIP_SAVE'})

        def invoke(self, context, event):
            from .ui_text import iface_
            add_lines(self, performance_lines(ui.get_controller().metrics_report(scope=self.scope, limit=20)))
            return context.window_manager.invoke_props_dialog(self, width=700, confirm_text=iface_('Close', 'Angelo'))

        def draw(self, context):
            self.layout.template_list('ANGELO_UL_native_review', '', self, 'review_lines', self, 'review_index', rows=14)

        def execute(self, context):
            return {'FINISHED'}

    from . import checkpoint_settings_ui
    from . import project_brief_rebind_ui
    core_operators = (ANGELO_PG_native_review_line, ANGELO_UL_native_review, ANGELO_OT_commit_native_job,
            ANGELO_OT_accept_native_preview, ANGELO_OT_discard_native_preview,
            ANGELO_OT_project_brief, ANGELO_OT_performance_summary)
    return (core_operators + checkpoint_settings_ui.workspace_operators(ANGELO_PG_native_review_line)
            + project_brief_rebind_ui.workspace_operators(ANGELO_PG_native_review_line, add_lines))


def register():
    global _classes
    import bpy
    _classes = workspace_operators()
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    global _classes
    import bpy
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    _classes = ()
    _approval_reviews.clear()
    from .native_preview_images import clear
    clear()
    from .agent_target_labels import clear
    clear()
