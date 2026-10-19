# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Without window focus, macOS never orders an Angelo window above another application's windows.

``--no-window-focus`` is documented as "Open behind other windows and without taking focus", and the
Windows GHOST lowers a new window to the bottom and shows it without activation. On macOS an
inactive application's ``orderFront:`` still puts its window above every other application, so
every GHOST Cocoa call that raises a window goes through ``GHOST_WindowCocoa::orderFrontAndMakeKey``:
with focus (the default) it is exactly ``makeKeyAndOrderFront:``; without focus it orders the window
relative to the application's own front-most window, or to the back. This test keeps every raise in
the GHOST Cocoa sources inside that helper or under a window-focus guard, so an upstream merge or a
new call cannot bring the old ordering back. What the compiled app does on screen was measured
(``doc/angelo/performance.rst``, "Window state of GUI runs").
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GHOST = ROOT / "intern/ghost/intern"
HELPER = "GHOST_WindowCocoa::orderFrontAndMakeKey"
# Selectors that put a window in front of other applications or activate the application.
RAISES = ("makeKeyAndOrderFront:", "orderFront:", "orderFrontRegardless", "orderWindow:",
          "activateIgnoringOtherApps:", "activateWithOptions:", "[NSApp activate]",
          "orderFrontStandardAboutPanel")
# Raises outside the helper and any focus guard, each with the reason it cannot happen in an automated run.
ALLOWED = {
    # The app menu's About item: an accessory application shows its menu bar only once the user activated it.
    ("orderFrontStandardAboutPanel", "showAboutPanel"),
    # Without focus, the activation guard hands an activation the user did not ask for back to the application
    # that was active before (it activates that application, never this one).
    ("activateWithOptions:", "applicationDidBecomeActive"),
}
FOCUS_GUARD = re.compile(r"^if \((system_cocoa_->)?window_focus_\)$")


def blank_comments(source):
    """The source with comments and string literals replaced by spaces, keeping every offset."""
    def blank(match):
        return re.sub(r"[^\n]", " ", match.group(0))
    return re.sub(r'//[^\n]*|/\*.*?\*/|@?"(?:\\.|[^"\\\n])*"', blank, source, flags=re.S)


def enclosing_headers(code, index):
    """The text before each brace that encloses ``index``, innermost first (statement headers)."""
    stack = []
    for position, char in enumerate(code[:index]):
        if char == "{":
            stack.append(position)
        elif char == "}" and stack:
            stack.pop()
    headers = []
    for brace in reversed(stack):
        start = max(code.rfind(token, 0, brace) for token in (";", "{", "}", "\n\n"))
        headers.append(" ".join(code[start + 1:brace].split()))
    return headers


def cocoa_sources():
    return sorted(GHOST.glob("*Cocoa*.mm")) + sorted(GHOST.glob("GHOST_Context*.mm"))


def raises(path):
    """Every raise in ``path``: ``(selector, line, headers)``."""
    source = path.read_text(encoding="utf-8")
    code = blank_comments(source)
    found = []
    for selector in RAISES:
        for match in re.finditer(re.escape(selector), code):
            line = code.count("\n", 0, match.start()) + 1
            found.append((selector, line, enclosing_headers(code, match.start())))
    return found


def function_of(headers):
    """The name of the function or Objective-C method a raise is in (the outermost header)."""
    outer = headers[-1] if headers else ""
    method = re.match(r"^[-+] \([^)]*\)(\w+)", outer)
    if method:
        return method.group(1)
    function = re.search(r"([\w:]+)\(", outer)
    return function.group(1) if function else outer


