# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Fab converter's parent side: how the child is started, what of its answer is believed, and the cache.

The child itself runs only in the compiled app (``fab_formats_native_check.py``); here it is replaced by a stand-in
that writes the answer a child would. Hostile answers (unknown codes, wrong types, oversized files) and hostile
paths (quotes in the work folder) are generated.
"""
import suite_isolation  # noqa: F401
import team_library_fixtures  # noqa: F401  Puts the add-on on the path.
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from host_test_policy import environment_skip
from angelo_agent import fab_convert as convert, fab_formats as formats
from angelo_agent.fab_catalog import FabError

OBJ = b'o Chair\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'
ITEM = SimpleNamespace(title='Modern Minimalist Chair Collection Pack')


class CommandTests(unittest.TestCase):
    def test_child_flags_never_run_scripts_or_reach_the_network(self):
        line = convert.command('/A/Angelo.app/Contents/MacOS/Angelo', 'job.json', 'result.json', '/w',
                               platform='linux')
        for flag in ('--background', '--factory-startup', '--disable-autoexec', '--offline-mode', '-noaudio'):
            self.assertIn(flag, line)
        self.assertNotIn('--enable-autoexec', line)
        self.assertEqual(line[line.index('--python') + 1], str(convert.CHILD))
        self.assertEqual(line[-3:], ['--', 'job.json', 'result.json'])

    @unittest.skipUnless(Path('/usr/bin/sandbox-exec').exists(), environment_skip('sandbox-exec exists on macOS only'))
    def test_macos_child_runs_in_a_sandbox_with_paths_as_parameters(self):
        with tempfile.TemporaryDirectory(prefix='quote"(deny default)') as work:
            line = convert.command('/Applications/Angelo.app/Contents/MacOS/Angelo', 'j', 'r', work, platform='darwin')
        self.assertEqual(line[0], '/usr/bin/sandbox-exec')
        profile = line[line.index('-p') + 1]
        self.assertEqual(profile, convert.sandbox_profile())
        self.assertNotIn('quote', profile)   # Paths reach the profile only as -D parameters.
        self.assertIn('APP=/Applications/Angelo.app', line)
        self.assertFalse(any(part.startswith('HOME=') for part in line))
        # Security review F3: deny by default; reads only the system, the app and the work folder; no network.
        self.assertTrue(profile.startswith('(version 1)(deny default)'))
        self.assertNotIn('(allow default)', profile)
        self.assertNotIn('network', profile.replace('(deny default)', ''))
        reads = re.search(r'\(allow file-read\*(.*?)\)\(', profile).group(1)
        self.assertNotRegex(reads, r'"/Users|"/private/etc|"/Library|"/Volumes|"/private/tmp')
        self.assertIn('(subpath (param "WORK"))', reads)
        writes = re.search(r'\(allow file-write\*(.*?)\)\(', profile + '(').group(1)
        self.assertEqual(set(re.findall(r'\((?:subpath|literal) (?:\(param "[A-Z]+"\)|"[^"]*")\)', writes)),
                         {'(subpath (param "WORK"))', '(literal "/dev/null")'})

    def test_environment_is_minimal_and_points_every_user_folder_into_the_work_folder(self):
        with tempfile.TemporaryDirectory() as work, patch.dict(os.environ, {
                'PYTHONPATH': '/evil', 'PYTHONSTARTUP': '/evil/start.py', 'BLENDER_USER_SCRIPTS': '/person/scripts',
                'ANGELO_STATE_DIR': '/person/state', 'OPENAI_API_KEY': 'secret', 'PATH': '/usr/bin'}):
            env = convert.environment(work)
            for key in ('PYTHONPATH', 'PYTHONSTARTUP', 'ANGELO_STATE_DIR', 'OPENAI_API_KEY'):
                self.assertNotIn(key, env)
            for key in ('HOME', 'TMPDIR', 'BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS', 'BLENDER_USER_DATAFILES',
                        'BLENDER_USER_EXTENSIONS'):
                self.assertTrue(env[key].startswith(work), key)
                self.assertTrue(Path(env[key]).is_dir(), key)


class LifetimeTests(unittest.TestCase):
    """Security review F6: the child keeps its own deadline and dies with the app."""

    def test_the_job_gives_the_child_its_deadline_and_parent(self):
        seen = {}

        class Started:
            pid, returncode = 4242, 0

            def __init__(self, command, **options):
                seen['job'] = json.loads(Path(command[command.index('--') + 1]).read_text())
                Path(command[-1]).write_text(json.dumps({'ok': True, 'name': 'Chair', 'id_type': 'COLLECTION'}))

            def poll(self):
                return 0
        with tempfile.TemporaryDirectory() as work, patch.object(convert, '_binary', '/bin/true'), \
                patch.object(convert.subprocess, 'Popen', Started), \
                patch.object(convert, '_kill_on_close_job', lambda process: None):   # A stand-in has no handle.
            convert.run({'kind': 'model'}, work, lambda: None, timeout=90)
        self.assertEqual(seen['job']['deadline_seconds'], 90)
        self.assertEqual(seen['job']['parent_pid'], os.getpid())

    def test_the_app_binary_is_read_at_registration_never_when_a_job_starts(self):
        """Read on the main thread when the add-on registers; starting a job needs no bpy (VM CI fab_live_host)."""
        import ast
        addon = Path(convert.__file__).parent
        register = next(n for n in ast.parse((addon / '__init__.py').read_text()).body
                        if isinstance(n, ast.FunctionDef) and n.name == 'register')
        self.assertIn('fab_convert.register()', ast.unparse(register))
        own = next(n for n in ast.parse(Path(convert.__file__).read_text()).body
                   if isinstance(n, ast.FunctionDef) and n.name == 'register')
        self.assertIn('set_binary(bpy.app.binary_path)', ast.unparse(own))
        start = next(n for n in ast.parse((addon / 'fab_native_job.py').read_text()).body
                     if isinstance(n, ast.FunctionDef) and n.name == 'start')
        body = start.body   # Its own statements; a nested callback runs later, on the main thread.
        self.assertNotIn('bpy', {a.name for s in body for n in ast.walk(s) if isinstance(n, ast.Import)
                                 and not any(isinstance(f, ast.FunctionDef) and n in ast.walk(f)
                                             for f in ast.walk(s)) for a in n.names})
        self.assertNotIn('set_binary', ast.unparse(start))
        self.assertNotIn('binary_path', ast.unparse(start))

    def test_windows_children_are_bound_to_a_kill_on_close_job(self):
        source = Path(convert.__file__).read_text()
        self.assertIn('JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE', source)
        self.assertRegex(source, r"if sys\.platform == 'win32':\n\s+\w+ = _kill_on_close_job\(process\)")

    @unittest.skipUnless(os.name == 'nt', environment_skip('Job Objects exist on Windows only'))
    def test_the_job_object_holds_a_real_child_and_kills_it_on_close(self):
        import subprocess
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            job = convert._kill_on_close_job(child)
            self.assertIsNotNone(job)
            convert._close_job(job)
            child.wait(timeout=10)
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.kill()


class ResultTests(unittest.TestCase):
    def setUp(self):
        owner = tempfile.TemporaryDirectory()
        self.addCleanup(owner.cleanup)
        self.path = Path(owner.name) / 'result.json'
        self.job = {'kind': 'model'}

    def answer(self, value):
        self.path.write_text(json.dumps(value))
        return convert.checked_result(self.path, self.job)

    def test_only_known_fields_of_known_types_are_kept(self):
        result = self.answer({'ok': True, 'name': 'Chair', 'id_type': 'COLLECTION', 'objects': 3, 'meshes': 'many',
                              'missing_textures': -1, 'unit_scale': 0.01, 'secret': '/Users/someone',
                              'timings': {'import': 1.5, 'evil': 2, 'write': 'x'},
                              'surface': {'roles': ['base_color', 'evil'], 'normal': 'sideways'}})
        self.assertEqual(result['objects'], 3)
        self.assertEqual(result['meshes'], 0)
        self.assertEqual(result['missing_textures'], 0)
        self.assertEqual(result['unit_scale'], 0.01)
        self.assertEqual(result['timings'], {'import': 1.5})
        self.assertEqual(result['surface'], {'roles': ['base_color'], 'normal': None})
        self.assertNotIn('secret', json.dumps(result))

    def test_refusals_become_fixed_sentences(self):
        for code in convert.MESSAGES:
            with self.subTest(code=code), self.assertRaises(convert.ConvertRefused) as refused:
                self.answer({'ok': False, 'error': code})
            self.assertEqual(str(refused.exception), convert.MESSAGES[code])
            self.assertEqual(refused.exception.code, 'UNSUPPORTED')
            self.assertIsInstance(refused.exception, FabError)
        with self.assertRaises(convert.ConvertRefused) as refused:
            self.answer({'ok': False, 'error': 'Your files at /Users/x were deleted'})
        self.assertEqual(str(refused.exception), convert.MESSAGES['FAILED'])

    def test_a_wrong_datablock_kind_name_or_oversized_answer_is_a_failure(self):
        for value in ({'ok': True, 'name': 'Chair', 'id_type': 'MATERIAL'}, {'ok': True, 'name': '', 'id_type': 'COLLECTION'},
                      {'ok': True, 'name': 'x' * 64, 'id_type': 'COLLECTION'}, ['ok'], 'ok'):
            with self.subTest(value=value), self.assertRaises(convert.ConvertRefused):
                self.answer(value)
        self.path.write_text(' ' * (convert.MAX_RESULT + 1))
        with self.assertRaises(convert.ConvertRefused):
            convert.checked_result(self.path, self.job)


class CacheTests(unittest.TestCase):
    def setUp(self):
        owner = tempfile.TemporaryDirectory()
        self.addCleanup(owner.cleanup)
        self.folder = Path(owner.name)
        self.download = self.folder / 'download.bin'
        with zipfile.ZipFile(self.download, 'w') as zipped:
            zipped.writestr('Chairs Pack.obj', OBJ)
            zipped.writestr('read me.txt', 'thanks')
        self.digest = hashlib.sha256(self.download.read_bytes()).hexdigest()
        self.jobs = []

    def fake_run(self, job, work, check, *, timeout):
        self.jobs.append(dict(job, timeout=timeout, files=sorted(p.name for p in Path(job['folder']).iterdir())))
        Path(job['output']).write_bytes(b'BLENDER-v502 converted')
        return {'name': job['datablock'], 'id_type': 'COLLECTION', 'objects': 1}

    def convert(self):
        with patch.object(convert, 'run', self.fake_run):
            return convert.cached(self.folder, self.download, self.digest, formats.BY_CODE['obj'],
                                  {'container': 'zip', 'content': 'zip'}, ITEM, lambda: None)

    def test_converts_once_then_reuses_and_leaves_no_unpacked_files(self):
        path, size, digest, result = self.convert()
        self.assertEqual(path, self.folder / 'converted.blend')
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertFalse(result['reused'])
        self.assertEqual(self.jobs[0]['models'], ['Chairs Pack.obj'])
        self.assertEqual(self.jobs[0]['files'], ['Chairs Pack.obj'])
        self.assertEqual(self.jobs[0]['datablock'], ITEM.title)
        self.assertEqual([p.name for p in self.folder.iterdir() if p.name.startswith('.convert-')], [])
        again = self.convert()
        self.assertTrue(again[3]['reused'])
        self.assertEqual(len(self.jobs), 1)

    def test_a_changed_conversion_or_converter_version_converts_again(self):
        self.convert()
        (self.folder / 'converted.blend').write_bytes(b'tampered')
        self.convert()
        self.assertEqual(len(self.jobs), 2)
        with patch.object(convert, 'VERSION', convert.VERSION + 1):
            self.convert()
        self.assertEqual(len(self.jobs), 3)

    def test_archive_and_format_refusals_reach_the_person_as_their_sentence(self):
        with zipfile.ZipFile(self.download, 'w') as zipped:
            zipped.writestr('notes.txt', 'no model here')
        with self.assertRaises(convert.ConvertRefused) as refused:
            self.convert()
        self.assertIn('no OBJ model', str(refused.exception))
        self.assertEqual(self.jobs, [])

    def test_without_the_app_binary_the_converter_refuses(self):
        with patch.object(convert, '_binary', None), self.assertRaises(convert.ConvertRefused) as refused:
            convert.run({'kind': 'model'}, self.folder, lambda: None, timeout=1)
        self.assertEqual(str(refused.exception), convert.MESSAGES['SANDBOX'])


class SourceTests(unittest.TestCase):
    """The child modules run only in the converter process; nothing in the add-on imports them."""

    def test_child_modules_are_never_imported_by_the_add_on(self):
        addon = Path(convert.__file__).parent
        child = {'fab_convert_child', 'fab_convert_clean', 'fab_convert_material'}
        for path in addon.glob('*.py'):
            if path.stem in child:
                continue
            text = path.read_text(encoding='utf-8')
            for name in child:
                with self.subTest(module=path.name, child=name):
                    self.assertIsNone(re.search(r'import\s+[\w., ]*\b%s\b|from\s+\S*\b%s\b' % (name, name), text))


if __name__ == '__main__':
    unittest.main()
