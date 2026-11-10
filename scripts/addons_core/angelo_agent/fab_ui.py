# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cached Fab presentation and fenced human actions. The library_host facade owns every Session and job.

Draw helpers never connect, search, read files or start jobs. A returned request number means enqueue only;
existing library job cards report actual import completion. No account identity or private path is shown here.
"""
import json
import re

from . import fab_settings
from .fab_catalog import MAX_RESULTS, MAX_VARIANTS
from .team_library_files import MAX_BYTES
from .ui_text import iface_, n_

CONNECTION_ACTIONS = frozenset({'fab_connect', 'fab_disconnect'})
PREF_OPERATORS = {name: ('fab_connection', 'EXEC_DEFAULT', 'request') for name in CONNECTION_ACTIONS}
STATUS = {
    'disconnected': n_('Not connected to Fab'), 'connecting': n_('Opening Fab…'),
    'sign_in_required': n_('Finish signing in to Fab'), 'verifying': n_('Checking Fab sign-in…'),
    'connected': n_('Connected to Fab'), 'disconnecting': n_('Disconnecting Fab…'),
    'cleanup_required': n_('Fab could not finish disconnecting'), 'error': n_('Fab needs attention'),
    'unavailable': n_('Fab is unavailable'),
}
REASONS = {
    'LIVE_CONNECTOR_UNAVAILABLE': n_('The Fab connection is not ready.'),
    'BROWSER_REQUIRED': n_('A supported browser is needed to connect Fab.'),
    'PLATFORM_UNAVAILABLE': n_('Fab is not available on this platform.'),
    'SIGN_IN_REQUIRED': n_('Sign in to Fab in the browser, then return here.'),
    'ACCOUNT_UNVERIFIED': n_('Fab has not confirmed the current account.'),
    'ACCOUNT_CHANGED': n_('The Fab account changed. Search and choose the asset again.'),
    'AUTH_REQUIRED': n_('Connect your Fab account to continue.'),
    'CANCELLED': n_('The Fab action stopped.'),
    'EXPIRED': n_('This Fab choice expired. Search and choose it again.'),
    'SERVICE_UNAVAILABLE': n_("Fab couldn't finish this action. Try again."),
    'METADATA_UNAVAILABLE': n_('Fab has not provided the details needed to add this asset.'),
    'SELECTION_REQUIRED': n_('Choose an asset and a version before adding it.'),
    'VARIANT_UNAVAILABLE': n_('This version is no longer available. Choose another version.'),
    'UNSUPPORTED_FORMAT': n_("This asset has no GLB download on Fab, so Angelo can't add it yet."),
    'PURCHASE_REQUIRED': n_('This asset is paid. Buy it on Fab, then add it here.'),
    'ACQUISITION_REQUIRED': n_('Add this asset to your Fab library before importing it.'),
    'DOWNLOAD_PERMISSION_UNKNOWN': n_('Fab has not confirmed permission to download this asset.'),
    'IMPORT_CLEANUP_REQUIRED': n_('Fab import cleanup is incomplete. New imports are blocked.'),
    'LICENSE_UNVERIFIED': n_('The license has not been confirmed.'),
    'DOWNLOAD_LIMIT': n_('This file exceeds the supported download size.'),
    'DOWNLOAD_FAILED': n_('The Fab download failed. Try again.'),
    'CACHE_UNAVAILABLE': n_("The Fab download couldn't be stored on this computer."),
    'IMPORT_FAILED': n_("The Fab asset couldn't be added. Check the scene before trying again."),
    'CLEANUP_REQUIRED': n_('Fab could not finish disconnecting. Try Disconnect again.'),
    'UNDO_REQUIRED': n_('Enable Global Undo and set Undo Steps above zero before importing an asset.'),
    'IMPORT_UNAVAILABLE': n_('Fab imports are not available in this build.'),
}
ACCESS = {'unverified': n_('Access not confirmed'), 'owned': n_('In your Fab library'), 'free': n_('Free'),
          'purchase_required': n_('Purchase required'), 'acquisition_required': n_('Add to your Fab library first'),
          'unavailable': n_('Access unavailable')}
GENERATION = re.compile(r'[0-9a-f]{32}\Z')


def snapshot():
    """The facade's bounded public memory snapshot; no second cache or transport is constructed here."""
    from . import library_host
    read = getattr(library_host, 'fab_snapshot', None)
    value = read() if callable(read) else None
    return value if isinstance(value, dict) and type(value.get('version')) is int and value['version'] == 1 else None


