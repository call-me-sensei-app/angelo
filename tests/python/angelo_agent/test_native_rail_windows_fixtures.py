# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows rail receipts: no-hidden paging and same-frame shelf-clearance observations."""
import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import test_native_session_rail_strip as strips
import rail_positions_checks as geometry
import angelo_agent
from angelo_agent import session_rail as rail

HERE = Path(__file__).parent


def function(file, name, scope):
    """Load a fixture function without starting its Blender timer or importing native GPU modules."""
    tree = ast.parse((HERE / file).read_text())
    node = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    if node is None and file == 'test_blender_rail_positions.py':   # Replay the pre-correction fixture.
        return function('rail_positions_stages.py' if name == 'scrolling' else 'rail_positions_fixture.py', name, scope)
    exec(compile(ast.Module(body=[node], type_ignores=[]), file, 'exec'), scope)
    return scope[name]


class ScrollFixtureTests(unittest.TestCase):
    def evaluate(self, workers):
        states = strips.sessions(workers)
        layout = strips.strip.layout(states, (825, 679), 2, dict(left=0, right=0, top=76, bottom=100), 'TOP')
        self.assertTrue(layout.paged)
        checks, events = [], []
        ids = lambda: [tile.session for tile in layout.tiles]
        run = NS(to_window=lambda rect: (rect[0]+rect[2]/2,rect[1]+rect[3]/2),
                 geometry_of=lambda *a: None, shot=lambda *a: None, order=lambda: [t.session for t in states], tiles=ids)
        scope = dict(plan=lambda: layout, tiles=ids, run=run,
                     S=NS(window=NS(event_simulate=lambda **kw: events.append(kw))),
                     check=lambda name, ok, detail=None: checks.append((name, bool(ok))),
                     pause=lambda seconds: ('pause', None), until=lambda predicate: ('until', predicate))
        scope['stages'] = NS(scrolling=function('rail_positions_stages.py', 'scrolling', scope))
        gen = function('test_blender_rail_positions.py', 'scrolling', scope)('fixture')
        value = None
        for _ in range(40):
            try:
                kind, predicate = gen.send(value)
                value = bool(predicate()) if kind == 'until' else True
            except StopIteration:
                break
        return layout, checks, events

    def test_two_visible_cards_must_remain_visible_without_fake_paging(self):
        layout, checks, events = self.evaluate(1)
        self.assertEqual(layout.hidden, (0,0))
        self.assertTrue(checks)
        self.assertTrue(all(passed for _,passed in checks), checks)
        swipe = next(i for i,event in enumerate(events) if event['type']=='TRACKPADPAN')
        self.assertNotEqual((events[swipe]['x'],events[swipe]['y']),
                            (events[swipe-1]['x'],events[swipe-1]['y']))

    def test_a_nonresponding_scroll_with_hidden_workers_still_fails(self):
        layout, checks, _events = self.evaluate(3)
        self.assertTrue(any(layout.hidden))
        self.assertFalse(all(passed for _,passed in checks), checks)


class FrameFixtureTests(unittest.TestCase):
    def test_probe_records_full_session_count_fitted_scale_and_actual_pill_top(self):
        region = NS(x=0,y=0,width=800,height=605)
        header=NS(type='HEADER',alignment='TOP',x=0,y=529,width=800,height=76)
        shelf=NS(type='ASSET_SHELF_HEADER',alignment='BOTTOM',x=0,y=0,width=800,height=33)
        area=NS(as_pointer=lambda:11,regions=[region,header,shelf])
        window=NS(as_pointer=lambda:7)
        tiles=[NS(session=s,kind='idle',status='Idle') for s in ('lead','w1')]
        plan=NS(tiles=tiles,panel=(0,100,300,371),scale=1.5)
        records=[]
        scope=dict(window=window,state={},bpy=NS(context=NS(window=window,area=area,region=region,
                   preferences=NS(system=NS(ui_scale=1.5)))),view_area=lambda:area,view_region=lambda:region,
                   session_rail_draw=NS(last={7:(11,plan)}),rail_pictures=NS(_drawn={}),
                   live_view_draw=NS(_offscreens={}),_bpy=NS(_angelo_live=NS(text_info_end=lambda r:468)),
                   parallel_sessions=NS(sessions=lambda:['lead','w1','w2','w3']),session_rail=rail,
                   time=NS(monotonic=lambda:10),frames=records)
        pills=NS(places={11:(7,(('studio',(10,42,200,42)),))})
        with patch.dict(sys.modules,{'angelo_agent.asset_shelf_add':pills}), \
                patch.object(angelo_agent,'asset_shelf_add',pills,create=True):
            function('test_blender_session_rail.py','probe',scope)()
        frame=records[-1]
        self.assertIn('bottom',frame)
        self.assertEqual((frame['bottom'],frame['headers'],frame['session_count'],frame['rail_scale']),
                         (84,76,4,1.5))
        inset=rail.fit_below_text(frame['headers'],605-468,frame['session_count'],605,1.5,frame['bottom'])
        self.assertEqual(605-inset-rail.MARGIN*frame['rail_scale'],471)


class TinyHitFixtureTests(unittest.TestCase):
    def test_absent_button_in_a_roomy_view_does_not_trigger_a_full_pixel_scan(self):
        region=NS(type='WINDOW',alignment='NONE',x=0,y=0,width=708,height=502)
        area=NS(regions=[region])
        calls=[]
        def hit(pointer,x,y):
            calls.append(1)
            return False
        fake=NS(hit=hit)
        scope=dict(window_region=lambda window:(area,region),geometry=geometry,
                   bpy=NS(context=NS(preferences=NS(system=NS(ui_scale=3)))))
        scope['fx']=NS(window_region=scope['window_region'])
        scope['_coarse_find_circle']=function('rail_positions_fixture.py','find_circle',scope)
        with patch.dict(sys.modules,{'angelo_agent.rail_minimized':fake}), \
                patch.object(angelo_agent,'rail_minimized',fake,create=True):
            self.assertEqual(function('test_blender_rail_positions.py','find_circle',scope)(NS(as_pointer=lambda:7)),
                             (None,None))
        self.assertLess(len(calls),5000)

    def test_hit_search_finds_a_one_pixel_disc_in_a_narrow_visible_strip(self):
        region=NS(type='WINDOW',alignment='NONE',x=0,y=0,width=708,height=502)
        toolbar=NS(type='TOOLS',alignment='LEFT',x=0,y=0,width=169,height=502)
        sidebar=NS(type='UI',alignment='RIGHT',x=171,y=0,width=537,height=502)
        area=NS(regions=[region,toolbar,sidebar])
        window=NS(as_pointer=lambda:7)
        fake=NS(hit=lambda pointer,x,y:(x-170)**2+(y-17)**2<=1)
        for scale in (3,8):
            scope=dict(window_region=lambda window:(area,region),geometry=geometry,
                       bpy=NS(context=NS(preferences=NS(system=NS(ui_scale=scale)))))
            scope['fx']=NS(window_region=scope['window_region'])
            scope['_coarse_find_circle']=function('rail_positions_fixture.py','find_circle',scope)
            with patch.dict(sys.modules,{'angelo_agent.rail_minimized':fake}), \
                    patch.object(angelo_agent,'rail_minimized',fake,create=True):
                found,frame=function('test_blender_rail_positions.py','find_circle',scope)(window)
            self.assertEqual((found,frame),((170,17,1),'window'),scale)


if __name__=='__main__':
    unittest.main()
