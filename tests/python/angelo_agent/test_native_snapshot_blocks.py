# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests: .blend segmentation for deduplicated snapshots.

The segmenter only chooses cut points. Every case checks the invariant the
store relies on: segments concatenate to exactly the input bytes, and no input
(legacy, compressed, truncated or corrupt) raises.
"""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_fixtures import LARGE_BHEAD8, blend_v0, blend_v1, scene_blocks  # noqa: E402
from angelo_agent.snapshot_blocks import (FORMAT_BLOCKS_V0, FORMAT_BLOCKS_V1, FORMAT_FIXED,  # noqa: E402
    GROUP_TARGET_BYTES, MAX_SEGMENT_BYTES, STANDALONE_BLOCK_BYTES, BlendSegmenter)


def split(data, size=None):
    segmenter = BlendSegmenter(io.BufferedReader(io.BytesIO(data), 64 * 1024), len(data) if size is None else size)
    return list(segmenter), segmenter


class SegmenterTests(unittest.TestCase):
    def assertExact(self, data, segments):
        self.assertEqual(b"".join(segments), data)
        self.assertTrue(all(segments), "empty segments would waste index rows")
        self.assertTrue(all(len(piece) <= MAX_SEGMENT_BYTES + STANDALONE_BLOCK_BYTES for piece in segments))

    def test_current_format_cuts_at_id_blocks_and_subchunks_large_blocks(self):
        data = blend_v1(scene_blocks(objects=30))
        segments, segmenter = split(data)
        self.assertExact(data, segments)
        self.assertEqual(segmenter.format, FORMAT_BLOCKS_V1)
        self.assertEqual(segmenter.fallback, "")
        self.assertEqual(segments[0], data[:17])
        # Each ID (OB, ME) starts a new small group; the 3 MiB array becomes
        # 1 MiB pieces whose first one carries its 32-byte block header.
        object_groups = [piece for piece in segments if piece[:4] == b"OB\0\0"]
        self.assertEqual(len(object_groups), 30)
        large = [piece for piece in segments if len(piece) == MAX_SEGMENT_BYTES]
        self.assertEqual(len(large), 3)
        self.assertEqual(segmenter.blocks, 1 + 30 * 4 + 2 + 1)

    def test_one_object_edit_changes_one_small_segment_and_array_edit_one_piece(self):
        base = set(split(blend_v1(scene_blocks()))[0])
        moved = split(blend_v1(scene_blocks(moved={7})))[0]
        changed = [piece for piece in moved if piece not in base]
        self.assertEqual(len(changed), 1)
        self.assertLess(len(changed[0]), GROUP_TARGET_BYTES + STANDALONE_BLOCK_BYTES)
        edited = split(blend_v1(scene_blocks(mesh_offset=1024 * 1024 + 4096)))[0]
        self.assertEqual(len([piece for piece in edited if piece not in base]), 1)

    def test_small_groups_close_at_their_target(self):
        blocks = [(b"OB\0\0", bytes(100), 1)] + [(b"DATA", bytes([index]) * 4000, 10 + index) for index in range(40)]
        data = blend_v1(blocks)
        segments, _ = split(data)
        self.assertExact(data, segments)
        groups = segments[1:-1]
        self.assertGreater(len(groups), 5)
        self.assertTrue(all(len(piece) < GROUP_TARGET_BYTES + 4100 for piece in groups))

    def test_legacy_headers_are_parsed_for_both_pointer_sizes_and_endians(self):
        blocks = scene_blocks(objects=5, array_bytes=200 * 1024)
        for pointer_size in (4, 8):
            for big_endian in (False, True):
                with self.subTest(pointer_size=pointer_size, big_endian=big_endian):
                    data = blend_v0(blocks, pointer_size=pointer_size, big_endian=big_endian)
                    segments, segmenter = split(data)
                    self.assertExact(data, segments)
                    self.assertEqual(segmenter.format, FORMAT_BLOCKS_V0)
                    self.assertEqual(segmenter.fallback, "")
                    self.assertEqual(segments[0], data[:12])

    def test_unknown_or_compressed_files_fall_back_to_fixed_chunks(self):
        for prefix in (b"\x28\xb5\x2f\xfd", b"\x1f\x8b\x08\x00", b"BLENDER99-02v0600", b"NOTBLEND"):
            data = prefix + bytes(range(256)) * 9000
            with self.subTest(prefix=prefix):
                segments, segmenter = split(data)
                self.assertExact(data, segments)
                self.assertEqual(segmenter.format, FORMAT_FIXED)
                self.assertTrue(segmenter.fallback)
                self.assertTrue(all(len(piece) == MAX_SEGMENT_BYTES for piece in segments[:-1]))

    def test_truncated_or_corrupt_blocks_never_raise_and_keep_every_byte(self):
        data = blend_v1(scene_blocks(objects=8, array_bytes=300 * 1024))
        cases = {
            "truncated header": data[:17 + 10],
            "truncated data": data[:len(data) // 2],
            "empty": b"",
            "header only": data[:9],
        }
        corrupt = bytearray(data)
        corrupt[17 + 16:17 + 24] = (2**62).to_bytes(8, "little")  # first block length beyond the file
        cases["length beyond file"] = bytes(corrupt)
        negative = bytearray(data)
        negative[17 + 16:17 + 24] = (-5).to_bytes(8, "little", signed=True)
        cases["negative length"] = bytes(negative)
        for name, sample in cases.items():
            with self.subTest(case=name):
                segments, segmenter = split(sample)
                self.assertEqual(b"".join(segments), sample)
                if name in {"length beyond file", "negative length", "truncated header", "truncated data"}:
                    self.assertTrue(segmenter.fallback)

    def test_bytes_after_endb_and_a_file_shorter_than_its_stat_are_preserved(self):
        data = blend_v1(scene_blocks(objects=3, array_bytes=1024), trailer=b"trailing bytes after ENDB")
        segments, segmenter = split(data)
        self.assertExact(data, segments)
        self.assertEqual(segments[-1], b"trailing bytes after ENDB")
        shorter, _ = split(data, size=len(data) + 1000)
        self.assertEqual(b"".join(shorter), data)

    def test_block_header_layout_matches_large_bhead8(self):
        self.assertEqual(LARGE_BHEAD8.size, 32)  # code, SDNAnr, old, len, nr


if __name__ == "__main__":
    unittest.main()
