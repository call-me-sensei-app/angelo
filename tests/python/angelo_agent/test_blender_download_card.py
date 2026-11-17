# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_download`` in the real window (doc/angelo/downloads.rst): its card in Ask and in Auto, a real click, the
file in ``assets/downloads``, and the refusals the person sees the agent get.

Real default start (Director); the chat connects through the stand-in ``claude`` CLI (``fake_provider_cli.py``),
then the suite asks for steps exactly as a provider's MCP call does (``Controller.request_tool``) and the app's own
pump raises the cards and runs what is approved. The network is a stand-in for ``download_fetch``'s own ``socket``
reference only (a resolver and connections that serve fixed bytes); nothing else in the app changes and nothing
leaves the computer. Every string the chat canvas draws is recorded where it is drawn, with a window screenshot at
each moment checked.

1. Ask: the card names the host in its title and shows the link, the kind, the 512 MB limit, the unsaved
   document's temporary folder and that the file isn't opened, run or imported. Approve by a real click: the file lands in
   ``assets/downloads`` of that folder (``angelo-downloads/<id>`` in Blender's temporary folder, never the chat's
   own folder, which agent scripts can't read) under the requested name with the checked type's extension, its bytes,
   size and SHA-256 as the result says, not executable, and the scene is unchanged.
2. Auto: the same tool still shows its card, with Auto's line (Approve for me never runs this kind of step on its
   own). Approved, a link whose name resolves to the cloud metadata address is refused (``address_link_local``)
   before any connection, and a page that is not an HDRI is refused (``wrong_type``); neither leaves a file.

Run through ``run_native_suites.py --gui --include test_blender_download_card``; check ``download-card-gui.json`` for
``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import hashlib
import json
import socket as real_socket
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-download-card-qa"))
OUT.mkdir(parents=True, exist_ok=True)
import fake_provider_cli  # noqa: E402
import qa_input  # noqa: E402  (the person's presses: qa_input.press, after_clicks)
from angelo_agent import product_env  # noqa: E402
import qa_boot  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')
fake_provider_cli.sign_in(os.environ["HOME"], "claude")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "download-card-gui.json"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)) * 16
PAGE = b"<!doctype html><html><title>Sign in to download</title></html>"
DNS = {"textures.example.com": "93.184.216.34", "hdri.example.com": "93.184.216.35",
       "metadata.example.com": "169.254.169.254"}
BODIES = {("textures.example.com", "/brick_wall_4k.png"): PNG, ("hdri.example.com", "/sky.hdr"): PAGE}
ASK = {"url": "https://textures.example.com/brick_wall_4k.png", "kind": "texture", "name": "Brick wall"}
METADATA = {"url": "https://metadata.example.com/latest/meta-data/", "kind": "other"}
NOT_HDRI = {"url": "https://hdri.example.com/sky.hdr", "kind": "hdri"}
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "drawn": {}, "replies": [],
         "connections": [], "fixture": "Stand-in claude CLI; steps requested as a provider's MCP call does; real "
                                       "clicks; a stand-in network for download_fetch only; no account or inference"}
started = time.monotonic()
drawn_now = []


def stand_in_network():
    """``download_fetch``'s ``socket`` and connection, standing in for the internet."""
    from angelo_agent import download_fetch

    def getaddrinfo(host, port, *args, **kwargs):
        if host not in DNS:
            raise real_socket.gaierror(8, "unknown host")
        return [(real_socket.AF_INET, real_socket.SOCK_STREAM, 6, "", (DNS[host], port))]

    def connect(host, address, port, proxy, timeout):
        class Answer:
            def __init__(self, body):
                self.status, self.body, self.at = 200, body, 0

            def getheader(self, name, default=None):
                return str(len(self.body)) if name.lower() == "content-length" else default

            def read(self, size=-1):
                data = self.body[self.at:self.at + (size if size and size > 0 else len(self.body))]
                self.at += len(data)
                return data

        class Connection:
            def request(self, method, path, headers=None):
                state["connections"].append({"host": host, "address": address, "path": path})
                self.path = path

            def getresponse(self):
                return Answer(BODIES[(host, self.path)])

            def close(self):
                pass
        return Connection()
    download_fetch.socket = SimpleNamespace(getaddrinfo=getaddrinfo, SOCK_STREAM=real_socket.SOCK_STREAM,
                                            AF_INET=real_socket.AF_INET)
    download_fetch._connect = connect


