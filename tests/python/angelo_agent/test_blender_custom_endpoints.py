# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An organization's own model server (a custom endpoint) in the real app, from sign-in to sign-out.

Signs in through the app's own loopback listener against the in-process fake console
(fake_team_console.py), whose providers/available lists one model server: a local fake
Responses server on 127.0.0.1 with one model mapped to a profile and one unmapped. Then
checks and captures:

1. AI Providers lists the organization's section and the server, asking for the person's key;
   its connection is offered but not ready;
2. the key sheet: the key typed into its masked field and saved (never in any file), then Test
   connection through the sheet, which reaches the fake server with that key and reports;
3. the connection is ready, its unmapped model labelled untested;
4. the seat removed on the console: the reason says so and the key leaves the runtimes; the
   seat given back restores it;
5. sign-out forgets the key and the connections.

Nothing leaves 127.0.0.1, the key store is off, no model runs. Screenshots and
``custom-endpoints.json`` go to ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-custom-endpoints-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"  # The fixture key must never reach a login Keychain.
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_team_console import FakeConsole  # noqa: E402
import qa_waits  # noqa: E402

addon_utils.enable("angelo_agent", default_set=False, persistent=False)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_input  # noqa: E402  (the person's press on the server's key control, which C mints a ticket for)
from angelo_agent import (chat_view, endpoint_connections, endpoint_ui, providers_page_host as page,  # noqa: E402
                          team_account_ui, ui, ui_kit_field)
from angelo_agent.endpoint_sheet import EndpointSheet  # noqa: E402

bpy.context.preferences.view.show_splash = False
KEY = "fixture-endpoint-key-not-a-real-credential"
IDENTITY = "5d0a8a43-0b7e-4f37-9c55-6c1b5b7c2a10"
WAIT_S = 20   # x the runner's timeout scale
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
state = {"phase": "boot", "success": False, "checks": {},
         "fixture": "In-process fake console and fake model server on 127.0.0.1; no network, no key store, no model"}
seen = []   # (method, path, carried the key) per request the fake model server answered.


