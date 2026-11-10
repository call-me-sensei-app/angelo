# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine preflight's contracts without Blender (doc/angelo/engine_preflight.rst): each rule fires for the
targets and formats its engine source covers and no other, every finding states what it looked for and found with
its source and versions, the name conventions match the engines' import code, the tools' arguments, and the tools'
place in the families and the MCP levels."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import importlib
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import engine_rules  # noqa: E402
from angelo_agent.engine_check_spec import CHECK, FIX, ENGINE_NAMES, validate_engine  # noqa: E402
from angelo_agent.engine_rules import evaluate, godot_token, unreal_body_matches  # noqa: E402

PROFILES = {   # The exporter options engine_preflight.profile gives each target (the rules read only these keys).
    ("GODOT", "GLB"): {"export_apply": True, "export_extras": True, "export_all_influences": True},
    ("UNITY", "FBX"): {"use_mesh_modifiers": True, "use_custom_props": True},
    ("UNITY", "GLB"): {"export_apply": True},
    ("UNREAL", "GLB"): {"export_apply": True},
    ("UNREAL", "FBX"): {"use_mesh_modifiers": True, "use_custom_props": False},
    ("WEB", "GLB"): {"use_visible": True, "export_apply": True, "export_extras": False},
}


def obj(name, kind="MESH", **fields):
    return dict({"name": name, "type": kind, "data": name + "Data", "parent": None, "hidden": False,
                 "rotation_deg": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0], "constraints": [],
                 "bone_constraints": [], "animated": False, "actions": [], "modifiers": [], "faces": None,
                 "geometry_key": None, "link_safe": True, "apply_scale_safe": True, "empty_display": None,
                 "path_properties": []}, **fields)


def facts(*objects, armatures=(), actions=(), images=(), influences=(), unit_scale=1.0):
    return {"scene": {"unit_scale": unit_scale, "unit_system": "METRIC"},
            "exported": [item["name"] for item in objects], "objects": list(objects), "armatures": list(armatures),
            "actions": list(actions), "images": list(images), "influences": list(influences)}


def armature(name, roots=("Root",), leaves=3, at_root=True, **fields):
    return dict({"name": name, "roots": list(roots), "leaves": leaves, "at_root": at_root, "rename_safe": True,
                 "transform_fix_safe": True}, **fields)


def codes(scene, target, fmt):
    return {finding["code"] for finding in evaluate(scene, target, fmt, PROFILES[(target, fmt)])}


def where(scene, code):
    """{(target, format)} whose findings include ``code``."""
    return {pair for pair in PROFILES if code in codes(scene, *pair)}


ALL = set(PROFILES)
GLB = {pair for pair in PROFILES if pair[1] == "GLB"}
FBX = {pair for pair in PROFILES if pair[1] == "FBX"}


