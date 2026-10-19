# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The window flags of every automated launch that opens windows: Angelo, or stock Blender beside it.

Every suite runner, host driver, live eval, benchmark, stress script and smoke gets its window flags from
``window_flags()``, so none can start a window that covers or interrupts the person using the machine.
``test_native_gui_launch_focus.py`` fails when a launcher builds a window command without it, unless the launch is
listed in ``FOCUSED_LAUNCHES`` with its reason.

Window states:

* ``behind`` (the default): ``--no-window-focus``. The app opens its windows behind other applications' windows and
  never takes the keyboard. On macOS Angelo also hands back the activation macOS gives an application its front-most
  application started, and does not present frames while a window is fully covered (the frame is still drawn, and
  captures read it). Stock Blender behaves as Angelo on Windows and Linux (the same GHOST code), but on macOS it
  activates and orders its windows above other applications whatever the flag, so the two sides of a comparison do
  not share a window state there (``side_window_state``).
* ``front``: no ``--no-window-focus`` on either side. Both apps activate and draw in front, presenting every frame at
  the display's rate: the conditions a person using the app has. It takes the screen and the keyboard, so only on a
  machine nobody is using. ``matched_window_state`` is the state both sides share on a platform (front on macOS,
  behind elsewhere); the timed campaign (``final_runs_plan.window_state``) uses it for every two-sided step.

Input isolation (``--enable-event-simulate``, on by default) makes the app ignore the machine's real keyboard and
mouse, so a key meant for another application cannot change a run. A launch that needs real input turns it off.
"""

import sys

WINDOW_STATES = ("behind", "front")
NO_WINDOW_FOCUS = "--no-window-focus"
INPUT_ISOLATION = "--enable-event-simulate"

# Launchers that deliberately open focused windows, as "file.py:function" -> reason. A demo recording needs none:
# it captures Blender's own frame-buffer, which needs no focus.
_UPDATE_E2E = ("the update end-to-end starts the installed old release as a person does and its driver presses "
               "Restart to Update with real window events; the updater relaunches the new release itself, with no "
               "flag, so both windows are a person's (a dedicated test machine, never a person's session)")
FOCUSED_LAUNCHES = {"tests/python/angelo_agent/update_e2e_macos.py:run": _UPDATE_E2E,
                    "tests/python/angelo_agent/update_e2e_windows.py:run": _UPDATE_E2E}


def add_arguments(parser, both_sides=False):
    """A harness's ``--input-isolation`` and ``--window-state`` options (``both_sides``: a comparison's wording)."""
    sides = "both sides: " if both_sides else ""
    parser.add_argument("--input-isolation", choices=("on", "off"), default="on",
                        help=sides + "on (default): --enable-event-simulate, so Blender ignores real keyboard/mouse "
                             "input and a person or agent typing on this machine cannot start playback or edit a "
                             "trial's scene; its main loop then never idle-sleeps (one busy core)")
    parser.add_argument("--window-state", choices=WINDOW_STATES, default="behind",
                        help=sides + "behind (default): --no-window-focus, trial windows open behind the person's "
                             "applications and never take focus (on macOS stock Blender still opens in front, and a "
                             "comparison says so); front: the apps activate and draw in front, as a person uses them, "
                             "for timed comparisons on a machine nobody is using")


def window_flags(window_state="behind", input_isolation=True):
    """The window flags for one launch that opens windows, in a fixed order."""
    if window_state not in WINDOW_STATES:
        raise ValueError("window state must be one of %s, not %r" % (", ".join(WINDOW_STATES), window_state))
    return ([NO_WINDOW_FOCUS] if window_state == "behind" else []) + ([INPUT_ISOLATION] if input_isolation else [])


def side_window_state(side, window_state, platform=None):
    """What the windows of one side of a comparison get: ``angelo``, or ``mcp`` (stock Blender), for run plans.

    Measured on macOS 26 (doc/angelo/performance.rst, "Window state of GUI runs"); on Windows both GHOSTs lower a
    no-focus window to the bottom unactivated; on Linux both run the same X11/Wayland code, so the sides match.
    """
    platform = sys.platform if platform is None else platform
    if window_state not in WINDOW_STATES:
        raise ValueError("window state must be one of %s, not %r" % (", ".join(WINDOW_STATES), window_state))
    if window_state == "front":
        return "front: activated, in front of other applications, every frame presented"
    if platform == "darwin":
        return ("front: stock Blender orders its windows above other applications and activates on macOS despite "
                "--no-window-focus" if side == "mcp" else
                "behind: under other applications' windows, activation handed back, no frames presented while covered")
    if platform == "win32":
        return "behind: lowered under other applications' windows, never activated"
    return "not activated; placed by the window manager"


def matched_window_state(platform=None):
    """The window state both sides of a comparison share on ``platform``: behind where stock Blender honours
    --no-window-focus as Angelo does, front where it does not (macOS)."""
    platform = sys.platform if platform is None else platform
    return "front" if platform == "darwin" else "behind"


def window_symmetry_warning(window_state, sides=("mcp", "angelo"), platform=None):
    """A line for the run's output when the sides' window states differ (timings are then not comparable), or ''."""
    states = {side: side_window_state(side, window_state, platform) for side in sides}
    if len(set(states.values())) < 2:
        return ""
    return ("Window states differ between the sides (%s); their timings are not comparable. Timed comparisons use "
            "--window-state front on a machine nobody is using." % "; ".join("%s %s" % item for item in states.items()))
