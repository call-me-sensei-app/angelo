# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed public Fab presentation records; no paths, URLs, account scope or browser handles."""
import copy
import uuid

from .fab_catalog import FabError, MAX_RESULTS, MAX_VARIANTS, thumb_url
from .fab_formats import BY_CODE, MAX_DOWNLOAD, RANK


def initial(generation=None, *, available=False):
    return dict(version=1, generation=generation, request_id=0,
                status='disconnected' if available else 'unavailable',
                can_connect=available, can_disconnect=False, can_search=False,
                reason_code=None if available else 'LIVE_CONNECTOR_UNAVAILABLE',
                search_status='idle', query='', items=[], lookup_status='idle',
                selected_item_id=None, selected_item=None, selected_variant_id=None,
                variants=[], import_ready=False)


def public(value):
    return copy.deepcopy(value)  # The owner stores only the bounded primitive records below.


def identity(value):
    try:
        if type(value) is str and value.startswith('fab:') and str(uuid.UUID(value[4:])) == value[4:]:
            return value
    except ValueError:
        pass
    raise FabError('INVALID_METADATA')


def label(value):
    if type(value) is not str or not 0 < len(value) <= 120 or any(ord(c) < 32 for c in value):
        raise FabError('INVALID_METADATA')
    return value


def version_label(value):
    # BrowserChoice's actual sanitized basename budget is 255 UTF8 bytes; it is
    # not the catalog title's 120-character bound. No truncation changes a choice.
    if (type(value) is not str or not value or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(0xD800 <= ord(c) <= 0xDFFF for c in value) or len(value.encode('utf8')) > 255):
        raise FabError('INVALID_METADATA')
    return value


def discoveries(result):
    rows = result.get('items') if type(result) is dict else None
    if type(rows) is not list or len(rows) > MAX_RESULTS:
        raise FabError('INVALID_METADATA')
    found, seen = [], set()
    for row in rows:
        if type(row) is not dict:
            raise FabError('INVALID_METADATA')
        item_id = identity(row.get('id'))
        if item_id in seen:
            raise FabError('INVALID_METADATA')
        seen.add(item_id)
        entry = dict(id=item_id, title=label(row.get('title')), source='fab', access='unverified', import_ready=False)
        if thumb_url(row.get('thumb')):
            entry['thumb'] = row['thumb']   # Drawn on the result card (library_host.texture); never a listing fact.
        from .fab_browser_listing_page import price_text
        if price_text(row.get('price')):   # The card's shown price: display only, never an offer or entitlement.
            entry.update(price=row['price'], paid=row['price'] != 'Free')
        found.append(entry)
    return found


def lookup(result):
    if type(result) is not dict or type(result.get('item')) is not dict:
        raise FabError('INVALID_METADATA')
    item = result['item']
    access = item.get('access')
    access = access if access in ('owned', 'free', 'purchase_required') else 'unverified'
    entitlement = result.get('entitlement')
    if entitlement == 'acquisition_required':
        access = 'acquisition_required'
    if result.get('download_permission') not in ('available', 'terms_required', 'unknown'):
        raise FabError('INVALID_METADATA')
    ready = (result.get('import_ready') is True and result['download_permission'] == 'available'
             and entitlement == 'download_available' and access in ('owned', 'free'))
    license_name = item.get('license')
    if license_name not in ('Fab Standard', 'CC-BY-4.0'):
        license_name = None
        ready = False
    selected = dict(id=identity(item.get('id')), title=label(item.get('title')), license=license_name,
                    access=access, import_ready=ready)
    author = item.get('author')
    if type(author) is str and 0 < len(author) <= 120 and author.isprintable():
        selected['author'] = author   # Fab's visible publisher label, shown as "by …".
    if thumb_url(item.get('thumb')):
        selected['thumb'] = item['thumb']
    variants = item.get('variants')
    if type(variants) is not list or not 1 <= len(variants) <= MAX_VARIANTS:
        raise FabError('INVALID_METADATA')
    choices, seen = [], set()
    for row in variants:
        if type(row) is not dict or type(row.get('id')) is not str or not 0 < len(row['id']) <= 128:
            raise FabError('INVALID_METADATA')
        if row['id'] in seen or row.get('format') not in BY_CODE:
            raise FabError('INVALID_METADATA')
        seen.add(row['id'])
        display = row.get('label') or row.get('quality')
        size = row.get('bytes')
        if size is not None and (type(size) is not int or not 0 < size <= MAX_DOWNLOAD):
            raise FabError('INVALID_METADATA')
        choices.append(dict(id=row['id'], label=version_label(display), format=row['format'],
                            kind=BY_CODE[row['format']].kind, supported=True,
                            reason_code=None, size_bytes=size, maximum_bytes=MAX_DOWNLOAD))
    # Add to scene takes the first choice. A listing with a texture set is a surface or decal (Fab gives models none:
    # 2026-11-11 sample), so its material comes first; otherwise the cheapest path (a converted GLB imports without
    # the converter), then the format table's order. Within a format, the listing's own order.
    choices.sort(key=lambda choice: (choice['kind'] != 'material', RANK[choice['format']]))
    # What the person can act on comes first: buy it, or add it to their Fab library.
    reason = (None if ready else 'LICENSE_UNVERIFIED' if result['download_permission'] == 'terms_required'
              else 'PURCHASE_REQUIRED' if entitlement == 'purchase_required'
              else 'ACQUISITION_REQUIRED' if entitlement == 'acquisition_required'
              else 'DOWNLOAD_PERMISSION_UNKNOWN' if result['download_permission'] != 'available' else 'ACCOUNT_UNVERIFIED')
    return selected, choices, reason
