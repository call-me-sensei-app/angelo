# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The converter child's guards (security review 2026-11-11, F1 and F6): no texture from outside the download reaches
the scene, a USD file whose dependencies leave the download is refused before import, and the child ends itself.

Paths are real files in temporary folders, links included; the parent and the clock are stand-ins.
"""
import suite_isolation  # noqa: F401
import team_library_fixtures as fixtures  # Puts the add-on on the path.
import os
from pathlib import Path
import tempfile
import unittest

from host_test_policy import environment_skip
from angelo_agent import fab_convert_guard as guard


class Folders(unittest.TestCase):
    def setUp(self):
        owner = tempfile.TemporaryDirectory()
        self.addCleanup(owner.cleanup)
        base = Path(os.path.realpath(owner.name))
        self.folder, self.temp, self.outside = base / 'files', base / 'tmp', base / 'outside'
        for path in (self.folder / 'textures', self.temp, self.outside):
            path.mkdir(parents=True)
        (self.folder / 'textures' / 'wood.png').write_bytes(b'png')
        (self.outside / 'secret.png').write_bytes(b'png')
        self.roots = [str(self.folder), str(self.temp)]


class SourceTests(Folders):
    def test_embedded_and_inside_sources_stay(self):
        self.assertTrue(guard.source_allowed('', self.roots))
        self.assertTrue(guard.source_allowed(str(self.folder / 'textures' / 'wood.png'), self.roots))
        self.assertTrue(guard.source_allowed(str(self.temp / 'blender_x' / 'usd' / 'tex.jpg'), self.roots))
        self.assertTrue(guard.source_allowed(str(self.folder / 'model.usdz') + '[0/tex.jpg]', self.roots))

    def test_outside_sources_are_dropped_absolute_or_relative(self):
        for raw in (str(self.outside / 'secret.png'), '/etc/hosts', str(self.folder / '..' / 'outside' / 'secret.png'),
                    '../outside/secret.png', '//../outside/secret.png', str(self.outside / 'x.usdz') + '[a.jpg]'):
            with self.subTest(raw=raw):
                base = str(self.folder)
                expand = lambda value: os.path.join(base, value[2:]) if value.startswith('//') else value
                cwd = os.getcwd()
                os.chdir(self.folder)
                try:
                    self.assertFalse(guard.source_allowed(raw, self.roots, expand))
                finally:
                    os.chdir(cwd)

    @unittest.skipIf(os.name == 'nt', environment_skip('creating symbolic links needs a privilege on Windows'))
    def test_a_link_inside_that_leads_outside_is_outside(self):
        link = self.folder / 'textures' / 'link.png'
        link.symlink_to(self.outside / 'secret.png')
        self.assertFalse(guard.source_allowed(str(link), self.roots))


class UsdTests(Folders):
    def test_dependencies_inside_the_download_pass(self):
        layers = [str(self.folder / 'model.usda'), str(self.folder / 'model.usdz') + '[root.usdc]']
        assets = [str(self.folder / 'textures' / 'wood.png'), str(self.folder / 'textures' / 'wood.<UDIM>.png')]
        self.assertEqual(guard.usd_escapes((layers, assets, ['textures/missing.png']), str(self.folder)), [])

    def test_every_escaping_dependency_is_listed(self):
        escaping = guard.usd_escapes(
            ([str(self.outside / 'layer.usda')], [str(self.outside / 'secret.png'), '/etc/hosts'],
             ['../../secret.png', 'https://example.invalid/t.png', '/abs/missing.png', 'ok/missing.png']),
            str(self.folder))
        self.assertEqual(len(escaping), 6)
        self.assertNotIn('ok/missing.png', escaping)

    def test_a_rooted_or_drive_path_escapes_whichever_system_wrote_it(self):
        # Python 3.13 on Windows no longer calls '/x' absolute (os.path.isabs): a rooted path is the current
        # drive's root, outside the download (VM CI win-3, b7b8d674cb0f). A file's text is judged in both forms.
        rooted = ['/abs/missing.png', '\\abs\\missing.png', 'C:\\x\\missing.png', 'C:/x/missing.png',
                  'D:missing.png']
        self.assertEqual(guard.usd_escapes(([], [], rooted + ['ok\\missing.png']), str(self.folder)), rooted)
        for text in rooted:
            self.assertTrue(guard.names_a_root(text), text)
        for text in ('ok/missing.png', 'ok\\missing.png', 'wood.<UDIM>.png', ''):
            self.assertFalse(guard.names_a_root(text), text)


class WatchdogTests(unittest.TestCase):
    def run_watch(self, seconds, parent, parents):
        exits, now = [], [0.0]
        parent_now = iter(parents)
        thread = guard.watchdog(seconds, parent, exit=exits.append, parent_now=lambda: next(parent_now),
                                clock=lambda: now[0], sleep=lambda _s: now.__setitem__(0, now[0] + 0.5), start=False)
        thread.run()
        return exits, now[0]

    def test_the_deadline_ends_the_child(self):
        exits, ended = self.run_watch(3.0, 41, [41] * 100)
        self.assertEqual(exits, [124])
        self.assertGreaterEqual(ended, 3.0)

    def test_a_vanished_parent_ends_the_child_at_once(self):
        exits, ended = self.run_watch(600.0, 41, [41, 41, 1])
        self.assertEqual(exits, [125])
        self.assertLess(ended, 2.0)



class NetworkPathTests(unittest.TestCase):
    """Re-review r2: on Windows, merely opening \\\\host\\share\\x.png tries SMB sign-in (an NTLM hash to that host).
    A path that names another machine or a device is refused before anything opens or resolves it."""

    def test_network_and_device_paths_are_recognised(self):
        for raw in ('\\\\attacker\\share\\x.png', '//attacker/share/x.png', '\\\\?\\UNC\\host\\s\\x.png',
                    '\\\\.\\pipe\\x', '//?/C:/x.png', 'file://host/x.png', 'smb://host/x.png', 'https://h/x.png',
                    'textures/CON.png', 'NUL', 'tex/lpt1.tga', 'C:/Users/x/AUX.jpg',
                    # Re-review r2: the NT prefix, streams and device colons, superscript device digits.
                    '\\??\\UNC\\host\\share\\x.png', '\\??\\C:\\x.png', '/??/x', 'x.png:stream',
                    'textures\\x.png::$DATA', 'CON:', 'CON .png', 'tex/COM\u00b9.png', 'LPT\u00b3',
                    'FILE:/x', 'file:///C:/x'):
            with self.subTest(raw=raw):
                self.assertTrue(guard.network_or_device(raw))
        for raw in ('textures/wood.png', 'C:\\Users\\author\\wood.png', '/Users/author/wood.png', '../wood.png',
                    'console.png', 'con-crete.png', '', 'model.usdz[0/tex.jpg]', 'C:/Users/a/PROGRA~1/x.png',
                    'C:x.png', '%5C%5Chost%5Cshare%5Cx.png', 'tex/COM10.png'):
            with self.subTest(raw=raw):
                self.assertFalse(guard.network_or_device(raw))

    def test_only_local_path_shapes_are_allowed(self):
        """Re-review r2: an allow-list of local path shapes, not a list of bad ones. Anything else, Windows' reserved
        characters included (they also spell \\\\?\\ and \\??\\), is refused before any open."""
        from angelo_agent import untrusted_paths
        for raw in ('wood.png', 'textures/wood 01.png', '../Textures/wood.png', 'C:/Users/author/wood.png',
                    'C:\\Users\\author\\wood.png', '/Users/author/wood.png', 'C:wood.png', 'tex/w\u00f6od.png',
                    'model.usdz[0/tex.jpg]', 'tex/wood.<UDIM>.png', 'tex/100%.png', ''):
            with self.subTest(allowed=raw):
                self.assertTrue(untrusted_paths.local_form(raw))
        for raw in ('tex/a|b.png', 'tex/a"b.png', 'tex/a*.png', 'tex/a?.png', 'tex/a<b>.png', 'tex/a\x01.png',
                    'tex/a\x00.png', '\\\\?\\C:\\x.png', '\\\\.\\C:\\x.png', 'C:/x/y:z.png', '1:/x.png',
                    'tex/NUL.txt.png', 'tex/con .png', 'tex/aux.',
                    # Re-review r2: NT-native names; a drive-less rooted path is allowed in POSIX form only.
                    '\\Device\\Mup\\host\\share\\x.png', '\\GLOBALROOT\\Device\\x', '\\textures\\x.png'):
            with self.subTest(refused=raw):
                self.assertFalse(untrusted_paths.local_form(raw))
        # Re-review r3 G3: surrounding Unicode whitespace, and any input whose NFKC form fails.
        for raw in (' wood.png', 'wood.png ', '\u3000\\\\host\\s', '\u00a0tex/w.png', 'tex/w.png\u2003',
                    '\uff3c\uff3chost\uff3cshare', 'C:/x\uff1ay.png', 'tex/CON\uff0epng'):
            with self.subTest(refused=raw):
                self.assertFalse(untrusted_paths.local_form(raw))
        self.assertTrue(untrusted_paths.local_form('tex\uff0fwood.png'))   # NFKC: tex/wood.png, a local path.
        self.assertTrue(all(guard.network_or_device(raw) == (not untrusted_paths.local_form(raw))
                            for raw in ('//h/s', 'wood.png', 'tex/a|b.png', 'C:/x.png')))

    def test_resolving_never_touches_a_network_path(self):
        from unittest.mock import patch
        with patch.object(guard.os.path, 'realpath', side_effect=AssertionError('opened')):
            self.assertIsNone(guard.resolved('\\\\attacker\\share\\x.png'))
            self.assertIsNone(guard.resolved('//attacker/share/x.png'))
            self.assertFalse(guard.source_allowed('//attacker/share/x.png', ['/tmp']))
            self.assertEqual(guard.usd_escapes(([], ['//attacker/share/x.png'], []), '/tmp'), ['//attacker/share/x.png'])


class PrescanTests(unittest.TestCase):
    """Before the child starts: every path an FBX, OBJ or MTL file names is checked (fab_convert.prescan)."""

    def setUp(self):
        owner = tempfile.TemporaryDirectory()
        self.addCleanup(owner.cleanup)
        self.folder = Path(owner.name)

    def refused(self, members):
        from angelo_agent import fab_convert
        with self.assertRaises(fab_convert.ConvertRefused) as refused:
            fab_convert.prescan(self.folder, members)
        return str(refused.exception)

    def test_fbx_texture_paths_naming_another_machine_are_refused(self):
        for raw in (b'\\\\attacker\\share\\wood.png', b'//attacker/share/wood.png', b'textures/CON.png'):
            with self.subTest(raw=raw):
                (self.folder / 'm.fbx').write_bytes(fixtures.fbx([('Objects', [], [('Video', [], [
                    ('RelativeFilename', [('S', raw)], [])])])]))
                self.assertIn('another computer', self.refused([('m.fbx', 'fbx')]))

    def test_fbx_with_an_author_disk_path_and_odd_embedded_texture_passes(self):
        from angelo_agent import fab_convert
        (self.folder / 'm.fbx').write_bytes(fixtures.fbx([('Objects', [], [('Video', [], [
            ('RelativeFilename', [('S', b'C:\\Users\\author\\wood.png')], []),
            ('Content', [('R', b'\x00\x00\x02' + bytes(40))], [])])])]))   # A TGA-like blob: not judged here.
        fab_convert.prescan(self.folder, [('m.fbx', 'fbx')])

    def texture(self, node, name, children):
        return fixtures.fbx([('Objects', [], [(node, [('I', 1), ('S', name + b'\x00\x01' + node.encode()),
                                                       ('S', b'')], children)])])

    def test_fbx_texture_name_is_checked_when_the_importer_would_open_it(self):
        """Re-review r3 G1: with no RelativeFilename/FileName/Filename, Blender's FBX importer opens the Texture or
        Video element's NAME as the path (import_fbx.blen_read_texture_image)."""
        from angelo_agent import fab_convert, team_library_files
        for node in ('Texture', 'Video'):
            for children in ([], [('FileName', [('S', b'')], [])]):   # No path, or only an empty one.
                with self.subTest(node=node, children=children):
                    (self.folder / 'm.fbx').write_bytes(self.texture(node, b'\\\\attacker\\share\\w.png', children))
                    self.assertIn('another computer', self.refused([('m.fbx', 'fbx')]))
                    with self.assertRaises(team_library_files.Refused):   # The Team Library reads FBX the same way.
                        team_library_files.inspect(str(self.folder / 'm.fbx'), 'model')
        # A name is not a path while the texture has one: a colon in it refuses nothing.
        (self.folder / 'm.fbx').write_bytes(self.texture('Texture', b'Map #1: diffuse', [
            ('RelativeFilename', [('S', b'textures/wood.png')], [])]))
        fab_convert.prescan(self.folder, [('m.fbx', 'fbx')])

    def test_continued_obj_lines_are_read_as_blender_joins_them(self):
        """Re-review r3 G2: Blender's OBJ reader joins a line ending in a backslash with the next, so the check reads
        the joined statement: a continued library name is checked whole, and a legitimate one is not refused."""
        from angelo_agent import fab_convert
        (self.folder / 'a.obj').write_text('mtllib \\\nchairs.mtl\nv 0 0 0\n')
        fab_convert.prescan(self.folder, [('a.obj', 'obj')])
        (self.folder / 'a.obj').write_text('mtllib chairs.mtl \\\n\\\\attacker\\share\\a.mtl\nv 0 0 0\n')
        self.assertIn('another computer', self.refused([('a.obj', 'obj')]))

    def test_obj_and_mtl_paths_naming_another_machine_are_refused(self):
        (self.folder / 'a.obj').write_text('mtllib a.mtl\nv 0 0 0\n')
        (self.folder / 'a.mtl').write_text('newmtl Wood\nmap_Kd -s 1 1 1 \\\\attacker\\share\\wood.png\n')
        self.assertIn('another computer', self.refused([('a.obj', 'obj'), ('a.mtl', 'mtl')]))
        (self.folder / 'a.mtl').write_text('newmtl Wood\nmap_Kd C:\\author\\wood.png\nbump -bm 0.5 tex/bump.png\n')
        from angelo_agent import fab_convert
        fab_convert.prescan(self.folder, [('a.obj', 'obj'), ('a.mtl', 'mtl')])


