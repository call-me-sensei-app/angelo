# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The generated renamed-and-removed table (scripts/angelo/api_drift): its rules, and one table for all three uses.

Host only: synthetic dumps exercise the rules (a replacement only on exact evidence, the release named only when
the release before it was dumped, versioning chains, curated rows); the committed table must render to the
committed C++ rows; and its certain rows must give the refusal and the note exactly what they gave when the
three tables were written by hand (the literals below are those tables, copied before the change).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from api_drift import cli, table, versioning  # noqa: E402


def dump(version, structs=None, operators=None, nodes=None, modules=None):
    return {"schema": 1, "version": version, "structs": structs or {}, "operators": operators or {},
            "nodes": nodes or {}, "modules": modules or {}}


def struct(**properties):
    return {"base": None, "name": "", "properties": properties, "functions": {}}


def prop(kind="FLOAT", name="", description="", items=None):
    record = {"type": kind, "name": name, "description": description}
    if items is not None:
        record["items"] = items
    return record


EMPTY_CURATED = {"target": "4.1", "rows": [], "replay_cleared": []}


def built(dumps, renames=(), curated=EMPTY_CURATED):
    result = table.build(dumps, list(renames), curated)
    return {(entry["use"], entry.get("owner"), entry.get("property"), entry["name"]): entry
            for entry in result["entries"]}


