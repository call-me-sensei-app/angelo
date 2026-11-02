# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an agent's EEVEE view drew with Blender's error shading or default world, from its scene's shader state
(``view_warm.state``): materials, and worlds, whose compile failed. Read through ``view_warm``."""

FAILED_NOTE = ("These shaders failed to compile; Blender draws such a material with its error shading and such a world "
               "with its default world, in this view and in the viewport alike.")


def failed(window, shading):
    """The names of what ``window``'s scene draws in an EEVEE ``shading`` whose compile failed: its materials, and its
    world in LIT, and in MATERIAL when it is the view layer's override (otherwise a studio light)."""
    from .view_looks import EEVEE_SHADINGS
    from . import view_warm
    found = view_warm.state(window) if shading in EEVEE_SHADINGS else None
    names = set(found[5]) if found is not None and len(found) > 5 else set()
    if found is not None and len(found) > 6 and (shading == "LIT" or (len(found) > 8 and found[8])):
        names |= set(found[6])   # MATERIAL draws the view layer's world override too (VW10-H-3).
    return names


def note(names):
    """The uncertainty note of a view that drew ``names`` (``failed``) with Blender's error or default shading."""
    return {"code": "shaders_failed", "detail": FAILED_NOTE + " " + ", ".join(sorted(names))}


def notes(window, shading):
    """The view's uncertainty notes from its scene's shader state: what failed to compile, drawn with Blender's error
    shading or default world in an EEVEE look (BREAKIT-viewwarm VW-H-1, VW8-H-1: the view must say so)."""
    names = failed(window, shading)
    return [note(names)] if names else []
