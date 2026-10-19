# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""LIVE write check of the team library tools inside Angelo, with an existing team sign-in (not billed).

Run by Angelo itself (``--python``), with ``ANGELO_CONNECTIONS_DIR`` set to a
connections folder whose team sign-in is already in the OS store, so the add-on
restores it with no click. Everything goes through the real tools (Controller,
BlenderExecutor, approvals clicked by this script) against the console's
library API only; no provider is called:

1. save a small cube as a GLB (``angelo_team_library_save``, team space), with
   the upload headers the console returned (the checksum header included);
2. search for it, read it with its thumbnail, import it into this scratch scene;
3. delete it from the library (the uploader's ``DELETE``) and confirm it is gone.

Grant-key renewal is switched off in this process, so the only OS-store item it
may write is the sign-in's own (a refresh rotates it). The report
(``ANGELO_LIVE_REPORT``) holds statuses, timings, route patterns, request ids
and header names; never titles, links, ids of people or tokens.

    ANGELO_CONNECTIONS_DIR=<profile> ANGELO_LIVE_REPORT=<scratch>/write.json <Angelo> --factory-startup \\
        --disable-autoexec --no-window-focus --python tests/python/angelo_agent/live_team_library_write_check.py

Dry run: ``ANGELO_LIVE_FAKE_CONSOLE=1`` (with ``ANGELO_DISABLE_KEYCHAIN=1``) starts the in-process fake
console (fake_team_console.py), points the add-on at it and signs in through its allow screen, then runs
the same steps. Nothing reaches a real service or the OS store.
"""

import json
import os
import re
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

REPORT = Path(os.environ["ANGELO_LIVE_REPORT"])
TITLE = "Angelo live check"
report = {"steps": [], "requests": [], "transfers": [], "success": False}
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def step(name, started, ok, **facts):
    report["steps"].append(dict({"step": name, "ok": bool(ok), "ms": round((time.perf_counter() - started) * 1000, 1)},
                                **facts))
    if not ok:
        raise RuntimeError("step failed: " + name)


def record_requests(transport):
    """The client's transport, recording method, route pattern, status, time and request ids (never URLs)."""
    def recorded(method, url, headers=None, body=None, timeout=20):
        started = time.perf_counter()
        status, answer, data = transport(method, url, headers, body, timeout)
        path = _ID.sub(":id", url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0])
        report["requests"].append({"method": method, "route": "/" + path, "status": status,
                                   "ms": round((time.perf_counter() - started) * 1000, 1),
                                   "request_id": {k: v for k, v in answer.items()
                                                  if k in {"x-request-id", "x-cloud-trace-context", "cf-ray"}}})
        return status, answer, data
    return recorded


def record_transfers(module):
    """Storage transfers: kind, header names, size and time (never the link)."""
    upload, fetch, download = module.upload, module.fetch, module.download

    def recorded_upload(url, headers, source, size, **kwargs):
        started = time.perf_counter()
        try:
            return upload(url, headers, source, size, **kwargs)
        finally:
            report["transfers"].append({"kind": "upload", "headers": sorted(k.lower() for k in headers), "bytes": size,
                                        "ms": round((time.perf_counter() - started) * 1000, 1)})

    def recorded_fetch(url, **kwargs):
        started = time.perf_counter()
        data = fetch(url, **kwargs)
        report["transfers"].append({"kind": "thumbnail", "bytes": len(data),
                                    "ms": round((time.perf_counter() - started) * 1000, 1)})
        return data

    def recorded_download(url, destination, **kwargs):
        started = time.perf_counter()
        size = download(url, destination, **kwargs)
        report["transfers"].append({"kind": "download", "bytes": size, "sha256_checked": bool(kwargs.get("sha256")),
                                    "ms": round((time.perf_counter() - started) * 1000, 1)})
        return size
    module.upload, module.fetch, module.download = recorded_upload, recorded_fetch, recorded_download


