.. SPDX-FileCopyrightText: 2026 Angelo Contributors
.. SPDX-License-Identifier: GPL-2.0-or-later

######################
GPU subdivision status
######################

The Subdivision Surface modifier says why the viewport subdivides it on the CPU
(`#58191 <https://projects.blender.org/blender/blender/issues/58191>`_).

GPU subdivision runs only when the GPU Subdivision preference is on, the GPU supports it, the modifier
is the last one enabled, and its input has no split normals. Shade Auto Smooth puts Smooth by Angle
after Subdivision, so the most common hard-surface setup always subdivides on the CPU, and Blender's
panel only explained sharp edges, read from the original mesh rather than the modifier's input.

Contract:

* ``BKE_subsurf_modifier_gpu_status(smd, mesh, is_last_enabled)`` (``blenkernel``) returns ``GPU``
  or the first reason in this order: ``PREFERENCE_OFF``, ``NO_GPU`` (no GPU back-end, as in
  background mode), ``UNSUPPORTED_GPU``, ``NOT_LAST``, ``SPLIT_NORMALS``.
  ``BKE_subsurf_modifier_can_do_gpu_subdiv`` is that function with ``is_last_enabled`` true, so the
  decision the draw code uses is unchanged.
* The modifier records it when it is evaluated for the viewport or render, never while being
  applied, in ``SubsurfModifierData.gpu_subdivision_status``: runtime data (stock Blender ignores the
  field), reset to ``NOT_EVALUATED`` when a file is read.
* The active depsgraph copies it to the original modifier with the modifier errors
  (``BKE_object_sync_to_original``); a modifier the viewport skips (disabled, level 0, hidden in Edit
  Mode) reads ``NOT_EVALUATED``; Edit Mode is read from the mesh, as the modifier reads it. Python
  reads ``SubsurfModifier.gpu_subdivision_status`` (read-only, not animatable, not overridable). A
  hidden object keeps the status of its last evaluation. Changing the GPU Subdivision preference
  re-evaluates every object with a Subdivision modifier, so every status follows it.
* Evaluation rewrites the status, so typed programs do not treat it as a dependency: it is in
  ``rna_fields._RNA_SKIP`` beside a point cache's ``info``. Fingerprinted, an evaluation between
  staging and commit would refuse every commit on the object as ``STALE_DEPENDENCY``.
* The panel names the enabled modifier that follows (``NOT_LAST``), says sharp edges or custom normals
  were detected (Blender's own message, now from the modifier's input), or says the GPU lacks support.
  The preference and background mode are the person's own choices and stay quiet. Every status wraps to
  the panel's width (``draw_status_label``): a clipped label loses its middle, which names the modifier.

Upstream
========

As of 2026-11-03, #58191 is open. Blender 5.3 and upstream ``main`` (the 5.4 alpha) still show only
Blender's two labels: the sharp-edge one, read from the original mesh, and "Using both CPU and GPU
subdivision". 5.3 changed those labels to ``ICON_STATUS_INFO`` (``0e5263ff5ba``) and the second to
``label_multiline`` (``e02e46e78f7``); neither exists in 5.2, so the 5.3 port of this change uses
them for its labels too and drops ``draw_status_label``. ``SubsurfModifierData`` is unchanged in 5.3
and ``main``.

Validation
==========

* Host: ``test_native_gpu_subdivision_status.py`` (contracts read from the sources; every new string
  in Angelo's template and its 20 catalogues).
* Background: ``test_blender_gpu_subdivision_status.py`` (needs a GPU; every status on cube,
  triangle and n-gon meshes, the copy to the original, the reset on read, a program fingerprint
  that status changes leave alone); ``test_blender_subdivision_levels.py`` commits typed programs
  on a Subdivision stack.
* GUI: ``test_blender_gpu_subdivision_status_gui.py`` (the panel in three states, screenshots checked
  by eye).
* Also run ``test_blender_subdiv_uploads.py``, which covers GPU subdivision's drawing.

How to continue
===============

Making GPU subdivision work with sharp edges, or with normal-only modifiers after it, is a separate,
larger change; so is evaluating OpenSubdiv stencils on several threads (verified bit-exact against
the serial path), which is where triangle and n-gon meshes spend their time on the CPU path.
