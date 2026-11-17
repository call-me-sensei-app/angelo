# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rigid bodies joined by a constraint are freed in any order without a use of freed memory (BACKGROUND).

Blender #91959 and #164309 (an intermittent crash on Undo with rigid body constraints). A Bullet body, a
constraint and a world belong to different data and are freed when that data is: Undo, Redo and loading a file
free a body before the constraint made with it; removing an object's data-block frees a constraint, or a body, the
world still lists; an object freed without a known world asks every scene's world to give its body up
(doc/angelo/rigid_body_teardown.rst). Official Blender 5.3.0 stops with a segmentation fault in the first three
orders once freed memory is filled, and drops another scene's body in the last.

One child process of this binary per order, so one order's crash is its own result:

``undo``               simulate, then Undo and Redo across the simulation, stepping after each
``load``               simulate, then load the startup file, twice
``remove_constraint``  simulate, remove the constraint's object, step, then the bodies
``remove_body``        simulate, remove one body's object, step, then the constraint's
``two_scenes``         two scenes that both simulate; remove a body of the second, step both, load

Each child runs with the system allocator's own switch that fills freed memory where the platform has one
(``MallocScribble`` on macOS, ``MALLOC_PERTURB_`` with glibc's allocator), and says whether its freed memory really
is filled: without it a use of freed memory usually reads the stale block and passes. On macOS the suite fails
when that switch did not take. ``rigidbody_test`` (C++) covers every constraint type on every platform by filling
what Bullet frees itself. No provider, account or network; no paid request.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).
import json
import subprocess
import traceback
from pathlib import Path

import bpy

assert '--factory-startup' in sys.argv and bpy.app.background
CHILD_FLAG = '--rigid-body-order'
ORDERS = ('undo', 'load', 'remove_constraint', 'remove_body', 'two_scenes')
PAIRS = 3
MARK = 'ANGELO_RIGID_BODY_TEARDOWN'


def say(*words):
    print(MARK, *words, flush=True)


# ---------------------------------------------------------------------------------------------------------------
# The child: one order.

def freed_memory_is_filled():
    """Whether this process's allocator fills what it frees (0x55, both switches' byte)."""
    import ctypes
    try:
        libc = ctypes.CDLL(None)
        libc.malloc.restype = ctypes.c_void_p
        libc.malloc.argtypes = [ctypes.c_size_t]
        libc.free.argtypes = [ctypes.c_void_p]
        block = libc.malloc(4096)
        ctypes.memset(block, 0x11, 4096)
        libc.free(block)
        return ctypes.string_at(block + 2048, 1) == b'\x55'
    except (OSError, AttributeError, TypeError):   # No C library to ask (Windows): not filled.
        return False


def build(pairs):
    """``pairs`` times: two active bodies, then an Empty whose Fixed constraint joins them."""
    made = []
    for index in range(pairs):
        bodies = []
        for z in (3.0, 6.0):
            bpy.ops.mesh.primitive_cube_add(location=(index * 4.0, 0.0, z))
            bpy.ops.rigidbody.object_add(type='ACTIVE')
            bodies.append(bpy.context.active_object)
        bpy.ops.object.empty_add(location=(index * 4.0, 0.0, 4.5))
        empty = bpy.context.active_object
        bpy.ops.rigidbody.constraint_add(type='FIXED')
        empty.rigid_body_constraint.object1, empty.rigid_body_constraint.object2 = bodies
        made.append((bodies[0].name, bodies[1].name, empty.name))
    assert bpy.context.scene.rigidbody_world is not None
    return made


def simulate(scene, first, last):
    for frame in range(first, last + 1):
        scene.frame_set(frame)


def height(scene, name):
    evaluated = bpy.data.objects[name].evaluated_get(scene.view_layers[0].depsgraph)
    return round(evaluated.matrix_world.translation.z, 3)


def simulated(scene, name, start):
    fell = height(scene, name)
    assert fell < start - 0.2, '%s did not fall: %s from %s' % (name, fell, start)
    return fell


def order_undo():
    scene = bpy.context.scene
    bpy.ops.ed.undo_push(message='QA before')
    made = build(PAIRS)
    bpy.ops.ed.undo_push(message='QA built')
    simulate(scene, 1, 12)
    say('simulated to', simulated(scene, made[0][0], 3.0))
    bpy.data.objects[made[0][0]].location.x += 1.0
    bpy.ops.ed.undo_push(message='QA moved')
    for step in ('undo', 'undo', 'redo', 'redo', 'undo'):
        getattr(bpy.ops.ed, step)()
        say(step, 'returned;', len(bpy.data.objects), 'objects')
        simulate(bpy.context.scene, 1, 6)


def order_load():
    for turn in (1, 2):
        made = build(PAIRS)
        simulate(bpy.context.scene, 1, 12)
        say('simulated to', simulated(bpy.context.scene, made[0][0], 3.0))
        bpy.ops.wm.read_homefile(use_factory_startup=True)
        say('load', turn, 'returned;', len(bpy.data.objects), 'objects')


