# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The person-click suite's probes (``test_blender_strict_person_click``): where the operating system has the pointer
and which window it has in front (Windows; failure details only), and the menu its ``covered`` case opens over the
Strict checkbox (a menu of several rows of the suite's own operator, opened where the pointer is, as any menu opens)."""

import os

import bpy


def os_cursor():
    """Where the OS pointer is (screen pixels), Windows only: the activation's own move goes there."""
    if os.name != 'nt':
        return None
    try:
        import ctypes
        import ctypes.wintypes
        point = ctypes.wintypes.POINT()
        return (point.x, point.y) if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)) else None
    except Exception as error:   # noqa: BLE001 - a detail only.
        return repr(error)


def os_foreground():
    """The OS's foreground window, Windows only: its title and whether it is this process's. With C's focus record
    it tells a window the OS left in the background from an activation the window manager missed."""
    if os.name != 'nt':
        return None
    try:
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(handle, title, 256)
        process = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process))
        return {'title': title.value, 'ours': process.value == os.getpid(), 'none': not handle}
    except Exception as error:   # noqa: BLE001 - a detail only.
        return repr(error)


COVER = "Cover"   # The covering menu's rows: "Cover 1" ...
ROWS = 7


class ANGELO_OT_suite_cover_noop(bpy.types.Operator):
    """The covering menu's item: does nothing (the suite's own)."""
    bl_idname = "angelo.suite_cover_noop"
    bl_label = COVER

    def execute(self, _context):
        return {'FINISHED'}


bpy.utils.register_class(ANGELO_OT_suite_cover_noop)


def cover_item(window, point=None):
    """The covering menu's row over ``point`` (window pixels) as (x, y, w, h), or None; without ``point``, any of its
    rows (whether the menu is open). A menu opens near the pointer, not exactly under it, so it has several rows."""
    from _bpy import _angelo_jobs
    rows = [(x, y, w, h) for pointer, _block, label, _idname, x, y, w, h in _angelo_jobs.popup_buttons()
            if pointer == window.as_pointer() and w > 0 and label.startswith(COVER)]
    if point is None:
        return rows[0] if rows else None
    px, py = point
    return next((r for r in rows if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]), None)


def open_cover(window):
    """A menu of ``ROWS`` rows opened where the pointer is, as any menu opens under it."""
    area = max(window.screen.areas, key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')

    def draw(menu, _context):
        for index in range(ROWS):
            menu.layout.operator(ANGELO_OT_suite_cover_noop.bl_idname, text="%s %d" % (COVER, index + 1))
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.context.window_manager.popup_menu(draw, title="")


def armed_checkbox(window, label):
    """Whether C has the checkbox armed now (``popup_button_guards``), or None when it can't be read."""
    from _bpy import _angelo_jobs
    rows = [row for row in _angelo_jobs.popup_button_guards()
            if row['window'] == window.as_pointer() and label in row['text']]
    return rows[0]['armed'] if rows else None
