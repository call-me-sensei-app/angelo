# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cheap host-only display labels; exact handles/revisions remain authority.

Read once at UI admission, never from a GPU draw callback. No fingerprints,
operators, custom properties, data creation, or model-supplied approval flags.
"""

from collections import OrderedDict

_cache = OrderedDict()
MAX_REVIEWS = 64


def argument_handles(arguments):
    handles = {entry['handle'] for field in ('read_set', 'write_set')
               for entry in arguments.get(field, []) if isinstance(entry, dict) and isinstance(entry.get('handle'), str)}
    target = arguments.get('object')   # A sculpt edit's object, by scene handle (sculpt.target) or by name.
    return handles | ({target} if isinstance(target, str) and target.startswith('scn:') else set())


def labels_for(controller, call_id):
    return dict(_cache.get((controller.state['id'], call_id), {}))


def prepare(controller, window, document):
    from . import scene_queries
    if controller.document != document or controller.document_invalid:
        return
    for call in tuple(controller.pending.values()):
        from .sculpt_spec import SCULPT_MUTATIONS
        if (call.name not in {'angelo_scene_program', 'angelo_apply_recipe'} | SCULPT_MUTATIONS
                or getattr(call, '_target_labels_prepared', False)):
            continue
        # A recipe call is labelled once the host has expanded its program, and a scene program the host may add to
        # (program_camera: a released camera binds the scene) once its first precondition fixed it.
        arguments = getattr(call, 'program_arguments', None)
        if call.name in SCULPT_MUTATIONS:
            arguments = call.arguments
        elif arguments is None and call.name == 'angelo_scene_program':
            from .program_camera import needs_camera
            pending = getattr(call, 'precondition', None) is None and needs_camera(call.arguments)
            arguments = None if pending else call.arguments
        if arguments is None:
            continue
        handles = argument_handles(arguments)
        # At most 64 declared handles. One scene scan, not 64 scans or an
        # expensive object_revision() call inside rendering.
        wanted = {int(value.rsplit(':', 1)[1]) for value in handles
                  if value.startswith('scn:') and value.rsplit(':', 1)[1].isdigit()}
        labels = {}
        scene_uid = getattr(window.scene, 'session_uid', None)
        scene_handle = scene_queries.object_handle(window.scene) if scene_uid is not None and scene_uid in wanted else None
        if scene_handle is not None and scene_handle in handles:
            labels[scene_handle] = "Scene '" + window.scene.name + "' settings"
            wanted.discard(window.scene.session_uid)
        for obj in window.scene.objects if wanted else ():
            if obj.session_uid in wanted:
                handle = scene_queries.object_handle(obj)
                if handle in handles:
                    labels[handle] = obj.name
                wanted.discard(obj.session_uid)
                if not wanted:
                    break
        for operation in arguments.get('operations', []):
            parameters = operation.get('parameters', {})
            if parameters.get('alias') and parameters.get('name'):
                labels['$' + parameters['alias'].lstrip('$')] = parameters['name']
        call.target_labels = labels
        call._target_labels_prepared = True
        key = (controller.state['id'], call.id)
        _cache[key] = dict(labels)
        _cache.move_to_end(key)
        while len(_cache) > MAX_REVIEWS:
            _cache.popitem(last=False)


def clear():
    _cache.clear()
