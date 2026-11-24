# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A link already in a folder the script may read, whose target is the network, is never followed by the script's own
open or by any native loader (test_blender_script_confinement; doc/angelo/script_confinement.rst, "Network";
break-it-confine-reparse-b7d499c S2).

* Windows: a symbolic link (by its long name and its 8.3 name), a chain through a local link, a relative link, a
  junction to a local folder holding the link, and a stream of the link, each whose target is a share nobody
  connected. Opening a path through one is the connection itself (the iso-win probe on a544c4b4b4d: SMB 0 -> 2), so
  each route (``open`` and every native loader of ``URL_CASES``) must be refused as the network before anything opens
  it. Symbolic links need the right to make them (an administrator, or Developer Mode): reported skipped.
* macOS and Linux: a link into ``/net/`` (the automounter of every host: even a stat there mounts the host) is refused
  as the network.

The targets are ``.invalid`` hosts: a refusal means nothing looked them up. Run only inside Blender, from the suite.

Windows tells a link on the way without following it only with "Strict script protection" on (Ruling 17: the walk's
cost is measurable against Angelo's own speed, so it is off by default; doc/angelo/script_confinement.rst, "Known
limits"): its cases run with the switch on, and the switch itself is checked (off by default; a script can't turn it
off).
"""

import contextlib
import os
import sys
import tempfile

from confinement_network_cases import URL_CASES

ROUTES = (("open", "open(URL).read()"),) + tuple(URL_CASES)


def _links_windows(folder):
    """{label: folder through which a file is named}, or None if this account may not make symbolic links."""
    import ctypes
    import _winapi
    remote = os.path.join(folder, "remote-share-link-with-a-long-name")
    try:
        os.symlink(r"\\angelo-probe.invalid\s", remote, target_is_directory=True)
    except OSError:
        return None
    os.symlink(remote, os.path.join(folder, "chain"), target_is_directory=True)
    os.symlink("remote-share-link-with-a-long-name", os.path.join(folder, "relative"), target_is_directory=True)
    inner = os.path.join(folder, "inner")
    os.mkdir(inner)
    os.symlink(r"\\angelo-probe.invalid\s", os.path.join(inner, "remote"), target_is_directory=True)
    _winapi.CreateJunction(inner, os.path.join(folder, "junction"))   # No right needed for a junction.
    paths = {"link": remote, "chain_of_links": os.path.join(folder, "chain"),
             "relative_link": os.path.join(folder, "relative"),
             "junction_to_a_folder_with_the_link": os.path.join(folder, "junction", "remote")}
    buffer = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetShortPathNameW(remote, buffer, 1024) and buffer.value.lower() != remote.lower():
        paths["short_name_of_a_link"] = buffer.value
    return paths


def strict_off(guard):
    """Strict script protection off again. Off is the person's (only this setting's own control turns it off): the
    suite stands in for the person through C's harness scope (this suite runs with --enable-event-simulate); a build
    without that rule takes the plain setter."""
    try:
        guard.set_strict_script_protection(False)
    except PermissionError:
        from angelo_agent import grant_guard
        grant_guard.harness(guard.set_strict_script_protection, False)


@contextlib.contextmanager
def strict_protection():
    """"Strict script protection" on for the cases inside, then back to what it was (set by this unconfined code). A
    build without the switch (a fail-first base) runs them as it is."""
    import _bpy
    guard = _bpy._angelo_guard
    if not hasattr(guard, "set_strict_script_protection"):
        yield
        return
    was = guard.strict_script_protection()
    guard.set_strict_script_protection(True)
    try:
        yield
    finally:
        if was:
            guard.set_strict_script_protection(True)
        else:
            strict_off(guard)


def strict_switch_cases(run, check):
    """Off by default; on, a confined script can't turn it off."""
    import _bpy
    guard = _bpy._angelo_guard
    if not hasattr(guard, "strict_script_protection"):
        check("strict_script_protection_switch_exists", False, "the guard has no strict_script_protection()")
        return
    check("strict_script_protection_off_by_default", guard.strict_script_protection() is False,
          guard.strict_script_protection())
    # Off, the stat family is CPython's own (no wrapper, no cost: Ruling 17); on, it is wrapped for the network
    # check. The first switch of this suite's process is below, so nothing wrapped it before.
    native = sys.modules["nt" if sys.platform == "win32" else "posix"]
    check("stat_family_unwrapped_while_strict_is_off", getattr(os.stat, "__self__", None) is native,
          repr(getattr(os.stat, "__self__", None)))
    # The number-ownership layer too (Head 20 read R-4): os.close, os.pipe and FileIO.close are CPython's own.
    import _io
    plain_close = type(_io.FileIO.__dict__["close"]).__name__ == "method_descriptor" and \
        getattr(_io.FileIO.__dict__["close"], "__objclass__", None) is _io.FileIO
    ownership_off = [name for name in ("close", "pipe", "dup2") if getattr(getattr(os, name), "__self__", None)
                     is not native]
    check("ownership_layer_unwrapped_while_strict_is_off", not ownership_off and plain_close,
          (ownership_off, plain_close))
    with strict_protection():
        check("stat_family_wrapped_once_strict_is_on", getattr(os.stat, "__self__", None) is not native,
              repr(getattr(os.stat, "__self__", None)))
        outcome = run("import _bpy\n_bpy._angelo_guard.set_strict_script_protection(False)")
        check("strict_script_protection_never_turned_off_by_a_script",
              "error" in outcome or outcome.get("refused") is not None, outcome)
        check("strict_script_protection_still_on_after_a_script", guard.strict_script_protection() is True,
              guard.strict_script_protection())
    check("strict_script_protection_restored", guard.strict_script_protection() is False,
          guard.strict_script_protection())
    check("stat_family_unwrapped_again_once_strict_is_off", getattr(os.stat, "__self__", None) is native and
          os.path.exists(os.__file__), repr(getattr(os.stat, "__self__", None)))
    strict_on_during_a_run(run, check, guard)
    check("ownership_layer_unwrapped_again_once_strict_is_off",
          getattr(os.close, "__self__", None) is native and getattr(os.pipe, "__self__", None) is native,
          (repr(getattr(os.close, "__self__", None)), repr(getattr(os.pipe, "__self__", None))))