class RuleScopeTests(unittest.TestCase):
    """Each rule fires where its engine source says, and nowhere else."""

    def test_unit_scale_matters_to_gltf_only(self):
        scene = facts(obj("Box"), unit_scale=0.01)
        self.assertEqual(where(scene, "unit_scale"), GLB)   # FBX applies the unit scale (apply_unit_scale).
        finding = next(item for item in evaluate(scene, "GODOT", "GLB", PROFILES[("GODOT", "GLB")])
                       if item["code"] == "unit_scale")
        self.assertIn("100", finding["message"])

    def test_armature_transform_everywhere_and_its_fix_follows_safety(self):
        scene = facts(obj("Rig", "ARMATURE", scale=[100.0, 100.0, 100.0]), armatures=[armature("Rig")])
        self.assertEqual(where(scene, "transform_unapplied"), ALL)
        unsafe = facts(obj("Rig", "ARMATURE", rotation_deg=[90.0, 0, 0]),
                       armatures=[armature("Rig", transform_fix_safe=False)])
        finding = next(item for item in evaluate(unsafe, "UNITY", "FBX", PROFILES[("UNITY", "FBX")])
                       if item["code"] == "transform_unapplied")
        self.assertIsNone(finding["fixable"])

    def test_unreal_fbx_drops_only_a_root_node_named_armature_with_one_child(self):
        rig = obj("Rig", "ARMATURE")
        self.assertEqual(where(facts(rig, armatures=[armature("Rig")]), "armature_extra_root"), {("UNREAL", "FBX")})
        for name in ("Armature", "armature", "ARMATURE"):
            self.assertEqual(where(facts(obj(name, "ARMATURE"), armatures=[armature(name)]),
                                   "armature_extra_root"), set(), name)
        parented = facts(obj("Armature", "ARMATURE"), armatures=[armature("Armature", at_root=False)])
        self.assertEqual(where(parented, "armature_extra_root"), {("UNREAL", "FBX")})
        two = facts(obj("Armature", "ARMATURE"), armatures=[armature("Armature", roots=("Hips", "Prop"))])
        self.assertEqual(where(two, "armature_extra_root"), {("UNREAL", "FBX")})
        self.assertEqual({pair[0] for pair in where(two, "root_bones")}, {"UNREAL"})

    def test_leaf_bones_are_an_fbx_fact(self):
        scene = facts(obj("Rig", "ARMATURE"), armatures=[armature("Rig", leaves=5)])
        self.assertEqual(where(scene, "leaf_bones"), FBX)

    def test_constraints_warn_where_nothing_samples_them(self):
        still = facts(obj("Lamp", "MESH", constraints=["TRACK_TO"]))
        for pair in PROFILES:   # Nothing moves: the file holds the constraint's result.
            found = [item for item in evaluate(still, *pair, PROFILES[pair]) if item["code"] == "constraints_live"]
            self.assertEqual([item["severity"] for item in found], ["info"], pair)
        scene = facts(obj("Lamp", "MESH", constraints=["TRACK_TO"]), obj("Target", "EMPTY", animated=True))
        for target, fmt in PROFILES:
            found = [item for item in evaluate(scene, target, fmt, PROFILES[(target, fmt)])
                     if item["code"] == "constraints_live"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["severity"], "info" if fmt == "FBX" else "warn", (target, fmt))

    def test_unitys_fbx_profile_writes_one_take(self):
        scene = facts(obj("Rig", "ARMATURE", actions=["Walk", "Run"]), armatures=[armature("Rig")])
        self.assertEqual(where(scene, "actions_one_take"), {("UNITY", "FBX")})

    def test_an_action_without_users_is_lost_on_save_everywhere(self):
        scene = facts(obj("Rig", "ARMATURE"), armatures=[armature("Rig")],
                      actions=[{"name": "Jump", "users": 0, "fake_user": False, "owners": ["Rig"], "cyclic": False}])
        self.assertEqual(where(scene, "action_unsaved"), ALL)
        kept = facts(obj("Rig", "ARMATURE"), armatures=[armature("Rig")],
                     actions=[{"name": "Jump", "users": 1, "fake_user": True, "owners": ["Rig"], "cyclic": False}])
        self.assertEqual(where(kept, "action_unsaved"), set())

    def test_godot_loop_names_follow_its_importer(self):
        def scene(name):
            return facts(obj("Rig", "ARMATURE"), armatures=[armature("Rig")],
                         actions=[{"name": name, "users": 1, "fake_user": False, "owners": ["Rig"], "cyclic": True}])
        self.assertEqual(where(scene("Walk"), "loop_name"), {("GODOT", "GLB")})
        for name in ("Walk-loop", "Walk_loop", "Walk-cycle", "walk_LOOP", "Walk-loop.001", "Run$loop"):
            self.assertEqual(where(scene(name), "loop_name"), set(), name)

    def test_shading_modifier_only_when_the_export_wont_apply_it(self):
        modifier = {"name": "Smooth by Angle", "type": "NODES", "viewport": True, "smooth": True, "apply_safe": True}
        scene = facts(obj("Crate", modifiers=[modifier]))
        self.assertEqual(where(scene, "smooth_modifier"), set())
        off = facts(obj("Crate", modifiers=[dict(modifier, viewport=False)]))
        self.assertEqual(where(off, "smooth_modifier"), ALL)
        unapplied = evaluate(scene, "GODOT", "GLB", {"export_apply": False})
        self.assertIn("smooth_modifier", {item["code"] for item in unapplied})

    def test_faceless_curves(self):
        scene = facts(obj("Path", "CURVE", faces=0), obj("Logo", "FONT", faces=120))
        found = evaluate(scene, "WEB", "GLB", PROFILES[("WEB", "GLB")])
        self.assertEqual([(item["code"], item["subject"]) for item in found], [("curve_no_surface", "Path")])

    def test_identical_meshes_in_separate_data(self):
        scene = facts(obj("Post", geometry_key="k"), obj("Post.001", data="Other", geometry_key="k"),
                      obj("Shared", data="PostData", geometry_key="k"))
        finding = next(item for item in evaluate(scene, "WEB", "GLB", PROFILES[("WEB", "GLB")])
                       if item["code"] == "instancing_lost")
        self.assertIn("1 extra", finding["message"])
        self.assertEqual(finding["fixable"], "link_data")

    def test_hidden_objects_are_written_by_engine_profiles_not_by_the_web(self):
        scene = facts(obj("Helper", hidden=True))
        self.assertEqual(where(scene, "hidden_exported"), ALL - {("WEB", "GLB")})

    def test_fbx_records_local_image_paths(self):
        image = {"name": "Albedo", "packed": False, "used_by_export": True, "file": True, "roles": ["colour"],
                 "colorspace": "sRGB"}
        self.assertEqual(where(facts(obj("Box"), images=[image]), "absolute_paths"), FBX)
        self.assertEqual(where(facts(obj("Box"), images=[dict(image, packed=True)]), "absolute_paths"), set())

    def test_custom_property_paths_only_where_extras_are_written(self):
        scene = facts(obj("Box", path_properties=["source"]))
        self.assertEqual(where(scene, "path_properties"), {("GODOT", "GLB"), ("UNITY", "FBX")})

    def test_collision_names_by_engine(self):
        self.assertEqual(where(facts(obj("Wall-colonly", "EMPTY", empty_display="CUBE")), "collision_empty"),
                         {("GODOT", "GLB")})
        self.assertEqual(where(facts(obj("Wall-colonly")), "collision_empty"), set())   # A mesh works.
        omi = facts(obj("Wall-colonly", "EMPTY", empty_display="CUBE", omi_physics=True))
        self.assertEqual(where(omi, "collision_empty"), set())   # Written as an OMI collider (game_physics).
        godot = facts(obj("Wall-col"))
        self.assertEqual({pair[0] for pair in where(godot, "collision_foreign")}, {"UNITY", "UNREAL", "WEB"})
        unreal = facts(obj("House"), obj("UCX_House_01"))
        self.assertEqual({pair[0] for pair in where(unreal, "collision_foreign")}, {"GODOT", "UNITY", "WEB"})
        self.assertEqual(where(unreal, "collision_unmatched"), set())
        stray = facts(obj("House"), obj("UCX_Barn"))
        self.assertEqual({pair[0] for pair in where(stray, "collision_unmatched")}, {"UNREAL"})
        web = next(item for item in evaluate(godot, "WEB", "GLB", PROFILES[("WEB", "GLB")])
                   if item["code"] == "collision_foreign")
        self.assertEqual(web["fixable"], "hide_collider")

    def test_godot_collision_mesh_with_uneven_scale(self):
        scene = facts(obj("Floor-colonly", scale=[4.0, 4.0, 0.1]))
        self.assertEqual(where(scene, "collider_scale"), {("GODOT", "GLB")})

    def test_influences_against_each_engines_limit(self):
        skin = {"object": "Body", "most": 9, "over": {"4": 120, "8": 6, "12": 0},
                "dropped": {"4": 0.21, "8": 0.02, "12": 0.0}, "sampled": False, "limit_safe": True}
        scene = facts(obj("Body"), influences=[skin])
        found = {pair: next((item for item in evaluate(scene, *pair, PROFILES[pair]) if item["code"] == "influences"),
                            None) for pair in PROFILES}
        self.assertIn("more than 4", found[("WEB", "GLB")]["message"])
        self.assertIn("more than 8", found[("GODOT", "GLB")]["message"])   # Godot's profile writes all; it reads 8.
        self.assertEqual(found[("GODOT", "GLB")]["severity"], "info")      # Only 0.02 is lost there.
        self.assertIn("more than 4", found[("UNITY", "FBX")]["message"])
        self.assertIn("more than 4", found[("UNREAL", "GLB")]["message"])   # glTF's exporter keeps 4 by default.
        self.assertIsNone(found[("UNREAL", "FBX")])                        # FBX keeps all; Unreal renders 12.

    def test_colour_space_by_role(self):
        normal = {"name": "Normal", "packed": True, "used_by_export": True, "file": False, "roles": ["data"],
                  "colorspace": "sRGB"}
        self.assertEqual(where(facts(obj("Box"), images=[normal]), "colour_space"), ALL)
        mixed = dict(normal, roles=["colour", "data"])
        self.assertEqual(where(facts(obj("Box"), images=[mixed]), "colour_space"), set())
        aces = dict(normal, colorspace="ACEScg")
        self.assertEqual(where(facts(obj("Box"), images=[aces]), "colour_space"), set())

    def test_objects_not_exported_never_produce_findings(self):
        scene = facts(obj("Rig", "ARMATURE", scale=[2, 2, 2]), armatures=[armature("Rig")])
        scene["exported"] = []
        for pair in PROFILES:
            self.assertEqual(evaluate(scene, *pair, PROFILES[pair]), [])

    def test_unsupported_format_is_refused(self):
        with self.assertRaises(ValueError):
            evaluate(facts(), "GODOT", "FBX", {})


