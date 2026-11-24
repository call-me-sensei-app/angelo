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

**Your own Three.js page.** The same link shows Angelo's edits in a web page you build with Three.js and Vite,
with your lights, shaders and game state left alone. **+** > **Game engine live link** > **Add Angelo Link to a
Three.js project…**, choose the folder with ``package.json``: Angelo puts ``npm i -D @call-me-sensei/angelo`` on the
clipboard. Run it, add ``angeloLive()`` from ``@call-me-sensei/angelo/vite`` to the Vite config's plugins, and call
``live.track(gltf, url)`` on the models your page loads. Alt-click a model in the page to edit it here; saving
writes the ``.glb`` your page loads.

Contracts: :doc:`engine_link`, :doc:`godot_link`, :doc:`unity_link`, :doc:`unreal_link`, :doc:`web_link`.

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

Goal mode
=========

Ask Angelo to keep going until the work is done: chat bar **+** > **Goal** (beside **Attach**) adds a goal chip to
your message, or just say so ("don't stop until it matches the picture"). After every turn Angelo measures the goal
(how closely the outline matches an attached picture from one camera, a set of scene checks, or the agent's own
judgment) and continues on its own until it passes, stops improving, or its working time or spend runs out. The chip
shows the score, the round and the time used; click it to set the threshold and budget or to see every round. Stop
goal on the chip, or the chat's Stop, ends it; sending your own message pauses it until you press Resume.

Contract: :doc:`goal_mode`.

Plan mode
=========

Talk it through before anything is built: chat bar **+** > **Plan first** (beside **Attach** and **Goal**), or just
say "let's plan first". The chat becomes read only, so nothing in the scene changes, while the agent researches on the
web, looks at the scene and asks what it needs. It ends with a plan card: up to three approaches, each with its steps,
risks and an estimate, the one it recommends and its open questions. **Edit** puts an approach in the message box to
change; **Go with this** switches the chat to the setting shown under **Then** (Ask before changes, or Auto) and sends
that approach, with anything you typed, as your next message. Keep talking to revise the plan; afterwards the **Plan**
chip keeps the chosen approach in view and **Revise plan** starts planning again.

Contract: :doc:`plan_mode`.

Base meshes and hands
=====================

Start from one of Angelo's base meshes (chat bar **+** > **Start from** > **Base meshes**; it is added when you send
your message), or choose **Work on hands** (chat bar **+** > **Rig** > **Work on hands**) so the agent starts from your
rig's hands with Angelo's hand tools.

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

Files from the web
==================

Give the agent a link to a texture, model, HDRI, image or sound and it brings the file into your project's
``assets/downloads`` folder (a temporary folder while the file is unsaved). A card first names the website, the size limit and the folder; nothing downloads until
you approve. The file's type is checked, and it is saved without being opened, run or imported.

* Ask in the chat, with the link.
* For free ready-made assets, chat bar **+** > **Create** > **Angelo Library**.

Contract: :doc:`downloads`.

Bake every map at once
======================

Bake all of a material's maps (colour, roughness, metallic, normal...) in one job, as separate images or packed for
glTF, Unreal Engine or Unity: chat bar **+** > **Create** > **Render** > **Bake maps…**, or F3.

Contract: :doc:`long_jobs`.

Mark up a picture
=================

Show the agent what you mean on a picture: click a picture in the chat's message box, or chat bar **+** > **Mark up
a picture…** (the newest picture, or the 3D view as it is now). Draw with a pen, arrows, ellipses, rectangles and
text in six colours, pin numbered comments on a point or a region, add instructions and send. The agent gets the
marked picture, the original and every comment with its place. The same bar removes a background or erases what you
brush over (an image generation: it shows the cost first and keeps the original) and crops or scales the picture.

Contract: :doc:`attachment_markup`.

Retopology
==========

