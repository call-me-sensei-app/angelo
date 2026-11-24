# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``update_carry_over_gate``: an update on one Blender base (a patch release) is decided from both apps' own
``--version``, never from a flag, and gated by that update's contract; an update to a newer base keeps the carry-over
contract. The verdicts (``update_carry_over_contract``) are tested here against stand-ins for what the candidate
shows; the in-app phases that read them are proven by the gate itself on macOS and Windows."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_carry_over_contract as contract  # noqa: E402
import update_carry_over_gate as gate  # noqa: E402
import update_carry_over_prefs  # noqa: E402

V200 = ("Blender 5.3.0\n\tbuild date: 2026-11-17\n\tbuild time: 11:58:16\n\tbuild hash: e3d0c27531af\n"
        "\tbuild branch: angelo-v2.0.0-release\n\tbuild platform: Darwin\n")
V201 = V200.replace("e3d0c27531af", "7bc06dcfca9a").replace("2.0.0", "2.0.1")
V1011 = V200.replace("Blender 5.3.0", "Blender 5.2.4").replace("e3d0c27531af", "5300af0847ab")


def version(text, binary="Angelo"):
    done = subprocess.CompletedProcess([], 0, text.encode(), None)
    with mock.patch.object(gate.subprocess, "run", return_value=done):
        return gate.app_version(binary, {})


class AppVersion(unittest.TestCase):
    def test_reads_what_the_app_says_and_its_own_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "Angelo"
            binary.write_bytes(b"the 2.0.0 executable")
            self.assertEqual(version(V200, binary), {
                "blender": "5.3.0", "base": "5.3", "build_hash": "e3d0c27531af", "build_branch": "angelo-v2.0.0-release",
                "executable_sha256": hashlib.sha256(b"the 2.0.0 executable").hexdigest()})
        self.assertEqual(version("\r\n" + V1011.replace("\n", "\r\n"))["base"], "5.2")
        self.assertEqual(version(V201.replace("angelo-v2.0.1-release", "angelo-v2.0.1-release (modified)"))["build_branch"],
                         "angelo-v2.0.1-release (modified)")
        self.assertIsNone(version(V200)["executable_sha256"])   # no such file

    def test_an_app_that_says_nothing_usable(self):
        said = version("Angelo crashed\n")
        self.assertEqual((said["blender"], said["base"], said["build_hash"], said["build_branch"]), (None,) * 4)
        with self.assertRaises(SystemExit):
            gate.same_base(said, version(V201))


class SameBase(unittest.TestCase):
    def test_one_base(self):
        self.assertTrue(gate.same_base(version(V200), version(V201)))

    def test_a_newer_base(self):
        self.assertFalse(gate.same_base(version(V1011), version(V200)))

    def test_seeding_an_older_version_folder_is_never_same_base(self):
        self.assertFalse(gate.same_base(version(V200), version(V201), as_older_version="5.2"))


class BaseModeMatches(unittest.TestCase):
    """The phases' cross-check of the host's reading against the folders the apps themselves use."""

    def test_each_mode(self):
        p53, p52 = "/h/Angelo/5.3/config", "/h/Angelo/5.2/config"
        self.assertTrue(contract.base_mode_matches(p53, p53, True))      # one base
        self.assertTrue(contract.base_mode_matches(p52, p53, False))     # a newer base
        self.assertTrue(contract.base_mode_matches("/h/Angelo/5.1/config", p53, False))   # --as-older-version 5.1
        self.assertFalse(contract.base_mode_matches(p53, p53, False))
        self.assertFalse(contract.base_mode_matches(p52, p53, True))


class Settled(unittest.TestCase):
    def parts(self, result="nothing", pending=False, done=True, chat=True):
        carry = SimpleNamespace(last_result=None if result is None else SimpleNamespace(status=result))
        return carry, SimpleNamespace(pending=lambda: pending), SimpleNamespace(startup_done=lambda: done), lambda: chat

    def test_only_when_everything_is_over(self):
        self.assertTrue(contract.settled(*self.parts()))
        for broken in ({"result": None}, {"pending": True}, {"done": False}, {"chat": False}):
            self.assertFalse(contract.settled(*self.parts(**broken)), broken)


class SameBaseUpdate(unittest.TestCase):
    def verdict(self, prefix="", **changed):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "Angelo/5.3/config/angelo-agent"
            state.mkdir(parents=True)
            values = dict(status="nothing", shown=None, last_outcome=None, record_exists=False, state=state,
                          seeded_state=state, sequence=["splash"])
            values.update({k: (Path(folder) / v if k == "state" else v) for k, v in changed.items()})
            return contract.same_base_update(prefix=prefix, **values)

    def test_a_correct_update_passes_every_check(self):
        verdict = self.verdict()
        self.assertEqual(sorted(verdict), sorted([
            "same_base_nothing_moved", "same_base_no_update_notice", "same_base_settings_not_imported",
            "same_base_no_carry_over_record", "same_base_state_used_in_place", "no_setup_card_and_no_quick_setup"]))
        self.assertTrue(all(verdict.values()))
        self.assertEqual(self.verdict("restart_"), {"restart_no_setup_card_and_no_quick_setup": True})

    def test_each_broken_update_fails_its_check(self):
        for changed, name in (({"status": "migrated"}, "same_base_nothing_moved"),
                              ({"status": "deferred"}, "same_base_nothing_moved"),
                              ({"shown": ("", "Angelo 2.0.1 is ready", "")}, "same_base_no_update_notice"),
                              ({"last_outcome": {"app": "ANGELO"}}, "same_base_settings_not_imported"),
                              ({"record_exists": True}, "same_base_no_carry_over_record"),
                              ({"state": "elsewhere"}, "same_base_state_used_in_place"),
                              ({"sequence": ["splash", "setup"]}, "no_setup_card_and_no_quick_setup")):
            verdict = self.verdict(**changed)
            self.assertFalse(verdict[name], changed)
            self.assertEqual([n for n, ok in verdict.items() if not ok], [name], changed)
        self.assertFalse(self.verdict("restart_", sequence=["setup"])["restart_no_setup_card_and_no_quick_setup"])


