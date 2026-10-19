# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender 5.2 itself: every renamed name script_api_notes lists, and what world_surface reads of real Worlds.

Run with --factory-startup (stock Blender 5.2 or the Angelo build). For each ``RENAMED`` row the old name is
absent from its 5.2 owner and the replacement the note gives is present. Then real World graphs: the default
grey, a linked Sky, an unlinked Sky beside a grey fallback (the try/except ``NISHITA`` script), outputs that
target one engine, and a muted link. Nothing is saved.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import bpy

assert '--factory-startup' in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-script-api-notes-test'))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import script_api_notes, world_surface  # noqa: E402

report = {'checks': {}, 'blender': bpy.app.version_string}


def check(name, condition):
    report['checks'][name] = bool(condition)
    assert condition, name


def enum_items(type_name, prop):
    return {item.identifier for item in getattr(bpy.types, type_name).bl_rna.properties[prop].enum_items}


def members(type_name):
    rna = getattr(bpy.types, type_name).bl_rna
    return {prop.identifier for prop in rna.properties} | {function.identifier for function in rna.functions}


def renamed_rows():
    rows = {row.get('string', row.get('attribute')): row for row in script_api_notes.RENAMED}
    check('sky_type_has_no_nishita', 'NISHITA' not in enum_items('ShaderNodeTexSky', 'sky_type'))
    # NISHITA's own model was renamed SINGLE_SCATTERING; a new node defaults to MULTIPLE_SCATTERING. The note names both.
    check('sky_type_has_both_replacements', {'SINGLE_SCATTERING', 'MULTIPLE_SCATTERING'}
          <= enum_items('ShaderNodeTexSky', 'sky_type'))
    check('nishita_note_names_both', all("'%s'" % value in rows['NISHITA']['note']
                                         for value in ('SINGLE_SCATTERING', 'MULTIPLE_SCATTERING')))
    world = bpy.data.worlds.new('Default sky probe')
    world.use_nodes = True
    check('a_new_sky_texture_defaults_to_multiple_scattering',
          world.node_tree.nodes.new('ShaderNodeTexSky').sky_type == 'MULTIPLE_SCATTERING')
    bpy.data.worlds.remove(world)
    scene = bpy.context.scene
    try:
        scene.render.engine = 'BLENDER_EEVEE_NEXT'
        accepted = True
    except TypeError:
        accepted = False
    scene.render.engine = 'BLENDER_EEVEE'
    check('engine_has_no_eevee_next', not accepted and scene.render.engine == 'BLENDER_EEVEE'
          and "'BLENDER_EEVEE'" in rows['BLENDER_EEVEE_NEXT']['note'])
    check('sky_has_aerosol_not_dust', 'dust_density' not in members('ShaderNodeTexSky')
          and 'aerosol_density' in members('ShaderNodeTexSky') and 'aerosol_density' in rows['dust_density']['note'])
    # dust_density is noted wherever it is used: no 5.2 type still has it.
    check('no_type_has_dust_density', not any('dust_density' in members(name) for name in dir(bpy.types)
                                               if hasattr(getattr(bpy.types, name), 'bl_rna')))
    check('action_has_no_fcurves', 'fcurves' not in members('Action') and 'fcurves' in members('ActionChannelbag'))
    obj = bpy.data.objects['Cube']
    obj.keyframe_insert('location', frame=1)
    from bpy_extras import anim_utils
    bag = anim_utils.action_get_channelbag_for_slot(obj.animation_data.action, obj.animation_data.action_slot)
    check('the_noted_channelbag_route_works', 'action_get_channelbag_for_slot' in rows['fcurves']['note']
          and len(bag.fcurves) == 3)
    obj.animation_data_clear()


def worlds():
    scene = bpy.context.scene
    world = scene.world
    tree = world.node_tree
    background = next(node for node in tree.nodes if node.type == 'BACKGROUND')
    output = next(node for node in tree.nodes if node.type == 'OUTPUT_WORLD')
    shown = world_surface.shows(world, scene.render.engine)
    check('default_world_is_flat_grey', shown['shows'] == 'COLOR' and shown['family'] == 'grey')
    before = world_surface.fingerprint(scene)
    # The try/except NISHITA script: the sky node is made, its enum raises, the grey fallback runs.
    code = ("sky = tree.nodes.new('ShaderNodeTexSky')\ntry:\n    sky.sky_type = 'NISHITA'\n"
            "    tree.links.new(sky.outputs['Color'], background.inputs['Color'])\nexcept TypeError:\n"
            "    background.inputs['Color'].default_value = (0.5, 0.5, 0.5, 1)\n")
    exec(compile(code, '<script>', 'exec'), {'tree': tree, 'background': background})
    reported = world_surface.changed(scene, before)
    check('silent_fallback_reads_as_grey', reported['shows'] == 'COLOR' and reported['family'] == 'grey'
          and reported['color'] == [0.5, 0.5, 0.5])
    check('silent_fallback_is_noted', 'NISHITA' in script_api_notes.notes(code)['api_notes'][0])
    sky = next(node for node in tree.nodes if node.type == 'TEX_SKY')
    sky.sun_elevation = 0.0872665
    link = tree.links.new(sky.outputs['Color'], background.inputs['Color'])
    shown = world_surface.shows(world, scene.render.engine)
    check('linked_sky_is_reported', shown['shows'] == 'SKY' and shown['sky']['type'] == 'MULTIPLE_SCATTERING'
          and shown['sky']['sun_elevation_deg'] == 5.0 and shown['sky']['sun_bearing_deg'] == 0.0)
    link.is_muted = True
    check('muted_link_shows_the_colour', world_surface.shows(world, scene.render.engine)['shows'] == 'COLOR')
    link.is_muted = False
    cycles = tree.nodes.new('ShaderNodeOutputWorld')
    cycles.target = 'CYCLES'
    blue = tree.nodes.new('ShaderNodeBackground')
    blue.inputs['Color'].default_value = (0.05, 0.2, 0.8, 1)
    tree.links.new(blue.outputs['Background'], cycles.inputs['Surface'])
    check('engine_output_wins_for_cycles', world_surface.shows(world, 'CYCLES')['family'] == 'blue')
    check('all_output_for_eevee', world_surface.shows(world, 'BLENDER_EEVEE')['shows'] == 'SKY')
    check('output_is_active', output.is_active_output)
    check('fingerprint_sees_the_edits', world_surface.fingerprint(scene) != before)


def main():
    renamed_rows()
    worlds()
    report['success'] = True


try:
    main()
except Exception:
    import traceback
    report.update(success=False, error=traceback.format_exc())
(OUT / 'script-api-notes.json').write_text(json.dumps(report, indent=2))
print('ANGELO_SCRIPT_API_NOTES', json.dumps(report), flush=True)
if not report.get('success'):
    raise RuntimeError(report['error'])
