# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fab's cached library view. The existing overlay owns its window, clipping, input and source field.

Discovery opens details; only an explicitly chosen, current owner-bound version offers Use. Neither a click nor
an enqueued job manufactures connected/import-ready/completed state. No download or browser work runs in draw.
"""
from . import card_kit as kit, fab_ui, library_overlay_draw as look, text_layout, ui_tokens as tk
from .ui_text import iface_, n_

SOURCE_H, ROW_H = 32, 34


def adjust(rects, state, scale):
    """Insert a source row without overlapping the existing kind row, grid or footer."""
    if 'panel' not in rects:
        return rects
    rects = dict(rects)
    x, y, w, h = rects['chips']
    rects['sources'] = (x, y, w, h)
    rects['chips'] = (x, y - SOURCE_H * scale, w, h)
    x, y, w, h = rects['grid']
    rects['grid'] = (x, y, w, max(0.0, h - SOURCE_H * scale))
    return rects


def button(paint, state, rect, label, action, value=None, enabled=True, active=False):
    s = paint.s
    focused = state.get('fab_focus') == (action, value)
    if focused and enabled:
        paint.shape((rect[0] - 2 * s, rect[1] - 2 * s, rect[2] + 4 * s, rect[3] + 4 * s), tk.FOCUS, 8 * s)
    hover = state.get('hover') == (action, value)
    paint.shape(rect, kit.TEAL_SOFT if active else kit.PANEL_HOVER if hover and enabled else kit.PANEL, 6 * s)
    paint.text(label, rect[0] + 8 * s, rect[1] + rect[3] / 2 - 4 * s, 11,
               kit.TEXT if enabled else kit.MUTED, rect[2] - 16 * s)
    if enabled:
        look.hit(state, rect, action, value)


def sources(paint, state, rects):
    rect = rects.get('sources')
    if rect is None:
        return
    x, y, w, h = rect
    # i18n: product names, displayed without translation.
    s = paint.s
    for identity, label in [('all', iface_('All')), ('angelo', 'Angelo'), ('fab', 'Fab')]:
        if identity in ('all', 'fab') and state.get('lock') not in (None, 'model'):
            continue
        icon = 16 * s if identity != 'all' else 0.0   # Angelo's raven and Fab's mark before their names.
        width = paint.measure(label, 11) + 24 * s + (icon + 6 * s if icon else 0.0)
        active = state.get('source', 'all') == identity
        button(paint, state, (x, y, width, h), '', 'source', identity, active=active)
        if icon:
            _source_icon(paint, identity, x + 10 * s + icon / 2, y + h / 2, icon)
        paint.text(label, x + 12 * s + (icon + 6 * s if icon else 0.0), y + h / 2 - 4 * s, 11, kit.TEXT)
        x += width + 6 * s


def _source_icon(paint, identity, cx, cy, size):
    """Angelo: the raven (marble_draw.draw_still). Fab: a small dark tile with its initial, never a copied logo."""
    if identity == 'angelo':
        try:
            from . import marble_draw
            marble_draw.draw_still(cx, cy, size * 1.25)
        except Exception:   # A mark that cannot draw leaves the name alone.
            pass
        return
    paint.shape((cx - size / 2, cy - size / 2, size, size), (0.02, 0.02, 0.03, 1.0), 4 * paint.s)
    width = paint.measure('F', 10)
    paint.text('F', cx - width / 2, cy - 3.6 * paint.s, 10, (1.0, 1.0, 1.0, 1.0))


def _dots(paint, cx, cy, radius, dot):
    import math
    import time
    import bpy
    still = getattr(bpy.context.preferences.view, 'use_reduce_motion', False)
    head = 0 if still else int(time.monotonic() * 10) % 8
    for index in range(8):
        angle = math.radians(90 - index * 45)
        paint.shape((cx + radius * math.cos(angle) - dot / 2, cy + radius * math.sin(angle) - dot / 2, dot, dot),
                    tk.alpha(tk.TEAL, 1 - ((head - index) % 8) * 0.11), dot / 2)


def spinner(paint, rect, label):
    """A large teal spinner centred in ``rect`` with what is happening under it, in body text (never a faint line)."""
    x, y, w, h = rect
    s = paint.s
    cx, cy = x + w / 2, y + h / 2 + 18 * s
    _dots(paint, cx, cy, 18 * s, 6 * s)
    width = paint.measure(label, 13)
    paint.text(label, cx - width / 2, cy - 18 * s - 32 * s, 13, kit.TEXT, w)


def inline_spinner(paint, x, cy, label, width):
    """The footer's working state: a small spinner, then what is happening."""
    s = paint.s
    _dots(paint, x + 9 * s, cy, 7 * s, 3.2 * s)
    paint.text(label, x + 26 * s, cy - 5 * s, 12, kit.TEXT, width - 26 * s)