class ChatChecks(unittest.TestCase):
    chat = {"id": "15918f4c-0000", "messages": 2, "brief": "Taller monkey 0", "native_id": "native-a"}

    def loaded(self, **changed):
        value = {"messages": [{"text": "Make the monkey taller (chat 0)"}, {"text": "Done"}],
                 "task_memory": {"brief": {"objective": "Taller monkey 0"}}, "native_id": "native-a"}
        value.update(changed)
        return value

    def test_one_base_keeps_the_very_native_context(self):
        verdict = contract.chat_checks("", self.chat, self.loaded(), {"15918f4c-0000"}, True)
        self.assertEqual(verdict, {"chat_15918f4c_listed": True, "chat_15918f4c_history_kept": True,
                                   "chat_15918f4c_brief_kept": True, "chat_15918f4c_native_context_kept": True})
        for native in ("native-b", None, ""):
            self.assertFalse(contract.chat_checks("", self.chat, self.loaded(native_id=native), {"15918f4c-0000"},
                                                  True)["chat_15918f4c_native_context_kept"], native)
        unrecorded = dict(self.chat, native_id=None)
        self.assertFalse(contract.chat_checks("", unrecorded, self.loaded(native_id=None), {"15918f4c-0000"},
                                              True)["chat_15918f4c_native_context_kept"])

    def test_a_carry_over_starts_a_fresh_native_context(self):
        verdict = contract.chat_checks("", self.chat, self.loaded(native_id=None), {"15918f4c-0000"}, False)
        self.assertTrue(verdict.pop("chat_15918f4c_fresh_native_context"))
        self.assertNotIn("chat_15918f4c_native_context_kept", verdict)
        self.assertFalse(contract.chat_checks("", self.chat, self.loaded(), set(), False)["chat_15918f4c_fresh_native_context"])

    def test_lost_chats_history_or_briefs_fail(self):
        for prefix in ("", "restart_"):
            name = prefix + "chat_15918f4c_"
            self.assertFalse(contract.chat_checks(prefix, self.chat, self.loaded(), set(), True)[name + "listed"])
            self.assertFalse(contract.chat_checks(prefix, self.chat, self.loaded(messages=[]), {"15918f4c-0000"},
                                                  True)[name + "history_kept"])
            self.assertFalse(contract.chat_checks(prefix, self.chat, self.loaded(
                task_memory={"brief": {"objective": ""}}), {"15918f4c-0000"}, True)[name + "brief_kept"])
        self.assertEqual(sorted(contract.chat_checks("restart_", self.chat, self.loaded(), {"15918f4c-0000"}, True)),
                         ["restart_chat_15918f4c_brief_kept", "restart_chat_15918f4c_history_kept",
                          "restart_chat_15918f4c_listed"])


class GoBack(unittest.TestCase):
    def test_each_contract(self):
        self.assertEqual(contract.go_back(True, {"seeded_chats_listed": 3}, 3), {"earlier_app_lists_the_shared_chats": True})
        for listed in (0, 2, None):
            self.assertEqual(contract.go_back(True, {"seeded_chats_listed": listed}, 3),
                             {"earlier_app_lists_the_shared_chats": False})
        self.assertEqual(contract.go_back(False, {"seeded_chats_listed": 0}, 3), {"earlier_app_starts_its_own_chats": True})
        self.assertEqual(contract.go_back(False, {"seeded_chats_listed": 3}, 3), {"earlier_app_starts_its_own_chats": False})


class PreferencesKept(unittest.TestCase):
    before = {"addons": ["angelo_agent"], "edit.undo_steps": 61, "view.ui_line_width": "THICK"}

    def test_across_bases_the_earlier_file_is_never_touched(self):
        self.assertEqual(contract.preferences_kept(False, False, {}, {}), {"earlier_preferences_unchanged": True})
        self.assertEqual(contract.preferences_kept(False, True, self.before, self.before),
                         {"earlier_preferences_unchanged": False})

    def test_on_one_base_every_value_decides(self):
        self.assertEqual(contract.preferences_kept(True, True, self.before, dict(self.before)),
                         {"earlier_preference_values_unchanged": True})
        for after in (dict(self.before, **{"edit.undo_steps": 32}), dict(self.before, addons=[]),
                      {k: v for k, v in self.before.items() if k != "view.ui_line_width"},
                      dict(self.before, **{"system.new": 1}), {}):
            self.assertEqual(contract.preferences_kept(True, False, self.before, after),
                             {"earlier_preference_values_unchanged": False}, after)
        self.assertEqual(contract.preferences_kept(True, False, {}, {}), {"earlier_preference_values_unchanged": False})