def epoch(view):
    if not isinstance(view, dict):
        return None
    generation, request = view.get('generation'), view.get('request_id')
    if ((generation is not None and (not isinstance(generation, str) or not GENERATION.fullmatch(generation)))
            or type(request) is not int or request < 0):
        return None
    return generation, request


def reason(code):
    return iface_(REASONS.get(code, REASONS['SERVICE_UNAVAILABLE']))


def status(view):
    return iface_(STATUS.get((view or {}).get('status'), STATUS['unavailable']))


def items(view):
    rows = (view or {}).get('items', [])
    return rows if isinstance(rows, list) and len(rows) <= MAX_RESULTS else []


def variants(view):
    rows = (view or {}).get('variants', [])
    return rows if isinstance(rows, list) and len(rows) <= MAX_VARIANTS else []


def selected(view):
    row = (view or {}).get('selected_item')
    return row if isinstance(row, dict) and row.get('id') == view.get('selected_item_id') else None


def choice(view, identity):
    return next((row for row in variants(view) if isinstance(row, dict) and row.get('id') == identity), None)


def ticket(view, item_id, variant_id=None):
    held = epoch(view)
    return (*held, item_id, variant_id) if held is not None else None


def matches(view, value):
    return (isinstance(value, (tuple, list)) and len(value) == 4 and type(value[1]) is int and epoch(view) is not None
            and tuple(value[:2]) == epoch(view))


def size_text(variant):
    """Exact observed bytes, or an explicit existing admission maximum; never an estimated exact size."""
    size, maximum = variant.get('size_bytes'), variant.get('maximum_bytes')
    if type(maximum) is not int or not 0 < maximum <= MAX_BYTES['model']:
        return None
    if size is not None:
        if type(size) is not int or not 0 < size <= maximum:
            return None
        return iface_('Download: {bytes} bytes', bytes=format(size, ','))
    return iface_('Download size not stated; at most {bytes} bytes.', bytes=format(maximum, ','))


def can_select(view, variant):
    item = selected(view)
    return bool(item and view.get('status') == 'connected' and item.get('import_ready') is True
                and item.get('license') in ('Fab Standard', 'CC-BY-4.0') and item.get('access') in ('owned', 'free')
                and variant and variant.get('supported') is True and size_text(variant) is not None)


def can_use(view):
    variant = choice(view, (view or {}).get('selected_variant_id'))
    return bool(view and view.get('import_ready') is True and can_select(view, variant))


def connection_request(action, expected):
    return json.dumps([action, *expected], separators=(',', ':'))


def run_connection_request(window, request):
    try:
        value = json.loads(request)
    except (TypeError, ValueError):
        return False
    if not isinstance(value, list) or len(value) != 3:
        return False
    return connection_action(value[0], window, value[1:])


def connection_action(action, window, expected):
    """A stale Preferences hit cannot connect/disconnect a replacement runtime or request."""
    from . import library_host
    view = snapshot()
    if (not isinstance(action, str) or action not in CONNECTION_ACTIONS or epoch(view) is None or not isinstance(expected, (tuple, list))
            or len(expected) != 2 or type(expected[1]) is not int or tuple(expected) != epoch(view)):
        return False
    if (view or {}).get('can_connect' if action == 'fab_connect' else 'can_disconnect') is not True:
        return False
    request = getattr(library_host, action)(window)
    return type(request) is int and request >= 0


_PENDING = []   # A search asked for before Fab was connected: run once the connection is ready (resume_pending).


