# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""In-app fixtures of test_blender_rail_positions (1.0.6): stand-in workers, what the window really drew, captures,
what stands over the 3D view, the minimized button found by its own hit test, and a job's state.

Stand-in workers are chats bound as worker sessions with no worker process and no provider, as
test_blender_rail_crowd's (the rail draws the same tiles from the same state). Nothing here forces a draw: ``Probe``
is a POST_PIXEL callback after Angelo's own that records each real draw of the rail's view, which plan the rail laid
out in it and what the minimized button was drawn with. Runs inside the app only (imports ``bpy``).
"""

import inspect
import time
from types import SimpleNamespace

import bpy

import rail_positions_checks as geometry


def window_region(window):
    """``(area, region)``: the 3D view the rail stands in and its main region (``session_rail_input.rail_region``)."""
    from angelo_agent import live_view_draw, session_rail_input
    area = live_view_draw.target(window)
    return area, (session_rail_input.rail_region(area) if area is not None else None)


class Probe:
    """Every real draw of the rail's view, after Angelo's own POST_PIXEL callbacks (added later, so drawn later)."""

    def __init__(self, window):
        from angelo_agent import session_rail_draw
        self.window, self.frames, self.capturing, self._count = window, [], False, 0
        self._plan, self._tiles, self._circles, self._circle_args = None, 0, 0, None
        self.handler = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), 'WINDOW', 'POST_PIXEL')
        original_tile = session_rail_draw._tile

        def tile(*args, **kwargs):
            self._tiles += self._own_draw()
            return original_tile(*args, **kwargs)
        session_rail_draw._tile = tile
        self.circle_module = None
        try:
            from angelo_agent import rail_minimized
        except ImportError as error:   # Lane L2 not merged: every circle check says so.
            self.circle_error = repr(error)
            return
        self.circle_module, self.circle_error = rail_minimized, None
        original_circle = rail_minimized.draw
        signature = inspect.signature(original_circle)

        def circle(*args, **kwargs):
            if self._own_draw():
                self._circles += 1
                try:
                    self._circle_args = dict(signature.bind(*args, **kwargs).arguments)
                except TypeError:
                    self._circle_args = {"args": args, "kwargs": kwargs}
            return original_circle(*args, **kwargs)
        rail_minimized.draw = circle

    def _own_draw(self):
        """1 for a draw in the probed window and not a capture's (a capture draws the rail too, before a change the
        next real frame shows: its tiles must not count as that frame's), else 0."""
        window = bpy.context.window
        return int(not self.capturing and window is not None and window.as_pointer() == self.window.as_pointer())

    def _draw(self):
        from angelo_agent import session_rail_draw
        context = bpy.context
        if self.capturing or context.window is None or context.window.as_pointer() != self.window.as_pointer():
            return
        area, region = window_region(self.window)
        if area is None or context.area != area or context.region != region:
            return
        placed = session_rail_draw.last.get(self.window.as_pointer())
        plan = placed[1] if placed and placed[0] == area.as_pointer() else None
        # A panel laid out in this draw: a new plan with tiles (minimized, the rail records an empty Layout).
        laid_out = plan is not None and plan is not self._plan and bool(plan.tiles)
        self._plan = plan if plan is not None else self._plan   # Held: a freed plan's address could come back.
        args = self._circle_args
        self._count += 1   # Monotonic: the list keeps its newest 2000 only, so its length stops counting.
        self.frames.append(SimpleNamespace(n=self._count, at=time.monotonic(), plan=plan if laid_out else None,
                                           tiles=self._tiles, circle=args if self._circles else None,
                                           size=(region.width, region.height)))
        self._tiles, self._circles, self._circle_args = 0, 0, None
        del self.frames[:-2000]

    def mark(self):
        return self.frames[-1].n if self.frames else 0

    def since(self, mark):
        return [frame for frame in self.frames if frame.n > mark]

    def release(self):
        try:
            bpy.types.SpaceView3D.draw_handler_remove(self.handler, 'WINDOW')
        except (ValueError, RuntimeError):
            pass


def capture(probe, out, name, report):
    """The window as it draws now, rail and live view included (``live_view.window_pixels``, Angelo's offscreen
    read: a window behind others is not presented on macOS). The probe ignores this draw."""
    from angelo_agent import live_view, view_editor
    probe.capturing = True
    try:
        width, height, pixels = live_view.window_pixels(probe.window)
    finally:
        probe.capturing = False
    (out / name).write_bytes(view_editor.encode_png(pixels, width, height, bottom_up=True))
    report['shots'].append(name)


def redraw(window):
    from angelo_agent import live_windows
    for area in live_windows.areas(window):
        area.tag_redraw()


