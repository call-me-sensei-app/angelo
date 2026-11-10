# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fresh-process import closure only; no native Windows, browser or profile acceptance."""
import suite_isolation  # noqa: F401
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


class PlatformImportTests(unittest.TestCase):
    def test_windows_common_producer_imports_do_not_load_darwin_fcntl_profile(self):
        root = Path(__file__).resolve().parents[3]
        # Test discovery legitimately imports the Mac profile in other modules. Keep this witness
        # independently fresh instead of deleting/reloading another test's live module objects.
        script = textwrap.dedent('''
            import sys
            from pathlib import Path
            root = Path(sys.argv[1])
            sys.dont_write_bytecode = True
            sys.path[:0] = [str(root / 'tests/python/angelo_agent'),
                           str(root / 'scripts/addons_core'), str(root / 'scripts/modules')]
            import suite_isolation
            import builtins
            import importlib
            from unittest.mock import patch
            assert 'angelo_agent.fab_browser_profile' not in sys.modules
            original = builtins.__import__
            def guarded(name, *args, **kwargs):
                if name == 'fcntl' or name.endswith('.fab_browser_profile'):
                    raise AssertionError('Darwin profile imported on Windows path')
                return original(name, *args, **kwargs)
            with patch.object(builtins, '__import__', side_effect=guarded):
                owner = importlib.import_module('angelo_agent.fab_browser_platform')
                with patch.object(owner, '_platform_name', return_value='win32'):
                    for name in ('fab_browser_connection', 'fab_browser_identity',
                                 'fab_browser_manual_connection', 'fab_browser_selected',
                                 'fab_browser_listing_owner'):
                        importlib.import_module('angelo_agent.' + name)
                    assert owner.browser_type().__name__ == 'WindowsBrowserProcess'
                    assert owner.profile_types()[0].__name__ == 'WindowsProfileStore'
                    assert owner.file_types()[0].__module__ == 'angelo_agent.fab_browser_download_file_windows'
            assert 'angelo_agent.fab_browser_profile' not in sys.modules
            print('FAB_WINDOWS_IMPORT_OK')
        ''')
        result = subprocess.run([sys.executable, '-I', '-c', script, str(root)],
                                cwd=root, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'FAB_WINDOWS_IMPORT_OK')