def install_recorder():
    from angelo_agent import chat_view
    if not getattr(chat_view.text, "_download_recorder", False):
        original = chat_view.text

        def text(value, *args, **kwargs):
            drawn_now.append(str(value))
            return original(value, *args, **kwargs)
        text._download_recorder = True
        chat_view.text = text


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def hits():
    from angelo_agent import chat_view
    return list(chat_view._views.get(chat()[0].as_pointer(), {}).get("hits", []))


def actions():
    return [(action, value) for _, action, value in hits()]


def click(action, value=None):
    """The person's click on a drawn chat control; an approval's own (Approve) aimed, as a person aims it: it takes
    only a press after its shield (``approval_arming``)."""
    area, region = chat()
    rect = next(r for r, a, v in hits() if a == action and (value is None or v == value))
    import qa_input
    from angelo_agent import approval_arming, chat_view
    scale = chat_view.ui_scale()   # Canvas points to window pixels, as the chat maps them back.
    if action in approval_arming.GRANTS:
        view = chat_view._views[area.as_pointer()]
        return qa_input.window_click(window(), region, rect, action, scale=scale,
                                     ready=lambda: approval_arming.view(view).key_armed((action, value)))
    qa_input.press(window(), *qa_input.chat_point(region, rect))   # A person's press: onto it a beat after reaching it.


def compact(text):
    return "".join(str(text).split())


def capture(name):
    drawn_now.clear()
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        state["drawn"][name] = list(drawn_now)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return " ".join(" ".join(state["drawn"][name]).split())


def finish(error=None):
    if state.get("finished"):
        return
    state["finished"] = True
    try:
        from angelo_agent import ui
        controller = ui.get_controller()
        controller.cancel_tools("Download card GUI fixture ended", persist=False)
        controller.busy = False
    except Exception:   # noqa: BLE001
        state["cleanup_error"] = traceback.format_exc()
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_DOWNLOAD_CARD", json.dumps({"success": state["success"], "phase": state["phase"],
          "failed": [name for name, ok in state["checks"].items() if not ok]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=30):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def check(name, condition, detail=None):
    state["checks"][name] = bool(condition)
    if detail is not None and not condition:
        state.setdefault("details", {})[name] = detail


def ask_for(controller, label, arguments):
    controller.busy, controller.cancelled = True, False
    controller.request_tool(label, "angelo_download", arguments, state["replies"].append)
    chat()[0].tag_redraw()
    return controller.state["id"] + "/" + label


def reply_for(arguments):
    """The newest reply that answers a download (a dict with path or error_code), or None."""
    found = [r for r in state["replies"] if isinstance(r, dict) and ("path" in r or "error_code" in r)]
    return found[-1] if len(found) > state.get("answered", 0) else None


def approve_step(controller, key, then):
    """Click Approve (a card whose first click shows its details takes a second) until the call leaves the card."""
    call = controller.pending.get(state[key])
    if call is None or not call.approval:
        state["phase"] = then
        return .3
    assert state.setdefault(key + "_clicks", 0) < 3, "Approve clicked three times; the call still waits"
    if ("approve", state[key]) in actions():
        click("approve", state[key])
        state[key + "_clicks"] += 1
    return .4