def rna(identifier="Struct", pointer=None, **members):
    """A stand-in RNA struct: (type, value, readonly) per member."""
    props = [SimpleNamespace(identifier="rna_type", type="POINTER", is_readonly=True)]
    values = {}
    for name, (kind, value, readonly) in members.items():
        props.append(SimpleNamespace(identifier=name, type=kind, is_readonly=readonly))
        values[name] = value
    struct = SimpleNamespace(bl_rna=SimpleNamespace(properties=props, identifier=identifier), rna_type=None, **values)
    if pointer is not None:
        struct.as_pointer = lambda: pointer
    return struct


class PreferenceValues(unittest.TestCase):
    def prefs(self):
        library = lambda name, path: rna("UserAssetLibrary", name=("STRING", name, False), path=("STRING", path, False),
                                         auth_token=("STRING", TOKEN, False))
        generic = rna("ThemeGeneric", pointer=7, header=("FLOAT", (0.25, 0.5, 0.125), False))
        space = rna("ThemeSpace", pointer=7, space=("POINTER", generic, True), text=("FLOAT", (0.123456, 0.2, 0.3), False))
        theme = rna("Theme", name=("STRING", "Default", False), view_3d=("POINTER", space, True))
        light = lambda on: rna("SolidLight", use=("BOOLEAN", on, False))
        angelo = rna("ANGELO_Preferences", provider=("ENUM", "codex", False), reasoning=("ENUM", "high", False),
                     claude_path=("STRING", "/usr/local/bin/claude", False))
        prefs = rna("Preferences",
                    edit=("POINTER", rna("PreferencesEdit", undo_steps=("INT", 61, False), use_global_undo=("BOOLEAN", True, False)), True),
                    view=("POINTER", rna("PreferencesView", ui_scale=("FLOAT", 1.0000001, False), color=("FLOAT", (0.1, 0.2), False),
                                         flags=("ENUM", {"B", "A"}, False), dpi=("INT", 144, True),
                                         font=("STRING", "/fonts/a.ttf", False), line=("FLOAT", 0.123456, False)), True),
                    filepaths=("POINTER", rna("PreferencesFilePaths", asset_libraries=("COLLECTION", [
                        library("Studio", "/lib/studio"), library("Mine", "/lib/mine")], True)), True),
                    themes=("COLLECTION", [theme], True), solid_lights=("COLLECTION", [light(True), light(False)], True),
                    is_dirty=("BOOLEAN", True, False))
        prefs.addons = [SimpleNamespace(module="b", preferences=None),
                        SimpleNamespace(module="angelo_agent", preferences=angelo)]
        def item(idname, modified, defined=False, **props):
            properties = SimpleNamespace(keys=lambda: list(props), **props)
            return SimpleNamespace(idname=idname, type="F5", value="PRESS", any=False, shift=True, ctrl=True, alt=False,
                                   oskey=False, hyper=False, key_modifier="NONE", active=True, map_type="KEYBOARD",
                                   repeat=False, direction="ANY", is_user_modified=modified, is_user_defined=defined,
                                   properties=properties)
        keymap = SimpleNamespace(name="3D View", is_user_modified=True, keymap_items=[
            item("view3d.select", False, extend=False),          # a default the person never touched: not theirs
            item("wm.call_menu", False, True, name="VIEW3D_MT_add"),
            item("wm.call_menu", True, False, name="VIEW3D_MT_snap")])
        default = SimpleNamespace(name="3D View", keymap_items=[item("view3d.select", False, extend=False),
                                                                item("view3d.toggle_xray", False)])
        self.default = SimpleNamespace(keymaps=[default])
        return prefs, SimpleNamespace(keymaps=[keymap])

    def test_everything_userpref_keeps(self):
        prefs, keyconfig = self.prefs()
        found = update_carry_over_prefs.values(prefs, keyconfig, self.default)
        xray = found.pop("keymaps.3D View.missing")
        self.assertEqual(len(xray), 1)
        self.assertTrue(xray[0].startswith('["view3d.toggle_xray", "F5", "PRESS"'))
        self.assertEqual(found.pop("keymaps.3D View[]"), 3)
        self.assertEqual(found.pop(update_carry_over_prefs.UNREADABLE), [])
        token = D(TOKEN)
        self.assertEqual({k: v for k, v in found.items() if k.endswith("auth_token")},
                         {"filepaths.asset_libraries[Studio].auth_token": token, "filepaths.asset_libraries[Mine].auth_token": token})
        found = {k: v for k, v in found.items() if not k.endswith("auth_token")}
        self.assertEqual(found, {
            "addons": ["angelo_agent", "b"], "edit.undo_steps": 61, "edit.use_global_undo": True,
            "view.ui_scale": 1.0, "view.color": [0.1, 0.2], "view.flags": ["A", "B"],
            "view.font": D("/fonts/a.ttf"), "view.line": 0.123456,
            "filepaths.asset_libraries[]": 2,
            "filepaths.asset_libraries[Studio].name": D("Studio"), "filepaths.asset_libraries[Studio].path": D("/lib/studio"),
            "filepaths.asset_libraries[Mine].name": D("Mine"), "filepaths.asset_libraries[Mine].path": D("/lib/mine"),
            "themes[]": 1, "themes[Default].name": D("Default"), "themes[Default].view_3d.text": [0.123456, 0.2, 0.3],
            "themes[Default].view_3d.space.header": [0.25, 0.5, 0.125],
            "solid_lights[]": 2, "solid_lights[0].use": True, "solid_lights[1].use": False,
            "addons.angelo_agent.provider": "codex", "addons.angelo_agent.reasoning": "high",
            "addons.angelo_agent.claude_path": D("/usr/local/bin/claude"),
            "keymaps.3D View.wm.call_menu#0": ["wm.call_menu", "F5", "PRESS", False, True, True, False, False, False,
                                               "NONE", True, "KEYBOARD", False, "ANY", {"name": D("VIEW3D_MT_add")}],
            "keymaps.3D View.wm.call_menu#1": ["wm.call_menu", "F5", "PRESS", False, True, True, False, False, False,
                                               "NONE", True, "KEYBOARD", False, "ANY", {"name": D("VIEW3D_MT_snap")}]})

    def test_a_credential_is_never_held_and_still_compared(self):
        prefs, keyconfig = self.prefs()
        reading = update_carry_over_prefs.values(prefs, keyconfig, self.default)
        self.assertNotIn(TOKEN, json.dumps(reading))
        prefs, keyconfig = self.prefs()
        prefs.filepaths.asset_libraries[0].auth_token = TOKEN + "-rotated"
        self.assertEqual(update_carry_over_prefs.differences(reading, update_carry_over_prefs.values(prefs, keyconfig, self.default)),
                         ["filepaths.asset_libraries[Studio].auth_token"])
        named = rna("Repo", name=("STRING", "x", False), access_token=("STRING", TOKEN, False),
                    api_key=("STRING", TOKEN, False), opaque=("STRING", TOKEN, False))
        named.bl_rna.properties[-1].subtype = "PASSWORD"   # a PASSWORD property, whatever its name
        self.assertNotIn(TOKEN, json.dumps(update_carry_over_prefs.values(rna("Preferences", repo=("POINTER", named, True)))))
        # Any string a person typed, whatever its name: an add-on's openai_key, a licence key, a URL with a password.
        addon = rna("ThirdParty", openai_key=("STRING", "PLANTED-3", False), license_key=("STRING", "PLANTED-4", False),
                    endpoint=("STRING", "https://u:PLANTED-5@h/v1", False))
        prefs = rna("Preferences")
        prefs.addons = [SimpleNamespace(module="third_party", preferences=addon)]
        self.assertNotIn("PLANTED", json.dumps(update_carry_over_prefs.values(prefs)))

    def test_an_unreadable_path_is_listed(self):
        class Broken:
            def __getattr__(self, name):
                raise TypeError("cannot read " + name)
        bad = rna("Paths", script_directories=("COLLECTION", None, True), font=("STRING", "x", False))
        bad.script_directories = property(lambda self: 1 / 0)
        prefs = rna("Preferences", filepaths=("POINTER", bad, True))
        prefs.filepaths = SimpleNamespace(bl_rna=bad.bl_rna, rna_type=None, font="x")   # no script_directories attribute
        found = update_carry_over_prefs.values(prefs)
        self.assertEqual(update_carry_over_prefs.unreadable(found), ["filepaths.script_directories"])

        class Section:   # a pointer section whose access raises
            bl_rna = SimpleNamespace(identifier="Preferences", properties=[
                SimpleNamespace(identifier="rna_type", type="POINTER", is_readonly=True),
                SimpleNamespace(identifier="system", type="POINTER", is_readonly=True)])
            rna_type = None

            @property
            def system(self):
                raise TypeError("this build cannot read it")
        self.assertEqual(update_carry_over_prefs.unreadable(update_carry_over_prefs.values(Section())), ["system"])

    def test_a_change_anywhere_is_a_difference(self):
        prefs, keyconfig = self.prefs()
        before = update_carry_over_prefs.values(prefs, keyconfig)
        for change in (lambda p, k: setattr(p.view, "line", 0.123457), lambda p, k: setattr(p.view, "font", "/fonts/b.ttf"),
                       lambda p, k: setattr(p.addons[1].preferences, "reasoning", "low"),
                       lambda p, k: p.filepaths.asset_libraries.pop(),
                       lambda p, k: setattr(p.themes[0].view_3d.space, "header", (0.25, 0.5, 0.126)),
                       lambda p, k: setattr(k.keymaps[0].keymap_items[1], "shift", False),
                       lambda p, k: setattr(k.keymaps[0].keymap_items[2].properties, "name", "VIEW3D_MT_add"),
                       lambda p, k: setattr(k.keymaps[0].keymap_items[1], "is_user_defined", False),
                       lambda p, k: k.keymaps[0].keymap_items.append(k.keymaps[0].keymap_items[0])):
            prefs, keyconfig = self.prefs()
            change(prefs, keyconfig)
            self.assertTrue(update_carry_over_prefs.differences(before, update_carry_over_prefs.values(prefs, keyconfig)))
        before = update_carry_over_prefs.values(*self.prefs(), self.default)
        prefs, keyconfig = self.prefs()
        keyconfig.keymaps[0].keymap_items.append(SimpleNamespace(**vars(self.default.keymaps[0].keymap_items[1])))
        self.assertIn("keymaps.3D View.missing",   # the removed shortcut came back
                      update_carry_over_prefs.differences(before, update_carry_over_prefs.values(prefs, keyconfig, self.default)))

    def test_nothing_is_left_out_unnamed(self):
        # Only the control-justified runtime flag; it is a top-level name, so nothing else starts with it.
        self.assertEqual(sorted(update_carry_over_prefs.EXCLUDED), ["is_dirty"])
        self.assertIn("control", update_carry_over_prefs.EXCLUDED["is_dirty"])
        prefs, keyconfig = self.prefs()
        self.assertNotIn("is_dirty", update_carry_over_prefs.values(prefs, keyconfig))

    def test_a_removed_one_of_two_identical_defaults_is_seen(self):
        # The default keymap holds the same shortcut twice; the person removed one: a multiset, never a set.
        prefs, keyconfig = self.prefs()
        twin = self.default.keymaps[0].keymap_items[1]
        self.default.keymaps[0].keymap_items.append(SimpleNamespace(**vars(twin)))
        keyconfig.keymaps[0].keymap_items.append(SimpleNamespace(**vars(twin)))
        found = update_carry_over_prefs.values(prefs, keyconfig, self.default)
        self.assertEqual(len(found["keymaps.3D View.missing"]), 1)
        keyconfig.keymaps[0].keymap_items.append(SimpleNamespace(**vars(twin)))
        self.assertEqual(update_carry_over_prefs.values(prefs, keyconfig, self.default)["keymaps.3D View.missing"], [])

    def test_a_fixed_default_keymap_is_no_loss(self):
        # A default item the person never changed may move or change in the candidate: it is not theirs to keep.
        prefs, keyconfig = self.prefs()
        before = update_carry_over_prefs.values(prefs, keyconfig)
        prefs, keyconfig = self.prefs()
        keyconfig.keymaps[0].keymap_items.insert(0, keyconfig.keymaps[0].keymap_items.pop(0))
        keyconfig.keymaps[0].keymap_items[0].type = "RIGHTMOUSE"
        self.assertEqual(update_carry_over_prefs.differences(before, update_carry_over_prefs.values(prefs, keyconfig)), [])

    def test_families(self):
        prefs, keyconfig = self.prefs()
        self.assertEqual(update_carry_over_prefs.families(update_carry_over_prefs.values(prefs, keyconfig, self.default)),
                         {"themes": 4, "keymaps": 4, "angelo_settings": 3, "asset_libraries": 7})

    def test_differences(self):
        self.assertEqual(update_carry_over_prefs.differences({"a": 1, "b": 2}, {"a": 1, "b": 2}), [])
        self.assertEqual(update_carry_over_prefs.differences({"a": 1, "b": 2}, {"a": 2, "c": None}), ["a", "b", "c"])


