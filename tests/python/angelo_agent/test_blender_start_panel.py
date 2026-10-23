# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Returning user: Angelo's start panel instead of Blender's New File splash page.

The profile is made a returning one before Angelo's startup decides (this script runs first): preferences
saved, the welcome already seen, three saved scenes in Blender's recent-files list (two with Blender's embedded
preview, one without). Covered: after Director
settles, the start panel opens by itself, once; Blender's splash is never reopened, and neither the setup card
nor the welcome shows; Help > Splash Screen opens it again instead of Blender's page; it offers each recent scene
as a tile, New scene, Open… and What's new, and no
templates, Recover Last Session or Donate; tiles show the saved thumbnails and, when known, the scene's last
chat title; a tile opens its scene; in each of Angelo's other 20 languages the open panel draws every one of
its strings as the catalogue translates it (read from the drawn plan, so a layout kept from another language
fails); English, Arabic and a narrow view are captured. No network, account or inference.

Run through ``run_native_suites.py --gui --include test_blender_start_panel``; check ``start-panel.json``.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-start-panel-qa"))
OUT.mkdir(parents=True, exist_ok=True)
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
product_env.clear('DISABLE_INTRO')
REPORT = OUT / "start-panel.json"
SCENES = [OUT / "scenes" / "Stone bridge.blend", OUT / "scenes" / "Brushed gold ring.blend",
          OUT / "scenes" / "Quick sketch.blend"]
CHAT_TITLE = "Build a stone bridge over a river"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": []}
started = time.monotonic()


def make_returning_profile():
    """Saved preferences, a seen welcome and three recent scenes, before Angelo's startup reads them.

    The bridge and the ring are saved with Blender's own embedded preview (a camera render of each scene, so the two
    pictures differ); the sketch is saved with previews turned off, so only its tile shows the placeholder.
    """
    from angelo_agent import intro
    prefs = bpy.context.preferences
    prefs.use_preferences_save = False
    bpy.ops.wm.save_userpref()
    intro.remember("DIRECTOR")
    SCENES[0].parent.mkdir(parents=True, exist_ok=True)
    cube, material = bpy.data.objects["Cube"], bpy.data.materials["Material"]
    looks = (((3.2, 0.8, 0.25), (0, 0, 0), (0.55, 0.50, 0.42, 1)),       # A long stone slab.
             ((1, 1, 1), (0.6, 0, 0.785), (0.95, 0.65, 0.12, 1)))      # A tilted gold block.
    scale, rotation, color = tuple(cube.scale), tuple(cube.rotation_euler), tuple(material.diffuse_color)
    for path, (new_scale, new_rotation, new_color) in zip(SCENES, looks):
        cube.scale, cube.rotation_euler, material.diffuse_color = new_scale, new_rotation, new_color
        bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    cube.scale, cube.rotation_euler, material.diffuse_color = scale, rotation, color
    preview = prefs.filepaths.file_preview_type
    prefs.filepaths.file_preview_type = 'NONE'
    bpy.ops.wm.save_as_mainfile(filepath=str(SCENES[2]), copy=True)
    prefs.filepaths.file_preview_type = preview
    day = 86400
    for path, age in zip(SCENES, (1, 3, 0)):   # Yesterday, three days ago, today.
        os.utime(path, (time.time() - age * day, time.time() - age * day))
    history = Path(bpy.utils.user_resource('CONFIG')) / "recent-files.txt"
    history.write_text("".join(str(p) + "\n" for p in (SCENES[2], SCENES[1], SCENES[0])), encoding="utf-8")
    # The bridge was last saved with a chat open (splash_setup records that on save); the others were not.
    (intro.state_path().parent / "scene-chats.json").write_text(json.dumps({str(SCENES[0]): CHAT_TITLE}), encoding="utf-8")


make_returning_profile()


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def card_actions():
    from angelo_agent import card_host
    return [(a, v) for _, a, v in card_host._active.plan.hits] if card_host._active and card_host._active.plan else []