class FollowUpTests(unittest.TestCase):
    """Re-review r4 follow-ups: exact OBJ continuation, a redacted failure log, layouts the app cannot carry, and
    slash lookalikes no normalisation maps."""

    def lines(self, data):
        import io
        from angelo_agent import fab_convert
        return [line.rstrip(b'\r') for line in fab_convert._statements(io.BytesIO(data), joined=True)]

    def test_obj_continuation_matches_blenders_reader(self):
        # obj_import_file_reader.cc read_next_obj_line: a backslash followed only by bytes <= 0x20 up to a newline
        # becomes one space; any other backslash stays; none at the end of the file without a newline.
        self.assertEqual(self.lines(b'mtllib a\\   \t\nb.mtl\nv 0 0 0\n'), [b'mtllib a b.mtl', b'v 0 0 0'])
        self.assertEqual(self.lines(b'mtllib\\\r\nb.mtl\n'), [b'mtllib b.mtl'])
        self.assertEqual(self.lines(b'mtllib a\\\\\nb.mtl\n'), [b'mtllib a\\ b.mtl'])
        self.assertEqual(self.lines(b'mtllib a\\b.mtl\n'), [b'mtllib a\\b.mtl'])
        self.assertEqual(self.lines(b'mtllib a.mtl \\'), [b'mtllib a.mtl \\'])
        self.assertEqual(self.lines(b'mtllib a\\\n\\\n c.mtl\n'), [b'mtllib a   c.mtl'])

    def test_the_failure_log_is_written_with_home_folders_redacted(self):
        from angelo_agent import fab_convert
        home = os.path.expanduser('~')
        with tempfile.TemporaryDirectory() as root:
            work, folder = Path(root) / 'work', Path(root) / 'cache'
            work.mkdir()
            folder.mkdir()
            (work / 'child.log').write_text('Could not open %s/Pictures/secret.png and %s\n'
                                            % (home, os.path.realpath(home)))
            fab_convert._keep_log(work, folder)
            kept = (folder / 'convert-failed.log').read_text()
        self.assertNotIn(home, kept)
        self.assertIn('~/Pictures/secret.png', kept)

    def test_packed_layouts_are_made_carriable_never_refused(self):
        """Re-review r5: one texture never refuses a download. Unpacked tiles are trimmed; a multiview image keeps
        one view; movies and sequences are dropped."""
        def layout(source, **facts):
            return guard.packed_layout(source, allowed=True, **facts)
        self.assertEqual(layout('FILE', tiles=1, packed=1, multiview=False), 'keep')
        self.assertEqual(layout('TILED', tiles=4, packed=4, multiview=False, tile_numbers=[1001, 1002, 1011, 1012],
                                pattern='/d/t.<UDIM>.png'), 'keep')
        self.assertEqual(layout('TILED', tiles=4, packed=2, multiview=False, tile_numbers=[1001, 1002, 1003, 1004],
                                pattern='/d/t.<UDIM>.png'), 'trim_tiles')
        self.assertEqual(layout('FILE', tiles=1, packed=2, multiview=True), 'single_view')
        self.assertEqual(layout('MOVIE', tiles=1, packed=1, multiview=False), 'drop')
        self.assertEqual(layout('SEQUENCE', tiles=1, packed=1, multiview=False), 'drop')

    def test_the_source_check_decides_first_for_every_packed_image(self):
        """Re-review r6 M1: the packed-source check is the backstop for every packed image, so no layout (multiview,
        partly packed tiles, a movie) is handled before it: an image from outside is dropped whatever its layout."""
        cases = [('FILE', 1, 1, False, None), ('FILE', 1, 2, True, None), ('TILED', 4, 2, False, [1001, 1002, 1003, 1004]),
                 ('TILED', 2, 2, True, [1001, 1002]), ('MOVIE', 1, 1, False, None), ('SEQUENCE', 1, 1, True, None),
                 ('TILED', 3, 3, False, [1001, 1002, 999])]
        for source, tiles, packed, multiview, numbers in cases:
            with self.subTest(source=source, multiview=multiview, numbers=numbers):
                self.assertEqual(guard.packed_layout(source, allowed=False, tiles=tiles, packed=packed,
                                                     multiview=multiview, tile_numbers=numbers,
                                                     pattern='/d/t.<UDIM>.png'), 'outside')
        # Re-review r7: a packed image whose source says GENERATED or VIEWER (Blender turns a generated image it packs
        # into a FILE image; switching Source back to Generated keeps the bytes) meets the same check. r8: such
        # bytes are never shown, so a generated image loses only them and keeps its pattern; a viewer is dropped.
        for source in ('GENERATED', 'VIEWER'):
            with self.subTest(source=source):
                self.assertEqual(guard.packed_layout(source, allowed=False, tiles=1, packed=1, multiview=False),
                                 'outside')
        self.assertEqual(guard.packed_layout('GENERATED', allowed=True, tiles=1, packed=1, multiview=False),
                         'remove_packed')
        self.assertEqual(guard.packed_layout('GENERATED', allowed=True, tiles=1, packed=2, multiview=True),
                         'remove_packed')
        self.assertEqual(guard.packed_layout('VIEWER', allowed=True, tiles=1, packed=1, multiview=False), 'drop')
        # A packed multiview image recorded from outside the download (an author's disk): never carried.
        outside = guard.source_allowed('/Users/author/stereo_L.png', [str(Path(tempfile.gettempdir()) / 'x')])
        self.assertFalse(outside)
        self.assertEqual(guard.packed_layout('FILE', allowed=outside, tiles=1, packed=2, multiview=True), 'outside')

    def test_tile_sets_outside_blenders_udim_range_are_dropped(self):
        """Re-review r6 R5: tile numbers come from the downloaded file. Blender's tiles are 1001..2000
        (IMA_UDIM_MAX), so at most 1000; any other set is dropped before a tile is listed or looked up."""
        self.assertEqual((guard.UDIM_FIRST, guard.UDIM_LAST, guard.MAX_TILES), (1001, 2000, 1000))
        self.assertTrue(guard.tiles_valid([1001]))
        self.assertTrue(guard.tiles_valid(list(range(1001, 2001))))
        for numbers in ([1000], [2001], [0], [-5], [1001, 1001], None, [], list(range(1001, 2002))):
            with self.subTest(numbers=numbers if not numbers or len(numbers) < 5 else len(numbers)):
                self.assertFalse(guard.tiles_valid(numbers))
        self.assertEqual(guard.packed_layout('TILED', allowed=True, tiles=2, packed=2, multiview=False,
                                             tile_numbers=[1001, 5000], pattern='/d/t.<UDIM>.png'), 'drop')
        self.assertEqual(guard.tile_paths('/d/t.<UDIM>.png', [1001, 2001, 999]), [(1001, '/d/t.1001.png')])

    def test_tile_patterns_follow_blenders_single_format_argument(self):
        """Re-review r6 R4: Blender (BKE_image_get_tile_strformat) turns every <UDIM> (or, without one, every
        <UVTILE>) into a printf directive and formats the path with ONE tile number, so a second token or any other
        '%' is undefined behaviour there. Such a pattern is refused; one token is the only form expanded."""
        self.assertEqual(guard.tile_format('/d/t.<UDIM>.png'), 'udim')
        self.assertEqual(guard.tile_format('/d/t.<UVTILE>.png'), 'uvtile')
        self.assertEqual(guard.tile_format('/d/t.png'), 'none')
        self.assertEqual(guard.tile_format('/d/t.<udim>.png'), 'none')   # Blender matches the token's case exactly.
        for pattern in ('/d/<UDIM>/t.<UDIM>.png', '/d/t.<UDIM>.<UVTILE>.png', '/d/<UVTILE>t<UVTILE>.png',
                        '/d/t%s.<UDIM>.png', '/d/100%.<UVTILE>.png'):
            with self.subTest(pattern=pattern):
                self.assertEqual(guard.tile_format(pattern), 'refused')
                self.assertIsNone(guard.tile_paths(pattern, [1001]))
                self.assertEqual(guard.tile_relink(pattern, [1001], {'t.1001.png': ['/x/t.1001.png']}), (None, []))
                self.assertEqual(guard.packed_layout('TILED', allowed=True, tiles=1, packed=1, multiview=False,
                                                     tile_numbers=[1001], pattern=pattern), 'drop')

    def test_single_view_size_caps_match_the_team_library_image_caps(self):
        """Re-review r6 R2: a packed .blend image is not size-capped by the archive, so the single-view copy refuses
        what the Team Library refuses."""
        from angelo_agent import team_library_files
        self.assertEqual((guard.MAX_SIDE, guard.MAX_PIXELS), (team_library_files.MAX_SIDE, team_library_files.MAX_PIXELS))
        self.assertTrue(guard.size_allowed(64, 64))
        for size in ((0, 64), (64, 0), (guard.MAX_SIDE + 1, 1), (guard.MAX_SIDE, guard.MAX_SIDE)):
            with self.subTest(size=size):
                self.assertFalse(guard.size_allowed(*size))

    def test_udim_and_uvtile_patterns_expand_per_tile(self):
        self.assertEqual(guard.tile_paths('/d/tex.<UDIM>.png', [1001, 1012]),
                         [(1001, '/d/tex.1001.png'), (1012, '/d/tex.1012.png')])
        self.assertEqual(guard.tile_paths('/d/tex.<UVTILE>.png', [1001, 1012]),
                         [(1001, '/d/tex.u1_v1.png'), (1012, '/d/tex.u2_v2.png')])
        self.assertIsNone(guard.tile_paths('/d/tex.png', [1001]))

    def test_a_udim_set_relinks_by_name_into_the_download(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(os.path.realpath(root))
            for name in ('textures/Wood.1001.png', 'textures/Wood.1002.png', 'other/Wood.1001.png'):
                (folder / name).parent.mkdir(parents=True, exist_ok=True)
                (folder / name).write_bytes(b'png')
            index = {}
            for path in folder.rglob('*.png'):
                index.setdefault(path.name.casefold(), []).append(str(path))
            pattern, found = guard.tile_relink('C:/author/disk/Wood.<UDIM>.png', [1001, 1002, 1003], index)
            self.assertEqual(pattern, str(folder / 'textures' / 'Wood.<UDIM>.png'))   # The folder holding most tiles.
            self.assertEqual(found, [1001, 1002])
            self.assertEqual(guard.tile_relink('C:/x/Stone.<UDIM>.png', [1001], index), (None, []))

    def test_the_failure_log_redacts_every_spelling_of_the_home_folder(self):
        """Re-review r5: doubled backslashes (Python tracebacks on Windows), mixed separators, other case (macOS and
        Windows file systems ignore it) and the Windows short name."""
        from angelo_agent import fab_convert
        homes = ['C:\\Users\\probeuser', '/Users/probeuser']
        text = ("No such file: 'C:\\\\Users\\\\probeuser\\\\tex.png' C:/Users/probeuser/a C:\\USERS\\PROBEUSER\\b "
                "/Users/PROBEUSER/c C:\\Users\\PROBEU~1\\d /Users/probeuserx/e")
        redacted = fab_convert._redacted(text, homes=homes, short='C:\\Users\\PROBEU~1', fold=True)
        self.assertNotIn('probeuser\\', redacted.lower().replace('probeuserx', ''))
        self.assertNotIn('PROBEU~1', redacted)
        self.assertIn('/Users/probeuserx/e', redacted)   # Only the home folder itself, never a longer name.
        self.assertEqual(redacted.count('~'), 5)

    def test_the_home_folder_is_redacted_before_any_punctuation(self):
        """Re-review r6 R1: only a longer NAME (a word character or '-') keeps the text; a home folder followed by
        ')', '.', ',', ':' or ';' is still the home folder."""
        from angelo_agent import fab_convert
        homes = ['/Users/probeuser', 'C:\\Users\\probeuser']
        for after in (')', '.', ',', ':', ';', ']', "'", '"', ' ', '/x', '\\x', ''):
            with self.subTest(after=after):
                text = '(/Users/probeuser%s and C:\\Users\\probeuser%s' % (after, after)
                self.assertNotIn('probeuser', fab_convert._redacted(text, homes=homes, fold=True))
        for longer in ('/Users/probeuserx', '/Users/probeuser-2', '/Users/probeuser_old', '/Users/probeuser1'):
            with self.subTest(longer=longer):
                self.assertEqual(fab_convert._redacted(longer, homes=homes, fold=True), longer)

    def test_slash_and_colon_lookalikes_without_a_mapping_are_refused(self):
        from angelo_agent import untrusted_paths
        for raw in ('tex\u2215w.png', '\u2216\u2216host\u2216s', 'tex\u29f5w.png', 'C\u2236x.png', 'a\u2044b.png',
                    'a\u29f8b.png', 'a\u29f9b.png'):
            with self.subTest(raw=raw):
                self.assertFalse(untrusted_paths.local_form(raw))
        for raw in ('Wood\u00b7Base.png', 'r\u00e9sum\u00e9.png', '\u6728\u6750.png', 'a\u2013b.png'):
            with self.subTest(allowed=raw):
                self.assertTrue(untrusted_paths.local_form(raw))


class WiringTests(unittest.TestCase):
    """The child (bpy, run only in the app) uses each guard; its effects are proven by test_blender_fab_formats."""

    def source(self, name):
        return (Path(guard.__file__).parent / name).read_text(encoding='utf-8')

    def test_the_child_starts_its_watchdog_first_and_checks_usd_dependencies(self):
        child = self.source('fab_convert_child.py')
        self.assertIn("guard.watchdog(job['deadline_seconds'], job['parent_pid'])", child)
        self.assertLess(child.index('guard.watchdog('), child.index('read_factory_settings'))
        self.assertIn('UsdUtils.ComputeAllDependencies', child)
        self.assertIn('guard.usd_escapes(', child)
        # Raw asset paths first, unresolved: resolving a network path would already open it (re-review r2).
        self.assertIn('UsdUtils.ModifyAssetPaths', child)
        self.assertLess(child.index('_usd_raw_paths(folder, path)'), child.index('UsdUtils.ComputeAllDependencies'))
        convert = self.source('fab_convert.py')
        self.assertLess(convert.index('prescan(source, members)'), convert.index('result = run(job'))

    def test_no_child_module_resolves_a_path_a_file_names_without_the_guard(self):
        """Path.resolve and os.path.realpath open a UNC path on Windows: only the guard may call them on such a path."""
        import re
        for name in ('fab_convert_clean.py', 'fab_convert_material.py', 'fab_convert_child.py'):
            for line in self.source(name).splitlines():
                if re.search(r'\.resolve\(\)|realpath\(', line):
                    with self.subTest(module=name, line=line.strip()):
                        self.assertRegex(line, r'\(folder|Path\(job\[.folder.\]\)|Path\(folder\)')

    def test_the_allowance_and_layouts_are_tracked_by_session_uid(self):
        child, clean = self.source('fab_convert_child.py'), self.source('fab_convert_clean.py')
        self.assertNotIn('as_pointer()', child + clean)
        self.assertIn('image.session_uid', child)
        self.assertIn('guard.packed_layout(', clean)

    def test_only_importers_that_pack_their_own_bytes_keep_packed_data_as_is(self):
        """Re-review r3 G4 and its .blend twin: FBX embedded Content and a .blend's packed images are the download's
        own bytes; USD and glTF packed sources must still pass source_allowed."""
        child = self.source('fab_convert_child.py')
        self.assertIn("OWN_PACKED_DATA = ('fbx', 'blend')", child)
        self.assertIn('clean.pack_images(folder, report, embedded=embedded)', child)

    def test_packing_keeps_only_allowed_sources(self):
        clean = self.source('fab_convert_clean.py')
        self.assertIn('guard.source_allowed(', clean)
        self.assertNotIn('os.path.isabs(source)', clean)

    def test_every_packed_image_meets_the_source_check_before_its_layout(self):
        """Re-review r6 M1: in pack_images, the packed branch computes the allowance first and lets packed_layout
        decide (it answers 'outside' first); nothing in that branch runs before it, and it is the branch's only
        exit to the layouts."""
        import ast
        tree = ast.parse(self.source('fab_convert_clean.py'))
        pack = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'pack_images')
        loop = next(n for n in pack.body if isinstance(n, ast.For))
        # Re-review r7: the packed branch is the loop's FIRST decision, before any skip (GENERATED, VIEWER), drop or
        # unpacked handling: only plain assignments come before it.
        first_decision = next(i for i, n in enumerate(loop.body) if not isinstance(n, ast.Assign))
        branch = loop.body[first_decision]
        self.assertIsInstance(branch, ast.If)
        self.assertIn('len(image.packed_files)', ast.unparse(branch.test))
        self.assertTrue(any(isinstance(n, ast.Continue) for n in branch.body))
        before = ast.unparse(ast.Module(body=loop.body[:first_decision], type_ignores=[]))
        self.assertNotIn('continue', before)
        first, second = branch.body[0], branch.body[1]
        self.assertIn('allowed', ast.unparse(first.targets[0]) if isinstance(first, ast.Assign) else '')
        self.assertIn('guard.source_allowed(', ast.unparse(first))
        self.assertIn('session_uid', ast.unparse(first))
        self.assertIn('guard.packed_layout(', ast.unparse(second))
        self.assertIn('allowed=allowed', ast.unparse(second))
        text = ast.unparse(branch)
        self.assertEqual(text.count('guard.source_allowed('), 1)
        self.assertLess(text.index("'outside'"), text.index("'single_view'"))
        # Packed means any packed data: a multiview or tiled image may hold several entries.
        self.assertIn('len(image.packed_files)', ast.unparse(branch.test))

    def test_a_generated_image_loses_only_its_packed_bytes(self):
        """Re-review r8: Image.unpack(method='REMOVE') frees the packed records without writing a file
        (packedFile.cc PF_REMOVE), and the image is dropped if any packed data is left."""
        import ast
        tree = ast.parse(self.source('fab_convert_clean.py'))
        pack = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'pack_images')
        text = ast.unparse(pack)
        branch = text[text.index("'remove_packed'"):]
        self.assertIn("image.unpack(method='REMOVE')", branch)
        self.assertLess(branch.index("image.unpack(method='REMOVE')"), branch.index('len(image.packed_files)'))
        self.assertEqual(text.count('.unpack('), 1)   # REMOVE only: no other method may write a file.

    def test_the_single_view_copy_is_bounded_and_keeps_alpha(self):
        """Re-review r6 R2/R3: a numpy float32 buffer within the size caps, never a Python list of every pixel;
        the colour space and alpha mode are set before the pixels (setting them frees the image buffer)."""
        import ast
        tree = ast.parse(self.source('fab_convert_clean.py'))
        view = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_single_view')
        text = ast.unparse(view)
        self.assertIn('guard.size_allowed(', text)
        self.assertIn('np.empty(', text)
        self.assertIn('np.float32', text)
        self.assertNotIn('[0.0] *', text)
        self.assertLess(text.index('alpha_mode'), text.index('pixels.foreach_set'))
        self.assertLess(text.index('colorspace_settings'), text.index('pixels.foreach_set'))

    def test_unpacked_tile_sets_are_checked_before_a_tile_is_looked_up(self):
        clean = self.source('fab_convert_clean.py')
        self.assertIn('guard.tiles_valid(', clean)
        self.assertIn('guard.tile_format(', clean)
        self.assertLess(clean.index('guard.tiles_valid('), clean.index('guard.tile_paths('))


if __name__ == '__main__':
    unittest.main()
