# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Saved fal videos after a pause or a restart: resume them, or say why not. Main thread; no bpy.

``tick`` runs from ``studio.pump`` (through ``studio_video.tick``) over the library's VIDEO assets that no worker
follows:

* **Resume.** One whose saved state is still at fal (``fal_video.RESUMABLE``, with its ``fal`` handle) is followed
  again once its connection can be bound (AI Providers' key check may still be running at start-up), first after
  ``RESUME_SECONDS`` and then less often (doubling, at most ``RESUME_MAX``), so a pause for an unreachable fal or an
  unread result is retried without hammering it. Only free status and result reads: never a submit. While its fal
  connection is missing the asset says so (``RECONNECT``) and keeps its saved request; a saved request that can't be
  read back fails with the reason.
* **Lost submits.** One left ``submitting`` (Angelo closed while the one billable request was in flight) can't be
  followed: it fails and points at the fal dashboard; one left ``uploading`` had sent nothing billable: stopped.
* **Thumbnails.** A ready one without a picture gets one, once (the caller's ``make_thumbnail``).

One record that can't be handled is skipped and logged; it never stops the pump.
"""

import time

from .fal_video import RESUMABLE
from .ui_text import n_

RESUME_SECONDS = 10.0
RESUME_MAX = 600.0
SUBMIT_LOST = n_("Angelo closed while sending this video to fal, so it may have been queued and billed. "
                 "Check your fal dashboard; nothing was sent again")
UPLOAD_LOST = n_("Angelo closed before this video was sent to fal; nothing was billed")
RECONNECT = n_("Reconnect fal in AI Providers to finish this video; its request is saved")
STOPPED_FOLLOWING = n_("Stopped following this video. fal may still finish and bill it; its request ID is saved")
# The same notices for Seedance on a BytePlus key (byteplus_video): they name where a billed video can be found.
BYTEPLUS = {SUBMIT_LOST: n_("Angelo closed while sending this video to BytePlus, so it may have been queued and billed. "
                            "Check your BytePlus console; nothing was sent again"),
            UPLOAD_LOST: n_("Angelo closed before this video was sent to BytePlus; nothing was billed"),
            RECONNECT: n_("Reconnect BytePlus in AI Providers to finish this video; its task is saved"),
            STOPPED_FOLLOWING: n_("Stopped following this video. BytePlus may still finish and bill it; its task ID is saved")}


def _notice(asset, message):
    """``message`` naming the asset's own provider."""
    from .studio_state import job_provider
    return BYTEPLUS[message] if job_provider(asset) == "BytePlus" else message
_tried_thumbnails = set()
_next_resume = {}   # Asset id -> (monotonic time of its next resume, attempts so far)


def tick(library, jobs, bind, make_thumbnail):
    """Resume and thumbnail work for the library; True when an asset changed (the caller redraws)."""
    changed = False
    for asset in list(library.assets.values()):   # fal's jobs: its videos and its 3D models (TRELLIS).
        if not (asset.get("kind") == "VIDEO" or asset.get("mode") == "fal_api") or asset["id"] in jobs.active:
            continue
        try:
            changed = _tick_one(library, jobs, bind, asset, make_thumbnail) or changed
        except Exception as error:   # noqa: BLE001 - one bad record never stops the pump that serves every other.
            print("Angelo: Media Studio video", asset.get("id"), "skipped:", type(error).__name__)
    return changed


def _tick_one(library, jobs, bind, asset, make_thumbnail):
    from .fal_queue import handle_from
    from .studio_http import GenerationError
    status, identity = asset.get("status"), asset["id"]
    if status == "submitting" or (status in RESUMABLE and not (asset.get("fal") or asset.get("byteplus_task"))):
        library.update(dict(asset, status="failed", error=_notice(asset, SUBMIT_LOST)))
        return True
    if status == "uploading":
        library.update(dict(asset, status="stopped", error=_notice(asset, UPLOAD_LOST)))
        return True
    if status == "ready" and asset.get("kind") == "VIDEO" and not asset.get("thumbnail") and identity not in _tried_thumbnails:
        _tried_thumbnails.add(identity)
        return make_thumbnail(library, asset)
    if status not in RESUMABLE:
        _next_resume.pop(identity, None)
        return False
    now = time.monotonic()
    due, attempts = _next_resume.get(identity, (0.0, 0))
    if now < due or len(jobs.active) >= 4:
        return False
    _next_resume[identity] = (now + min(RESUME_MAX, RESUME_SECONDS * 2 ** attempts), attempts + 1)
    try:
        if asset.get("byteplus_task"):
            from .byteplus_video import task_id
            task_id(asset["byteplus_task"])
        else:
            handle_from(asset.get("fal"))
    except GenerationError as error:
        library.update(dict(asset, status="failed", error=str(error)))
        return True
    try:
        binding = bind(asset.get("connection", ""))
        if binding.service != ("seedream_api" if asset.get("byteplus_task") else "fal_api"):
            raise ValueError("not the connection the request was made on")
    except (ValueError, RuntimeError):
        if asset.get("error") != _notice(asset, RECONNECT):
            library.update(dict(asset, error=_notice(asset, RECONNECT)))
            return True
        return False
    if asset.get("error"):
        asset = dict(asset, error="")
        library.update(asset)
    jobs.resume(asset, binding)
    return True


def stop_following(library, asset):
    """Stop on a video no worker follows (paused, or waiting for its connection): it stops here; fal isn't told."""
    if (asset.get("kind") == "VIDEO" or asset.get("mode") == "fal_api") and asset.get("status") in RESUMABLE:
        library.update(dict(asset, status="stopped", error=_notice(asset, STOPPED_FOLLOWING)))
        _next_resume.pop(asset["id"], None)