Turn a scanned, sculpted or AI-generated mesh into clean quad topology that follows its shape and its sharp and
marked edges, as a new object: chat bar **+** > **Model** > **Retopology**, **Properties** > **Mesh** > **Remesh** >
**Retopology**, or F3. It works on broken meshes (holes, loose parts, flipped faces, triangle soups), keeps vertex
groups, and can make the result exactly symmetric. Ask the agent ("retopologize this at about 8k faces") and it runs
the same tool as a background job.

Contract: :doc:`retopology`.

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

Scatter
=======

Place trees, rocks or any objects from your scene on a surface by slope, height, a painted mask and distance from
paths or buildings, in layers that keep out of each other's way: select the objects, then the surface, and choose
chat bar **+** > **Create** > **Scatter** (or **F3** > **Scatter**). The result is a new object with plain Geometry
Nodes you can open and edit; density, seed, camera culling and viewport points are its modifier's inputs.
Contract: :doc:`scatter`.

Camera Look
===========

Give the camera a lens and film look (vignette, glare, halation, a film grade, grain and bokeh shape) from a
preset: chat bar **+** > **Render tools** > **Camera Look** (or **F3** > **Camera Look**). It works with every
render engine and shows in the viewport's camera view; **Clean** takes it off and puts back what it replaced.
Contract: :doc:`camera_look`.

Tension maps
============

See where a deforming mesh stretches (red) or squashes (blue), live in the viewport, and use it in materials: chat
bar **+** > **Inspect** > **Tension map**, or **Add Modifier** > **Edit** > **Tension**.

Loops (LoopTools tools)
=======================

Blender's built-in versions of the LoopTools tools, together and under the names LoopTools used: **Bridge**,
**Circle**, **Curve**, **Flatten**, **Gstretch to Annotation**, **Relax** and **Space**. In mesh Edit Mode:

* right-click: **Loops**, at the top of the menu, where LoopTools put its own;
* **Edge** > **Loops**, so F3 search finds each tool by its LoopTools name ("Relax", "Gstretch").

Each item runs Blender's own operator, the same one **Mesh** > **Transform** offers under its longer name. LoopTools'
Loft has no built-in equivalent; Bridge is the closest. Source: ``angelo_agent/loops_menu.py``.

Splats to meshes
================

Turn Gaussian splats (imported with **File** > **Import** > **PLY**) into meshes you can use: select the splats and
run **Splats to Mesh** (chat bar **+** > **Create**, or F3). **Collision** makes a closed, light mesh within a triangle budget for physics and game
engines; **Visual** makes a finer mesh coloured from the splats. Faint splats floating off the captured surface are
left out, judged against the capture's own density. The splats themselves are not changed. **Export Splats (.ply)**
writes splats back in the format other splat tools read. Source: ``angelo_agent/splat_mesh.py``.

A collision mesh always fits its triangle budget, stays closed and never passes through itself
(``angelo_agent/splat_budget.py``). Walls and other features at least about the splats' spacing across are kept: a
closed room keeps every wall wherever the voxel grid falls, and a result that has lost part of the capture is refused
(each splat must lie within its own radius plus half a voxel of the surface, judged against the finest surface,
which keeps everything). When thinning it would make a thin wall's two sides cross, or lose part of the capture, the
mesh is rebuilt a little coarser instead, then refined toward finer voxels only while that brings it measurably closer
to the splats, and no closer than the splats' own size. If a result were ever over the budget, or missing part of the
splats, the operator says so.

Thin walls sit about a splat radius off on both sides, so a sparse capture of a large space fits looser than a dense
one (a 20 m floor plan from 60,000 splats has splats about 0.3 m across), and a large flat space at the default
budget sits further off than with more triangles. For tighter collision, raise the triangle budget or capture more
densely: a 20 m floor plan of six rooms with 15 cm interior walls, from a million splats, stays well within 2% of its
size at about 18,000 triangles.

On a test machine a million splats take about 20 s (an object shaped like a sphere, or a 20 m floor plan, 5,000
triangles) and about 1.5 GB of memory; 60,000 splats take a few seconds. A later release narrows the costly part to the
thin or enclosed places that need it. A Visual mesh of a million splats takes about 4 s and 0.9 GB, and has about
1.3 million triangles.

