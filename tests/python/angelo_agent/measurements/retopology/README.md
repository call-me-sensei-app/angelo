<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Retopology benchmark

Angelo's Retopology against QuadriFlow and voxel remesh on the same inputs, face targets, machine and grading
(`doc/angelo/retopology.rst`, "Measurement").

* `bench_retopology.py`: one method on one asset at one face target in a background Angelo; appends one JSON line
  (refusals and errors included). Its docstring states every setting each method gets and how all are graded.
* The mesh sets (dev and held-out, with SHA-256 and licences) are listed outside the repository; no asset is
  committed here.

Run one process per (asset, method, target) on a quiet machine, niced, under a per-run time cap; a run that appends
nothing crashed and is counted as a failure of that method.
