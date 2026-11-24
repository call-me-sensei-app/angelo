# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The in-app half of ``update_carry_over_gate.py``: run inside the NEWER app, one window process per phase.

``update`` is the first launch after the update: the earlier version's chats, snapshots, attachments, briefs and
preferences are here and usable, the setup card and Blender's Quick Setup never showed, one notice said the update
is ready (captured), and each saved account's native sign-in resolves to the profile it had. When both versions share
a Blender base (a patch release, ``same_base``: the host reads both apps' ``--version`` and passes ``--same-base``;
this phase checks it against the apps' own versions) nothing is carried over by design (``version_carry_over`` moves
state only from an earlier base's folder): after the start has settled, nothing moved, no import ran, no notice
showed, the state is used where it is, every chat keeps its native context, and everything else is checked as
above. ``restart`` is the next
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
import update_carry_over_contract as contract  # noqa: E402  The verdicts, tested without Blender.

ARGS = sys.argv[sys.argv.index("--") + 1:]
PHASE = ARGS[ARGS.index("--native-phase") + 1]
OUT = Path(ARGS[ARGS.index("--fixture-root") + 1])
SAME_BASE = "--same-base" in ARGS and ARGS[ARGS.index("--same-base") + 1] == "1"   # The host's reading of both apps.
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
    listed = SessionCollection(state).records
    for chat in SEED["chats"]:
        loaded = SessionStore(state, chat["id"]).load()
        for name, value in contract.chat_checks(prefix, chat, loaded, listed, same_base()).items():
            check(name, value)
    first = SessionStore(state, SEED["snapshot"]["chat"]).load()
    reference = first["messages"][0]["references"][0]
    picture = ReferenceStore(state, SEED["snapshot"]["chat"]).checked_path(reference).read_bytes()
    snapshots = CheckpointStore(state, SEED["snapshot"]["chat"])
    _record, copy = snapshots.recovery_copy(SEED["snapshot"]["id"])
    targets = [str(target) for target, _backup, _entry in snapshots.restore_plan(SEED["snapshot"]["id"])]
    prefs = bpy.context.preferences
    config = Path(bpy.utils.user_resource('CONFIG'))
    recent = (config / "recent-files.txt").read_text(errors="replace") if (config / "recent-files.txt").exists() else ""
    seen = {"agent_enabled": addon_utils.check("angelo_agent") == (True, True),
            "chat_shown": any(console.is_chat(a) for a in bpy.context.window_manager.windows[0].screen.areas),
            "attachment_sha256": hashlib.sha256(picture).hexdigest(),
            "snapshot_sha256": hashlib.sha256(copy.read_bytes()).hexdigest(),
            "journal_targets": [str(Path(target).resolve()) for target in targets],
            "workspace_notes": str((state / "workspace/notes.md").resolve()),
            "store_issues": snapshots.inspect()["issues"],
            "preferences": {"%s.%s" % (group, name): getattr(getattr(prefs, group), name)
                            for group, name, _value in SEED["preferences"]},
            "preferences_saved": (config / "userpref.blend").is_file(), "recent": recent}
    for name, value in contract.data_checks(prefix, seen, SEED).items():
        check(name, value)


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


def same_base():
    """Whether the host found both apps on one Blender base (``update_carry_over_gate.app_version``)."""
    return SAME_BASE


def settled():
    """The start is over (``contract.settled``): the carry-over decided, nothing owed, start-up done, the chat open."""
    from angelo_agent import intro, version_carry_over, version_carry_over_ui as update
    return contract.settled(version_carry_over, update, intro, chat_shown)


def same_base_checks(prefix):
    """``contract.same_base_update`` from what this launch shows."""
    from angelo_agent import splash_setup, ui, version_carry_over, version_carry_over_ui as update
    record = Path(bpy.utils.user_resource('CONFIG')) / "angelo-carry-over.json"
    for name, value in contract.same_base_update(
            version_carry_over.last_result.status, update.shown, update.last_outcome, record.exists(),
            ui.state_directory(), SEED["state"], splash_setup.sequence, prefix).items():
        check(name, value)


def phase_update():
    from angelo_agent import card_host, splash_setup, version_carry_over, version_carry_over_ui as update
    report["same_base"] = same_base()
    # The earlier app seeded its own version's folder (or --as-older-version's): the host's reading must agree.
    check("base_mode_matches_the_apps",
          contract.base_mode_matches(SEED["config"], bpy.utils.user_resource('CONFIG'), same_base()))
    if report["same_base"]:
        yield from phase_update_same_base()
        return
    yield Until(lambda: update.shown is not None and card_host.is_active("notice"), "the update notice")
    began = time.monotonic()
    yield Until(lambda: chat_shown() or time.monotonic() - began > 30, "the settled layout", limit=40)
    yield contract.DWELL_S
    notice_active = card_host.is_active("notice")
    capture("update-settled.png")
    screen = bpy.context.window_manager.windows[0].screen
    report["layout"] = {"screen": screen.name, "areas": [area.type for area in screen.areas],
                        "workspace": bpy.context.window_manager.windows[0].workspace.name}
    result = version_carry_over.last_result
    report["carry_over"] = {"status": result.status, "message": result.message}
    capture("update-notice.png")
    record_file = Path(bpy.utils.user_resource('CONFIG')) / "angelo-carry-over.json"
    seen = {"notice_active": notice_active, "status": result.status, "outcome": update.last_outcome,
            "sequence": list(splash_setup.sequence), "shown": update.shown,
            "record": json.loads(record_file.read_text()) if record_file.exists() else None,
            "earlier_has_sessions": (Path(SEED["state"]) / "sessions").exists(),
            "earlier_has_readme": (Path(SEED["state"]) / "README-Angelo.txt").is_file()}
    for name, value in contract.carry_over_update(seen, SEED["state"], update.READY_ALL).items():
        check(name, value)
    check_everything_is_here("")
    candidate_values()
    report["sign_ins"] = sign_ins()


def phase_update_same_base():
    """The first launch of a patch release on the earlier version's own folder: nothing to carry over, nothing moves,
    no notice; the person's state is used where it is."""
    from angelo_agent import version_carry_over
    yield Until(settled, "the settled start (carry-over decided, nothing owed, start-up done, the chat open)")
    yield contract.DWELL_S   # As the carry-over path's dwell after its settled layout: a card the start-up opened is drawn.
    capture("update-settled.png")
    screen = bpy.context.window_manager.windows[0].screen
    report["layout"] = {"screen": screen.name, "areas": [area.type for area in screen.areas],
                        "workspace": bpy.context.window_manager.windows[0].workspace.name}
    result = version_carry_over.last_result
    report["carry_over"] = {"status": result.status, "message": result.message}
    same_base_checks("")
    check_everything_is_here("")
    candidate_values()
    report["sign_ins"] = sign_ins()


def phase_earlier_open():
    """The earlier app, opened in a window once after the seed (one base only), as a person who used it has: what its
    own start-up writes into the preferences they share is part of the person's state before the update, and its
    reading of every preference then, in its window (where its keymaps are), is the "before"."""
    yield from earlier_window("earlier_open")


def phase_earlier_reopen():
    """The earlier app in its window again after going back (one base only): its reading of every preference is the
    "after", by the same code as the "before"."""
    yield from earlier_window("earlier_reopen")


def earlier_settled():
    """``settled`` for the earlier app, whichever version it is: a part its version lacks counts as done (it only
    waits for the person's window; every check of the update is the candidate's, under ``settled``)."""
    from angelo_agent import intro, version_carry_over, version_carry_over_ui as update
    parts = (getattr(version_carry_over, "last_result", True), getattr(update, "pending", lambda: False),
             getattr(intro, "startup_done", lambda: True))
    return parts[0] is not None and not parts[1]() and parts[2]() and chat_shown()


def seed_keymap():
    """A person's keymap edits, made in the earlier app's window (a background app has no user keymap items): Ctrl
    Shift F5 opens the Add menu in the 3D View, and its X-ray toggle shortcut is removed; saved with the preferences,
    as the Preferences editor saves them (contract.SEEDED_KEYMAP, contract.SEEDED_REMOVAL)."""
    keymap_name, operator, key, value, menu = contract.SEEDED_KEYMAP
    keymap = bpy.context.window_manager.keyconfigs.user.keymaps[keymap_name]
    item = keymap.keymap_items.new(operator, key, value, ctrl=True, shift=True)
    item.properties.name = menu
    removal = bpy.context.window_manager.keyconfigs.user.keymaps[contract.SEEDED_REMOVAL[0]]
    default = next(i for i in removal.keymap_items if i.idname == contract.SEEDED_REMOVAL[1])
    removal.keymap_items.remove(default)
    bpy.ops.wm.save_userpref()


def reading():
    """Every preference as this app holds them now (update_carry_over_prefs.values), keymaps against its defaults."""
    import update_carry_over_prefs
    keyconfigs = bpy.context.window_manager.keyconfigs
    return update_carry_over_prefs.values(bpy.context.preferences, keyconfigs.user, keyconfigs.default)


def candidate_values():
    report["preference_values"] = reading()


def earlier_window(name):
    yield Until(earlier_settled, "the earlier app's settled start (its chat open)")
    yield contract.DWELL_S
    if name == "earlier_open":
        seed_keymap()
        yield contract.DWELL_S
    values = reading()
    report["preference_values"] = values
    for check_name, value in contract.earlier_window(name, earlier_settled(), values).items():
        check(check_name, value)


def phase_restart():
    from angelo_agent import version_carry_over, version_carry_over_ui as update
    if same_base():
        yield Until(settled, "the settled start (carry-over decided, nothing owed, start-up done, the chat open)")
    yield Until(lambda: version_carry_over.last_result is not None, "the add-on")
    yield 8.0   # Past the first-launch decision: a notice would have shown by now.
    check("nothing_moved_again", version_carry_over.last_result.status == "nothing")
    check("no_notice_again", update.shown is None)
    if same_base():
        same_base_checks("restart_")
    check_everything_is_here("restart_")
    candidate_values()
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
          "safe_restart": phase_safe_restart, "earlier_open": phase_earlier_open, "earlier_reopen": phase_earlier_reopen}


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
