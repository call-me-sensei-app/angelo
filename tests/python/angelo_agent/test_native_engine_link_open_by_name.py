# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""OPEN by an asset's plain name, and refusals a typed plan corrects (doc/angelo/engine_link.rst, "The agent"), on the
real ``engine_link_tool_host``, ``engine_link_assets`` and ``claude_outcomes``.

Covered (engine-link-compare harness check 3, 2026-11-03: GPT 6.1 Sol asked to "Open the barrel" passed "barrel", was
refused as outside the project, and the plan ended the request with no reply): a person names an asset by its file
name, so OPEN finds it in the connected project when exactly one has that name and names them all when several do;
the lookup reads no hidden folder, link or Angelo's working folder and stops at its bound. A refusal raised before the
tool did anything says nothing changed, so a plan returns it to the model; one after an effect never does.
"""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_engine_link_tool as base  # noqa: E402  (its stand-in bpy and fixtures)

engine_link, host, assets, EDIT, RefusalError = base.engine_link, base.host, base.assets, base.EDIT, base.RefusalError


def plan_correction(error):
    """What a typed plan returns to the model for this refusal (None: the request ends)."""
    from angelo_agent.claude_outcomes import correctable_failure
    from angelo_agent.refusal_codes import error_result
    return correctable_failure([{"tool": EDIT, "arguments": {"action": "OPEN", "asset": "x"}}], [error_result(error)])


class OpenByNameTests(unittest.TestCase):
    setUp, asked = base.ToolTests.setUp, base.ToolTests.asked   # Its fixtures, without running its tests again.

    def place(self, *names, project=None):
        for name in names:
            path = (project or self.project) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")

    def open(self, asset, **request):
        return host.execute(None, EDIT, dict(request, action="OPEN", asset=asset))

    def test_a_plain_name_opens_the_one_asset_with_it(self):
        self.place("props/barrel.glb")
        self.server.editor(1)
        events = self.asked()
        for spoken in ("barrel", "Barrel", "barrel.glb", "BARREL.GLB"):
            with self.subTest(spoken=spoken):
                engine_link._state.update(pending=None, agent_policy=base.facts.new_policy())
                result = self.open(spoken)
                self.assertEqual(result["asked"], "res://props/barrel.glb")
        self.assertEqual(set(events), {"res://props/barrel.glb"})

    def test_several_assets_with_the_name_are_named_and_nothing_is_asked(self):
        self.place("barrel.glb", "props/barrel.blend")
        self.server.editor(1)
        events = self.asked()
        with self.assertRaises(RefusalError) as refused:
            self.open("barrel")
        self.assertEqual(refused.exception.code, "VALIDATION")
        self.assertIn("res://barrel.glb, res://props/barrel.blend", str(refused.exception))
        self.assertEqual(events, [])

    def test_hidden_folders_links_and_angelos_working_folder_are_never_read(self):
        outside = Path(tempfile.mkdtemp()).resolve()
        self.place("barrel.glb", project=outside)
        self.place(".angelo/barrel.glb.blend", ".godot/imported/barrel.glb", "notes/barrel.txt")
        (self.project / "linked").symlink_to(outside, target_is_directory=True)
        os.symlink(outside / "barrel.glb", self.project / "barrel.glb")
        self.server.editor(1)
        for spoken in ("barrel", "barrel.glb"):
            with self.subTest(spoken=spoken), self.assertRaises(RefusalError) as refused:
                self.open(spoken)
            self.assertIn("no asset of the project is named", str(refused.exception))
            self.assertNotIn(str(outside), str(refused.exception))
        self.assertIsNone(engine_link.pending())

    def test_a_lookup_that_stopped_at_its_bound_never_counts_as_unique(self):
        """Break-it 8-2: one match read before the bound may not be the only one."""
        self.place("z/barrel.glb", *("y/f%02d.png" % index for index in range(30)), "a/barrel.glb")   # z is read first.
        self.server.editor(1)
        with mock.patch.object(assets, "NAME_SCAN", 10), self.assertRaises(RefusalError) as refused:
            self.open("barrel")
        self.assertIn("couldn't be read whole", str(refused.exception))
        self.assertIsNone(engine_link.pending())
        self.assertEqual(self.open("res://a/barrel.glb")["asked"], "res://a/barrel.glb")   # The engine's name still works.

    def test_a_name_unsure_in_one_project_is_never_settled_by_another(self):
        """Break-it 8-1: several in one project and one in another is as ambiguous as one in each."""
        other = Path(tempfile.mkdtemp()).resolve()
        (other / "project.godot").write_text("config_version=5\n")
        self.place("barrel.glb", "props/barrel.blend")
        self.place("barrel.glb", project=other)
        self.server.editor(1)
        self.server.editor(2, project=other)
        self.server.info[2]["project_name"] = "Other"
        with self.assertRaises(RefusalError) as refused:
            self.open("barrel")
        self.assertIn("res://props/barrel.blend", str(refused.exception))
        self.assertIn("(Other): res://barrel.glb", str(refused.exception))
        self.assertIsNone(engine_link.pending())
        self.assertEqual(self.open("barrel", project="Other")["asked"], "res://barrel.glb")

    def test_a_long_list_of_matches_is_named_whole_and_counted(self):
        """Break-it 8-7: never a name cut in the middle."""
        deep = "/".join(["folder-with-a-long-name-%02d" % level for level in range(4)])
        self.place(*("%s/v%02d/barrel.glb" % (deep, index) for index in range(20)))
        self.server.editor(1)
        with self.assertRaises(RefusalError) as refused:
            self.open("barrel")
        text = str(refused.exception)
        self.assertRegex(text, r"and \d+ more")
        listed = re.findall(r"res://[^,;]*", text)
        self.assertTrue(listed and all(re.fullmatch(r"res://\S+/barrel\.glb", ident.split(" and ")[0])
                                       for ident in listed), listed)

    def test_folders_the_engine_ignores_are_never_offered(self):
        """Break-it 8-8: Godot's .gdignore and Unity's trailing ~."""
        self.place("exports/.gdignore", "exports/barrel.glb", "props/barrel.glb")
        self.server.editor(1)
        self.assertEqual(self.open("barrel")["asked"], "res://props/barrel.glb")
        unity = Path(tempfile.mkdtemp()).resolve()
        self.place("Assets/Old~/Barrel.fbx", "Assets/Models/Barrel.fbx", "ProjectSettings/ProjectVersion.txt",
                   project=unity)
        engine_link._state.update(pending=None)
        self.server.info.clear()
        self.server.editor(1, engine="unity", project=unity)
        self.assertEqual(self.open("barrel")["asked"], "Assets/Models/Barrel.fbx")

    def test_names_in_any_script_are_found_and_spaces_of_any_width_are_names(self):
        """Break-it 8b-1: an ideographic or no-break space, or an emoji joiner, is part of a person's name."""
        self.place("\u6a3d\u3000\u5927.glb", "Props\u3000Old/barrel.glb", "props/barrel.glb")
        self.server.editor(1)
        self.assertEqual(self.open("\u6a3d\u3000\u5927")["asked"], "res://\u6a3d\u3000\u5927.glb")
        engine_link._state.update(pending=None, agent_policy=base.facts.new_policy())
        with self.assertRaises(RefusalError) as refused:
            self.open("barrel")
        self.assertIn("res://Props\u3000Old/barrel.glb", str(refused.exception))

    def test_what_the_lookup_cant_read_or_name_makes_it_unsure(self):
        """Break-it 8b-1 and 8b-2: a match it can't name, or a folder it can't read, may be the other barrel."""
        self.place("evil\nfolder/barrel.glb", "props/barrel.glb")
        self.server.editor(1)
        with self.assertRaises(RefusalError) as refused:
            self.open("barrel")
        self.assertIn("couldn't be read whole", str(refused.exception))
        self.assertNotIn("\n", str(refused.exception))
        (self.project / "evil\nfolder/barrel.glb").unlink()
        self.place("locked/barrel.glb")
        os.chmod(self.project / "locked", 0)
        self.addCleanup(os.chmod, self.project / "locked", 0o755)
        if os.access(self.project / "locked", os.R_OK):
            self.skipTest("this account reads every folder")   # root
        with self.assertRaises(RefusalError) as refused:
            self.open("barrel")
        self.assertIn("couldn't be read whole", str(refused.exception))
        self.assertIsNone(engine_link.pending())

    def test_the_unclear_refusal_says_what_to_do_once(self):
        """Break-it 8b-3 and 8b-4: one instruction; engine and project only when several projects are connected; a
        single reason too long for the budget is cut, never dropped."""
        self.place("barrel.glb", "props/barrel.glb")
        self.server.editor(1)
        with self.assertRaises(RefusalError) as refused:
            self.open("barrel")
        self.assertEqual(str(refused.exception).count("Name it"), 1)
        self.assertNotIn("engine and project", str(refused.exception))
        with self.assertRaises(RefusalError) as refused:
            self.open("y" * 1000)
        self.assertIn("y" * 600, str(refused.exception))
        self.assertNotIn("and 1 more", str(refused.exception))

    def test_a_name_matches_in_any_unicode_form(self):
        """Break-it 8-8: macOS may store a decomposed name; a person types the composed one."""
        self.place("caf\u0065\u0301.glb")
        self.server.editor(1)
        self.assertEqual(self.open("caf\u00e9")["asked"], "res://caf\u0065\u0301.glb")

    def test_links_junctions_and_unreadable_entries_are_skipped(self):
        """Break-it 8-4 and 8-5: a Windows junction is no symlink; an entry whose type can't be read is left out and
        reported unread (None), which makes the lookup unsure."""
        class Entry:
            def __init__(self, name, junction=False, failing=False):
                self.name, self.path, self.junction, self.failing = name, "/nowhere/" + name, junction, failing

            def is_symlink(self):
                if self.failing:
                    raise PermissionError(13, "Permission denied", self.path)
                return False

            def is_junction(self):
                return self.junction

            def stat(self, follow_symlinks=True):
                return os.stat_result((0,) * 10)

            def is_dir(self, follow_symlinks=True):
                return True

        adapter = base.godot_link_assets
        self.assertTrue(assets._skipped(Entry("linked", junction=True), adapter))
        self.assertIsNone(assets._skipped(Entry("locked", failing=True), adapter))
        self.assertFalse(assets._skipped(Entry("props"), adapter))

    def test_a_path_shaped_name_is_never_looked_up_by_name(self):
        self.place("props/barrel.glb")
        self.server.editor(1)
        for spoken in ("props/barrel", "C:barrel", "..\\barrel"):
            with self.subTest(spoken=spoken), self.assertRaises(RefusalError):
                self.open(spoken)

    def test_unity_names_it_under_assets(self):
        unity = Path(tempfile.mkdtemp()).resolve()
        self.place("Assets/Models/Barrel.fbx", "ProjectSettings/ProjectVersion.txt", "Library/barrel.fbx",
                   project=unity)
        self.server.editor(1, engine="unity", project=unity)
        self.assertEqual(self.open("barrel")["asked"], "Assets/Models/Barrel.fbx")

    def test_a_refusal_before_any_effect_goes_back_to_the_model(self):
        self.server.editor(1)
        with self.assertRaises(RefusalError) as refused:
            self.open("chair")
        self.assertIn("chair", plan_correction(refused.exception))
        engine_link._state["server"] = None   # The link off: refused at once, nothing asked.
        with self.assertRaises(RefusalError) as refused:
            self.open("chair")
        self.assertIsNotNone(plan_correction(refused.exception))

    def test_a_refusal_no_retry_can_change_ends_the_request_with_its_reason(self):
        """Break-it 8-3: Edit Mode, no window, the wrong open file: a correction would only ask for another card."""
        def in_edit_mode(*_args):
            raise host._final(RefusalError("Angelo is in Edit Mode", code="STALE_CONTEXT"))
        with mock.patch.object(host, "_execute", in_edit_mode), self.assertRaises(RefusalError) as refused:
            host.execute(None, EDIT, {"action": "PUSH"})
        self.assertIsNone(plan_correction(refused.exception))
        from angelo_agent.refusal_codes import error_result
        from angelo_agent.request_recovery import unchanged
        self.assertTrue(unchanged(error_result(refused.exception)), "it changed nothing: no recovery hold")
        source = Path(host.__file__).read_text(encoding="utf-8")
        for reason in ("No window shows the linked scene", "in the middle of a tool", "no window to save from",
                       "The open file is not the linked asset"):
            with self.subTest(reason=reason):
                self.assertRegex(source, r"raise _final\(RefusalError\(\"[^\"]*" + re.escape(reason))

    def test_a_refusal_after_an_effect_ends_the_request(self):
        def failed_after_sending(*_args):
            raise RefusalError("The live update wasn't sent: no editor took it", code="INTERNAL_ERROR")
        with mock.patch.object(host, "_execute", failed_after_sending), self.assertRaises(RefusalError) as refused:
            host.execute(None, EDIT, {"action": "PUSH"})
        self.assertIsNone(plan_correction(refused.exception))


if __name__ == "__main__":
    unittest.main()
