# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The update's background preparation inside Blender, and what the update card reads about it.

``update_ui`` finds an update; ``consider`` decides whether Angelo can install
it itself (the feed allows it, this install can replace itself, earlier
attempts did not fail twice, and for a newer release line the person said yes:
``accept``) and, if so, starts one worker thread that runs
``update_flow.Preparer.prepare``: the download, then unpacking and verification.
The persistent ``tick`` collects the result on the main thread, shows the
"ready" card (unless the person chose Later or Skip for that version), retries
a download that failed for a passing reason, and throttles a download while an
agent turn runs. The card and the status bar read ``phase``, ``progress`` and
``wait_reason``, which never touch a file or the network.

Restarting is ``update_restart``. The preparation and restart modules are
imported only once an update is offered, so a launch without one loads none of
them. Contract: doc/angelo/updates_and_reports.rst, "Installing updates".
"""

import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import bpy

from . import app_version, card_host, update_feed, update_state, work_guard

AGENT_RATE = 2 * 1024 * 1024        # Bytes a second while an agent turn runs: the agent keeps the connection.
RETRY_AFTER = (900, 3600, 4 * 3600)  # A download that failed for a passing reason is tried again after these.
INSTALL_ATTEMPTS = 2                 # Failed installations of one version before it is offered as a download.

_lock = threading.Lock()
_job = None          # {"version", "phase", "done", "total", "result", "cancel", "thread"} of the running worker
_ready = {}          # version -> update_flow.Ready
_failed = {}         # version -> (code, transient, when, attempts)
_offers = {}         # version -> the Offer being prepared (to show its card when it is ready)
_agent_busy = False
_wait = ""           # The current work_guard reason (refreshed by tick, read by the card).
_results_handled = False
_restarting = None   # The version a restart is installing.
_support_reason = None             # "" when this install can replace itself (``_support``); None: not asked yet.
_install_failures = ("", 0)        # (version, failed installations in a row), from ``updates.json``.
_accepted_line = ""                # The newer release line the person chose to move to, from ``updates.json``.


def directory():
    """``<cache>/updates``, or None when Blender reports no absolute cache or temporary folder (no updates then)."""
    for folder in (getattr(bpy.app, "cachedir", ""), bpy.app.tempdir):
        if folder and os.path.isabs(folder):
            return Path(folder) / "updates"
    return None


def upgrade_code():
    try:
        import angelo_identity
        return angelo_identity.data()["windows"]["msi_upgrade_code"]
    except (ImportError, KeyError, TypeError):
        return ""


def _flow():
    from . import update_flow
    return update_flow


def _restart_module():
    from . import update_restart
    return update_restart


def background_allowed():
    """Whether Angelo may download on its own now: automatic checks on, no policy against them, not offline."""
    from . import update_ui
    return update_ui.automatic_allowed()


def preparer():
    """This install's ``Preparer``, or None when there is nowhere to keep updates."""
    root = directory()
    if root is None:
        return None
    return _flow().Preparer(root, app_version.current(), update_feed.platform_key(), bpy.app.binary_path,
                                upgrade_code())


def _support():
    """Whether this install can replace itself, asked once per session (it reads the disk or Windows Installer)."""
    global _support_reason
    if _support_reason is None:
        instance = preparer()
        reason = instance.support()[0] if instance is not None else "location"
        _support_reason = "location_windows" if reason == "location" and sys.platform.startswith("win") else reason
    return _support_reason


def _load_install_failures():
    global _install_failures, _accepted_line
    state = update_state.load(_state_directory())
    _install_failures = (state.install_failed_version, state.install_failures)
    _accepted_line = state.accepted_line or _accepted_line   # A yes this session outlives an unwritable state folder.


def automatic(offer):
    """"" when Angelo can install ``offer`` itself, else the reason (``update_card.WHY_MANUAL`` keys, "manual", or
    "choice" for a newer line that only waits for the person's yes).

    Reads only what ``consider`` and ``tick`` cached, so the card may call it while it is laid out.
    """
    if (offer is None or offer.download is None or not offer.release.automatic_install
            or not update_feed.installable(offer.download.url)):
        return "manual"
    version = offer.release.version
    if _install_failures[0] == version and _install_failures[1] >= INSTALL_ATTEMPTS:
        return "failed"
    failure = _failed.get(version)
    if failure is not None and not failure[1]:
        return "failed"
    if _support_reason:
        return _support_reason
    if not offer.choice or update_feed.line_of(version) == _accepted_line:
        return ""
    from . import update_ui
    return "manual" if update_ui.explicit_offline() else "choice"   # In offline mode the person is sent to the download.