class Rules(unittest.TestCase):
    def test_a_rename_is_named_only_on_exact_evidence(self):
        old = dump("3.6", {"Sky": struct(dust=prop(name="Dust", description="Amount of dust"),
                                         gone=prop(description="Old"),
                                         twin=prop(description="Shared text"))})
        new = dump("4.0", {"Sky": struct(aerosol=prop(name="Aerosol", description="Amount of dust"),
                                         one=prop(description="Shared text"), two=prop(description="Shared text"))})
        entries = built([old, new])
        self.assertEqual(entries[("attribute", "Sky", None, "dust")]["replacements"], ["aerosol"])
        self.assertEqual(entries[("attribute", "Sky", None, "dust")]["hint"], "Blender 4.0 renamed 'dust' to 'aerosol'")
        self.assertEqual(entries[("attribute", "Sky", None, "gone")]["hint"], "Blender 4.0 removed Sky.gone")
        self.assertEqual(entries[("attribute", "Sky", None, "twin")]["replacements"], [],
                         "two candidates: none is named")

    def test_a_rename_of_another_kind_is_not_evidence(self):
        old = dump("3.6", {"Mesh": struct(size=prop("FLOAT", description="Size"))})
        new = dump("4.0", {"Mesh": struct(scale=prop("INT", description="Size"))})
        self.assertEqual(built([old, new])[("attribute", "Mesh", None, "size")]["replacements"], [])

    def test_the_release_is_named_only_when_the_one_before_it_was_dumped(self):
        old = dump("3.6", {"Sky": struct(gone=prop())})
        new = dump("4.2", {"Sky": struct()})
        self.assertEqual(built([old, new])[("attribute", "Sky", None, "gone")]["hint"],
                         "A Blender release after 3.6 (by 4.2) removed Sky.gone")

    def test_the_release_that_removed_a_name_is_the_first_without_it(self):
        dumps = [dump("4.0", {"Sky": struct(gone=prop())}), dump("4.1", {"Sky": struct(gone=prop())}),
                 dump("4.2", {"Sky": struct()})]
        entry = built(dumps)[("attribute", "Sky", None, "gone")]
        self.assertEqual((entry["last"], entry["since"]), ("4.1", "4.2"))

    def test_enum_items_renamed_by_stored_value(self):
        items_old = [["NISHITA", 2, "Nishita", "Old model"], ["HOSEK", 1, "Hosek", ""]]
        items_new = [["SINGLE_SCATTERING", 2, "Single Scattering", "New text"], ["HOSEK", 1, "Hosek", ""]]
        entries = built([dump("4.5", {"Tex": struct(sky_type=prop("ENUM", items=items_old))}),
                         dump("5.0", {"Tex": struct(sky_type=prop("ENUM", items=items_new))})])
        self.assertEqual(entries[("enum_item", "Tex", "sky_type", "NISHITA")]["hint"],
                         "Blender 5.0 renamed 'NISHITA' to 'SINGLE_SCATTERING'")
        self.assertNotIn(("enum_item", "Tex", "sky_type", "HOSEK"), entries)

    def test_sockets_still_reachable_by_identifier_are_not_listed(self):
        old = dump("4.0", nodes={"N": {"name": "Node", "inputs": [["Fac", "Fac"], ["Gone", "Gone"]], "outputs": []}})
        new = dump("4.1", nodes={"N": {"name": "Node", "inputs": [["Factor", "Fac"]], "outputs": []}})
        entries = built([old, new])
        self.assertNotIn(("socket", "N", "inputs", "Fac"), entries)
        self.assertEqual(entries[("socket", "N", "inputs", "Gone")]["hint"], "Blender 4.1 removed the Node input 'Gone'")

    def test_operators_and_their_arguments(self):
        old = dump("4.1", operators={"export_scene.obj": {"properties": {}},
                                     "mesh.primitive_cube_add": {"properties": {"radius": {}, "size": {}}}})
        new = dump("4.2", operators={"mesh.primitive_cube_add": {"properties": {"size": {}}}})
        entries = built([old, new])
        self.assertEqual(entries[("operator", None, None, "export_scene.obj")]["hint"],
                         "Blender 4.2 removed bpy.ops.export_scene.obj")
        self.assertEqual(entries[("operator_argument", "mesh.primitive_cube_add", None, "radius")]["hint"],
                         "Blender 4.2 removed the argument 'radius' of bpy.ops.mesh.primitive_cube_add")

    def test_an_attribute_another_type_still_has_is_marked(self):
        old = dump("4.5", {"Action": struct(fcurves=prop("COLLECTION")), "Bag": struct(fcurves=prop("COLLECTION"))})
        new = dump("5.0", {"Action": struct(), "Bag": struct(fcurves=prop("COLLECTION"))})
        self.assertTrue(built([old, new])[("attribute", "Action", None, "fcurves")]["elsewhere"])

    def test_versioning_renames_follow_chains_to_the_target_name(self):
        renames = [{"release": "4.0", "constant": "P", "node": "Principled", "side": "inputs", "old": "Specular",
                    "new": "Specular IOR"},
                   {"release": "4.2", "constant": "P", "node": "Principled", "side": "inputs", "old": "Specular IOR",
                    "new": "Specular IOR Level"}]
        target = dump("5.2", nodes={"Principled": {"name": "Principled BSDF",
                                                   "inputs": [["Specular IOR Level", "Specular IOR Level"]],
                                                   "outputs": []}})
        entries = built([dump("5.1", nodes=target["nodes"]), target], renames)
        entry = entries[("socket", "Principled", "inputs", "Specular")]
        self.assertEqual(entry["replacements"], ["Specular IOR Level"])
        self.assertEqual(entry["hint"], "Blender 4.0 renamed the Principled BSDF input 'Specular' to 'Specular IOR Level'")

    def test_a_two_sided_rename_keeps_only_the_side_a_dump_shows(self):
        rename = [{"release": "4.1", "constant": "R", "node": "Rot", "side": "both", "old": "Rotation 1",
                   "new": "Rotation"}]
        rot_old = {"Rot": {"name": "Rotate", "inputs": [["Rotation 1", "Rotation 1"]], "outputs": [["Out", "Out"]]}}
        rot_new = {"Rot": {"name": "Rotate", "inputs": [["Rotation", "Rotation"]], "outputs": [["Rotation", "Rotation"]]}}
        self.assertEqual(built([], rename), {}, "no dump: neither side is stated")
        entries = built([dump("4.0", nodes=rot_old), dump("4.1", nodes=rot_new)], rename)
        self.assertIn(("socket", "Rot", "inputs", "Rotation 1"), entries)
        self.assertNotIn(("socket", "Rot", "outputs", "Rotation 1"), entries, "the output never had that name")

    def test_a_rename_of_a_name_no_release_had_is_dropped_when_a_dump_shows_it(self):
        rename = [{"release": "4.1", "constant": "N", "node": "N", "side": "inputs", "old": "Dev name", "new": "Fac"}]
        nodes_old = {"N": {"name": "Node", "inputs": [["Factor", "Factor"]], "outputs": []}}
        nodes_new = {"N": {"name": "Node", "inputs": [["Fac", "Fac"]], "outputs": []}}
        self.assertNotIn(("socket", "N", "inputs", "Dev name"),
                         built([dump("4.0", nodes=nodes_old), dump("4.1", nodes=nodes_new)], rename))

    def test_versioning_chains_within_a_release_drop_unreleased_names(self):
        calls = [{"release": "4.0", "constant": "P", "node": "N", "side": "inputs", "old": "Clearcoat", "new": "Coat"},
                 {"release": "4.0", "constant": "P", "node": "N", "side": "inputs", "old": "Coat",
                  "new": "Coat Weight"}]
        resolved = versioning.resolve_chains(calls)
        self.assertEqual([(call["old"], call["new"]) for call in resolved], [("Clearcoat", "Coat Weight")])

    def test_versioning_calls_are_parsed_on_one_line_or_several(self):
        text = ('version_node_input_socket_name(ntree, SH_NODE_X, "A", "B");\n'
                'version_node_output_socket_name(\n      &ntree, FN_NODE_Y, "Image", "Output");\n'
                'version_node_socket_name(node_tree, CMP_NODE_Z, "On", "True");')
        self.assertEqual(versioning.RENAME_CALL.findall(text),
                         [("input_", "SH_NODE_X", "A", "B"), ("output_", "FN_NODE_Y", "Image", "Output"),
                          ("", "CMP_NODE_Z", "On", "True")])
        self.assertEqual(versioning.NODE_REGISTRATION.findall(
            'sh_node_type_base(&ntype, "ShaderNodeBsdfPrincipled"_ustr, SH_NODE_BSDF_PRINCIPLED);'),
            [("ShaderNodeBsdfPrincipled", "SH_NODE_BSDF_PRINCIPLED")])

    def test_a_versioning_file_names_its_release_in_both_namings(self):
        # Blender 5.2's files and 5.3's renamed ones (0c2ca963600): the same releases.
        for old, new, release in (("410", "401", (4, 1)), ("450", "405", (4, 5)), ("520", "502", (5, 2)),
                                  ("400", "400", (4, 0)), ("500", "500", (5, 0))):
            with self.subTest(old=old, new=new):
                self.assertEqual(versioning.release_of("versioning_%s.cc" % old), release)
                self.assertEqual(versioning.release_of("versioning_%s.cc" % new), release)
        self.assertEqual(versioning.release_of("versioning_503.cc"), (5, 3))
        self.assertIsNone(versioning.release_of("versioning_common.cc"))

    def test_a_call_that_prepares_a_node_replacement_is_no_rename(self):
        text = ('static void plain(bNodeTree *tree)\n{\n  version_node_socket_name(tree, A, "Old", "New");\n}\n\n'
                'static void swap(bNodeTree *tree)\n{\n  version_node_socket_name(tree, B, "Off", "False");\n'
                '  if (node.type_legacy == B) {\n    STRNCPY_UTF8(node.idname, "GeometryNodeSwitch");\n  }\n}\n\n'
                'static void large(bNodeTree *tree)\n{\n  version_node_socket_name(tree, C, "Old", "New");\n'
                '  if (node.type_legacy == D) {\n    STRNCPY(node->idname, "Other");\n  }\n}\n')
        kept = [match.group(2) for match in versioning.RENAME_CALL.finditer(text)
                if not versioning.replaces_the_node(text, match.start(), match.group(2))]
        self.assertEqual(kept, ["A", "C"])

    def test_curated_rows_win_and_are_certain(self):
        curated = {"target": "4.1", "replay_cleared": [["attribute", "Sky", None, "other"]],
                   "rows": [{"use": "attribute", "owner": "Sky", "name": "gone", "hint": "Cited sentence",
                             "refusal": "use x", "source": "release notes"}]}
        entries = built([dump("4.0", {"Sky": struct(gone=prop(), other=prop())}), dump("4.1", {"Sky": struct()})],
                        curated=curated)
        self.assertEqual(entries[("attribute", "Sky", None, "gone")]["hint"], "Cited sentence")
        self.assertTrue(entries[("attribute", "Sky", None, "gone")]["certain"])
        self.assertTrue(entries[("attribute", "Sky", None, "other")]["certain"], "cleared by the replay")


