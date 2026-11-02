# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""fal's queue API: upload an input, submit, read status, fetch the result, cancel. No Blender dependency.

fal docs (read 2026-10-30): https://fal.ai/docs/api-reference/inference-api/queue/ . A submit
(``POST https://queue.fal.run/<endpoint id>``) answers ``request_id`` plus the three URLs of that request:
``status_url``, ``response_url`` and ``cancel_url``. They live under the model's *app*
(``/<owner>/<app>/requests/<id>``), not under the endpoint's full path, so a client that builds them from the
endpoint id gets HTTP 405. Angelo never builds them: it keeps the URLs fal returned, after checking that each one
is exactly ``https://queue.fal.run/<prefix>/requests/<request id>[/status|/cancel]`` where ``<prefix>`` is the app of
the endpoint it submitted to (the queue docs' answer) or that endpoint itself (the per-endpoint OpenAPI documents'
form; both name this one request). A URL that fails the check is refused, so a forged or redirected answer can never send
the key to another host or another account's request.

A team binding sends the same requests through the Angelo gateway (``/v1/fal``): the checked path is put under the
gateway route, so the org's key is applied by the gateway and never reaches the app.

``Handle`` is what a job saves (``job-state.json``) to resume after a restart: the request id, the endpoint and
the three checked URLs. None of them is a credential. Status reads and result reads are free; only the submit is
billed, and it is never retried (``fal_video``).

Every function takes ``transport`` (``studio_http.request_bytes``'s signature), so tests run against fixtures.
"""

import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from . import fal_models
from .studio_http import GatewayRejection, GenerationError, _headers, public_url, request_bytes
from .provider_errors import credential_values, error_detail
from .ui_text import n_

QUEUE_HOST = "queue.fal.run"
_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,80}")
_SUFFIXES = {"status_url": "/status", "response_url": "", "cancel_url": "/cancel"}
MAX_UPLOAD = 30 * 1024 * 1024   # The models' documented per-image limit (Seedance: "Max 30 MB").
MAX_VIDEO_UPLOAD = 50 * 1024 * 1024   # Seedance 2.0's limit for its reference videos together (the tightest listed).
UPLOAD_ROUTE = "/storage/upload/initiate?storage_type=fal-cdn-v3"
AGENT = "Angelo-MediaStudio/0.1"


@dataclass(frozen=True)
class Handle:
    request_id: str
    endpoint: str
    status_url: str
    response_url: str
    cancel_url: str

    def saved(self):
        return asdict(self)


def handle_from(saved):
    """A saved handle, re-checked exactly as when it was first received (a tampered job-state.json is refused)."""
    try:
        endpoint, identity = saved["endpoint"], saved["request_id"]
        urls = {name: saved[name] for name in _SUFFIXES}
    except (KeyError, TypeError):
        raise GenerationError(n_("This video's saved fal request is incomplete; check your fal dashboard")) from None
    return checked_handle(endpoint, dict(urls, request_id=identity))


def admitted(endpoint):
    """Whether Angelo submits to ``endpoint``: a video model's (``fal_models``) or a 3D model's (``fal_3d``)."""
    from . import fal_3d
    return endpoint in fal_models.ENDPOINTS or endpoint in fal_3d.ENDPOINTS


def app_of(endpoint):
    from . import fal_3d
    return fal_3d.APP if endpoint in fal_3d.ENDPOINTS else fal_models.app_of(endpoint)


def checked_handle(endpoint, response):
    """The ``Handle`` of a submit answer, or GenerationError when any returned URL isn't this request's own."""
    prefixes = (app_of(endpoint), endpoint)
    identity = response.get("request_id") if isinstance(response, dict) else None
    if not isinstance(identity, str) or not _REQUEST_ID.fullmatch(identity):
        raise GenerationError(n_("fal returned an invalid request ID"))
    urls = {}
    for name, suffix in _SUFFIXES.items():
        value = response.get(name)
        if not isinstance(value, str) or len(value) > 2048:
            raise GenerationError(n_("fal didn't return this request's status and result links"))
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname != QUEUE_HOST or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {f"/{prefix}/requests/{identity}{suffix}" for prefix in prefixes}):
            raise GenerationError(n_("fal returned a request link Angelo doesn't recognise; nothing was sent to it"))
        urls[name] = f"https://{QUEUE_HOST}{parsed.path}"
    return Handle(identity, endpoint, urls["status_url"], urls["response_url"], urls["cancel_url"])


def _route(binding, url):
    """Where a checked queue URL is sent: itself for a personal key, its path under the gateway route for a team."""
    if not binding.team:
        return url
    return binding.base_url + urlsplit(url).path


def _call(binding, url, body=None, method=None, accept=(200, 201), transport=request_bytes):
    """One JSON request with the binding's credential. A team token the gateway refused as UNAUTHENTICATED (before
    anything reached fal) is renewed once, as ``studio_http.binding_json``; nothing else is retried here."""
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")

    def once(headers):
        headers = dict(headers, Accept="application/json", **{"User-Agent": AGENT})
        if payload is not None:
            headers["Content-Type"] = "application/json"
        data = transport(url, body=payload, headers=headers, service=True, method=method, accept=accept)
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise GenerationError(n_("fal returned an invalid response")) from None
        if not isinstance(value, dict):
            raise GenerationError(n_("fal returned an invalid response"))
        return value, headers

    headers = _headers(binding)
    try:
        return once(headers)
    except GatewayRejection as refused:
        if refused.code != "UNAUTHENTICATED" or not binding.team:
            raise
    return once(_headers(binding, headers))


def submit(binding, endpoint, body, transport=request_bytes):
    """Queue ONE billable request. Never retried: a failed answer may still have queued it."""
    if not admitted(endpoint):
        raise GenerationError(n_("Choose a supported fal model"))
    value, _ = _call(binding, binding.url("/" + endpoint), body, transport=transport)
    return checked_handle(endpoint, value)


def status(binding, handle, transport=request_bytes):
    """``{"status": IN_QUEUE|IN_PROGRESS|COMPLETED, "queue_position"?, "error"?}``. fal reports a failed or cancelled
    request as COMPLETED with ``error``; that text is sanitized and bounded before anyone sees it."""
    value, headers = _call(binding, _route(binding, handle.status_url), accept=(200, 202), transport=transport)
    state = value.get("status")
    if state not in {"IN_QUEUE", "IN_PROGRESS", "COMPLETED"}:
        raise GenerationError(n_("fal returned an unknown request status"))
    output = {"status": state}
    position = value.get("queue_position")
    if type(position) is int and position >= 0:
        output["queue_position"] = position
    if value.get("error"):
        output["error"] = error_detail({"error": value["error"]}, credential_values(headers)) or n_("fal reported an error")
    return output


def result(binding, handle, transport=request_bytes):
    """The model's output. fal keeps it for an hour after completion (six minutes when it is 10 KB or more)."""
    value, _ = _call(binding, _route(binding, handle.response_url), transport=transport)
    return value


def cancel(binding, handle, transport=request_bytes):
    """Ask fal to cancel. True when fal took it (removed from the queue, or a signal sent to a running request);
    False otherwise (already finished, or fal couldn't be reached). Best effort: a running model may still finish and
    be billed, so the caller keeps following the request either way."""
    try:
        value, _ = _call(binding, _route(binding, handle.cancel_url), method="PUT", accept=(200, 202),
                         transport=transport)
    except GenerationError:
        return False
    return value.get("status") in {"OK", "CANCELLATION_REQUESTED"}


def upload(binding, data, mime, transport=request_bytes):
    """Put one input file on fal's CDN; returns its HTTPS URL for the model to read.

    fal's two-step upload: ``initiate`` (with the credential) answers a presigned ``upload_url`` and the file's
    ``file_url``; the bytes go to ``upload_url`` with NO credential. fal serves the file to anyone holding its
    unguessable URL (its default; generative_studio.rst says so where frames are chosen).
    """
    if not data or len(data) > (MAX_VIDEO_UPLOAD if mime == "video/mp4" else MAX_UPLOAD):
        raise GenerationError(n_("Each frame or reference must be at most 30 MB, a reference video 50 MB"))
    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4"}.get(mime)
    if extension is None:
        raise GenerationError(n_("Frames and references must be PNG, JPEG or WebP images"))
    value, _ = _call(binding, binding.storage_url(UPLOAD_ROUTE), {"file_name": "angelo-input" + extension,
                                                                  "content_type": mime}, transport=transport)
    upload_url, file_url = value.get("upload_url"), value.get("file_url")
    if not isinstance(upload_url, str) or not isinstance(file_url, str):
        raise GenerationError(n_("fal didn't return an upload link"))
    public_url(upload_url)
    public_url(file_url)
    transport(upload_url, body=data, headers={"Content-Type": mime, "User-Agent": AGENT}, method="PUT",
              accept=(200, 201, 204))
    return file_url


def output_video(value):
    """The generated video's URL from a model output (``{"video": {"url": ...}}``, every model in ``fal_models``)."""
    return output_file(value, "video")


def output_file(value, field):
    """The public HTTPS URL of an output file ``{field: {"url": ...}}`` (a video's ``video``, TRELLIS's ``model_glb``)."""
    found = value.get(field) if isinstance(value, dict) else None
    url = found.get("url") if isinstance(found, dict) else None
    if not isinstance(url, str):
        raise GenerationError(n_("fal finished but returned no file; check your fal dashboard before trying again"))
    public_url(url)
    return url

