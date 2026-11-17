# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The in-app half of ``update_carry_over_gate.py``: run inside the NEWER app, one window process per phase.

``update`` is the first launch after the update: the earlier version's chats, snapshots, attachments, briefs and
preferences are here and usable, the setup card and Blender's Quick Setup never showed, one notice said the update
is ready (captured), and each saved account's native sign-in resolves to the profile it had. ``restart`` is the next
launch: nothing moves again, no notice shows, everything is still here. Checks are recorded, never asserted one by
one; ``<phase>.json`` lists every one (and the sign-in commands the host runs with the person's own home).
"""

import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))   # Its siblings (start_record_checks), run inside the app.

ARGS = sys.argv[sys.argv.index("--") + 1:]
PHASE = ARGS[ARGS.index("--native-phase") + 1]
OUT = Path(ARGS[ARGS.index("--fixture-root") + 1])
SEED = json.loads((OUT / "seed.json").read_text())
report = {"phase": PHASE, "checks": {}, "screenshots": [], "success": False}


class Until:
    def __init__(self, condition, what, limit=90):
        self.condition, self.what, self.limit = condition, what, limit


def check(name, condition):
    report["checks"][name] = bool(condition)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    report["screenshots"].append(str(path))


def check_everything_is_here(prefix):
    from angelo_agent import console, ui
    from angelo_agent.checkpoints import CheckpointStore
    from angelo_agent.references import ReferenceStore
    from angelo_agent.sessions import SessionCollection, SessionStore
    state = Path(ui.state_directory())
    report[prefix + "state"] = str(state)
    check(prefix + "agent_enabled_with_its_chat", addon_utils.check("angelo_agent") == (True, True)
          and any(console.is_chat(a) for a in bpy.context.window_manager.windows[0].screen.areas))
    listed = SessionCollection(state).records
    for chat in SEED["chats"]:
        loaded = SessionStore(state, chat["id"]).load()
        name = prefix + "chat_%s_" % chat["id"][:8]
        check(name + "listed", chat["id"] in listed)
        check(name + "history_kept", len(loaded["messages"]) >= chat["messages"]
              and loaded["messages"][0].get("text", "").startswith("Make the monkey taller"))
        check(name + "brief_kept", loaded["task_memory"]["brief"]["objective"] == chat["brief"])
        if prefix == "":
            check(name + "fresh_native_context", loaded["native_id"] is None)   # Started in the old working folder.
    first = SessionStore(state, SEED["snapshot"]["chat"]).load()
    reference = first["messages"][0]["references"][0]
    picture = ReferenceStore(state, SEED["snapshot"]["chat"]).checked_path(reference).read_bytes()
    check(prefix + "attachment_opens", hashlib.sha256(picture).hexdigest() == SEED["attachment_sha256"])
    snapshots = CheckpointStore(state, SEED["snapshot"]["chat"])
    _record, copy = snapshots.recovery_copy(SEED["snapshot"]["id"])
    check(prefix + "snapshot_restores_byte_for_byte",
          hashlib.sha256(copy.read_bytes()).hexdigest() == SEED["snapshot"]["sha256"])
    targets = [str(target) for target, _backup, _entry in snapshots.restore_plan(SEED["snapshot"]["id"])]
    check(prefix + "file_journal_follows_the_workspace",
          [str(Path(target).resolve()) for target in targets] == [str((state / "workspace/notes.md").resolve())])
    check(prefix + "snapshot_store_sound", snapshots.inspect()["issues"] == [])
    prefs = bpy.context.preferences
    for group, name, value in SEED["preferences"]:
        check(prefix + "preference_" + name, getattr(getattr(prefs, group), name) == value)
    config = Path(bpy.utils.user_resource('CONFIG'))
    check(prefix + "preferences_saved_in_this_version", (config / "userpref.blend").is_file())
    recent = (config / "recent-files.txt").read_text(errors="replace") if (config / "recent-files.txt").exists() else ""
    check(prefix + "recent_scene_listed", SEED["scene"] in recent)


def sign_ins():
    """The native sign-in status command of every saved coding account, as Angelo would run it."""
    from angelo_agent import authentication, ui
    commands = []
    for record in ui.connection_records().values():
        if record["mode"] not in {"claude_subscription", "codex_subscription", "codex_api"}:
            continue
        settings = ui.connection_settings(record["mode"], record=record)
        command, env = authentication.native_auth_command(record["mode"], settings, ui.state_directory())
        commands.append({"id": record["id"], "mode": record["mode"], "command": command,
                         "profile_env": {key: env[key] for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME") if key in env}})
    return commands


def chat_shown():
    from angelo_agent import console
    return any(console.is_chat(area) for area in bpy.context.window_manager.windows[0].screen.areas)


def phase_update():
    from angelo_agent import card_host, splash_setup, version_carry_over, version_carry_over_ui as update
    yield Until(lambda: update.shown is not None and card_host.is_active("notice"), "the update notice")
    began = time.monotonic()
    yield Until(lambda: chat_shown() or time.monotonic() - began > 30, "the settled layout", limit=40)
    yield 2.0
    check("the_notice_is_on_screen_in_the_settled_layout", card_host.is_active("notice"))
    capture("update-settled.png")
    screen = bpy.context.window_manager.windows[0].screen
    report["layout"] = {"screen": screen.name, "areas": [area.type for area in screen.areas],
                        "workspace": bpy.context.window_manager.windows[0].workspace.name}
    result = version_carry_over.last_result
    report["carry_over"] = {"status": result.status, "message": result.message}
    check("state_moved", result.status == "migrated")
    check("settings_imported", update.last_outcome is not None and not update.last_outcome.get("exception"))
    check("no_setup_card_and_no_quick_setup", "setup" not in splash_setup.sequence)
    check("notice_says_the_update_is_ready", "is ready" in update.shown[1])
    check("notice_says_chats_snapshots_and_settings_came_over", update.shown[2].startswith(update.READY_ALL))
    capture("update-notice.png")
    record = json.loads((Path(bpy.utils.user_resource('CONFIG')) / "angelo-carry-over.json").read_text())
    check("record_done", record["status"] == "done" and Path(record["from"]).resolve() == Path(SEED["state"]).resolve())
    check("earlier_state_folder_gave_up_its_chats", not (Path(SEED["state"]) / "sessions").exists())
    check("earlier_state_folder_says_where_it_went", (Path(SEED["state"]) / "README-Angelo.txt").is_file())
    check_everything_is_here("")
    report["sign_ins"] = sign_ins()


def phase_restart():
    from angelo_agent import version_carry_over, version_carry_over_ui as update
    yield Until(lambda: version_carry_over.last_result is not None, "the add-on")
    yield 8.0   # Past the first-launch decision: a notice would have shown by now.
    check("nothing_moved_again", version_carry_over.last_result.status == "nothing")
    check("no_notice_again", update.shown is None)
    check_everything_is_here("restart_")
    capture("restart.png")


def crashing_addon_off():
    module = SEED["crashing_addon"]
    return module not in bpy.context.preferences.addons and not addon_utils.check(module)[1]


def start_records():
    """This launch's start records, judged (``start_record_checks``): none of its own left, any other marked found."""
    import angelo_safe_start
    import start_record_checks
    start_record_checks.record(check, report, Path(bpy.utils.user_resource('CONFIG')) / angelo_safe_start.FOLDER)


def phase_crash():
    yield Until(lambda: False, "the native crash (this launch must not get here)", limit=200)


def phase_safe():
    import angelo_safe_start
    from angelo_agent import card_host, safe_start_notice as notice
    yield Until(lambda: notice.shown is not None, "the safe-start notice", limit=200)
    yield 1.0
    report["cards_before_the_notice"] = []

    def notice_on_screen():
        """Cards ahead of it (the start panel, the crash report of the launch that crashed) are closed as a person
        would close them; each is recorded."""
        if card_host.is_active("safe_start"):
            return True
        card = card_host.active_card()
        if card is not None:
            report["cards_before_the_notice"].append(card.kind)
            capture("safe-before-%d-%s.png" % (len(report["cards_before_the_notice"]), card.kind))
            card_host.close("close")
        return False
    yield Until(notice_on_screen, "the notice on screen", limit=60)
    yield 1.0
    check("the_crashing_addon_was_left_off", angelo_safe_start.left_off() == [SEED["crashing_addon"]])
    check("the_crashing_addon_is_off", crashing_addon_off())
    check("the_notice_names_it", "Crash On Load" in notice.shown[1])
    check("the_notice_says_how_to_turn_it_back_on", "Preferences" in notice.shown[1])
    check("the_notice_is_on_screen", card_host.is_active("safe_start"))
    check("angelo_opened_with_its_chat", addon_utils.check("angelo_agent") == (True, True) and chat_shown())
    prefs = bpy.context.preferences
    for group, name, value in SEED["preferences"]:
        check("preference_" + name, getattr(getattr(prefs, group), name) == value)
    start_records()
    capture("safe-notice.png")


def phase_safe_restart():
    import angelo_safe_start
    from angelo_agent import safe_start_notice as notice, version_carry_over
    yield Until(lambda: version_carry_over.last_result is not None and chat_shown(), "the add-on and its chat")
    yield 8.0   # Past the start's completion: a notice would have shown by now.
    check("no_add_on_left_off_again", angelo_safe_start.left_off() == [])
    check("no_safe_start_notice", notice.shown is None)
    check("the_crashing_addon_stays_off", crashing_addon_off())
    start_records()
    capture("safe-restart.png")


PHASES = {"update": phase_update, "restart": phase_restart, "crash": phase_crash, "safe": phase_safe,
          "safe_restart": phase_safe_restart}


def finish(error=None):
    if error:
        report["error"] = error.replace("Traceback (most recent", "Trace (most recent")   # The host scans for it.
    report["success"] = not error and bool(report["checks"]) and all(report["checks"].values())
    report["failed_checks"] = [name for name, ok in report["checks"].items() if not ok]
    (OUT / (PHASE + ".json")).write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_CARRY_OVER_PHASE", json.dumps({"phase": PHASE, "success": report["success"]}), flush=True)
    bpy.ops.wm.quit_blender()


def start():
    steps, state = PHASES[PHASE](), {"until": None, "since": 0.0}
    started = time.monotonic()

    def tick():
        try:
            assert time.monotonic() - started < 240, "Phase timed out"
            until = state["until"]
            if until is not None:
                if not until.condition():
                    assert time.monotonic() - state["since"] < until.limit, "Timed out waiting for " + until.what
                    return 0.2
                state["until"] = None
            step = next(steps)
            if isinstance(step, Until):
                state.update(until=step, since=time.monotonic())
                return 0.2
            return step
        except StopIteration:
            finish()
        except Exception:
            finish(traceback.format_exc())
        return None

    bpy.app.timers.register(tick, first_interval=1.0, persistent=True)   # The import reloads the start-up file.


start()
