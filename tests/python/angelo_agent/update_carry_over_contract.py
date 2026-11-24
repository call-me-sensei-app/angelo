# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The update gate's verdicts, without Blender: what ``update_carry_over_phases`` (in the candidate) and
``update_carry_over_gate`` (on the host) decide from what they read, so each rule is tested on its own
(``test_native_update_gate_same_base``). The phases read Angelo's modules and pass them, or their values, in here.

Two contracts: an update to a newer Blender base carries the state over (the notice, the record, a fresh native
context for each chat, the earlier app starting without the moved chats); an update on one base (a patch release)
carries nothing over by design (nothing moved, no import, no notice, no record, the state used where it is, each
chat's native context kept, the earlier app listing the same chats). Every data check is the same in both."""

from pathlib import Path

import update_carry_over_prefs

FIRST_MESSAGE = "Make the monkey taller"
DWELL_S = 2.0   # after the settled start, before any check: a card the start-up opened is drawn
SIGNED_IN = ("Connected to Claude", "Connected to ChatGPT", "Native API credential configured")   # Angelo's own texts


def signed_in(status):
    """Whether a sign-in status command's whitelisted result says the account is signed in (never its exit code alone:
    a CLI exits 0 for a different billing mode too)."""
    return status in SIGNED_IN


def data_checks(prefix, seen, seed):
    """Everything a person had, as the candidate shows it (``seen``, read by the phase): the agent with its chat, the
    attachment and the snapshot byte for byte, the file journal, the snapshot store, each seeded preference, the
    preferences file of this version and the recent scene. The same in both contracts."""
    checks = {
        "agent_enabled_with_its_chat": bool(seen["agent_enabled"]) and bool(seen["chat_shown"]),
        "attachment_opens": seen["attachment_sha256"] == seed["attachment_sha256"],
        "snapshot_restores_byte_for_byte": seen["snapshot_sha256"] == seed["snapshot"]["sha256"],
        "file_journal_follows_the_workspace": bool(seen["journal_targets"])
        and seen["journal_targets"] == [seen["workspace_notes"]],
        "snapshot_store_sound": seen["store_issues"] == [],
        "preferences_saved_in_this_version": bool(seen["preferences_saved"]),
        "recent_scene_listed": seed["scene"] in seen["recent"],
    }
    for group, name, value in seed["preferences"]:
        checks["preference_" + name] = seen["preferences"].get("%s.%s" % (group, name), ...) == value
    return {prefix + name: value for name, value in checks.items()}


def carry_over_update(seen, seed_state, ready_all):
    """The first launch after an update to a newer base: the state moved, the settings imported, the notice on screen
    saying it is ready and that chats, snapshots and settings came over, the record done, the earlier folder empty of
    chats and saying where they went."""
    shown, outcome, record = seen["shown"] or ("", "", ""), seen["outcome"], seen["record"] or {}
    return {
        "the_notice_is_on_screen_in_the_settled_layout": bool(seen["notice_active"]),
        "state_moved": seen["status"] == "migrated",
        "settings_imported": outcome is not None and not outcome.get("exception"),
        "no_setup_card_and_no_quick_setup": "setup" not in seen["sequence"],
        "notice_says_the_update_is_ready": "is ready" in shown[1],
        "notice_says_chats_snapshots_and_settings_came_over": shown[2].startswith(ready_all),
        "record_done": record.get("status") == "done" and bool(record.get("from"))
        and Path(record["from"]).resolve() == Path(seed_state).resolve(),
        "earlier_state_folder_gave_up_its_chats": not seen["earlier_has_sessions"],
        "earlier_state_folder_says_where_it_went": bool(seen["earlier_has_readme"]),
    }


def earlier_window(name, settled_now, preference_values):
    """The earlier app in its window (one base: before the update, ``earlier_open``, and after going back,
    ``earlier_reopen``): settled, and its own reading of every preference (``update_carry_over_prefs``)."""
    return {"earlier_app_settled_in_its_window" if name == "earlier_open" else "earlier_app_settled_again": bool(settled_now),
            ("earlier_app_read_its_preferences" if name == "earlier_open" else "earlier_app_read_its_preferences_again"):
            bool(preference_values)}


def settled(carry, update, intro, chat_shown):
    """The start is over: the carry-over decided, no settings import or notice owed, Angelo's start-up done (the
    card that follows the splash decided: ``intro.startup_done``), and the agent's chat open."""
    return (carry.last_result is not None and not update.pending() and intro.startup_done()
            and chat_shown())


def base_mode_matches(seed_config, own_config, same_base):
    """Whether the host's same-base reading agrees with the apps: the earlier app seeded the folder of its own version
    (``--as-older-version``: an earlier version's), and the candidate's own folder is the same one only on one base."""
    return (Path(seed_config).parent.name == Path(own_config).parent.name) == bool(same_base)


def same_base_update(status, shown, last_outcome, record_exists, state, seeded_state, sequence, prefix=""):
    """The first launch (``prefix`` "") or the next (``restart_``) of a patch release on the earlier version's own
    folder."""
    checks = {"no_setup_card_and_no_quick_setup": "setup" not in sequence}
    if prefix == "":
        checks.update({
            "same_base_nothing_moved": status == "nothing",
            "same_base_no_update_notice": shown is None,
            "same_base_settings_not_imported": last_outcome is None,
            "same_base_no_carry_over_record": not record_exists,
            "same_base_state_used_in_place": Path(state).resolve() == Path(seeded_state).resolve(),
        })
    return {prefix + name: value for name, value in checks.items()}


def chat_checks(prefix, chat, loaded, listed, same_base):
    """One seeded chat as the candidate finds it: listed, its history and brief kept, and on the first launch its
    native context: fresh after a carry-over (it started in the old working folder), the very one it had on one base
    (its working folder never moved)."""
    name = prefix + "chat_%s_" % chat["id"][:8]
    messages = loaded.get("messages") or []
    checks = {
        name + "listed": chat["id"] in listed,
        name + "history_kept": len(messages) >= chat["messages"]
        and messages[0].get("text", "").startswith(FIRST_MESSAGE),
        name + "brief_kept": loaded["task_memory"]["brief"]["objective"] == chat["brief"],
    }
    if prefix == "" and not same_base:
        checks[name + "fresh_native_context"] = loaded["native_id"] is None
    elif prefix == "":
        checks[name + "native_context_kept"] = bool(chat.get("native_id")) and loaded["native_id"] == chat["native_id"]
    return checks


def preferences_kept(same_base, rewritten, before, after):
    """The person's preferences after the update. Across bases the earlier version's own file is never touched
    (byte for byte). On one base both versions use one file, and every Angelo rewrites it on a plain relaunch (the
    control of 2026-11-20: the published 2.0.0 relaunched as itself rewrote it), so every value a person can change,
    as the earlier app reads it before the update and after going back, must be the same (``update_carry_over_prefs``)."""
    if not same_base:
        return {"earlier_preferences_unchanged": not rewritten}
    return {"earlier_preference_values_unchanged": bool(before) and bool(after)
            and not update_carry_over_prefs.differences(before, after)}


# What the person did to their preferences before the update (update_carry_over_seed: a theme colour and an asset
# library; update_carry_over_phases.seed_keymap in the earlier app's window: an added shortcut and a removed one).
# The seeded theme colour as a reading holds it (update_carry_over_seed.SEEDED_THEME_INNER, 8-bit exact, to 6 places).
SEEDED_THEME = ("themes[Default].user_interface.wcol_regular.inner", [0.211765, 0.419608, 0.631373, 1.0])
SEEDED_LIBRARY = "Carry-over library"
SEEDED_KEYMAP = ("3D View", "wm.call_menu", "F5", "PRESS", "VIEW3D_MT_add")
SEEDED_REMOVAL = ("3D View", "view3d.toggle_xray")


def seeded_in_before(before):
    """Each of the person's own changes is in the "before" reading, by its exact key: the comparison covers them."""
    import update_carry_over_prefs as prefs
    keymap, operator, key, value, menu = SEEDED_KEYMAP
    added = before.get("keymaps.%s.%s#0" % (keymap, operator))
    removed = before.get("keymaps.%s.missing" % SEEDED_REMOVAL[0]) or []
    return {
        "seeded_theme_in_before": before.get(SEEDED_THEME[0]) == SEEDED_THEME[1],
        "seeded_asset_library_in_before": before.get("filepaths.asset_libraries[%s].name" % SEEDED_LIBRARY)
        == prefs.digest(SEEDED_LIBRARY),   # strings are held as digests
        "seeded_keymap_in_before": isinstance(added, list) and len(added) > 6 and added[:7] == [operator, key, value, False,
                                                                                               True, True, False]
        and added[-1] == {"name": prefs.digest(menu)},
        "seeded_keymap_removal_in_before": any(entry.startswith(json_list_start(SEEDED_REMOVAL[1])) for entry in removed),
    }


def json_list_start(operator):
    return '["%s", ' % operator


def candidate_preferences(phase, before, after, same_base):
    """The person's preferences as the candidate itself holds them in its window (``phase`` update or restart): every
    value the earlier app read before the update must be there and equal (``changed``, ``missing``); values new in
    the candidate are listed (``new``), never a failure. Across bases Blender's own RNA changes with the base, so
    there only the values both versions have are compared and the rest are listed as ``gone``."""
    import update_carry_over_prefs as prefs
    before = {k: v for k, v in before.items() if not any(k.startswith(x) for x in prefs.EXCLUDED)}
    after = {k: v for k, v in after.items() if not any(k.startswith(x) for x in prefs.EXCLUDED)}
    shared = [key for key in before if key in after]
    details = {"changed": sorted(key for key in shared if before[key] != after[key]),
               "missing" if same_base else "gone": sorted(key for key in before if key not in after),
               "new": sorted(key for key in after if key not in before)}
    # On one base a keymap item the person never had is a stray or duplicated one of theirs, never a new default.
    details["new_keymap_items"] = [key for key in details["new"] if key.startswith("keymaps.")] if same_base else []
    kept = (bool(before) and bool(after) and not details["changed"]
            and not (same_base and (details["missing"] or details["new_keymap_items"])))
    return {phase + "_preferences_as_before": kept}, details


def go_back(same_base, back, seeded_chats):
    """The earlier app opened again: its chats as the contract says (none after a carry-over, every one on one base)."""
    if same_base:
        return {"earlier_app_lists_the_shared_chats": back.get("seeded_chats_listed") == seeded_chats}
    return {"earlier_app_starts_its_own_chats": back.get("seeded_chats_listed") == 0}
