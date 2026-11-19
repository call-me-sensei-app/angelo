# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The headless agent on macOS, end to end (LIVE, billed): a hidden-window Angelo driven through its real MCP launcher.

A plain MCP client speaks stdio JSON-RPC to the app's own ``scripts/angelo/angelo_mcp.py`` (run with the app's
bundled Python, as an AI app starts it) with the test profile's connections, config and library and a fresh state
folder (never a sign-in: the profile's connection must already exist). It proves, with state checks, window samples
and screenshots (doc/angelo/mcp_headless_window.rst, "Validation"):

1. ``angelo_open_file {agent: true}``: no window of Angelo's on screen, no Dock icon (activation policy) and no focus
   change while it starts and works (sampled every 100 ms);
2. ``angelo_viewport`` draws while hidden (a real picture, not blank);
3. ``angelo_send`` runs a turn and ``angelo_read`` reports ``state.session``;
4. a script card is relayed read-only; approve answers ``needs_person``; decline declines it;
5. a second card: ``angelo_show`` brings the window up (a screenshot with the card); it outlives the launcher;
6. ``angelo_close_file`` saves next to the source, the source's bytes unchanged, the process gone;
7. the person's own launch reveals the hidden session (it comes up as their window; the second launch ends), and a
   Finder or Dock launch (``open -a``) does too: LaunchServices reopens the running app;
8. a one-minute time cap ends the session with its edits saved.

``--app`` is an Angelo.app copy with this branch's installed files overlaid. Refuses to start while any Angelo runs.
Processes are ended by pid only. Everything goes to ``--out``::

    <Angelo.app>/Contents/Resources/<v>/python/bin/python3.13 -I check_mcp_hidden_window.py --app <Angelo.app> \\
        --profile "<home>/Library/Application Support/Angelo Agent Test" --out <folder ending in .noindex>
"""

import argparse
import ctypes
import ctypes.util
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import provider_isolation as isolation  # noqa: E402  (the connection store it hands Angelo, before it is billed)

CLIENT = "claude_code"
ACCESS_ON = ("import bpy; from angelo_agent import grant_guard; "
             "prefs = bpy.context.preferences.addons['angelo_agent'].preferences; "
             "grant_guard.harness(setattr, prefs, 'allow_local_cli', True); "
             "grant_guard.harness(setattr, prefs, 'provider', 'claude_subscription'); bpy.ops.wm.save_userpref(); "
             "import os; open(os.environ['ANGELO_PROOF_ACCESS'], 'w').write(str(prefs.allow_local_cli)); os._exit(0)")
SEND = "Add a cube named HiddenProof of size 0.5 at x=0, y=0, z=1. Do it with a typed edit, not a script."
SCRIPT_SEND = ("Call angelo_execute_python (your Python script tool, not a typed edit) with exactly this script and "
               "nothing else, then stop: import bpy; bpy.data.objects['HiddenProof'].location.x = {x}")


class Client:
    """One MCP session over the launcher's stdio."""

    def __init__(self, python, launcher, environ, log, label, state):
        # The app's name as Connect wrote it (``claude-code``), as an app's MCP settings carry it.
        self.process = subprocess.Popen([python, "-I", str(launcher), "--client", label, "--state-dir", str(state)],
                                        stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=log, env=environ, text=True, bufsize=1)
        self.next_id = 0
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "hidden-window-check", "version": "1"}})
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")

    def request(self, method, params):
        self.next_id += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method,
                                             "params": params}) + "\n")
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("the launcher ended")
            message = json.loads(line)
            if message.get("id") == self.next_id:
                return message

    def call(self, name, arguments=None):
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}}).get("result") or {}
        value = result.get("structuredContent")
        if isinstance(value, dict):
            return value, result
        text = "".join(part.get("text", "") for part in result.get("content") or () if part.get("type") == "text")
        try:
            return json.loads(text), result
        except ValueError:
            return {"text": text, "isError": result.get("isError")}, result

    def end(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        self.process.wait(timeout=120)


def angelo_pids(binary):
    listing = subprocess.run(["/bin/ps", "-U", str(os.getuid()), "-o", "pid=,comm="], capture_output=True, text=True)
    return [int(line.split(None, 1)[0]) for line in listing.stdout.splitlines()
            if len(line.split(None, 1)) == 2 and line.split(None, 1)[1].strip() == str(binary)]


def front_pid():
    asn = subprocess.run(["/usr/bin/lsappinfo", "front"], capture_output=True, text=True).stdout.strip()
    info = subprocess.run(["/usr/bin/lsappinfo", "info", "-only", "pid", asn], capture_output=True, text=True).stdout
    digits = "".join(ch for ch in info.split("=")[-1] if ch.isdigit())
    return int(digits) if digits else None


def app_type(pid):
    """``"ApplicationType"="Foreground"`` (a regular app) and the like, by the app's ASN as lsappinfo needs it."""
    asn = subprocess.run(["/usr/bin/lsappinfo", "find", "pid=%d" % pid], capture_output=True, text=True).stdout.strip()
    if not asn:
        return "not registered"
    return subprocess.run(["/usr/bin/lsappinfo", "info", "-only", "ApplicationType", asn], capture_output=True,
                          text=True).stdout.strip()


def onscreen_windows(pid):
    """How many on-screen windows ``pid`` owns (CGWindowListCopyWindowInfo; no screen-recording permission needed)."""
    cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
    cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    cf.CFArrayGetCount.restype, cf.CFArrayGetCount.argtypes = ctypes.c_long, [ctypes.c_void_p]
    cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    windows = cg.CGWindowListCopyWindowInfo(1 | 16, 0)   # On screen only, no desktop elements.
    key = cf.CFStringCreateWithCString(None, b"kCGWindowOwnerPID", 0x08000100)
    count = 0
    try:
        for index in range(cf.CFArrayGetCount(windows)):
            number = cf.CFDictionaryGetValue(cf.CFArrayGetValueAtIndex(windows, index), key)
            owner = ctypes.c_int(0)
            if number and cf.CFNumberGetValue(number, 9, ctypes.byref(owner)) and owner.value == pid:
                count += 1
    finally:
        cf.CFRelease(key)
        cf.CFRelease(windows)
    return count


class Watch:
    """Samples, every 100 ms until stopped: Angelo's on-screen windows, its activation type and the frontmost app."""

    def __init__(self, binary, front):
        self.binary, self.front, self.samples, self.running = binary, front, [], True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while self.running:
            for pid in angelo_pids(self.binary):
                self.samples.append({"t": time.monotonic(), "pid": pid, "windows": onscreen_windows(pid),
                                     "front": front_pid() == pid})
            time.sleep(0.1)

    def stop(self):
        self.running = False
        self.thread.join(5)  # window: the sampler ends within one 100 ms sample
        return {"samples": len(self.samples), "windows_on_screen": sum(s["windows"] for s in self.samples),
                "frontmost": sum(1 for s in self.samples if s["front"]), "front_still": front_pid() == self.front}


def screenshot(out, name):
    path = out / (name + ".png")
    subprocess.run(["/usr/sbin/screencapture", "-x", str(path)], check=False)
    return path.name


def wait_for(predicate, seconds, step=1.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def read_until(client, predicate, seconds):
    deadline, state = time.monotonic() + seconds, {}
    while time.monotonic() < deadline:
        value, _raw = client.call("angelo_read", {"wait_seconds": 20, "detail": "brief"})
        state = value.get("state") or value
        if predicate(state):
            return state
    return state


def fixture(binary, out, environ):
    source = out / "proof.blend"
    subprocess.run([str(binary), "--background", "--factory-startup", "--python-expr",
                    "import bpy; bpy.ops.wm.save_as_mainfile(filepath=%r)" % str(source)], env=environ,
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    return source


def end_all(binary):
    for pid in angelo_pids(binary):   # By pid only.
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    wait_for(lambda: not angelo_pids(binary), 60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    app, profile, out = Path(args.app), Path(args.profile), Path(args.out)
    binary = app / "Contents/MacOS/Angelo"
    out.mkdir(parents=True, exist_ok=True)
    connections = profile / "connections"
    isolation.check_billed(stores=[connections])
    if angelo_pids(binary) or subprocess.run(["/usr/bin/pgrep", "-x", "Angelo"], capture_output=True).stdout:
        sys.exit("An Angelo runs already: quit it first (a hidden session is refused while one runs).")
    state = out / "state"   # A fresh state folder: no profile chat history, nothing of the person's.
    environ = isolation.without_credentials(dict(os.environ))
    config = out / "config"   # A copy of the profile's preferences, so the switch below never touches the profile.
    shutil.copytree(profile / "config", config, dirs_exist_ok=True)
    environ.update(ANGELO_CONNECTIONS_DIR=str(connections), ANGELO_STATE_DIR=str(state),
                   ANGELO_LIBRARY_DIR=str(profile / "library"), BLENDER_USER_CONFIG=str(config),
                   BLENDER_USER_SCRIPTS=str(profile / "scripts"), ANGELO_HIDDEN_APP_LOG=str(out / "hidden-app.log"))
    resources = next((app / "Contents/Resources").glob("[0-9]*.[0-9]*"))
    sys.path.insert(0, str(resources / "scripts/addons_core"))
    os.environ["ANGELO_STATE_DIR"] = str(state)
    from angelo_agent.external_clients import AppRegistry
    # What the person did in Angelo's window before any app works without it: turned on "Allow apps on this computer
    # to reach Angelo" and chose their Claude subscription (the profile's active connection) as the connection (as
    # the live MCP checks do, in the harness scope, saved into this check's own preferences copy), and pressed
    # Connect (in this check's own state folder).
    with open(out / "access-on.log", "w") as access_log:
        # The harness scope exists only with --enable-event-simulate, and the add-on's preferences only in a GUI
        # start: this preferences step only (before anything is measured), never the session.
        subprocess.run([str(binary), "--enable-event-simulate", "--python-expr", ACCESS_ON],
                       check=True, timeout=180,
                       env=dict(environ, ANGELO_PROOF_ACCESS=str(out / "access.txt")), stdout=access_log,
                       stderr=subprocess.STDOUT)
    access = (out / "access.txt").read_text() if (out / "access.txt").is_file() else "not written"
    if access != "True":
        sys.exit("The test profile's copy could not turn on app access (%s); see access-on.log." % access)
    label = AppRegistry(state).connect(CLIENT)
    launcher = resources / "scripts/angelo/angelo_mcp.py"
    python = sys.executable
    report, log = {"steps": []}, open(out / "launcher.log", "w")

    def step(name, ok, **facts):
        report["steps"].append(dict(facts, step=name, ok=bool(ok)))
        print(("PASS " if ok else "FAIL ") + name, json.dumps(facts, default=str)[:600], flush=True)
        (out / "report.json").write_text(json.dumps(report, indent=2, default=str))

    source = fixture(binary, out, environ)
    before = source.read_bytes()
    front = front_pid()
    # 1-5: open hidden (watched), a picture, a turn, a relayed card, approve/decline, show.
    client = Client(python, launcher, environ, log, label, state)
    tools = {tool["name"] for tool in client.request("tools/list", {})["result"]["tools"]}
    step("tools", {"angelo_show", "angelo_send", "angelo_read", "angelo_answer_approval"} <= tools, tools=sorted(tools))
    watch = Watch(binary, front)
    opened, _raw = client.call("angelo_open_file", {"file": str(source), "caps": {"usd": 2}})
    pids = wait_for(lambda: angelo_pids(binary), 30)
    step("opened_hidden", opened.get("hidden") is True and len(pids) == 1, result=opened, pids=pids,
         app_type=pids and app_type(pids[0]), shot=screenshot(out, "1-hidden"))
    sent, _raw = client.call("angelo_send", {"message": SEND, "wait_seconds": 120})
    report["sent"] = sent
    state_now = read_until(client, lambda s: s.get("turn") in ("idle", "done", "ended"), 240)
    rows = state_now.get("rows") or ()
    step("turn", (state_now.get("session") or {}).get("hidden") is True
         and any(row.get("role") in ("assistant", "tool") for row in rows)
         and not any(row.get("role") == "error" for row in rows), state=state_now)
    # The model the person would pick for the app's conversation in the window: the profile's Claude connection.
    settings, _raw = client.call("angelo_settings")   # Read after the first send: the conversation exists then.
    step("settings", "claude" in json.dumps(settings.get("connection") or settings).lower(), settings=settings)
    _view, raw = client.call("angelo_viewport")
    image = next((part for part in raw.get("content") or () if part.get("type") == "image"), None)
    if image:
        import base64
        (out / "2-viewport-hidden.png").write_bytes(base64.b64decode(image["data"]))
    # A real picture, not a blank one: a uniform 512 px PNG is well under 2 KB; a drawn scene is several times that.
    step("draws_while_hidden", image is not None and len(image.get("data", "")) > 4000,
         bytes=image and len(image["data"]),
         raw=None if image and image.get("data") else json.dumps(raw)[:2000])
    # An app may narrow its conversation's approval (never widen it): with Ask, the next edit waits for the person,
    # script or typed (Angelo's routing chooses which; a transform is typed), so a card is certain.
    narrowed, _raw = client.call("angelo_settings", {"approval": "ASK"})
    step("narrowed_to_ask", narrowed.get("approval") == "ASK", result=narrowed)
    client.call("angelo_send", {"message": SCRIPT_SEND.format(x=2), "wait_seconds": 0})
    state_now = read_until(client, lambda s: isinstance(s.get("approval"), dict) and s["approval"].get("card"), 240)
    approval = state_now.get("approval") or {}
    step("relayed_read_only", (approval.get("script") or approval.get("arguments")) and approval.get("card")
         and "digest" not in json.dumps(approval),
         approval=approval, needs=state_now.get("needs"),
         rows=None if approval else [{k: str(v)[:200] for k, v in row.items()} for row in state_now.get("rows") or ()])
    approve, _raw = client.call("angelo_answer_approval", {"card": approval.get("card", ""), "answer": "approve"})
    step("approve_needs_person", approve.get("code") == "needs_person", result=approve)
    declined, _raw = client.call("angelo_answer_approval", {"card": approval.get("card", ""), "answer": "decline"})
    step("decline", declined.get("declined") is True, result=declined)
    hidden_watch = watch.stop()
    step("no_window_dock_or_focus_while_hidden", hidden_watch["windows_on_screen"] == 0
         and hidden_watch["frontmost"] == 0 and hidden_watch["front_still"], **hidden_watch)
    client.call("angelo_send", {"message": SCRIPT_SEND.format(x=3), "wait_seconds": 0})
    waiting = read_until(client, lambda s: isinstance(s.get("approval"), dict) and s["approval"].get("card"), 240)
    step("second_card_waits", bool((waiting.get("approval") or {}).get("card")), approval=waiting.get("approval"))
    shown, _raw = client.call("angelo_show")
    time.sleep(4)
    shown_type = app_type(pids[0])
    step("shown", shown.get("shown") is True and onscreen_windows(pids[0]) > 0 and "Foreground" in shown_type
         and front_pid() == pids[0], result=shown, app_type=shown_type, shot=screenshot(out, "3-shown-with-card"))
    client.end()
    step("shown_window_outlives_the_launcher", wait_for(lambda: pids[0] in angelo_pids(binary), 5))
    end_all(binary)
    # 6: close saves beside the source.
    client = Client(python, launcher, environ, log, label, state)
    client.call("angelo_open_file", {"file": str(source)})
    client.call("angelo_send", {"message": SEND, "wait_seconds": 120})
    read_until(client, lambda s: s.get("turn") in ("idle", "done", "ended"), 240)
    closed, _raw = client.call("angelo_close_file")
    gone = wait_for(lambda: not angelo_pids(binary), 90)
    outputs = sorted(path.name for path in out.glob("proof.angelo-*.blend"))
    step("close_saves_beside_the_source", gone and outputs and source.read_bytes() == before, closed=closed,
         outputs=outputs)
    client.end()
    # 7: the person's launch hands over.
    client = Client(python, launcher, environ, log, label, state)
    client.call("angelo_open_file", {"file": str(source)})
    hidden = wait_for(lambda: angelo_pids(binary), 30)
    time.sleep(5)
    person = subprocess.Popen([str(binary)], env=environ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ended = wait_for(lambda: person.poll() is not None, 60)
    time.sleep(3)
    step("hand_over_reveals", ended and hidden and hidden[0] in angelo_pids(binary)
         and onscreen_windows(hidden[0]) > 0, second_launch_exit=person.poll(),
         app_type=hidden and app_type(hidden[0]), shot=screenshot(out, "4-revealed-for-the-person"))
    client.end()
    end_all(binary)
    # 7b: a Finder or Dock launch (LaunchServices: ``open -a``) reopens the running hidden app; it comes up.
    client = Client(python, launcher, environ, log, label, state)
    client.call("angelo_open_file", {"file": str(source)})
    hidden = wait_for(lambda: angelo_pids(binary), 30)
    time.sleep(5)
    subprocess.run(["/usr/bin/open", "-a", str(app)], env=environ, check=False, timeout=60)
    up = wait_for(lambda: hidden and onscreen_windows(hidden[0]) > 0 and front_pid() == hidden[0], 30)
    step("finder_launch_reveals", up and angelo_pids(binary) == hidden, pids=angelo_pids(binary),
         app_type=hidden and app_type(hidden[0]), shot=screenshot(out, "5-finder-launch"))
    client.end()
    end_all(binary)
    # 8: a one-minute time cap ends the session with its edits saved.
    for leftover in out.glob("proof.angelo-*.blend"):
        leftover.unlink()
    client = Client(python, launcher, environ, log, label, state)
    client.call("angelo_open_file", {"file": str(source), "caps": {"minutes": 1}})
    client.call("angelo_send", {"message": SEND, "wait_seconds": 50})
    gone = wait_for(lambda: not angelo_pids(binary), 150)
    outputs = sorted(path.name for path in out.glob("proof.angelo-*.blend"))
    step("cap_ends_and_saves", gone and outputs, outputs=outputs)
    client.end()
    end_all(binary)
    report["passed"] = all(entry["ok"] for entry in report["steps"])
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
