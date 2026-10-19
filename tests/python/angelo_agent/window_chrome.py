# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's window chrome colour and the contrast arithmetic its tests share.

The title bar and the top bar are the darkest facet of the wordmark's "A"
(``build_files/utils/angelo_wordmark.mjs``) at ``TINT`` of its brightness: the
same hue, darkened until every text colour drawn on it reaches WCAG AA
(``doc/angelo/app_identity.rst``, "Window chrome"). Standard library only, so
the host suite and the suites running inside the app both import it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TINT = 0.85
READABLE = 4.5  # WCAG 2 level AA for normal-size text.


def rgb(text):
    """``"#087787"`` or ``"087787b3"`` as an (r, g, b) byte triple; an alpha pair is ignored."""
    text = text.lstrip("#")
    return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))


def luminance(color):
    """WCAG 2 relative luminance of an sRGB byte triple."""
    def linear(channel):
        channel /= 255
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
    return 0.2126 * linear(color[0]) + 0.7152 * linear(color[1]) + 0.0722 * linear(color[2])


def contrast(first, second):
    """WCAG 2 contrast ratio of two sRGB byte triples: 1 (none) to 21 (black on white)."""
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def over(color, alpha, backdrop):
    """``color`` at ``alpha`` (0-1) drawn over ``backdrop``."""
    return tuple(top * alpha + below * (1 - alpha) for top, below in zip(color, backdrop))


def needs_light_text(color):
    """GHOST's title-bar rule (``GHOST_Window::coloredTitleBarNeedsLightText``): white has the higher contrast."""
    return contrast(color, (255, 255, 255)) >= contrast(color, (0, 0, 0))


def facets():
    """The A's facet colours, in the order ``angelo_wordmark.mjs`` lists them."""
    source = (ROOT / "build_files/utils/angelo_wordmark.mjs").read_text(encoding="utf-8")
    block = source.split("export const FACETS = [", 1)[1].split("];", 1)[0]
    return [rgb(color) for color in re.findall(r'color: "(#[0-9a-f]{6})"', block)]


def darkest_facet():
    return min(facets(), key=luminance)


def chrome():
    """The chrome colour as an (r, g, b) byte triple."""
    return tuple(round(channel * TINT) for channel in darkest_facet())


def hex_color(color):
    return "#%02x%02x%02x" % tuple(round(channel) for channel in color)
