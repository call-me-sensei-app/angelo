# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real cache files under existing NTFS-sharing model; not actual Windows or Fab authentication."""
import suite_isolation  # noqa: F401
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import team_library_fixtures as fixtures
import ntfs_sharing
from angelo_agent import fab_cache, shared_replace


class FabCacheSharedFiles(unittest.TestCase):
    def setUp(self):
        self.folder_owner = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder_owner.cleanup)
        self.folder = Path(self.folder_owner.name)
        self.payload = fixtures.glb(images=[fixtures.png()])
        self.variant = SimpleNamespace(size=len(self.payload))
        self.sharing = ntfs_sharing.Sharing(self)

    @contextmanager
    def stream(self):
        yield iter([self.payload])

    def test_publication_replaces_both_names_while_delete_sharing_readers_hold_old_files(self):
        asset, receipt = self.folder / 'asset.glb', self.folder / 'receipt.json'
        asset.write_bytes(b'old payload')
        receipt.write_text('old receipt')
        with shared_replace.open_file(asset) as held_asset, shared_replace.open_file(receipt) as held_receipt:
            with patch.object(shared_replace, 'replace', wraps=shared_replace.replace) as replaced:
                path, digest = fab_cache._download(self.folder, self.variant, self.stream, lambda: None, None)
            self.assertEqual([call.args[1] for call in replaced.call_args_list], [asset, receipt])
            self.assertTrue(all(call.kwargs == {'told': False} for call in replaced.call_args_list))
            self.assertEqual(held_asset.read(), b'old payload')
            self.assertEqual(held_receipt.read(), b'old receipt')
        self.assertTrue(held_asset.closed and held_receipt.closed)
        self.assertEqual(path.read_bytes(), self.payload)
        self.assertEqual(digest, hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(list(self.folder.glob('*.part')), [])
        self.assertEqual(self.sharing.pauses, [])

    def test_cache_receipt_and_digest_readers_allow_replacement_and_close_their_handles(self):
        path, digest = fab_cache._download(self.folder, self.variant, self.stream, lambda: None, None)
        receipt = self.folder / 'receipt.json'
        replacement_asset, replacement_receipt = self.folder / 'new-asset', self.folder / 'new-receipt'
        replacement_asset.write_bytes(self.payload)
        replacement_receipt.write_bytes(receipt.read_bytes())
        self.sharing.while_open(path, lambda: shared_replace.replace(replacement_asset, path, told=False))
        self.sharing.while_open(receipt, lambda: shared_replace.replace(replacement_receipt, receipt, told=False))
        opened = []
        original = shared_replace.open_file
        def observe(*args, **kwargs):
            handle = original(*args, **kwargs)
            opened.append(handle)
            return handle
        with patch.object(shared_replace, 'open_file', side_effect=observe):
            self.assertEqual(fab_cache._cached(self.folder, self.variant, lambda: None), (path, digest))
        self.assertEqual(len(opened), 2)
        self.assertTrue(all(handle.closed for handle in opened))
        self.assertEqual(self.sharing.pauses, [])
        self.assertEqual(json.loads(receipt.read_text())['sha256'], digest)