def all_rows(query):
    """All's Fab tiles: this query's Fab results as library tiles (``source`` fab: a Fab badge, its own details)."""
    view = fab_ui.snapshot() or {}
    if not query.strip() or view.get('query') != query:
        return []
    return [dict(id=row['id'], title=row['title'], kind='model', source='fab', thumb=row.get('thumb'),
                 price=row.get('price'), paid=row.get('paid')) for row in fab_ui.items(view)]


def all_status():
    """All's footer while Fab works for this search: ``(fraction, label)`` for the library's progress bar."""
    busy = fab_ui.working(fab_ui.snapshot())
    return (0.35, iface_(busy)) if busy else None


def cover_with_spinner(paint, grid, label):
    """All with no Angelo match yet while Fab searches: the spinner instead of an empty-results line."""
    paint.shape(grid, kit.CARD, 0)
    spinner(paint, grid, label)


def open_from_all(window, state, item_id):
    """A Fab tile in All: Fab's details for it, Back returns to All."""
    view = fab_ui.snapshot()
    if fab_ui.lookup(window, fab_ui.ticket(view, item_id)):
        state.update(source='fab', fab_detail=True, fab_requested_item=item_id, fab_scroll=0.0, fab_from_all=True)


def price_badge(paint, inner, row):
    """The card's shown price ("Free", "$12.99") in a pill at the picture's top left: paid ones in amber."""
    price = row.get('price')
    if not price:
        return
    s, label = paint.s, (iface_('Free') if price == 'Free' else price)
    w, h = paint.measure(label, 10) + 12 * s, 18 * s
    x, y = inner[0] + 6 * s, inner[1] + inner[3] - h - 6 * s
    paint.shape((x, y, w, h), tk.alpha(tk.AMBER if row.get('paid') else tk.TEAL, 0.9), h / 2)
    paint.text(label, x + 6 * s, y + 5 * s, 10, (0.05, 0.05, 0.06, 1.0), w)


def _text(paint, text, x, top, width, size=11, color=None):
    rows = text_layout.two_lines(text, lambda part: paint.measure(part, size), width)
    if rows is None:
        return None
    for index, row in enumerate(rows):
        paint.text(row, x, top - index * (size + 4) * paint.s, size, color or kit.TEXT, width)
    return len(rows) * (size + 4) * paint.s