def bind(lead, workers, count):
    """``count`` more stand-in workers bound to the rail, each with a plan of its own (its tile's progress)."""
    from angelo_agent import parallel_sessions, rail_pictures, ui
    for _ in range(count):
        workers.append(ui.create_session().state['id'])
    ui.show_session(lead.state['id'])
    for number, session in enumerate(workers, start=1):
        if session in parallel_sessions.sessions():
            continue
        parallel_sessions.bound(ui._controllers[session], SimpleNamespace(worker=None, part_layout=None))
        parallel_sessions.names[session] = 'Part %d' % number
        parallel_sessions.note_progress(session, number % 4, ['Block out', 'Shape', 'Detail', 'Finish'])
    rail_pictures.ensure_timer()


def unbind(window, workers, keep):
    """Every stand-in worker after the first ``keep`` leaves the rail (its tile dismissed)."""
    from angelo_agent import session_rail_input
    for session in workers[keep:]:
        session_rail_input.remove(session, window)
    del workers[keep:]


def ask_approval(session):
    """A pending approval on ``session``'s chat, as a script card waits (display only: never run, never expires
    during the run; the chat asks for every change)."""
    from angelo_agent import ui
    from angelo_agent.controller import ToolCall
    controller = ui._controllers[session]
    controller.state['options'] = dict(controller.state.get('options') or {}, approval='ASK')
    call = ToolCall(session + '/rail-qa-approval', 'angelo_execute_python',
                    {'description': 'Rail QA approval', 'code': "print('rail positions fixture')"},
                    ui.document_key(), approval=True)
    call.expires = time.monotonic() + 3600
    controller.pending[call.id] = call
    return call.id


def clear_approval(session, call_id):
    from angelo_agent import ui
    ui._controllers[session].pending.pop(call_id, None)


def waiting(order):
    from angelo_agent import ui
    return [s for s in order if s in ui._controllers
            and any(getattr(call, 'approval', None) for call in ui._controllers[s].pending.values())]


def job_state(window, lead, order):
    """What a job is: every chat's turn, calls and rows, the rail's bound sessions, their plans and ends, the queue,
    the worker parts, the window's scene and the lead's document. A tile click changes none of it."""
    from angelo_agent import parallel_sessions, ui, worker_queue, worker_sessions
    chats = {}
    for session in order:
        controller = ui._controllers.get(session)
        if controller is not None:
            chats[session] = (bool(controller.busy), sorted(controller.pending),
                              len(controller.state.get('messages') or ()), repr(getattr(controller, 'turn_outcome', None)),
                              repr(controller.state.get('options')))
    return {'chats': chats, 'sessions': list(parallel_sessions.sessions()),
            'ended': sorted(getattr(parallel_sessions, '_ended', ())),
            'progress': {s: parallel_sessions.progress_of(s) for s in order},
            'names': dict(parallel_sessions.names), 'merged': [s for s in order if parallel_sessions.is_merged(s)],
            'queue': [(i.get('number'), i.get('kind')) for i in getattr(worker_queue, 'queue', ())],
            'parts': sorted(getattr(worker_sessions, '_parts', {})), 'scene': window.scene.name_full,
            'document': repr(getattr(lead, 'document', None))}


def label_width(window, session):
    """The widest the live view's label can be for ``session`` (its three lines, ``live_view_draw._draw_chip``)."""
    from angelo_agent import chat_view, parallel_sessions, ui_tokens as tk
    from angelo_agent.ui_text import iface_
    scale = chat_view.ui_scale()
    name = parallel_sessions.names.get(session) or iface_("Worker", "Angelo")
    lines = (iface_("Live view · {name} · nothing saved yet", name=name),
             iface_("Read-only: {name} edits its part. Ask it in its chat.", name=name),
             iface_("Live view · {name} · Solid, simplified · look only, steer it in its chat", name=name))
    return max(chat_view.measure(line, tk.TYPE_SMALL * scale, scale=1.0) for line in lines) + 28 * scale


