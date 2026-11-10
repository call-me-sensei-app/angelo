# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned ordinary-window metadata only; no title, URL, page, screenshot or credential inspection."""
import ctypes as C
from contextlib import contextmanager
import math
import sys
import time
from .fab_browser_process import BrowserProcess, BrowserUnavailable
from .fab_browser_ownership import snapshot, identity

INITIAL_STARTUP_SECONDS = 15


def _check_stop(stop):
    if stop is not None and stop.is_set():
        raise BrowserUnavailable('CANCELLED')


class _Point(C.Structure):
    _fields_ = [('x', C.c_double), ('y', C.c_double)]

class _Size(C.Structure):
    _fields_ = [('width', C.c_double), ('height', C.c_double)]

class _Rect(C.Structure):
    _fields_ = [('origin', _Point), ('size', _Size)]

def _frameworks():
    if sys.platform != 'darwin' or C.sizeof(C.c_void_p) != 8:
        raise BrowserUnavailable('PLATFORM_UNVERIFIED')
    cg = C.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    for name, result, args in (('CFArrayGetCount', C.c_long, [C.c_void_p]), ('CFArrayGetValueAtIndex', C.c_void_p, [C.c_void_p, C.c_long]), ('CFDictionaryGetValue', C.c_void_p, [C.c_void_p, C.c_void_p]), ('CFGetTypeID', C.c_ulong, [C.c_void_p]), ('CFNumberGetTypeID', C.c_ulong, []), ('CFDictionaryGetTypeID', C.c_ulong, []), ('CFNumberGetValue', C.c_bool, [C.c_void_p, C.c_long, C.c_void_p]), ('CFRelease', None, [C.c_void_p])):
        f = getattr(cf, name)
        f.restype = result
        f.argtypes = args
    cg.CGWindowListCopyWindowInfo.argtypes = [C.c_uint32, C.c_uint32]
    cg.CGWindowListCopyWindowInfo.restype = C.c_void_p
    cg.CGRectMakeWithDictionaryRepresentation.argtypes = [C.c_void_p, C.POINTER(_Rect)]
    cg.CGRectMakeWithDictionaryRepresentation.restype = C.c_bool
    keys = {n: C.c_void_p.in_dll(cg, n).value for n in ('kCGWindowOwnerPID', 'kCGWindowNumber', 'kCGWindowLayer', 'kCGWindowBounds')}
    if not all(keys.values()):
        raise BrowserUnavailable('WINDOW_METADATA_UNAVAILABLE')
    return (cg, cf, keys)