class CommittedTable(unittest.TestCase):
    def setUp(self):
        self.table = json.loads(cli.TABLE.read_text(encoding="utf-8"))

    def test_the_cpp_rows_are_rendered_from_the_table(self):
        self.assertEqual(cli.CPP_TABLE.read_text(encoding="utf-8"), table.render_cpp(self.table),
                         "run: python3 scripts/angelo/api_drift/cli.py build --keep-dumped")

    def test_every_curated_row_is_in_the_table_unchanged(self):
        curated = json.loads(cli.CURATED.read_text(encoding="utf-8"))
        entries = {(entry["use"], entry.get("owner"), entry.get("property"), entry["name"]): entry
                   for entry in self.table["entries"]}
        for row in curated["rows"]:
            with self.subTest(name=row["name"]):
                entry = entries[(row["use"], row.get("owner"), row.get("property"), row["name"])]
                self.assertTrue(entry["certain"])
                for field in ("refusal", "note"):
                    self.assertEqual(entry.get(field), row.get(field))
                if "hint" in row:
                    self.assertEqual(entry["hint"], row["hint"])
                self.assertTrue(row.get("source"), "every curated row cites its source")

    def test_every_entry_has_a_sentence(self):
        for entry in self.table["entries"]:
            self.assertTrue(entry["hint"] and "\n" not in entry["hint"], entry)