Collision hulls for game engines
================================

Give a mesh collision a game engine reads: chat bar **+** > **Share** > **Collision hulls** (F3: **Fit Collision
Hulls**). A concave mesh gets several convex hulls, so a doorway stays open and a bowl stays hollow; each engine's
export writes them its way (Unreal ``UCX_``, Unity MeshCollider, Godot OMI or name suffixes).

Contract: :doc:`game_physics`.

UV checker
==========

Show a checker pattern on your models to spot UV stretching and uneven texel density: chat bar **+** > **Inspect** >
**UV checker**, Director's top bar, or **View** > **Toggle UV Checker**.

Shape Cut
=========

Draw on a surface to cut it: chat bar **+** > **Model** > **Shape Cut**, or F3 > **Shape Cut**. Hover a face, drag a
box or circle (click corners for a polygon, or hold and draw freehand), then move the mouse to set the depth and click.
The shape snaps to a grid lined up with the face's edge. Keys 1 to 6 choose Cut, Union, Intersect, Slice, Inset or
Panel; T cuts all the way through. Every cut stays live: select its wire cutter to move or scale it, or delete the
cutter to undo the cut. Ask the agent for the same thing in words, such as "cut a 2 cm vent grid into this panel".

UV Layout
=========

Unwrap and pack the selected meshes for one texture: chat bar **+** > **Model** > **UV Layout**, or F3 > **UV
Layout**. Choose the texture size, an optional texel density in pixels per metre (extra UDIM tiles are added when the
parts do not fit one), how many mip levels the padding keeps clean, whether to unwrap first, stack identical parts,
give each material or object its own tile, and how many extra layouts to search. Pack Islands has the same **Search**
setting. Ask the agent in words, such as "unwrap and pack this for 2K at 10 px/cm".

Rigging: retarget, game rigs and IK
===================================

Chat bar **+** > **Rig** holds the rigging tools, beside **Work on hands**. Each works on any humanoid armature by
the role each bone plays, whatever its bones are called: **Retarget animation** copies the other selected rig's
animation onto the active one; **Make game rig** makes a deform-only copy named for Unreal Engine, Unity or Godot,
with every action baked and one root; **Root motion** moves the hips' travel onto the root bone or keeps the clip in
place; **Export clips** writes one clip per action, the way each engine imports them; **IK controls** adds hand and
foot effectors with elbow and knee poles, and **Switch IK/FK** moves the limbs between them, keeping the pose. F3 >
**Pose Body** poses the whole body from where the hands, feet, head or hips should be, within a human range of motion
and balanced over the planted feet. The agent runs the same operators. Details: ``doc/angelo/rigging.rst``.

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

Rules
=====

Instructions Angelo follows in every request, written once: for this project (saved in the file) or for all projects
(kept on this computer). Rules that came with someone else's file wait until you review them: chat bar **+** >
**Rules…**.

Contract: :doc:`agent_chat`.

Folder as context
=================

Let Angelo read a folder of yours in one chat, such as a game project or a folder of references. It only reads it,
never changes it, and the model never sees where it is on your computer: chat bar **+** > **Folder as context…**; the
chat's card names the folder, with **Remove**.

Contract: :doc:`agent_chat`.

Learn: how to do it yourself
============================

See how to do by hand what Angelo just did, when you want to. Learn is off until you turn it on for a chat, and
until then nothing of it shows. With it on, under each step Angelo makes is the command a person would use, the keys
for it in **your** keymap, where it is in the menus, the settings Angelo used and the mode it needs. Angelo reads all
of it from the app as it is on your computer, so a key you have rebound, another keymap or another language shows as
yours. Where there is no single command for what Angelo did, the step says so.

* Chat bar **+** > **Learn** (at the bottom of the menu) > **Teach me as you work**: the steps of your latest request
  open by themselves with their how-to, and opening an earlier step shows its how-to too, in that chat. It is your
  choice per chat, and it changes nothing about how Angelo works.

A step Angelo made with a script has no card: the script is what it did.

Contract: :doc:`learn_mode`.

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