class FindingShapeTests(unittest.TestCase):
    def test_every_finding_reports_facts_source_and_versions(self):
        scene = facts(obj("Rig", "ARMATURE", scale=[100, 100, 100], constraints=["COPY_LOCATION"],
                          actions=["A", "B"]), obj("UCX_Barn"), obj("Path", "CURVE", faces=0),
                      armatures=[armature("Rig", roots=("Hips", "Prop"))], unit_scale=0.01)
        seen = set()
        for pair in PROFILES:
            for finding in evaluate(scene, *pair, PROFILES[pair]):
                seen.add(finding["code"])
                self.assertTrue(finding["facts"]["looked_for"] and finding["facts"]["found"], finding)
                self.assertIn(finding["source"], engine_rules.SOURCES.values())
                self.assertRegex(finding["versions"], r"\d")
                self.assertIn(finding["severity"], engine_rules.SEVERITIES)
                self.assertNotIn("{", finding["message"] + finding["fix"])
                self.assertIn(finding["fixable"], set(engine_rules.FIXES.values()) | {None})
        self.assertGreater(len(seen), 8)

    def test_every_rule_names_a_source_and_every_fix_a_rule(self):
        for code, rule in engine_rules.RULES.items():
            self.assertIn(rule[3], engine_rules.SOURCES, code)
            self.assertTrue(set(rule[4]) <= set(engine_rules.TARGETS), code)
        self.assertTrue(set(engine_rules.FIXES) <= set(engine_rules.RULES))

    def test_versions(self):
        self.assertTrue(engine_rules.in_range("GODOT", "4.7.2"))
        self.assertTrue(engine_rules.in_range("UNITY", "6000.5.6f1"))
        self.assertFalse(engine_rules.in_range("GODOT", "3.6"))
        self.assertFalse(engine_rules.in_range("UNREAL", "5.9"))
        self.assertTrue(engine_rules.in_range("WEB", None))