def _owned_manual_windows_locked(browser, *, seconds=2, require_front=False, _absolute_deadline=None, stop=None):
    """Before the human phase only. Requested startup URL is never a committed-origin/account proof."""
    if type(browser) is not BrowserProcess or not getattr(browser, '_manual_login', False) or browser._closed or getattr(browser, '_manual_auth_active', False):
        raise BrowserUnavailable('MANUAL_WINDOW_UNAVAILABLE')
    if type(seconds) not in (int, float) or not 0 < seconds <= INITIAL_STARTUP_SECONDS:
        raise BrowserUnavailable('WINDOW_LIMIT')
    _check_stop(stop)
    pid = browser._pid
    expected = browser._leader_identity
    deadline = time.monotonic() + seconds
    if _absolute_deadline is not None:
        if type(_absolute_deadline) is not float or not math.isfinite(_absolute_deadline):
            raise BrowserUnavailable('WINDOW_LIMIT')
        deadline = min(deadline, _absolute_deadline)
    if type(pid) is not int or pid <= 1 or type(expected) is not tuple or (len(expected) != 3) or (expected[0] != pid):
        raise BrowserUnavailable('MANUAL_OWNERSHIP_UNPROVED')

    def current():
        _check_stop(stop)
        row = snapshot(deadline).get(pid)
        _check_stop(stop)
        if row is None or row['zombie'] or identity(row) != expected:
            raise BrowserUnavailable('MANUAL_OWNERSHIP_UNPROVED')
        if time.monotonic() >= deadline:
            raise BrowserUnavailable('TIMEOUT')
    current()
    _check_stop(stop)
    cg, cf, keys = _frameworks()
    _check_stop(stop)

    def number(value):
        if not value or cf.CFGetTypeID(value) != cf.CFNumberGetTypeID():
            raise BrowserUnavailable('WINDOW_METADATA_UNAVAILABLE')
        output = C.c_int64()
        if not cf.CFNumberGetValue(value, 4, C.byref(output)):
            raise BrowserUnavailable('WINDOW_METADATA_UNAVAILABLE')
        return output.value
    array = cg.CGWindowListCopyWindowInfo(1 | 16, 0)
    if not array:
        raise BrowserUnavailable('WINDOW_METADATA_UNAVAILABLE')
    matches = []
    first_normal = True
    try:
        _check_stop(stop)  # The returned CFArray is already owned and must release even on cancellation.
        count = cf.CFArrayGetCount(array)
        if not 0 <= count <= 8192:
            raise BrowserUnavailable('WINDOW_LIMIT')
        for index in range(count):
            _check_stop(stop)
            if time.monotonic() >= deadline:
                raise BrowserUnavailable('TIMEOUT')
            item = cf.CFArrayGetValueAtIndex(array, index)
            # Only PID and layer are read for foreign windows. Bounds/ID are owned-only.
            owner_pid = number(cf.CFDictionaryGetValue(item, keys['kCGWindowOwnerPID']))
            if require_front:
                layer = number(cf.CFDictionaryGetValue(item, keys['kCGWindowLayer']))
                if layer != 0:
                    continue
                if first_normal:
                    first_normal = False
                    if owner_pid != pid:
                        raise BrowserUnavailable('MANUAL_FOREGROUND_UNPROVED')
            if owner_pid != pid:
                continue
            if not require_front and number(cf.CFDictionaryGetValue(item, keys['kCGWindowLayer'])) != 0:
                continue
            value = cf.CFDictionaryGetValue(item, keys['kCGWindowBounds'])
            rect = _Rect()
            if not value or cf.CFGetTypeID(value) != cf.CFDictionaryGetTypeID() or (not cg.CGRectMakeWithDictionaryRepresentation(value, C.byref(rect))):
                raise BrowserUnavailable('WINDOW_METADATA_UNAVAILABLE')
            bounds = {'left': rect.origin.x, 'top': rect.origin.y, 'width': rect.size.width, 'height': rect.size.height}
            if not all((math.isfinite(v) for v in bounds.values())) or not 0 < rect.size.width <= 16384 or (not 0 < rect.size.height <= 16384):
                raise BrowserUnavailable('WINDOW_LIMIT')
            native = number(cf.CFDictionaryGetValue(item, keys['kCGWindowNumber']))
            if not 0 < native < 2 ** 32:
                raise BrowserUnavailable('WINDOW_METADATA_UNAVAILABLE')
            matches.append({'native_window_id': native, 'bounds': bounds})
            if len(matches) > 8:
                raise BrowserUnavailable('WINDOW_LIMIT')
    finally:
        cf.CFRelease(array)
    current()
    if not matches:
        raise BrowserUnavailable('MANUAL_WINDOW_NOT_READY')
    return tuple(matches)

def owned_manual_windows(browser, *, seconds=2):
    """The same lock orders metadata reads before begin_manual_auth; no read starts after its return."""
    if type(browser) is not BrowserProcess:
        raise BrowserUnavailable('MANUAL_WINDOW_UNAVAILABLE')
    if type(seconds) not in (int, float) or not 0 < seconds <= 2:
        raise BrowserUnavailable('WINDOW_LIMIT')
    with browser._lock:
        return _owned_manual_windows_locked(browser, seconds=seconds)

