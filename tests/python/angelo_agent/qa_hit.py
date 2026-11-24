# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a surface's hit rect is in window pixels: the one way the GUI suites turn a drawn surface's coordinates into
the pointer position a person would click (2.1.0).

Each surface keeps its hits in its own space and its input maps the pointer into that space; a suite clicks where the
hit is drawn only with that mapping's inverse. A surface is one of:

``LAYOUT``  the chat canvas (``chat_view``: ``hits``, ``popup_hits``, ``popup_rect``, ``composer``) and the canvases
            drawn with its scale (``flow_menu``): layout units, drawn scaled by ``chat_view.ui_scale()`` (the
            interface scale with the display's pixel size) and mirrored about the region's width in a right-to-left
            language. Input: ``x = pointer_x(mouse_x - region.x, region.width) / scale``, ``y = (mouse_y - region.y)
            / scale``.
``COMPOSER_TEXT``  the composer's text field (``chat_composer`` columns, ``draft_rows``): layout units scaled like
            the chat but never mirrored (``chat_view``: ``pointer_x(x, region.width / scale)`` undoes the mirror).
``PIXELS``  surfaces laid out in region pixels and mirrored about the region's width in a right-to-left language
            (``card_host``, ``intro``, ``connect_hero``, ``providers_page_host``, ``flow_window``). Input:
            ``x = pointer_x(mouse_x - region.x, region.width)``.
``RAW``     region pixels never mirrored: a 3D view projection (``location_3d_to_region_2d``), Blender's own regions,
            the library overlay, photo and media drops.
``mirrored_about(axis)``  region pixels mirrored about ``axis`` (a draw's ``canvas_rtl.axis()``, which a surface keeps
            with its hits: the studio's ``state["mirror"]``; None: unmirrored). Input: ``canvas_rtl.layout_x``.

A suite that added a hit rect to the region's origin by hand clicked beside the button whenever the interface scale
was not 1 (an Xvfb display at a DPI other than 96, a Retina screen, a user's Resolution Scale) or the language was
right to left: the 2.0.0 + menu "offset" of 2026-11-15 was exactly that, at ui_scale 1.0417 under a 100 DPI display
(the menu drew and hit-tested in one place; the suite's click missed). ``test_native_qa_hit.py`` refuses that raw form
in every suite, and ``test_blender_chat_hit_scale.py`` proves drawn == hit and a real click at scales 1, 1.5 and 2,
left to right and right to left.

2.0.0 shipped the chat's half of this as ``qa_input.chat_rect``/``chat_point`` (and ``window_click``'s ``scale``),
pinned by ``test_native_chat_click_scale.py``; they are ``LAYOUT`` here and call this module, so there is one mapping.
"""


class Surface:
    def __init__(self, name, scaled, mirror):
        self.name, self.scaled, self.mirror = name, scaled, mirror

    def __repr__(self):
        return "qa_hit.%s" % self.name


REGION = object()   # Mirrored about the region's own width in a right-to-left language.
LAYOUT = Surface("LAYOUT", True, REGION)
COMPOSER_TEXT = Surface("COMPOSER_TEXT", True, None)
PIXELS = Surface("PIXELS", False, REGION)
RAW = Surface("RAW", False, None)


def mirrored_about(axis):
    """Region pixels mirrored about ``axis`` (None: unmirrored), as ``canvas_rtl.layout_x`` reads the pointer."""
    return Surface("mirrored_about(%r)" % (axis,), False, axis)


def ui_scale():
    """The chat canvas's scale (``chat_view.ui_scale``). Read at each call: a suite may change the scale."""
    from angelo_agent import chat_view
    return chat_view.ui_scale()


def is_rtl():
    from angelo_agent import ui_text
    return ui_text.is_rtl()


def _scale(surface, scale):
    return (ui_scale() if scale is None else scale) if surface.scaled else 1.0


def _rtl(rtl):
    return is_rtl() if rtl is None else rtl


def xy(surface, region, x, y, *, scale=None, rtl=None):
    """Window pixels of the point (x, y) in ``surface``'s space in ``region``. ``scale`` and ``rtl`` replace the
    chat's current scale and the language's direction (a scaled surface; one mirrored about its region)."""
    scale = _scale(surface, scale)
    px, py = x * scale, y * scale
    if surface.mirror is REGION:
        if _rtl(rtl):
            px = region.width - px
    elif surface.mirror is not None:
        px = surface.mirror - px
    return region.x + px, region.y + py


def point(surface, region, rect, fx=0.5, fy=0.5, *, scale=None, rtl=None):
    """Window pixels of the point ``fx`` across and ``fy`` up ``rect`` (x, y, width, height) of ``surface``: the
    centre by default. Fractions are of the rect as laid out, so a mirrored rect's ``fx`` 0 is drawn at its right."""
    return xy(surface, region, rect[0] + rect[2] * fx, rect[1] + rect[3] * fy, scale=scale, rtl=rtl)


def ipoint(surface, region, rect, fx=0.5, fy=0.5, *, scale=None, rtl=None):
    """``point`` as whole pixels, for ``event_simulate``."""
    x, y = point(surface, region, rect, fx, fy, scale=scale, rtl=rtl)
    return int(x), int(y)


def rect(surface, region, rect, *, scale=None, rtl=None):
    """Window pixels (x, y, width, height) of ``rect`` where ``surface`` draws it: left and bottom edges, whichever
    side mirroring put them."""
    x0, y0 = xy(surface, region, rect[0], rect[1], scale=scale, rtl=rtl)
    x1, y1 = xy(surface, region, rect[0] + rect[2], rect[1] + rect[3], scale=scale, rtl=rtl)
    return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)



def from_window(surface, region, x, y, *, scale=None, rtl=None):
    """The point in ``surface``'s space at window pixels (x, y): what the surface's input reads there."""
    px, py = x - region.x, y - region.y
    if surface.mirror is REGION:
        if _rtl(rtl):
            px = region.width - px
    elif surface.mirror is not None:
        px = surface.mirror - px
    scale = _scale(surface, scale)
    return px / scale, py / scale


def from_window_rect(surface, region, rect, *, scale=None, rtl=None):
    """``rect`` (x, y, width, height in window pixels) in ``surface``'s space: the inverse of ``rect``."""
    x0, y0 = from_window(surface, region, rect[0], rect[1], scale=scale, rtl=rtl)
    x1, y1 = from_window(surface, region, rect[0] + rect[2], rect[1] + rect[3], scale=scale, rtl=rtl)
    return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)
