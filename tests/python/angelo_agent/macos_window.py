# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The app's own macOS windows, read from inside the app: titles, title-bar colours and a picture of the frame.

Used by GUI suites running in the app (``test_blender_app_identity``, ``test_blender_window_chrome``) on
Blender's main thread. Blender has no Python access to a window's title or title bar, and capturing the
screen needs a Screen Recording grant a test machine may not have; AppKit can report a window's own
properties and draw its frame (title bar included) into a bitmap without one. The 3D content is GPU-drawn
into a Metal layer, so it may come out blank: the frame is what the picture is for. Standard library only
(ctypes and the Objective-C runtime); nothing here runs on import.
"""

import ctypes

OBJC = "/usr/lib/libobjc.A.dylib"
APPKIT = "/System/Library/Frameworks/AppKit.framework/AppKit"
PNG = 4  # NSBitmapImageFileTypePNG


class Rect(ctypes.Structure):
    """NSRect (origin and size, in points)."""
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("width", ctypes.c_double),
                ("height", ctypes.c_double)]


_runtime = None


def _objc():
    global _runtime
    if _runtime is None:
        ctypes.cdll.LoadLibrary(APPKIT)
        runtime = ctypes.cdll.LoadLibrary(OBJC)
        runtime.objc_getClass.restype, runtime.objc_getClass.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
        runtime.sel_registerName.restype, runtime.sel_registerName.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
        _runtime = runtime
    return _runtime


def _call(receiver, selector, restype=ctypes.c_void_p, *args):
    """``[receiver selector...]``: objc_msgSend cast to this exact signature (arm64 needs the real types)."""
    runtime = _objc()
    types = [type(arg) if isinstance(arg, (Rect, ctypes._SimpleCData)) else ctypes.c_void_p for arg in args]
    function = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *types)(
        ctypes.cast(runtime.objc_msgSend, ctypes.c_void_p).value)
    return function(receiver, runtime.sel_registerName(selector.encode()), *args)


def _class(name):
    return _objc().objc_getClass(name.encode())


def windows():
    """The application's windows, as ``(window, title, visible)``, in AppKit's order."""
    array = _call(_call(_class("NSApplication"), "sharedApplication"), "windows")
    found = []
    for index in range(_call(array, "count", ctypes.c_ulong)):
        window = _call(array, "objectAtIndex:", ctypes.c_void_p, ctypes.c_ulong(index))
        text = _call(window, "title")
        title = _call(text, "UTF8String", ctypes.c_char_p) if text else None
        found.append((window, title.decode("utf-8") if title else "", bool(_call(window, "isVisible", ctypes.c_bool))))
    return found


def _text(string):
    value = _call(string, "UTF8String", ctypes.c_char_p) if string else None
    return value.decode("utf-8") if value else ""


def title_bar(window):
    """How AppKit draws ``window``'s title bar: ``background`` (sRGB bytes; the colour behind a transparent
    title bar), ``appearance`` (the window's own, which decides light or dark title text and buttons; empty
    when it follows the system) and ``transparent`` (the title bar shows the background colour)."""
    color = _call(window, "backgroundColor")
    srgb = _call(color, "colorUsingColorSpace:", ctypes.c_void_p,
                 ctypes.c_void_p(_call(_class("NSColorSpace"), "sRGBColorSpace"))) if color else None
    background = tuple(round(_call(srgb, component, ctypes.c_double) * 255)
                       for component in ("redComponent", "greenComponent", "blueComponent")) if srgb else None
    appearance = _call(window, "appearance")
    return {"background": background, "appearance": _text(_call(appearance, "name")) if appearance else "",
            "transparent": bool(_call(window, "titlebarAppearsTransparent", ctypes.c_bool))}


def capture_frame(window, path):
    """Write a PNG of ``window``'s frame as AppKit draws it, title bar included. True when written."""
    frame = _call(_call(window, "contentView"), "superview")
    bounds = _call(frame, "bounds", Rect)
    bitmap = _call(frame, "bitmapImageRepForCachingDisplayInRect:", ctypes.c_void_p, bounds)
    _call(frame, "cacheDisplayInRect:toBitmapImageRep:", None, bounds, ctypes.c_void_p(bitmap))
    data = _call(bitmap, "representationUsingType:properties:", ctypes.c_void_p, ctypes.c_ulong(PNG),
                 ctypes.c_void_p(_call(_class("NSDictionary"), "dictionary")))
    target = _call(_class("NSString"), "stringWithUTF8String:", ctypes.c_void_p, ctypes.c_char_p(str(path).encode()))
    return bool(data) and bool(_call(data, "writeToFile:atomically:", ctypes.c_bool, ctypes.c_void_p(target),
                                     ctypes.c_bool(True)))