def click_card(action, value=None):
    from angelo_agent import card_host, canvas_rtl
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, v in reversed(active.plan.hits) if a == action and (value is None or v == value))
    x = canvas_rtl.pointer_x(rect[0] + rect[2] / 2, region.width)
    x, y = int(region.x + x), int(region.y + rect[1] + rect[3] / 2)
    for kind, event_value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=event_value, x=x, y=y)


def other_languages():
    """Blender's identifiers for Angelo's interface languages other than English."""
    from angelo_agent import setup_card
    identifiers = [i.identifier for i in bpy.context.preferences.view.bl_rna.properties["language"].enum_items]
    found = (setup_card.resolve(code, identifiers) for code, _ in setup_card.LANGUAGES if code != "en_US")
    return [identifier for identifier in found if identifier]


def untranslated():
    """The start panel's strings whose catalogue translation is missing or not what the open panel drew."""
    from angelo_agent import card_host, start_card
    from angelo_agent.ui_text import iface_
    lines = []
    for op in card_host._active.plan.ops:
        if op[0] in {"text", "mono"}:
            lines.append(str(op[1]).strip())
        elif op[0] == "button":
            lines.append(str(op[2]).strip())
        elif op[0] == "kit" and op[1] == "firstlaunch" and getattr(op[3]["painter"], "__name__", "") == "tile":
            lines.extend(str(line[0]).strip() for line in op[3]["args"][1])
    joined = "".join("".join(line.split()) for line in lines)
    words = {word for line in lines for word in line.split()}
    missing = []
    for message in (start_card.TITLE, start_card.SUBTITLE, start_card.RECENT, start_card.NEW, start_card.OPEN,
                    start_card.WHATS_NEW, "Edited today"):
        expected = iface_(message).strip()
        drawn = (expected in lines or "".join(expected.split()) in joined
                 or (len(expected.split()) > 1 and set(expected.split()) <= words))   # A wrapped paragraph.
        if expected == message or not drawn:
            missing.append(message)
    return missing


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_START_PANEL", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=30):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + phase
    return False


