# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fab branches of the existing two Library tools and their shared human Session."""
from . import fab_approval, fab_host, fab_native_job
from .fab_catalog import FabError
from .ui_text import n_


def _point(window, args):
    return tuple(args.get('location', window.scene.cursor.location))


def precondition(service, args):
    fab_native_job.ready()
    service._require_view()
    host = fab_host.owner()
    session = host.current_session()
    return fab_approval.Binding(session, session.selection(args['item_id'], args['variant_id']),
                               generation=host.ticket()[0], controller=service.controller,
                               item_id=args['item_id'], variant_id=args['variant_id'],
                               scene=service.window.scene.session_uid, location=_point(service.window, args))


def search(service, args):
    from .library_agent import LibraryPending, _failure
    host = fab_host.owner()
    job = host.submit_agent(lambda session, stop: session.search(args['query'],
                           limit=args.get('limit', 8), cancel=stop))
    state = {'result': None}

    def poll():
        if state['result'] is not None:
            return state['result']
        if not job.done.is_set():
            return None
        try:
            result = job.take()
            rows = result['items']
            state['result'] = dict(result, source='fab', query=args['query'],
                                   total=len(rows), next_offset=None)
        except FabError as error:
            state['result'] = _failure(str(error), 'EXPIRED' if error.code == 'EXPIRED' else 'UNSUPPORTED')
        except Exception:
            state['result'] = _failure('Fab search could not complete.', 'UNSUPPORTED')
        return state['result']
    return LibraryPending(poll, cancel=job.stop, cleanup=job.stop, timeout=120,
                          status_text=n_('Searching Fab…'), activity_note=n_('Getting asset details…'))


def use(service, args):
    host = fab_host.owner()
    session = host.current_session()
    point = _point(service.window, args)
    expected = fab_approval.executing(service, session=session, generation=host.ticket()[0], args=args,
                                     scene=service.window.scene.session_uid, location=point)
    return fab_native_job.start(host, service.window, args['item_id'], args['variant_id'], expected, point)


def human_use(host, window, area, region, item_id, variant_id, *, at=None):
    # Fab has no drag/drop bypass: its reviewed Use action imports at the cursor.
    if at is not None or area.type != 'VIEW_3D' or region.type != 'WINDOW':
        raise FabError('UNSUPPORTED')
    expected = host.selection
    if expected is None or host.current_session().selection(item_id, variant_id) != expected:
        raise FabError('EXPIRED')
    from . import library_actions, library_overlay, live_target, live_windows
    from .library_agent import LibraryPending
    from .ui_text import iface_
    held_window, area_pointer = live_target.window_ref(window), area.as_pointer()
    title = expected[0].title
    state = library_overlay.state_for(area)
    pending = fab_native_job.start(host, window, item_id, variant_id, expected, tuple(window.scene.cursor.location))
    state['progress'] = (0.02, iface_('Adding “{title}” to your scene…', title=title))
    finished = False

    def poll():
        nonlocal finished
        result = pending.poll()
        if result is None or finished:
            return result
        finished = True
        current = held_window()
        current_area = next((a for a in live_windows.areas(current) if a.as_pointer() == area_pointer), None) if current else None
        view = library_overlay._views.get(area_pointer)
        if view is state:
            state.pop('progress', None)
            success = result.get('success') is True
            library_actions._record('added' if success else 'failed', item_id, result)
            if success:
                library_actions._notice(state, iface_('Added “{title}”', title=title))
            else:
                library_actions._notice(state, iface_("The Fab asset couldn't be added. Check the scene before trying again."))
            if current_area is not None:
                current_area.tag_redraw()
        return result

    def cleanup():
        pending.cancel()
        if library_overlay._views.get(area_pointer) is state:
            state.pop('progress', None)
    return LibraryPending(poll, cancel=pending.cancel, cleanup=cleanup, timeout=900,
                          status_text=pending.status_text, activity_note=pending.activity_note)
