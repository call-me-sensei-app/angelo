# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The add-on a bench's Angelo side runs must be the app's own, and every trial records which it was.

``eval_live_agent_session.py`` puts the checkout's ``scripts/addons_core`` first on ``sys.path`` inside the app,
so an Angelo trial runs the checkout's host Python on the app's binary, not the add-on bundled in the app. When
merges land in the worktree a bench runs from, its later trials run newer host Python than the run's head, with
identical model-facing text but changed host behaviour.

So the bench (``bench_mcp_vs_angelo.py``, before anything runs) and each ``eval_live_agent.py`` run (before its
first trial, and again before each trial) refuse when the checkout's add-on and startup modules are not the app's
bundled ones (``native_addon_guard``, the native suites' check); ``--allow-addon-mismatch`` runs anyway and prints
the guard's warning. Every trial records ``stamp``: the add-on digest it imported, the app's bundled digest, the
commit and whether the checkout had uncommitted add-on changes. Measured runs come from pinned, detached run
worktrees that nobody merges into (doc/angelo/oneshot_tests.rst, "Pinned run worktrees").
"""

import subprocess
from pathlib import Path

import native_addon_guard as guard

OVERRIDE = guard.OVERRIDE
ROOT = Path(__file__).resolve().parents[3]
WATCHED = ("scripts/addons_core/angelo_agent", "scripts/startup")


def add_argument(parser):
    parser.add_argument(OVERRIDE, action="store_true",
                        help="run although the checkout's add-on, which the Angelo side imports, is not the app's "
                             "bundled one (native_addon_guard); the run prints a warning and every trial records both")


def _git(*arguments, root=ROOT):
    """``git`` output, or None. Popen, not ``subprocess.run``: the harness tests replace ``run`` to count trials."""
    try:
        process = subprocess.Popen(["git", "-C", str(root), *arguments], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True,
                                   env={"GIT_LFS_SKIP_SMUDGE": "1", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})
        output, _error = process.communicate(timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return output.strip() if process.returncode == 0 else None


def stamp(binary, root=ROOT, compared=None):
    """What a trial ran: the checkout's add-on digest, the app's bundled one (None without ``binary``, an MCP-only
    run), whether they match, the commit, and whether the add-on files had uncommitted changes."""
    if binary is not None:
        compared = compared or guard.check(binary, root)
    status = _git("status", "--porcelain", "--", *WATCHED, root=root)
    return {"addon_digest": compared["source"]["digest"] if compared else guard.source_digest(root),
            "app_addon_digest": compared["app"].get("digest") if compared else None,
            "matches_app": compared["matches"] if compared else None, "commit": _git("rev-parse", "HEAD", root=root),
            "uncommitted_addon_changes": None if status is None else bool(status), "checkout": str(root)}


def problem(binary, allow, root=ROOT, warn=print):
    """The refusal text when the checkout's add-on is not ``binary``'s bundled one, else None (``allow`` warns)."""
    compared = guard.check(binary, root)
    if compared["matches"]:
        return None
    if allow:
        warn(guard.warning(compared), end="", flush=True)
        return None
    return guard.refusal(compared).replace(
        "the app would test another add-on than this checkout's, so no suite ran",
        "the Angelo side imports this checkout's add-on, which is not the app's bundled one, so nothing ran")


def trial_stamp(binary, allow, root=ROOT):
    """One trial's ``stamp``, taken as it starts; a trial report that ends the run instead (``aborted``) when the
    checkout's add-on no longer matches the app's (changed since the run began) and ``allow`` is off."""
    compared = guard.check(binary, root)
    pinned = stamp(binary, root, compared)
    if compared["matches"] or allow:
        return pinned
    return {"tasks": [], "success": False, "addon": pinned,
            "aborted": "the checkout's add-on is no longer the app's bundled one (it changed after the run began); "
                       "no further trial runs: " + "; ".join(compared["differing"][:guard.LISTED])}
