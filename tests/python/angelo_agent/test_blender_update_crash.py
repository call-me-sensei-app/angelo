# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Update notice, crash report and What's New in the real window: the cards, their states and what leaves the app.

Real default startup (Director layout), real mouse and keyboard events (``Window.event_simulate``) on the
cards' own hit geometry, screenshots, and pixel checks of every control state: rest, hover, pressed,
keyboard focus and disabled. Nothing leaves the computer: the update feed and the crash endpoint are a
loopback server in this process; the feed is signed by a throwaway in-memory key that replaces the shipped
(empty) key list for this process only; crash logs are synthetic files (``crash_fixtures.MAC``, with planted
canaries) written to the suite's own temporary folder; opening a link is recorded, not done.

In the order the app shows them:

1. The "Angelo quit unexpectedly" dialog as 1.0 ships it, with no crash endpoint announced: Send report is
   disabled (no hover, no press, no focus, no request), Save report… writes exactly the report through the
   real File Browser, Report on the website opens the report page without the stack.
2. What's New (the saved state says 0.9.0 was last seen), then the update notice found by the signed feed,
   in a corner, with the viewport still usable; Later, the status-bar item, Skip.
3. "Check for Updates…" results: up to date, offline, turned off by the machine policy.
4. The dialog once an endpoint exists: disclosure of the exact report, scrolling, keyboard focus, Remember
   my choice and Send, with the bytes received compared to the bytes shown.
5. The next crash with "always send": the report goes without a dialog, a notice says so, and its review
   shows exactly what was sent. ``DisableCrashReporting`` then shows and sends nothing.
6. Preferences > System > Updates and Reports, and the light theme.

Run through ``run_native_suites.py --gui --include test_blender_update_crash``; check ``update-crash.json``
for ``success`` and ``phase == "complete"``. Look at the screenshots it lists.
"""

import json
import os
import socket
import sys
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crash_fixtures as cfx  # noqa: E402
import update_crash_gui as gui  # noqa: E402
import update_fixtures as ufx  # noqa: E402
from loopback_server import LoopbackServer  # noqa: E402
from update_crash_gui import until, window  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')        # This suite needs the default Director startup...
os.environ["ANGELO_ENABLE_APP_NOTICES"] = "1"    # ...and the notices the runner turns off for every other suite.
os.environ["ANGELO_NOTICES_DELAY"] = "600"       # Nothing starts before prepare() has planted the crash and the feed.
os.environ["ANGELO_UPDATE_CHECK_DELAY"] = "600"
bpy.context.preferences.use_preferences_save = False
gui.OUT.mkdir(parents=True, exist_ok=True)
REPORT = gui.OUT / "update-crash.json"
state = {"phase": "early", "success": False, "checks": {}, "screenshots": gui.shots,
         "fixture": "Loopback feed and crash endpoint, throwaway signing key, synthetic crash logs, synthetic input; "
                    "no network, account, browser or inference"}
started = time.monotonic()
opened_links, server = [], LoopbackServer()
SEED, KEYS = ufx.new_key()
feed = {"body": None, "sequence": 3}
reports = []


def set_feed(version="1.1.0", **changes):
    feed["sequence"] += 1
    feed["body"] = ufx.envelope(ufx.payload(version, sequence=feed["sequence"], **changes), SEED)


def crash_answer(request):
    document = json.loads(request.body)
    reports.append(document)
    return 202, {"Content-Type": "application/json"}, json.dumps({"id": document["report_id"]}).encode()   # As the service answers.


def plant_crash(age):
    """A crash log of Angelo's in the temporary folder, ``age`` seconds old (each call is a new crash)."""
    from angelo_agent import crash_ui
    text = cfx.MAC.replace("/Applications/Angelo.app/Contents/Resources/5.2", bpy.utils.resource_path('LOCAL'))
    log = Path(crash_ui.temp_base()) / "angelo.crash.txt"
    log.write_text(text + "\n" * int(age % 7), encoding="utf-8")   # A different size and time: a different crash.
    stamp = time.time() - age
    os.utime(log, (stamp, stamp))
    state["crash_log"] = str(log)
    return log


def closed_port():
    """A loopback address nothing listens on (the offline check)."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return "http://127.0.0.1:%d/updates/v1/stable.json" % port


def prepare():
    from angelo_agent import update_feed, update_state
    from angelo_agent.ui import state_directory
    set_feed()
    server.routes["/updates/v1/stable.json"] = lambda request: (200, {"ETag": '"f%d"' % feed["sequence"]}, feed["body"])
    server.routes["/crash"] = crash_answer
    server.start()
    os.environ["ANGELO_UPDATE_FEED_URL"] = server.base + "/updates/v1/stable.json"
    os.environ.pop("ANGELO_CRASH_REPORT_URL", None)   # As shipped: no endpoint until a feed announces one.
    update_feed.load_keys = lambda path=update_feed.KEYS_FILE: dict(KEYS)   # This process trusts the throwaway key only.
    plant_crash(90)
    update_state.save(state_directory(), update_state.UpdateState(last_seen_version="0.9.0"))
    os.environ["ANGELO_NOTICES_DELAY"] = "0"
    os.environ["ANGELO_UPDATE_CHECK_DELAY"] = "0"


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    Path(state.get("crash_log", "")).unlink(missing_ok=True)   # The runner reads any *.crash.txt as a real crash.
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        try:   # What the app thought it was doing when the fixture gave up.
            from angelo_agent import app_notices, card_host
            state["app_state"] = {"steps": app_notices.steps, "history": card_host.history,
                                  "queued": [c.kind for c in card_host.queued()],
                                  "active": card_host.active_card() and card_host.active_card().kind,
                                  "modal_operators": [op.bl_idname for op in window().modal_operators]}
        except Exception as inner:
            state["app_state"] = repr(inner)
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
        state["failed_checks"] = [name for name, value in state["checks"].items() if not value]
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    state.pop("scenario", None)
    save()
    server.stop()
    print("ANGELO_UPDATE_CRASH", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 400, "The fixture timed out in " + state["phase"]
        if state["phase"] == "early":
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=window().width // 2, y=window().height // 2)
            prepare()
            state["phase"] = "run"
            state["scenario"] = scenario(state["checks"])
            return .5
        try:
            next(state["scenario"])
        except StopIteration:
            finish()
            return None
        return .2
    except Exception as error:
        finish(error)
        return None


def scenario(checks):
    from types import SimpleNamespace
    from angelo_agent import app_notices, director, ui
    yield from until("the Director layout", lambda: director.is_director(window()) and director._timer is None
                     and ui._layout_timer is None)
    from angelo_agent import update_card
    update_card.open_link = opened_links.append   # Opening a link is recorded, never done.
    state["window"] = [window().width, window().height]
    checks["notices_are_registered"] = app_notices._started is not None
    fixture = SimpleNamespace(checks=checks, state=state, links=opened_links, server=server, reports=reports,
                              set_feed=set_feed, plant_crash=plant_crash, closed_port=closed_port)
    for scene in (scenes.crash_without_endpoint, scenes.whats_new, scenes.update_notice, scenes.manual_checks,
                  scenes.crash_with_endpoint, scenes.automatic_send_and_policy, scenes.preferences_and_themes):
        state["scene"] = scene.__name__
        yield from scene(fixture)


import update_crash_scenes as scenes  # noqa: E402

if __name__ == "__main__":
    bpy.app.timers.register(tick, first_interval=1.0)
