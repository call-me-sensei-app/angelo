# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixtures of ``test_blender_checkpoint_content_binding`` (built app): a portable in-place rewrite that keeps the
file's stamp, the rewrite hook on the first ``_native_facts`` call, SAVE_POST/FAIL observation and a Sculpt owner."""

import os
from unittest.mock import patch

import bpy

from angelo_agent import checkpoint_capture as capture_api
import file_times


def read_at(fd, size, offset):
    """``os.pread`` on every platform (Windows has none): read at ``offset``, then put the descriptor's offset back."""
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        chunks, remaining = [], size
        while remaining:
            data = os.read(fd, remaining)
            if not data:
                raise OSError("The file ended before %d more bytes" % remaining)
            chunks.append(data)
            remaining -= len(data)
        return b"".join(chunks)
    finally:
        os.lseek(fd, saved, os.SEEK_SET)


def write_at(fd, data, offset):
    """``os.pwrite`` on every platform: write all of ``data`` at ``offset``, then put the descriptor's offset back."""
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.lseek(fd, saved, os.SEEK_SET)


def rewrite_keeping_stamp(fd):
    """Another writer: one byte in the middle replaced in place, modification time put back exactly (futimens on
    POSIX, SetFileTime on Windows: ``file_times.set_times_exact``)."""
    before = os.fstat(fd)
    offset = before.st_size // 2
    byte = read_at(fd, 1, offset)
    write_at(fd, bytes([byte[0] ^ 0xFF]), offset)
    file_times.set_times_exact(fd, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.fstat(fd)
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == \
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns), "the stamp was not kept"


class Rewrite:
    """Patch the first ``_native_facts`` call of a matching owner: rewrite ``role`` before Python's first witness."""

    def __init__(self, role, purpose=None):
        self.role, self.purpose, self.done = role, purpose, False
        self.original = capture_api._native_facts

    def __call__(self, capture, owner):
        if not self.done and (self.purpose is None or dict(owner._binding.sealed)["purpose"] == self.purpose):
            outputs = owner._resource.outputs
            if self.role in outputs:
                self.done = True
                rewrite_keeping_stamp(outputs[self.role]._resource.descriptor)
        return self.original(capture, owner)

    def patch(self):
        return patch.object(capture_api, "_native_facts", self)


class SaveEvents:
    def __init__(self):
        self.events = []

    def __enter__(self):
        self.post = lambda *_: self.events.append("post")
        self.fail = lambda *_: self.events.append("fail")
        bpy.app.handlers.save_post.append(self.post)
        bpy.app.handlers.save_post_fail.append(self.fail)
        return self

    def __exit__(self, *_):
        bpy.app.handlers.save_post.remove(self.post)
        bpy.app.handlers.save_post_fail.remove(self.fail)


def sculpt_target(name="Binding Multires"):
    """A Multires object in Sculpt mode: the capture selects it as a Sculpt owner (a version 4 pair)."""
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
    target = bpy.context.object
    target.name = name
    modifier = target.modifiers.new("Binding Multires", "MULTIRES")
    assert bpy.ops.object.multires_subdivide(modifier=modifier.name, mode="CATMULL_CLARK") == {"FINISHED"}
    modifier.levels = modifier.sculpt_levels = 1
    assert bpy.ops.object.mode_set(mode="SCULPT") == {"FINISHED"}
    bpy.context.view_layer.update()
    return target


def read_all(fd):
    return read_at(fd, os.fstat(fd).st_size, 0)
