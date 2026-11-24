.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Lens and film look
==================

A camera's look in two parts, both Blender's own: the camera's settings (bokeh shape, focus breathing, f-stop) and
one compositor node group whose stages run in physical order. Both work with every render engine (Cycles and EEVEE)
on every GPU, and the node group runs live in the viewport compositor, so the look shows in the 3D view's camera and
the camera preview while it is set. Cycles' Custom (OSL) camera is not used for it: that camera renders only on the
CPU or with OptiX (no Metal, HIP, oneAPI or EEVEE), and NVIDIA drivers 590 to 609 fail with OSL on OptiX.

Status (2.1.0, in progress): the models, stages and presets (``look_spec``), the node group builder
(``look_nodes``), applying and clearing a look (``look_system``), the operator and its row in the + menu's Render group
(``look_ui``), the Camera Look card (``look_card``) and the agent's declaration (``look_ops``) are implemented and
proven in the app (``test_blender_look``, and in Director Mode ``test_blender_look_card``). Not built yet: the agent
operation's wiring (it waits for the shared ``wrapped_ops`` code).

Stages
------

``look_spec.ORDER``; each stage's values and ranges are ``look_spec.STAGES``. A stage a look leaves out is not built.

lens
  Barrel or pincushion distortion and lateral chromatic aberration (the Lens Distortion node's distortion and
  dispersion).
vignette
  Natural vignetting: the light reaching each point relative to the centre is cos^4 of its angle from the optical
  axis, computed from the camera's focal length and sensor (``natural_vignette``), so a wide lens darkens its corners
  more than a long one; ``amount`` blends it, ``mechanical`` adds the lens barrel's cut-off.
glare
  Bloom, streaks (the horizontal flare of anamorphic lenses) and ghosts, above a threshold (the Glare node).
halation
  Highlights scattered back through the film base: blurred, warm, added back.