def _state_directory():
    from . import update_ui
    return update_ui.directory()


def _handle_results_once():
    """The previous session's installer result, read before anything decides about an update."""
    global _results_handled
    if _results_handled:
        return
    _results_handled = True
    instance = preparer()
    if instance is not None:
        _restart_module().handle_results(instance, _state_directory())


def _supersede(version):
    """Forget everything about versions older than ``version``: a newer release replaces them, whether or not
    Angelo can install the newer one itself (an older download running for it is stopped)."""
    global _job
    newer = app_version.parse(version)
    for table in (_failed, _offers, _ready):
        for old in [v for v in table if app_version.parse(v) < newer]:
            table.pop(old, None)
    with _lock:
        job = _job
        if job is not None and app_version.parse(job["version"]) < newer:
            job["cancel"].set()
            _job = None


def consider(offer, manual=False):
    """Start preparing ``offer`` when Angelo can install it itself; True when it will (the card follows it).

    ``manual``: the person asked (Check for Updates…); otherwise only when automatic checks are allowed.
    """
    _handle_results_once()
    _support()
    _load_install_failures()
    chosen = target(offer)
    version = chosen.release.version
    if offer.own_skipped and chosen is offer and not manual:
        return False   # The person skipped this update; the newer line beside it still shows.
    if chosen is offer:
        _supersede(version)
    if automatic(chosen):
        _ready.pop(version, None)   # A release the feed (or a later failure) took out of automatic install.
        return False
    _offers[version] = offer
    if not manual and not background_allowed():
        return version in _ready
    if version in _ready or (_job is not None and _job["version"] == version):
        return True
    failure = _failed.get(version)
    if failure is not None and not manual and time.time() < failure[2] + RETRY_AFTER[min(failure[3], 3) - 1]:
        return True   # A passing failure: tick tries again after the backoff.
    _start(chosen)
    return True


def target(offer):
    """Which side of ``offer`` Angelo prepares: the newer line once the person said yes and it can be installed;
    otherwise the update of their own line, which comes back by itself if the newer line can't be prepared."""
    later = offer.later
    if later is None or update_feed.line_of(later.release.version) != _accepted_line or automatic(later):
        return offer
    return later


def accept(offer, whole=None):
    """The person said yes to a newer line (``offer``, in the card's offer ``whole``): prepare it now, as Check for
    Updates… would. A prepared update of their own line stays; a running one stops and is prepared again if they
    change their mind (``update_ui.decide``) or the newer line can't be prepared (the next check)."""
    global _accepted_line
    from . import update_ui
    update_ui.checker().accept(offer)
    _accepted_line = update_feed.line_of(offer.release.version)   # Also when the state folder can't be written.
    consider(whole or offer, manual=True)


def withdraw(offer):
    """The person stays off a newer line: nothing of it runs, comes back or stays on disk."""
    global _accepted_line, _job
    _accepted_line = ""
    version = offer.release.version
    for table in (_failed, _ready):
        table.pop(version, None)
    for held, whole in list(_offers.items()):
        if held == version:
            del _offers[held]
        elif whole.later is not None and whole.later.release.version == version:
            _offers[held] = replace(whole, later=None)
    with _lock:
        job = _job
        if job is not None and job["version"] == version:
            job["cancel"].set()
            _job = None
    instance = preparer()
    if instance is not None:
        def forget():   # Off the main thread: a stage is gigabytes. A worker still writing there removes it as it stops.
            instance.forget(version, unless=lambda: _wanted(version))
        threading.Thread(target=forget, name="angelo-update-forget", daemon=True).start()


def _wanted(version):
    """Whether ``version`` is being prepared or ready again (the person said yes again before its removal ran)."""
    job = _job
    return version in _ready or (job is not None and job["version"] == version)


def _start(offer):
    global _job
    if _job is not None and _job["thread"].is_alive():
        _job["cancel"].set()   # A newer version supersedes the one being prepared.
    flow, instance, cancel = _flow(), preparer(), threading.Event()
    job = {"version": offer.release.version, "phase": "downloading", "done": 0, "total": offer.download.size,
           "result": None, "cancel": cancel, "clean": not offer.choice}   # A yes can be taken back: the own update stays.

    def progress(done, total):
        with _lock:
            job["done"], job["total"] = done, total
            job["phase"] = "preparing" if done >= total else "downloading"

    def work():
        try:
            result = instance.prepare(offer.release, offer.download, cancel=cancel, progress=progress,
                                      limit=lambda: AGENT_RATE if _agent_busy else None)
        except flow.PrepareError as error:
            result = error
        except Exception as error:   # A worker must never end silently or take Blender down with it; retried later.
            result = flow.PrepareError("exception:" + type(error).__name__, transient=True)
        with _lock:
            job["result"] = result

    job["thread"] = threading.Thread(target=work, name="angelo-update-prepare", daemon=True)
    _job = job
    try:
        job["thread"].start()
    except RuntimeError as error:   # No thread to prepare on: a passing failure, collected and retried (``tick``).
        with _lock:
            job["result"] = flow.PrepareError("exception:" + type(error).__name__, transient=True)


