# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's Asset Browser library row: its size, Turn off (asking whether to free the space too), Turn on and
Free up space. The files and rules are ``studio_assets_space``'s.

Turn off, Free up space and the question after the person removed the library in Preferences ask in a centred
Angelo card (``card_host``, the ``card_kit`` language) over the editor where they clicked (Media Studio, or the
Preferences window), not in a stock popup at the window's edge (1.0.5 review L1). The card only asks: its confirm
runs the operator's ``execute`` with a one-shot token the card issued (``_tokens``): only that deletes anything; an
``EXEC_DEFAULT`` call, from a script or an agent, frees nothing. Turn off and Free up space delete files, so their confirm is the destructive button. "Also delete its
copies" starts unticked when this file links models from the library or the library imports by linking (those links
show as missing until the library is turned on again). The free runs on a worker thread (``studio_assets_space
.free_later``); a main-thread timer reports how much it freed."""

import os
import secrets

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from . import live_windows
from . import studio_assets_host as host
from . import studio_assets_space as space
from . import card_kit as kit
from .ui_text import iface_, language, n_, rpt_

_freeing = {"result": None, "done": False, "running": False}   # The worker's result, read by ``_poll_free``.
_tokens = set()   # One-shot tokens the card's confirm issues; execute consumes one, so only the card deletes.


def issue_token():
    token = secrets.token_hex(16)
    _tokens.add(token)
    return token


def consume_token(token):
    """Whether ``token`` was issued by the card and not used yet; it is used now."""
    if token and token in _tokens:
        _tokens.discard(token)
        return True
    return False


def _directory():
    return host.directories()[1]


def size_text(count_bytes):
    template, value = space.readable(count_bytes)
    return iface_(template, value=value)


def active():
    """Whether the library is in Preferences now (path text only; main thread)."""
    from . import studio_assets
    return studio_assets.find_library(host._libraries(), _directory()) is not None


def links_from_library():
    """Whether deleting the copies would leave links missing: the open file links from the library's folder, or the
    library's entry imports by linking. Resolves paths (``realpath`` touches the disk) on the main thread: called once
    from the card's ``__init__`` on the person's click, never from a draw."""
    directory = os.path.realpath(_directory())
    for library in bpy.data.libraries:
        try:
            path = os.path.realpath(bpy.path.abspath(library.filepath))
            if os.path.commonpath([path, directory]) == directory:
                return True
        except ValueError:
            continue
    from . import studio_assets
    entry = studio_assets.find_library(host._libraries(), _directory())
    return getattr(entry, "import_method", "") == 'LINK'


def start_free():
    """Free the copies on a worker thread; ``_poll_free`` (persistent: a file load never strands it) reports the
    outcome. Never blocks the main thread; a second request while one runs is dropped."""
    if _freeing["running"]:
        return

    def done(result):   # Worker thread: only plain data here, never bpy. Ends "running" whatever happened.
        _freeing.update(result=result, done=True, running=False)
    _freeing.update(result=None, done=False, running=True)
    try:
        space.free_later(_directory(), active(), done)
    except ValueError:
        _freeing.update(running=False)
        return
    if not bpy.app.timers.is_registered(_poll_free):
        bpy.app.timers.register(_poll_free, first_interval=0.1, persistent=True)


def _poll_free():
    if not _freeing["done"]:
        return 0.1 if _freeing["running"] else None
    result = _freeing["result"]
    _freeing.update(done=False, result=None)
    if result is None:
        report('WARNING', rpt_("Media Studio's library is busy. Try Free up space again in a moment"))
    elif result == space.ON:
        report('WARNING', rpt_("The library is on again, so nothing was deleted"))
    else:
        report('INFO', rpt_("Freed {size}", size=size_text(result[0])))
    host.redraw_studio()
    return None


def report(level, message):
    """A report in the status bar from a timer (an operator's own report ends with it)."""
    window = live_windows.first(bpy.context.window_manager)
    if window is not None:
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.studio_library_report(level=level, message=message)


TURN_OFF_TITLE = n_("Turn off the Asset Browser library?")
TURN_OFF_LEAD = (n_("Media Studio's models and images leave the Asset Browser and asset shelves."),
                 n_("Media Studio itself keeps everything."))
FREE_TITLE_SIZE, FREE_TITLE = n_("Free up {size}?"), n_("Delete the Asset Browser library's copies?")
FREE_LEAD = n_("Media Studio keeps every model and image: this deletes only the Asset Browser's copies, and turning "
               "the library on writes them again.")
WARNING = (n_("A scene that links a model from this library shows it as missing until you turn the library on again."),
           n_("Appended and packed items are not affected."))
LINKED = n_("This file links models from this library, or the library imports by linking.")
CANCEL, TURN_OFF, FREE_UP = n_("Cancel"), n_("Turn off"), n_("Free up space")
CARD_WIDTH = 480


class LibraryCard(kit.BaseCard):
    """Turn off (``what`` "off", with Also delete its copies) or Free up space ("free"), asked over ``area``."""
    kind, modal, priority = "studio_library", True, 85
    cancel_action, default_action, outside_dismisses = "cancel", None, True   # Return never deletes files.

    def __init__(self, what, area_pointer):
        self.linked = links_from_library()
        self.what, self.area_pointer, self.free = what, area_pointer, not self.linked
        self.measured = space.size(_directory())
        self.decision = None

    def version(self):
        return self.what, self.free, self.measured, language()

    def target_area(self, window):
        """The editor the person asked in; None (``card_host``'s largest 3D view) when it is gone or there was none."""
        return next((a for a in live_windows.areas(window) if a.as_pointer() == self.area_pointer), None)

    def layout(self, view, region_w, region_h, scale, ui):
        def plan(size):
            builder = self._build(view, size, region_w, ui)
            top = min((region_h + builder.height()) / 2, region_h - 10 * builder.s)
            return builder.place((region_w - builder.width) / 2, top, True, (region_w, region_h))
        return kit.fit(plan, region_w, region_h, scale)

    def _build(self, view, scale, region_w, ui):
        b = kit.Builder(view, scale, min(CARD_WIDTH * scale, region_w - 40 * scale), ui, padding=26)
        size = size_text(self.measured) if self.measured else ""
        if self.what == "off":
            b.heading(iface_(TURN_OFF_TITLE), 19)
            b.gap(6)
            for line in TURN_OFF_LEAD:
                b.paragraph(iface_(line), 13.5, kit.MUTED)
            b.gap(14)
            b.checkbox("free", None, iface_("Also delete its copies ({size})", size=size) if size
                       else iface_("Also delete its copies"), self.free)
            warn, confirm = self.free, TURN_OFF
        else:
            b.heading(iface_(FREE_TITLE_SIZE, size=size) if size else iface_(FREE_TITLE), 19)
            b.gap(6)
            b.paragraph(iface_(FREE_LEAD), 13.5, kit.MUTED)
            warn, confirm = True, FREE_UP
        if warn or self.linked:   # What deleting the copies costs, aligned with the text above it.
            b.gap(10)
            if self.linked:
                b.paragraph(iface_(LINKED), 13, kit.AMBER)
            b.paragraph(iface_(WARNING[0]), 13, kit.AMBER)
            b.paragraph(iface_(WARNING[1]), 13, kit.MUTED)
        b.gap(18)
        destructive = self.what == "free" or self.free
        b.buttons([("cancel", None, iface_(CANCEL), "secondary", True),
                   ("confirm", None, iface_(confirm), "destructive" if destructive else "primary", True)])
        b.close_button("cancel")
        return b

    def act(self, action, value, host):
        if action == "free":
            self.free = not self.free
            return
        if action == "confirm":
            self.decision = "confirm"
            host.close("done")
            if self.what == "off":   # The card is the confirmation: only its token deletes.
                bpy.ops.angelo.studio_library_off('EXEC_DEFAULT', free=self.free, token=issue_token())
            else:
                bpy.ops.angelo.studio_library_free('EXEC_DEFAULT', token=issue_token())
        elif action in {"cancel", "dismiss", "close"}:
            self.decision = "cancel"
            host.close("done")


def ask(what, context):
    """Show the card over the editor where the person asked (Media Studio's area, or the Preferences window's)."""
    from . import card_host
    area = context.area
    if area is None and context.window is not None:
        area = next((a for a in live_windows.areas(context.window) if a.type == 'PREFERENCES'), None)
    card_host.request(LibraryCard(what, area.as_pointer() if area is not None else 0))   # 0: over the 3D view.
    card_host.pump(lambda: True)
    return {'FINISHED'}


class ANGELO_OT_studio_library_off(Operator):
    bl_idname = "angelo.studio_library_off"
    bl_label = "Turn Off Asset Browser Library"
    bl_description = "Remove Angelo Media Studio from Angelo's Asset Browser and asset shelves"
    free: BoolProperty(name="Also free up the space", default=True, options={'SKIP_SAVE'})
    token: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})   # Issued only by the card's confirm (one use).

    def invoke(self, context, event):
        return ask("off", context)

    def execute(self, context):
        host.turn_off()
        if self.free and consume_token(self.token):
            start_free()
        elif self.free:
            self.report({'WARNING'}, rpt_("Turned off. Its copies are deleted only after you confirm in its dialog"))
        host.redraw_studio()
        return {'FINISHED'}


