# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether the operating system asks apps for less motion. No ``bpy``: host-testable.

Blender's own Reduce Motion preference (``PreferencesView.use_reduce_motion``) is the app's setting; this adds
the system's, so a person who turned motion down for every app gets it in Angelo too without finding Blender's:

* macOS: System Settings > Accessibility > Display > Reduce motion, read with
  ``[[NSWorkspace sharedWorkspace] accessibilityDisplayShouldReduceMotion]`` through the Objective-C runtime
  (AppKit is already loaded in the app; the answer is AppKit's cached value, under a microsecond to read).
* Windows: Settings > Accessibility > Visual effects > Animation effects ("Show animations in Windows"), read
  with ``SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION)``; off means reduce motion.
* Elsewhere, or when the query fails: False (the app's preference still applies).

Callers ask once when an animation starts (``marble_host``), never in a draw callback.
"""

import ctypes
import sys

SPI_GETCLIENTAREAANIMATION = 0x1042
_APPKIT = "/System/Library/Frameworks/AppKit.framework/AppKit"
_OBJC = "/usr/lib/libobjc.A.dylib"
_macos = None  # (workspace, selector, send) once resolved, False when it cannot be.


def _macos_query():
    global _macos
    if _macos is None:
        try:
            objc = ctypes.cdll.LoadLibrary(_OBJC)
            ctypes.cdll.LoadLibrary(_APPKIT)
            objc.objc_getClass.restype, objc.objc_getClass.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
            objc.sel_registerName.restype, objc.sel_registerName.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
            send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
            send_bool = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
            workspace = send_id(objc.objc_getClass(b"NSWorkspace"), objc.sel_registerName(b"sharedWorkspace"))
            selector = objc.sel_registerName(b"accessibilityDisplayShouldReduceMotion")
            _macos = (workspace, selector, send_bool) if workspace and selector else False
        except (OSError, AttributeError, TypeError, ValueError):
            _macos = False
    if not _macos:
        return False
    workspace, selector, send = _macos
    return bool(send(workspace, selector))


def _windows_query():
    try:
        enabled = ctypes.c_int(1)
        if not ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0):
            return False
        return not enabled.value
    except (OSError, AttributeError):
        return False


def os_reduces_motion(platform=None):
    """True when the system asks for reduced motion; False when it does not or cannot say."""
    platform = platform or sys.platform
    if platform == "darwin":
        return _macos_query()
    if platform == "win32":
        return _windows_query()
    return False
