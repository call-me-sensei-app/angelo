# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixed, bounded QA mutations sent through existing scene-Python consent/tool owners.

No oracle import: this is the system under test's input, not its expected result.
There is no sleep, busy-wait or barrier inside a tool. Tool result timestamps
bracket actual edits and the dependency update only.
"""

HELPERS = '''import bpy
import os
import time
import threading

def collection(name):
    found = bpy.data.collections.get(name)
    if found is None:
        found = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(found)
    return found

def material(identity, name, colour):
    found = next((m for m in bpy.data.materials if m.get('qa_palette') == identity), None)
    if found is None:
        found = bpy.data.materials.new(name)
        found['qa_palette'] = identity
        found.use_nodes = True
        found.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = colour
    return found

def cube(identity, name, target, location, scale, paint):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-.5,-.5,-.5),(.5,-.5,-.5),(.5,.5,-.5),(-.5,.5,-.5),
                     (-.5,-.5,.5),(.5,-.5,.5),(.5,.5,.5),(-.5,.5,.5)], [],
                    [(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj['qa_entity_id'] = identity
    obj.location, obj.scale = location, scale
    target.objects.link(obj)
    mesh.materials.append(paint)
    return obj
'''


def setup(protected):
    code = HELPERS + '''
for obj in list(bpy.data.objects):
    mesh = obj.data if obj.type == 'MESH' else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
'''
    if protected:
        code += '''
paint = material('protected:base', 'QA worker Paint', [.9,.1,.8,1])
group = collection('QA protected')
cube('protected:0', 'QA-worker-0001', group, (-10,-10,1), (2,2,2), paint)
cube('protected:1', 'QA protected sentinel', group, (-13,-10,1), (2,2,2), paint)
'''
    return code + "\nbpy.context.view_layer.update()\nresult = {'prepared': True, 'pid': os.getpid()}\n"


def script(job):
    if (job['actor'] not in ('lead', 'worker') or job['phase'] not in ('create', 'transform', 'material', 'delete', 'place')
            or not 1 <= len(job['indices']) <= 64 or len(set(job['indices'])) != len(job['indices'])
            or any(type(i) is not int or not 0 <= i < 1536 for i in job['indices'])):
        raise ValueError('Invalid bounded mutation batch')
    return HELPERS + '\njob = ' + repr(dict(job)) + '''
actor, phase = job['actor'], job['phase']
start_ns = time.monotonic_ns()
objects = {obj.get('qa_entity_id'): obj for obj in bpy.data.objects}
changed = 0
mutation_spans = []
if phase == 'create':
    group = collection('QA ' + actor)
    colour = [.15,.25,.75,1] if actor == 'lead' else [.75,.2,.15,1]
    paint = material(actor + ':base', 'QA ' + actor + ' Paint', colour)
elif phase == 'material':
    colour = [.2,.7,.3,1] if actor == 'lead' else [.6,.3,.8,1]
    paint = material(actor + ':changed', 'QA ' + actor + ' Changed', colour)
for i in job['indices']:
    identity = '%s:%04d' % (actor, i)
    if phase == 'create':
        if identity in objects:
            raise RuntimeError('Duplicate identity before creation')
        row, column, layer = (i // 16) % 16, i % 16, i // 256
        mutation_start = time.monotonic_ns()
        obj = cube(identity, 'QA-%s-%04d' % (actor, i), group,
                   (column*2 + (64 if actor == 'worker' else 0), row*2, layer*2), (1,1,1), paint)
    else:
        obj = objects.get(identity)
        if obj is None:
            raise RuntimeError('Missing mutation target ' + identity)
        mutation_start = time.monotonic_ns()
        if phase == 'transform':
            obj.location.z += .25
            obj.scale = (1.25,.75,1.5)
        elif phase == 'material':
            obj.data.materials.clear()
            obj.data.materials.append(paint)
        elif phase == 'delete':
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        elif phase == 'place':
            obj.location.y += 40
    mutation_spans.append([i, mutation_start, time.monotonic_ns()])
    changed += 1
bpy.context.view_layer.update()
result = dict(job, start_ns=start_ns, end_ns=time.monotonic_ns(), pid=os.getpid(),
              mutated=changed, mutation_spans=mutation_spans, main_thread=threading.current_thread() is threading.main_thread())
'''
