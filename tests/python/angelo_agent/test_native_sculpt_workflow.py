# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
import copy
import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/addons_core'))
from angelo_agent.sculpt_spec import choose_level, resample_paths, SCULPT_MUTATIONS, ANCHORED
from angelo_agent.tool_specs import validate_call, BOUNDED_MUTATIONS, COLLABORATOR_TOOLS


def step():
    return {'brush':'CLAY_STRIPS','radius':.4,'strength':.25,'direction':'ADD','symmetry':'NONE',
            'normal':[0,-1,0],'front_faces_only':True,'spacing':.2,
            'paths':[[{'position':[0,-1,0],'pressure':.4,'radius_scale':.5,'tilt':[0,0]},
                      {'position':[.2,-1,0],'pressure':.8,'radius_scale':1.5,'tilt':[.6,-.2]}]]}


class SculptWorkflowContractTests(unittest.TestCase):
    def test_controls_interpolate_with_endpoints_and_copy(self):
        original=step(); source=copy.deepcopy(original)
        checked=validate_call('angelo_sculpt_batch',dict(original,object='Sphere',mesh_revision='a'*64))
        points=resample_paths(checked['paths'],.05)[0]
        self.assertAlmostEqual(points[2]['radius_scale'],1)
        self.assertAlmostEqual(points[2]['tilt'][0],.3)
        self.assertEqual(points[-1]['radius_scale'],1.5)
        checked['paths'][0][0]['tilt'][0]=1
        self.assertEqual(original,source)

    def test_optional_controls_fail_closed(self):
        for field,value in [('falloff','EXECUTE'),('automasking',['FACE_SETS','FACE_SETS']),('automasking','TOPOLOGY'),('region','\x00')]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    validate_call('angelo_sculpt_batch',dict(step(),object='Sphere',mesh_revision='a'*64,**{field:value}))
        for field,value in [('radius_scale',math.nan),('radius_scale',0),('radius_scale',2.1),('tilt',[0,math.inf]),('tilt',[1,2,3])]:
            args=dict(step(),object='Sphere',mesh_revision='a'*64);args['paths'][0][0][field]=value
            with self.assertRaises(ValueError): validate_call('angelo_sculpt_batch',args)

    def test_program_is_bounded_as_a_whole(self):
        args={'object':'Sphere','mesh_revision':'a'*64,'steps':[step(),step()]}
        checked=validate_call('angelo_sculpt_program',args)
        self.assertEqual(len(checked['steps']),2)
        checked['steps'][0]['paths'][0][0]['position'][0]=9
        self.assertEqual(args['steps'][0]['paths'][0][0]['position'][0],0)
        for steps in ([],[step()]*9,[dict(step(),code='anything')]):
            with self.assertRaises(ValueError):validate_call('angelo_sculpt_program',dict(args,steps=steps))
        big=step();big.update(brush='DRAW',radius=.02,spacing=.1);big['paths'][0][0]['radius_scale']=1
        # Each pass is admitted, but eight together must not multiply the budget.
        validate_call('angelo_sculpt_batch',dict(big,object='Sphere',mesh_revision='a'*64))
        with self.assertRaisesRegex(ValueError,'whole sculpt program'):
            validate_call('angelo_sculpt_program',dict(args,steps=[big]*8))

    def test_anchored_brushes_keep_two_endpoints(self):
        for brush in ANCHORED:
            args=step();args['brush']=brush
            validate_call('angelo_sculpt_batch',dict(args,object='Sphere',mesh_revision='a'*64))
            self.assertEqual(len(resample_paths(args['paths'],.00001,grab=True)[0]),2)

    def test_readonly_roles_cannot_define_regions_or_change_levels(self):
        self.assertTrue(SCULPT_MUTATIONS <= BOUNDED_MUTATIONS)
        self.assertFalse(SCULPT_MUTATIONS & COLLABORATOR_TOOLS)
        self.assertIn('angelo_sculpt_review',COLLABORATOR_TOOLS)

    def test_region_and_level_parameters_are_bounded(self):
        args={'object':'Sphere','mesh_revision':'a'*64,'name':'Eyelid','position':[0,-1,0],'radius':.2,'face_set':0}
        validate_call('angelo_sculpt_region',args)
        for field,value in [('name','x'*65),('face_set',True),('face_set',-1),('position',[0,math.nan,0])]:
            with self.assertRaises(ValueError):validate_call('angelo_sculpt_region',dict(args,**{field:value}))
        args={'object':'Sphere','mesh_revision':'a'*64,'mode':'AUTO','stage':'DETAIL','level':0,'radius':.2}
        for field,value in [('level',True),('level',13),('stage','UNKNOWN'),('radius',0)]:
            with self.assertRaises(ValueError):validate_call('angelo_sculpt_resolution',dict(args,**{field:value}))

    def test_auto_resolution_uses_lowest_adequate_existing_level(self):
        counts=[100,400,1600,6400,25600]
        self.assertEqual(choose_level(4,'FORMS',1,5,counts),(1,True))
        self.assertEqual(choose_level(4,'DETAIL',1,5,counts),(2,True))
        self.assertEqual(choose_level(4,'DETAIL',.001,5,counts),(4,False))
        self.assertEqual(choose_level(2,'DETAIL',.001,5,[100,400,1600000]),(1,False))

    def test_review_rejects_invalid_or_unbounded_image_goals(self):
        args={'object':'Sphere','baseline':'d7683281-89d0-4293-96b5-d4a35e3078cb','outline':[],'landmarks':[]}
        validate_call('angelo_sculpt_review',args)
        for field,value in [('baseline','file:///tmp/reference.png'),('outline',[[0,0],[1,1]]),('outline',[[0,0],[1,0],[math.inf,1]]),('landmarks',[{'region':'eye','pixel':[1.1,0]}])]:
            with self.assertRaises((ValueError,TypeError)):validate_call('angelo_sculpt_review',dict(args,**{field:value}))

if __name__=='__main__': unittest.main()
