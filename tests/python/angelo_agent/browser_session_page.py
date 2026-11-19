# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An app session's live page in a real browser (Chromium, Playwright), with no Blender: it opens and it updates.

This checkout's page is served by the real ``SessionSite`` on Angelo's one local server, beside a real
``PreviewServer`` whose export (a small cube) is the page's 3D scene, and the main thread's part is played by
``session_page_state.build`` on stand-in conversation rows. The page is opened as the person would open the link,
then the session moves on and the page must follow, without a reload:

* while the agent works: "Working", the step it is on, the app's message and the tool rows in the stream, the scene
  drawn from the export, the session clock running;
* a new view and the end of the turn: the picture in Latest views, "Done" and the result;
* a goal's round and score, and an app's text holding markup and script shown as text, running nothing;
* the session ends: the page says its link has ended;
* with WebGL off (``--disable-webgl``), the status, the stream and the result still follow the session, the page
  reads state.json, nothing throws, and the scene's place says it can't be drawn (the 2.0.1 audit, P2-7);
* five scene versions in a row leave as many WebGL buffers alive as the first did: each replaced scene's geometry,
  materials and textures are disposed (the audit, P2-8; buffers counted as the audit's probe counts them).

Every request the page makes is a GET to its own server (it is read-only), and nothing loads from elsewhere.
Screenshots go to ``ANGELO_QA_DIR`` (default: the temporary folder). Run it where Playwright is installed (not part of
the dependency-free host suite)::

    python -m pip install playwright
    python -m playwright install chromium
    python tests/python/angelo_agent/browser_session_page.py

``test_native_session_page`` pins the server, the state and the page's source for the host suite.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_web_preview_drag import cube_glb, png  # noqa: E402  (the drag check's small cube and pictures)
import host_waits  # noqa: E402
from angelo_agent import session_page, session_page_state  # noqa: E402
from angelo_agent.session_page_site import SessionSite  # noqa: E402
from angelo_agent.web_preview_server import PreviewServer  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-session-page-browser"))
WORDMARK = '<svg viewBox="0 0 96 20" role="img" aria-label="Angelo"><text x="0" y="16">Angelo</text></svg>'
VIEW = "3f6c2a1e-6a2b-4b8e-9d1a-0c1d2e3f4a5b"
HOSTILE = '<img src=x onerror="window.__hostile=1"><script>window.__hostile=2</script> {app} {{round}}'


def until(page, expression):
    """Wait until ``expression`` is true in the page, read through DevTools (``page.evaluate``): the page's own policy
    allows no string evaluation, which Playwright's waiting for a function would need."""
    return host_waits.until(lambda: page.evaluate(expression), poll=0.1, what="the page showing " + expression)


def state(messages, turn, goal=None, scene_version=1, started=0.0):
    return session_page_state.build(
        messages, conversation="c1", version=1, turn=turn, goal=goal,
        usage={"tokens": "8.2k tokens", "money": "≈ $0.06", "plan": None, "window": None, "cached": None},
        now=time.monotonic(), started=started, turn_started=started if turn != "idle" else None, app="Codex",
        roots=(("/home/person", "<home>"),), scene_version=scene_version, strings=dict(session_page.PAGE_TEXT))


COUNT_BUFFERS = """
window.__buffers = {created: 0, deleted: 0};
for (const type of [window.WebGL2RenderingContext, window.WebGLRenderingContext]) {
  if (!type) continue;
  for (const [method, key] of [["createBuffer", "created"], ["deleteBuffer", "deleted"]]) {
    const native = type.prototype[method];
    type.prototype[method] = function (...args) { window.__buffers[key] += 1; return native.apply(this, args); };
  }
}
"""


def without_webgl(playwright, site, checks):
    """P2-7: a browser that can't draw 3D still shows the session."""
    browser = playwright.chromium.launch(args=["--disable-webgl"])
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors, reads = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: reads.append(request.url) if request.url.endswith("/state.json") else None)
        page.goto(site.url)
        until(page, "document.getElementById('status').textContent === 'Done'")
        checks["webgl_off_the_status_still_follows_the_session"] = True
        checks["webgl_off_the_page_reads_its_state"] = len(reads) >= 1
        checks["webgl_off_the_result_and_stream_show"] = bool(page.text_content("#result-text")) and bool(
            page.text_content("#rows"))
        checks["webgl_off_nothing_throws"] = errors == []
        hint = page.text_content("#scene-hint") or ""
        checks["webgl_off_the_scene_says_it_cant_be_drawn"] = (
            hint == session_page.PAGE_TEXT["scene_unavailable"] and page.is_visible("#scene-hint"))
        page.screenshot(path=str(OUT / "session-webgl-off.png"))
    finally:
        browser.close()


