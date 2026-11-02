.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

############################
Render engines and AI render
############################

Angelo 1.0.8 makes the render engine a first-class choice beside the scene: EEVEE for fast looks, Cycles ready on
the GPU with two presets, and AI render, which restyles the camera's view with an image model. This document is the
contract for the switcher, the Cycles presets and the AI render card. The GPU setup they rely on is "Render device"
in ``long_jobs.rst``; the flow AI render runs is a Generation Flow (``generation_flows.rst``).

.. contents::
   :local:

Surfaces
========

One list of choices (``render_engines.choices``) feeds every surface, so they never disagree:

* **Director's top bar**: an engine button before the mode menu (``render_engines.draw_top_bar``, drawn from
  ``director.draw_topbar`` in Director only; Manual keeps Properties > Render). It names the choice ("EEVEE",
  "Cycles · Final") beside the choice's glyph, is as wide as its widest label in the interface language so a switch
  never moves the bar (``top_bar_fit.fitted_row``), and gives way with Studio and Flows when the bar lacks room (icon
  only: ``top_bar_fit.compact`` counts it). A click opens Angelo's dropdown (``flow_menu``'s ``ENGINES`` kind, the
  Flows popup's look and input): a header, **EEVEE**, **Cycles · Preview**, **Cycles · Final** (each row's why line
  says where Cycles renders: the GPU and its name, or the CPU and why, worded as the job card words it), then
  **AI Render…** under its own label. The button is pressed while its own dropdown is open, never for Flows', and
  opening one dropdown replaces the other.
* **The Render menu**, in both modes: **Render Engine ▸** (the same choices, the current one marked) and
  **AI Render…** at the top, before Render Image (``render_engines.render_menu``).
* **The chat bar's + menu**: **AI render** in Create's **Render** group (``chat_destinations`` id ``ai_render``, a member of ``render_tools``).
* **F3**: Render Engine (``angelo.render_engines``) and AI Render (``angelo.ai_render``).

A choice runs ``angelo.render_engine_set`` (``engine``, ``preset``) with an Undo step and reports where Cycles will
render (a warning when that is the CPU).

Engine names
------------

