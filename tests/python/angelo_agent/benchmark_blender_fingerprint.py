# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Serial exact-fingerprint timings and cProfile; no scene-query or cache shortcut.

Run current Angelo --background --factory-startup --python-exit-code 1 --python
this_file only after other native measurements stop. Set ANGELO_QA_DIR to a
separate before/after folder. One warmup and five fresh reads of a 16,641-vertex
grid; profiling is separate from timed samples. No provider/build/auth calls.
"""

import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import statistics
import sys
import tempfile
import time

import bmesh
import bpy

assert '--factory-startup' in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent.program_dependencies import _fingerprint
from angelo_agent.action_context import StagedActionContext, _mesh

OUT = Path(os.environ.get('ANGELO_QA_DIR') or Path(tempfile.gettempdir(), 'angelo-exact-fingerprint-profile'))
OUT.mkdir(parents=True, exist_ok=True)
mesh = bpy.data.meshes.new('Exact Fingerprint Profile Grid')
bm = bmesh.new()
try:
    bmesh.ops.create_grid(bm, x_segments=128, y_segments=128, size=1)
    bm.to_mesh(mesh)
finally:
    bm.free()
target = bpy.data.objects.new('Exact Fingerprint Profile Target', mesh)
bpy.context.scene.collection.objects.link(target)
bpy.context.view_layer.update()
context = StagedActionContext(bpy.context.scene, bpy.context.scene.collection, objects={'profile': target})
coordinate_samples = []
for repeat in range(6):
    start = time.perf_counter()
    _mesh(context, target)
    if repeat:
        coordinate_samples.append((time.perf_counter() - start) * 1000)
coordinate_profile = cProfile.Profile()
coordinate_profile.runcall(_mesh, context, target)
coordinate_stream = io.StringIO()
pstats.Stats(coordinate_profile, stream=coordinate_stream).strip_dirs().sort_stats('cumulative').print_stats(20)
(OUT / 'coordinate-profile.txt').write_text(coordinate_stream.getvalue())
coordinate_result = {'samples_ms': [round(value, 3) for value in coordinate_samples],
                     'median_ms': round(statistics.median(coordinate_samples), 3)}
samples, digests = [], []
for repeat in range(6):
    start = time.perf_counter()
    digests.append(_fingerprint(target))
    if repeat:
        samples.append((time.perf_counter() - start) * 1000)
assert len(set(digests)) == 1, 'Fresh reads of unchanged data must be stable'
profile = cProfile.Profile()
profile.runcall(_fingerprint, target)
profile.dump_stats(str(OUT / 'fingerprint.pstats'))
stream = io.StringIO()
pstats.Stats(profile, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(30)
(OUT / 'profile.txt').write_text(stream.getvalue())
report = {'binary_path': bpy.app.binary_path, 'version': bpy.app.version_string,
          'opensubdiv': bool(bpy.app.build_options.opensubdiv), 'paid_requests': 0,
          'method': 'serial; one warmup + five fresh fingerprint samples; cProfile separate',
          'vertices': len(mesh.vertices), 'edges': len(mesh.edges), 'faces': len(mesh.polygons),
          'attributes': [{'name': attribute.name, 'type': attribute.data_type,
                          'domain': attribute.domain, 'count': len(attribute.data)} for attribute in mesh.attributes],
          'samples_ms': [round(value, 3) for value in samples], 'median_ms': round(statistics.median(samples), 3),
          'coordinate_validation': coordinate_result,
          'fresh_digest_stable': True, 'success': True}
(OUT / 'results.json').write_text(json.dumps(report, indent=2, allow_nan=False))
print('ANGELO_EXACT_FINGERPRINT_PROFILE', json.dumps(report), flush=True)
print(stream.getvalue(), flush=True)
print(coordinate_stream.getvalue(), flush=True)