# The three hand-written tables just before they were generated (integration/v1.0.6, 2026-10-24).
SKY = ("use 'SINGLE_SCATTERING' (Blender 5.0 renamed 'NISHITA' to it, the same model: files saved with 'NISHITA' open "
       "with it); the new default is 'MULTIPLE_SCATTERING'")
LEAVE_OUT = "leave it out: an EEVEE Legacy setting, removed in Blender 4.2 with nothing in its place"
HAND_VALUES = {("sky_type", "NISHITA"): SKY,
               ("engine", "BLENDER_EEVEE_NEXT"): "use 'BLENDER_EEVEE' (Blender 5.0 renamed EEVEE Next back to EEVEE)"}
HAND_ATTRIBUTES = {
    "dust_density": "use aerosol_density (renamed in Blender 5.0)",
    "use_auto_smooth": "use mesh.set_sharp_from_angle(angle=...) or the Smooth by Angle modifier (removed in Blender 4.1)",
    "auto_smooth_angle": "use mesh.set_sharp_from_angle(angle=...) (removed in Blender 4.1)",
    "calc_normals": "leave it out: normals are always current (removed in Blender 4.0)",
    "calc_normals_split": "read mesh.corner_normals, always current (removed in Blender 4.1)",
    "free_normals_split": "leave it out: corner normals are always current (removed in Blender 4.1)",
    "use_ssr": "use scene.eevee.use_raytracing (screen-space reflections were removed in Blender 4.2)",
    "use_ssr_refraction": "use scene.eevee.use_raytracing (screen-space refraction was removed in Blender 4.2)",
    "use_bloom": "use the compositor's Glare node (EEVEE's bloom was removed in Blender 4.2)",
    "use_gtao": LEAVE_OUT, "use_soft_shadows": LEAVE_OUT, "shadow_cube_size": LEAVE_OUT,
    "shadow_cascade_size": LEAVE_OUT,
}
HAND_OPERATORS = {
    "export_scene.obj": "use bpy.ops.wm.obj_export (since Blender 4.0)",
    "import_scene.obj": "use bpy.ops.wm.obj_import (since Blender 4.0)",
    "export_mesh.stl": "use bpy.ops.wm.stl_export (since Blender 4.2)",
    "import_mesh.stl": "use bpy.ops.wm.stl_import (since Blender 4.2)",
    "export_mesh.ply": "use bpy.ops.wm.ply_export (since Blender 4.0)",
    "import_mesh.ply": "use bpy.ops.wm.ply_import (since Blender 4.0)",
    "wm.collada_export": "use bpy.ops.wm.usd_export or bpy.ops.export_scene.gltf (Collada was removed in Blender 5.0)",
    "wm.collada_import": "use bpy.ops.wm.usd_import or bpy.ops.import_scene.gltf (Collada was removed in Blender 5.0)",
}
HAND_BUMP_INVERT = "set node.invert = True: the Bump node's invert is a property, not an input"
# The Blender 5.3 refusals as first written by hand from the 5.2.2 to 5.3 source diff (prep/v1.1-agent-facts-5.3,
# 2026-11-03), before they became curated rows: the rows must refuse in the same words.
HAND_53_VALUES = {("overlap_mode", "EXPAND"): "use 'RIPPLE' (Blender 5.3 renamed the Sequencer's Expand overlap mode "
                                              "to Ripple)"}
