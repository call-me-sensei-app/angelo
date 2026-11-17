# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_download`` on the host (``download_spec``; doc/angelo/downloads.rst, "The agent"). Main thread.

``BlenderExecutor`` routes the tool here after the person approved its card (``download_approvals``), in Ask and in
Auto alike. ``execute`` starts the transfer (``download_fetch.download``) on a worker thread and returns a
``DownloadPending`` the controller polls; Stop ends the transfer and leaves no file. The folder is the
saved file's folder, or the unsaved document's own folder (``download_folders``), never the chat's own folder, which
agent scripts cannot read.

Results and refusals are model-facing English: ``path`` (relative to the project's folder, or the full path for an
unsaved document), ``file`` (the full path), ``folder`` ("project" or "unsaved"), ``bytes``, ``sha256``, ``format``,
``host``, and ``scene_changed: false`` (a download changes no scene); a refusal has ``error_code``
(``refusal_codes``) and ``reason``. Both entry points are grant entries (``grant_guard.entry``): agent code cannot call them.
"""

import threading
from pathlib import Path

import bpy

from . import download_folders, grant_guard, thread_start
from .download_spec import TIME_LIMIT_S, validate_download


def destination(controller):
    """(root, "project" or "unsaved"): where ``assets/downloads`` is made (``download_folders``). Main thread."""
    return download_folders.root(bpy), download_folders.where(bpy)


def precondition(controller, window, name, arguments):
    """Before the card: the arguments as checked, the folder the file will land in, and whether the proxy may find the
    address instead of Angelo (``download_fetch.proxy_lookup_possible``). Never touches the network."""
    from . import download_fetch
    from urllib.parse import urlsplit
    args = validate_download(name, arguments)
    return {"url": args["url"], "kind": args["kind"], "folder": download_folders.where(bpy),
            "proxy_lookup": download_fetch.proxy_lookup_possible(urlsplit(args["url"]).hostname)}


def _pending(poll, cancel):
    from .studio_agent import StudioPending

    class DownloadPending(StudioPending):
        status_text = "Downloading"
        activity_note = "Downloading one file into assets/downloads. Stop ends the transfer and keeps nothing."
        timeout = TIME_LIMIT_S + 60.0
    return DownloadPending(poll, cancel)


def execute(controller, window, name, arguments):
    """Start the download on a worker thread; the result arrives through the returned pending call. The transfer's
    modules (``http.client``, ``urllib.request``) load on the first download, never at startup."""
    from . import download_fetch
    args = validate_download(name, arguments)
    try:
        root, where = destination(controller)
    except OSError as error:
        return {"error": str(error)[:400], "error_code": "UNSUPPORTED", "reason": "folder_unavailable",
                "scene_changed": False}
    stop, record = threading.Event(), {}
    done = threading.Event()

    def work():
        try:
            record["value"] = download_fetch.download(args["url"], args["kind"], root, args.get("name"),
                                                      cancel=stop)
        except download_fetch.Refused as refused:
            record["refused"] = refused
        except Exception as error:   # noqa: BLE001 - reported as a result; the worker touches no Blender data.
            record["error"] = error
        finally:
            done.set()

    def poll():
        if not done.is_set():
            return None
        if "value" in record:
            found = record["value"]
            full = str(Path(root) / Path(*found["path"].split("/")))
            return dict(found, path=found["path"] if where == "project" else full, file=full, folder=where,
                        scene_changed=False)
        refused = record.get("refused")
        if refused is not None:
            return {"error": str(refused)[:400], "error_code": refused.code, "reason": refused.reason,
                    "scene_changed": False}
        error = record.get("error")
        return {"error": "The download failed: %s" % type(error).__name__, "error_code": "INTERNAL_ERROR",
                "reason": "failed", "scene_changed": False}

    thread_start.daemon("AngeloDownload", work)
    return _pending(poll, stop.set)


precondition = grant_guard.entry(precondition)
execute = grant_guard.entry(execute)
