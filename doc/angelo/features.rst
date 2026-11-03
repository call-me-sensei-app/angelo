.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

########################################
Angelo's features and where to find them
########################################

This page is for people who use Angelo. It says what each major feature does and how to reach it. Almost
everything starts from the **+ button in the chat bar**, Angelo's main menu, in Director and in Manual mode alike.
Video and rendering tools sit in two rows of their own there, **Video** and **Render**, each opening to its tools.
You can also ask the agent: "how do I link Unity?" or "can you turn on the engine link?" get an answer, and, where a
feature can be switched on for you, a card to approve.

Each section names the developer contract that describes the feature in depth.

.. contents::
   :local:

Game engine live link (Godot, Unity, Unreal Engine)
===================================================

Edit a game engine's models in Angelo. In the engine, choose **Edit in Angelo** on a model: it opens here. Godot
and Unity show every change live in their editor while you work; saving in Angelo updates the model in the engine.
Unreal Engine re-imports the model when you save (its live preview needs the separate Angelo Link Live source
plugin). One link serves all three engines.

**Set it up once per project:**

1. Chat bar **+** > **Share** > **Game engine live link** > **Add Angelo Link to a Godot / Unity / Unreal Engine
   project…** (also in **File > Export**). Choose the project's folder. Angelo copies its plugin into the project and
   turns the link on.
2. Finish in the engine: in **Godot**, enable *Angelo Link* in Project Settings > Plugins; **Unity** loads it by
   itself; in **Unreal Engine**, enable *Angelo Link* in Edit > Plugins and restart the editor.

**Every day:** the link stays on across launches until you turn it off (+ > Game engine live link > **Turn off
the live link**). The status bar, or Director's top bar, shows which model is linked and whether the engine is
connected.

**Angelo notices your engine.** When Godot, Unity or the Unreal Editor is open on your computer, Angelo shows a
small card once per engine offering to link it: **Link a project…** sets it up, **Not now** dismisses it for good.
Nothing connects unless you click. Angelo only checks which of these three editors is running; it never sends
your list of programs or any file path anywhere.

Contracts: :doc:`engine_link`, :doc:`godot_link`, :doc:`unity_link`, :doc:`unreal_link`.

Game physics for game engines
=============================

Mark what an object is to a game engine: **Properties > Physics > Game Physics**, or chat bar **+** > **Game engine
live link** > **Game physics for the selected object…**. Choose a **Body** (Static, Rigid with its mass, Kinematic
or Trigger) and a **Shape** (box, sphere, capsule or cylinder fitted to the mesh, or sized by hand; convex hull; or
the exact mesh). **Collision Only** exports a mesh only as its collision shape. Every glTF export writes this as
standard OMI physics, which Godot 4.4 and newer import as physics bodies and collision shapes, so you no longer
need name suffixes such as -col or -rigid. The panel warns about setups an engine would import differently, such
as a shape animated inside its body.

Contract: :doc:`game_physics`.

Media Studio: AI images and 3D models
=====================================

Make images and 3D models from a description or reference pictures; results stay in your Media Studio library and
import into the scene with one click. 3D models come from Tripo, Meshy or **TRELLIS 2**: TRELLIS works from pictures
only (one image, or several views of one object) and appears in the 3D tab once a fal key is connected (**AI
Providers** > **Media generation** > **fal**).

* Chat bar **+** > **Create** > **Media Studio**, or **Workflows** for saved multi-step workflows.
* The **Studio** button beside Chat in the top bar.
* Or ask the agent: it can generate and import for you, and shows the price on the card before anything is paid.

Video generation
================

Make a short video clip from a prompt, a still, a first and last frame, or a reference video. The models come
through **fal** (one key, many video models) or, for **Seedance**, through your **BytePlus ModelArk** key at about half
fal's price; every model picker shows its provider and the price per second.

* Chat bar **+** > **Create** > **Video** > **Video** (Media Studio's Video tab).
* Connect a key first: **AI Providers** > **Media generation** > **fal**, or **AI Providers** > **BytePlus
  ModelArk** for Seedance.

Contracts: :doc:`generative_studio`, :doc:`generation_flows`.

Generation Flows
================

Chain generation steps (an image, a 3D model, an upscale...) into a node-tree workflow that runs with one approval,
and reuse it: chat bar **+** > **Create** > **Generation Flows** offers New, the templates and your saved flows.

Contract: :doc:`generation_flows`.

Base meshes and hands
=====================

Start from one of Angelo's base meshes (chat bar **+** > **Start from** > **Base meshes**; it is added when you send
your message), or choose **Work on hands** so the agent starts from your rig's hands with Angelo's hand tools.

Contract: :doc:`hand_rigging`.

Photo to 3D model
=================

Turn a photo into a 3D model placed in your scene.