def strict_on_during_a_run(run, check, guard):
    """Strict ON is allowed while a script runs, from an unconfined thread only (the preference's review): a region
    that began with it off goes on under the wrappers it installs and ends cleanly. Its own pipe, made after, works;
    the hot paths are free after; the flag is off again after."""
    import threading
    import host_waits
    if not hasattr(guard, "strict_script_protection"):
        return
    go, done, errors = threading.Event(), threading.Event(), []

    def turn_on():
        try:
            if go.wait(host_waits.CAP_S):
                guard.set_strict_script_protection(True)   # An Angelo thread: unconfined.
        except Exception as error:   # noqa: BLE001 - reported by the check.
            errors.append(repr(error))
        finally:
            done.set()

    helper = threading.Thread(target=turn_on, daemon=True)
    helper.start()
    held = {}
    try:
        # A pipe made before the switch turned on is used after it (S10: the run is tracked as it began), and one
        # made after it too.
        outcome = run("import os\nr0, w0 = os.pipe()\nGO.set()\nassert DONE.wait(CAP)\nHELD['on'] = STRICT()\n"
                      "os.write(w0, b'y')\nHELD['before'] = os.read(r0, 1)\nos.close(r0)\nos.close(w0)\n"
                      "r, w = os.pipe()\nos.write(w, b'x')\nHELD['read'] = os.read(r, 1)\nos.close(r)\nos.close(w)\n"
                      "HELD['stat'] = os.stat(os.__file__).st_size > 0\n",
                      GO=go, DONE=done, CAP=host_waits.CAP_S, STRICT=guard.strict_script_protection, HELD=held)
    finally:
        helper.join(host_waits.CAP_S)
        on_after = guard.strict_script_protection()
        strict_off(guard)   # Off again, as the person.
    check("strict_on_during_a_run_ends_cleanly",
          not errors and outcome.get("refused") is None and "error" not in outcome and held.get("on") is True and
          held.get("read") == b"x" and held.get("before") == b"y" and held.get("stat") is True and on_after is True,
          {"errors": errors, "outcome": outcome, "held": held, "on_after": on_after})
    import gc
    outcome = None   # Nothing of the script's kept: its code is collected, as the suite's own free check does.
    gc.collect()
    check("strict_on_during_a_run_leaves_the_hot_paths_free", guard.confine_open() == 0, guard.confine_open())
    check("strict_on_during_a_run_restored_off", guard.strict_script_protection() is False,
          guard.strict_script_protection())


def run_link_cases(run, check, NETWORK, skip):
    strict_switch_cases(run, check)
    with tempfile.TemporaryDirectory(prefix="angelo-link-cases-") as folder, strict_protection():
        if sys.platform == "win32":
            links = _links_windows(folder)
            if links is None:
                skip("link_to_an_unconnected_share_refused", "this account may not make symbolic links")
                return
            for label, through in links.items():
                for route, source in ROUTES:
                    outcome = run(source, URL=os.path.join(through, "x.wav"))
                    check("%s_to_an_unconnected_share_refused_%s" % (label, route),
                          outcome.get("refused") == NETWORK, outcome)
            outcome = run("open(URL).read()", URL=links["link"] + ":x")
            check("stream_of_a_link_to_an_unconnected_share_refused", outcome.get("refused") == NETWORK, outcome)
        else:
            link = os.path.join(folder, "net")
            os.symlink("/net/angelo-probe.invalid/s", link)
            for route, source in ROUTES:
                outcome = run(source, URL=os.path.join(link, "x.wav"))
                check("link_into_the_automounter_refused_" + route, outcome.get("refused") == NETWORK, outcome)
            outcome = run("open(URL).read()", URL="/net/angelo-probe.invalid/s/x.txt")
            check("automounter_name_refused_as_the_network", outcome.get("refused") == NETWORK, outcome)
