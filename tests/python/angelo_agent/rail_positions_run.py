# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The shared state of a test_blender_rail_positions run (1.0.6): its report, the window and sessions it drives, and
the small steps every stage uses (a check that keeps its first failure, waits the scenario yields, the drawn plan,
captures, the geometry rules applied to what the rail drew). Runs inside the app only (imports ``bpy``)."""

import time

import bpy

import qa_waits
import rail_positions_checks as geometry
import rail_positions_fixture as fx

SCALE = qa_waits.timeout_scale()
WAIT = 4.0   # Seconds for the product to draw a change by itself (x the runner's timeout scale).
OUT = None   # The QA folder: set by the suite.
state = {'phase': 'boot', 'started': time.monotonic(), 'done': False, 'pending': None, 'run': None}
report = {'checks': {}, 'details': {}, 'readings': {}, 'shots': [], 'cases': [], 'binary': bpy.app.binary_path}


class S:
    window = lead = probe = base_scale = None
    workers = []


def check(name, condition, detail=None):
    """A rule checked in more than one state keeps its first failure (and what it saw then)."""
    passed = bool(condition)
    if not passed and report['checks'].get(name, True) and detail is not None:
        report['details'][name] = detail
    report['checks'][name] = report['checks'].get(name, True) and passed


def rules(tag, found, stage):
    for rule, passed, detail in found:
        check('%s__%s' % (tag, rule), passed, {'stage': stage, 'saw': detail})


def until(condition, seconds=WAIT):
    """Yielded by a stage: resumes with True once ``condition()`` holds, False after ``seconds`` (x the scale)."""
    return ('until', condition, seconds * SCALE)


def pause(seconds):
    return ('pause', None, seconds)


def order():
    """The rail's sessions in rail order: the lead first, then the workers as they were bound."""
    return [S.lead.state['id']] + list(S.workers)


def plan():
    """The plan the rail last drew in its view (``session_rail_draw.last``), or None."""
    from angelo_agent import session_rail_draw
    placed = session_rail_draw.last.get(S.window.as_pointer())
    area, _region = fx.window_region(S.window)
    return placed[1] if placed and area is not None and placed[0] == area.as_pointer() else None


def drawn_since(mark, test=lambda p: True):
    """The newest plan the rail laid out in a real draw after probe frame ``mark`` that passes ``test``, or None."""
    return next((f.plan for f in reversed(S.probe.since(mark)) if f.plan is not None and test(f.plan)), None)


def gizmo(tag, readings, stage):
    """The product's navigation gizmo rect against Blender's own placement (``geometry.gizmo_rules``)."""
    rules(tag, geometry.gizmo_rules(readings['gizmo_product'], readings['gizmo_blender'], readings['ui_scale'],
                                    readings['gizmo_product_error']), stage)


def tiles():
    placed = plan()
    return [t.session for t in placed.tiles] if placed is not None else []


def to_window(rect):
    """The window pixel at the centre of ``rect`` (rail-region pixels)."""
    _area, region = fx.window_region(S.window)
    return region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2


def at(find, fallback=(0, 0, 0, 0)):
    """A click point for ``qa_input.press``, read again at the press: the centre of ``find(plan())`` (a rect or
    None), else ``fallback``'s; never raises in the press's timer (a plan gone meanwhile)."""
    def where():
        placed = plan()
        found = find(placed) if placed is not None else None
        return to_window(found or fallback)
    return where


def tile_rect(session):
    return lambda placed: next((t.rect for t in placed.tiles if t.session == session), None)


def shot(tag, number, name):
    fx.capture(S.probe, OUT, '%s-%d-%s.png' % (tag, number, name), report)


def least_pixels():
    """``(height, width)`` pixels of the least panel the product draws in the current position: its least size
    (``session_rail.least`` / ``session_rail_strip.least``) at the readable floor of the person's scale."""
    from angelo_agent import chat_view, parallel_setting, session_rail, session_rail_draw, session_rail_strip
    count = len(order())
    least = (session_rail.least(count) if parallel_setting.rail_position() == 'LEFT' else
             session_rail_strip.least(count, session_rail_draw._more_width(count)))   # Its cells as drawn.
    floor = session_rail_strip.READABLE * chat_view.ui_scale()
    return least[0] * floor, least[1] * floor


def geometry_of(tag, stage, live=None):
    """Every geometry rule on the plan drawn now (``rail_positions_checks.plan_rules``), with what it stood among."""
    from angelo_agent import chat_view, live_view_label, parallel_setting
    placed, (_area, region) = plan(), fx.window_region(S.window)
    if placed is None:
        check(tag + '__a_plan_is_drawn', False, stage)
        return None
    found, readings = fx.obstacles(S.window, live)
    rules(tag, geometry.plan_rules(placed, (region.width, region.height), found, parallel_setting.rail_position(),
                                   order(), fx.waiting(order()), live_view_label.room(chat_view.ui_scale()),
                                   least_pixels()), stage)
    gizmo(tag, readings, stage)
    report['readings'].setdefault(tag, {})[stage] = dict(readings, panel=placed.panel, first=placed.first,
                                                         hidden=placed.hidden, paged=placed.paged,
                                                         region=[region.width, region.height], scale=placed.scale)
    return placed
