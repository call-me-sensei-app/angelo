# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reproduction: reading a Python BYTE_STRING property runs its callbacks twice.

pyrna now asks RNA_property_string_length before trying the direct (one-copy) path. For a
property defined in Python with `get=` that length query calls the Python getter
(`length_ex`), and with `get_transform=` it runs the getter and the transform; the direct path
then declines (no plain `get`) and the ordinary read calls them again. The previous build called each
once per read. A stateful getter therefore returns different bytes than on the previous build (b'v2' for the first read
instead of b'v1'), and every such read costs two Python calls. Plain `str` properties are
unchanged. The product's own byte strings (the three Mesh buffers) have plain getters and are
not affected.

  Angelo -b --factory-startup --python bytestring_callback_repro.py

Exits 1 when a read runs a callback more than once.
"""
import sys

import bpy

calls = {'get': 0, 'transform': 0}


def get(_self):
    calls['get'] += 1
    return 'v%d' % calls['get']


def transform(_self, value, _is_set):
    calls['transform'] += 1
    return value + '!'


bpy.types.Object.review_get_bytes = bpy.props.StringProperty(subtype='BYTE_STRING', get=get)
bpy.types.Object.review_transform_bytes = bpy.props.StringProperty(subtype='BYTE_STRING',
                                                                   get_transform=transform)
obj = bpy.data.objects.new('review', None)
values = [obj.review_get_bytes for _ in range(3)]
obj.review_transform_bytes = b'abc'
transformed = [obj.review_transform_bytes for _ in range(3)]
print('get=', values, 'getter calls', calls['get'])
print('get_transform=', transformed, 'transform calls', calls['transform'])
ok = values == [b'v1', b'v2', b'v3'] and calls['get'] == 3 and calls['transform'] == 3
print('OK' if ok else 'CALLBACKS RUN TWICE PER READ')
sys.exit(0 if ok else 1)
