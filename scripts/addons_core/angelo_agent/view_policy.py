# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Add-on Python a view's own temporary writes run, for the views that are neither RENDERED looks nor strips.

A view's own writes, set for a moment and put back, run
Python from add-ons: depsgraph-update handlers when the writes make Blender
update the depsgraph, and ``bpy.msgbus`` subscribers to the settings written.
The mode-aware rule covers every view that writes
(``render_extensions``): the Read-only and Ask approval modes refuse such a
view before it writes anything; the Auto modes, whose edits run the same
Python, make it, name the Python, and never say the scene is unchanged.
RENDERED looks (``view_render``) and strips (``view_motion``) apply it
themselves. Here:

* **Viewport captures** (SOLID, WIREFRAME, ATTRIBUTE, MATERIAL, LIT):
  ``view_capture.DisplayOverrides`` sets the 3D view's shading and overlay
  settings (``view_capture.display_keys``). They belong to the screen, which
  Blender does not copy for evaluation, so the writes update the depsgraph in
  two cases only (``rna_space.cc``): the shading type changing to or from
  Material Preview or Rendered while an object has a sculpt session (sculpt,
  vertex and weight paint modes), which re-evaluates its geometry, and the
  overlays turned on or off under the Retopology overlay (``display_dirties``).
  WEIGHT draws without writing; a cached view draws nothing.
* **Render Result, render-pass and finished-image pictures** write nothing:
  ``view_save`` saves them with an image format of the save's own. Only a
  build without its native helper sets the scene's image format for the save
  (``view_render_restore.LookSettings``): scene settings, so the depsgraph
  updates after every such save (``before_settings``).
* Not covered, because they write nothing: UV layouts, image data-blocks,
  editor captures (Blender's own screenshot operator) and job files read as
  they are.

All of these views are made at once on the main thread: the Python they let
run runs after the view has returned, so no check after the view could see
it, and the result says so instead (``disclose``: ``scene_changed`` null).
Without such Python the cost is one pass over the depsgraph-update lists (only
when the writes update the depsgraph) and one over the message bus for the
settings written. Model-facing English.
"""

from array import array
from contextlib import contextmanager

SESSION_MODES = ("SCULPT", "VERTEX_PAINT", "WEIGHT_PAINT")  # Modes with a sculpt session (paint_vertex.cc).
PREVIEW_TYPES = frozenset({"MATERIAL", "RENDERED"})

_current = None  # The view being made by ``shown`` (view_finished): its mode and the Python it let run.


class ViewPython:
    """One view's approval mode and the add-on Python its writes let run."""

    def __init__(self, mode):
        self.mode, self.ran, self.unknown = mode, [], False

    def admit(self, written, dirties, what):
        """Refuse (RuntimeError) or record the Python that writing ``what`` would run."""
        from . import render_extensions
        report = render_extensions.refuse_view(written, dirties, self.mode, what)
        self.ran += [label for label in report.view_python if label not in self.ran]
        self.unknown = self.unknown or report.subscribers is None
        return self


def mode_of(controller):
    """A view's approval mode: its chat's (effective) mode, or Ask, the strict one, when no chat asks for it.

    A flow run's or a library save's capture has no chat; the chat default
    (Auto, ``chat_settings.DEFAULT_APPROVAL``) never applies to it.
    """
    from .chat_settings import clean_options
    options = getattr(controller, "state", {}).get("options")
    return clean_options(options)["approval"] if isinstance(options, dict) else "ASK"


def _sculpt_sessions():
    """Whether an object is in a mode that keeps a sculpt session (read natively, all objects at once)."""
    import bpy
    items = bpy.types.Object.bl_rna.properties["mode"].enum_items
    bits = 0
    for name in SESSION_MODES:
        bits |= items[name].value
    objects = bpy.data.objects
    modes = array("i", bytes(4 * len(objects)))
    objects.foreach_get("mode", modes)
    return any(mode & bits for mode in modes)


def display_dirties(space, shading):
    """Whether drawing ``shading`` in the 3D view ``space`` makes Blender update the depsgraph (module docstring)."""
    from .view_capture import OVERLAY, SHADING
    wanted = dict(SHADING[shading])["type"]
    current = space.shading.type
    if wanted != current and PREVIEW_TYPES & {wanted, current} and _sculpt_sessions():
        return True
    overlays = dict(OVERLAY.get(shading, (("show_overlays", False),)))["show_overlays"]
    return overlays != space.overlay.show_overlays and bool(getattr(space.overlay, "show_retopology", False))


def admit_display(space, shading, controller):
    """Before a viewport capture draws: refuse in Read-only and Ask, else the ``ViewPython`` to disclose."""
    from .view_capture import display_keys
    return ViewPython(mode_of(controller)).admit(display_keys(shading), display_dirties(space, shading),
                                                 "the 3D view's display settings")


@contextmanager
def making(controller):
    """While ``shown`` makes a picture: the ``ViewPython`` its settings writes record into."""
    global _current
    previous, _current = _current, ViewPython(mode_of(controller))
    try:
        yield _current
    finally:
        _current = previous


def before_settings(scene, values):
    """Before ``LookSettings`` sets ``values`` on the scene for a save: refuse, or record what will run."""
    from .view_render_restore import written_keys
    view = _current if _current is not None else ViewPython("ASK")  # Outside ``making``: the strict mode.
    view.admit(written_keys(scene, values), True, "the scene's image format")


def disclose(result, view):
    """Say in ``result`` what add-on Python the view let run: it runs after the view, so never "unchanged"."""
    from . import view_scene_check
    if view is not None and (view.ran or view.unknown):
        view_scene_check.claims(result, None, view.ran, view.unknown)
    return result