class ConventionTests(unittest.TestCase):
    """The name matching follows the engines' import code (resource_importer_scene.cpp, InterchangeLODDataParser)."""

    def test_godot_suffixes(self):
        cases = {"Wall-col": "col", "Wall_colonly": "colonly", "Rock-convcolonly": "convcolonly",
                 "Rock-convcol.001": "convcol", "Wall$colonly": "colonly", "Crate-rigid": "rigid",
                 "Wall-COLONLY 2": "colonly", "Column": None, "Colonnade": None, "col": None, "Wall-collar": None}
        for name, token in cases.items():
            self.assertEqual(godot_token(name), token, name)

    def test_unreal_bodies(self):
        self.assertTrue(unreal_body_matches("UCX_House", ["House"]))
        self.assertTrue(unreal_body_matches("UCX_House_01", ["House"]))
        self.assertTrue(unreal_body_matches("UBX_house", ["House"]))   # FString == ignores case.
        self.assertTrue(unreal_body_matches("UCX_Big_House_02", ["Big_House"]))
        self.assertFalse(unreal_body_matches("UCX_Barn", ["House"]))


class VersionTests(unittest.TestCase):
    """A rule's version range changes what it reports (resource_importer_scene.cpp 4.0.4 against 4.1 and later)."""

    def test_godot_4_0_does_not_read_a_suffix_before_blenders_number(self):
        self.assertEqual(godot_token("Wall-colonly.001"), "colonly")
        self.assertIsNone(godot_token("Wall-colonly.001", numbered=False))
        self.assertEqual(godot_token("Wall-colonly", numbered=False), "colonly")
        scene = facts(obj("Wall-colonly.001", "EMPTY", empty_display="CUBE"))
        profile = PROFILES[("GODOT", "GLB")]
        old = {item["code"]: item for item in evaluate(scene, "GODOT", "GLB", profile, "4.0.4")}
        self.assertIn("suffix_numbered", old)
        self.assertEqual(old["suffix_numbered"]["versions"], "Godot 4.0 to 4.0")
        self.assertNotIn("collision_empty", old)
        for version in ("4.1", "4.7.2", None):
            found = {item["code"] for item in evaluate(scene, "GODOT", "GLB", profile, version)}
            self.assertEqual(found, {"collision_empty"}, version)

    def test_godot_4_0_loop_names(self):
        scene = facts(obj("Rig", "ARMATURE"), armatures=[armature("Rig")],
                      actions=[{"name": "Idle-loop.001", "users": 1, "fake_user": False, "owners": ["Rig"],
                                "cyclic": True}])
        profile = PROFILES[("GODOT", "GLB")]
        self.assertIn("loop_name", {item["code"] for item in evaluate(scene, "GODOT", "GLB", profile, "4.0")})
        self.assertNotIn("loop_name", {item["code"] for item in evaluate(scene, "GODOT", "GLB", profile, "4.3")})


class EverydayPathTests(unittest.TestCase):
    def test_no_preflight_module_runs_by_itself(self):
        """No handler, timer, draw callback or registration: the preflight runs only when a tool, an export or a
        save calls it (doc/angelo/engine_preflight.rst, "Where it runs")."""
        for path in sorted(ADDON.glob("engine_*.py")):
            if path.stem in {"engine_detect", "engine_setup", "engine_status", "engine_suggest", "engine_suggest_ui"} \
                    or path.stem.startswith("engine_link"):
                continue
            source = path.read_text(encoding="utf-8")
            for hook in ("app.handlers", "app.timers", "draw_handler_add", "def register", "msgbus"):
                self.assertNotIn(hook, source, "%s: %s" % (path.name, hook))


