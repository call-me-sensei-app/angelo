# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual paste scratch ownership and extracted chat callbacks; no native clipboard/GUI credit."""
import suite_isolation  # noqa: F401  First: no real provider login.

import ast
import contextlib
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import clipboard_image, draft_overflow, live_windows, private_roots  # noqa: E402


class PrivatePaste(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(mock.patch.object(private_roots, 'scratch_root', return_value=self.root))
        self.paths = []
        self.refuse = False
        def import_images(paths, existing):
            path = Path(paths[0])
            self.paths.append(path)
            folder = path.parent
            self.assertIn(folder, private_roots.registered())
            info = folder.stat()
            self.assertIn((info.st_dev, info.st_ino), private_roots.identities())
            if sys.platform != 'win32':   # Windows reports 0o777 for every folder; its privacy is the folder's ACL.
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
            self.assertTrue((folder / private_roots.OWNER).is_file())
            data = path.read_bytes()
            if self.refuse:
                raise ValueError('Attachment refused')
            return [dict(name=path.name, bytes=data)]
        self.chat = SimpleNamespace(busy=False, attachments=[], version=0, emit=mock.Mock(),
                                    references=SimpleNamespace(image_limit=1024, import_images=import_images))

    def test_overflow_text_is_private_during_import_and_removed_after_success_or_refusal(self):
        for refused in (False, True):
            with self.subTest(refused=refused):
                self.refuse = refused
                self.assertIs(draft_overflow.attach_text(self.chat, 'Whole pasted text'), not refused)
                self.assertFalse(self.paths[-1].parent.exists())
        self.assertEqual(self.chat.attachments, [dict(name=draft_overflow.FILE_NAME, bytes=b'Whole pasted text')])
        self.assertEqual(self.chat.version, 1)

    def test_clipboard_image_is_private_during_import_and_removed_after_success_or_refusal(self):
        def save(path, **limits):
            self.assertEqual(limits, dict(compression=clipboard_image.FAST,
                                         max_side=clipboard_image.MAX_SIDE, max_bytes=1024))
            Path(path).write_bytes(b'synthetic clipboard bytes')
            return dict(saved=True, refused='')
        native = SimpleNamespace(image_available=lambda: True, image_save=save)
        with mock.patch.object(clipboard_image, 'binding', return_value=native):
            for refused in (False, True):
                with self.subTest(refused=refused):
                    self.refuse = refused
                    self.assertTrue(clipboard_image.paste(self.chat, ''))
                    self.assertFalse(self.paths[-1].parent.exists())
        self.assertEqual(len(self.chat.attachments), 1)
        self.assertEqual(self.chat.version, 1)


class ChatCallbacks(unittest.TestCase):
    def source(self):
        path = ROOT / 'scripts/addons_core/angelo_agent/chat_view.py'
        return path, ast.parse(path.read_text())

    def test_keyboard_dispatch_resolves_same_window_and_never_uses_a_retired_window(self):
        path, tree = self.source()
        node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                    and node.name == 'activate_control')
        original, replacement = object(), object()
        current = [original]
        entered, dispatched = [], []
        @contextlib.contextmanager
        def override(**values):
            entered.append(values['window'])
            yield
        namespace = dict(held_window=lambda: current[0], context=SimpleNamespace(temp_override=override),
                         area=object(), region=object(), bpy=SimpleNamespace(context=object()), controller=object(),
                         dispatch_action=lambda *args: dispatched.append(args[2:]))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        activate = namespace['activate_control']
        activate('send', None)
        current[0] = None  # The actual window_ref's retired/replaced-document result.
        activate('approve', 'call')
        self.assertEqual(entered, [original])
        self.assertEqual(dispatched, [('send', None)])
        self.assertNotIn(replacement, entered)

    def test_screenless_window_keeps_editor_state_until_inventory_is_complete(self):
        import angelo_agent
        from angelo_agent import chat_history
        path, tree = self.source()
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'animate_popovers')
        area = lambda pointer: SimpleNamespace(as_pointer=lambda: pointer, regions=[])
        first, second = area(101), area(202)
        a = SimpleNamespace(screen=SimpleNamespace(areas=[first]), as_pointer=lambda: 1)
        b = SimpleNamespace(screen=None, as_pointer=lambda: 2)
        manager = SimpleNamespace(windows=[a, b])
        views = {101: {'session': 'chat-a'}, 202: {'session': 'chat-b', 'draft': 'unsent'}}
        scrolls = {(101, 'chat-a'): 3, (202, 'chat-b'): 7}
        modules = {'marble_host': SimpleNamespace(poll=lambda: None),
                   'approval_review': SimpleNamespace(prepare_canvas_reviews=lambda: False)}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(sys.modules, {'angelo_agent.' + k: v for k, v in modules.items()}))
            for name, module in modules.items():
                stack.enter_context(mock.patch.object(angelo_agent, name, module, create=True))
            namespace = dict(__package__='angelo_agent', _enabled=True, _views=views, _scroll=scrolls,
                             live_windows=live_windows, bpy=SimpleNamespace(context=SimpleNamespace(
                                 window_manager=manager, preferences=SimpleNamespace(view=SimpleNamespace(
                                     use_reduce_motion=True)))))
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
            tick = namespace['animate_popovers']
            tick()
            self.assertEqual(views[202]['draft'], 'unsent')
            self.assertEqual(scrolls[(202, 'chat-b')], 7)
            b.screen = SimpleNamespace(areas=[second])
            tick()
            self.assertEqual(views[202]['draft'], 'unsent')
            manager.windows = [a]  # Only now is the second window actually closed.
            tick()
            self.assertEqual(set(views), {101})
            self.assertEqual(scrolls, {(101, 'chat-a'): 3})


if __name__ == '__main__':
    unittest.main()