TOKEN = "tok-3f9a1c7e5b2d4a6f8e0c"


def D(value):
    return update_carry_over_prefs.digest(value)


class SeededInBefore(unittest.TestCase):
    before = {"themes[Default].user_interface.wcol_regular.inner": [0.211765, 0.419608, 0.631373, 1.0],
              "filepaths.asset_libraries[Carry-over library].name": D("Carry-over library"),
              "keymaps.3D View.wm.call_menu#0": ["wm.call_menu", "F5", "PRESS", False, True, True, False, False, False,
                                                 "NONE", True, "KEYBOARD", False, "ANY", {"name": D("VIEW3D_MT_add")}],
              "keymaps.3D View.missing": ['["view3d.toggle_xray", "Z", "PRESS", false, false, false, true] {}']}

    def test_each_seeded_change_is_in_the_before(self):
        self.assertEqual(contract.seeded_in_before(self.before), dict.fromkeys(
            ("seeded_theme_in_before", "seeded_asset_library_in_before", "seeded_keymap_in_before",
             "seeded_keymap_removal_in_before"), True))
        for key, value, name in (
                ("themes[Default].user_interface.wcol_regular.inner", [0.2, 0.2, 0.2, 1.0], "seeded_theme_in_before"),
                ("filepaths.asset_libraries[Carry-over library].name", None, "seeded_asset_library_in_before"),
                ("keymaps.3D View.wm.call_menu#0", self.before["keymaps.3D View.wm.call_menu#0"][:-1] + [{"name": D("X")}],
                 "seeded_keymap_in_before"),   # the shortcut's property: the menu it opens
                ("keymaps.3D View.wm.call_menu#0", None, "seeded_keymap_in_before"),
                ("keymaps.3D View.missing", [], "seeded_keymap_removal_in_before")):
            verdict = contract.seeded_in_before(dict(self.before, **{key: value}))
            self.assertEqual([n for n, ok in verdict.items() if not ok], [name], (key, value))

    def test_the_seeded_theme_is_the_one_the_seed_writes(self):
        import update_carry_over_prefs as prefs
        seed = ast.parse((HERE / "update_carry_over_seed.py").read_text(encoding="utf-8"))
        value = next(node.value for node in seed.body if isinstance(node, ast.Assign)
                     and ast.unparse(node.targets[0]) == "SEEDED_THEME_INNER")
        written = [eval(compile(ast.Expression(e), "seed", "eval")) for e in value.elts]   # numbers and n / 255 only
        stored = [prefs._plain(round(v * 255) / 255) for v in written]   # what a theme's 8 bits give back
        self.assertEqual(stored, contract.SEEDED_THEME[1])

    def test_the_seeded_keymap_is_the_one_the_phase_makes(self):
        self.assertEqual(contract.SEEDED_KEYMAP, ("3D View", "wm.call_menu", "F5", "PRESS", "VIEW3D_MT_add"))
        self.assertEqual(contract.SEEDED_REMOVAL, ("3D View", "view3d.toggle_xray"))
        seeded = ast.unparse(function(Wiring.phases, "seed_keymap"))
        for needed in ("contract.SEEDED_KEYMAP", "item.properties.name = menu", "contract.SEEDED_REMOVAL",
                       "removal.keymap_items.remove(default)", "bpy.ops.wm.save_userpref()"):
            self.assertIn(needed, seeded)