class WebReadBackTests(unittest.TestCase):
    """The web export's read-back result reaches the chat that asked: a notice now, the observation once."""

    class Chat:
        def __init__(self):
            self.rows = []

        def emit(self, kind, data):
            self.rows.append((kind, data))

    def test_a_result_is_a_notice_and_one_observation(self):
        import weakref
        from angelo_agent import web_readback
        chat = self.Chat()
        job = {"controller": weakref.ref(chat), "written": "web/scene.glb", "revision": 7}
        web_readback._deliver(job, {"findings": [{"code": "reimport_weights", "severity": "warn", "subject": "Body",
                                                  "message": "m", "facts": {"looked_for": "a", "found": "b"}}],
                                    "ms": 12.0})
        web_readback._deliver(job, {"findings": [], "ms": 9.0})
        web_readback._deliver(job, None)
        self.assertEqual([data["i18n"][0] for _, data in chat.rows],
                         [web_readback.DIFFERS, web_readback.MATCHES, web_readback.FAILED])
        self.assertTrue(all(kind == "message" and data["role"] == "system" for kind, data in chat.rows))
        taken = web_readback.for_request(chat)
        self.assertEqual([entry["status"] for entry in taken], ["done", "done", "failed"])
        self.assertEqual(taken[0]["file"], "web/scene.glb")
        self.assertEqual({entry["revision"] for entry in taken}, {7})
        self.assertEqual(taken[0]["rules"][0]["code"], "reimport_weights")   # Grouped, as every tool result.
        self.assertNotIn("findings", taken[0])
        self.assertIsNone(web_readback.for_request(chat))   # Once.

    def test_a_replaced_read_back_ends_said_so_and_leaves_no_folder(self):
        import tempfile
        import weakref
        from angelo_agent import web_readback
        chat = self.Chat()
        runner = web_readback._Runner("angelo")
        runner.running = True   # One is running: the next waits, and a newer one replaces it.
        folders = [tempfile.mkdtemp(prefix="angelo-preflight-test-") for _ in range(2)]
        jobs = [{"controller": weakref.ref(chat), "written": "w%d.glb" % index, "revision": index + 1,
                 "folder": folder, "spec": "", "result": ""} for index, folder in enumerate(folders)]
        runner.request(jobs[0])
        runner.request(jobs[1])
        self.assertIs(runner.queued, jobs[1])
        self.assertFalse(Path(folders[0]).exists())
        job, result = runner.done.get_nowait()
        self.assertEqual((job["revision"], result), (1, {"superseded_by": 2}))
        web_readback._deliver(job, result)
        self.assertEqual(chat.rows, [])   # No notice: the newer export's own result shows.
        self.assertEqual(web_readback.for_request(chat), [{"file": "w0.glb", "revision": 1, "status": "superseded",
                                                           "superseded_by": 2}])
        Path(folders[1]).rmdir()

    def test_the_read_back_reads_a_copy_taken_at_the_export(self):
        import tempfile
        from angelo_agent import export_snapshot
        folder = Path(tempfile.mkdtemp(prefix="angelo-preflight-test-"))
        try:
            original = folder / "scene.glb"
            original.write_bytes(b"first export")
            copied = export_snapshot.take(str(original), str(folder / "copy.glb"))
            original.write_bytes(b"a later export over the same name")
            self.assertEqual((folder / "copy.glb").read_bytes(), b"first export")
            self.assertEqual(export_snapshot.identity(str(folder / "copy.glb")), copied)
            (folder / "copy.glb").write_bytes(b"changed")
            self.assertNotEqual(export_snapshot.identity(str(folder / "copy.glb")), copied)
            self.assertIsNone(export_snapshot.identity(str(folder / "missing")))
        finally:
            for item in folder.iterdir():
                item.unlink()
            folder.rmdir()

    def test_a_link_preflight_that_cannot_run_still_names_its_save(self):
        import json
        import tempfile
        from angelo_agent import engine_preflight_link
        folder = Path(tempfile.mkdtemp(prefix="angelo-preflight-test-"))
        report = folder / "asset.glb.preflight.json"
        spec = {"folder": None, "report": str(report), "scene_version": 3, "blend_identity": [1, 2, 3, 4],
                "save_key": "token:9"}
        engine_preflight_link.Runner(str(folder / "no-angelo"))._run(str(folder / "a.blend"), spec)
        written = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual({key: written[key] for key in ("status", "scene_version", "blend_identity", "save_key")},
                         {"status": "failed", "scene_version": 3, "blend_identity": [1, 2, 3, 4],
                          "save_key": "token:9"})
        self.assertGreater(written["saved_at"], 0)
        report.unlink()
        folder.rmdir()

    def test_a_link_preflight_with_thousands_of_baked_objects_keeps_a_short_command_line(self):
        """Windows refuses a command line past 32,767 characters: the spec goes in a file, whatever the scene."""
        import json
        import tempfile
        import stat
        import subprocess
        from angelo_agent import engine_preflight_link
        if sys.platform == "win32":   # Windows starts no "#!" script: Python runs the stand-in, its arguments as sent.
            run = subprocess.run
            self.enterContext(mock.patch.object(engine_preflight_link.subprocess, "run",
                                                lambda command, **options: run([sys.executable, *command], **options)))
        with tempfile.TemporaryDirectory() as folder:
            log, binary = Path(folder, "call.json"), Path(folder, "Angelo")
            binary.write_text("#!%s\nimport json, sys\nspec = json.load(open(sys.argv[-1], encoding='utf-8'))\n"
                              "json.dump({'argv': sys.argv[1:], 'spec': spec}, open(%r, 'w'))\n" % (sys.executable, str(log)))
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
            baked = [["Object.%05d" % index, "Baked.%05d" % index] for index in range(5000)]
            engine_preflight_link.Runner(str(binary))._run(str(Path(folder, "a.blend")),
                                                         {"folder": None, "report": str(Path(folder, "r.json")),
                                                          "baked": baked})
            call = json.loads(log.read_text())
        self.assertEqual(call["spec"]["baked"], baked)
        self.assertLess(len(" ".join(call["argv"])), 8192)   # The child's own code and its paths; never the list.
        self.assertFalse(Path(call["argv"][-1]).exists())

    def test_a_replaced_link_preflight_leaves_no_folder(self):
        import tempfile
        from angelo_agent import engine_preflight_link
        runner = engine_preflight_link.Runner("angelo")
        runner._running = True
        folders = [tempfile.mkdtemp(prefix="angelo-preflight-test-") for _ in range(2)]
        for index, folder in enumerate(folders):
            runner.request("a.blend", {"folder": folder, "scene_version": index})
        self.assertFalse(Path(folders[0]).exists())
        self.assertEqual(runner._again[1]["scene_version"], 1)
        Path(folders[1]).rmdir()

    def test_a_closed_chat_gets_nothing_and_waiting_results_are_bounded(self):
        import weakref
        from angelo_agent import web_readback
        chat = self.Chat()
        job = {"controller": weakref.ref(chat), "written": "a.glb", "revision": 1}
        for _ in range(web_readback.MAX_WAITING + 3):
            web_readback._deliver(job, {"findings": [], "ms": 1.0})
        self.assertEqual(len(web_readback.for_request(chat)), web_readback.MAX_WAITING)
        gone = self.Chat()
        reference = weakref.ref(gone)
        del gone
        web_readback._deliver({"controller": reference, "written": "a.glb", "revision": 2}, {"findings": []})

    def test_the_observation_is_an_event_key(self):
        from angelo_agent.context_ledger import EVENT_OBSERVATION_KEYS
        self.assertIn("preflight", EVENT_OBSERVATION_KEYS)
        console = (ADDON / "console.py").read_text(encoding="utf-8")
        self.assertIn('turn.observation["preflight"]', console)