def tick():
    try:
        assert time.monotonic() - started < 180, "Start-panel fixture timed out in " + state["phase"]
        from angelo_agent import card_host, director, intro, splash_setup, firstlaunch_controls, ui
        checks, phase = state["checks"], state["phase"]
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not wait(phase, lambda: card_host.is_active("start") and card_host._active.plan is not None):
                return .2
            state["phase"] = "start"
            return .5
        if phase == "start":
            capture("start-panel-0.png")
            shown = card_actions()
            state["sequence"] = list(splash_setup.sequence)
            checks["start_panel_opens_once_without_setup_or_welcome"] = (
                [step for step in splash_setup.sequence if step != "splash"] == ["start"]
                and intro.opened == 0 and splash_setup.reopened == 0)
            checks["offers_recent_scenes_new_open_and_whats_new"] = (
                [v for a, v in shown if a == "scene"] == [0, 1, 2] and ("new", None) in shown
                and ("open", None) in shown and ("whats_new", None) in shown)
            card = card_host.active_card()
            checks["recent_scenes_newest_first"] = card.scenes == [str(SCENES[2]), str(SCENES[1]), str(SCENES[0])]
            checks["tile_shows_the_scenes_last_chat_title_when_known"] = (
                card.titles.get(str(SCENES[0])) == CHAT_TITLE and str(SCENES[1]) not in card.titles)
            from angelo_agent import start_card
            lines = {p: start_card.tile_lines(p, card.titles.get(p), card.mtimes.get(p))[1][0] for p in card.scenes}
            state["tile_subtitles"] = lines
            checks["untitled_tiles_say_when_they_were_edited"] = (lines[str(SCENES[1])] == "Edited 3 days ago"
                                                                  and lines[str(SCENES[2])] == "Edited today")
            thumbs = {p: firstlaunch_controls._thumbs.get(p, {}) for p in card.scenes}
            state["thumbnails"] = {p: t.get("size") for p, t in thumbs.items()}
            pixels = {p: t.get("pixels") for p, t in thumbs.items()}
            checks["tiles_show_each_files_embedded_preview"] = (
                pixels[str(SCENES[0])] is not None and pixels[str(SCENES[1])] is not None
                and pixels[str(SCENES[0])] != pixels[str(SCENES[1])])
            checks["placeholder_only_without_a_preview"] = pixels[str(SCENES[2])] is None
            checks["no_templates_recover_or_donate"] = not any(a in {"template", "recover", "donate"} for a, _ in shown)
            state["phase"] = "languages"
            return .1
        if phase == "languages":
            # Every language: the open panel's drawn text is the catalogue's translation of each of its strings.
            queue, shown = state.setdefault("language_queue", other_languages()), state.get("language_shown")
            if shown is not None:
                with bpy.context.temp_override(window=window()):
                    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
                try:
                    missing = untranslated()
                except Exception as error:   # Recorded as a miss; the rest of the run still reports.
                    missing = [repr(error)]
                state.setdefault("untranslated_by_language", {})[shown] = missing
            if queue:
                state["language_shown"] = queue.pop(0)
                bpy.context.preferences.view.language = state["language_shown"]
                return .3
            missing = state.get("untranslated_by_language", {})
            checks["start_panel_text_is_the_catalogues_in_every_language"] = (
                len(missing) >= 15 and not any(missing.values()))
            bpy.context.preferences.view.language = next(
                i.identifier for i in bpy.context.preferences.view.bl_rna.properties["language"].enum_items
                if i.identifier.startswith("ar"))
            state["phase"] = "arabic"
            return .6
        if phase == "arabic":
            capture("start-panel-0-ar.png")
            bpy.context.preferences.view.language = "en_US"
            bpy.context.preferences.view.ui_scale = 2.0
            state["phase"] = "narrow"
            return .6
        if phase == "narrow":
            capture("start-panel-0-narrow.png")
            plan = card_host._active.plan
            area = next(a for a in window().screen.areas if a.as_pointer() == card_host._active.area)
            region = next(r for r in area.regions if r.type == 'WINDOW')
            checks["narrow_card_fits_its_view"] = (plan.card[0] >= 0 and plan.card[0] + plan.card[2] <= region.width
                                                   and plan.card[1] >= 0 and plan.card[1] + plan.card[3] <= region.height)
            bpy.context.preferences.view.ui_scale = 1.0
            click_card("new")
            state["phase"] = "help_splash"
            return .6
        if phase == "help_splash":
            if not wait(phase, lambda: not card_host.is_active("start")):
                return .2
            # Help > Splash Screen runs this operator: Blender's splash must give way to the start panel.
            with bpy.context.temp_override(window=window()):
                bpy.ops.wm.splash('INVOKE_DEFAULT')
            state["phase"] = "help_start"
            return .3
        if phase == "help_start":
            if not wait(phase, lambda: card_host.is_active("start") and card_host._active.plan is not None):
                return .2
            capture("start-panel-1-from-help.png")
            checks["help_splash_opens_the_start_panel_not_blenders_page"] = (
                [step for step in splash_setup.sequence if step != "splash"] == ["start", "start"]
                and not splash_setup._redirecting)
            state["phase"] = "open_tile"
            return .4
        if phase == "open_tile":
            click_card("scene", 1)
            state["phase"] = "opened"
            return .5
        if phase == "opened":
            if not wait(phase, lambda: bpy.data.filepath == str(SCENES[1])):
                return .2
            checks["tile_opens_its_scene"] = not card_host.is_active("start")
            state["phase"] = "settled"
            return 1.0
        if phase == "settled":
            checks["start_panel_not_shown_again_after_opening"] = (
                [step for step in splash_setup.sequence if step != "splash"] == ["start", "start"]
                and not card_host.is_active("start"))
            finish()
            return None
        return .2
    except Exception as error:  # noqa: BLE001 - every failure is reported, then the app quits.
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=.05, persistent=True)  # The fixture opens a scene.