def draw_panel(paint, state, rects):
    view = fab_ui.snapshot()
    look.draw_frame(paint, rects['panel'])
    x, y, w, h = rects['chips']
    right = x + w
    query = state.get('query', '')
    search_w = paint.measure(iface_('Search'), 11) + 24 * paint.s
    button(paint, state, (right - search_w, y, search_w, h), iface_('Search'), 'fab_search', fab_ui.epoch(view),
           enabled=fab_ui.can_start(view) and bool(query.strip()) and query.isprintable())
    right -= search_w + 6 * paint.s
    # Only the status here: Search connects (or resumes a kept sign-in) itself and Disconnect lives in
    # Preferences; the one button is Check sign-in, while Fab waits for the person to finish signing in.
    for action, flag, label in [('fab_connect', 'can_connect', iface_(fab_ui.connect_label(view)))]:
        if (view and view.get(flag) is True and view.get('status') == 'sign_in_required'
                and fab_ui.epoch(view) is not None):
            width = paint.measure(label, 11) + 24 * paint.s
            value = fab_ui.connection_request(action, fab_ui.epoch(view))
            button(paint, state, (right - width, y, width, h), label, action, value)
            right -= width + 6 * paint.s
    if fab_ui.expired(view):
        label = iface_('Sign in to Fab')
        width = paint.measure(label, 11) + 24 * paint.s
        button(paint, state, (right - width, y, width, h), label, 'fab_signin', fab_ui.epoch(view), active=True)
        right -= width + 6 * paint.s
    paint.text(fab_ui.status(view), x, y + 8 * paint.s, 11, kit.MUTED, max(0, right - x))
    rects = _reason_region(paint, state, rects, view)
    if state.get('fab_detail'):
        _details(paint, state, rects, view)
    else:
        _results(paint, state, rects['grid'], view)
        if not fab_ui.working(view) and fab_ui.items(view):
            _text(paint, iface_('Choose an asset to see its details and add it to your scene.'),
                  rects['footer'][0] + 12 * paint.s, rects['footer'][1] + 30 * paint.s,
                  rects['footer'][2] - 24 * paint.s, color=kit.MUTED)


def _reason_region(paint, state, rects, view):
    """Current failure facts remain visible even while the owner retains results or a selected version."""
    code = (view or {}).get('reason_code')
    if not code or (code == 'ACQUISITION_REQUIRED' and state.get('fab_detail')):
        return rects   # Adding it to the library is the details footer's own action; no second message above.
    x, y, w, h = rects['grid']
    text = fab_ui.reason(code)
    used = _text(paint, text, x, y + h - 15 * paint.s, w, color=kit.MUTED)
    # A narrow view cannot display the reason and the mandatory review facts: withhold review/use hits.
    if used is None:
        paint.text(text, x, y + h - 15 * paint.s, 11, kit.MUTED, w)
        used = h
    reserve = min(h, used + 18 * paint.s)
    adjusted = dict(rects)
    adjusted['grid'] = (x, y, w, max(0.0, h - reserve))
    return adjusted


def _results(paint, state, rect, view):
    x, y, w, h = rect
    s, top = paint.s, y + h - 18 * paint.s
    found = fab_ui.items(view)
    state['shown'] = [row['id'] for row in found]
    busy = fab_ui.working(view)
    if busy and (not found or (view or {}).get('search_status') == 'searching' or fab_ui._PENDING):
        spinner(paint, rect, iface_(busy))
        return
    if not found:
        _text(paint, iface_('Type what you need, then press Enter.'), x, top, w, color=kit.MUTED)
        return
    heading = iface_('Results for “{query}”', query=view.get('query', ''))
    paint.text(heading, x, top, 11, kit.MUTED, w)
    grid = (x, y, w, max(0.0, top - 10 * s - y))
    # The Angelo library's tiles: each result's own Fab picture above its title, so a person sees what it is.
    scroll = max(0.0, min(state.get('scroll', 0.0), max(0.0, look.content_height(grid[2], len(found), s) - grid[3])))
    state['scroll'] = scroll
    _tiles(paint, state, grid, found, scroll, view)