def scene_versions(playwright, site, preview, messages, checks, started):
    """P2-8: replacing the scene disposes the one it replaces."""
    browser = playwright.chromium.launch()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.add_init_script(COUNT_BUFFERS)
        loads = []
        page.on("response", lambda response: loads.append(1) if response.url.endswith("/scene.glb") else None)
        page.goto(site.url)
        until(page, "document.getElementById('scene-hint').hidden")
        until(page, "!document.getElementById('loading').classList.contains('on')")
        first, live = len(loads), []
        for version in range(2, 7):
            preview.publish(cube_glb(), {"status": "ok"})
            site.publish(state(messages, "idle", scene_version=version, started=started))
            host_waits.until(lambda: len(loads) >= first + version - 1, poll=0.05,
                             what="scene version %d loading" % version)
            until(page, "!document.getElementById('loading').classList.contains('on')")
            page.wait_for_timeout(300)   # window: a frame or two after the swap, as the audit's probe waits.
            counts = page.evaluate("window.__buffers")
            live.append(counts["created"] - counts["deleted"])
        counts = page.evaluate("window.__buffers")
        checks["replaced_scenes_free_their_buffers"] = counts["deleted"] >= 8 and len(set(live)) == 1
        print(json.dumps({"buffers": counts, "alive_after_each_version": live}))
    finally:
        browser.close()


def main():
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    checks, requests = {}, []
    preview = PreviewServer()
    site = SessionSite(marks={"<!--WORDMARK-->": WORDMARK})
    preview.publish(cube_glb(), {"status": "ok"})
    site.scene_feed = preview.scene_bytes
    started = time.monotonic()
    sent = {"role": "user", "text": "Build a small table", "request_kind": "external_task"}
    working = [sent, {"role": "activity", "tool": "angelo_inspect_scene", "status": "completed", "result": {}},
               {"role": "activity", "tool": "angelo_create_cube", "status": "running"}]
    site.publish(state(working, "running", started=started))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("request", lambda request: requests.append((request.method, request.url)))
            page.goto(site.url)
            until(page, "document.getElementById('status').textContent === 'Working'")
            checks["working_is_shown"] = True
            checks["the_step_is_the_running_tool"] = page.text_content("#step-tool") == "angelo_create_cube"
            stream = page.text_content("#rows")
            checks["the_stream_has_the_apps_message_and_the_tools"] = (
                "Build a small table" in stream and "angelo_inspect_scene" in stream and "angelo_create_cube" in stream)
            until(page, "document.getElementById('scene-hint').hidden")
            checks["the_scene_is_drawn_from_the_export"] = True
            first = page.text_content("#v-elapsed")
            page.wait_for_timeout(2100)   # window: the session clock moves once a second; two must pass.
            checks["the_session_clock_runs"] = page.text_content("#v-elapsed") != first
            page.screenshot(path=str(OUT / "session-working.png"))

            site.publish_picture(VIEW, png(320, 200, (40, 160, 170)))
            done = working[:2] + [
                {"role": "activity", "tool": "angelo_create_cube", "status": "completed", "result": {}},
                {"role": "activity", "tool": "angelo_view", "status": "completed", "result": {"views": [{"id": VIEW}]}},
                {"role": "user", "text": HOSTILE, "request_kind": "external_task"},
                {"role": "assistant", "text": "Built a small table from four legs and a top."}]
            goal = {"objective": "A sturdy small table", "status": "active", "pass_at": 0.8, "best": 0.72,
                    "rounds": [{"n": 1, "score": 0.5}, {"n": 2, "score": 0.72}]}
            site.publish(state(done, "idle", goal=goal, started=started))
            until(page, "document.getElementById('status').textContent === 'Done'")
            checks["it_updates_to_done_without_a_reload"] = True
            checks["the_result_is_shown"] = page.text_content("#result-text") == (
                "Built a small table from four legs and a top.")
            until(page, "document.querySelectorAll('#views img').length === 1 && "
                                   "document.querySelector('#views img').complete && "
                                   "document.querySelector('#views img').naturalWidth === 320")
            checks["the_latest_view_is_shown"] = True
            checks["hostile_text_is_shown_as_text_and_runs_nothing"] = (
                HOSTILE in page.text_content("#rows")
                and page.evaluate("document.querySelectorAll('#rows img, #rows script').length") == 0
                and page.evaluate("window.__hostile === undefined"))
            checks["the_goal_round_and_score_are_shown"] = (
                "Round 2" in page.text_content("#goal-facts") and "Score 72%" in page.text_content("#goal-facts"))
            page.screenshot(path=str(OUT / "session-done.png"))

            narrow = browser.new_page(viewport={"width": 390, "height": 844})
            narrow.goto(site.url)
            until(narrow, "document.getElementById('status').textContent === 'Done'")
            box = narrow.locator("#panel").bounding_box()
            checks["a_phone_width_page_fits"] = box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 390
            narrow.screenshot(path=str(OUT / "session-narrow.png"))
            narrow.close()

            without_webgl(playwright, site, checks)
            scene_versions(playwright, site, preview, done, checks, started)

            site.close()
            until(page, "!document.getElementById('ended').hidden")
            checks["the_page_says_its_link_ended"] = True
            page.screenshot(path=str(OUT / "session-ended.png"))
            browser.close()
    finally:
        site.close()
        preview.close()
    own = "http://127.0.0.1:%d/" % preview.port
    checks["read_only_every_request_a_get_to_its_own_server"] = bool(requests) and all(
        method == "GET" and url.startswith(own) for method, url in requests)
    report = {"success": all(checks.values()), "checks": checks, "requests": len(requests), "out": str(OUT)}
    (OUT / "session-page-browser.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
