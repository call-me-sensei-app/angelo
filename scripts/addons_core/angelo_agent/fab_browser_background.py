# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Keep the managed Fab browser behind Angelo: it searches and reads listings while the person keeps working.

Only the manual sign-in window (and a listing the person must act on, such as a purchase) is meant to be in front.
A managed browser's window comes from Chrome's own launch, which macOS brings forward; Angelo then makes itself the
active application again, so Chrome stays an ordinary window behind it. It is never hidden or headless: a hidden
Chrome stops drawing Fab's Download dialog (live 2026-11-09: LISTING_CONVERTED_GROUP) and Fab does not accept a
headless browser's sign-in. Background throttling is off (``MANAGED_FLAGS``). A failure only leaves the window
where Chrome put it: never a reason to refuse Fab work.
"""
import ctypes as C
import os
import sys

# Hidden or covered pages keep their timers and rendering, so the Download dialog and listing reads finish.
MANAGED_FLAGS = ('--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
                 '--disable-renderer-backgrounding')


def _appkit():
    C.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
    objc = C.CDLL('/usr/lib/libobjc.A.dylib')
    objc.objc_getClass.argtypes, objc.objc_getClass.restype = [C.c_char_p], C.c_void_p
    objc.sel_registerName.argtypes, objc.sel_registerName.restype = [C.c_char_p], C.c_void_p
    address = C.cast(objc.objc_msgSend, C.c_void_p).value

    def send(result, args, obj, selector, *values):
        return C.CFUNCTYPE(result, C.c_void_p, C.c_void_p, *args)(address)(obj, objc.sel_registerName(selector), *values)
    return objc, send


def background(pid):
    """Make Angelo active again in front of the managed browser ``pid``; ``True`` when that browser was found.
    Never raises."""
    if sys.platform != 'darwin' or type(pid) is not int or pid <= 0 or pid == os.getpid():
        return False
    try:
        objc, send = _appkit()
        apps, pools = objc.objc_getClass(b'NSRunningApplication'), objc.objc_getClass(b'NSAutoreleasePool')
        if not apps or not pools:
            return False
        pool = send(C.c_void_p, [], send(C.c_void_p, [], pools, b'alloc'), b'init')
        try:
            app = send(C.c_void_p, [C.c_int], apps, b'runningApplicationWithProcessIdentifier:', pid)
            own = send(C.c_void_p, [C.c_int], apps, b'runningApplicationWithProcessIdentifier:', os.getpid())
            if own:   # 2: NSApplicationActivateIgnoringOtherApps. Chrome stays a window behind Angelo and keeps drawing.
                send(C.c_bool, [C.c_ulong], own, b'activateWithOptions:', 2)
            return bool(app)
        finally:
            if pool:
                send(None, [], pool, b'drain')
    except Exception:  # A focus courtesy: it never decides whether Fab work proceeds.
        return False


def foreground(pid):
    """Show the managed browser ``pid`` in front, for a listing the person acts on (Add to My Library). Never raises."""
    if sys.platform != 'darwin' or type(pid) is not int or pid <= 0 or pid == os.getpid():
        return False
    try:
        objc, send = _appkit()
        apps, pools = objc.objc_getClass(b'NSRunningApplication'), objc.objc_getClass(b'NSAutoreleasePool')
        if not apps or not pools:
            return False
        pool = send(C.c_void_p, [], send(C.c_void_p, [], pools, b'alloc'), b'init')
        try:
            app = send(C.c_void_p, [C.c_int], apps, b'runningApplicationWithProcessIdentifier:', pid)
            if not app:
                return False
            send(C.c_bool, [], app, b'unhide')
            send(C.c_bool, [C.c_ulong], app, b'activateWithOptions:', 0)
            return True
        finally:
            if pool:
                send(None, [], pool, b'drain')
    except Exception:
        return False


def place(owner, show, identity=None):
    """After Fab work on ``owner`` (a managed connection): keep its browser behind Angelo, or, when the person must
    act on listing ``identity`` (Add to My Library), reopen that listing cleanly (no Download dialog left open by
    the read) and bring the browser forward."""
    browser = getattr(owner, '_browser', None)
    pid = getattr(browser, '_pid', None)
    if not show:
        return background(pid)
    session = getattr(owner, '_session', None)
    if identity is not None and session is not None:
        try:
            browser.call('Page.navigate', {'url': 'https://www.fab.com/listings/' + identity}, session=session)
        except Exception:
            return False
    return foreground(pid)
