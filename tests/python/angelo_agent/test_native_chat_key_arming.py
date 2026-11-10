# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual modal keyboard block and approval dispatch, without Blender/GPU execution.

The old actual modal path remains callable: replacement assertions fail
ordinarily, rather than because a new helper API is missing.
"""
import suite_isolation  # noqa: F401
import ast
from contextlib import nullcontext
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import canvas_controls, canvas_focus, review_again
from angelo_agent.pending_delivery import ToolCall
from angelo_agent.request_fences import DeliveryOwner


def event(value, repeat=False):
    return NS(type='RET', value=value, shift=False, ctrl=False, alt=False, oskey=False, is_repeat=repeat)


def drivers(state, invoked):
    tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent/chat_view.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ANGELO_OT_chat_input')
    modal = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'modal')
    index = next(i for i, n in enumerate(modal.body) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'key'
                 and isinstance(n.value.func.value, ast.Name)
                 and n.value.func.value.id in ('canvas_controls', 'chat_key_arming'))
    body = modal.body[index:index + 2]
    if index and isinstance(modal.body[index - 1], ast.FunctionDef) and modal.body[index - 1].name == 'activate_control':
        body = [modal.body[index - 1], *body]
        # The imports and assignments right above it that it closes over (held_window = window_ref(window)).
        used = {n.id for n in ast.walk(modal.body[index - 1]) if isinstance(n, ast.Name)}
        start = index - 1
        while start and isinstance(modal.body[start - 1], (ast.Assign, ast.ImportFrom)):
            bound = ({a.asname or a.name for a in modal.body[start - 1].names} if isinstance(modal.body[start - 1], ast.ImportFrom)
                     else {n.id for t in modal.body[start - 1].targets for n in ast.walk(t) if isinstance(n, ast.Name)})
            if not bound & used:
                break
            used |= {n.id for n in ast.walk(modal.body[start - 1]) if isinstance(n, ast.Name)}
            start -= 1
        body = [*modal.body[start:index - 1], *body]
    function = ast.parse("def drive(controller, state, event):\n    pass\n").body[0]
    function.body = body + [ast.Return(value=ast.Constant(value=None))]
    dispatch = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'dispatch_action')
    branch = next(n for n in ast.walk(dispatch) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "action == 'approve'")
    send = next(n for n in ast.walk(dispatch) if isinstance(n, ast.If)
                and ast.unparse(n.test) == "action == 'send'")
    native = ast.parse("def dispatch_action(context, controller, action, value):\n    pass\n").body[0]
    native.body = [ast.If(test=branch.test, body=branch.body,
                         orelse=[ast.If(test=send.test, body=send.body, orelse=[])])]
    context = NS(temp_override=lambda **_kw: nullcontext())
    namespace = dict(__package__='angelo_agent', canvas_controls=canvas_controls, state=state,
                     review_again=review_again, context=context, window=object(), region=object(),
                     area=NS(tag_redraw=lambda: None),
                     console=NS(execute=lambda context: invoked.append({'send': True})),
                     bpy=NS(context=context, ops=NS(angelo=NS(approve=lambda *a, **kw: invoked.append(kw)))))
    if any(isinstance(n, ast.Name) and n.id == 'chat_key_arming' for n in ast.walk(function)):
        namespace['chat_key_arming'] = importlib.import_module('angelo_agent.chat_key_arming')
    module = ast.fix_missing_locations(ast.Module(body=[native, function], type_ignores=[]))
    exec(compile(module, '<actual-chat-keyboard-block>', 'exec'), namespace)
    return namespace['drive'], namespace


class Host:
    def __init__(self, call):
        self.state = {'id': 'chat-A'}
        self.pending = {call.id: call}
        self._epoch, self._request_generation, self._worker_request = 1, 2, None
        self._closing = False


class KeyboardOwnership(unittest.TestCase):
    def setUp(self):
        self.call = ToolCall('keyboard-call', 'angelo_execute_python', {}, None, approval=True,
                             epoch=1, generation=2, delivery_owner=DeliveryOwner())
        self.host = Host(self.call)
        review_again.forget(self.call.id)
        review_again.drew(self.host, self.call.id)
        self.state = {'session': 'chat-A', 'hits': [((0, 0, 100, 30), 'approve', self.call.id)],
                      'focus': ('approve', self.call.id), 'approval_review': (self.call.id, object())}
        self.invoked = []
        self.drive, self.namespace = drivers(self.state, self.invoked)

    def tearDown(self):
        review_again.forget(self.call.id)

    def press(self):
        self.drive(self.host, self.state, event('PRESS'))

    def release(self):
        self.drive(self.host, self.state, event('RELEASE'))

    def replace(self):
        self.host.pending[self.call.id] = ToolCall(self.call.id, self.call.name, {}, None, approval=True,
            epoch=self.call.epoch, generation=self.call.generation, delivery_owner=self.call.delivery_owner)
        self.state['approval_review'] = (self.call.id, object())
        review_again.drew(self.host, self.call.id)

    def test_unchanged_production_toolcall_activates_once_on_release(self):
        self.press()
        self.assertEqual(self.invoked, [])
        self.release()
        self.assertEqual(len(self.invoked), 1)
        self.release()
        self.assertEqual(len(self.invoked), 1)

    def test_same_id_replacement_cannot_borrow_new_draw(self):
        self.press(); self.replace(); self.release()
        self.assertEqual(self.invoked, [])

    def test_changed_review_generation_cannot_borrow_new_draw(self):
        self.press()
        self.call.review_generation = 1
        review_again.drew(self.host, self.call.id)
        self.release()
        self.assertEqual(self.invoked, [])

    def test_changed_review_token_is_not_current_draw_fallback(self):
        self.press()
        self.state['approval_review'] = (self.call.id, object())
        self.release()
        self.assertEqual(self.invoked, [])

    def test_changed_request_generation_cancels_release(self):
        self.press(); self.host._request_generation += 1; self.release()
        self.assertEqual(self.invoked, [])

    def test_changed_controller_epoch_cancels_release(self):
        self.press(); self.host._epoch += 1; self.release()
        self.assertEqual(self.invoked, [])

    def test_changed_call_epoch_or_generation_cancels_release(self):
        for field in ('epoch', 'generation'):
            with self.subTest(field=field):
                before = getattr(self.call, field)
                self.press()
                setattr(self.call, field, before + 1)
                self.release()
                self.assertEqual(self.invoked, [])
                setattr(self.call, field, before)

    def test_changed_worker_request_cancels_release(self):
        self.press(); self.host._worker_request = ('new', 3); self.release()
        self.assertEqual(self.invoked, [])

    def test_changed_delivery_owner_cancels_release(self):
        self.press(); self.call.delivery_owner = DeliveryOwner(); self.release()
        self.assertEqual(self.invoked, [])

    def test_closed_delivery_owner_cancels_release(self):
        self.press(); self.call.delivery_owner.close(); self.release()
        self.assertEqual(self.invoked, [])

    def test_replacement_controller_with_same_session_cancels_release(self):
        self.press(); self.host = Host(self.call); self.release()
        self.assertEqual(self.invoked, [])

    def test_changed_session_cancels_same_control_release(self):
        self.press(); self.host.state['id'] = 'chat-B'; self.release()
        self.assertEqual(self.invoked, [])

    def test_nonapproval_send_is_also_bound_to_press_session(self):
        self.state.update(hits=[((0, 0, 100, 30), 'send', None)], focus=('send', None))
        self.press(); self.host.state['id'] = 'chat-B'; self.release()
        self.assertEqual(self.invoked, [])

    def test_repeat_press_cannot_refresh_replacement_ownership(self):
        self.press(); self.replace()
        self.drive(self.host, self.state, event('PRESS', repeat=True))
        self.release()
        self.assertEqual(self.invoked, [])

    def test_lost_focus_discards_proof_even_after_focus_returns(self):
        self.press()
        tab = event('PRESS'); tab.type = 'TAB'
        self.drive(self.host, self.state, tab)  # Existing Tab leaves the only control.
        self.drive(self.host, self.state, tab)  # Existing Tab re-enters it.
        self.release()
        self.assertEqual(self.invoked, [])

    def test_other_activation_key_release_cannot_use_this_press(self):
        self.press()
        release = event('RELEASE'); release.type = 'SPACE'
        self.drive(self.host, self.state, release)
        self.assertEqual(self.invoked, [])

    def test_keyboard_dispatch_restores_pointer_press_metadata(self):
        previous = {'pressed_generation': (self.call.id, 99), 'pressed_approval_review': ('pointer', object())}
        self.state.update(previous)
        self.press(); self.release()
        self.assertEqual(len(self.invoked), 1)
        self.assertEqual({key: self.state[key] for key in previous}, previous)

    def test_exception_during_dispatch_still_restores_pointer_metadata(self):
        previous = {'pressed_generation': (self.call.id, 99), 'pressed_approval_review': ('pointer', object())}
        self.state.update(previous)
        def fail(*a, **kw):
            raise RuntimeError('native dispatch failed')
        self.namespace['bpy'].ops.angelo.approve = fail
        self.press()
        with self.assertRaises(RuntimeError):
            self.release()
        self.assertEqual({key: self.state[key] for key in previous}, previous)


if __name__ == '__main__':
    unittest.main()