def tick():
    try:
        assert time.monotonic() - started < 240, "Download card fixture timed out in " + state["phase"]
        from angelo_agent import auto_reasons, director, quick_connect, ui
        from angelo_agent.ui_text import iface_
        phase = state["phase"]
        controller = ui.get_controller()
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not wait(lambda: not ui._authentication.running
                        and ("welcome_connect", "claude_subscription") in actions()):
                return .2
            install_recorder()
            stand_in_network()
            click("welcome_connect", "claude_subscription")
            state["phase"] = "connected"
            return .3
        if phase == "connected":
            if not wait(lambda: quick_connect.flow("claude_subscription") is None and controller.models
                        and ui.connection_available()):
                return .2
            check("starts_in_director_mode", director.is_director(window()))
            check("the_file_is_unsaved", not bpy.data.filepath)
            state["objects_before"] = sorted(o.name for o in bpy.data.objects)
            controller.set_options(approval="ASK")
            state["ask"] = ask_for(controller, "dl-ask", ASK)
            state["phase"] = "ask_card"
            return .3
        if phase == "ask_card":
            if not wait(lambda: ("approve", state["ask"]) in actions()):
                return .2
            shown = capture("dl-1-ask-card.png")
            check("ask_title_names_the_host", compact(iface_("Download a file from {host}",
                                                             host="textures.example.com")) in compact(shown), shown)
            for name, text in (("link", ASK["url"]), ("size", "512 MB"), ("folder", "assets/downloads"),
                               ("temporary_folder", "temporary folder"), ("not_run", "isn't opened, run or imported")):
                check("ask_card_shows_" + name, compact(text) in compact(shown), shown)
            chips = state["drawn"]["dl-1-ask-card.png"]
            check("ask_chip_says_download", iface_("File download") in chips and iface_("Scene changes") not in chips,
                  chips)
            check("nothing_connected_before_approval", state["connections"] == [], state["connections"])
            state["phase"] = "ask_approve"
            return .2
        if phase == "ask_approve":
            return approve_step(controller, "ask", "ask_done")
        if phase == "ask_done":
            if not wait(lambda: reply_for(ASK) is not None):
                return .2
            result = reply_for(ASK)
            state["answered"] = len([r for r in state["replies"] if isinstance(r, dict)
                                     and ("path" in r or "error_code" in r)])
            state["ask_result"] = result
            from angelo_agent import download_folders
            path = Path(str(result.get("file", "-")))
            state["unsaved_root"] = str(download_folders.root(bpy))
            check("the_file_landed_in_the_unsaved_documents_folder", result.get("folder") == "unsaved"
                  and path == Path(state["unsaved_root"]) / "assets" / "downloads" / "Brick wall.png", result)
            check("never_in_the_chats_folder", Path(controller.workspace).resolve() not in path.resolve().parents)
            check("its_bytes_are_the_files", path.is_file() and path.read_bytes() == PNG, str(path))
            check("size_and_sha256_match", result.get("bytes") == len(PNG)
                  and result.get("sha256") == hashlib.sha256(PNG).hexdigest(), result)
            check("not_executable", path.is_file() and os.stat(path).st_mode & 0o111 == 0)
            check("the_scene_is_unchanged", result.get("scene_changed") is False
                  and sorted(o.name for o in bpy.data.objects) == state["objects_before"])
            check("pinned_to_the_checked_address", state["connections"][-1]["address"] == DNS["textures.example.com"])
            capture("dl-2-ask-downloaded.png")
            from angelo_agent import grant_guard   # The person picks Auto: the harness stands in.
            grant_guard.harness(controller.set_options, approval="AUTO_BOUNDED")
            state["metadata"] = ask_for(controller, "dl-auto-metadata", METADATA)
            state["phase"] = "auto_card"
            return .3
        if phase == "auto_card":
            if not wait(lambda: ("approve", state["metadata"]) in actions()):
                return .2
            shown = capture("dl-3-auto-card.png")
            line = iface_(auto_reasons.LINES[auto_reasons.OUTSIDE])
            check("auto_still_shows_the_card", controller.pending[state["metadata"]].approval)
            check("auto_card_says_why_it_asks", compact(line) in compact(shown), shown)
            check("auto_title_names_the_host", "metadata.example.com" in shown, shown)
            state["connections_before_metadata"] = len(state["connections"])
            state["phase"] = "auto_approve"
            return .2
        if phase == "auto_approve":
            return approve_step(controller, "metadata", "metadata_done")
        if phase == "metadata_done":
            if not wait(lambda: reply_for(METADATA) is not None):
                return .2
            result = reply_for(METADATA)
            state["answered"] += 1
            state["metadata_result"] = result
            check("cloud_metadata_is_refused", result.get("reason") == "address_link_local"
                  and result.get("error_code") == "VALIDATION", result)
            check("no_connection_to_it", len(state["connections"]) == state["connections_before_metadata"],
                  state["connections"])
            capture("dl-4-auto-metadata-refused.png")
            state["page"] = ask_for(controller, "dl-auto-page", NOT_HDRI)
            state["phase"] = "page_card"
            return .3
        if phase == "page_card":
            if not wait(lambda: ("approve", state["page"]) in actions()):
                return .2
            state["phase"] = "page_approve"
            return .1
        if phase == "page_approve":
            return approve_step(controller, "page", "page_done")
        if phase == "page_done":
            if not wait(lambda: reply_for(NOT_HDRI) is not None):
                return .2
            result = reply_for(NOT_HDRI)
            state["page_result"] = result
            check("a_page_is_not_an_hdri", result.get("reason") == "wrong_type"
                  and result.get("error_code") == "CHECK_FAILED", result)
            folder = Path(state["unsaved_root"]) / "assets" / "downloads"
            check("refusals_leave_no_file", sorted(p.name for p in folder.iterdir()) == ["Brick wall.png"],
                  sorted(p.name for p in folder.iterdir()))
            capture("dl-5-auto-page-refused.png")
            finish()
            return None
    except Exception as error:   # noqa: BLE001
        finish(error)
        return None
    return .2


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)   # Never ahead of a press.