film
  Exposure, white balance (colour management's own temperature and tint), contrast, saturation and faded blacks.
grain
  Luminance noise sized as a share of the frame's height (``grain_pixels``), so a 4K render has the same grain as an
  HD one.

Camera
------

* Bokeh: ``aperture_blades``, ``aperture_rotation`` and ``aperture_ratio`` (above 1: the oval bokeh of anamorphic
  lenses); Cycles and EEVEE both read them.
* Focus breathing: the focal length follows the focus distance, ``f * (1 + k * f / (d - f))`` (thin-lens
  magnification times the lens's coefficient ``k``; ``breathing_lens``). It is a driver whose expression Blender
  evaluates without Python (``breathing_expression``: only arithmetic and ``clamp``), so it works with automatic
  script execution off; the focus distance is read in scene units.
* Exposure in stops (``exposure``; off unless asked): the film stage's exposure moves by the stops between the
  settings the scene's light is right for (``reference``: f-stop, shutter in seconds, ISO) and the camera's
  (``setting``: shutter and ISO, with the camera's own f-stop unless it names one, so exposure agrees with the depth
  of field), on top of the look's own film exposure (``look_spec.exposure_stops``). One stop is twice the light: f/4
  against a reference of f/8 at the same shutter and ISO is +2. A shift that would take the film's exposure past its
  range is refused before anything changes. The receipt and the scene's look state carry ``exposure_stops``. Blender
  lights scenes in its own units, so the reference is the person's or agent's statement of what the light was set
  for, not a measured value; the scene's lights are never changed.

Applying a look
---------------

``angelo.camera_look`` (chat bar **+** > **Render** > **Camera Look**, or F3, opens `The card`_) builds a new ``Angelo Look`` group
for the preset and its overrides and puts its node before the scene's compositing output, after whatever the person
built there (a scene without a compositor gets Render Layers to the output first). Reapplying replaces that one node
and drops the earlier group if nothing else uses it. The group's Focal Length, Sensor Width and Frame inputs are
driven from the camera and the scene by simple expressions (no Python), so the vignette follows a lens change and the
grain moves every frame. The camera gets the preset's bokeh, starting from the person's each time; white balance is
set through colour management. What the look replaced is kept on the scene, and **Clean** with nothing else takes the
look off and puts the bokeh, focal length, white balance and compositor back.

Focus breathing is opt-in (``breathing``): while it is on, the focal length is a driver's, so Director's and the
person's own focal-length edits wait until the look is taken off; it is refused on a camera whose lens settings are
animated, since the driver would override them. With a focus object the driver reads the distance to it.

The operator's redo panel offers the preset, breathing, showing the look in the viewport's camera view (the viewport
compositor; a view setting, off for the agent unless asked) and **Match Exposure** (the camera's shutter and ISO and
the **Lit For** f-stop, shutter and ISO; the camera's own f-stop). The agent gives the same as ``exposure`` JSON.

The card
--------

``look_card`` (``card_host``), centred over the 3D view, on the scene's camera (or the operator's ``camera``):

* **Look**: the presets but Clean, starting from the look already on the scene.
* **Focus breathing**, offered only where the look's lens breathes (its preset's coefficient).
* **Show in the camera view** (on by default for a person): the 3D views' camera view runs the compositor.
* **Match exposure**: the light is taken as right for the camera's f-stop when the card opened, at 1/50 s and ISO
  100; the shutter (1/25 to 1/500) and ISO (100 to 3200) chosen on the card move the film's exposure by the stops
  between, shown as they change.
* **Apply** and, with a look on, **Take Off** each run ``look_system.apply`` and push one Undo step; the card then
  moves to the corner with the look and its stages, or keeps the refusal, in the look's own words. The agent's operation (``look_ops``) is scene-level with the camera
as a write handle; it mutates the camera, the scene, the compositing group and their drivers, and adds one node group.
Its checks: one look node (none when cleared), the compositing output reads the look, and every driver is a simple
expression.

Presets
-------

``look_spec.PRESETS``: ``CLEAN`` (nothing), ``VINTAGE``, ``ANAMORPHIC``, ``DOCUMENTARY``, ``WARM_PORTRAIT``,
``BLEACH_BYPASS``, ``NOIR``. Named by the look, never by a product, lens or film stock. ``resolve(preset, overrides)``
fills each stage's defaults and checks overrides against the ranges.

Validation
----------

``test_native_look_spec``: vignetting in closed form (full-frame 50 mm corner), breathing at infinity and close, the
driver expression limited to Blender's simple-expression functions and equal to the formula in scene units, stops,
exposure in stops from the camera's own or a named f-stop and its refusals, grain scaling, every preset within the stage ranges, overrides and refusals.

``test_native_look_ops``: the declaration is plain data, its schema valid with defaults that validate, its presets
and stages ``look_spec``'s, every preset fits it, every parameter an operator property with the same default,
revision 4's fields, the ``exposure`` JSON parameter (its schema takes what ``look_spec`` takes and refuses the rest),
and the checks and receipt (``exposure_stops`` when asked).

``test_blender_look`` (background, Cycles CPU): the rendered vignette's corner-to-centre ratio equals cos^4 for an
18 mm and a 50 mm lens; NOIR has no colour; grain's spread grows with its amount and the grain moves between frames;
the look sits after the person's own compositor step and before the output; reapplying keeps one look and drops the
old group; every driver needs no Python; the preset's bokeh; breathing follows the formula, Clean restores the focal
length, and breathing is refused on an animated lens; Warm Portrait's white balance and Clean restoring bokeh and
white balance; a two-stop exposure renders four times brighter, adds to the look's own film exposure and is
refused past the film's range without a change; ``angelo.camera_look`` with exposure as JSON and as the dialog's
fields, and its refusals.

``test_native_look_card``: the card's layouts (presets, breathing only where the lens breathes, Match Exposure's
controls and stops, Take Off only with a look on), its request, the result, and layouts that fit at several region
sizes and scales.

``test_blender_look_card`` (GUI, Director Mode, EEVEE Material Preview in the camera view): the + menu row opens the
card on the scene's camera; Anamorphic two stops over applies one look node, the oval bokeh, film exposure +2 and
the camera view's compositor; the camera view changes; the card opened again offers Take Off, which puts everything
back; one Undo brings the look back. Screenshots at each step.