def _tiles(paint, state, grid, found, scroll, view):
    import gpu
    from gpu_extras.presets import draw_texture_2d
    from . import library_host
    s, library = paint.s, library_host.library()
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(int(state.get('region_x', 0) + grid[0]), int(state.get('region_y', 0) + grid[1]),
                          int(grid[2]), int(grid[3]))
    try:
        for index, rect in look.tile_rects(grid, len(found), scroll, s):
            row, value = found[index], fab_ui.ticket(view, found[index]['id'])
            hovered = state.get('hover') == ('fab_lookup', value)
            image = (rect[0], rect[1] + look.TITLE_H * s, rect[2], rect[3] - look.TITLE_H * s)
            paint.shape(image, kit.LINE_HOVER if hovered else kit.LINE, 9 * s)
            inner = (image[0] + s, image[1] + s, image[2] - 2 * s, image[3] - 2 * s)
            paint.shape(inner, kit.PANEL_HOVER if hovered else kit.PANEL, 8 * s)
            texture = library.texture(row) if row.get('thumb') else None
            if texture is not None:
                fit = min(inner[2] / texture.width, inner[3] / texture.height)
                tw, th = texture.width * fit, texture.height * fit
                gpu.state.blend_set('ALPHA')
                draw_texture_2d(texture, (inner[0] + (inner[2] - tw) / 2, inner[1] + (inner[3] - th) / 2), tw, th)
            price_badge(paint, inner, row)
            paint.text(row['title'], rect[0] + 2 * s, rect[1] + 10 * s, 11, kit.TEXT if hovered else kit.MUTED,
                       rect[2] - 4 * s)
            top = min(rect[1] + rect[3], grid[1] + grid[3])
            look.hit(state, (rect[0], max(rect[1], grid[1]), rect[2], top - max(rect[1], grid[1])), 'fab_lookup', value)
    finally:
        gpu.state.scissor_test_set(False)


def _paid_details(paint, state, rects, view, row, top):
    """A paid listing the person does not own: its picture, title and shown price, then Buy on Fab, which brings that
    listing forward to buy (Angelo never pays) and adds it to the scene by itself once Fab shows it as theirs."""
    x, y, w, h = rects['grid']
    s = paint.s
    top = _picture(paint, state, x, top, w, row, view)
    paint.text(row['title'], x, top, 12, kit.TEXT, w)
    top -= 22 * s
    if row.get('price'):
        paint.text(iface_('Price on Fab: {price}', price=row['price']), x, top, 11, kit.TEXT, w)
        top -= 20 * s
    fx, fy, fw, fh = rects['footer']
    if fab_ui._AUTO:
        inline_spinner(paint, fx + 12 * s, fy + fh / 2, iface_(PAYING), fw - 24 * s)
        return
    label = iface_('Buy on Fab')
    width = paint.measure(label, 11) + 28 * s
    _text(paint, iface_(BUY_NOTE), fx + 12 * s, fy + fh - 18 * s, fw - width - 42 * s, color=kit.MUTED)
    button(paint, state, (fx + fw - width - 12 * s, fy + (fh - 30 * s) / 2, width, 30 * s), label, 'fab_add_scene',
           fab_ui.ticket(view, row['id']), active=True)


BUY_NOTE = n_("Opens it on Fab to buy. Angelo adds it to your scene once it's yours.")
PAYING = n_('Finish buying it on Fab. Angelo adds it to your scene once it is yours…')


