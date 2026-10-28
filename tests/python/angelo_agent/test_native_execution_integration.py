# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Public registry/provider glue: no provider process or paid inference."""
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/addons_core'))
from angelo_agent.program_checks import required_checks
from angelo_agent.tool_specs import TOOLS, TOOL_MAP, BOUNDED_MUTATIONS, COLLABORATOR_TOOLS, validate_call, dynamic_tools, native_plan_schema
from angelo_agent.providers import tool_content, _invalidates_scene_images, _record_runtime_round
from angelo_agent.references import ReferenceStore


def expand_refs(value,root):
    if isinstance(value,list):
        return [expand_refs(item,root) for item in value]
    if not isinstance(value,dict):
        return value
    if set(value)=={'$ref'}:
        reference=value['$ref']
        if not reference.startswith('#/$defs/'):
            raise AssertionError('Unexpected reference scope')
        return expand_refs(root['$defs'][reference.rsplit('/',1)[-1]],root)
    return {key:expand_refs(item,root) for key,item in value.items() if key!='$defs'}


class ExecutionIntegrationTests(unittest.TestCase):
    def test_unique_tools_and_closed_native_gate(self):
        self.assertEqual(len(TOOLS),len(TOOL_MAP))
        self.assertIn('angelo_scene_program',BOUNDED_MUTATIONS)
        self.assertNotIn('angelo_memory_update',BOUNDED_MUTATIONS)
        self.assertNotIn('angelo_execute_python',BOUNDED_MUTATIONS)
        self.assertTrue({'angelo_scene_search','angelo_scene_verify','angelo_blender_api'}<=COLLABORATOR_TOOLS)
        self.assertFalse({'angelo_scene_program','angelo_accept_preview','angelo_memory_update'}&COLLABORATOR_TOOLS)

    def test_program_defaults_are_validated_before_review(self):
        value={'mode':'PREVIEW','read_set':[],'write_set':[],
               'operations':[{'kind':'create_primitive','target':None,'parameters':{'primitive':'CUBE','alias':'body'}}]}
        result=validate_call('angelo_scene_program',value)
        self.assertEqual(result['operations'][0]['parameters']['scale'],[1,1,1])
        # The host derives what the defaults state: location, and an unrotated cube's size.
        self.assertEqual(required_checks(result),[
            {'kind':'transform','target':'$body','property':'location','expected':[0.0,0.0,0.0]},
            {'kind':'dimensions','target':'$body','expected':[2.0,2.0,2.0],'space':'WORLD'}])
        self.assertEqual(result,validate_call('angelo_scene_program',result))
        with self.assertRaises(ValueError):
            validate_call('angelo_scene_program',dict(value,code='import os'))
        with self.assertRaises(ValueError):
            validate_call('angelo_scene_program',dict(value,operations=[{'kind':'execute_python','target':None,'parameters':{'code':'pass'}}]))

    def test_compacted_input_schemas_reproduce_original_constraints(self):
        from angelo_agent.flow_tool_spec import declared_form
        from angelo_agent.plan_schema import codex_arguments
        for tool,advertised in zip(map(declared_form,TOOLS),dynamic_tools()):  # The every-turn form (flow_tool_spec).
            self.assertEqual(tool['name'],advertised['name'])
            # Program and verification items defer kinds/checks; every other tool is exact.
            expected = codex_arguments(tool)
            self.assertEqual(expected == tool['inputSchema'], tool['name'] not in {'angelo_scene_program','angelo_scene_verify'})
            self.assertEqual(expected,expand_refs(advertised['inputSchema'],advertised['inputSchema']))
        first=native_plan_schema()
        second=native_plan_schema()
        self.assertEqual(first,second)
        first['properties'].clear()
        self.assertTrue(native_plan_schema()['properties'])

    def test_schema_filter_does_not_leak_mutation_tools(self):
        native=dynamic_tools(COLLABORATOR_TOOLS)
        self.assertEqual({tool['name'] for tool in native},set(COLLABORATOR_TOOLS))
        schema=native_plan_schema(set())
        self.assertEqual(schema['properties']['operations']['maxItems'],0)

    def test_invalid_tool_names_are_validation_errors(self):
        for name in [None,[],{},'unknown']:
            with self.assertRaises(ValueError):
                validate_call(name,{})

    def test_non_scene_work_does_not_trigger_six_recaptures(self):
        for name,args in [('angelo_memory_update',{}),('angelo_scene_program',{'mode':'PREVIEW'}),
                          ('angelo_discard_preview',{}),('angelo_set_workspace_mode',{}),
                          ('angelo_studio_generate',{}),('angelo_scene_object',{})]:
            self.assertFalse(_invalidates_scene_images({'tool':name,'arguments':args}),name)
        for name,args in [('angelo_scene_program',{'mode':'APPLY'}),('angelo_accept_preview',{}),
                          ('angelo_execute_python',{}),('angelo_sculpt_program',{}),('angelo_studio_import_3d',{})]:
            self.assertTrue(_invalidates_scene_images({'tool':name,'arguments':args}),name)

    def test_preview_pixels_forwarded_without_granting_camera_authority(self):
        with tempfile.TemporaryDirectory() as root:
            refs=ReferenceStore(root,'fdb39270-73e7-47db-a059-8ca8660628bb')
            record=refs.new_observation('Staged result · not applied')
            data=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
            Path(record['path']).write_bytes(data)
            record['bytes']=len(data)
            controller=SimpleNamespace(references=refs,turn_options={})
            result={'success':True,'status':'preview','scene_changed':False,'views':[record]}
            with patch('angelo_agent.providers.selected_model',return_value={'images':True}):
                content=tool_content(controller,'angelo_scene_program',result)
            self.assertEqual([item['type'] for item in content],['inputText','inputImage'])
            self.assertFalse(json.loads(content[0]['text'])['scene_changed'])
            with self.assertRaises(ValueError):
                refs.observation_camera(record['id'])
            with patch('angelo_agent.providers.selected_model',return_value={'images':False}):
                content=tool_content(controller,'angelo_scene_program',result)
            self.assertEqual(len(content),1)
            self.assertTrue(json.loads(content[0]['text'])['success'])

    def test_provider_round_scopes_are_explicit(self):
        controller=SimpleNamespace(record_provider_turn=Mock(),record_model_round=Mock())
        _record_runtime_round(controller)
        controller.record_provider_turn.assert_called_once()
        controller.record_model_round.assert_not_called()
        _record_runtime_round(controller,claude=True)
        self.assertEqual(controller.record_provider_turn.call_count,2)
        controller.record_model_round.assert_called_once()


if __name__=='__main__':
    unittest.main()