class ModelServer(BaseHTTPRequestHandler):
    """A Responses server: the model list, a tool call when asked for one, a reply streamed in spaced pieces."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _record(self):
        seen.append((self.command, self.path, self.headers.get("authorization") == "Bearer " + KEY))

    def do_GET(self):  # noqa: N802
        self._record()
        body = json.dumps({"data": [{"id": "team-gpt-sol"}, {"id": "house-model"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        self._record()
        request = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()
        if request.get("tools"):
            events = [{"type": "response.output_item.done",
                       "item": {"type": "function_call", "name": "angelo_check", "arguments": '{"ok": true}'}}]
        else:
            events = [{"type": "response.output_text.delta", "delta": "word "} for _ in range(20)]
        for event in events + [{"type": "response.completed", "response": {"status": "completed"}}]:
            self.wfile.write(("data: %s\n\n" % json.dumps(event)).encode())
            self.wfile.flush()
            time.sleep(0.05)


server = ThreadingHTTPServer(("127.0.0.1", 0), ModelServer)
threading.Thread(target=server.serve_forever, daemon=True).start()
console = FakeConsole()
console.custom_endpoints = [{
    "id": IDENTITY, "name": "Northwind Model Server", "baseUrl": "http://127.0.0.1:%d" % server.server_address[1],
    "formats": {"openai-responses": {"path": "/v1"}}, "credential": "member_key",
    "models": [{"id": "team-gpt-sol", "format": "openai-responses", "profile": "gpt-6.1-sol"},
               {"id": "house-model", "format": "openai-responses"}]}]
HEX = IDENTITY.replace("-", "")
MODE = endpoint_connections.mode_for(HEX, "openai-responses")


def save():
    (OUT / "custom-endpoints.json").write_text(json.dumps(state, indent=2))


def capture(file, window):
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / file))


def prefs_window():
    return next((w for w in bpy.context.window_manager.windows
                 if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)


def record():
    return next((r for r in endpoint_ui.records().values() if r["mode"] == MODE), None)


def template(text):
    return getattr(text, "template", text)


def waited(label):
    """True while ``label``'s bounded wait lasts; the wait's end fails the suite with its name."""
    deadline = state.setdefault("deadlines", {}).setdefault(label, time.monotonic() + WAIT_S * qa_waits.timeout_scale())
    assert time.monotonic() < deadline, "waited too long for " + label
    return True


def files_without_key():
    """No file Angelo wrote under its state folders holds the key."""
    roots = [Path(ui.connection_directory())] + [Path(os.environ[v]) for v in ("ANGELO_STATE_DIR",) if v in os.environ]
    for root in roots:
        for path in root.rglob("*") if root.is_dir() else ():
            if path.is_file() and KEY.encode() in path.read_bytes():
                return False
    return True


def tick():
    try:
        window = bpy.context.window_manager.windows[0]
        ui.pump()
        account = team_account_ui.account()
        phase = state["phase"]
        if phase == "boot":
            environ = dict(os.environ, ANGELO_CONSOLE_URL=console.url, ANGELO_GATEWAY_URL="http://127.0.0.1:9")
            assert team_account_ui.start(ui, background=False, environ=environ)
            account = team_account_ui.account()
            DIRECT.open(console.callback(account.begin_sign_in(device="Angelo QA")), timeout=10).close()
            state["phase"] = "signing_in"
            return .2
        if phase == "signing_in":
            view = account.view()
            if not (view.signed_in and view.providers is not None and record() is not None) and waited("sign-in"):
                account.tick(idle=True)
                return .2
            rec = record()
            state["checks"]["endpoint_listed_from_the_console"] = (
                endpoint_ui.source()[0] == "console" and rec["name"].startswith("Northwind Model Server"))
            state["checks"]["asks_for_the_persons_key"] = template(endpoint_ui.reason(rec)) == endpoint_ui.NEEDS_KEY
            state["checks"]["not_ready_without_a_key"] = not ui.connection_ready(rec)
            with bpy.context.temp_override(window=window):
                bpy.ops.angelo.settings()
            state["phase"] = "page"
            return .6
        if phase == "page":
            prefs = prefs_window()
            capture("1-ai-providers-endpoint-needs-key.png", prefs)
            drawn = page.states_drawn[-1] if page.states_drawn else {}
            state["page"] = {k: list(v) if isinstance(v, (list, tuple)) else v for k, v in drawn.items()
                             if isinstance(v, (list, tuple, str, int, bool))}
            state["checks"]["page_draws_the_organization_section"] = (
                "Your organization's model servers" in json.dumps(state["page"]))
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            from angelo_agent import grant_guard
            with bpy.context.temp_override(window=prefs, area=area):
                try:   # A script's click on the server's key (no press C saw): refused.
                    scripted = str(bpy.ops.angelo.endpoint_key('INVOKE_DEFAULT', endpoint=HEX))
                except RuntimeError as error:
                    scripted = str(error)
            state["checks"]["a_scripted_key_sheet_is_refused"] = (grant_guard.NOT_YOURS in scripted
                                                                   and page._sheet is None)
            state["phase"] = "key_press"
            return .2
        if phase == "key_press":   # The server's Add key, scrolled into view, pressed by the person.
            prefs = prefs_window()
            area = next(a for a in prefs.screen.areas if a.type == 'PREFERENCES')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            plan = page.plan_for(region)
            rect = next((r for r, action, _value in (plan.hits if plan else ()) if action == "endpoint_key"), None)
            if rect is None or not (24 <= rect[1] and rect[1] + rect[3] <= region.height - 24):
                if waited("the server's key control in view") and rect is not None:
                    scroll = bpy.ops.view2d.scroll_down if rect[1] < 24 else bpy.ops.view2d.scroll_up
                    with bpy.context.temp_override(window=prefs, area=area, region=region):
                        scroll()
                return .2
            state["key_press"] = {"rect": list(rect), "region": [region.width, region.height]}
            qa_input.press(prefs, region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2)   # The person's.
            state["phase"] = "key_sheet"
            return .5
        if phase == "key_sheet":
            if not isinstance(page._sheet, EndpointSheet) and waited("the key sheet the person's press opens"):
                return .2
            state["checks"]["key_sheet_opens"] = isinstance(page._sheet, EndpointSheet)
            state["phase"] = "sheet"
            return .2
        if phase == "sheet":
            sheet, host = page._sheet, page._SheetHost(page._sheet)
            sheet.act("field", None, host)
            ui_kit_field.paste(sheet.key, KEY)   # As a paste into the masked field.
            capture("2-key-sheet-typed.png", prefs_window())
            state["checks"]["typed_key_masked"] = not sheet.key.revealed
            sheet.act("test", None, host)   # Saves the typed key, then runs Test connection.
            state["checks"]["field_wiped_after_save"] = sheet.key.text == ""
            state["phase"] = "testing"
            return .3
        if phase == "testing":
            if endpoint_ui.checking(HEX) and waited("test connection"):
                return .3
            endpoint_ui.pump()
            report = endpoint_ui.check_report(HEX)
            capture("3-key-sheet-test-connection.png", prefs_window())
            state["report"] = [str(f.text) for f in report.findings] if report else None
            state["checks"]["test_connection_works"] = bool(report) and report.ok
            state["checks"]["requests_carried_the_key"] = bool(seen) and all(carried for _m, _p, carried in seen)
            state["checks"]["model_list_and_responses_reached"] = (
                ("GET", "/v1/models") in [(m, p) for m, p, _c in seen]
                and sum(m == "POST" and p == "/v1/responses" for m, p, _c in seen) >= 2)
            page.close_sheet("done")
            state["phase"] = "ready"
            return .4
        if phase == "ready":
            rec = record()
            state["checks"]["ready_with_the_key"] = (ui.connection_ready(rec)
                                                     and template(ui.connection_status(rec)) == endpoint_ui.READY)
            state["checks"]["offered_in_chat"] = any(r["id"] == rec["id"] for r in ui.ready_connections())
            models = {m["id"]: m for m in endpoint_connections.spec(MODE)["models"]}
            state["checks"]["unmapped_model_is_untested"] = (
                models["house-model"].get("untested") and models["team-gpt-sol"].get("profile") == "gpt-6.1-sol")
            state["checks"]["key_in_no_file"] = files_without_key()
            capture("4-ai-providers-endpoint-ready.png", prefs_window())
            console.seat = False
            account.access_token(stale=account.access_token())   # The next token says seat: false.
            state["phase"] = "no_seat"
            return .3
        if phase == "no_seat":
            if account.view().seat and waited("seat removed"):
                return .2
            rec = record()
            state["checks"]["seat_removed_says_so"] = template(endpoint_ui.reason(rec)) == endpoint_connections.NO_SEAT
            state["checks"]["seat_removed_withdraws_the_key"] = (
                not ui.connection_ready(rec) and not ui._memory_keys.get(rec["id"]))
            for area in prefs_window().screen.areas:
                area.tag_redraw()
            state["phase"] = "no_seat_capture"
            return .4
        if phase == "no_seat_capture":
            capture("5-ai-providers-endpoint-no-seat.png", prefs_window())
            console.seat = True
            account.access_token(stale=account.access_token())
            state["phase"] = "seat_back"
            return .3
        if phase == "seat_back":
            rec = record()
            if not (account.view().seat and ui.connection_ready(rec)) and waited("seat given back"):
                return .2
            state["checks"]["seat_given_back_restores_the_key"] = ui._memory_keys.get(rec["id"]) == KEY
            account.sign_out()
            state["phase"] = "signed_out"
            return .3
        if phase == "signed_out":
            if (account.view().signed_in or endpoint_ui.has_key(HEX)) and waited("sign-out"):
                return .2
            rec = record()
            state["checks"]["sign_out_forgets_key_and_connection"] = (
                not endpoint_ui.has_key(HEX) and (rec is None or template(endpoint_ui.reason(rec)) == endpoint_ui.SIGN_IN))
            state["checks"]["key_in_no_file_after_sign_out"] = files_without_key()
            for area in prefs_window().screen.areas:
                area.tag_redraw()
            state["phase"] = "signed_out_capture"
            return .4
        if phase == "signed_out_capture":
            capture("6-ai-providers-signed-out.png", prefs_window())
            state["checks"]["no_chat_draw_error"] = chat_view.last_error is None
            state.pop("deadlines", None)
            state["success"] = all(state["checks"].values())
            state["phase"] = "complete"
            save()
            print("ANGELO_CUSTOM_ENDPOINTS_GUI", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
        return .2
    except Exception as error:
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        state.pop("deadlines", None)
        save()
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None


# Held until Angelo's start-up (the first tab build its enable schedules) is over: qa_boot.gated.
bpy.app.timers.register(qa_boot.gated(qa_input.after_clicks(tick)), first_interval=1)   # Never ahead of a press.