def obstacles(window, live=None):
    """``([(kind, rect)], readings)`` in the rail region's pixels: everything the panel and the button stay clear of.
    The regions over the view, the navigation gizmo column (the product's ``rail_minimized.navigation_rect``, or
    Blender's own placement where the product has none), the asset shelf's Media Studio and Generation Flows pills,
    Angelo in the corner and, while ``live`` (a worker session) shows, the live view's label."""
    from angelo_agent import asset_shelf_add, chat_launcher
    area, region = window_region(window)
    found = geometry.view_obstacles(area.regions, region)
    preferences = bpy.context.preferences
    scale = preferences.system.ui_scale
    own = geometry.navigation_gizmo(area.regions, region, preferences.view, scale, area.spaces.active)
    product, error = None, None
    try:
        from angelo_agent import rail_minimized
        if hasattr(rail_minimized, 'navigation_rect'):   # The view as drawn now (its preferences, toggles, locks).
            product = rail_minimized.navigation_rect(area, region)
        else:
            product = rail_minimized.gizmo_rect(region, preferences.view, scale)
    except Exception as failure:   # noqa: BLE001 - lane L2 not merged or failing: said; Blender's own still counts.
        error = repr(failure)
    product = tuple(product) if product is not None else None
    gizmo = product if error is None else own   # The product's, checked against Blender's own (``gizmo_rules``).
    if gizmo is not None:
        found.append(('navigation_gizmo', gizmo))
    pills = asset_shelf_add.places.get(area.as_pointer())
    for action, rect in (pills[1] if pills and pills[0] == window.as_pointer() else ()):
        found.append(('shelf_pill_' + action, (rect[0] - region.x, rect[1] - region.y, rect[2], rect[3])))
    corner = chat_launcher._places.get(window.as_pointer())
    if corner and corner[0] == area.as_pointer() and window.as_pointer() in chat_launcher._shown:
        _area, cx, cy, radius = corner
        found.append(('angelo_in_the_corner', (cx - region.x - radius, cy - region.y - radius, 2 * radius, 2 * radius)))
    if live is not None:
        found.append(('live_view_label', geometry.label_rect(region.width, region.height, found,
                                                             label_width(window, live), scale)))
    readings = {'gizmo_blender': own, 'gizmo_product': product, 'gizmo_product_error': error, 'ui_scale': scale,
                'kinds': sorted({kind for kind, _rect in found})}
    return found, readings


def find_circle(window):
    """``((cx, cy, radius) in rail-region pixels, frame)`` of the minimized button, found by asking its own hit test
    (``rail_minimized.hit``) across the whole view: window pixels first, then region pixels; ``(None, None)`` when
    no point is on it (or the module is missing)."""
    try:
        from angelo_agent import rail_minimized
    except ImportError:
        return None, None
    area, region = window_region(window)
    scale = bpy.context.preferences.system.ui_scale
    coarse, step = max(2, int(round(5 * scale))), max(1, int(scale // 2))   # A fine step well inside the size rule.
    for frame, (ox, oy) in (('window', (region.x, region.y)), ('region', (0, 0))):
        points = [(x, y) for x in range(0, region.width, coarse) for y in range(0, region.height, coarse)
                  if rail_minimized.hit(window.as_pointer(), ox + x, oy + y)]
        if not points:
            continue
        x0, x1 = min(p[0] for p in points) - coarse, max(p[0] for p in points) + coarse
        y0, y1 = min(p[1] for p in points) - coarse, max(p[1] for p in points) + coarse
        fine = [(x, y) for x in range(max(0, x0), min(region.width, x1 + 1), step)
                for y in range(max(0, y0), min(region.height, y1 + 1), step)
                if rail_minimized.hit(window.as_pointer(), ox + x, oy + y)]
        return geometry.circle_from_hits(fine), frame
    return None, None


def shelf(window, shown):
    """Show (expanded to its rows) or hide the 3D view's Asset Shelf, as View > Asset Shelf and its chevron do."""
    from angelo_agent import media_shelf
    area, _region = window_region(window)
    area.spaces.active.show_region_asset_shelf = shown
    if shown and hasattr(media_shelf, 'set_collapsed'):
        try:
            media_shelf.set_collapsed(window, area, False)
        except Exception as error:   # noqa: BLE001 - a binary that cannot collapse: shown as it is.
            return repr(error)
    area.tag_redraw()
    return None


def chat_area(window):
    from angelo_agent import console, live_windows
    return next((a for a in live_windows.areas(window) if console.is_chat(a)), None)


def split_view(window, share=0.45):
    """A short view, as a person makes one: the rail's 3D view split top and bottom (``screen.area_split``), so the
    larger part, the one the rail stands in, is about ``1 - share`` of its height. Returns the new area's pointer
    (closed again by ``join_view``) or an error."""
    from angelo_agent import live_windows
    area, region = window_region(window)
    before = {a.as_pointer() for a in live_windows.areas(window)}
    with bpy.context.temp_override(window=window, area=area, region=region):
        result = bpy.ops.screen.area_split(direction='HORIZONTAL', factor=share)
    made = [a.as_pointer() for a in live_windows.areas(window) if a.as_pointer() not in before]
    return (made[0], None) if result == {'FINISHED'} and made else (None, repr(result))


def join_view(window, pointer):
    """Close the area ``split_view`` made: the view is as tall as before."""
    from angelo_agent import live_windows
    area = next((a for a in live_windows.areas(window) if a.as_pointer() == pointer), None)
    if area is None:
        return False
    with bpy.context.temp_override(window=window, area=area):
        return bpy.ops.screen.area_close() == {'FINISHED'}