def order_remove(first):
    """Remove the constraint's objects or the first bodies first, stepping after each kind."""
    scene = bpy.context.scene
    made = build(PAIRS)
    simulate(scene, 1, 12)
    say('simulated to', simulated(scene, made[0][0], 3.0))
    frame = 12
    for which in {'constraint': (2, 0, 1), 'body': (0, 2, 1)}[first]:
        for names in made:
            bpy.data.objects.remove(bpy.data.objects[names[which]], do_unlink=True)
        simulate(scene, frame + 1, frame + 4)
        frame += 4
        say('removed every', ('first body', 'second body', "constraint's object")[which], 'and stepped to', frame)
    simulate(scene, 1, 6)


def order_two_scenes():
    first = bpy.context.scene
    made = build(1)
    second = bpy.data.scenes.new('QA second')
    others = []
    for index in range(2):
        other = bpy.data.objects.new('QA other %d' % index, bpy.data.objects[made[0][0]].data.copy())
        other.location = (index * 4.0, 8.0, 3.0)
        second.collection.objects.link(other)
        with bpy.context.temp_override(scene=second, view_layer=second.view_layers[0], object=other,
                                       active_object=other, selected_objects=[other]):
            bpy.ops.rigidbody.object_add(type='ACTIVE')
        assert other.rigid_body is not None
        others.append(other.name)
    assert second.rigidbody_world is not None and second.rigidbody_world != first.rigidbody_world
    simulate(first, 1, 12)
    simulate(second, 1, 12)
    say('simulated to', simulated(first, made[0][0], 3.0), 'and', simulated(second, others[1], 3.0))

    # The body is in the second scene's world; freed as a data-block, it asks the first scene's world first.
    bpy.data.objects.remove(bpy.data.objects[others[0]], do_unlink=True)
    before = [height(first, made[0][0]), height(first, made[0][1]), height(second, others[1])]
    simulate(first, 13, 18)
    simulate(second, 13, 18)
    after = [height(first, made[0][0]), height(first, made[0][1]), height(second, others[1])]
    say('after a body of the second scene was removed:', before, '->', after)
    assert all(now < then - 0.2 for then, now in zip(before, after)), \
        'a body stopped being simulated: %s -> %s' % (before, after)
    bpy.ops.wm.read_homefile(use_factory_startup=True)
    say('load returned;', len(bpy.data.scenes), 'scene')


def child(order):
    say('order', order, 'filled', 'yes' if freed_memory_is_filled() else 'no')
    {'undo': order_undo, 'load': order_load, 'remove_constraint': lambda: order_remove('constraint'),
     'remove_body': lambda: order_remove('body'), 'two_scenes': order_two_scenes}[order]()
    say('DONE', order)


# ---------------------------------------------------------------------------------------------------------------
# The suite: every order in its own process.

def run_order(out, order):
    env = dict(os.environ, MallocScribble='1', MALLOC_PERTURB_='85')
    command = [bpy.app.binary_path, '--background', '--factory-startup', '--python-exit-code', '9',
               '--python', __file__, '--', CHILD_FLAG, order]
    log = out / ('order-%s.log' % order)
    try:
        with open(log, 'wb') as output:
            code = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=240).returncode
    except subprocess.TimeoutExpired:
        code = 'timeout'
    lines = [line[len(MARK) + 1:] for line in log.read_text(encoding='utf-8', errors='replace').splitlines()
             if line.startswith(MARK + ' ')]
    return {'exit': code, 'done': ('DONE ' + order) in lines, 'filled': bool(lines) and lines[0].endswith('filled yes'),
            'lines': lines[-8:]}


def main():
    out = Path(os.environ['ANGELO_QA_DIR']).resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'checks': {}, 'orders': {}, 'paid_requests': 0, 'binary': bpy.app.binary_path,
              'platform': sys.platform}
    try:
        for order in ORDERS:
            row = report['orders'][order] = run_order(out, order)
            report['checks'][order + '_returns'] = row['exit'] == 0 and row['done']
        filled = [row['filled'] for row in report['orders'].values()]
        report['freed_memory_filled'] = all(filled)
        if sys.platform == 'darwin':   # The switch exists here: a run without it would prove far less.
            report['checks']['freed_memory_was_filled'] = all(filled)
        report['success'] = all(report['checks'].values())
    except Exception:
        report['error'] = traceback.format_exc()
        raise
    finally:
        (out / 'results.json').write_text(json.dumps(report, indent=1, default=str))
        print(MARK + '_SUITE', json.dumps({'success': report['success'], 'checks': report['checks']}), flush=True)


if CHILD_FLAG in sys.argv:
    child(sys.argv[sys.argv.index(CHILD_FLAG) + 1])
else:
    main()