An engine's name is a product name, shown exactly as the engine registers it in every language and on every surface:
the switcher's rows and button, the Render menu, the sign-in and set-up cards, and Blender's own engine dropdown
(Properties > Render, whose enum is marked ``PROP_ENUM_NO_TRANSLATE`` in ``rna_scene.cc``). Only the words around it
are translated ("Cycles · Final" keeps "Cycles" and translates "Final"). Blender's catalogue is asked first for every
message, and it gives the bare word its other senses ("Cycles" as the F-curve modifier: "Döngüler", "循環";
"Workbench" as a bench), so Angelo never looks an engine's name up: ``render_engines.engine_name`` returns the
registered ``bl_label``, or ``NAMES`` (Blender's own C++ names for EEVEE and Workbench, which have no Python class).
In a translated sentence an engine's name is a product name (``i18n.languages.ENGINE_NAMES``): the catalogue check
refuses a translation that drops it. ``test_native_engine_names`` renders the switcher in every UI locale through the
real catalogues.

Cycles ready on first use
=========================

Choosing Cycles here never renders on the CPU by accident:

* Angelo's Cycles already defaults new scenes to the GPU, and Angelo lists the GPU devices in a child process at
  every windowed start (``long_jobs.rst``, "Render device"); ``apply`` calls that setup again (a no-op once decided).
* A scene whose Cycles device is the CPU (an older file, a stock Blender file) is put on the GPU when the switcher
  moves it to Cycles from another engine and Angelo has set a GPU up (``render_device.facts`` reports one
  available). A scene already on Cycles keeps its device when only the preset changes: a CPU chosen there (memory,
  OSL) is the person's. Without a GPU the engine still changes, the rows say
  why Cycles will use the CPU, and the report is a warning. Choosing EEVEE or a preset never changes Preferences.
* Denoising runs on the GPU too (``denoising_use_gpu``); Cycles falls back to the CPU by itself where it must.

The presets (``render_engines.PRESETS``) set what a person tuning Cycles by hand would set:

==========  ============  ===================  ===============  ==============================================
Preset      Samples       Adaptive threshold   Viewport         Common to both
==========  ============  ===================  ===============  ==============================================
Preview     128           0.05                 32, denoised     adaptive sampling, OpenImageDenoise (every GPU
Final       1024          0.01                 64, denoised     backend and the CPU), no time limit
==========  ============  ===================  ===============  ==============================================

The active preset is read back from the scene's own values (``preset_of``), so a saved file shows its preset, and
values tuned by hand show as Cycles with no preset ticked: choosing Cycles then starts on Preview, while a scene
already on a preset keeps it. Nothing is stored beside the scene's own Cycles settings.

Installed engines
=================

Angelo cannot bundle a proprietary engine (it is GPL-2.0-or-later), but a person can install one as an add-on, and
Angelo treats it as a first-class choice with the same safety rules as any other add-on code:

* **The switcher lists every registered engine**: after EEVEE and Cycles come Workbench, any other engine Blender
  ships (Hydra Storm when enabled), and every engine an add-on registered (``render_engines.others``: ``bpy.types.RenderEngine`` subclasses, an add-on's own base classes
  included), each under the name its add-on gives it, never translated, with the add-on's module in its why line.
  The list is read when the dropdown opens, so enabling or disabling an add-on shows at once. Choosing one sets only
  the engine: the Cycles presets and the GPU setup are Cycles' alone. An engine is an add-on's when its module is
  outside the shipped scripts (``render_extensions.addon_engines``), the same test a render job uses.
* **The agent's ``render.engine``** (``action_properties``) accepts any registered engine: the value is checked by
  RNA when it is written, so an engine that is not registered (4.x's ``BLENDER_EEVEE_NEXT``, an uninstalled
  add-on's) is still refused. ``angelo_scene_settings`` lists the installed add-on engines as ``installed_engines``
  with a short note, **only when there are any**, so a stock install's prompt and reads are unchanged; the guidance
  sentence naming Blender's three engines is unchanged for the same reason.
* **The same approval as rendering with one.** Setting an add-on engine starts its code (its viewport render in a
  Rendered view, its update callbacks), so Auto asks before a scene program sets ``render.engine`` to one
  (``render_extensions.engine_edits``, read by ``controller._auto_bounded``; Ask already asks for every edit). Only
  ``angelo_scene_program`` carries ``property_set`` on the scene (a batch takes cubes and transforms,
  ``property_keyframe`` keys object paths), so the check reads that program's operations list once and never a
  sculpt stroke's points. ``addon_engines`` is cached while the registered engine classes are the same, so the top
  bar's draw pays a short class walk, never the file checks.
  Rendering with it then needs the render approval that already names it as extension code, and a rendered look
  with it is refused with that route (``render_extensions``, "Uses").
* Proof: ``test_native_render_engines`` (``InstalledEngineTests``), ``test_native_property_set`` and, in the app,
  ``test_blender_installed_engines`` with a stand-in engine add-on in the test folder (``installed_engine_fixture``),
  which renders through F12 like a real one.

Setting up an installed engine
------------------------------

A vendor's installer knows Blender, not Angelo: it puts its engine add-on (often a small loader pointing at the
vendor's own install folder) in Blender's add-ons folder for this version, ``~/Library/Application
Support/Blender/5.2/scripts/addons`` on macOS (``blender_folders`` names it on every platform), whether or not
Blender is installed. Angelo sets such an engine up itself, so nobody needs Blender:

* **Found, never run** (``engine_setup.found``): the switcher lists, after the engines that are on, every add-on of
  category ``Render`` that is not on yet, from Angelo's own add-ons folders and from Blender's for this version,
  under the add-on's own name with "Installed, not on in Angelo yet: choose it to set it up". Its ``bl_info`` is read
  as text (``ast.literal_eval``); nothing third-party runs before the click. Angelo's shipped add-ons are never
  listed. Blender's folder is read again when it changes, so an install shows without a restart.
* **The click is the consent** (``angelo.render_engine_setup``, which takes only a module name and finds its folder
  again: never a path from the caller): an add-on from Blender's folder is linked into Angelo's add-ons folder (a
  symbolic link; a directory junction on Windows; else a copy of a small add-on), so the vendor's updates and
  uninstaller keep applying; it is enabled and kept on in the preferences; its engine becomes the scene's. An add-on
  that registers no engine is turned off again and its link removed; one that fails to enable leaves nothing behind.
* **The vendor's sign-in, from Angelo** (``engine_status``): an engine that needs its vendor's licence gets Angelo's
  corner card (card_kit, never modal, translated). Angelo asks the vendor's **sign-in service** first, without
  importing the add-on (V-Ray: Chaos' Unified Login Agent, whose port is in ``~/.chaos/UnifiedLogin/run/port``), so
  the vendor's own error dialogs never appear: while it does not answer, the card says so with **Retry** and, where
  Angelo can, **Restart it** (macOS: the agent's launch agent), and a set-up waits there, turning nothing on, until
  it answers. Then **Starting** for ``GRACE`` seconds, **Sign in to <vendor>** while there is no licence (and **Use
  the free edition**, through the add-on's own operator and its own confirmation, never toggled back to paid), and
  **Ready** with the edition. A licence Angelo cannot read closes the card rather than guess. The switcher's row
  says the same ("sign-in service isn't answering", "Needs a <vendor> sign-in"). The service is checked, and
  restarted, on a worker thread, at most every two seconds: a menu or card draw and a click read the last answer
  and never wait on its socket or ``launchctl``; until a first answer the engine reads as starting, and a set-up
  goes straight on only when the service answered within those two seconds (else it waits in the card).
  The buttons call the add-on's **own** operators, which open the vendor's own window; Angelo never asks for or sees
  the account's credentials. ``engine_status.VENDORS`` names, per add-on module, the service check, the licence
  read, the operators and the restart; it holds V-Ray's entry only, and an engine without one is ready once on.
  The card also opens when the switcher chooses an installed engine that is not ready.
* **A Render add-on that is no engine** (a render-farm client, a preset tool) is turned off again after its click and
  not offered again until its files change.
* Isolation Angelo applies (a worker's or test's temporary folder) does not hide the vendor's service: it is found
  from the person's home folder. Automated runs never set an engine up (only a click does), and a background Angelo
  never starts an add-on's engine (Background processes).
* Proof: ``test_native_engine_setup`` and, in the app, ``test_blender_engine_setup`` (a stand-in vendor add-on in a
  private "Blender add-ons" folder, set up from Director's dropdown, rendering, and its sign-in card going from
  Sign in to Ready through the add-on's own operator).

Background processes
--------------------

A background Angelo (a parallel-edit worker, the external MCP's headless session, an offscreen-draw child) has no
window. Angelo cannot tell whether an add-on's engine works without one, and some end the whole process: V-Ray for
Blender's Community Edition exits when it starts its render server with no window, which it does as its add-on
registers, and a commercial V-Ray that cannot get its licence exits after about 120 s. So:

* **No render starts on an add-on's engine in a background Angelo** (``render_extensions.refuse_headless``): a render
  job (still or animation) and a look are refused when admitted and again when they start, telling the agent to
  render from a chat in Angelo's window. Every scene the render renders with its own engine counts
  (``render_reach.rendered_scenes``: the scene, the scenes its compositor's Render Layers and Render-source
  Cryptomatte nodes render, and its Camera-input scene strips, followed in turn); the approval's engine line reads
  the same scenes, so a look in the window never runs another scene's add-on engine without approval either.
  Blender's own engines (EEVEE, Cycles, Workbench, Hydra Storm) render there as before. The reason is one fixed
  sentence: English to the agent, and translated where a job card shows it to the person.
* **Engine add-ons are lead-only** (``angelo_lead_only_addons``, installed by the startup module
  ``angelo_lead_only.py``): in the background processes Angelo starts (a parallel-edit worker, the external MCP's
  headless session: ``--background`` with ``ANGELO_HEADLESS=1``), Blender's ``addon_utils.enable`` is wrapped before
  the first add-on loads (Blender registers startup modules first) and holds back every render engine add-on without
  importing it: a legacy add-on whose ``bl_info`` category is Render, or an extension tagged Render, outside what
  ships with Angelo. None of its code runs there, so no route (an Angelo job, an approved script calling Blender's
  render or bake operator, a direct operator call) can start its engine, and nothing of it can end the process at
  registration or later (V-Ray's Community Edition exits as it registers without a window; a paid V-Ray without its
  licence exits about two minutes later). Workers also start with factory settings, so they load no person's add-on
  at all; the headless session loads the person's other add-ons as before. A person's own ``Angelo --background``
  run is not Angelo's child and is unchanged.
* **Known gap** (no crash): where an engine add-on is not loaded, Blender reads the scene's engine as its first
  engine, so a render there of a scene set to an add-on's engine uses EEVEE without saying so. Python cannot read the
  stored engine name; closing it needs that name from the application (a read-only RNA property).

V-Ray for Blender
-----------------

Checked 2026-10-30 and 2026-10-31 against Chaos' public pages, its add-on source and an install on Mac 1 (V-Ray
7.40.02 for Blender 5.2, arm64):

* **Version**: V-Ray for Blender Update 4 says it officially supports Blender 5.2; Update 3 (2026-06-15) added 5.1.
  Its engine is ``VRAY_RENDER_RT`` ("V-Ray"). Platforms: Windows, Linux and macOS on Apple Silicon (Blender 5.0+);
  GPU rendering is CUDA/RTX and AMD HIP (Metal not confirmed).
* **Install, with no Blender**: the Chaos installer (``vray_74002_blender520_dr2_bigsur_arm64.dmg``) installs V-Ray
  under ``~/Library/Application Support/Chaos/V-Ray for Blender 5.2``, the Chaos License Server, the Unified Login
  Agent (a login agent on ``127.0.0.1``) and Chaos Cosmos, and writes a three-file loader (``__init__.py``,
  ``install_location.txt``, ``blender_version.txt``; ``bl_info`` category Render) to Blender's add-ons folder. Its own
  install script (``uninstall/blender_install.xml``) never looks for Blender: the loader's folder defaults to
  ``$HOME/Library/Application Support/Blender/5.2/scripts/addons/vray_blender`` whether Blender exists or not, and
  its Advanced page lets the person change it (Angelo's add-ons folder works too). So the person runs Chaos'
  installer as published (an administrator password for the licence components, and its own EULA), then in Angelo:
  engine button > **V-Ray For Blender** ("Installed, not on in Angelo yet") > the card's **Sign in to Chaos**. The
  installer's command line can also unpack the add-on alone (``<installer>.bin -gui=0 -unpackInstall=<folder>``),
  but that copy lacks the licence components. The add-on keeps its settings under
  ``bpy.utils.user_resource('CONFIG')/vrayblender``, Angelo's own configuration folder; nothing in it assumes the app
  is named Blender.
* **In Angelo**: it loads, registers ``VRAY_RENDER_RT``, appears in the switcher under its own name and starts its
  render server (V-Ray 7.40.05, AppSDK 5.6.4). Its licence: ``VRayBlenderLib.hasLicense()``; its edition: the
  add-on's own ``community_edition`` preference, which its licence window's "Switch to Community Edition" and its
  ``vray.switch_license_type`` operator set (the card's Use the free edition calls that operator, so nobody ticks a
  preference). Its sign-in window is ``vray.show_account_status`` (Chaos' account dialog, with Sign in). Without a
  signed-in account the server reports "Cloud-Licensing: There are no active user details (-91)" and F12 writes a
  black frame. Without the licence components at all (the unpacked add-on alone) it reports "The Chaos License
  Server is taking too long to respond" and after about 120 s **terminates the whole application**; Angelo's
  auto-save and recovery are what protect that session.
* **Free tier**: V-Ray Community Edition (from 2026-04-14): up to 2560 × 2560, 8-bit output, no batch or headless
  rendering (Angelo's render jobs run in its window, so they are unaffected; see Background processes), a 90-day
  licence renewed for free, a Chaos account required.
* **Materials**: V-Ray renders Cycles node materials, Principled BSDF included, for a supported subset of nodes, and
  can convert them to V-Ray materials (Convert to V-Ray Material, or the scene-wide Convert Materials). Materials
  Angelo's agent builds (Principled BSDF with image textures and the common utility nodes) are expected to render,
  but nodes outside Chaos' supported list render differently or not at all: compare an EEVEE or Cycles render
  before trusting a V-Ray one, and convert for production.
* Other engines follow the same path: BlendLuxCore (GPL-3.0; 2.11 supports Blender 5.2 on macOS arm64, Windows
  and Linux, installed as an extension zip that downloads its ``pyluxcore`` wheel from PyPI) and Octane's add-on
  (needs its own Octane Server). Corona has no Blender version.

AI render
=========

AI render is an action, not an engine: it restyles the scene camera's view and leaves the scene and its engine as
they are. It runs through an ordinary Generation Flow, so everything Generation Flows guarantee holds (one itemized,
priced generation; reuse; run cards; outputs in the Media Studio library), and the person can open the flow and
change it.

* **The flow**: the ``ai_render`` template (``flow_templates``): **Camera** (Scene Capture of the scene camera in
  Material preview at 1024 px), **Style** (Text) and **AI render** (Generate image with an English restyle prompt
  that keeps the camera, composition, poses and proportions and changes only the look). Its role prefers Nano Banana
  2.1, then Seedream 5.0 Pro, then Nano Banana Pro and Nano Banana 2; any ready image account still runs. One flow
  per document, found by its mark (``angelo_ai_render`` on the node tree), never by name; it is created on the first
  render.
* **The card** (``ai_render_card``, a ``card_host`` card over the 3D view, Angelo's card look): the camera it uses,
  the style (a text field holding the last style), the account and model (Nano Banana and Seedream first), what it
  is billed to and the price estimate (``flow_prices``; "price unknown" when the table has none), and **Render**.
  Without a camera or a ready image account it says so and Render stays off; Connect opens AI Providers.
* **The click is the approval** (``ai_render.start``): it writes the style, account, model and the camera's ratio
  (the nearest of Media Studio's 1:1, 16:9, 9:16, 4:3 and 3:4) into the flow, plans it (which captures the view:
  local and free), and starts only when the plan is exactly one generation of the AI render step at the price the
  card showed. Anything else (a step the person added, a changed price) is refused with a line saying to run the flow
  from its editor, whose dialog itemizes it. An unchanged style, account and scene would reuse the last image, so
  the click advances the step's take (Media Studio's Redo) and makes a new one.
* **Nothing else spends money.** Return in the style field only ends the typing; a Render already clicked, or a run
  of this flow still going (queued behind a full library included), refuses another (``RUNNING_ALREADY``). The
  click is a transaction: a refusal puts the flow's steps and the step's take back as they were. Before approving,
  ``wired_as_shown`` checks the flow still does what the card says (the camera capture as the only reference, the
  Style text as the only prompt input, the template's prompt); a flow changed in the Node Editor is run from there.
  A started run is an Undo step of its own.
* **While it runs** the card moves to the 3D view's corner and stops taking input, so the person keeps working;
  Close leaves it running and Stop ends monitoring as a flow's Stop does. When it ends the card offers **Open in
  Media Studio** (the Image tab) and **Render again**, or shows the provider's error; a run that ends after the card
  was closed brings it back once with the result.
* **Remembered** (``ai-render.json`` in the state folder): the last style, account and model, per person.

Validation
==========

* Host: ``test_native_render_engines`` (choices and order, installed engines, presets read back, GPU only when set up, refused
  engines, the button's width, the dropdown kind and pressed states, glyphs, the template, accounts, ratio, memory,
  and the click as the approval: one priced generation, a new take, refusals); ``test_native_flow_menu``,
  ``test_native_flow_templates``, ``test_native_chat_destinations``.
* Host: ``test_native_engine_setup`` (found, never run; set-up links, enables and cleans up; the sign-in card's
  states and the add-on's own operators); app: ``test_blender_engine_setup``.
* Host: ``test_native_render_extensions`` (``HeadlessTests``: an add-on's engine refused in a background Angelo at
  admission and at start, for jobs and looks; Blender's own engines and the window unaffected).
* App: ``test_blender_render_engines`` (Director's button and dropdown, a Cycles choice that lands on the GPU and
  renders there, the Render menu, the AI render card's phases with a stand-in run), with screenshots in Director.