def _details(paint, state, rects, view):
    x, y, w, h = rects['grid']
    s, top = paint.s, y + h
    button(paint, state, (x, top - 28 * s, 90 * s, 26 * s), iface_('Back'), 'fab_back')
    top -= 48 * s
    item = fab_ui.selected(view)
    cached = state.get('fab_last_item')
    if (item is None and cached and cached['id'] == state.get('fab_requested_item')
            and (fab_ui._AUTO or state.get('progress')) and (view or {}).get('lookup_status') == 'loading'):
        item = cached   # Adding it: its details stay while the library add runs (the footer shows the work).
    if item is not None and item['id'] == state.get('fab_requested_item'):
        state['fab_last_item'] = item
    paid = next((r for r in fab_ui.items(view) if r['id'] == state.get('fab_requested_item')), None)
    if item is None and paid is not None and (fab_ui._AUTO or (view or {}).get('reason_code') == 'PURCHASE_REQUIRED'):
        return _paid_details(paint, state, rects, view, paid, top)   # Not a dead end: its price and Buy on Fab.
    if item is None or item['id'] != state.get('fab_requested_item'):
        if view and view.get('lookup_status') == 'loading':
            spinner(paint, (x, y, w, max(0.0, top - y)), iface_('Getting asset details…'))
        elif not (view or {}).get('reason_code'):
            _text(paint, iface_('Choose an asset to view its details.'), x, top, w, color=kit.MUTED)
        return
    top = _picture(paint, state, x, top, w, item, view)
    paint.text(item['title'], x, top, 12, kit.TEXT, w)
    top -= 20 * s
    if item.get('author'):
        paint.text(iface_('by {author}', author=item['author']), x, top, 11, kit.MUTED, w)
        top -= 20 * s
    top -= 5 * s
    facts = [iface_('License: {license}', license=item.get('license') or iface_('Not confirmed')),
             iface_('Access: {access}', access=iface_(fab_ui.ACCESS.get(item.get('access'), fab_ui.ACCESS['unverified'])))]
    review_visible = True
    for fact in facts:
        rows = text_layout.two_lines(fact, lambda part: paint.measure(part, 11), w)
        height = len(rows) * 15 * s if rows is not None else h + s
        if top - height < y:
            review_visible = False
            break
        used = _text(paint, fact, x, top, w)
        if used is None:
            review_visible = False
            break
        top -= used + 3 * s
    if not review_visible:
        _text(paint, iface_('Enlarge the library to review this version.'), x, y + 30 * s, w, color=kit.MUTED)
        return  # Never offer Use when required review facts could not be displayed.
    top -= 8 * s
    rows = fab_ui.variants(view)
    available = max(0.0, top - y)
    row_h = ROW_H * s
    scroll = max(0.0, min(state.get('fab_scroll', 0.0), max(0.0, len(rows) * row_h - available)))
    state['fab_scroll'] = scroll
    for index, variant in enumerate(rows):
        ry = top - (index + 1) * row_h + scroll
        if ry < y or ry + row_h > top:
            continue
        size = variant.get('size_bytes')
        label = '%s · %s%s' % (variant['label'], variant.get('format', '').upper(),
                               ' · ' + look.human_bytes(size) if type(size) is int and size > 0 else '')
        button(paint, state, (x, ry, w, row_h - 3 * s), label, 'fab_select',
               fab_ui.ticket(view, item['id'], variant['id']), enabled=fab_ui.can_select(view, variant),
               active=view.get('selected_variant_id') == variant['id'])
    _use_footer(paint, state, rects['footer'], view, review_visible)


def _picture(paint, state, x, top, w, item, view):
    """The listing's own Fab picture, as large as the panel allows above the facts (none when Fab showed none)."""
    import gpu
    from gpu_extras.presets import draw_texture_2d
    from . import library_host
    thumb = item.get('thumb') or next((r.get('thumb') for r in fab_ui.items(view) if r.get('id') == item['id']), None)
    texture = library_host.library().texture({'thumb': thumb}) if thumb else None
    if texture is None:
        return top
    s = paint.s
    box_h = min(160 * s, w * 9 / 16)
    fit = min(w / texture.width, box_h / texture.height)
    tw, th = texture.width * fit, texture.height * fit
    paint.shape((x, top - box_h, w, box_h), kit.PANEL, 8 * s)
    gpu.state.blend_set('ALPHA')
    draw_texture_2d(texture, (x + (w - tw) / 2, top - box_h + (box_h - th) / 2), tw, th)
    return top - box_h - 18 * s


ADDING = n_('Adding it to your scene…')
LICENSE_NAMES = {'Fab Standard': n_('the Fab Standard License'), 'CC-BY-4.0': n_('the CC BY 4.0 license')}


