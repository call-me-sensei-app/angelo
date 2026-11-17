# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A windowed launch that crashes on Windows: the crash dialog a person sees, recorded, then closed as they would.

On Windows a crash outside ``--background`` (``creator_signals.cc``, ``windows_exception_handler``) writes the crash
log, then shows Angelo's task dialog ("Angelo has stopped working": Restart, View Crash Log, Close) and waits for it;
the crash watchdog stands down once it is up. Only Close ends the process, with exit code 11 (``SIGSEGV``,
``sig_cleanup_and_terminate``). A host that only waits for the exit therefore waits forever (the T4 crash-start
proof, 2026-11-12). ``wait`` waits for either: the exit, or the crashed process's dialog, which it records (window
class and title, a PNG of it, and every text it shows, read through UI Automation as a screen reader reads it) and
closes with its Close button (UI Automation's Invoke on the button named Close, what a click does; only when that is
unavailable, the message the button sends, ``TDM_CLICK_BUTTON`` with ``IDCLOSE``, recorded as such), then waits for
the exit. macOS and Linux show no dialog: ``wait`` only waits there.

Standard library only; the waiting is tested with stand-ins by ``test_native_crash_dialog.py`` on every platform.
"""

import os
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

CLOSED_EXIT = 11   # SIGSEGV: the exit status after the dialog's Close (sig_cleanup_and_terminate).
DIALOG_CLASS = "#32770"   # A task dialog's window class.
IDCLOSE = 8
BUTTONS = {"Close": IDCLOSE, "Restart": 4}   # The dialog's buttons a run presses, and their ids (IDRETRY: Restart).
TDM_CLICK_BUTTON = 0x0400 + 102   # WM_USER + 102
# How a press counts as a person's: UI Automation's Invoke on the button, or a mouse click on the button's rectangle
# (a task dialog's DirectUI buttons are panes with no Invoke). The buttons' own message is only a fallback.
PRESSED_BY = frozenset({"ui_automation_invoke", "mouse_click"})


def wait(process, timeout, find=None, record=None, close=None, clock=time.monotonic, sleep=time.sleep):
    """Wait for ``process`` (a ``Popen``) to end, closing its crash dialog if one appears. Returns
    ``(returncode, dialog)``: ``dialog`` is None when none appeared, else what ``record`` returned plus whether
    ``close`` worked. Raises ``subprocess.TimeoutExpired`` (after killing the process) when neither happens in time."""
    windows = sys.platform == "win32"
    find = find or (_find if windows else (lambda pid: None))
    record, close = record or globals()["record"], close or _close
    deadline, dialog = clock() + timeout, None
    while True:
        code = process.poll()
        if code is not None:
            return code, dialog
        if dialog is None:
            window = find(process.pid)
            if window is not None:
                dialog = dict(record(window))
                closed = close(window)
                dialog.update(closed if isinstance(closed, dict) else {"closed": bool(closed)})
                deadline = clock() + 60   # Close ends the process: the clean-up has its own minute.
        if clock() > deadline:
            process.kill()
            process.wait()
            raise subprocess.TimeoutExpired(process.args, timeout)
        sleep(0.5)


# ---------------------------------------------------------------- Windows


def _user32():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
                                   wintypes.LPARAM]
    # Every handle typed: ctypes' default int would cut a 64-bit HWND or HDC.
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetDC.argtypes, user32.GetDC.restype = [wintypes.HWND], wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    return ctypes, wintypes, user32


def _gdi32(ctypes, wintypes):
    gdi32 = ctypes.WinDLL("gdi32")
    gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    return gdi32


def _text(user32, ctypes, function, window):
    buffer = ctypes.create_unicode_buffer(512)
    function(window, buffer, len(buffer))
    return buffer.value


def _find(pid):
    """The visible task dialog of process ``pid``, or None."""
    ctypes, wintypes, user32 = _user32()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(window, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if (owner.value == pid and user32.IsWindowVisible(window)
                and _text(user32, ctypes, user32.GetClassNameW, window) == DIALOG_CLASS):
            found.append(window)
            return False
        return True
    user32.EnumWindows(each, 0)
    return found[0] if found else None


def record(window, png=None):
    """The dialog's class and title, and a PNG of it (``png``: the path; default ``crash-dialog.png`` in
    ``$ANGELO_QA_DIR``, else the working folder)."""
    ctypes, wintypes, user32 = _user32()
    seen = {"class": _text(user32, ctypes, user32.GetClassNameW, window),
            "title": _text(user32, ctypes, user32.GetWindowTextW, window)}
    try:
        target = Path(png or os.path.join(os.environ.get("ANGELO_QA_DIR") or ".", "crash-dialog.png"))
        _png(target, *_capture(window))
        seen["screenshot"] = str(target)
    except Exception as error:   # The dialog is recorded and closed even when it cannot be pictured.
        seen["screenshot_error"] = "%s: %s" % (type(error).__name__, error)
    return seen


# Windows PowerShell's UI Automation client (part of Windows): the dialog's texts, then Invoke on its Close button.
UIA_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class CrashDialogMouse {
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int cx,
                                                                   int cy, uint flags);
  [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT point);
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hwnd);
}
'@
# Physical pixels for UI Automation's rectangles and the cursor alike (per-monitor aware v2).
[void][CrashDialogMouse]::SetProcessDpiAwarenessContext([IntPtr](-4))
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$handle = [IntPtr]::new(%d)
$A = [System.Windows.Automation.AutomationElement]
$dialog = $A::FromHandle($handle)
$all = $dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                       [System.Windows.Automation.Condition]::TrueCondition)
$elements = @($all | ForEach-Object { [pscustomobject]@{name = $_.Current.Name;
                                                       type = $_.Current.ControlType.ProgrammaticName} })
$texts = @($dialog.Current.Name) + @($elements | Where-Object { $_.name } | ForEach-Object { $_.name })
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$invoked = $false; $clicked = $false; $problem = $null; $by = $null; $named = $null
foreach ($element in $all) {
  if ($element.Current.Name -ne '%s') { continue }
  $named = $element
  $target = $element
  for ($up = 0; $up -lt 3 -and $target -ne $null; $up++) {
    $pattern = $null
    if ($target.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
      try { $by = $target.Current.ControlType.ProgrammaticName; $pattern.Invoke(); $invoked = $true }
      catch { $problem = $_.Exception.Message }
      break
    }
    $target = $walker.GetParent($target)
  }
  break
}
if (-not $invoked -and $named -ne $null) {
  # A task dialog's buttons can be panes with no Invoke (Windows' DirectUI): click the button as a person does, at
  # the middle of its own rectangle, with the dialog raised, after checking that point is the dialog's.
  [void][CrashDialogMouse]::SetWindowPos($handle, [IntPtr](-1), 0, 0, 0, 0, 0x43)   # TOPMOST, NOSIZE|NOMOVE|SHOW
  Start-Sleep -Milliseconds 300
  $box = $named.Current.BoundingRectangle
  $point = New-Object CrashDialogMouse+POINT
  $point.X = [int]($box.X + $box.Width / 2); $point.Y = [int]($box.Y + $box.Height / 2)
  $hit = [CrashDialogMouse]::GetAncestor([CrashDialogMouse]::WindowFromPoint($point), 2)   # GA_ROOT
  if ($box.Width -le 0 -or $hit -ne $handle) {
    $problem = 'the button at (' + $point.X + ', ' + $point.Y + ') is not on the dialog (' + $hit + ')'
  } else {
    [void][CrashDialogMouse]::SetCursorPos($point.X, $point.Y)
    [CrashDialogMouse]::mouse_event(0x2, 0, 0, 0, [UIntPtr]::Zero)   # LEFTDOWN
    Start-Sleep -Milliseconds 60
    [CrashDialogMouse]::mouse_event(0x4, 0, 0, 0, [UIntPtr]::Zero)   # LEFTUP
    for ($wait = 0; $wait -lt 40 -and [CrashDialogMouse]::IsWindow($handle); $wait++) { Start-Sleep -Milliseconds 50 }
    if ([CrashDialogMouse]::IsWindow($handle)) { $problem = 'clicked, but the dialog stayed open' }
    else { $clicked = $true; $by = $named.Current.ControlType.ProgrammaticName; $problem = $null }
  }
}
if (-not $invoked -and -not $clicked -and -not $problem) { $problem = 'no element named %s' }
[pscustomobject]@{texts = $texts; elements = $elements; invoked = $invoked; clicked = $clicked; by = $by;
                  problem = $problem} | ConvertTo-Json -Compress -Depth 4
"""