class ANGELO_OT_studio_library_on(Operator):
    bl_idname = "angelo.studio_library_on"
    bl_label = "Turn On Asset Browser Library"
    bl_description = "Show Media Studio's models and images in Angelo's Asset Browser and asset shelves again"

    def execute(self, context):
        host.turn_on()
        host.redraw_studio()
        return {'FINISHED'}


class ANGELO_OT_studio_library_free(Operator):
    bl_idname = "angelo.studio_library_free"
    bl_label = "Free Up Space"
    bl_description = "Delete the Asset Browser library's copies of Media Studio's models and images"

    token: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})   # Issued only by the card's confirm (one use).

    @classmethod
    def poll(cls, context):
        return not active()

    def invoke(self, context, event):
        return ask("free", context)

    def execute(self, context):
        if not consume_token(self.token):
            self.report({'ERROR'}, rpt_("Free up space deletes files only after you confirm in its dialog"))
            return {'CANCELLED'}
        start_free()
        host.redraw_studio()
        return {'FINISHED'}


class ANGELO_OT_studio_library_report(Operator):
    bl_idname = "angelo.studio_library_report"
    bl_label = "Asset Browser Library"
    bl_options = {'INTERNAL'}
    level: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})
    message: StringProperty(options={'SKIP_SAVE', 'HIDDEN'})

    def execute(self, context):
        self.report({self.level if self.level in {'INFO', 'WARNING', 'ERROR'} else 'INFO'}, self.message)
        return {'FINISHED'}


def ask_free(tries=[0]):   # noqa: B006 - counts the waits for the size across timer calls.
    """After the person removed the library in Preferences (their own Turn off): ask whether to free the space too,
    in the window where they did it. Nothing to ask when no copies are left. The size is measured off the main
    thread: this waits for it (up to about three seconds)."""
    if active():
        tries[0] = 0
        return None
    measured = space.size(_directory())
    if measured is None and tries[0] < 30:
        tries[0] += 1
        return 0.1
    tries[0] = 0
    if not measured:
        return None
    window = next((w for w, a in live_windows.window_areas(bpy.context.window_manager) if a.type == 'PREFERENCES'),
                  live_windows.first(bpy.context.window_manager))
    if window is not None:
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.studio_library_free('INVOKE_DEFAULT')
    return None


classes = (ANGELO_OT_studio_library_off, ANGELO_OT_studio_library_on, ANGELO_OT_studio_library_free,
           ANGELO_OT_studio_library_report)