class CandidatePreferences(unittest.TestCase):
    before = {"addons.angelo_agent.reasoning": "high", "themes[Default].ui.inner": [0.21, 0.42, 0.63, 1.0],
              "keymaps.3D View.wm.call_menu#0": ["F5", {"name": "VIEW3D_MT_add"}], "is_dirty": False}

    def test_as_before_with_new_values_listed(self):
        after = dict(self.before, **{"addons.angelo_agent.new_setting": 1, "is_dirty": True})
        verdict, details = contract.candidate_preferences("update", self.before, after, True)
        self.assertEqual(verdict, {"update_preferences_as_before": True})
        self.assertEqual(details, {"changed": [], "missing": [], "new": ["addons.angelo_agent.new_setting"],
                                   "new_keymap_items": []})

    def test_a_stray_keymap_item_fails_on_one_base(self):
        after = dict(self.before, **{"keymaps.3D View.wm.call_menu#1": ["F5", {"name": "VIEW3D_MT_add"}]})
        verdict, details = contract.candidate_preferences("update", self.before, after, True)
        self.assertEqual((verdict["update_preferences_as_before"], details["new_keymap_items"]),
                         (False, ["keymaps.3D View.wm.call_menu#1"]))
        self.assertTrue(contract.candidate_preferences("update", self.before, after, False)[0]["update_preferences_as_before"])

    def test_a_lost_or_changed_value_fails(self):
        for key, value in (("addons.angelo_agent.reasoning", "low"), ("themes[Default].ui.inner", [0.2, 0.2, 0.2, 1.0]),
                           ("keymaps.3D View.wm.call_menu#0", ["F5", {"name": "VIEW3D_MT_snap"}])):
            verdict, details = contract.candidate_preferences("restart", self.before, dict(self.before, **{key: value}), True)
            self.assertEqual(verdict, {"restart_preferences_as_before": False}, key)
            self.assertEqual(details["changed"], [key])
        gone = {k: v for k, v in self.before.items() if k != "keymaps.3D View.wm.call_menu#0"}
        verdict, details = contract.candidate_preferences("update", self.before, gone, True)
        self.assertEqual((verdict["update_preferences_as_before"], details["missing"]), (False, ["keymaps.3D View.wm.call_menu#0"]))
        self.assertFalse(contract.candidate_preferences("update", self.before, {}, True)[0]["update_preferences_as_before"])
        self.assertFalse(contract.candidate_preferences("update", {}, self.before, True)[0]["update_preferences_as_before"])

    def test_across_bases_only_shared_values_are_compared(self):
        gone = {k: v for k, v in self.before.items() if k != "themes[Default].ui.inner"}
        verdict, details = contract.candidate_preferences("update", self.before, gone, False)
        self.assertEqual((verdict, details["gone"]), ({"update_preferences_as_before": True}, ["themes[Default].ui.inner"]))
        changed = dict(self.before, **{"addons.angelo_agent.reasoning": "low"})
        self.assertFalse(contract.candidate_preferences("update", self.before, changed, False)[0]["update_preferences_as_before"])


