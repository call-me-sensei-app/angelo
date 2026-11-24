# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The update card's add-on section in the real window (doc/angelo/updates_and_reports.rst, "Your add-ons and the
update"): real default startup (Director), real add-ons of the person's, a real signed feed, real clicks.

Three add-ons are installed into this run's own user folders: a legacy add-on that loads (``bl_info`` minimum only),
an extension in the local ``user_default`` repository whose manifest closes its range at Blender 5.4.0, and a legacy
add-on that raises in ``register``. Nothing leaves the computer: the feed is a loopback server in this process,
signed by a throwaway in-memory key, and the extension repository is local (no listing is asked for).

1. An update of this line on the same Blender (the feed's ``blender`` is the running one) while the two working
   add-ons are enabled: the card says all of them are known to work.
2. The failing add-on is enabled while a start record is held. The runner starts the app with ``--factory-startup``,
   where safe start keeps no record by design, so the fixture calls ``angelo_safe_start.start`` itself, exactly as the
   startup module ``angelo_start_guard`` does on a normal launch: safe start collects the failure and, once the start
   completes, records which add-ons loaded (``addon-loads.json``).
3. A newer line built on Blender 5.4.0: the card names all three as not yet known to work, most pressing first, and
   says what safe start does; **Show add-ons** (a real click) opens one reason each; a second click closes it.

Run through ``run_native_suites.py --gui --include test_blender_update_addons``; check ``update-addons.json`` for
``success`` and ``phase == "complete"``, and look at its screenshots.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import time
import traceback
from pathlib import Path

import bpy

import update_crash_gui as gui  # noqa: E402
import update_fixtures as ufx  # noqa: E402
from loopback_server import LoopbackServer  # noqa: E402
from update_crash_gui import active, capture, card, park_pointer, settle, until, window  # noqa: E402
from angelo_agent import product_env  # noqa: E402
import qa_boot  # noqa: E402
import qa_hit  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')        # The default Director startup...
os.environ["ANGELO_ENABLE_APP_NOTICES"] = "1"    # ...and the notices the runner turns off for every other suite.
os.environ["ANGELO_NOTICES_DELAY"] = "600"
os.environ["ANGELO_UPDATE_CHECK_DELAY"] = "600"
bpy.context.preferences.use_preferences_save = False
gui.OUT.mkdir(parents=True, exist_ok=True)
REPORT = gui.OUT / "update-addons.json"
state = {"phase": "early", "success": False, "checks": {}, "screenshots": gui.shots,
         "fixture": "Loopback signed feed, throwaway key, three add-ons written into this run's user folders; "
                    "no network, account or inference"}
started = time.monotonic()
server, links = LoopbackServer(), []
SEED, KEYS = ufx.new_key()
feed = {"body": None, "sequence": 3}

PLAIN = ('bl_info = {"name": "Plain Tool", "version": (1, 0, 0), "blender": (4, 2, 0), "category": "Object"}\n'
         'def register():\n    pass\n\ndef unregister():\n    pass\n')
BROKEN = ('bl_info = {"name": "Broken Tool", "version": (2, 0, 0), "blender": (4, 2, 0), "category": "Object"}\n'
          'def register():\n    raise AttributeError("bpy_struct: attribute \\"gone\\" from \\"Scene\\" is read-only")\n\n'
          'def unregister():\n    pass\n')
MANIFEST = ('schema_version = "1.0.0"\nid = "range_tool"\nversion = "3.1.0"\nname = "Range Tool"\n'
            'tagline = "A tool whose author closed its range"\nmaintainer = "Fixture"\ntype = "add-on"\n'
            'blender_version_min = "4.2.0"\nblender_version_max = "5.4.0"\nlicense = ["SPDX:GPL-3.0-or-later"]\n')


def set_feed(version, blender):
    feed["sequence"] += 1
    document = ufx.payload(version, sequence=feed["sequence"])
    document["latest"]["blender"] = blender
    feed["body"] = ufx.envelope(document, SEED)


def install_addons():
    addons = Path(bpy.utils.user_resource('SCRIPTS', path="addons", create=True))
    (addons / "plain_tool.py").write_text(PLAIN, encoding="utf-8")
    (addons / "broken_tool.py").write_text(BROKEN, encoding="utf-8")
    repo = next(r for r in bpy.context.preferences.extensions.repos if r.module == "user_default")
    package = Path(repo.directory) / "range_tool"
    package.mkdir(parents=True, exist_ok=True)
    (package / "blender_manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (package / "__init__.py").write_text("def register():\n    pass\n\ndef unregister():\n    pass\n", encoding="utf-8")
    bpy.utils.refresh_script_paths()   # The user add-on folder did not exist at start: put it on Python's path.
    import addon_utils
    addon_utils.modules_refresh()
    state["installed"] = {"addons": str(addons.name), "repo": repo.module}


def prepare():
    from angelo_agent import update_feed
    from angelo_agent import app_version
    major, minor, patch = app_version.parse(app_version.current())
    state["versions"] = {"current": app_version.current(), "patch": "%d.%d.%d" % (major, minor, patch + 1),
                         "line": "%d.%d.0" % (major, minor + 1), "blender": ".".join(map(str, bpy.app.version))}
    set_feed(state["versions"]["patch"], state["versions"]["blender"])
    server.routes["/updates/v1/stable.json"] = lambda request: (200, {"ETag": '"f%d"' % feed["sequence"]}, feed["body"])
    server.start()
    os.environ["ANGELO_UPDATE_FEED_URL"] = server.base + "/updates/v1/stable.json"
    update_feed.load_keys = lambda path=update_feed.KEYS_FILE: dict(KEYS)
    install_addons()
    os.environ["ANGELO_NOTICES_DELAY"] = "0"        # The feed is planted: notices and checks may run now.
    os.environ["ANGELO_UPDATE_CHECK_DELAY"] = "0"


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
        state["failed_checks"] = [name for name, value in state["checks"].items() if not value]
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    state.pop("scenario", None)
    save()
    server.stop()
    print("ANGELO_UPDATE_ADDONS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 600, "The fixture timed out in " + state["phase"]
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


def offering(version):
    """The update card on screen offers ``version``: the answer to the check just made, not an earlier card that
    came back from the queue for a moment (update_ui.show_notice replaces that one)."""
    shown = card() if active("update") else None
    return shown is not None and shown.offer.release.version == version


def show_update(version):
    """Check for Updates…, as the person does, and wait for the update card offering ``version`` (the feed's)."""
    from angelo_agent import card_host
    if card_host.is_active():
        card_host.close("fixture")
    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.check_for_updates()
    yield from until("the update card for " + version, lambda: offering(version), limit=60)
    park_pointer()
    yield from settle(3)


def disclosure_rect(label):
    _, region, st = gui.card_region()
    return qa_hit.rect(qa_hit.PIXELS, region, next(op[1] for op in st.plan.ops
                                                    if op[0] == "disclosure" and op[2] == label))


def scenario(checks):
    import addon_utils
    import angelo_safe_start
    import angelo_addon_loads
    from angelo_agent import director, update_card
    yield from until("the Director layout", lambda: director.is_director(window()) and qa_boot.started())
    update_card.open_link = links.append
    checks["director_mode_is_the_view"] = director.is_director(window())

    # 1. Same Blender, two working add-ons.
    addon_utils.enable("plain_tool", default_set=True)
    addon_utils.enable("bl_ext.user_default.range_tool", default_set=True)
    checks["the_working_addons_loaded"] = (addon_utils.check("plain_tool")[1]
                                           and addon_utils.check("bl_ext.user_default.range_tool")[1])
    yield from show_update(state["versions"]["patch"])
    capture("addons-1-all-known.png")
    text = gui.text()
    state["text_all_known"] = text
    checks["same_series_says_all_known"] = "All 2 of your add-ons are known to work on Angelo" in text
    checks["same_series_has_no_list"] = not [op for op in gui.host()._active.plan.ops if op[0] == "disclosure"]

    # 2. The failing add-on, enabled as a preference read enables add-ons.
    angelo_safe_start.start(bpy)   # What angelo_start_guard.register does on a launch without --factory-startup.
    checks["safe_start_holds_a_record"] = angelo_safe_start._state["held"] is not None
    # As a start enables what the preferences list (addon_utils.reset_all): listed first, then enabled without
    # default_set, so a failure leaves it listed (default_set=True would remove a failing add-on from the list).
    addon_utils._addon_ensure("broken_tool")
    errors = []   # Kept here rather than printed: the runner fails any suite whose log holds a traceback.
    addon_utils.enable("broken_tool", default_set=False, handle_error=errors.append)
    state["broken_tool_error"] = [repr(e) for e in errors]
    yield from until("safe start settling", lambda: angelo_safe_start._state["held"] is None, limit=30)
    yield from settle(3)
    checks["the_failure_is_kept_with_its_cause"] = "broken_tool" in angelo_safe_start.failed_now()
    record = Path(bpy.utils.user_resource('CONFIG')) / angelo_addon_loads.FOLDER / angelo_addon_loads.FILE
    series, loaded = angelo_addon_loads.read(record)
    state["load_record"] = sorted(loaded)
    checks["the_load_record_lists_what_loaded"] = (series == tuple(bpy.app.version[:2])
                                                   and ("plain_tool", "1.0.0") in loaded
                                                   and ("bl_ext.user_default.range_tool", "3.1.0") in loaded
                                                   and not any(m == "broken_tool" for m, _ in loaded))

    # 3. A newer line on Blender 5.4.0.
    set_feed(state["versions"]["line"], "5.4.0")
    yield from show_update(state["versions"]["line"])
    capture("addons-2-not-yet-known.png")
    text = gui.text()
    state["text_not_known"] = text
    checks["names_them_most_pressing_first"] = "Broken Tool, Range Tool, Plain Tool" in text
    checks["says_what_safe_start_does"] = "the next start opens without your add-ons" in text
    show = disclosure_rect("Show add-ons")
    gui.click_at(*gui.center(show))
    yield from settle(3)
    park_pointer()
    yield from settle(2)
    capture("addons-3-reasons.png")
    text = gui.text()
    state["text_reasons"] = text
    checks["the_click_opened_the_list"] = card().addons_open
    checks["failing_reason_with_its_cause"] = "Fails to load now: AttributeError" in text
    checks["closed_range_reason"] = "Made for Blender versions before 5.4.0" in text
    checks["open_range_reason"] = "Its author has not said it works with Blender 5.4.0 yet" in text
    hide = disclosure_rect("Hide add-ons")
    gui.click_at(*gui.center(hide))
    yield from settle(3)
    checks["a_second_click_closes_it"] = not card().addons_open
    capture("addons-4-closed-again.png")
    addon_utils.disable("broken_tool", default_set=True)


if __name__ == "__main__":
    bpy.app.timers.register(tick, first_interval=1.0)
