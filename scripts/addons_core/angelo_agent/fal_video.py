# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One fal video generation, from inputs to a saved .mp4, run on a ``StudioJobs`` worker thread. No bpy.

Order: validate everything (``fal_models.request`` with placeholder frames), upload the frames or references to
fal's CDN, submit ONCE, save the request's ``Handle`` (``emit({"fal": ...})`` lands in ``job-state.json`` before
anything else can happen), then follow the status fal returned until COMPLETED, read the result and download the
video next to the asset. The submit is never retried: a failed answer may still have queued a billed request.

Stopping has two meanings, told apart by the stop signal (``studio_generation.StopSignal``):

* the person's Stop (``remote``): Angelo asks fal to cancel (``cancel_url``) once and keeps following, because a
  video fal still finishes is billed: it is downloaded, not thrown away; a cancelled one ends "stopped";
* Angelo closing or the add-on unloading (``closing``): monitoring pauses and NOTHING is sent to fal; the saved
  handle lets a later session resume (``follow``), because status and result reads are free.

Failed status reads (network, fal busy) are retried with backoff, never resubmitted; after ``STATUS_FAILURES`` in a
row, or a result or download that can't be read yet, monitoring pauses and ``studio_video.tick`` resumes it later.
A 401, 403 or 404 ends the job (the key, its access or the request is gone). fal keeps a finished request's result
for an hour (six minutes when 10 KB or more), so a resume later than that says so and points at the fal dashboard.
A pause's message is for logs only; nothing shows it.
"""

import os
import time
import uuid

from . import fal_models, fal_queue
from .studio_http import GatewayRejection, GenerationCancelled, GenerationError, request_bytes
from .ui_text import n_

MAX_VIDEO = 512 * 1024 * 1024
VIDEO_FILE = "video.mp4"
POLL_SECONDS = 5
TIMEOUT_SECONDS = 2 * 60 * 60   # Long queues happen; past this the request id stays saved and the next session resumes.
STATUS_FAILURES = 8              # Consecutive failed status reads before monitoring pauses (free reads; no resubmit).
# Statuses a saved video job can be in while fal may still be working on it (``video_resume``).
RESUMABLE = frozenset({"queued", "generating", "downloading", "cancelling", "finishing"})


class MonitoringPaused(GenerationCancelled):
    """Monitoring stopped without asking fal to cancel; the saved handle resumes it in a later session."""


def _inputs_for_validation(inputs):
    placeholder = "https://fal.media/angelo-placeholder.png"
    return {"first": placeholder if inputs.get("first") else "", "last": placeholder if inputs.get("last") else "",
            "references": [placeholder] * len(inputs.get("references") or ()),
            "videos": [placeholder] * len(inputs.get("videos") or ())}


def mp4_seconds(data):
    """An MP4's length from its ``mvhd`` box (version 0 or 1), or None. Used for a reference video's price."""
    at = data.find(b"mvhd")
    if at < 4 or len(data) < at + 32:
        return None
    version = data[at + 4]
    if version == 1 and len(data) >= at + 44:
        scale, length = int.from_bytes(data[at + 24:at + 28], "big"), int.from_bytes(data[at + 28:at + 36], "big")
    else:
        scale, length = int.from_bytes(data[at + 16:at + 20], "big"), int.from_bytes(data[at + 20:at + 24], "big")
    return round(length / scale, 3) if scale else None


def load_video(reference):
    """A reference video's bytes: a regular local MP4 (``{"path"}``) of at most 50 MB."""
    from pathlib import Path
    from .fal_queue import MAX_VIDEO_UPLOAD
    path = Path(reference.get("path") or "")
    if not path.is_file() or path.is_symlink():
        raise GenerationError(n_("A reference video is missing; choose it again"))
    with path.open("rb") as handle:
        data = handle.read(MAX_VIDEO_UPLOAD + 1)
    if len(data) > MAX_VIDEO_UPLOAD or len(data) < 12 or data[4:8] != b"ftyp":
        raise GenerationError(n_("A reference video must be an MP4 of at most 50 MB"))
    return data, "video/mp4"


def validate(model, prompt, options, inputs):
    """Every refusal ``generate`` could make before uploading or billing anything; returns (endpoint, body)."""
    return fal_models.request(model, prompt, options, **_inputs_for_validation(inputs))


