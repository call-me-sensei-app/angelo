# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent semantic oracle and receipt checks for the no-model concurrent edit fixture.

Expected state is frozen before any tool runs. It is calculated from the stated
case, never from mutation results, the script generator, or merge receipts.
The RNA observer reads the actual scene independently in either Blender process.
"""
import hashlib
import itertools
import json
import math

VERSION = 'concurrent-edit-3072-v1'
COUNT = 1536
BATCH = 64
SURVIVORS = 1408
PHASES = ('create', 'transform', 'material', 'delete')
ACTORS = ('lead', 'worker')
PLACEMENT = (0.0, 40.0, 0.0)
TOLERANCE = 1e-5
PALETTES = {'lead:base': [0.15, 0.25, 0.75, 1.0], 'lead:changed': [0.2, 0.7, 0.3, 1.0],
            'worker:base': [0.75, 0.2, 0.15, 1.0], 'worker:changed': [0.6, 0.3, 0.8, 1.0],
            'protected:base': [0.9, 0.1, 0.8, 1.0]}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def cube_geometry():
    points = sorted(itertools.product((-0.5, 0.5), repeat=3))
    faces = sorted(sorted(point for point in points if point[axis] == side)
                   for axis in range(3) for side in (-0.5, 0.5))
    return digest({'vertices': points, 'faces': faces})


def matrix(location, scale=(1, 1, 1)):
    return [[scale[0], 0, 0, location[0]], [0, scale[1], 0, location[1]],
            [0, 0, scale[2], location[2]], [0, 0, 0, 1]]


def expected(stage='delete', actors=ACTORS, *, placed=False, protected=True):
    if stage not in PHASES or any(actor not in ACTORS for actor in actors):
        raise ValueError('Unknown case stage or actor')
    rows = []
    for actor in actors:
        for i in range(SURVIVORS if stage == 'delete' else COUNT):
            x = 2 * (i % 16) + (64 if actor == 'worker' else 0)
            y, z = 2 * ((i // 16) % 16), 2 * (i // 256)
            transformed = stage != 'create' and i % 3 == 0
            palette = actor + (':changed' if stage in ('material', 'delete') and i % 5 == 0 else ':base')
            rows.append({'id': '%s:%04d' % (actor, i), 'type': 'MESH',
                         'matrix': matrix((x, y + (40 if placed and actor == 'worker' else 0),
                                           z + (0.25 if transformed else 0)),
                                          (1.25, 0.75, 1.5) if transformed else (1, 1, 1)),
                         'geometry': cube_geometry(), 'mesh_users': 1, 'collections': ['QA ' + actor],
                         'materials': [{'palette': palette, 'rgba': PALETTES[palette]}]})
    if protected:
        for i, name in enumerate(('QA-worker-0001', 'QA protected sentinel')):
            rows.append({'id': 'protected:%d' % i, 'name': name, 'type': 'MESH',
                         'matrix': matrix((-10 - i * 3, -10, 1), (2, 2, 2)), 'geometry': cube_geometry(),
                         'mesh_users': 1, 'collections': ['QA protected'],
                         'materials': [{'palette': 'protected:base', 'rgba': PALETTES['protected:base']}]})
    return sorted(rows, key=lambda row: row['id'])


def observe(bpy):
    """Main thread only; plain JSON from current RNA, not cached tool/model reports."""
    rows = []
    bpy.context.view_layer.update()
    for obj in bpy.context.scene.objects:
        row = {'id': obj.get('qa_entity_id'), 'name': obj.name, 'type': obj.type,
               'matrix': [list(axis) for axis in obj.matrix_world],
               'collections': sorted(c.name for c in obj.users_collection), 'materials': []}
        if obj.type == 'MESH':
            points = [tuple(float(v) for v in vertex.co) for vertex in obj.data.vertices]
            faces = sorted(sorted(points[i] for i in face.vertices) for face in obj.data.polygons)
            row.update(geometry=digest({'vertices': sorted(points), 'faces': faces}), mesh_users=obj.data.users)
        for slot in obj.material_slots:
            material = slot.material
            node = next((n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None) \
                if material is not None and material.use_nodes else None
            row['materials'].append({'palette': material.get('qa_palette') if material else None,
                                     'rgba': list(node.inputs['Base Color'].default_value) if node else None})
        rows.append(row)
    return sorted(rows, key=lambda row: (str(row['id']), row['name']))


def _same(expected_value, actual):
    if isinstance(expected_value, (int, float)) and not isinstance(expected_value, bool):
        return type(actual) in (int, float) and math.isfinite(actual) and abs(actual - expected_value) <= TOLERANCE
    if isinstance(expected_value, list):
        return isinstance(actual, list) and len(actual) == len(expected_value) and all(
            _same(left, right) for left, right in zip(expected_value, actual))
    if isinstance(expected_value, dict):
        return isinstance(actual, dict) and all(key in actual and _same(value, actual[key])
                                                for key, value in expected_value.items())
    return expected_value == actual


def compare(wanted, actual):
    """Every semantic field, no missing/duplicate/extra IDs. Ordinary auto-renamed names are diagnostic only."""
    failures, found = [], {}
    for row in actual:
        identity = row.get('id')
        if not isinstance(identity, str) or identity in found:
            failures.append('missing or duplicate identity: %r' % identity)
            continue
        found[identity] = row
    expected_ids = {row['id'] for row in wanted}
    if set(found) != expected_ids:
        failures.append('identity set differs: missing %d, extra %d' % (
            len(expected_ids - set(found)), len(set(found) - expected_ids)))
    for row in wanted:
        other = found.get(row['id'])
        if other is not None and not _same(row, other):
            failures.append('semantic state differs: ' + row['id'])
    return failures


def batches(actor, phase):
    if actor not in ACTORS or phase not in PHASES + ('place',):
        raise ValueError('Unknown batch owner or phase')
    ids = (list(range(COUNT)) if phase == 'create' else list(range(0, COUNT, 3)) if phase == 'transform'
           else list(range(0, COUNT, 5)) if phase == 'material' else list(range(SURVIVORS, COUNT))
           if phase == 'delete' else list(range(SURVIVORS)))
    return [{'actor': actor, 'phase': phase, 'batch': offset // BATCH, 'indices': ids[offset:offset + BATCH],
             'job_id': '%s/%s/%d' % (actor, phase, offset // BATCH)} for offset in range(0, len(ids), BATCH)]


def temporal_proof(events, jobs, pids, *, parallel):
    """Actual mutation intervals, bounded by host admission/result clocks; no wait/sleep counts as work."""
    expected_jobs = {job['job_id']: job for job in jobs}
    found, errors = {}, []
    if len(expected_jobs) != len(jobs):
        errors.append('duplicate planned job identity')
    for event in events:
        job = expected_jobs.get(event.get('job_id'))
        if job is None or event['job_id'] in found:
            errors.append('unknown or duplicate execution event')
            continue
        found[event['job_id']] = event
        if any(event.get(k) != job[k] for k in ('actor', 'phase', 'batch', 'indices')):
            errors.append('execution ownership/phase differs: ' + job['job_id'])
        clocks = [event.get(k) for k in ('admitted_ns', 'start_ns', 'end_ns', 'answered_ns')]
        if (any(type(v) is not int or v <= 0 for v in clocks) or clocks != sorted(clocks)
                or clocks[1] >= clocks[2] or event.get('pid') != pids.get(job['actor'])
                or event.get('mutated') != len(job['indices']) or event.get('main_thread') is not True):
            errors.append('invalid execution proof: ' + job['job_id'])
        spans = event.get('mutation_spans')
        if not isinstance(spans, list) or len(spans) != len(job['indices']):
            errors.append('missing per-object mutation intervals: ' + job['job_id'])
        else:
            previous = event.get('start_ns')
            for index, span in zip(job['indices'], spans):
                if (not isinstance(span, list) or len(span) != 3 or span[0] != index
                        or any(type(value) is not int for value in span)
                        or type(previous) is not int or type(event.get('end_ns')) is not int
                        or not previous <= span[1] < span[2] <= event['end_ns']):
                    errors.append('invalid per-object mutation interval: ' + job['job_id'])
                    break
                previous = span[2]
    if set(found) != set(expected_jobs):
        errors.append('missing execution events')
    overlaps = []
    if not errors and parallel:
        if pids.get('lead') == pids.get('worker'):
            errors.append('parallel actors share a process')
        else:
            for lead in (e for e in events if e['actor'] == 'lead'):
                for worker in (e for e in events if e['actor'] == 'worker'):
                    ns = min(lead['end_ns'], worker['end_ns']) - max(lead['start_ns'], worker['start_ns'])
                    if ns > 0:
                        crossings = [(a[0], b[0], min(a[2], b[2]) - max(a[1], b[1]))
                                     for a in lead['mutation_spans'] for b in worker['mutation_spans']
                                     if min(a[2], b[2]) > max(a[1], b[1])]
                        if crossings:
                            overlaps.append({'lead': lead['job_id'], 'worker': worker['job_id'],
                                             'overlap_ns': sum(row[2] for row in crossings),
                                             'mutation_pairs': len(crossings), 'first_indices': list(crossings[0][:2])})
    return {'complete': not errors and (bool(overlaps) if parallel else True), 'errors': errors,
            'overlap_pairs': overlaps, 'reason': 'invalid proof' if errors else
            'observed mutation overlap' if overlaps else 'no mutation overlap observed' if parallel else 'serial control'}