class PreflightLoadIdentityTests(unittest.TestCase):
    """The current path's stat cannot vouch for the scene Blender actually loaded from a different descriptor."""

    def run_case(self, read_as, copied, changed=False):
        from angelo_agent import document_file_identity, engine_preflight, private_roots
        with tempfile.TemporaryDirectory() as folder:
            blend, other, output = (Path(folder, name) for name in ("saved.blend", "other.blend", "export"))
            blend.write_bytes(b"the original save")
            other.write_bytes(b"another scene Blender read")
            output.mkdir()
            saved = blend.stat()
            expected = [saved.st_dev, saved.st_ino, saved.st_size, saved.st_mtime_ns]
            loaded = saved if read_as == "same" else other.stat()
            native = None if read_as is None else (str(blend), loaded.st_dev, loaded.st_ino, loaded.st_size,
                                                   loaded.st_mtime_ns, 0)
            if changed:
                other.replace(blend)
            spec = {"blend_identity": expected, "scene_version": 7, "file": "copy.glb" if copied else None,
                    "exporter": "gltf", "options": {}, "engine": "godot", "report": "report.json"}
            fake = SimpleNamespace(data=SimpleNamespace(filepath=str(blend)), app=SimpleNamespace(background=True))
            with mock.patch.dict(sys.modules, {"bpy": fake}), \
                    mock.patch.object(document_file_identity, "_native", return_value=native), \
                    mock.patch.object(private_roots, "allocate", return_value=output), \
                    mock.patch.object(engine_preflight, "_export_to", return_value={"FINISHED"}) as exported, \
                    mock.patch.object(engine_preflight, "after_writeback", return_value={"checked": True}) as checked:
                result = engine_preflight.preflight_child(spec)
            return result, checked.call_count, exported.call_count

    def test_another_loaded_scene_is_not_the_original_save_even_when_its_path_is_restored(self):
        for copied in (False, True):
            with self.subTest(copied=copied):
                self.assertEqual(self.run_case("other", copied), (None, 0, 0))

    def test_a_load_without_descriptor_evidence_is_not_verified(self):
        for copied in (False, True):
            with self.subTest(copied=copied):
                self.assertEqual(self.run_case(None, copied), (None, 0, 0))

    def test_the_original_descriptor_and_unchanged_path_are_both_required(self):
        for copied in (False, True):
            with self.subTest(copied=copied):
                self.assertEqual(self.run_case("same", copied), ({"checked": True}, 1, 0 if copied else 1))
                self.assertEqual(self.run_case("same", copied, changed=True), (None, 0, 0))


