# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixture pictures a GUI suite paints itself (1.0.5).

The repository's pictures are in Git LFS: a short text pointer in a ``git archive`` snapshot or a worktree that skips
LFS, so a suite that read one failed there (test_blender_studio: "Fixture artwork did not decode", studio-disc-full on
6ceaef365ff). ``painted_picture`` is a real picture in any checkout (test_native_suite_fixtures keeps every suite off
LFS files). Main thread only: it uses Blender's image API.
"""

import bpy


def painted_picture(path, file_format='PNG', size=256):
    """A teal-to-amber gradient with a disc, ``size`` pixels square, written to ``path`` as ``file_format``; its
    bytes."""
    image = bpy.data.images.new("Angelo QA Fixture", width=size, height=size)
    try:
        pixels = []
        for y in range(size):
            for x in range(size):
                inside = (x - size / 2) ** 2 + (y - size / 2) ** 2 < (size * 0.27) ** 2
                pixels += (0.95, 0.62, 0.2, 1.0) if inside else (0.1, 0.45 + 0.4 * x / size, 0.5 + 0.3 * y / size, 1.0)
        image.pixels.foreach_set(pixels)
        image.filepath_raw, image.file_format = str(path), file_format
        image.save()
    finally:
        bpy.data.images.remove(image)
    with open(path, "rb") as handle:
        return handle.read()