def phase(offer):
    """``notify``, ``downloading``, ``preparing``, ``ready`` or ``restarting`` for the card."""
    version = offer.release.version
    if _restarting == version:
        return "restarting"
    if version in _ready:
        return "ready"
    with _lock:
        if _job is not None and _job["version"] == version and _job["result"] is None:
            return _job["phase"]
    return "notify"


def progress(offer):
    with _lock:
        if _job is not None and _job["version"] == offer.release.version:
            return _job["done"], _job["total"]
    return 0, 0


def unsupported_reason(offer):
    return automatic(offer) if phase(offer) == "notify" else ""


def wait_reason():
    return _wait


def ready_for(offer):
    return offer is not None and target(offer).release.version in _ready


def restart(offer, host):
    _restart_module().begin(offer, _ready.get(target(offer).release.version), host)


def set_restarting(version):
    global _restarting
    _restarting = version
    card_host.refresh("update")


def forget(version):
    """Drop a prepared update that can no longer be installed (its stage changed): it is prepared again."""
    _ready.pop(version, None)


def _collect():
    """Main thread: take a finished worker's result."""
    global _job
    with _lock:
        job = _job
        if job is None or job["result"] is None:
            return None
        _job = None
    version, result = job["version"], job["result"]
    if isinstance(result, _flow().Ready):
        _ready[version] = result
        _failed.pop(version, None)
        instance = preparer()
        if instance is not None and job["clean"]:
            instance.clean_older(version)   # Never a newer version's folder.
        return version
    attempts = _failed.get(version, (None, None, 0, 0))[3] + 1
    _failed[version] = (result.code, result.transient, time.time(), attempts)
    print("Angelo update: %s could not be prepared (%s)" % (version, result.code))
    # The person still hears of the update: at once when it can't be prepared, or after three passing failures
    # (a network that reaches the feed but not the downloads); the download keeps being tried meanwhile.
    return version if (not result.transient or attempts >= len(RETRY_AFTER)) else None


def _show_card(version):
    from . import update_ui
    offer = _offers.get(version)
    if offer is None or card_host.is_active("update"):
        card_host.refresh("update")
        return
    state = update_ui.checker().state()
    if state.skipped(version, offer.critical) or state.snoozed(version, time.time()):
        return
    update_ui.show_notice(offer)


def tick():
    """Called from ``update_ui.tick`` once a second while Angelo runs in a window."""
    global _agent_busy, _wait
    if not _results_handled:
        _handle_results_once()
        _load_install_failures()
    # What a restart would interrupt matters only while an update is being prepared (throttling) or is ready.
    reasons = work_guard.reasons() if (_job is not None or _ready) else []
    _agent_busy = "agent" in reasons
    wait = reasons[0] if reasons else ""
    if wait != _wait:
        _wait = wait
        card_host.refresh("update")
    finished = _collect()
    if finished is not None:
        _show_card(finished)
        from . import update_ui
        update_ui.redraw_status()
    elif _job is not None:
        card_host.refresh("update")
    for version, (code, transient, when, attempts) in list(_failed.items()):
        offer = _offers.get(version)
        offer = offer and target(offer)
        if (transient and offer is not None and offer.release.version == version and _job is None
                and time.time() >= when + RETRY_AFTER[min(attempts, 3) - 1] and background_allowed()):
            _start(offer)


def register():
    global _job, _agent_busy, _wait, _results_handled, _restarting, _support_reason, _install_failures, _accepted_line
    _job, _agent_busy, _wait, _results_handled, _restarting = None, False, "", False, None
    _support_reason, _install_failures, _accepted_line = None, ("", 0), ""
    _ready.clear()
    _failed.clear()
    _offers.clear()


def unregister():
    global _job
    with _lock:
        job, _job = _job, None
    if job is not None:
        job["cancel"].set()   # The partial download stays and resumes at the next launch.