HAND_53_ATTRIBUTES = {
    "use_inverse_smooth_pressure": "use brush.use_smooth_pressure with its curve_auto_smooth pressure curve "
                                   "(Blender 5.3 replaced the inverse toggle with a curve)",
    "use_sort_alpha": "use sort_method = 'ALPHA' ('CUSTOM' and 'NONE' are the others; replaced in Blender 5.3)",
    "geometry_nodes_stack_limit": "use nodes_stack_limit (renamed in Blender 5.3)",
}
HAND_53_OPERATORS = {
    "sequencer.select_inverse": "use bpy.ops.sequencer.select_all(action='INVERT') (removed in Blender 5.3)",
    "anim.update_animated_transform_constraints": "leave it out: Blender 5.3 removed this one-time update of old "
                                                  "files' constraint values",
}
STRIP_ADD = ["sequencer.%s_strip_add" % kind for kind in ("effect", "image", "mask", "movie", "movieclip", "scene",
                                                          "sound")] + ["sequencer.scene_strip_add_new",
                                                                       "sequencer.add_scene_strip_from_scene_asset"]
HAND_53_KEYWORDS = {
    ("object.text_add", "radius"): "use size (renamed in Blender 5.3; the same value)",
    ("export_scene.gltf", "export_image_add_webp"): "use export_image_add_compressed_images with "
                                                    "export_compressed_images_type='WEBP' (renamed in Blender 5.3)",
    ("export_scene.gltf", "export_image_webp_fallback"): "use export_compressed_images_fallback (renamed in "
                                                         "Blender 5.3)",
    ("export_scene.gltf", "at_collection_center"): "use collection_center_mode='CENTER_MASS' (replaced in Blender 5.3)",
    **{(op, "move_strips"): "use move_strips_after_add (renamed in Blender 5.3)" for op in STRIP_ADD},
    **{(op, "overlap_shuffle_override"): "use overlap_mode='SHUFFLE', 'RIPPLE' or 'OVERWRITE' (replaced in "
                                         "Blender 5.3)" for op in STRIP_ADD},
}
HAND_NOTES = (
    {"string": "NISHITA", "owner": "ShaderNodeTexSky.sky_type",
     "note": "Blender 5.3 has no sky_type 'NISHITA': use 'SINGLE_SCATTERING' (the same model: files saved with "
             "'NISHITA' open with it); the new default is 'MULTIPLE_SCATTERING'."},
    {"string": "BLENDER_EEVEE_NEXT", "owner": "RenderSettings.engine",
     "note": "Blender 5.3 has no render engine 'BLENDER_EEVEE_NEXT': use 'BLENDER_EEVEE'."},
    {"attribute": "dust_density", "owner": "ShaderNodeTexSky",
     "note": "Blender 5.3's Sky Texture has no dust_density: use aerosol_density."},
    {"attribute": "fcurves", "on": "action", "owner": "Action",
     "note": "Blender 5.3's Action has no fcurves: use "
             "bpy_extras.anim_utils.action_get_channelbag_for_slot(action, animation_data.action_slot).fcurves."},
)