* Drag a photo from Finder or Explorer onto the 3D view and choose **Make a 3D model** on the card that opens
  (it also offers using the photo as a chat reference, a material or a picture).
* Or chat bar **+** > **Create** > **Photo to 3D model…**; the model is placed at the 3D cursor.

Contract: :doc:`photo_drop`.

Render engines and AI render
============================

Choose how F12, Render Animation and render jobs draw your scene: **EEVEE** (fast), **Cycles Preview** (quick
path-traced check) or **Cycles Final** (full quality); Cycles uses your GPU when it can.

* Director's top bar: the engine button.
* Chat bar **+** > **Create** > **Render Engine**.
* **Render** > **Render Engine**.

Render engine add-ons installed in Blender, such as **V-Ray**, appear in the same list with "Installed, not on in
Angelo yet": choose one to set it up, sign-in included, without leaving Angelo.

**AI render** restyles the scene camera's view into a finished picture in one priced click: chat bar **+** >
**Create** > **Render** > **AI render**, or **Render** > **AI Render…**.

Contract: :doc:`render_engines`.

Camera to video
===============

Make a video from your scene camera: **Make Video from This Shot** (an AI video from rendered frames) or **Video
from This Camera Move**; renders can also be written straight to MP4.

* Chat bar **+** > **Create** > **Video** > **Make Video from This Shot** or **Video from This Camera Move**.
* The **Render** menu, or **View** > **Cameras** in the 3D view.

Contract: :doc:`long_jobs`.

Angelo Library
==============

Ready-made models, materials and skies (HDRIs) to search and add to your scene.

* The search pill at the top centre of the 3D view, in Object Mode.
* Chat bar **+** > **Create** > **Angelo Library**.
* **Browse Library Materials** in the Material properties and **Browse Library HDRIs** in the World properties.

Bake every map at once
======================

Bake all of a material's maps (colour, roughness, metallic, normal...) in one job, as separate images or packed for
glTF, Unreal Engine or Unity: chat bar **+** > **Create** > **Render** > **Bake maps…**, or F3.

Contract: :doc:`long_jobs`.

Render a region over the last render
====================================

Re-render only part of the picture over your last finished render, to check a small change without waiting for a
whole frame: chat bar **+** > **Create** > **Render** > **Render region**, **Render** > **Render Region Over Last
Render**, or the region pills in the render window.

Select Through
==============

Box, lasso and circle selection reach hidden elements too, as in X-ray, while the view stays solid (mesh Edit
Mode, paint masks and Particle Edit).

* The 3D view's header, beside X-ray; in Director, the top bar while you are in Edit Mode.
* The shading pie (Z).
* Chat bar **+** > **Inspect** > **Select Through**.

Contract: :doc:`select_through`.

Tension maps
============

See where a deforming mesh stretches (red) or squashes (blue), live in the viewport, and use it in materials: chat
bar **+** > **Inspect** > **Tension map**, or **Add Modifier** > **Edit** > **Tension**.

UV checker
==========

Show a checker pattern on your models to spot UV stretching and uneven texel density: chat bar **+** > **Check** >
**UV checker**, Director's top bar, or **View** > **Toggle UV Checker**.

Rig check for a game engine
===========================

See whether a rigged character fits Unreal Engine, Unity or Godot before you export it: chat bar **+** > **Inspect**
> **Check a rig for a game engine**, or F3 > **Check Rig for Game Engine**. The card says, for the engine you choose,
whether its bone names and hierarchy match (and for Unreal, whether its IK Retargeter would set itself up), and checks
the root bone, rest pose, axes, transform, constraints and weights, each with its fix. The agent runs the same check
(``engine_skeleton.rst``).

Web export and live browser preview
===================================

A web-ready .glb for Three.js with its size budget, and a browser preview that updates after every change: chat bar
**+** > **Share** > **Web export** or **Live browser preview**, and **File > Export**.

Contract: :doc:`web_export`.

Send feedback
=============

Found a bug, or want Angelo to do something it can't yet? Chat bar **+** > **Feedback** > **Report a bug…** or
**Request a feature…**, or **Help** > **Report a Bug** / **Request a Feature**. Angelo opens the feedback form on
angelo.studio with your Angelo version, build and system (and, for a bug, your graphics card) filled in. You see and
can change every detail before you send it, and nothing is sent until you do. The form never includes your scene,
your files or their paths. Your email is optional and only used to reply.

Contract: :doc:`app_identity` ("Links").

How this page stays complete
============================

The + menu is one registry (``chat_destinations``), and the agent's view of these features is one list
(``feature_guide``; the ``FEATURES`` family of ``angelo_agent_capabilities``). ``test_native_feature_guide`` checks
that every feature the agent knows is reachable from the + menu and has a section here, so a new feature adds its
menu row, its ``feature_guide`` entry and its section together.