class GhostCocoaWindowOrder(unittest.TestCase):
    def test_every_raise_is_the_helper_or_focus_guarded(self):
        unguarded = []
        for path in cocoa_sources():
            for selector, line, headers in raises(path):
                function = function_of(headers)
                if function == HELPER or any(FOCUS_GUARD.match(header) for header in headers):
                    continue
                if (selector, function) in ALLOWED:
                    continue
                unguarded.append("%s:%d %s in %s" % (path.name, line, selector, function))
        self.assertEqual(unguarded, [], "raise a window through %s, or guard it with window_focus_" % HELPER)

    def test_the_helper_is_makekeyandorderfront_with_focus_and_never_raises_without(self):
        source = blank_comments((GHOST / "GHOST_WindowCocoa.mm").read_text(encoding="utf-8"))
        body = source.split("void %s(NSWindow *window, const bool window_focus)" % HELPER, 1)[1].split("\n}\n", 1)[0]
        focused, unfocused = body.split("return;", 1)
        self.assertEqual(" ".join(focused.split()), "{ if (window_focus) { [window makeKeyAndOrderFront:nil];")
        for selector in ("makeKeyAndOrderFront:", "orderFront:", "orderFrontRegardless", "activate"):
            self.assertNotIn(selector, unfocused)
        # Relative to this application's own front-most window, or to the back; key within the application.
        self.assertIn("[window orderWindow:NSWindowAbove relativeTo:front_window.windowNumber];", unfocused)
        self.assertIn("[window orderBack:nil];", unfocused)
        self.assertIn("[window makeKeyWindow];", unfocused)
        self.assertIn("other != window && other.isVisible && other.isOnActiveSpace && other.level == window.level",
                      " ".join(unfocused.split()))

    def test_every_former_raise_site_uses_the_helper(self):
        # Window creation, setOrder(top), setOrder(bottom)'s next key window, the next key window when the key
        # window closes (two searches), dialogs when the application becomes active, and an open-document request.
        window = (GHOST / "GHOST_WindowCocoa.mm").read_text(encoding="utf-8")
        system = (GHOST / "GHOST_SystemCocoa.mm").read_text(encoding="utf-8")
        self.assertEqual(window.count("orderFrontAndMakeKey("), 4)  # three calls and the definition
        self.assertEqual(system.count("GHOST_WindowCocoa::orderFrontAndMakeKey("), 4)

    def test_without_focus_an_unrequested_activation_is_handed_back(self):
        # macOS activates an application its front-most application started once it shows a window, whatever its
        # activation policy; the guard (created only without focus) puts the windows back and reactivates the other.
        system = blank_comments((GHOST / "GHOST_SystemCocoa.mm").read_text(encoding="utf-8"))
        focus = system.split("void GHOST_SystemCocoa::useWindowFocus(const bool use_focus)", 1)[1].split("\n}\n", 1)[0]
        unfocused = focus.split("else if (use_focus", 1)[0]
        self.assertIn("if (!use_focus && !no_focus_activity_)", " ".join(unfocused.split()))
        self.assertIn("no_focus_activation_guard_ = [[GHOSTNoFocusActivationGuard alloc] init];", unfocused)
        self.assertEqual(system.count("[GHOSTNoFocusActivationGuard alloc]"), 1)
        handler = system.split("- (void)applicationDidBecomeActive", 1)[1].split("\n}\n", 1)[0]
        flat = " ".join(handler.split())
        self.assertTrue(flat.startswith("{ if ([self isUserClick]) { return; }"))
        self.assertIn("if (window.isVisible && window.parentWindow == nil) { [window orderBack:nil]; }", flat)
        self.assertIn("[previous_app_ activateWithOptions:0];", flat)
        remembered = system.split("- (void)rememberApplication:", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("app.activationPolicy != NSApplicationActivationPolicyRegular", " ".join(remembered.split()))
        # A handed-back activation leaves no per-loop catch-up; with focus the retry is unchanged.
        retry = " ".join(system.split("if (need_delayed_application_become_active_event_processing_) {", 1)[1]
                         .split("if (outside_loop_event_processed_)", 1)[0].split())
        self.assertEqual(retry, "if (!window_focus_ && !NSApp.isActive) { "
                                "need_delayed_application_become_active_event_processing_ = false; } "
                                "else { handleApplicationBecomeActiveEvent(); } }")

    def test_window_state_changes_stay_in_set_state(self):
        # Full screen and restoring a minimized window are state changes macOS performs itself (and animates in
        # front); only setState, which a user operator or a saved window state asks for, may make them.
        window = blank_comments((GHOST / "GHOST_WindowCocoa.mm").read_text(encoding="utf-8"))
        for selector in ("toggleFullScreen:", "deminiaturize:"):
            self.assertTrue(re.search(re.escape(selector), window), selector)
            for match in re.finditer(re.escape(selector), window):
                headers = enclosing_headers(window, match.start())
                if headers and headers[-1].startswith("@interface"):
                    continue   # The delegate's declaration list.
                self.assertEqual(function_of(headers), "GHOST_WindowCocoa::setState", selector)

    def test_without_focus_a_covered_window_does_not_wait_for_a_drawable(self):
        # An occluded window gets a free drawable about once a second; without focus its frames are drawn but not
        # presented while covered, and it is redrawn once it shows. With focus, every frame is presented as before.
        header = (GHOST / "GHOST_ContextMTL.hh").read_text(encoding="utf-8")
        self.assertIn("bool present_when_occluded_ = true;", header)
        metal = blank_comments((GHOST / "GHOST_ContextMTL.mm").read_text(encoding="utf-8"))
        swap = " ".join(metal.split("void GHOST_ContextMTL::metalSwapBuffers()", 1)[1].split("\n}\n", 1)[0].split())
        skip = ("if (!present_when_occluded_) { NSWindow *window = metal_view_.window; if (window && "
                "!(window.occlusionState & NSWindowOcclusionStateVisible)) { return; } }")
        self.assertIn("updateDrawingContext(); " + skip + " id<CAMetalDrawable> drawable = [metal_layer_ nextDrawable];",
                      swap)
        window = " ".join(blank_comments((GHOST / "GHOST_WindowCocoa.mm").read_text(encoding="utf-8")).split())
        self.assertIn("if (!system_cocoa_->window_focus_) { context->setPresentWhenOccluded(false); }", window)
        self.assertEqual(window.count("setPresentWhenOccluded("), 1)
        redraw = window.split("- (void)windowDidChangeOcclusionState:(NSNotification *)notification {", 1)[1]
        self.assertTrue(redraw.strip().startswith("if (!system_cocoa_->window_focus_ &&"))

    def test_the_documented_flag_contract(self):
        args = (ROOT / "source/creator/creator_args.cc").read_text(encoding="utf-8")
        self.assertIn('"Open behind other windows and without taking focus."', args)
        win32 = (GHOST / "GHOST_WindowWin32.cc").read_text(encoding="utf-8")
        self.assertIn("::SetWindowPos(h_wnd_, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);",
                      win32)

    def test_the_guard_finds_an_unguarded_raise(self):
        # A blind check passes everything: an unguarded raise in a method must be reported, a guarded one not.
        code = blank_comments("- (void)f\n{\n  [w makeKeyAndOrderFront:nil];\n  if (window_focus_) {\n"
                              "    [NSApp activateIgnoringOtherApps:YES];\n  }\n}\n")
        first = enclosing_headers(code, code.index("makeKeyAndOrderFront"))
        second = enclosing_headers(code, code.index("activateIgnoringOtherApps"))
        self.assertEqual(function_of(first), "f")
        self.assertFalse(any(FOCUS_GUARD.match(header) for header in first))
        self.assertTrue(any(FOCUS_GUARD.match(header) for header in second))


if __name__ == "__main__":
    unittest.main()
