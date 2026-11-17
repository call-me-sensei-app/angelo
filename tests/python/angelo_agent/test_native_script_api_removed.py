# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender Python that this release removed is refused before a script runs (script_api_removed), without Blender.

Every recorded raise of a removed name (ten in 2,618 recorded one-shot scripts, both sides: the Nishita sky six
times, EEVEE Next, dust_density, the Bump node's Invert input twice) is refused with its replacement, and none of the
2,608 others is: scripts that probe for either spelling (a try, hasattr, ``in``, a conditional value, a loop over
names) keep running. The lines below are the recorded shapes. The Blender 5.3 rows (attributes, operators and
operator keywords) come from the 5.2.2 to 5.3 source diff, not from recorded raises; test_blender_script_api_removed.py
checks the whole table against the release's own RNA and operators.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import script_api_removed as removed  # noqa: E402
from angelo_agent.blender_release import RELEASE  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

# Recorded lines that raised, with the scene's own names left out.
RAISED = {
    "sky=wn.nodes.new('ShaderNodeTexSky'); sky.sky_type='NISHITA'; sky.sun_disc=False": "'SINGLE_SCATTERING'",
    "bpy.context.scene.render.engine = 'BLENDER_EEVEE_NEXT'": "'BLENDER_EEVEE'",
    "sky.air_density=1.5; sky.dust_density=4 if hasattr(sky,'dust_density') else 0": "aerosol_density",
    "bu=N.new('ShaderNodeBump'); bu.inputs['Invert'].default_value=True if 'Invert' in bu.inputs else None":
        "node.invert = True",
    'bump=N.new("ShaderNodeBump"); bump.inputs[\'Invert\'].default_value=True': "node.invert = True",
}
# Recorded lines that ran: each probes for the name before it can raise.
RAN = [
    "for t in('NISHITA','MULTIPLE_SCATTERING'):\n    try: sky.sky_type=t; break\n    except Exception: pass",
    "try: sky.sky_type='NISHITA'\nexcept Exception: pass",
    "sky.sky_type='MULTIPLE_SCATTERING' if 'MULTIPLE_SCATTERING' in items else 'NISHITA'",
    "try: sc.eevee.use_raytracing=True\nexcept Exception: sc.eevee.use_ssr=True",
    "if hasattr(sk,'dust_density'): sk.dust_density=3.0",
    "for k,v in (('air_density',1.8),('aerosol_density',0.3),('dust_density',0.3)):\n    if hasattr(sky,k): setattr(sky,k,v)",
    "try:\n    sky.air_density=1.5; sky.dust_density=4\nexcept Exception: pass",
    "sc.render.engine='BLENDER_EEVEE' if 'BLENDER_EEVEE' in engines else 'BLENDER_EEVEE_NEXT'",
    "if sky.sky_type == 'NISHITA': pass",
    "glare = tree.nodes.new('CompositorNodeConvertToDisplay'); glare.inputs['Invert'].default_value = True",
    "print('use_auto_smooth was removed')",
]


class TableTests(unittest.TestCase):
    def test_every_recorded_raise_is_refused_with_its_replacement(self):
        for line, replacement in RAISED.items():
            with self.subTest(line=line):
                found = removed.problems(line)
                self.assertTrue(found and replacement in found[0], found)
                self.assertTrue(found[0].startswith("line 1: "), found)

    def test_every_probing_line_runs(self):
        for line in RAN:
            with self.subTest(line=line):
                self.assertEqual(removed.problems(line), [])

    def test_documented_removals(self):
        for line, replacement in (
                ("me.use_auto_smooth = True", "set_sharp_from_angle"),
                ("me.auto_smooth_angle = 0.5", "set_sharp_from_angle"),
                ("me.calc_normals_split()", "corner_normals"),
                ("scene.eevee.use_bloom = True", "Glare"),
                ("scene.eevee.use_ssr = True", "use_raytracing"),
                ("scene.eevee.shadow_cube_size = '1024'", "leave it out"),
                ("bpy.ops.export_scene.obj(filepath=path)", "bpy.ops.wm.obj_export"),
                ("bpy.ops.import_mesh.stl(filepath=path)", "bpy.ops.wm.stl_import"),
                ("bpy.ops.wm.collada_export(filepath=path)", "usd_export"),
                ("brush.use_inverse_smooth_pressure = True", "use_smooth_pressure"),
                ("space.use_sort_alpha = False", "sort_method = 'ALPHA'"),
                ("prefs.system.geometry_nodes_stack_limit = 64", "nodes_stack_limit"),
                ("bpy.ops.sequencer.select_inverse()", "select_all(action='INVERT')"),
                ("bpy.ops.anim.update_animated_transform_constraints()", "leave it out"),
                ("scene.sequencer_tool_settings.overlap_mode = 'EXPAND'", "'RIPPLE'")):
            with self.subTest(line=line):
                found = removed.problems(line)
                self.assertTrue(found and replacement in found[0], found)

    def test_removed_operator_keywords(self):
        for line, replacement in (
                ("bpy.ops.object.text_add(radius=2.0, location=(0, 0, 0))", "use size"),
                ("bpy.ops.sequencer.image_strip_add(directory=d, files=f, move_strips=False)",
                 "move_strips_after_add"),
                ("bpy.ops.sequencer.sound_strip_add(filepath=p, overlap_shuffle_override=True)", "overlap_mode="),
                ("bpy.ops.export_scene.gltf(filepath=p, export_image_add_webp=True)",
                 "export_image_add_compressed_images"),
                ("bpy.ops.export_scene.gltf(filepath=p, at_collection_center=True)", "collection_center_mode")):
            with self.subTest(line=line):
                found = removed.problems(line)
                self.assertTrue(found and replacement in found[0], found)
                self.assertIn("=...)", found[0])

    def test_current_operator_keywords_and_spread_options_run(self):
        for line in ("bpy.ops.object.text_add(size=2.0)",
                     "bpy.ops.mesh.primitive_uv_sphere_add(radius=2.0)",   # radius is gone from text_add only.
                     "bpy.ops.sequencer.image_strip_add(directory=d, files=f, move_strips_after_add=False)",
                     "bpy.ops.export_scene.gltf(**{'filepath': p, 'export_image_add_webp': True})",
                     "options = dict(radius=2.0)\nbpy.ops.object.text_add(**options)",
                     "try:\n    bpy.ops.object.text_add(radius=2.0)\n"
                     "except TypeError:\n    bpy.ops.object.text_add(size=2.0)",
                     "if 'radius' in props: bpy.ops.object.text_add(radius=2.0)"):
            with self.subTest(line=line):
                self.assertEqual(removed.problems(line), [])

    def test_a_function_and_a_loop_are_read_too_and_the_list_is_capped(self):
        code = "def make(sky):\n    sky.sky_type = 'NISHITA'\n" + "".join(
            "obj%d.data.use_auto_smooth = True\n" % i for i in range(9))
        found = removed.problems(code)
        self.assertEqual(len(found), removed.MAX_LISTED)
        self.assertTrue(found[0].startswith("line 2: sky_type = 'NISHITA'"))

    def test_code_that_does_not_parse_is_left_to_the_syntax_check(self):
        self.assertEqual(removed.problems("sky.sky_type = 'NISHITA"), [])


class ValidationTests(unittest.TestCase):
    def test_validation_refuses_before_anything_runs(self):
        code = "import bpy\nw = bpy.context.scene.world\nsky = w.node_tree.nodes.new('ShaderNodeTexSky')\nsky.sky_type = 'NISHITA'\n"
        release = re.escape(RELEASE)
        with self.assertRaisesRegex(ValueError, r"Blender %s removed, so it was not run: line 4: sky_type = 'NISHITA' "
                                                r"is not in Blender %s: use 'SINGLE_SCATTERING' .*the new default is "
                                                r"'MULTIPLE_SCATTERING'" % (release, release)):
            validate_call("angelo_execute_python", {"description": "Sky", "code": code})
        ran = validate_call("angelo_execute_python", {"description": "Sky", "code": code.replace("NISHITA",
                                                                                                "MULTIPLE_SCATTERING")})
        self.assertIn("MULTIPLE_SCATTERING", ran["code"])



class ConsistencyTests(unittest.TestCase):
    def test_the_refusal_names_the_same_5_2_values_as_the_result_notes(self):
        # Three places state a 5.2 rename: this refusal before a script runs, the note on a script that ran
        # (script_api_notes) and Blender's own error (bpy_angelo_renamed_api.cc, checked by
        # test_blender_renamed_api_errors). A name in both host tables gives the model the same replacements.
        import re
        from angelo_agent.script_api_notes import RENAMED
        for row in RENAMED:
            name = row.get("string") or row.get("attribute")
            refusal = next((text for (_, value), text in removed.VALUES.items() if value == name),
                           removed.ATTRIBUTES.get(name))
            if refusal is None:
                continue
            def named(text):   # The 5.2 values a "use ..." clause names, first the one to use.
                return [a or b for a, b in re.findall(r"'([A-Z_]{3,})'|\b([a-z]+_[a-z_]+)\b",
                                                      text.split("use ", 1)[-1]) if (a or b) != name]
            with self.subTest(name=name):
                self.assertEqual(named(refusal)[0], named(row["note"])[0], "the same value to use")
                self.assertLessEqual(set(named(row["note"])), set(named(refusal)))


if __name__ == "__main__":
    unittest.main()