class SameAsTheHandWrittenTables(unittest.TestCase):
    def test_the_refusal_reads_the_same_rows(self):
        from angelo_agent import script_api_removed as removed
        self.assertEqual(removed.VALUES, {**HAND_VALUES, **HAND_53_VALUES})
        self.assertEqual(removed.ATTRIBUTES, {**HAND_ATTRIBUTES, **HAND_53_ATTRIBUTES})
        self.assertEqual(removed.OPERATORS, {**HAND_OPERATORS, **HAND_53_OPERATORS})
        self.assertEqual(removed.KEYWORDS, HAND_53_KEYWORDS)
        self.assertEqual(removed.BUMP_INVERT, HAND_BUMP_INVERT)
        self.assertEqual(set(removed.SOCKETS), {(removed.BUMP, "inputs", "Invert")})

    def test_the_note_reads_the_same_rows(self):
        from angelo_agent import script_api_notes
        self.assertEqual(sorted(script_api_notes.RENAMED, key=repr), sorted(HAND_NOTES, key=repr))

    def test_the_table_targets_the_release_the_texts_name(self):
        from angelo_agent.blender_release import RELEASE
        self.assertEqual(json.loads((ROOT / "scripts/angelo/api_drift/curated.json").read_text())["target"], RELEASE)
        self.assertEqual(json.loads((ROOT / "scripts/addons_core/angelo_agent/assets/api_drift.json").read_text())
                         ["target"], RELEASE)
        from angelo_agent import script_api_notes
        self.assertTrue(all(row["note"].startswith("Blender %s" % RELEASE) for row in script_api_notes.RENAMED))

    def test_the_release_is_the_blender_this_source_builds(self):
        # The per-version user folder and every text that names the release follow it: a stale value made the CLI
        # and the headless worker read the earlier release's folder.
        import re
        from angelo_agent.blender_release import RELEASE
        from angelo_agent.cli_client import BLENDER_SERIES
        header = ROOT / "source/blender/blenkernel/BKE_blender_version.h"
        if not header.is_file():
            self.skipTest("[environment] no Blender source in this checkout (a scripts and tests snapshot)")
        version = int(re.search(r"^#define BLENDER_VERSION (\d+)$", header.read_text(), re.M).group(1))
        self.assertEqual(RELEASE, "%d.%d" % divmod(version, 100))
        self.assertEqual(BLENDER_SERIES, RELEASE)

    def test_importing_reads_no_table(self):
        import importlib
        from angelo_agent import script_api_table
        script_api_table.entries.cache_clear()
        for module in ("angelo_agent.script_api_removed", "angelo_agent.script_api_notes"):
            importlib.reload(sys.modules[module]) if module in sys.modules else importlib.import_module(module)
        self.assertEqual(script_api_table.entries.cache_info().currsize, 0)


if __name__ == "__main__":
    unittest.main()