class SummaryTests(unittest.TestCase):
    """Tool results send each rule once with a few examples (engine_findings); code fetches the rest."""

    @staticmethod
    def many(count):
        """``count`` findings of two rules: faceless curves and rotated armatures."""
        objects = [obj("Curve%04d" % index, "CURVE", faces=0) for index in range(count // 2)]
        rigs = ["Rig%04d" % index for index in range(count - count // 2)]
        objects += [obj(name, "ARMATURE", rotation_deg=[90.0, 0.0, 0.0]) for name in rigs]
        return evaluate(facts(*objects, armatures=[armature(name) for name in rigs]), "GODOT", "GLB",
                        PROFILES[("GODOT", "GLB")])

    def test_many_findings_are_bounded_and_every_one_is_counted(self):
        import json
        from angelo_agent import engine_findings
        findings = self.many(1000)
        self.assertEqual(len(findings), 1000)
        summary = engine_findings.summarize(findings)
        self.assertEqual(summary["total"], 1000)
        self.assertEqual(sum(rule["count"] for rule in summary["rules"]), 1000)
        for rule in summary["rules"]:
            self.assertLessEqual(len(rule["examples"]), engine_findings.EXAMPLES)
            self.assertEqual(rule.get("count", 1), len(rule["examples"]) + rule.get("more", 0))
            self.assertIn(rule["source"], engine_rules.SOURCES.values())   # Once per rule, never per finding.
        sent = len(json.dumps(summary))
        self.assertLess(sent, 6000)   # Was every finding in full (doc/angelo/engine_preflight.rst, "Tool results").
        self.assertLess(sent, len(json.dumps(findings)) / 50)
        self.assertEqual(len(json.dumps(engine_findings.summarize(self.many(1000)))),
                         len(json.dumps(engine_findings.summarize(self.many(1002)))))   # Bounded: no growth.

    def test_a_small_report_costs_no_more_than_its_findings(self):
        """The usual report has a few findings or none: grouping must not cost more there than sending them."""
        import json
        from angelo_agent import engine_findings
        rig = obj("Rig", "ARMATURE", rotation_deg=[90.0, 0.0, 0.0])
        scenes = [facts(), facts(rig, armatures=[armature("Rig")]),
                  facts(rig, obj("Text", "FONT", faces=0), armatures=[armature("Rig")])]
        for scene in scenes:
            for target, fmt in PROFILES:
                findings = evaluate(scene, target, fmt, PROFILES[(target, fmt)])
                self.assertLessEqual(len(json.dumps(engine_findings.summarize(findings))),
                                     len(json.dumps(findings)) + 30, (target, fmt, len(findings)))

    def test_fixable_counts_and_mixed_examples(self):
        from angelo_agent import engine_findings
        scene = facts(*[obj("Rig%d" % index, "ARMATURE", rotation_deg=[90.0, 0.0, 0.0]) for index in range(5)],
                      armatures=[armature("Rig%d" % index, transform_fix_safe=index % 2 == 0) for index in range(5)])
        rule = next(item for item in engine_findings.summarize(evaluate(scene, "UNREAL", "GLB", PROFILES[
            ("UNREAL", "GLB")]))["rules"] if item["code"] == "transform_unapplied")
        self.assertEqual((rule["count"], rule["fixable"], rule["fixable_count"]), (5, "apply_transform", 3))
        self.assertEqual([example["fixable"] for example in rule["examples"]], [True, False, True])
        every = engine_findings.summarize(self.many(10))["rules"]   # All or none fixable: nothing extra is sent.
        self.assertTrue(all("fixable_count" not in rule and "fixable" not in rule["examples"][0] for rule in every))

    def test_details_list_one_rule_in_full(self):
        from angelo_agent import engine_findings
        from angelo_agent.engine_check_spec import MAX_FIXES
        self.assertGreaterEqual(engine_findings.PAGE, MAX_FIXES)   # One fetch names more than one fix call takes.
        findings = self.many(2 * engine_findings.PAGE + 40)
        page = engine_findings.page(findings, "curve_no_surface")
        self.assertEqual((page["total"], len(page["findings"]), page["more"]),
                         (engine_findings.PAGE + 20, engine_findings.PAGE, 20))
        self.assertIn("source", page)
        self.assertNotIn("source", page["findings"][0])
        self.assertTrue(set(page["findings"][0]) >= {"code", "subject", "message", "facts", "fix", "fixable"})
        few = engine_findings.page(self.many(10), "curve_no_surface")
        self.assertEqual((len(few["findings"]), "more" in few), (5, False))
        self.assertEqual(engine_findings.page(findings, "unit_scale")["total"], 0)


class ArgumentTests(unittest.TestCase):
    def test_check_arguments(self):
        self.assertEqual(validate_engine(CHECK, {"target": "UNITY"}),
                         {"target": "UNITY", "format": "AUTO", "selection_only": False})
        self.assertEqual(validate_engine(CHECK, {"target": "UNREAL", "format": "FBX", "engine_version": " 5.8 "}),
                         {"target": "UNREAL", "format": "FBX", "engine_version": "5.8", "selection_only": False})
        for bad in ({}, {"target": "CRYENGINE"}, {"target": "GODOT", "format": "FBX"}, {"target": "WEB",
                    "format": "OBJ"}, {"target": "GODOT", "engine_version": "4\n7"},
                    {"target": "GODOT", "selection_only": "yes"}, {"target": "GODOT", "code": "reimport_weights"},
                    {"target": "GODOT", "code": 7}, {"target": "GODOT", "code": "x" * 65}):
            with self.assertRaises(ValueError, msg=bad):
                validate_engine(CHECK, bad)
        self.assertEqual(validate_engine(CHECK, {"target": "GODOT", "code": "hidden_exported"}),
                         {"target": "GODOT", "format": "AUTO", "selection_only": False, "code": "hidden_exported"})

    def test_fix_arguments(self):
        request = validate_engine(FIX, {"target": "GODOT", "fixes": [
            {"code": "loop_name", "subject": "Walk"}, {"code": "loop_name", "subject": "Walk"}]})
        self.assertEqual(request["fixes"], [{"code": "loop_name", "subject": "Walk"}])
        for bad in ({"target": "GODOT"}, {"target": "GODOT", "fixes": []},
                    {"target": "GODOT", "fixes": [{"code": "unit_scale", "subject": ""}]},
                    {"target": "GODOT", "fixes": [{"code": "loop_name"}]},
                    {"target": "GODOT", "fixes": [{"code": "loop_name", "subject": "x", "extra": 1}]},
                    {"target": "GODOT", "fixes": [{"code": "loop_name", "subject": str(i)} for i in range(33)]}):
            with self.assertRaises(ValueError, msg=str(bad)[:80]):
                validate_engine(FIX, bad)


class FamilyTests(unittest.TestCase):
    def test_tools_are_deferred_and_registered(self):
        from angelo_agent.plan_kinds import DEFERRED_TOOLS
        from angelo_agent.tool_specs import TOOL_MAP
        self.assertTrue(ENGINE_NAMES <= DEFERRED_TOOLS)
        self.assertTrue(TOOL_MAP[CHECK]["annotations"]["readOnlyHint"])
        self.assertFalse(TOOL_MAP[FIX]["annotations"]["readOnlyHint"])

    def test_mcp_levels(self):
        from angelo_agent.external_clients import EDIT, READ, tools_for
        self.assertIn(CHECK, tools_for((READ,)))
        self.assertIn(CHECK, tools_for((READ,), headless=True))
        self.assertNotIn(FIX, tools_for((READ,)))
        self.assertIn(FIX, tools_for((READ, EDIT)))
        self.assertNotIn(FIX, tools_for((READ, EDIT), headless=True))   # Its card needs the window.

    def test_every_fix_has_a_function_and_a_card_line(self):
        fixes = ast.parse((ADDON / "engine_fixes.py").read_text(encoding="utf-8"))
        table = next(node for node in ast.walk(fixes) if isinstance(node, ast.Assign)
                     and getattr(node.targets[0], "id", "") == "FIX_FUNCTIONS")
        self.assertEqual({key.value for key in table.value.keys}, set(engine_rules.FIXES.values()))
        from angelo_agent.engine_fix_approvals import FIX_LINES, message
        self.assertEqual(set(FIX_LINES), set(engine_rules.FIXES.values()))
        card = message(FIX, {"target": "UNREAL", "fixes": [{"code": "armature_extra_root", "subject": "Rig"}]})
        self.assertIn("Unreal Engine", card["title"])
        self.assertIn("Rig", card["body"])

    def test_the_write_back_child_is_untouched_and_the_preflight_never_runs_in_it(self):
        # The engine's re-import never waits for the preflight (engine_preflight_link runs after the write-back).
        source = (ADDON / "engine_link_writeback.py").read_text(encoding="utf-8")
        self.assertNotIn("preflight", source)
        saves = (ADDON / "engine_link_saves.py").read_text(encoding="utf-8")
        self.assertIn("engine_preflight_link.after_writeback(state, binary, asset,", saves)
        self.assertIn("engine_preflight_link.after_blend_save(state, binary, asset,", saves)
        hook = (ADDON / "engine_preflight_link.py").read_text(encoding="utf-8")
        self.assertIn("after_save(binary, asset, asset.editable, asset.path,", hook)

    def test_the_preflight_and_the_engine_link_each_reach_their_own_host(self):
        """Their tools share a name's start: routing by a prefix would send one family to the other's host."""
        from angelo_agent.engine_check_spec import ENGINE_NAMES
        from angelo_agent.engine_link_tool_spec import ENGINE_LINK_NAMES
        self.assertFalse(ENGINE_NAMES & ENGINE_LINK_NAMES)
        dispatch = (ADDON / "blender_tools.py").read_text(encoding="utf-8")
        self.assertIn("if name in ENGINE_NAMES:", dispatch)
        self.assertNotIn('name.startswith("angelo_engine_")', dispatch)
        routes = (ADDON / "approvals.py").read_text(encoding="utf-8")
        self.assertIn('("angelo_engine_fix", "engine_fix_approvals")', routes)
        self.assertNotIn('("angelo_engine_", ', routes)

    def reimport(self):
        """``engine_reimport`` over a stand-in bpy, which leaves with the test."""
        import angelo_agent
        self.addCleanup(lambda: vars(angelo_agent).pop("engine_reimport", None))
        with mock.patch.dict(sys.modules, {"bpy": mock.MagicMock()}):
            return importlib.import_module("angelo_agent.engine_reimport")

    def test_a_baked_export_is_compared_with_what_it_wrote(self):
        """The file carries the baked material on every slot and an importer merges identical slots: no difference."""
        reimport = self.reimport()
        source = {"Crate": {"materials": ["Body", "Trim", "Glass"], "images": []},
                  "Other": {"materials": ["Body", "Trim"], "images": []}}
        written = reimport.with_baked(source, [["Crate", "Crate Baked"], ["Gone", "Gone Baked"]])
        self.assertEqual(written["Crate"]["materials"], ["Crate Baked"])
        self.assertEqual(source["Crate"]["materials"], ["Body", "Trim", "Glass"], "the description itself stays")
        arrived = {"materials": ["Crate Baked.001"], "images": []}
        self.assertEqual(reimport._material_findings("Crate", written["Crate"], arrived), [])
        self.assertTrue(reimport._material_findings("Crate", source["Crate"], arrived), "without the bake it differs")

    def test_slots_that_share_a_material_are_one_material_on_both_sides(self):
        reimport = self.reimport()
        twice = {"materials": ["Body", "Trim", "Body"], "images": []}
        self.assertEqual(reimport._material_findings("Crate", twice, {"materials": ["Body", "Trim"], "images": []}), [])
        self.assertTrue(reimport._material_findings("Crate", twice, {"materials": ["Body"], "images": []}))
        two = {"materials": ["Metal", "Metal.001"], "images": []}
        self.assertEqual(reimport._material_findings("Crate", two, two), [], "two materials, not one with a number")
        self.assertEqual(reimport._material_findings("Crate", two, {"materials": ["Metal.002", "Metal.003"],
                                                                    "images": []}), [], "collision numbers on import")

    def test_a_link_saves_preflight_that_cant_start_is_its_error_never_the_timers(self):
        """Report-only after the save: any failure is a reason string, never an exception into the link's timer."""
        from angelo_agent import engine_preflight_link as link
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder, "angelo")
            binary.write_bytes(b"")
            asset = SimpleNamespace(engine="GODOT")
            with mock.patch("angelo_agent.engine_link_assets.ensure_working_folder"), \
                    mock.patch.object(link, "request", side_effect=RuntimeError("can't start new thread")):
                self.assertEqual(link.after_save(str(binary), asset, "a.blend", None, None, {}), "preflight: RuntimeError")
            with mock.patch("angelo_agent.engine_link_assets.ensure_working_folder", side_effect=OSError("full")):
                self.assertEqual(link.after_save(str(binary), asset, "a.blend", None, None, {}), "preflight: full")

    def test_the_live_preview_never_reads_back(self):
        preview = (ADDON / "web_preview.py").read_text(encoding="utf-8")
        self.assertNotIn("preflight=True", preview)
        self.assertIn("preflight=True", (ADDON / "web_host.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
