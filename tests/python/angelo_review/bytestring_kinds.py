# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every kind of byte-string property, read through pyrna.

For each kind: the value read three times and the number of Python callbacks each read ran.
Print-only; run on the previous build and on the change and diff the CASE lines, which must be identical. Kinds:
plain C getters (the Mesh buffers, PackedFile.data, StringAttributeValue.value, the
DNA-generated FileSelectParams.directory when a file browser exists), Python properties stored
as ID properties, with get=, get= and set= (set= alone is refused at definition), get_transform=, set_transform=, get= with
get_transform=, maxlen, on an ID, a PropertyGroup and an operator, custom properties through
path_resolve, and empty values of each. Python callbacks receive text, so the values are valid UTF-8 (on the previous
build and on the change alike, b'\xff' given to a property with get_transform= crashes in
bpy_prop_string_get_transform_fn).

  Angelo -b --factory-startup --python bytestring_kinds.py
"""
import hashlib

import bpy

calls = {}


def counted(name, fn):
    """Blender checks a callback's arity, so the wrapper keeps the wrapped function's."""
    arity = fn.__code__.co_argcount

    def bump():
        calls[name] = calls.get(name, 0) + 1

    if arity == 1:
        return lambda self: (bump(), fn(self))[1]
    if arity == 2:
        return lambda self, value: (bump(), fn(self, value))[1]
    if arity == 3:
        return lambda self, value, is_set: (bump(), fn(self, value, is_set))[1]
    return lambda self, new, current, is_set: (bump(), fn(self, new, current, is_set))[1]


def show(name, read):
    before = dict(calls)
    values = []
    for _ in range(3):
        try:
            values.append(read())
        except Exception as error:  # noqa: BLE001 - printed and compared
            values.append(f'{type(error).__name__}: {error}')
    ran = {k: calls.get(k, 0) - before.get(k, 0) for k in calls if calls.get(k, 0) != before.get(k, 0)}
    shown = [v if not isinstance(v, bytes) or len(v) <= 32 else
             f'<{len(v)} bytes {hashlib.sha256(v).hexdigest()[:12]}>' for v in values]
    print(f'CASE {name}: {shown} callbacks {ran}', flush=True)


store = {}


def getter(key):
    def get(_self):
        store[key + '_n'] = store.get(key + '_n', 0) + 1
        return store.get(key, '') + str(store[key + '_n'])
    return get


def setter(key):
    def set_(_self, value):
        store[key] = value
    return set_


B = 'BYTE_STRING'
props = {
    'idprop': {},
    'get': {'get': counted('get', getter('g'))},
    'get_set': {'get': counted('get_set.get', getter('gs')), 'set': counted('get_set.set', setter('gs'))},
    'get_transform': {'get_transform': counted('get_transform', lambda self, v, is_set: v + '!')},
    'set_transform': {'set_transform': counted('set_transform', lambda self, new, cur, is_set: new + '?')},
    'get_and_get_transform': {'get': counted('ggt.get', getter('ggt')),
                              'get_transform': counted('ggt.transform', lambda self, v, s: v.upper())},
    'maxlen': {'maxlen': 5},
}
for name, kwargs in props.items():
    setattr(bpy.types.Object, 'review_' + name, bpy.props.StringProperty(subtype=B, **kwargs))


class ReviewGroup(bpy.types.PropertyGroup):
    raw: bpy.props.StringProperty(subtype=B)
    cb: bpy.props.StringProperty(subtype=B, get=counted('group.get', getter('grp')))


bpy.utils.register_class(ReviewGroup)
bpy.types.Scene.review_group = bpy.props.PointerProperty(type=ReviewGroup)

seen = {}


class REVIEW_OT_bytes(bpy.types.Operator):
    bl_idname = 'review.bytes'
    bl_label = 'Review Bytes'
    raw: bpy.props.StringProperty(subtype=B)
    cb: bpy.props.StringProperty(subtype=B, get=counted('operator.get', getter('op')))

    def execute(self, _context):
        before = dict(calls)
        seen['raw'] = [self.raw for _ in range(3)]
        seen['cb'] = [self.cb for _ in range(3)]
        seen['calls'] = {k: calls.get(k, 0) - before.get(k, 0) for k in calls
                         if calls.get(k, 0) != before.get(k, 0)}
        return {'FINISHED'}


bpy.utils.register_class(REVIEW_OT_bytes)

obj = bpy.data.objects.new('review', None)
bpy.context.collection.objects.link(obj)
for value_label, value in (('text', b'ab\xc3\xa9'), ('empty', b'')):
    for name in props:
        attr = 'review_' + name
        try:
            setattr(obj, attr, value)
        except Exception as error:  # noqa: BLE001
            print(f'CASE set {name}/{value_label}: {type(error).__name__}', flush=True)
        show(f'{name}/{value_label}', lambda attr=attr: getattr(obj, attr))
    bpy.context.scene.review_group.raw = value
    show(f'group.raw/{value_label}', lambda: bpy.context.scene.review_group.raw)
    show(f'group.cb/{value_label}', lambda: bpy.context.scene.review_group.cb)
    obj['custom'] = value
    show(f'custom.path_resolve/{value_label}', lambda: obj.path_resolve('["custom"]'))
    show(f'custom.getitem/{value_label}', lambda: obj['custom'])
    bpy.ops.review.bytes(raw=value)
    print(f'CASE operator/{value_label}: {seen}', flush=True)

# Plain C getters.
mesh = bpy.data.meshes.new('m')
show('mesh.positions/empty', lambda: mesh.angelo_sculpt_positions)
show('mesh.topology/empty', lambda: mesh.angelo_sculpt_topology)
show('mesh.weights/empty', lambda: mesh.angelo_vertex_group_weights)
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
show('mesh.positions', lambda: mesh.angelo_sculpt_positions)
show('mesh.topology', lambda: mesh.angelo_sculpt_topology)
attribute = mesh.attributes.new('s', 'STRING', 'POINT')
for label, value in (('text', b'x\xe9y'), ('empty', b'')):
    attribute.data[0].value = value
    show(f'string_attribute/{label}', lambda: attribute.data[0].value)
image = bpy.data.images.new('packed', 2, 2)
image.pack(data=b'\0\1\2\xff', data_len=4)
show('packed_file', lambda: image.packed_file.data)
# FileSelectParams.directory exists only on an initialized file browser; in background mode
# the space's params are usually None.
screen = bpy.data.screens['Layout']
area = screen.areas[0]
area.type = 'FILE_BROWSER'
params = getattr(area.spaces.active, 'params', None)
print('CASE file_browser_params', params is not None, flush=True)
if params is not None:
    show('file_browser.directory', lambda: params.directory)
