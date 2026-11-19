# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hiding and showing a hidden-window session's app on macOS (doc/angelo/mcp_headless_window.rst): AppKit through the
Objective-C runtime (ctypes), so no native change ships. No bpy.

* ``hide_app``: no Dock icon and no menu bar, never activated (activation policy Prohibited, so Blender's own window
  setup can't bring it forward), hidden (``hide:``) and its windows ordered out; with ``keep_awake``, App Nap off
  while the system may still sleep, so its timers and the agent's turn run at full speed while nothing is shown.
* ``show_app``: a regular app again (Dock icon, menu bar), unhidden, its windows in front and active: the person's
  window. Returns whether the switch back to Regular took (the caller retries on a tick when it didn't).
* ``on_reopen``: a Finder or Dock launch of Angelo while this hidden app runs reaches it as a reopen AppleEvent
  (LaunchServices reopens the running app; no second process starts); it calls back, so the session can reveal
  itself as the person's own launch does (review 5, 1).
"""

_activity = []   # The App Nap activity token, held for the process's life.
_reopen = []     # The reopen handler's object and callback, held while it is installed.
REOPEN_CLASS = b"AngeloHiddenReopenHandler"


def _runtime():
    import ctypes
    import ctypes.util
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.objc_getClass.restype = objc.sel_registerName.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = objc.sel_registerName.argtypes = [ctypes.c_char_p]

    def send(receiver, selector, *args, restype=ctypes.c_void_p, argtypes=()):
        function = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p,
                                                                   *argtypes))
        return function(receiver, objc.sel_registerName(selector), *args)
    return ctypes, objc, send


def hide_app(keep_awake=True):
    """Prohibited (no Dock icon, no menu bar, never activated: Blender's own window setup can't bring it forward or
    take focus), hidden, and every window ordered out (the macOS proof saw a window flash before this ran on ticks)."""
    ctypes, objc, send = _runtime()
    app = send(objc.objc_getClass(b"NSApplication"), b"sharedApplication")
    send(app, b"setActivationPolicy:", 2, restype=ctypes.c_bool, argtypes=(ctypes.c_long,))   # Prohibited.
    send(app, b"hide:", None, restype=None, argtypes=(ctypes.c_void_p,))
    windows = send(app, b"windows")
    for index in range(send(windows, b"count", restype=ctypes.c_ulong) if windows else 0):
        window = send(windows, b"objectAtIndex:", index, argtypes=(ctypes.c_ulong,))
        send(window, b"orderOut:", None, restype=None, argtypes=(ctypes.c_void_p,))
    if not keep_awake or _activity:
        return
    reason = send(objc.objc_getClass(b"NSString"), b"stringWithUTF8String:", b"Angelo works for an app",
                  argtypes=(ctypes.c_char_p,))
    info = send(objc.objc_getClass(b"NSProcessInfo"), b"processInfo")
    token = send(info, b"beginActivityWithOptions:reason:", 0x00EFFFFF, reason,   # ...AllowingIdleSystemSleep.
                 argtypes=(ctypes.c_uint64, ctypes.c_void_p))
    _activity.append(send(token, b"retain"))


def end_activity():
    """End the App Nap activity ``hide_app`` began (the person's window naps as any app does)."""
    if not _activity:
        return
    _ctypes, objc, send = _runtime()
    import ctypes
    info = send(objc.objc_getClass(b"NSProcessInfo"), b"processInfo")
    token = _activity.pop()
    send(info, b"endActivity:", token, restype=None, argtypes=(ctypes.c_void_p,))
    send(token, b"release", restype=None)


def show_app():
    ctypes, objc, send = _runtime()
    app = send(objc.objc_getClass(b"NSApplication"), b"sharedApplication")
    regular = send(app, b"setActivationPolicy:", 0, restype=ctypes.c_bool, argtypes=(ctypes.c_long,))   # Regular.
    send(app, b"unhide:", None, restype=None, argtypes=(ctypes.c_void_p,))
    windows = send(app, b"windows")
    for index in range(send(windows, b"count", restype=ctypes.c_ulong) if windows else 0):
        window = send(windows, b"objectAtIndex:", index, argtypes=(ctypes.c_ulong,))
        send(window, b"makeKeyAndOrderFront:", None, restype=None, argtypes=(ctypes.c_void_p,))
    send(app, b"activateIgnoringOtherApps:", True, restype=None, argtypes=(ctypes.c_bool,))
    return bool(regular) and send(app, b"activationPolicy", restype=ctypes.c_long) == 0


def _four(code):
    return int.from_bytes(code, "big")


def on_reopen(callback):
    """Call ``callback`` (no arguments, main thread) for each reopen AppleEvent: a Finder or Dock launch of Angelo."""
    ctypes, objc, send = _runtime()
    objc.objc_allocateClassPair.restype = ctypes.c_void_p
    objc.objc_allocateClassPair.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    objc.class_addMethod.restype = ctypes.c_bool
    objc.class_addMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
    objc.objc_registerClassPair.argtypes = [ctypes.c_void_p]
    selector = objc.sel_registerName(b"handleReopen:withReply:")
    handler = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
        lambda _self, _cmd, _event, _reply: callback())
    cls = objc.objc_getClass(REOPEN_CLASS)
    if not cls:
        cls = objc.objc_allocateClassPair(objc.objc_getClass(b"NSObject"), REOPEN_CLASS, 0)
        objc.class_addMethod(cls, selector, ctypes.cast(handler, ctypes.c_void_p), b"v@:@@")
        objc.objc_registerClassPair(cls)
    target = send(send(cls, b"alloc"), b"init")
    manager = send(objc.objc_getClass(b"NSAppleEventManager"), b"sharedAppleEventManager")
    send(manager, b"setEventHandler:andSelector:forEventClass:andEventID:", target, selector, _four(b"aevt"),
         _four(b"rapp"), restype=None, argtypes=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32))
    _reopen[:] = [target, handler]