def validate_any(model, prompt, options, inputs):
    """``validate`` for any video model: fal's, or BytePlus's (``byteplus_video.validate``)."""
    from . import byteplus_models, byteplus_video
    if model in byteplus_models.MODELS:
        return byteplus_video.validate(model, prompt, options, inputs)
    return validate(model, prompt, options, inputs)


def _load(reference, transport):
    from .studio_generation import load_references   # Same checks as image references: size, type, dimensions.
    return load_references([reference], transport)[0]


def generate(binding, model, prompt, options, inputs, directory, emit, cancel, transport=request_bytes,
             poll_interval=None, timeout=TIMEOUT_SECONDS):
    """Run ONE confirmed generation. ``inputs``: ``{"first", "last"}`` references (``{"path"}`` or ``{"url"}``),
    ``"references"``, a list of them, and ``"videos"``, local MP4 references (``{"path"}``). Returns the asset's final
    fields."""
    validate(model, prompt, options, inputs)
    if model not in binding.model_ids:
        raise GenerationError(n_("This model is not available on the selected generation connection"))
    if cancel.is_set():
        raise GenerationCancelled(n_("Stopped before anything was sent to fal"))
    if inputs.get("first") or inputs.get("references") or inputs.get("videos"):
        emit({"status": "uploading", "progress": 2})
    urls = {}
    for name in ("first", "last"):
        if inputs.get(name):
            urls[name] = fal_queue.upload(binding, *_load(inputs[name], transport), transport=transport)
    urls["references"] = [fal_queue.upload(binding, *_load(r, transport), transport=transport)
                          for r in inputs.get("references") or ()]
    urls["videos"] = [fal_queue.upload(binding, *load_video(v), transport=transport) for v in inputs.get("videos") or ()]
    endpoint, body = fal_models.request(model, prompt, options, urls.get("first", ""), urls.get("last", ""),
                                        urls["references"], urls["videos"])
    if cancel.is_set():
        raise GenerationCancelled(n_("Stopped before anything was sent to fal"))
    # Marked first: an app that closes while the submit is in flight finds this, not a silent loss (video_resume).
    emit({"status": "submitting"})
    handle = fal_queue.submit(binding, endpoint, body, transport)
    emit({"fal": handle.saved(), "task_id": handle.request_id, "status": "queued", "progress": 5})
    return follow(binding, handle, directory, emit, cancel, transport, poll_interval, timeout)


TERMINAL_READS = frozenset({401, 403, 404})   # The key, its access or the request itself is gone: retrying can't help.


def changes_only(emit):
    """``emit`` that drops an event equal to the one before it: a poll that changed nothing rewrites no saved state."""
    last = []

    def once(data):
        if last and last[0] == data:
            return
        last[:] = [dict(data)]
        emit(data)
    return once


def follow(binding, handle, directory, emit, cancel, transport=request_bytes, poll_interval=None,
           timeout=TIMEOUT_SECONDS, save=None):
    """Follow a submitted request to its saved video (also the resume path).

    The person's Stop (``cancel.remote``) sends ONE cancel and keeps following: a request fal still finishes is
    billed, so its video is downloaded rather than thrown away. Angelo closing (``cancel.closing``) pauses at once.
    A pause (``MonitoringPaused``) leaves the saved state as it is, for ``video_resume.tick`` to resume later.
    ``save(output, directory, transport)`` keeps the result (``save_video`` by default, ``save_model`` for 3D).
    The first read is at once (a resumed request may have finished while Angelo was closed); an unchanged state isn't
    emitted again.
    """
    poll_interval = POLL_SECONDS if poll_interval is None else poll_interval
    deadline, failures, asked, first = time.monotonic() + timeout, 0, False, True
    emit = changes_only(emit)
    while True:
        wait = 0 if first else poll_interval if failures == 0 else min(60, poll_interval * 2 ** failures)
        first = False
        if cancel.wait(wait):
            if cancel.closing or not cancel.remote:
                raise MonitoringPaused("Monitoring paused; the saved request resumes later")
            if not asked:
                asked = True
                fal_queue.cancel(binding, handle, transport)
                emit({"status": "cancelling"})
            cancel.clear()   # Keep following: fal may still finish (and bill) the video.
        if time.monotonic() > deadline:
            raise MonitoringPaused("fal is still working on this video; monitoring paused")
        try:
            state = fal_queue.status(binding, handle, transport)
        except GatewayRejection as refused:
            if refused.code not in {"RATE_LIMITED", "UNAVAILABLE"}:
                raise
            if cancel.wait(min(30 if refused.retry_after is None else refused.retry_after, 120)):
                cancel.set()   # Handled at the top of the loop.
            continue
        except GenerationError as error:
            if getattr(error, "status", None) in TERMINAL_READS:
                raise
            failures += 1
            if failures >= STATUS_FAILURES:
                raise MonitoringPaused("fal can't be reached; monitoring paused") from None
            continue
        failures = 0
        if state["status"] == "IN_QUEUE":
            emit({"status": "cancelling" if asked else "queued", "progress": 5,
                  "queue_position": state.get("queue_position", 0)})
        elif state["status"] == "IN_PROGRESS":
            emit({"status": "cancelling" if asked else "generating", "progress": 50})
        else:
            if state.get("error"):
                if asked:
                    raise GenerationCancelled(n_("Cancelled at fal"))
                raise GenerationError(n_("fal couldn't make this video: {detail}"), detail=state["error"])
            break

    emit({"status": "downloading", "progress": 95})
    try:
        output = fal_queue.result(binding, handle, transport)
    except GenerationError as error:
        status = getattr(error, "status", None)
        if status == 404:
            raise GenerationError(n_("fal no longer holds this result (it keeps results for an hour). Request ID {request}: "
                                     "find the video in your fal dashboard"), request=handle.request_id) from None
        if status == 499 and asked:
            raise GenerationCancelled(n_("Cancelled at fal")) from None
        if status is not None and 400 <= status < 500:
            raise   # The model's own refusal (its input, its policy): final.
        raise MonitoringPaused("The result couldn't be read yet; monitoring paused") from None
    return (save or save_video)(output, directory, transport)


