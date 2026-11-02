# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One Seedance video on a BytePlus ModelArk key, from inputs to a saved .mp4, on a ``StudioJobs`` worker. No bpy.

BytePlus's task API (read 2026-10-30): ``POST {base}/contents/generations/tasks`` answers ``{"id"}``; ``GET
{base}/contents/generations/tasks/{id}`` answers ``status`` (``queued``, ``running``, ``succeeded``, ``failed``,
``expired``, ``cancelled``), ``error`` and, once it succeeded, ``content.video_url`` (valid 24 hours). ``DELETE`` on
the same path cancels a task that is queued at that moment but deletes the record of one that has finished
("Cancel or delete a video generation task", read 2026-10-31), and there is no conditional form: a task read as
queued can finish before the DELETE lands (a slept laptop, a slow network), and the paid video would be erased.
Angelo never sends it. ``{base}`` is the
connection's own (``https://ark.ap-southeast.bytepluses.com/api/v3``) or, for a team connection, the gateway's
BytePlus route: both are ``binding.url``, so the key never goes anywhere else.

The rules are ``fal_video``'s: everything is checked first (``byteplus_models.request``), the one billable create is
never retried, the task id is saved (``job-state.json``, ``byteplus_task``) before following starts. The person's
Stop after the create can't stop BytePlus, so the job turns ``finishing`` and the video it bills is still saved;
Angelo closing
pauses and ``video_resume`` resumes later with reads only, a 401, 403 or 404 ends the job, other failed reads are
retried and then pause. The video is downloaded without the key and checked as an MP4; its link lasts 24 hours, so a
refused download is final and names the task for the BytePlus console. The create's body carries the images inline,
so it is checked against the route's request limit (BytePlus's 64 MB; a team gateway's 32 MiB) before it is sent.
"""

import base64
import json
import re
import time

from . import byteplus_models, fal_video
from .fal_video import MAX_VIDEO, STATUS_FAILURES, TERMINAL_READS, TIMEOUT_SECONDS, VIDEO_FILE, \
    MonitoringPaused, _load, _write
from .studio_http import GatewayRejection, GenerationCancelled, GenerationError, _headers, public_url, request_bytes
from .provider_errors import credential_values, error_detail
from .ui_text import n_

ROUTE = "/contents/generations/tasks"
MAX_BODY = 60 * 1024 * 1024        # BytePlus's 64 MB request limit, with room to spare.
MAX_TEAM_BODY = 30 * 1024 * 1024   # The team gateway's (and its Cloud Run front's) 32 MiB.
EXPIRED_LINK = {401, 403, 404, 410}
_TASK = re.compile(r"[A-Za-z0-9_-]{1,160}")
AGENT = "Angelo-MediaStudio/0.1"


def _call(binding, url, body=None, method=None, accept=(200, 201), transport=request_bytes):
    """One request with the binding's credential; the answer's JSON (``{}`` for an empty one). A team token the
    gateway refused as UNAUTHENTICATED is renewed once (``studio_http.binding_json``); nothing else is retried."""
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")

    def once(headers):
        headers = dict(headers, Accept="application/json", **{"User-Agent": AGENT})
        if payload is not None:
            headers["Content-Type"] = "application/json"
        data = transport(url, body=payload, headers=headers, service=True, method=method, accept=accept)
        if not data:
            return {}, headers
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise GenerationError(n_("BytePlus returned an invalid response")) from None
        if not isinstance(value, dict):
            raise GenerationError(n_("BytePlus returned an invalid response"))
        return value, headers

    headers = _headers(binding)
    try:
        return once(headers)
    except GatewayRejection as refused:
        if refused.code != "UNAUTHENTICATED" or not binding.team:
            raise
    return once(_headers(binding, headers))


def task_id(value):
    if not isinstance(value, str) or not _TASK.fullmatch(value):
        raise GenerationError(n_("BytePlus returned an invalid task ID"))
    return value


def _data_uri(reference, transport):
    data, mime = _load(reference, transport)
    return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")


def validate(model, prompt, options, inputs):
    placeholder = "https://bytepluses.com/angelo-placeholder.png"
    return byteplus_models.request(model, prompt, options, placeholder if inputs.get("first") else "",
                                   placeholder if inputs.get("last") else "",
                                   [placeholder] * len(inputs.get("references") or ()), inputs.get("videos") or ())


def generate(binding, model, prompt, options, inputs, directory, emit, cancel, transport=request_bytes,
             poll_interval=None, timeout=TIMEOUT_SECONDS):
    """Run ONE confirmed generation; ``inputs`` as ``fal_video.generate``'s. Returns the asset's final fields."""
    validate(model, prompt, options, inputs)
    if model not in binding.model_ids:
        raise GenerationError(n_("This model is not available on the selected generation connection"))
    if cancel.is_set():
        raise GenerationCancelled(n_("Stopped before anything was sent to BytePlus"))
    first = _data_uri(inputs["first"], transport) if inputs.get("first") else ""
    last = _data_uri(inputs["last"], transport) if inputs.get("last") else ""
    references = [_data_uri(r, transport) for r in inputs.get("references") or ()]
    _endpoint, body = byteplus_models.request(model, prompt, options, first, last, references)
    size, limit = len(json.dumps(body, ensure_ascii=False).encode("utf-8")), MAX_TEAM_BODY if binding.team else MAX_BODY
    if size > limit:
        raise GenerationError(n_("These images are too large to send to BytePlus together ({size} MB; at most {limit} MB); "
                                 "use fewer or smaller images"), size=f"{size / 2 ** 20:.0f}", limit=f"{limit / 2 ** 20:.0f}")
    if cancel.is_set():
        raise GenerationCancelled(n_("Stopped before anything was sent to BytePlus"))
    emit({"status": "submitting"})   # Marked first: a close during the create is reported, never silently lost.
    value, _ = _call(binding, binding.url(ROUTE), body, transport=transport)
    try:
        identity = task_id(value.get("id"))
    except GenerationError:
        raise GenerationError(n_("BytePlus accepted the request but returned no task ID; check your BytePlus console "
                                 "before trying again")) from None
    emit({"byteplus_task": identity, "task_id": identity, "status": "queued", "progress": 5})
    return follow(binding, identity, directory, emit, cancel, transport, poll_interval, timeout)


def follow(binding, identity, directory, emit, cancel, transport=request_bytes, poll_interval=None,
           timeout=TIMEOUT_SECONDS, finishing=False):
    """Follow a created task to its saved video (also the resume path; ``finishing``: the person already pressed Stop).

    The first read is at once (a resumed task may have finished while Angelo was closed), then every poll interval;
    an unchanged state isn't emitted again, so the job's saved state isn't rewritten for nothing."""
    poll_interval = fal_video.POLL_SECONDS if poll_interval is None else poll_interval
    deadline, url = time.monotonic() + timeout, binding.url(ROUTE + "/" + task_id(identity))
    failures, first = 0, True
    emit = fal_video.changes_only(emit)
    while True:
        wait = 0 if first else poll_interval if failures == 0 else min(60, poll_interval * 2 ** failures)
        first = False
        if cancel.wait(wait):
            if cancel.closing or not cancel.remote:
                raise MonitoringPaused("Monitoring paused; the saved task resumes later")
            finishing = True   # BytePlus can't stop a created task safely: it finishes, is billed, and is saved.
            cancel.clear()
        if time.monotonic() > deadline:
            raise MonitoringPaused("BytePlus is still working on this video; monitoring paused")
        try:
            value, headers = _call(binding, url, transport=transport)
        except GatewayRejection as refused:
            if refused.code not in {"RATE_LIMITED", "UNAVAILABLE"}:
                raise
            if cancel.wait(min(30 if refused.retry_after is None else refused.retry_after, 120)):
                cancel.set()
            continue
        except GenerationError as error:
            if getattr(error, "status", None) in TERMINAL_READS:
                raise
            failures += 1
            if failures >= STATUS_FAILURES:
                raise MonitoringPaused("BytePlus can't be reached; monitoring paused") from None
            continue
        failures = 0
        status = value.get("status")
        if status in {"queued", "running"}:
            emit({"status": "finishing" if finishing else "queued" if status == "queued" else "generating",
                  "progress": 5 if status == "queued" else 50})
            continue
        if status == "succeeded":
            break
        if status == "cancelled":
            raise GenerationCancelled(n_("Cancelled at BytePlus"))
        detail = error_detail({"error": value.get("error")}, credential_values(headers)) if value.get("error") else ""
        if status in {"failed", "expired"}:
            raise GenerationError(n_("BytePlus couldn't make this video: {detail}"), detail=detail or status)
        raise GenerationError(n_("BytePlus returned an unknown task status"))
    emit({"status": "downloading", "progress": 95})
    content = value.get("content") if isinstance(value.get("content"), dict) else {}
    video = content.get("video_url")
    if not isinstance(video, str):
        raise GenerationError(n_("BytePlus finished but returned no video; check your BytePlus console before trying again"))
    try:
        public_url(video)
        data = transport(video, limit=MAX_VIDEO, timeout=600)
    except GenerationError as error:   # A refused link (or one that isn't a public HTTPS URL) is final; the rest pause.
        unusable = getattr(error, "status", None) in EXPIRED_LINK or not video.startswith("https://")
        if unusable:
            raise GenerationError(n_("BytePlus's video link can't be used (links last 24 hours); find task {task} in "
                                     "your BytePlus console"), task=identity) from None
        raise MonitoringPaused("The video couldn't be downloaded yet; monitoring paused") from None
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise GenerationError(n_("BytePlus returned a file that isn't an MP4 video"))
    _write(directory, VIDEO_FILE, data)
    return {"status": "ready", "progress": 100, "files": [VIDEO_FILE], "thumbnail": ""}
