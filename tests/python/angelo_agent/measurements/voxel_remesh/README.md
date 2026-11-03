<!-- SPDX-FileCopyrightText: 2026 Angelo Contributors -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Voxel remesh measurement

The data behind the constants in `source/blender/blenkernel/intern/mesh_remesh_voxel_estimate.cc` and the
table in `doc/angelo/voxel_remesh_guard.rst`.

* `measure_one.py`: one remesh in a background Blender; prints the estimate, the real output, the time and the
  peak memory growth (resident and macOS physical footprint) as one JSON line.
* `run.zsh`: runs specs one process each, niced, and stops when free memory drops below half.
* `results.jsonl`: the 127 runs of 2026-10-24 on an M5 Max (128 GB), Blender 5.2.2's remesh kernel. Times are
  contended. The estimate column (`target`) is the closed-surface estimate; the guard doubles it for open shapes
  (`grid`, `big_terrain`, `monkey`).

Re-measure when the remesh kernel, OpenVDB or the Mesh layout changes, and keep the model above every run.