def _use_footer(paint, state, rect, view, review_visible):
    """One action: Add to scene. Not in the person's Fab library yet: the click also adds it there, accepting its
    license as the note says (Angelo presses Fab's own Add to My Library behind it); then its GLB is added."""
    x, y, w, h = rect
    s, item = paint.s, fab_ui.selected(view) or state.get('fab_last_item')
    if item is None:
        return
    progress = state.get('progress')
    if fab_ui._AUTO or progress or (state.get('fab_adding') == item['id'] and view.get('lookup_status') == 'loading'):
        busy = fab_ui.working(view) or ADDING
        label = progress[1] if progress and not fab_ui._AUTO else iface_(busy)
        inline_spinner(paint, x + 12 * s, y + h / 2, label, w - 24 * s)
        return
    import time
    notice = state.get('notice')
    if notice and time.monotonic() - notice[1] < 4.0:   # The add's outcome ("Added …"), in body text.
        _text(paint, notice[0], x + 12 * s, y + h - 18 * s, w - 24 * s)
    code = view.get('reason_code')
    if code not in (None, 'ACQUISITION_REQUIRED', 'SELECTION_REQUIRED'):
        return   # Its reason is shown above the details.
    variant = fab_ui.choice(view, view.get('selected_variant_id')) or next(
        (row for row in fab_ui.variants(view) if fab_ui.can_select(view, row)), None)
    if code == 'ACQUISITION_REQUIRED':
        note = iface_('Also adds it to your Fab library, accepting {license}.',
                      license=iface_(LICENSE_NAMES.get(item.get('license'), n_('its Fab license'))))
    elif notice and time.monotonic() - notice[1] < 4.0:
        note = None
    else:
        size = (variant or {}).get('size_bytes')
        note = (iface_('{size} download', size=look.human_bytes(size)) if type(size) is int and size > 0
                else None) if variant else iface_('No version can be added.')
    label = iface_('Add to scene')
    width = paint.measure(label, 11) + 28 * s
    if note:
        _text(paint, note, x + 12 * s, y + h - 18 * s, w - width - 42 * s, color=kit.MUTED)
    button(paint, state, (x + w - width - 12 * s, y + (h - 30 * s) / 2, width, 30 * s), label, 'fab_add_scene',
           fab_ui.ticket(view, item['id']), active=True,
           enabled=review_visible and (code == 'ACQUISITION_REQUIRED' or variant is not None))


def pill(area, state, action):
    """Opening Fab's cached panel never constructs the unrelated Angelo library engine."""
    if action != 'pill':
        return
    if state.get('mode') is None:
        state.update(mode='search', caret=len(state.get('query', '')))
    state['focused'] = True
    area.tag_redraw()


def click(window, area, region, state, action, value):
    """Only presentation state changes locally. The facade owns account, metadata, choice and import readiness."""
    if action == 'source':
        if value not in ('all', 'angelo', 'fab') or (value != 'angelo' and state.get('lock') not in (None, 'model')):
            return False
        state.update(source=value, selected=None, scroll=0.0, fab_scroll=0.0, fab_detail=False, focused=True, hits=[])
        state.pop('fab_focus', None)
        return True
    if action == 'fab_back':
        state.update(fab_detail=False, fab_scroll=0.0)
        if state.pop('fab_from_all', False):
            state['source'] = 'all'
        return True
    if action in fab_ui.CONNECTION_ACTIONS:
        return fab_ui.run_connection_request(window, value)
    if action == 'fab_search':
        accepted = fab_ui.search(window, state.get('query', ''), value)
        if accepted:
            state.update(fab_detail=False, scroll=0.0)
        return accepted
    if action == 'fab_lookup':
        accepted = fab_ui.lookup(window, value)
        if accepted:
            state.update(fab_detail=True, fab_requested_item=value[2], fab_scroll=0.0)
        return accepted
    if action == 'fab_signin':
        return fab_ui.sign_in_again(window, value)
    if action == 'fab_add_scene':
        accepted = (state.get('fab_detail', False) and isinstance(value, (tuple, list)) and len(value) == 4
                    and state.get('fab_requested_item') == value[2]
                    and fab_ui.add_to_scene(window, area, region, value))
        if accepted:
            state['fab_adding'] = value[2]
        return accepted
    if action == 'fab_select':
        return (isinstance(value, (tuple, list)) and len(value) == 4
                and state.get('fab_detail', False) and state.get('fab_requested_item') == value[2]
                and fab_ui.select(window, value))
    if action == 'fab_use':
        return (isinstance(value, (tuple, list)) and len(value) == 4 and state.get('fab_detail', False) and state.get('fab_requested_item') == value[2]
                and fab_ui.use(window, area, region, value))
    return False