@contextmanager
def _application_api():
    """Public exact-PID AppKit requests in a local pool; never a cached termination certificate."""
    if sys.platform != 'darwin':
        raise BrowserUnavailable('PLATFORM_UNVERIFIED')
    C.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
    objc = C.CDLL('/usr/lib/libobjc.A.dylib')
    objc.objc_getClass.argtypes = [C.c_char_p]
    objc.objc_getClass.restype = C.c_void_p
    objc.sel_registerName.argtypes = [C.c_char_p]
    objc.sel_registerName.restype = C.c_void_p
    address = C.cast(objc.objc_msgSend, C.c_void_p).value

    def send(result, args, obj, selector, *values):
        method = C.CFUNCTYPE(result, C.c_void_p, C.c_void_p, *args)(address)
        return method(obj, objc.sel_registerName(selector), *values)
    appclass = objc.objc_getClass(b'NSRunningApplication')
    poolclass = objc.objc_getClass(b'NSAutoreleasePool')
    threadclass = objc.objc_getClass(b'NSThread')
    if not threadclass or not (send(C.c_bool, [], threadclass, b'isMainThread') or
                               send(C.c_bool, [], threadclass, b'isMultiThreaded')):
        raise BrowserUnavailable('MANUAL_COCOA_THREAD_UNSUPPORTED')
    if not appclass or not poolclass:
        raise BrowserUnavailable('MANUAL_FOREGROUND_UNPROVED')
    # Each serialized Python worker owns a local pool; no AppKit main-loop caching is used as proof.
    pool = send(C.c_void_p, [], poolclass, b'alloc')
    pool = send(C.c_void_p, [], pool, b'init') if pool else None
    if not pool:
        raise BrowserUnavailable('MANUAL_FOREGROUND_UNPROVED')
    try:
        def activate(pid):
            app = send(C.c_void_p, [C.c_int], appclass, b'runningApplicationWithProcessIdentifier:', pid)
            if not app:
                return False
            # No ignoringOtherApps, security, or accessibility option is requested.
            return bool(send(C.c_bool, [C.c_ulong], app, b'activateWithOptions:', 0))
        def terminate(pid):
            app = send(C.c_void_p, [C.c_int], appclass, b'runningApplicationWithProcessIdentifier:', pid)
            return bool(app and send(C.c_bool, [], app, b'terminate'))
        yield activate, terminate
    finally:
        send(None, [], pool, b'drain')


@contextmanager
def _activation_api():
    """Keep the existing activation-only internal interface; same pool and thread prerequisite."""
    with _application_api() as operations:
        yield operations[0]


def foreground_manual_window(browser, *, seconds=2, stop=None):
    """Initial startup15s, then foreground proof<=2s. No titles/page data; caller cancellation never grants readiness."""
    if type(browser) is not BrowserProcess:
        raise BrowserUnavailable('MANUAL_WINDOW_UNAVAILABLE')
    with browser._lock:
        if type(seconds) not in (int, float) or not 0 < seconds <= 2:
            raise BrowserUnavailable('WINDOW_LIMIT')
        _check_stop(stop)
        startup_deadline = time.monotonic() + INITIAL_STARTUP_SECONDS
        while True:
            _check_stop(stop)
            remaining = startup_deadline - time.monotonic()
            if remaining <= 0:
                raise BrowserUnavailable('MANUAL_WINDOW_NOT_READY')
            try:
                before = _owned_manual_windows_locked(browser, seconds=remaining, _absolute_deadline=startup_deadline, stop=stop)
                break
            except BrowserUnavailable as error:
                if error.code != 'MANUAL_WINDOW_NOT_READY':
                    raise
                _check_stop(stop)
                remaining = startup_deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(.025, remaining))
                _check_stop(stop)
        if len(before) != 1:
            raise BrowserUnavailable('MANUAL_FOREGROUND_UNPROVED')
        _check_stop(stop)
        if time.monotonic() >= startup_deadline:
            raise BrowserUnavailable('TIMEOUT')
        deadline = time.monotonic() + seconds  # A distinct fixed foreground budget, not a refreshed startup budget.
        with _activation_api() as activate:
            _check_stop(stop)
            if time.monotonic() >= deadline or not activate(browser._pid):
                raise BrowserUnavailable('MANUAL_FOREGROUND_UNPROVED')
            _check_stop(stop)
            while True:
                _check_stop(stop)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BrowserUnavailable('MANUAL_FOREGROUND_UNPROVED')
                try:
                    # CG optionOnScreenOnly is freshly ordered front-to-back. Skip nonzero system layers.
                    after = _owned_manual_windows_locked(browser, seconds=remaining, require_front=True, _absolute_deadline=deadline, stop=stop)
                except BrowserUnavailable as error:
                    if error.code != 'MANUAL_FOREGROUND_UNPROVED':
                        raise
                    _check_stop(stop)
                    time.sleep(min(.025, max(0, deadline - time.monotonic())))
                    _check_stop(stop)
                    continue
                if after != before:
                    raise BrowserUnavailable('MANUAL_FOREGROUND_UNPROVED')
                _check_stop(stop)
                if time.monotonic() >= deadline:
                    raise BrowserUnavailable('TIMEOUT')
                return {'pid': browser._pid, **after[0], 'foreground_verified': True}