def can_start(view):
    """Search may also start the connection: a kept sign-in resumes on its own, a new one opens Fab's sign-in."""
    return bool(view) and (view.get('status') == 'connected' or view.get('can_search') is True
                           or (view.get('status') == 'disconnected' and view.get('can_connect') is True))


def search(window, query, expected):
    from . import library_host
    view = snapshot()
    if (not can_start(view) or epoch(view) is None
            or not isinstance(expected, (tuple, list)) or tuple(expected) != epoch(view) or not isinstance(query, str)
            or not 1 <= len(query) <= 200 or not query.isprintable()):
        return False
    if view.get('status') == 'disconnected':
        request = library_host.fab_connect(window)
        if type(request) is int and request >= 0:
            _PENDING[:] = [query]
            return True
        return False
    _PENDING.clear()
    request = library_host.fab_search(window, query, limit=8)
    return type(request) is int and request >= 0


_RECONNECT = []   # Sign in again: once the expired sign-in is cleared (disconnected), open a fresh sign-in.


def expired(view):
    """The kept sign-in no longer proves an account: the person signs in again (Sign in to Fab)."""
    return bool(view) and view.get('status') == 'error' and view.get('reason_code') == 'ACCOUNT_UNVERIFIED'


def sign_in_again(window, expected):
    """Clear the expired kept sign-in, then open Fab's sign-in window (resume_pending); never a silent login."""
    from . import library_host
    view = snapshot()
    if not expired(view) or epoch(view) is None or not isinstance(expected, (tuple, list)) or tuple(expected) != epoch(view):
        return False
    request = library_host.fab_disconnect(window)
    if type(request) is int and request >= 0:
        _RECONNECT[:] = [True]
        return True
    return False


_PAYING = set()   # Buy on Fab: the listings waiting for the person to finish buying them on Fab.
_AUTO = []   # Add to scene: (item id, window, area, region) until the chosen GLB is being added.


def add_to_scene(window, area, region, value):
    """One click: add the asset to the person's Fab library when it is not there (the click accepts its license, as the
    button says), then choose its GLB and add it to the scene (resume_pending carries it through)."""
    view = snapshot()
    item = selected(view)
    if not isinstance(value, (tuple, list)) or len(value) != 4 or area is None or region is None:
        return False
    if view.get('reason_code') == 'PURCHASE_REQUIRED':
        # Buy on Fab: that listing comes forward to buy; once it is theirs the read finishes and the add continues.
        if not lookup(window, value, True):
            return False
        _PAYING.add(value[2])
    elif item is None or item['id'] != value[2] or not matches(view, value):
        return False
    elif view.get('reason_code') == 'ACQUISITION_REQUIRED' and not lookup(window, value, True):
        return False
    _AUTO[:] = [(value[2], window, area, region)]
    return True


def _carry_add(view):
    """Main-thread tick for Add to scene: choose the GLB, then add it; any refusal ends it (its reason shows)."""
    item_id, window, area, region = _AUTO[0]
    item = selected(view)
    if view.get('lookup_status') == 'loading':
        return False
    _PAYING.discard(item_id)
    if (view.get('lookup_status') == 'error' or item is None or item['id'] != item_id
            or view.get('reason_code') not in (None, 'SELECTION_REQUIRED')):
        _AUTO.clear()
        return True
    chosen = choice(view, view.get('selected_variant_id'))
    if chosen is None:
        rows = [row for row in variants(view) if can_select(view, row)]
        _AUTO.clear() if not rows else None
        return bool(rows) and select(window, ticket(view, item_id, rows[0]['id']))
    if can_use(view):
        _AUTO.clear()
        return use(window, area, region, ticket(view, item_id, chosen['id']))
    return False


def working(view):
    """What Fab is busy with for the person, as the results space's spinner line; None when idle."""
    view = view or {}
    if view.get('status') in ('connecting', 'verifying') or (_PENDING and view.get('status') != 'connected'):
        return n_('Connecting to Fab…')
    if view.get('search_status') == 'searching' or (_PENDING and view.get('status') == 'connected'):
        return n_('Searching Fab…')
    if view.get('lookup_status') == 'loading':
        if _AUTO and _AUTO[0][0] in _PAYING:
            return n_('Finish buying it on Fab. Angelo adds it to your scene once it is yours…')
        return n_('Adding it to your Fab library…') if _AUTO else n_('Getting asset details…')
    if _AUTO:
        return n_('Adding it to your scene…')
    return None