class Tools(unittest.TestCase):
    def test_the_receipt_names_what_produced_it(self):
        with mock.patch.dict(gate.os.environ, {"ANGELO_SOURCE_COMMIT": "6d7b6de", "ANGELO_GATE_SESSION_TOOLS": '{"session.ps1": "ab"}'}):
            found = gate.tools()
        self.assertEqual(found["source_commit"], "6d7b6de")
        self.assertEqual(found["session"], {"session.ps1": "ab"})
        self.assertEqual(sorted(found["files"]), sorted(gate.GATE_FILES))
        self.assertEqual(found["files"]["update_carry_over_gate.py"],
                         hashlib.sha256((HERE / "update_carry_over_gate.py").read_bytes()).hexdigest())


SEED = {"attachment_sha256": "a" * 64, "snapshot": {"sha256": "s" * 64}, "scene": "/home/Documents/carry-over.blend",
        "preferences": [["edit", "undo_steps", 61], ["view", "show_tooltips_python", True]]}


def seen(**changed):
    value = {"agent_enabled": True, "chat_shown": True, "attachment_sha256": "a" * 64, "snapshot_sha256": "s" * 64,
             "journal_targets": ["/state/workspace/notes.md"], "workspace_notes": "/state/workspace/notes.md",
             "store_issues": [], "preferences": {"edit.undo_steps": 61, "view.show_tooltips_python": True},
             "preferences_saved": True, "recent": "/home/Documents/carry-over.blend\n"}
    value.update(changed)
    return value