def main():
    from angelo_agent import team_account, team_account_ui, team_library_client, team_library_host
    from angelo_agent.blender_tools import BlenderExecutor
    from angelo_agent.controller import Controller
    from angelo_agent.scene_queries import object_handle
    from angelo_agent.team_library_spec import ITEM_PROPERTY

    started = time.perf_counter()
    account = team_account_ui.account()
    deadline = time.monotonic() + 90
    while account is not None and account.view().status not in {"signed_in", "signed_out"} and time.monotonic() < deadline:
        time.sleep(0.1)
    view = account.view() if account is not None else None
    report.update(seat=bool(view and view.seat), scopes=sorted(view.scopes) if view else [])
    step("restore_sign_in", started, view is not None and view.signed_in,
         status=view.status if view else "no account", message=str(view.message) if view else "")

    team_library_client.TeamLibraryClient.__init__.__defaults__ = (
        record_requests(team_library_client.TeamLibraryClient.__init__.__defaults__[0]),)
    record_transfers(team_library_host)
    window = bpy.context.window_manager.windows[0]
    controller = Controller(os.environ["ANGELO_STATE_DIR"] + "/live-chat")
    controller.set_document(("instance", "live-check.blend", window.scene.name))
    controller.busy = True
    executor = BlenderExecutor(window)
    keys = iter(range(1, 100))

    def run(name, arguments, approve=False, timeout=180):
        answers = []
        controller.request_tool("live-%d" % next(keys), name, arguments, answers.append)
        controller.pump_tools(executor, controller.document)
        if approve and not answers:  # The script stands in for the person's click.
            call = next(c for c in controller.pending.values() if c.name == name and c.approval)
            controller.execute_tool(call, executor, controller.document)
        until = time.monotonic() + timeout
        while not answers and time.monotonic() < until:
            controller.pump_events()
            time.sleep(0.02)
        if not answers:
            raise RuntimeError(name + " never answered")
        return answers[0]

    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.mesh.primitive_cube_add(size=0.5)
    cube = bpy.context.view_layer.objects.active
    cube.name = TITLE
    item_id = None
    try:
        started = time.perf_counter()
        saved = run("angelo_team_library_save", {
            "source": "OBJECTS", "objects": [object_handle(cube)], "space": "team", "title": TITLE,
            "description": "Automated end-to-end check of Angelo's team library tools; deleted right after.",
            "tags": ["angelo-live-check"]}, approve=True)
        item_id = saved.get("item_id")
        upload = next((t for t in report["transfers"] if t["kind"] == "upload"), {})
        step("save_glb", started, item_id and not saved.get("error"), error=saved.get("error"),
             checksum_header="x-amz-checksum-sha256" in upload.get("headers", []),
             verified_sha256=bool(saved.get("saved", {}).get("id")) and bool(
                 team_library_client.TeamLibraryClient(account).item(item_id).get("sha256")),
             thumbnail_uploaded=sum(1 for t in report["transfers"] if t["kind"] == "upload") == 2)

        started = time.perf_counter()
        found = run("angelo_team_library_search", {"query": TITLE, "limit": 5})
        step("search", started, any(i.get("id") == item_id for i in found.get("items", [])), error=found.get("error"),
             results=len(found.get("items", [])))

        started = time.perf_counter()
        got = run("angelo_team_library_get", {"item_id": item_id, "preview": True})
        step("get_with_preview", started, got.get("item", {}).get("id") == item_id and bool(got.get("views")),
             error=got.get("error"), preview_note=got.get("preview_note"),
             fields=sorted(got.get("item", {})))

        started = time.perf_counter()
        imported = run("angelo_team_library_import", {"item_id": item_id}, approve=True)
        tagged = [o for o in window.scene.objects if o.get(ITEM_PROPERTY) == item_id]
        step("import_into_scratch_scene", started, imported.get("success") and tagged, error=imported.get("error"),
             objects=imported.get("object_count"), format=imported.get("format"))
    finally:
        if item_id:
            started = time.perf_counter()
            client = team_library_client.TeamLibraryClient(account)
            try:
                client.delete_item(item_id)
                deleted, error = True, None
            except Exception as failure:  # noqa: BLE001 - reported; the item id is in the receipt for cleanup.
                deleted, error = False, str(getattr(failure, "message", failure))[:300]
            gone = False
            if deleted:
                try:
                    client.item(item_id)
                except team_library_client.TeamLibraryError as refusal:
                    gone = refusal.code == "NOT_FOUND"
            report["steps"].append({"step": "delete", "ok": deleted and gone, "error": error,
                                    "ms": round((time.perf_counter() - started) * 1000, 1)})
            if not (deleted and gone):
                report["leftover_item"] = item_id
    report["success"] = all(s["ok"] for s in report["steps"])


def tick():
    try:
        main()
    except Exception as error:  # noqa: BLE001 - the receipt records it.
        report.update(error=str(error)[:500], traceback=traceback.format_exc()[-2000:])
    REPORT.write_text(json.dumps(report, indent=2))
    print("ANGELO_LIVE_WRITE_CHECK", json.dumps({k: report.get(k) for k in ("success", "error")}), flush=True)
    bpy.ops.wm.quit_blender()


FAKE = None
if os.environ.get("ANGELO_LIVE_FAKE_CONSOLE") == "1":
    import sys
    import urllib.request
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fake_team_console import FakeConsole
    FAKE = FakeConsole()
    os.environ["ANGELO_CONSOLE_URL"] = FAKE.url
    assert os.environ.get("ANGELO_DISABLE_KEYCHAIN") == "1", "a dry run never uses the OS store"

# No grant key is issued or renewed in this process: the only OS-store item it may write is the sign-in's own.
from angelo_agent import team_account as _team_account  # noqa: E402
_team_account.TeamAccount.grant_key_due = lambda self, idle, now=None: False
from angelo_agent import ui as _ui  # noqa: E402
_ui._authentication.start = lambda *args, **kwargs: None  # No provider sign-in probes.
_ui.refresh_connected_models = lambda *args, **kwargs: None  # No provider catalog calls.
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
if FAKE is not None:
    from angelo_agent import team_account_ui as _team_account_ui
    _url = _team_account_ui.account().begin_sign_in(device="Dry run")
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(FAKE.callback(_url), timeout=5):
        pass
bpy.app.timers.register(tick, first_interval=2)