def resume_pending():
    """Main-thread tick: the search asked for before connecting runs once Fab is connected; a failed or abandoned
    connection drops it (signing in first keeps it: Check sign-in then runs it)."""
    from . import library_host
    view = snapshot() or {}
    if _AUTO:
        return _carry_add(view)
    if _RECONNECT and view.get('status') == 'disconnected' and view.get('can_connect') is True:
        _RECONNECT.clear()
        request = library_host.fab_connect(None)
        return type(request) is int and request >= 0
    if not _PENDING:
        return False
    if view.get('status') == 'connected' and view.get('can_search') is True:
        query = _PENDING.pop()
        request = library_host.fab_search(None, query, limit=8)
        return type(request) is int and request >= 0
    if view.get('status') in ('error', 'unavailable', 'cleanup_required', 'disconnecting'):
        _PENDING.clear()
    return False


def lookup(window, value, show=False):
    """Read a result's listing again; ``show``: then bring that listing forward so the person can add it on Fab."""
    from . import library_host
    view = snapshot()
    if not matches(view, value) or not any(row.get('id') == value[2] for row in items(view)):
        return False
    request = library_host.fab_lookup(window, value[2], show)
    return type(request) is int and request >= 0


def select(window, value):
    from . import library_host
    view = snapshot()
    if (not matches(view, value) or not selected(view) or selected(view)['id'] != value[2]
            or not can_select(view, choice(view, value[3]))):
        return False
    return library_host.fab_select(window, value[2], value[3],
        expected_generation=value[0], expected_request_id=value[1]) is True


def use(window, area, region, value, *, at=None):
    from . import library_host
    view = snapshot()
    if (not matches(view, value) or not can_use(view) or view['selected_item_id'] != value[2]
            or view['selected_variant_id'] != value[3] or window is None or area is None or region is None):
        return False
    request = library_host.fab_use(window, area, region, value[2], value[3],
        expected_generation=value[0], expected_request_id=value[1], at=at)
    return type(request) is int and request >= 0


def connect_label(view):
    return n_('Check sign-in') if (view or {}).get('status') == 'sign_in_required' else n_('Connect Fab')


def preferences_section(preferences):
    view = snapshot()
    rows = fab_settings.section(preferences)['rows']
    buttons = []
    for action, field, label in [('fab_connect', 'can_connect', connect_label(view)),
                                 ('fab_disconnect', 'can_disconnect', n_('Disconnect'))]:
        if view and view.get(field) is True and epoch(view) is not None:
            buttons.append((iface_(label), action, connection_request(action, epoch(view)), 'secondary'))
    lines = [reason(view['reason_code'])] if view and view.get('reason_code') else []
    rows.insert(0, {'kind': 'connection', 'name': 'Fab', 'lines': lines, 'buttons': buttons,
                   'chip': (status(view), 'teal' if view and view.get('status') == 'connected' else 'muted',
                            bool(view and view.get('status') in ('connecting', 'verifying', 'disconnecting')))})
    return {'title': 'Fab', 'description': iface_('Find assets on Fab and review a version before adding it.'), 'rows': rows}


def draw_preferences(layout, preferences):
    """Native fallback uses the same cached facts and existing saved preference as the canvas page."""
    section = preferences_section(preferences)
    box = layout.box()
    box.label(text=section['title'], translate=False)
    row = section['rows'][0]
    box.label(text=row['chip'][0], translate=False)
    from .text_layout import label_rows
    for text in row['lines']:
        for line in label_rows(text):
            box.label(text=line, translate=False)
    for label, action, expected, _style in row['buttons']:
        operator = box.operator('angelo.fab_connection', text=label, translate=False)
        operator.request = expected
    box.prop(preferences, fab_settings.NAME)