class DataChecks(unittest.TestCase):
    def test_everything_here_passes(self):
        verdict = contract.data_checks("restart_", seen(), SEED)
        self.assertEqual(sorted(verdict), sorted("restart_" + n for n in (
            "agent_enabled_with_its_chat", "attachment_opens", "snapshot_restores_byte_for_byte",
            "file_journal_follows_the_workspace", "snapshot_store_sound", "preferences_saved_in_this_version",
            "recent_scene_listed", "preference_undo_steps", "preference_show_tooltips_python")))
        self.assertTrue(all(verdict.values()))

    def test_each_loss_fails_its_check(self):
        for changed, name in (({"agent_enabled": False}, "agent_enabled_with_its_chat"),
                              ({"chat_shown": False}, "agent_enabled_with_its_chat"),
                              ({"attachment_sha256": "b" * 64}, "attachment_opens"),
                              ({"snapshot_sha256": "t" * 64}, "snapshot_restores_byte_for_byte"),
                              ({"journal_targets": ["/elsewhere/notes.md"]}, "file_journal_follows_the_workspace"),
                              ({"journal_targets": []}, "file_journal_follows_the_workspace"),
                              ({"store_issues": ["a blob is missing"]}, "snapshot_store_sound"),
                              ({"preferences": {"edit.undo_steps": 32, "view.show_tooltips_python": True}}, "preference_undo_steps"),
                              ({"preferences": {"edit.undo_steps": 61}}, "preference_show_tooltips_python"),
                              ({"preferences_saved": False}, "preferences_saved_in_this_version"),
                              ({"recent": ""}, "recent_scene_listed")):
            verdict = contract.data_checks("", seen(**changed), SEED)
            self.assertEqual([n for n, ok in verdict.items() if not ok], [name], changed)


class CarryOverUpdate(unittest.TestCase):
    READY = "Your chats, snapshots and settings came over from your earlier Angelo."

    def seen(self, **changed):
        value = {"notice_active": True, "status": "migrated", "outcome": {"app": "ANGELO"}, "sequence": ["splash"],
                 "shown": ("", "Angelo 2.0.0 is ready", self.READY + " More."), "record": {"status": "done", "from": "/s/5.2"},
                 "earlier_has_sessions": False, "earlier_has_readme": True}
        value.update(changed)
        return value

    def test_a_carried_over_update_passes_and_each_miss_fails(self):
        self.assertTrue(all(contract.carry_over_update(self.seen(), "/s/5.2", self.READY).values()))
        for changed, name in (({"notice_active": False}, "the_notice_is_on_screen_in_the_settled_layout"),
                              ({"status": "nothing"}, "state_moved"), ({"outcome": None}, "settings_imported"),
                              ({"outcome": {"exception": "boom"}}, "settings_imported"),
                              ({"sequence": ["setup"]}, "no_setup_card_and_no_quick_setup"),
                              ({"shown": ("", "Angelo", self.READY)}, "notice_says_the_update_is_ready"),
                              ({"shown": ("", "is ready", "Your settings came over.")}, "notice_says_chats_snapshots_and_settings_came_over"),
                              ({"record": {"status": "moving", "from": "/s/5.2"}}, "record_done"),
                              ({"record": {"status": "done", "from": "/other"}}, "record_done"), ({"record": None}, "record_done"),
                              ({"earlier_has_sessions": True}, "earlier_state_folder_gave_up_its_chats"),
                              ({"earlier_has_readme": False}, "earlier_state_folder_says_where_it_went")):
            verdict = contract.carry_over_update(self.seen(**changed), "/s/5.2", self.READY)
            self.assertEqual([n for n, ok in verdict.items() if not ok], [name], changed)


class EarlierWindowAndSignIn(unittest.TestCase):
    def test_the_earlier_window(self):
        self.assertEqual(contract.earlier_window("earlier_open", True, {"a": 1}),
                         {"earlier_app_settled_in_its_window": True, "earlier_app_read_its_preferences": True})
        self.assertEqual(contract.earlier_window("earlier_reopen", False, {}),
                         {"earlier_app_settled_again": False, "earlier_app_read_its_preferences_again": False})
        self.assertFalse(contract.earlier_window("earlier_open", True, {})["earlier_app_read_its_preferences"])

    def test_signed_in_is_angelos_own_status_never_an_exit_code(self):
        for status in contract.SIGNED_IN:
            self.assertTrue(contract.signed_in(status))
        for status in ("Not connected", "Different native billing mode; sign in with ChatGPT", "", None, 0):
            self.assertFalse(contract.signed_in(status), status)


def function(source, name):
    return next(node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef) and node.name == name)


