# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unpacking a Fab zip: every hostile archive is refused before a byte lands outside the new folder.

All archives are generated here. The real layouts seen on Fab (2026-11-11: an OBJ pack with spaces in its names,
Megascans maps beside an FBX, a glTF with a textures/ folder) are the success cases; the attacks are the usual
zip-slip, link, device, collision, bomb and damage classes, each written the way an attacker would.
"""
import suite_isolation  # noqa: F401
import team_library_fixtures  # noqa: F401  Puts the add-on on the path.
import os
from pathlib import Path
import shutil
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

from angelo_agent import fab_archive as archive, fab_formats as formats

PNG = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 4, 4) + b'\x08\x06\x00\x00\x00'
OBJ = b'mtllib Chairs Pack.mtl\no Chair\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'
MTL = b'newmtl Wood\nKd 0.8 0.6 0.4\n'
FBX = b'Kaydara FBX Binary  \x00\x1a\x00' + b'\x00' * 64


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        owner = tempfile.TemporaryDirectory()
        self.addCleanup(owner.cleanup)
        self.root = Path(owner.name)
        self.target = self.root / 'out'

    def make(self, entries, name='a.zip'):
        path = self.root / name
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zipped:
            for entry in entries:
                zipped.writestr(*entry)   # (name or ZipInfo, data)
        return path

    def refused(self, path, fmt='obj'):
        with self.assertRaises(archive.ArchiveRefused) as refused:
            archive.extract(path, self.target, formats.BY_CODE[fmt])
        self.assertFalse(self.target.exists(), 'a refused archive leaves no folder')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted(p.name for p in self.root.glob('*.zip')))
        return str(refused.exception)

    def test_real_layouts_unpack_with_their_names_and_only_kept_members(self):
        path = self.make([('Chairs Pack.obj', OBJ), ('Chairs Pack.mtl', MTL), ('textures/Wood_BaseColor.png', PNG),
                          ('readme.txt', b'Thanks for buying!\n'), ('extra/models.zip', b'PK\x03\x04' + b'\x00' * 40),
                          ('other/chair.fbx', FBX)])
        result = archive.extract(path, self.target, formats.BY_CODE['obj'])
        self.assertEqual(sorted(result['members']), [('Chairs Pack.mtl', 'mtl'), ('Chairs Pack.obj', 'obj'),
                                                     ('textures/Wood_BaseColor.png', 'png')])
        self.assertEqual(result['left'], 3)
        self.assertEqual((self.target / 'Chairs Pack.obj').read_bytes(), OBJ)
        self.assertFalse((self.target / 'readme.txt').exists())
        if os.name != 'nt':   # Windows has no mode bits to set: the folder keeps the profile's access list.
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)

    def test_a_gltf_keeps_the_side_files_its_uris_name(self):
        """Buffers (.bin) have no signature: the model's own URIs name them (Fab's glTF zips, 2026-11-11)."""
        import json
        document = {'asset': {'version': '2.0'},
                    'buffers': [{'uri': 'Chairs%20Pack.bin', 'byteLength': 4}, {'uri': 'data:application/octet-stream;base64,AAAA'}],
                    'images': [{'uri': 'textures/Wood%20Base.png'}, {'uri': '../outside.bin'}]}
        path = self.make([('Chairs Pack.gltf', json.dumps(document).encode()), ('Chairs Pack.bin', b'\x00\x01\x02\x03'),
                          ('textures/Wood Base.png', PNG), ('unrelated.bin', b'\x00' * 8), ('notes.dat', b'\x00')])
        result = archive.extract(path, self.target, formats.BY_CODE['gltf'])
        self.assertEqual(sorted(p for p, _kind in result['members']),
                         ['Chairs Pack.bin', 'Chairs Pack.gltf', 'textures/Wood Base.png'])
        self.assertEqual(result['left'], 2)
        self.assertEqual((self.target / 'Chairs Pack.bin').read_bytes(), b'\x00\x01\x02\x03')

    def test_traversal_and_absolute_names_are_refused(self):
        for name in ('../evil.obj', 'a/../../evil.obj', '/etc/evil.obj', 'C:/evil.obj', 'C:evil.obj',
                     '..\\evil.obj', 'a\\..\\evil.obj', 'evil.obj:stream', 'a/./b.obj', 'a//b.obj'):
            with self.subTest(name=name):
                info = zipfile.ZipInfo('placeholder.obj')
                info.filename = name   # Written raw, as a hostile tool would.
                self.assertIn('nothing was unpacked', self.refused(self.make([(info, OBJ)], name='t.zip')))

    def test_names_no_supported_system_can_hold_are_refused(self):
        for name in ('CON.obj', 'textures/aux.png', 'lpt1', 'trailing.obj.', 'space.obj ', 'nul.tar.gz',
                     'bell\x07.obj', 'bidi\u202e.obj', 'x' * 201 + '.obj'):
            with self.subTest(name=name):
                info = zipfile.ZipInfo('placeholder.obj')
                info.filename = name
                self.refused(self.make([(info, OBJ)], name='n.zip'))

    def test_case_and_normalisation_collisions_are_refused(self):
        self.refused(self.make([('Chair.obj', OBJ), ('chair.OBJ', OBJ)]))
        self.refused(self.make([('caf\u00e9.obj', OBJ), ('cafe\u0301.obj', OBJ)]))
        self.refused(self.make([('model', OBJ), ('model/inner.obj', OBJ)]))

    def test_links_and_devices_are_refused(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFCHR | 0o666, stat.S_IFIFO | 0o644):
            with self.subTest(mode=oct(mode)):
                info = zipfile.ZipInfo('textures/link.png')
                info.create_system = 3
                info.external_attr = mode << 16
                self.assertIn('link', self.refused(self.make([('a.obj', OBJ), (info, b'/etc/passwd')], name='l.zip')))

    def test_unix_entries_with_permission_bits_and_no_type_are_regular_files(self):
        """Security review F2: zipfile.writestr and other tools write 0o600 with no type nibble; that is a file."""
        entries = []
        for name, data, mode in (('a.obj', OBJ, 0o600), ('b.mtl', MTL, 0o644), ('t/c.png', PNG, stat.S_IFREG | 0o644)):
            info = zipfile.ZipInfo(name)
            info.create_system, info.external_attr = 3, mode << 16
            entries.append((info, data))
        result = archive.extract(self.make(entries), self.target, formats.BY_CODE['obj'])
        self.assertEqual(sorted(path for path, _kind in result['members']), ['a.obj', 'b.mtl', 't/c.png'])
        shutil.rmtree(self.target)
        for mode in (stat.S_IFSOCK | 0o600, stat.S_IFBLK | 0o600):
            with self.subTest(mode=oct(mode)):
                info = zipfile.ZipInfo('t/odd.png')
                info.create_system, info.external_attr = 3, mode << 16
                self.refused(self.make([('a.obj', OBJ), (info, PNG)], name='o.zip'))

    def test_encrypted_entries_are_refused(self):
        path = self.make([('a.obj', OBJ)])
        raw = bytearray(path.read_bytes())
        for signature, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
            at = raw.find(signature)
            raw[at + offset] |= 0x1
        path.write_bytes(bytes(raw))
        self.assertIn('encrypted', self.refused(path))

    def test_zip_bombs_are_refused_before_writing(self):
        self.assertIn('zip bomb', self.refused(self.make([('a.obj', OBJ), ('big.png', PNG + b'\x00' * (40 << 20))])))

    def test_shared_compressed_data_counts_every_declared_byte(self):
        with patch.object(archive, 'MAX_TOTAL', 64 << 10):
            entries = [('a.obj', OBJ)] + [('copy_%d.png' % i, PNG + b'\x00' * (20 << 10)) for i in range(4)]
            self.assertIn('unpacks to more than', self.refused(self.make(entries)))

    def test_entry_count_is_bounded(self):
        with patch.object(archive, 'MAX_ENTRIES', 8):
            self.assertIn('more than 8 files', self.refused(self.make([('m%d.obj' % i, OBJ) for i in range(9)])))

    def test_damaged_data_is_refused_and_removed(self):
        path = self.make([('a.obj', OBJ), ('b.png', PNG + bytes(range(256)) * 64)])
        raw = bytearray(path.read_bytes())
        at = raw.find(b'b.png') + len(b'b.png') + 40
        raw[at] ^= 0xFF
        path.write_bytes(bytes(raw))
        self.assertIn('damaged', self.refused(path))

    def test_not_a_zip_is_refused(self):
        path = self.root / 'x.zip'
        path.write_bytes(b'this is not an archive')
        self.assertIn('not a readable zip', self.refused(path))

    def test_oversized_texture_header_is_refused(self):
        huge = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 20000, 20000) + b'\x08\x06\x00\x00\x00'
        self.assertIn('pixels a side', self.refused(self.make([('a.obj', OBJ), ('huge.png', huge)])))

    def test_stop_while_writing_removes_the_folder(self):
        path = self.make([('a.obj', OBJ), ('b.obj', OBJ)])
        calls = []

        def check():
            calls.append(1)
            if len(calls) > 1:
                raise InterruptedError('stopped')
        with self.assertRaises(InterruptedError):
            archive.extract(path, self.target, formats.BY_CODE['obj'], check)
        self.assertFalse(self.target.exists())

    def test_an_existing_folder_is_never_reused(self):
        self.target.mkdir()
        with self.assertRaises(FileExistsError):
            archive.extract(self.make([('a.obj', OBJ)]), self.target, formats.BY_CODE['obj'])

    def test_compression_is_bounded_per_entry_only_above_the_floor(self):
        # A highly compressible small side file (a .mtl of repeats) is normal and stays below RATIO_FLOOR.
        small = MTL + b'# padding\n' * (50 << 10)
        result = archive.extract(self.make([('a.obj', OBJ), ('a.mtl', small)]), self.target, formats.BY_CODE['obj'])
        self.assertIn(('a.mtl', 'mtl'), result['members'])
        self.assertGreater(len(small), 200 * len(zlib.compress(small)))


if __name__ == '__main__':
    unittest.main()
