# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pin that nothing reaching a File/Asset Browser assumes it was initialized and refreshed (1.0.6).

``SpaceFile.files`` is made at the browser's first refresh and ``SpaceFile.runtime`` at its first init: a browser just
made, read from a file or split off another one has neither. Notifier listeners, the Python API and broadcasts over
every browser can reach it before that. Each unguarded use was a SIGSEGV on a null pointer:

* ``file_listener``'s language reset (``ND_UI_LANG``) passed a null list to ``filelist_tag_force_reset``
  (1.0.5, and a deterministic repro on the 1.0.6 rehearsal build);
* a job-finished notifier of another browser reached a just-split one, whose ``runtime`` ``file_duplicate`` clears
  (the top-bar suite's Asset Browser sweep, looped under load);
* ``activate_asset_by_id`` read a null list, and its deferred form wrote into a null ``runtime``.

``test_blender_file_browser_before_refresh`` runs the deterministic ones in the app; this pins the rule in the source
so a new listener or entry point keeps it.
"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3] / 'source/blender/editors'
SPACE_FILE = ROOT / 'space_file/space_file.cc'
FILESEL = ROOT / 'space_file/filesel.cc'
ASSET_LIST = ROOT / 'asset/intern/asset_list.cc'


def code_only(text):
    """Blank out comments so that names mentioned in them are not counted as uses."""
    text = re.sub(r'/\*.*?\*/', lambda m: ' ' * len(m.group(0)), text, flags=re.S)
    return re.sub(r'//[^\n]*', lambda m: ' ' * len(m.group(0)), text)


def function(source, signature):
    """The definition starting with ``signature`` (never a forward declaration)."""
    start = re.search(re.escape(signature) + r'[^;{]*\)\s*\{', source).start()
    end = source.index('\n}\n', start) + 2
    return source[start:end]


def functions(source):
    """(name, body) of every top-level function definition."""
    for match in re.finditer(r'^[A-Za-z][^\n;{}]*?\b(\w+)\([^;{}]*\)\s*\n\{\n', source, flags=re.M):
        end = source.index('\n}\n', match.end()) + 2
        yield match.group(1), source[match.start():end]


def if_condition(body, brace):
    """The condition of the ``if (...)`` that opens the block at ``brace``, or None when that block is no ``if``."""
    index = brace - 1
    while index >= 0 and body[index].isspace():
        index -= 1
    if index < 0 or body[index] != ')':
        return None
    depth, close = 0, index
    while index >= 0:
        depth += {')': 1, '(': -1}.get(body[index], 0)
        if depth == 0:
            break
        index -= 1
    return body[index + 1:close] if re.search(r'\bif\s*$', body[:index]) else None


def statement_guard(body, offset):
    """The conditions of every ``if`` whose block contains ``offset``, innermost first, joined with ``&&``."""
    conditions, depth, index = [], 0, offset
    while index > 0:
        index -= 1
        if body[index] == '}':
            depth += 1
        elif body[index] == '{':
            if depth:
                depth -= 1
                continue
            condition = if_condition(body, index)
            if condition is not None:
                conditions.append(condition)
    return ' && '.join(conditions)


def guards_list(condition):
    """Whether ``condition`` holds only when the list exists: ``sfile->files`` used positively, never ``!`` or null."""
    positive = re.search(r'(?<![!\w])sfile->files\b(?!\s*==)', condition)
    return bool(positive) and not re.search(r'!\s*sfile->files\b|sfile->files\s*==\s*nullptr', condition)


def early_return_guard(body, offset):
    """Whether the function returns earlier when there is no list (``if (... !sfile->files ...) return``)."""
    return bool(re.search(r'if\s*\([^{;]*(!\s*sfile->files\b|sfile->files\s*==\s*nullptr)[^{;]*\)\s*\{?\s*return',
                          body[:offset]))


class FileBrowserUninitializedTests(unittest.TestCase):
    def test_every_list_use_in_the_listener_is_guarded(self):
        body = code_only(function(code_only(SPACE_FILE.read_text()), 'static void file_listener('))
        uses = [m.start() for m in re.finditer(r'filelist_\w+\(sfile->files\b', body)]
        self.assertTrue(uses, "file_listener no longer passes the list anywhere: update this test")
        for offset in uses:
            line = body[body.rfind('\n', 0, offset) + 1:body.find('\n', offset)].strip()
            guard = statement_guard(body, offset)
            self.assertTrue(guards_list(guard + ' ' + line.split('filelist_', 1)[0]),
                            "unguarded list use in file_listener: %s (a browser not refreshed yet has none)" % line)

    def test_runtime_is_ensured_or_checked_wherever_it_is_dereferenced(self):
        source = code_only(SPACE_FILE.read_text())
        found = False
        for name, body in functions(source):
            if 'sfile->runtime->' not in body and 'runtime.' not in body:
                continue
            found = True
            ensured = 'file_runtime_ensure(' in body or name == 'file_runtime_ensure'
            checked = re.search(r'sfile->runtime\s*((==|!=)\s*nullptr|&&)', body)
            self.assertTrue(ensured or checked, "%s dereferences SpaceFile.runtime without ensuring or checking "
                                                "it (a split or newly read browser has none before init)" % name)
        self.assertTrue(found)

    def test_registering_a_reload_callback_creates_the_runtime(self):
        body = code_only(function(code_only(SPACE_FILE.read_text()), 'void file_on_reload_callback_register('))
        self.assertIn('file_runtime_ensure(sfile)', body)

    def test_polls_never_read_a_missing_list(self):
        """A poll runs whenever a menu or shortcut is checked, refreshed or not (file.delete, the OS operations menu)."""
        for path in sorted((ROOT / 'space_file').glob('*.cc')):
            for name, body in functions(code_only(path.read_text())):
                if not name.endswith('_poll'):
                    continue
                for match in re.finditer(r'filelist_\w+\(sfile->files\b', body):
                    self.assertTrue(guards_list(statement_guard(body, match.start()))
                                    or early_return_guard(body, match.start()),
                                    "%s:%s reads SpaceFile.files without checking it exists" % (path.name, name))

    def test_operators_that_read_the_list_require_it_in_their_poll(self):
        """An operator called before the first refresh (Python right after an area becomes a browser, a File Browser
        switched away and back, background mode) must refuse in its poll, never read a null list in its callback."""
        source = code_only((ROOT / 'space_file/file_ops.cc').read_text())
        bodies = dict(functions(source))
        found = 0
        for match in re.finditer(r'void (FILE_OT_\w+)\(wmOperatorType \*ot\)\n\{(.*?)\n\}\n', source, flags=re.S):
            name, definition = match.groups()
            callbacks = re.findall(r'ot->(?:exec|invoke|modal) = (\w+);', definition)
            if not any(re.search(r'sfile->files|\bfiles\s*=\s*sfile->files', bodies.get(cb, '')) for cb in callbacks):
                continue
            found += 1
            poll = re.search(r'ot->poll = (\w+);', definition)
            self.assertIsNotNone(poll, "%s reads the file list and has no poll" % name)
            self.assertRegex(bodies.get(poll.group(1), ''), r'file_list_exists\(C\)|!\s*sfile->files\b|'
                             r'sfile->files\s*==\s*nullptr', "%s reads the file list; its poll %s does not require it"
                             % (name, poll.group(1)))
        self.assertGreater(found, 5)

    def test_an_immediate_activation_searches_the_filtered_list(self):
        """Between a threaded read's end and the next refresh the entries are not filtered yet."""
        body = code_only(function(code_only(FILESEL.read_text()), 'void ED_fileselect_activate_by_id('))
        self.assertLess(body.index('filelist_files_ensure(files);'), body.index('filelist_file_find_id(files'))

    def test_a_directory_change_without_a_list_only_sets_the_directory(self):
        body = code_only(function(code_only(FILESEL.read_text()), 'void ED_file_change_dir_ex('))
        offset = body.index('filelist_is_dir(sfile->files')
        self.assertTrue(guards_list(statement_guard(body, offset)))

    def test_a_reload_callback_is_cleared_before_it_runs(self):
        """It may register again (its list is still being read when another browser's job finished first)."""
        body = code_only(function(code_only(SPACE_FILE.read_text()), 'static void file_on_reload_callback_call('))
        cleared = body.index('sfile->runtime->on_reload = nullptr;')
        self.assertLess(cleared, body.index('callback(sfile, custom_data);'))

    def test_a_reload_callback_runs_once_the_list_is_filtered(self):
        """A threaded read's job-finished notifier arrives before the refresh filters the entries it delivered (and
        filtering resets the active file): the listener only marks the reload, the refresh runs the callback."""
        source = code_only(SPACE_FILE.read_text())
        listener = function(source, 'static void file_listener(')
        self.assertNotIn('file_on_reload_callback_call(', listener)
        self.assertIn('on_reload_finished = true', listener)
        refresh = function(source, 'static void file_refresh(')
        call = refresh.index('file_on_reload_callback_call(sfile)')
        self.assertLess(refresh.index('filelist_filter(sfile->files)'), call)
        gate = refresh[:call]
        self.assertRegex(gate, r'on_reload_finished\s*&&\s*list_final')
        final = gate[gate.index('const bool list_final'):]
        for part in ('filelist_is_ready(sfile->files)', '!filelist_pending(sfile->files)',
                     '!filelist_needs_filtering(sfile->files)', 'filelist_files_num_entries(sfile->files) == 0'):
            self.assertIn(part, final, "an empty read list is final too; a pending one never is")

    def test_a_deferred_relative_path_is_the_spaces_own_copy(self):
        """The caller's text is a Python string, freed when the call returns."""
        body = code_only(function(code_only(FILESEL.read_text()), 'void ED_fileselect_activate_by_relpath('))
        self.assertIn('STRNCPY(runtime.on_reload_relpath, relative_path);', body)
        self.assertNotIn('const_cast<char *>(relative_path)', body)

    def test_activation_defers_while_there_is_no_list(self):
        source = code_only(FILESEL.read_text())
        for signature in ('void ED_fileselect_activate_by_id(', 'void ED_fileselect_activate_by_relpath('):
            body = function(source, signature)
            pending = body.index('filelist_pending(')
            self.assertRegex(body[:pending], r'(sfile->files|files)\s*==\s*nullptr',
                             "%s reads the list before checking it exists" % signature)

    def test_a_browse_mode_change_exits_the_old_modes_data(self):
        """Upstream 272b114d07b (#164024): a File Browser list kept after the area became an Asset Browser (or the
        reverse) was read as the other kind and crashed."""
        body = code_only(function(code_only(SPACE_FILE.read_text()), 'static void file_space_subtype_set('))
        exit_call = body.index('file_exit(')
        self.assertRegex(body[:exit_call], r'sfile->browse_mode\s*!=\s*eFileBrowse_Mode\(value\)')
        self.assertLess(exit_call, body.index('sfile->browse_mode = eFileBrowse_Mode(value);'))

    def test_the_download_broadcast_skips_browsers_without_a_list(self):
        body = code_only(function(code_only(ASSET_LIST.read_text()), 'void on_remote_assets_downloaded('))
        offset = body.index('filelist_remote_asset_library_refresh_online_assets_status(\n              sfile->files')
        self.assertTrue(guards_list(statement_guard(body, offset)))


if __name__ == '__main__':
    unittest.main()