class Wiring(unittest.TestCase):
    """The phases and the host take each verdict from the contract (their behaviour is the contract's, above)."""
    phases = (HERE / "update_carry_over_phases.py").read_text(encoding="utf-8")
    host = (HERE / "update_carry_over_gate.py").read_text(encoding="utf-8")
    seed = (HERE / "update_carry_over_seed.py").read_text(encoding="utf-8")

    def test_phases(self):
        self.assertIn("contract.settled(", ast.unparse(function(self.phases, "settled")))
        self.assertIn("contract.same_base_update(", ast.unparse(function(self.phases, "same_base_checks")))
        self.assertIn("contract.chat_checks(", ast.unparse(function(self.phases, "check_everything_is_here")))
        update = ast.unparse(function(self.phases, "phase_update"))
        self.assertIn("contract.base_mode_matches(SEED['config']", update)
        same = ast.unparse(function(self.phases, "phase_update_same_base"))
        for needed in ("Until(settled", "yield contract.DWELL_S", "same_base_checks('')", "check_everything_is_here('')",
                       "sign_ins()"):
            self.assertIn(needed, same)
        self.assertLess(same.index("Until(settled"), same.index("yield contract.DWELL_S"))
        self.assertLess(same.index("yield contract.DWELL_S"), same.index("same_base_checks('')"))
        restart = function(self.phases, "phase_restart")
        guarded = [node for node in ast.walk(restart) if isinstance(node, ast.If) and ast.unparse(node.test) == "same_base()"]
        self.assertTrue(any("same_base_checks('restart_')" in ast.unparse(node.body) and len(node.body) == 1
                            for node in guarded), "the restart's same-base checks run whenever the base is one")
        self.assertTrue(any("Until(settled" in ast.unparse(node.body) for node in guarded))
        window = ast.unparse(function(self.phases, "earlier_window"))
        for needed in ("Until(earlier_settled", "yield contract.DWELL_S", "values = reading()",
                       "contract.earlier_window(name, earlier_settled(), values)"):
            self.assertIn(needed, window)
        for name in ("check_everything_is_here", "phase_update"):
            body = ast.unparse(function(self.phases, name))
            self.assertIn("contract.data_checks(prefix, seen, SEED)" if name == "check_everything_is_here"
                          else "contract.carry_over_update(seen, SEED['state'], update.READY_ALL)", body)
            self.assertNotRegex(body, r"check\('(attachment|snapshot|preference|record|state_moved|notice)")

    def test_host_and_seed(self):
        main = ast.unparse(function(self.host, "main"))
        for needed in ("app_version(args.from_app", "app_version(args.to_app", "contract.go_back(report['same_base']",
                       "contract.preferences_kept(report['same_base'], rewritten", "'--same-base'"):
            self.assertIn(needed, main)
        self.assertIn("'native_id': chat['native_id']", ast.unparse(ast.parse(self.seed)))
        # One base: the earlier app's window first, and the "before" preferences are its reading after it.
        main = ast.unparse(function(self.host, "main"))
        for needed in ("launch('earlier_open', binary=args.from_app, env=earlier_env)",
                       "before_values = report['phases']['earlier_open'].pop('preference_values', None) or {}",
                       "launch('earlier_reopen', binary=args.from_app, env=earlier_env)",
                       "after_values = report['phases']['earlier_reopen'].pop('preference_values', None) or {}",
                       "report['preference_exclusions'] = dict(update_carry_over_prefs.EXCLUDED)",
                       "contract.signed_in(entry['status'])"):
            self.assertIn(needed, main)
        self.assertIn("'earlier_open': phase_earlier_open", self.phases.replace('"', "'"))
        self.assertIn("'earlier_reopen': phase_earlier_reopen", self.phases.replace('"', "'"))
        for name in ("phase_update", "phase_update_same_base", "phase_restart"):   # the candidate's own reading
            body = ast.unparse(function(self.phases, name))
            self.assertIn("candidate_values()", body, name)
            self.assertLess(body.index("check_everything_is_here("), body.index("candidate_values()"), name)
        window = ast.unparse(function(self.phases, "earlier_window"))
        self.assertIn("if name == 'earlier_open':\n        seed_keymap()", window)
        self.assertLess(window.index("seed_keymap()"), window.index("values = reading()"))
        self.assertIn("update_carry_over_prefs.values(bpy.context.preferences, keyconfigs.user, keyconfigs.default)",
                      ast.unparse(function(self.phases, "reading")))
        self.assertIn("report['preference_values'] = reading()", ast.unparse(function(self.phases, "candidate_values")))
        seeded = ast.unparse(function(self.phases, "seed_keymap"))
        self.assertIn("keymap.keymap_items.new(operator, key, value, ctrl=True, shift=True)", seeded)
        self.assertIn("bpy.ops.wm.save_userpref()", seeded)
        seed = ast.unparse(function(self.seed, "seed"))
        self.assertIn("prefs.themes[0].user_interface.wcol_regular.inner = SEEDED_THEME_INNER", seed)
        self.assertIn("prefs.filepaths.asset_libraries.new(name='Carry-over library'", seed)
        main = ast.unparse(function(self.host, "main"))
        self.assertIn("contract.candidate_preferences(phase, before_values, held, report['same_base'])", main)
        # registered for both candidate windows, each verdict a check of the receipt
        loop = next(node for node in ast.walk(function(self.host, "main")) if isinstance(node, ast.For)
                    and "candidate_preferences" in ast.unparse(node))
        self.assertEqual(ast.unparse(loop.iter), "('update', 'restart')")
        self.assertIn("check(name, ok)", ast.unparse(loop))
        self.assertIn("contract.seeded_in_before(before_values)", main)
        # no preference value is ever stored in the receipt: every reading is popped, go-back's too
        for source in ("report['phases']['earlier_open'].pop('preference_values', None)",
                       "report['phases'][phase].pop('preference_values', None)",
                       "report['phases']['earlier_reopen'].pop('preference_values', None)",
                       "back.pop('preference_values', None)"):
            self.assertIn(source, main)
        self.assertLess(main.index("back.pop('preference_values', None)"), main.index("report['phases']['go_back'] = dict(back"))
        self.assertIn("report['preference_families'] = update_carry_over_prefs.families(before_values)", main)
        self.assertIn("report['tools'] = tools()", main)
        tree = function(self.host, "main")   # one parse: its nodes and their parents
        opens = [node for node in ast.walk(tree) if isinstance(node, ast.Expr) and "launch('earlier_open'" in ast.unparse(node)]
        self.assertEqual(len(opens), 1)
        parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        node = opens[0]
        while id(node) in parents:   # no enclosing condition on the base
            node = parents[id(node)]
            self.assertFalse(isinstance(node, ast.If) and "same_base" in ast.unparse(node.test),
                             "the earlier window opens in both contracts")


if __name__ == "__main__":
    unittest.main()
