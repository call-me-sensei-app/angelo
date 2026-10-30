# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only installed API introspection; run with --factory-startup."""
import json
import os
import sys
import tempfile
from pathlib import Path

import bpy

assert '--factory-startup' in sys.argv
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-api-lookup-test'))
OUT.mkdir(parents=True,exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT='1',ANGELO_CONNECTIONS_DIR=str(OUT/'accounts'),ANGELO_STATE_DIR=str(OUT/'state'))
sys.path.insert(0,str(ROOT/'scripts/addons_core'))
from angelo_agent.api_lookup import execute

report={'checks':{},'paid_requests':0}


def check(name,condition):
    report['checks'][name]=bool(condition)
    assert condition,name


def lookup(domain='RNA',name='Object',member='',offset=0,limit=32):
    return execute(dict(domain=domain,name=name,member=member,offset=offset,limit=limit))


def main():
    before=(len(bpy.data.objects),len(bpy.data.meshes),tuple(tuple(v.co) for v in bpy.data.objects['Cube'].data.vertices))
    result=lookup(member='location')
    check('installed_property_shape',result['property']['type']=='FLOAT' and result['property']['array_length']==3)
    result=lookup(member='modifiers')
    check('installed_collection_type',result['property']['type']=='COLLECTION' and bool(result['property']['target_type']))
    result=lookup(name='Action',member='layers')
    check('current_slotted_animation_api',result['property']['type']=='COLLECTION')
    result=lookup(member='evaluated_get')
    check('installed_function_arguments',any(p['name']=='depsgraph' for p in result['function']['parameters']))
    result=lookup(name='Mesh',member='from_pydata')  # Blender's Python layer, not RNA.
    check('python_layer_function',result['function']['kind']=='python_function'
          and result['function']['signature'].startswith('from_pydata(') and 'vertices' in result['function']['signature'])
    check('python_layer_members_listed',any(v['name']=='from_pydata' and v['kind']=='python_function'
                                            for v in lookup(name='Mesh',limit=64,offset=0)['items']
                                            + lookup(name='Mesh',limit=64,offset=64)['items']))
    result=lookup(name='Modifier',member='type')
    check('bounded_enumeration',len(result['property']['choices'])<=64 and 'choices_truncated' in result['property'])
    first=lookup(limit=5)
    next_page=lookup(offset=first['next_offset'],limit=5)
    check('stable_paginated_members',len(first['items'])==5 and {v['name'] for v in first['items']}.isdisjoint(v['name'] for v in next_page['items']))
    result=lookup(domain='BMESH',name='extrude_face_region')
    check('native_bmesh_documentation',bool(result['documentation']) and not result['scene_changed'])
    check('bounded_type_directory',len(lookup(name='',limit=7)['items'])==7)
    # A registered add-on's settings types are not attributes of bpy.types in this Blender (Cycles is enabled by default).
    check('addon_types_are_not_bpy_types_attributes',getattr(bpy.types,'CyclesRenderSettings',None) is None
          and type(bpy.context.scene.cycles).bl_rna.identifier=='CyclesRenderSettings')
    result=lookup(name='CyclesRenderSettings',limit=64)
    check('addon_settings_type_members',result['name']=='CyclesRenderSettings' and any(v['name']=='adaptive_threshold' for v in result['items']))
    result=lookup(name='CyclesRenderSettings',member='samples')
    check('addon_settings_property',result['property']['type']=='INT')
    directory,offset=[],0
    while offset is not None:
        page=lookup(name='',offset=offset,limit=64)
        directory+=page['items']
        offset=page['next_offset']
    check('type_directory_lists_addon_settings',{'CyclesRenderSettings','CyclesMaterialSettings','Object'}<=set(directory)
          and directory==sorted(set(directory)))
    try:
        lookup(domain='WRITABLE',name='CyclesRenderSettings')
    except ValueError as error:
        check('writable_names_the_owner_for_addon_types','Scene' in str(error) and 'Unknown' not in str(error))
    else:
        check('writable_names_the_owner_for_addon_types',False)
    # Operators, C (mesh.primitive_cube_add) and Python-defined (object.shade_smooth_by_angle), by either name.
    from angelo_agent.refusal_codes import refusal_code
    for idname,rna_name in [('mesh.primitive_cube_add','MESH_OT_primitive_cube_add'),
                            ('object.shade_smooth_by_angle','OBJECT_OT_shade_smooth_by_angle')]:
        for name in (idname,rna_name):
            result=lookup(name=name,limit=64)
            check('operator_'+name,result['operator']==idname and result['name']==rna_name and result['items'])
        member=lookup(name=rna_name,limit=64)['items'][0]['name']
        check('operator_property_'+idname,lookup(name=idname,member=member)['property']['name']==member)
    for domain,name in [('RNA','NoSuchBlenderType'),('RNA','object.no_such_operator'),('BMESH','no_such_op'),
                        ('WRITABLE','object.shade_smooth_by_angle')]:
        try:
            lookup(domain=domain,name=name)
        except ValueError as error:
            check('validation_miss_'+name,refusal_code(error)=='VALIDATION' and name in str(error))
        else:
            check('validation_miss_'+name,False)
    for name,member in [('NoSuchBlenderType',''),('Object','__dict__'),('Object','missing_member')]:
        try:
            lookup(name=name,member=member)
        except ValueError:
            check('rejected_'+name+'_'+member,True)
        else:
            check('rejected_'+name+'_'+member,False)
    check('no_scene_changes',before==(len(bpy.data.objects),len(bpy.data.meshes),tuple(tuple(v.co) for v in bpy.data.objects['Cube'].data.vertices)))
    report['success']=True


try:
    main()
except Exception:
    import traceback
    report.update(success=False,error=traceback.format_exc())
(OUT/'results.json').write_text(json.dumps(report,indent=2))
print('ANGELO_API_LOOKUP',json.dumps(report),flush=True)
if not report.get('success'):
    raise RuntimeError(report['error'])