def _write(directory, name, data):
    """``data`` as ``directory/name``, atomically (a resumed job may finish a download an earlier session began)."""
    temporary = directory / f".{name}.{uuid.uuid4().hex}.part"
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle_file:
            handle_file.write(data)
        os.replace(temporary, directory / name)
    finally:
        temporary.unlink(missing_ok=True)


def _download(url, limit, timeout, transport):
    try:
        return transport(url, limit=limit, timeout=timeout)
    except GenerationError:
        raise MonitoringPaused("The result couldn't be downloaded yet; monitoring paused") from None


def save_video(output, directory, transport=request_bytes):
    """A video model's output (``{"video": {"url"}}``) as ``video.mp4``."""
    data = _download(fal_queue.output_video(output), MAX_VIDEO, 600, transport)
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise GenerationError(n_("fal returned a file that isn't an MP4 video"))
    _write(directory, VIDEO_FILE, data)
    return {"status": "ready", "progress": 100, "files": [VIDEO_FILE], "thumbnail": ""}


def save_model(output, directory, transport=request_bytes):
    """A 3D model's output (``{"model_glb": {"url"}}``) as ``model.glb``, checked as Tripo's and Meshy's are."""
    from .studio_generation import MAX_MODEL, checked_glb
    data = checked_glb(_download(fal_queue.output_file(output, "model_glb"), MAX_MODEL, 180, transport))
    _write(directory, "model.glb", data)
    return {"status": "ready", "progress": 100, "files": ["model.glb"], "thumbnail": ""}


def saver(kind):
    """How a fal job of an asset ``kind`` keeps its result (the resume path picks it from the asset)."""
    return save_model if kind == "3D" else save_video


def generate_3d(binding, model, options, references, directory, emit, cancel, transport=request_bytes,
                poll_interval=None, timeout=TIMEOUT_SECONDS):
    """Run ONE confirmed fal 3D generation (``fal_3d``): upload the images, submit once, follow, save the GLB."""
    from . import fal_3d
    fal_3d.validate(model, options, references)
    if model not in binding.model_ids:
        raise GenerationError(n_("This model is not available on the selected generation connection"))
    if cancel.is_set():
        raise GenerationCancelled(n_("Stopped before anything was sent to fal"))
    emit({"status": "uploading", "progress": 2})
    urls = [fal_queue.upload(binding, *_load(r, transport), transport=transport) for r in references]
    endpoint, body = fal_3d.request(model, options, urls)
    if cancel.is_set():
        raise GenerationCancelled(n_("Stopped before anything was sent to fal"))
    emit({"status": "submitting"})
    handle = fal_queue.submit(binding, endpoint, body, transport)
    emit({"fal": handle.saved(), "task_id": handle.request_id, "status": "queued", "progress": 5})
    return follow(binding, handle, directory, emit, cancel, transport, poll_interval, timeout, save=save_model)
