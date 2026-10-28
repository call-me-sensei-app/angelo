# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pre-rename names in saved data stay readable and never become executable.

Dependency-free. The pre-rename tool, operator, property and Console names
below are deliberate legacy fixtures (see "Legacy names" in
doc/angelo/native_editor_build.rst).
"""

import ast
import hashlib
import json
import re
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import external_cli_protocol, legacy_names  # noqa: E402
from angelo_agent.invocations import InvocationGuardError, check_invocation, document_digest  # noqa: E402
from angelo_agent.routing import RENAMED_TOOLSET, TOOLSET_VERSION, shared_context  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402
from angelo_agent.task_memory import clean_memory, new_memory, scope_token  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, validate_call  # noqa: E402

LEGACY_TABLE = ROOT / "source/blender/blenkernel/intern/angelo_legacy_names.cc"
ADDON = ROOT / "scripts/addons_core/angelo_agent"


def digest(tool, arguments):
    return hashlib.sha256(json.dumps({"tool": tool, "arguments": arguments}, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def cpp_table(name):
    """``{legacy: current}`` from one frozen table in angelo_legacy_names.cc."""
    source = LEGACY_TABLE.read_text(encoding="utf-8")
    body = source.split("static const LegacyName " + name + "[] = {", 1)[1].split("};", 1)[0]
    return dict(re.findall(r'\{"([^"]+)", "([^"]+)"\}', body))


def current_ui_idnames():
    """Every operator and menu idname the add-on registers, in the stored ``PREFIX_OT_name`` form."""
    names = set()
    for path in ADDON.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {getattr(base, "attr", getattr(base, "id", "")) for base in node.bases}
            idname = next((statement.value.value for statement in node.body if isinstance(statement, ast.Assign)
                           and any(getattr(target, "id", "") == "bl_idname" for target in statement.targets)
                           and isinstance(statement.value, ast.Constant)), None)
            if "Menu" in bases:
                names.add(idname or node.name)
            elif idname and "." in idname:
                prefix, name = idname.split(".", 1)
                names.add(prefix.upper() + "_OT_" + name)
    sculpt = (ROOT / "source/blender/editors/sculpt_paint/mesh").glob("*.*")
    names |= {match for path in sculpt for match in re.findall(r'ot->idname = "(SCULPT_OT_angelo_\w+)"', path.read_text())}
    return names


class ToolNameTests(unittest.TestCase):
    def test_every_tool_has_the_angelo_prefix_and_the_toolset_records_the_rename(self):
        self.assertEqual(len(TOOL_MAP), 76)   # 67, the three web export tools (42) and the six rig tools (43).
        self.assertTrue(all(name.startswith("angelo_") for name in TOOL_MAP))
        # Toolset 16 added angelo_apply_recipe after the rename (toolset 15);
        # toolset 17 added the capabilities lookup of deferred plan schemas;
        # toolset 19 gave Claude short identifiers; 20-22 were the agent stack and
        # (separately) the angelo_flow_* Generation Flow tools; 23 merges both;
        # 24 (local) added the native-coverage kinds and angelo_scene_settings; 26
        # adds flow recipes and subflows; 27 adds adaptive routing's optional
        # "conf" and Codex chat-thread contexts; 28 merges
        # native coverage onto 27.
        # 29 adds phase 5 of native coverage (animation, node_edit, operator_run families).
        # 30 ends Codex chat turns with a typed plan under a strict output schema.
        # 31 adds the eight angelo_team_* tools (offered only while signed in to a team).
        # 32 types every const and enum of the Codex plan schema (the platform API requires it).
        # 33 states the survey's everyday kinds exactly in Claude's plan.
        # 34 adds the two angelo_job_* background-job tools (a deferred family).
        # 35 lets a reviewed script's description be 500 characters instead of 240.
        # 36 adds the live read tools angelo_read_data (exact) and angelo_blend_file (deferred).
        # 37 adds angelo_view's LIT and RENDERED shadings and its region, image and frame arguments.
        # 38 lets angelo_job_start take production bakes and renders, and angelo_view show one of their outputs.
        # 39 adds angelo_view's image, UV, editor, weight, attribute, pass and strip pictures, property_set's
        # shared opt-in and the typed-plan refusals, certification and commit fidelity; within 39,
        # the read-only angelo_script and angelo_execute_python's base and edits (scripts by id; the old form stays
        # valid, so earlier Codex threads keep working: agent_integration.rst, "Toolset 39").
        # 40 numbers those, the job output rule (a taken name gets the next free one) and the Codex
        # code-mode facts, so Codex threads restart with the changed descriptions (agent_integration.rst, "Toolset 40").
        # 41 names Mantaflow fluid bakes in the job guidance; 42 adds the deferred web export tools; 43 was, on two
        # branches, parallel edit's angelo_workers_* family and the six deferred angelo_rig_hand_* tools; 44 is both.
        self.assertEqual((TOOLSET_VERSION, RENAMED_TOOLSET), (44, 15))

    def test_pre_rename_names_are_readable_but_never_executable(self):
        for name in TOOL_MAP:
            legacy = "toonlab_" + name.removeprefix("angelo_")
            self.assertEqual(legacy_names.current_tool_name(legacy), name)
            with self.subTest(legacy=legacy), self.assertRaisesRegex(ValueError, "Unknown Blender tool"):
                validate_call(legacy, {})
        self.assertEqual(legacy_names.current_tool_name("angelo_view"), "angelo_view")
        self.assertIsNone(legacy_names.current_tool_name(None))

    def test_an_old_mutation_id_never_runs_under_the_renamed_tool(self):
        arguments = {"name": "Cube", "size": 1.0, "location": [0, 0, 0]}
        document = repr(("document", 1))
        state = {"invocations": {"version": 1, "closed": False, "entries": {
            "session/call-1": {"digest": digest("toonlab_create_cube", arguments), "document": document_digest(document)}}}}
        with self.assertRaisesRegex(InvocationGuardError, "cannot be reused"):
            check_invocation(state, "session/call-1", digest("angelo_create_cube", arguments), document)
        self.assertFalse(check_invocation(state, "session/call-2", digest("angelo_create_cube", arguments), document))

    def test_handoff_and_task_memory_name_tools_by_their_current_names(self):
        messages = [{"role": "activity", "status": "completed", "tool": "toonlab_create_cube", "result": {"ok": True}}]
        self.assertIn('"tool": "angelo_create_cube"', shared_context(messages))
        self.assertNotIn("toonlab_", shared_context(messages))
        memory = new_memory("profile", "document")
        memory["verified_results"] = [{"id": "host:1", "tool": "toonlab_inspect_scene", "document": scope_token("document"),
                                       "generation": 0, "status": "verified", "summary": {}, "object_handles": [],
                                       "provenance": {"kind": "host_result", "id": "host:1"}, "stale": True}]
        cleaned = clean_memory(memory, profile_id="profile")
        self.assertEqual([entry["tool"] for entry in cleaned["verified_results"]], ["angelo_inspect_scene"])

    def test_native_contexts_from_before_the_rename_are_not_resumed(self):
        with tempfile.TemporaryDirectory() as directory:
            for toolset, expected in ((RENAMED_TOOLSET - 1, None), (RENAMED_TOOLSET, "native-context")):
                with self.subTest(toolset=toolset):
                    store = SessionStore(directory, str(uuid.uuid4()))
                    state = store.new("codex_subscription")
                    state.update(native_id="native-context", toolset_version=toolset, runtime_sessions={
                        "codex_subscription": {"native_id": "native-context", "synced_length": 3, "toolset_version": toolset}})
                    store.save(state)
                    loaded = store.load()
                    self.assertEqual(loaded["native_id"], expected)
                    self.assertEqual(loaded["runtime_sessions"]["codex_subscription"]["native_id"], expected)
                    self.assertEqual(loaded["toolset_version"], toolset, "the handoff still sees the old toolset")


class SavedPropertyTests(unittest.TestCase):
    def test_read_falls_back_to_the_pre_rename_key_and_write_keeps_one_value(self):
        owner = {"_sensei_sculpt_regions_v1": "{\"Cheek\": 1}"}
        keys = legacy_names.SCULPT_REGIONS
        self.assertEqual(legacy_names.present_key(owner, keys), "_sensei_sculpt_regions_v1")
        self.assertEqual(legacy_names.read_property(owner, keys), "{\"Cheek\": 1}")
        legacy_names.write_property(owner, keys, "{}")
        self.assertEqual(owner, {"_angelo_sculpt_regions_v1": "{}"})
        self.assertEqual(legacy_names.read_property({}, legacy_names.WORKSPACE_DIRECTOR, False), False)
        both = {"angelo_director": True, "sensei_director": False}
        self.assertTrue(legacy_names.read_property(both, legacy_names.WORKSPACE_DIRECTOR))
        for keys in (legacy_names.WORKSPACE_DIRECTOR, legacy_names.MANUAL_WORKSPACE, legacy_names.NATIVE_GRAPH,
                     legacy_names.SCULPT_REGIONS, legacy_names.PROGRAM_STAGE):
            self.assertTrue(keys[0].lstrip("_").startswith("angelo_") and keys[1].lstrip("_").startswith("sensei_"))


# Shipped operators later removed. Their rows stay in the frozen table; renaming
# to a missing operator is harmless. Director's input modal was INTERNAL and only
# ever added with modal_handler_add, so no keymap or preference can name it.
RETIRED = {"ANGELO_OT_director_input"}


class NativeTableTests(unittest.TestCase):
    def test_every_legacy_operator_and_menu_maps_to_one_that_exists(self):
        table, current = cpp_table("legacy_idnames"), current_ui_idnames()
        self.assertEqual(len(table), 75)
        self.assertEqual(sorted(table), list(table), "keep the frozen table sorted")
        for legacy, renamed in table.items():
            with self.subTest(legacy=legacy):
                self.assertRegex(legacy, r"^(TOONLAB_(OT|MT)_|SENSEI_(OT|MT)_|SCULPT_OT_sensei_)")
                if renamed in RETIRED:
                    self.assertNotIn(renamed, current, "a retired operator is registered again; drop it from RETIRED")
                    continue
                self.assertIn(renamed, current)
        self.assertEqual(table["TOONLAB_OT_sensei_about"], "ANGELO_OT_about")
        self.assertEqual(table["SCULPT_OT_sensei_sculpt_pass"], "SCULPT_OT_angelo_sculpt_pass")
        self.assertEqual(len(set(table.values())), len(table), "two legacy names never share one target")

    def test_console_languages_add_on_and_property_tables(self):
        self.assertEqual(cpp_table("legacy_console_languages"), {"toonlab": "angelo", "sensei_genai": "angelo_genai"})
        self.assertEqual(cpp_table("legacy_properties"), {"toonlab_preferences_section": "angelo_preferences_section"})
        source = LEGACY_TABLE.read_text()
        self.assertIn('ADDON_MODULE = "angelo_agent"', source)
        self.assertIn('LEGACY_ADDON_MODULE = "toonlab_agent"', source)
        self.assertIn("angelo_preferences_section", (ADDON / "preferences_ui.py").read_text())
        readfile = (ROOT / "source/blender/blenloader/intern/readfile.cc").read_text()
        self.assertIn("BKE_angelo_legacy_userdef_update(user);", readfile)
        console = (ROOT / "source/blender/editors/space_console/space_console.cc").read_text()
        self.assertIn("BKE_angelo_legacy_console_language(sconsole->language)", console)
        keymaps = (ROOT / "source/blender/makesrna/intern/rna_wm_api.cc").read_text()
        self.assertIn("BKE_angelo_legacy_idname(idname_bl)", keymaps)


class ExternalProtocolTests(unittest.TestCase):
    def test_protocol_two_signs_with_angelo_contexts(self):
        self.assertEqual(external_cli_protocol.CLI_PROTOCOL, 2)
        self.assertEqual(external_cli_protocol._CLIENT_CONTEXT, "angelo-cli/2/client")
        self.assertEqual(external_cli_protocol._SERVER_CONTEXT, "angelo-cli/2/server")


if __name__ == "__main__":
    unittest.main()
