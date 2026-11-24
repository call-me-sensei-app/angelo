.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

=======================
The device budget check
=======================

What a written ``.glb`` (or ``.vrm``, which is a ``.glb``) costs on a target device, compared with that device's
profile: triangles, vertices, draw calls, GPU texture memory as the device stores it, bones, skin influences, morph
targets, the file's size, and the size of every file that ships against an app-size budget. Owner:
``scripts/addons_core/angelo_agent/device_budget.py`` (no Blender), built on ``web_budget.py``
(:doc:`web_export`). Angelo 2.1.0.

.. contents::
   :local:

Profiles are data
=================

A profile is a JSON file. Angelo ships a few in ``angelo_agent/device_profiles/``; a team adds its own (its own
numbers, its own devices) and a later folder replaces a shipped profile with the same ``id``. Nothing about a
particular team or project is in Angelo's code.

==========================  =====================================================================================
Field                       Meaning
==========================  =====================================================================================
``schema``                  ``1``.
``id``, ``name``            The profile's key and the name people see.
``fps``                     The frame rate the budgets are for.
``basis``                   Where the numbers come from (a vendor table, a team's measurement on a device...).
``texture_formats``         How the device stores each texture role: ``default`` plus any of ``base_color``,
                            ``metallic_roughness``, ``normal``, ``occlusion``, ``emissive``, ``other``. Formats:
                            ``ASTC_4x4`` to ``ASTC_12x12``, ``ETC2_RGB``, ``ETC2_RGBA``, ``BC1``, ``BC3``,
                            ``BC7``, ``RGBA8``. A texture used in two roles is counted in the costlier format.
``passes_per_primitive``    How many times the engine draws each primitive (an outline or shadow-caster pass
                            draws it again); default 1.
``max_texture_size``        Optional: textures larger on either side are listed and fail the check.
``budgets``                 Any of ``triangles``, ``vertices``, ``draw_calls``, ``texture_memory_mb``,
                            ``bones`` (largest skin), ``influences`` (per vertex), ``morph_targets`` (largest
                            mesh), ``file_mb`` and ``app_mb``. ``null`` or absent: measured, no verdict.
==========================  =====================================================================================

The shipped profiles are common device classes, not any team's targets: iOS phones at 30 and 60 fps, a mid-range
Android phone (OpenGL ES 3.2 or Vulkan) and an entry-level one (OpenGL ES 3.0, ETC2), an iPad, a standalone headset at
72 fps and a desktop GPU (BC7). Each states only hardware facts with their source and leaves every budget unset:
there are no vendor-published per-character budgets, and a made-up default would read as one. A team copies a
class, gives it its own id and fills in its own numbers (its triangle and texture limits, its frame rate, its
app-size limit).

What is measured
================

* **Triangles, vertices, draw calls**: per mesh instance in the default scene (``web_budget``); a GPU-instanced
  node draws once; draw calls are multiplied by ``passes_per_primitive``.
* **GPU texture memory**: each image's pixel size in its role's format, whole blocks per level (a block format pays
  at least one block for each small mip), with the full mip chain. It is the memory the device holds, not the
  file's bytes. The largest five textures are listed with their role and format.
* **Bones**: joints of the largest skin. **Influences**: 4 per ``JOINTS_n`` set on any primitive (an engine that
  keeps 4 drops the rest). **Morph targets**: the most on one mesh, plus the total and the vertex count they move.
* **App size**: the bytes of the files given, by name.

The verdict
===========

Every set budget gives a row with ``value``, ``budget``, ``ratio`` and ``passed``. The check passes only when at
least one budget is set (or ``max_texture_size``) and nothing fails; with none set, ``passed`` is ``null``, never a
pass. Results name files and images, never local paths.