def _close(window):
    return press(window, "Close")


def press(window, button):
    """Read the dialog's texts and press its ``button`` (``BUTTONS``) as a person would: UI Automation's Invoke on the
    element named so (or its nearest invokable parent), else a mouse click at the middle of that element's rectangle,
    with the dialog raised and the point checked to be the dialog's (the T4 runs of 2026-11-12: Windows' DirectUI gives
    a task dialog's buttons as panes, with no Invoke). The button's own message is only a fallback, recorded as one
    (``closed_by`` outside ``PRESSED_BY``), so the run fails but still reaches its next launches. Returns ``{"texts", "pressed", "closed", "closed_by"[, "problem"]}``."""
    import json
    seen = {"texts": [], "pressed": button, "closed": False, "closed_by": None}
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                               UIA_SCRIPT % (int(window), button, button)], capture_output=True, text=True,
                              timeout=60)
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        texts = answer.get("texts") or []
        seen["texts"] = [texts] if isinstance(texts, str) else list(texts)
        seen["elements"] = answer.get("elements") or []   # Each element's name and control type, as UIA sees them.
        if answer.get("invoked"):
            seen.update(closed=True, closed_by="ui_automation_invoke", invoked_on=answer.get("by"))
        elif answer.get("clicked"):
            seen.update(closed=True, closed_by="mouse_click", invoked_on=answer.get("by"))
        if answer.get("problem"):
            seen["problem"] = answer["problem"]
    except Exception as error:   # Recorded; the fallback below still lets the run reach its next launches.
        seen["problem"] = "%s: %s" % (type(error).__name__, error)
    if not seen["closed"]:
        _, _, user32 = _user32()
        user32.SendMessageW(window, TDM_CLICK_BUTTON, BUTTONS[button], 0)
        seen.update(closed=True, closed_by="TDM_CLICK_BUTTON")
    return seen


def _capture(window):
    """``(width, height, rows)``: the window as top-down RGB rows, drawn by itself (``PrintWindow``)."""
    ctypes, wintypes, user32 = _user32()
    gdi32 = _gdi32(ctypes, wintypes)
    rect = wintypes.RECT()
    user32.GetWindowRect(window, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    gdi32.SelectObject(memory, bitmap)
    user32.PrintWindow(window, memory, 2)   # PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]
    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory)
    user32.ReleaseDC(None, screen)
    raw = pixels.raw
    rows = []
    for y in range(height):   # BGRA -> RGB
        line = raw[y * width * 4:(y + 1) * width * 4]
        rows.append(bytes(b for i in range(0, len(line), 4) for b in (line[i + 2], line[i + 1], line[i])))
    return width, height, rows


def _png(path, width, height, rows):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    data = b"".join(b"\x00" + row for row in rows)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(data, 6)) + chunk(b"IEND", b""))
