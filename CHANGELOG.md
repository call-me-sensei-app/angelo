<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Angelo release notes

What changed in each Angelo release, written for the people who use it. This file is the single
source: the app's What's New, the update notice and the website's release page all come from it.
The format is checked by `scripts/angelo/changelog.py` and described in
[the updates and reports contract](doc/angelo/updates_and_reports.rst). Add the new release at the
top; run `python3 scripts/angelo/changelog.py export` after editing.

## 1.0.7 — 2026-10-30
Based on Blender 5.2.2 LTS.

Angelo 1.0.7 links Angelo to game engines: edit a Godot asset and watch each change live in the Godot editor, or save models back into Unity and Unreal Engine projects. Chats stay with their file across save and reopen, a restore keeps you in your own file, and chat bugs are fixed.

### Highlights
- Godot live link: choose Edit in Angelo on a Godot asset, and every change you make in Angelo shows live in the Godot editor.
- Unity and Unreal Engine links: edit a project's model in Angelo, and saving writes it back for the engine to re-import.
- A chat stays with its file when you save, quit and reopen it.
- After a restore you keep working in your own file, and the first save asks before writing over it.

### New
- Godot live link (tested with Godot 4.7): add the Angelo Link plugin to a project from the File menu's Export or the chat bar's + menu (Add Angelo Link to a Godot Project…). The status bar and Director's top bar show while the link is live or lost.
- A .glb asset from Godot is edited in a working .blend inside the project's .angelo folder and written back when you save; a .blend asset is saved in place.
- Unity link (tested with Unity 6.5): the Angelo Link package adds Edit in Angelo to .blend, .glb and .fbx models. Saving in Angelo writes the model back with the options Unity itself uses, and Unity re-imports it.
- Unreal Engine link (tested with Unreal Engine 5.8): the Angelo Link plugin edits a .glb-sourced asset in Angelo; saving writes the .glb and the plugin re-imports it with the asset's own settings. It needs Unreal's Python Editor Script Plugin.
- Unreal Engine live preview (tested with Unreal Engine 5.8 on macOS): the Angelo Link Live plugin shows your edits on the asset's instances in the open Unreal Editor before you save. It is a source plugin you build yourself; Angelo doesn't install it or ship its binaries yet.
- Live Web Preview: hide the whole report card with the button beside its chevron, or press I. A small button brings it back and still shows when a new export arrives; your choice is kept across live updates and reloads.

### Improved
- Chat replies show Markdown tables as tables. A table too wide for the chat wraps its widest columns, or shows each row as a list, so no word is split.
- A tool row says what is happening: "Running Blender Python" while it runs, the past tense only once it ran, and a declined or queued step never reads as done.
- A script's approval says plainly that the agent will run Blender Python code, and asks you to review it first.
- Escape now ends typing in the chat box, so the next key goes to the editor under the pointer, such as Z for the shading pie.
- Solid view shows a material built from shader nodes in the colour its nodes give it, for the agent's materials, imports and scripts alike.
- When the agent's plan is rejected, the notice lists the problems, not a page of schema text.
- The agent can look up the parameters of every operator, however the operator is registered.
- The agent's job tool says which fields each kind of job takes, so it no longer sends a field a job refuses.
- The Restore confirmation is two short paragraphs.

### Fixed
- Saving, quitting and reopening a file made the chat that built it refuse every request with "The document changed". The chat now continues on the same file; a copy or a different file at the same path is still another document.
- After Restore snapshot or Undo restore, Save wrote into Angelo's private folder and the quit prompt offered a "recovered-…" file. The document is now your own file, marked unsaved.
- A new chat showed "Choose a model to start" even with an account connected and Default selected.
- After a relaunch, a chat could show text written for the model instead of its notice.
- The live web preview no longer ends Edit Mode after each pause: it waits while you edit, and Sculpt and the paint modes keep their mode.

## 1.0.6 — 2026-10-26
Based on Blender 5.2.2 LTS.

Angelo 1.0.6 brings parallel edit: on a capable machine, the agent can split a scene into parts that worker agents build at the same time, then merge them into your scene. It also poses hands, exports scenes for the web and welds imported glTF models with shape keys so they show no seams.

### Highlights
- Parallel edit: ask the agent to split a big scene, and workers build its parts at once, live in the Sessions panel, then merge them in.
- The agent poses hands: a natural closed fist, a curled finger or any hand pose, inside each joint's limits.
- Export for the web in one step: a compressed .glb for Three.js, with a live browser preview and web shaders.
- glTF/GLB models with shape keys added through Media Studio, Flow or the Library no longer show seams when a shape key moves.
- Media Studio models saved without a thumbnail get one in a later session, once the machine can render.

### New
- Parallel edit, on by default on Macs and PCs that can run it: the agent starts worker agents that each build one part of your scene in a file of its own, at the same time.
- The Sessions panel shows each worker live with its progress; open its tile to watch it or send it a message.
- Merging brings each finished part into your scene where it was built, or where the agent places its copies, as one Undo step. In Ask mode, a card shows first.
- When the agent's turn ends while workers still build, Angelo continues the chat once they have all finished, so their parts get merged.
- Settings, Workers at once: how many workers run together. On a machine below what parallel edit needs, Settings says what's missing.
- Media Studio has a Workflows tab of ready-made runs. The first, Character in parts, turns one character image or description into a parts sheet, a 3D model for each part and the assembled character, ready to finish in Angelo. Open it from the chat bar's + menu.
- Character in parts can fit each part to your character image (Fit parts to my image, when AI Enhance uses GPT-6.1 Sol on a ChatGPT subscription), so the parts land where your image shows them: typically about 1 cm off on our test characters.
- Export for the web in one step: a compressed .glb for Three.js, its size budget, and what won't carry over, each with its fix.
- Live browser preview: your scene in your own browser as Three.js draws it, updated after every change, yours or the agent's.
- Web shaders: give a material a toon, rim glow or hologram look, or your own GLSL file; the browser shows it live.
- Choose where the Sessions panel stands (left, top or bottom of the 3D view, under AI Providers in Preferences); its minimize button turns it into a round button at the top right that counts the workers and marks one waiting for your approval.
- Each chat keeps to the scene it works on. Show another scene and the chat pauses and says which scene it's waiting for; switch back and it continues. A voice session ends when you switch away from its scene.
- Work on hands in the chat bar's + menu: Angelo reads the selected rig's hands and gives them to the agent, with how hands bend, before it starts.
- The agent can check a hand rig for anatomy and weight problems, measure where fingers touch, and show each pose beside the rest pose.

### Improved
- Generation Flows start from the same ready-made templates as Media Studio's Workflows.
- Blender's glTF importer warns, in the status bar and the console, when a file with morph targets is imported with Merge Vertices off. Blender's import default is unchanged.
- A render job can write the scene's own image format, including multilayer OpenEXR, with the scene's shot names and frame step.
- The approval for an agent's render shows its estimated time, from earlier renders of the same scene on this device.
- When an agent's Cycles render ends, the agent learns the most memory it used.
- Angelo no longer passes Codex's own sub-agent instructions and tools to GPT models, so every request is smaller (about 8 KB, roughly 2,000 tokens, on GPT-6.1 Sol's first request).
- When a worker stops (its file was replaced, the lead's document changed, or its background Angelo stopped running), its chat says why, stops its model request, and gives back any message it hadn't sent yet.
- A chat paused on another scene no longer holds up the edits of the chat on the scene you're looking at.
- Restoring a checkpoint brings each chat back to its own scene, even if scenes were renamed or added since.
- The lead's tile in the Sessions panel keeps its picture while you look at another scene, and shows the scene camera or your view only when the model fills the frame; otherwise it frames the model.
- The chat box's hint wraps across lines, including Arabic text and Chinese and Japanese line breaking. In very narrow fields, long hints are shortened.
- Parallel edit starts sooner and takes fewer steps: the lead learns when workers finish and merges their parts in the same step.
- The lead's window no longer freezes for seconds when a view first shows new materials: their shaders compile in the background.
- A part's size now counts the point and curve radius that Geometry Nodes gives it.

### Fixed
- Auto Save didn't write while Angelo's chat or panels were open, so work between agent requests had no recovery file. It now saves on schedule, and still waits while a tool such as Move or a sculpt stroke is in progress.
- AI Enhance waits out a busy provider instead of failing.
- Media Studio models saved to the Asset Browser library without a thumbnail, on machines where the background render couldn't use the GPU, get their thumbnail in a later session once the machine can render.
- Imported glTF/GLB models with shape keys no longer show seams when a shape key moves, when you add them through Media Studio, Flow, the Library or the team library: Angelo welds their split seam vertices.
- The agent can edit a full character's armature again; every change to a rig with long finger chains was refused.
- A long error or refusal reason the agent reads is shortened at a word boundary, so it never ends in half a file path.
- On macOS and Linux, apps working on a .blend without Angelo's window (headless MCP) can now apply staged programs and recipes, each as one Undo step, and Angelo reports unsaved changes correctly after their edits are saved.
- An agent's render or texture bake always stops at its time budget, even while the interface is held up or Cycles is still preparing the scene on the GPU.
- Cancelling a Cycles GPU render on a Mac works while it is still building the scene (BVH).
- A render's finished frames are no longer removed when Angelo can't read its hidden render folder for a moment (a disk or permission error); they are kept and published at the next start.
- Media Studio's Blender asset library no longer deletes a model's .blend copy when it can't read the library's records.
- The usage meter keeps your per-chat history: a long history is trimmed to the newest chats instead of being ignored and then replaced.
- A complete OpenEXR frame with a very large header is no longer treated as unfinished.
- Your chat list, job history and saved connections are no longer replaced when their file can't be read; it is kept beside it and read back, and the chat list also lists every chat on disk.
- The agent keeps sight of what an edit did to the World, motion and shared data in long conversations, instead of losing those facts when older results are shortened.
- Angelo never stumbles over a window that is still opening or closing: its panels, cards and timers wait for it instead of failing or acting on it.
- An approved script that opens, reverts or resets the file no longer crashes a headless MCP session (macOS and Linux): the script stops with a clear message, the session's own file is loaded again with nothing of that run saved, and the script adds no Undo step.
- The 1.0.5 notes now say that headless MCP works on macOS and Linux only for now, and that an approved script or one of your add-ons can still write the .blend Angelo opens, since only Angelo's own edits go to a new file.
- An internal failure in a typed program while it is only being prepared no longer ends the agent's turn: it comes back as a correctable refusal naming the operation and saying nothing changed. A material given after a Boolean that adds a slot is refused clearly before anything changes.
- The agent was told the wrong size for curves, outline text and lattices: a thin curve read about 2 m wide, and a lattice read 0. Sizes now come from the geometry Angelo shows.
- Geometry Nodes instancers and collection instances told the agent the wrong size (0, or a padded box around a curve); sizes now include what the instances show.
- Agent file tools can no longer read or list Angelo's private data (settings, account profiles, checkpoint scene maps, background-process folders), even when a project folder sits inside them; such a project gets no file tools.
- A material swap on copies the agent placed is either applied everywhere it shows or refused by name; it no longer silently fails on collection instances. In this version, swaps on parts with Geometry Nodes, instancing, drivers, curves or text are refused.

### Known issues
- The first time a view shows one object with many new materials, or a new world, the window can still pause briefly while their shaders are set up. More of this moves to the background in 1.1.

## 1.0.5 — 2026-10-23
Based on Blender 5.2.2 LTS.

Angelo 1.0.5 puts Media Studio and Generation Flows one click away, lets you drag Media Studio's work into Blender's editors, opens your .blend files to external AI apps without Angelo's window on macOS and Linux, and stops trusting the compromised emSign certificates.

### Highlights
- Drag Media Studio's models and images into the 3D view, the Outliner, node editors, Generation Flows or the chat.
- On macOS and Linux, external AI apps connected over MCP can open and edit a .blend without Angelo's window, saving each edit as a new file.
- Media Studio and Generation Flows are one click away: the chat bar's + menu, Studio and Flows in Director, and the welcome.
- Ready-made Generation Flows to start from (image to 3D, image to reference to 3D), and chat suggestions for images and 3D.
- Angelo starts 2.1× faster than 1.0.4 in the background and in about 40% less time with a window, close to stock Blender.

### New
- Drag Media Studio's models and images into the 3D view, the Outliner, Properties, the Image Editor, node editors, Generation Flows or the chat; every drop also has a menu item, such as Add to Scene, Apply to Selected and Add to Chat.
- On macOS and Linux, external AI apps connected over MCP can now open a .blend without Angelo's window: they read the scene and make Angelo's bounded edits, each saved to a new file beside yours. It follows the “Allow apps on this computer to reach Angelo” switch and your organization's policies.
- Headless MCP sessions use your Angelo preferences read-only: your add-ons, extensions, script folders and GPU device, as in the window. Angelo works on a copy beside your file; in a read-only folder it can't save edits. An approved script or one of your add-ons can still write the file.
- An external AI app can run a script file you choose to always allow while it's unchanged: Angelo reads the file itself and asks again if it changes. The same approval lets that file run when the app opens a .blend without Angelo's window.

### Improved
- The chat bar's + menu groups the base meshes in a list that stays closed until you open it, and remembers your choice.
- Rendered looks finish as soon as their render ends instead of waiting for the next job check, up to 0.25 s sooner per look.
- A new chat shows its usage meter as soon as a connection is chosen, including your plan's usage window.
- The chat's welcome cards sit in the middle of the chat.
- In Manual mode, Media Studio and Generation Flows have their own tabs after Director, each a single editor with no stock header, sidebar or toolbar.
- The Asset Browser and the Object Mode Asset Shelf open Media Studio and Generation Flows, and say when they are empty.
- Step names that Angelo gives a Generation Flow appear in your interface language; names you type stay as you typed them.
- Generation Flows' nodes use Angelo's card style; every other node editor keeps Blender's look.
- Every base mesh in the chat bar's + menu shows its own picture.
- In Object Mode, Angelo's Asset Shelf starts collapsed to its tabs; its chevron button opens and closes it, and Angelo remembers your choice.
- Media Studio's models and images are in Blender's Asset Browser and asset shelves under Angelo Media Studio and drag into a scene like any asset. The library keeps compressed model copies (about 40% of each model); Media Studio shows their size and can turn the library off or free up the space.
- Start-up times were measured on an M4 Pro Mac as medians of 5 launches, against Angelo 1.0.4 and stock Blender 5.2.2.

### Fixed
- When the agent assigns a material to several objects at once, every one of them now takes the new material.
- When one of the agent's steps fails its own check and changes nothing, the agent now gets its own chance to correct it, even after earlier plans were sent back, instead of ending the turn with nothing built.
- If a reply from the bundled Codex agent keeps streaming without finishing, Angelo now stops it within 10 minutes and, if nothing was changed yet, asks again once, instead of waiting up to 30 minutes.
- While the chat is hidden, Angelo moves to the 3D view's corner as soon as you switch tabs, change an editor, load a file or switch modes.
- In Manual mode, showing the chat now works at larger interface scales, such as UI scale 2, and with wider editor borders.
- In Manual mode, hiding the chat gives its room back to your 3D view.
- Blender's Quick Setup form no longer appears in an Angelo window, however Angelo is started.
- The setup card's language list no longer shows the card's controls through it.
- The start panel appears in your interface language.
- When Angelo starts, Blender's default layout no longer shows before Angelo's own.
- After you switch to the Angelo Light or Dark look in Preferences, the chat's cards keep their text.
- The model list opens above the chat's message box instead of covering it.
- Adding a base mesh from the + menu no longer shows a timing in the chat.
- Base mesh names in the + menu are spelled correctly.
- Angelo no longer crashes when an asset library changes while its previews are still loading.
- The Asset Browser's online access card, its All Libraries and Built-In entries and its offline notice appear in your interface language.
- A new, empty chat no longer warns that the document changed when you open a file.
- Angelo and its bundled Codex agent no longer trust emSign ECC Root CA - G3 or the eight eMudhra intermediate certificates whose private keys became public in October 2026, so no one on your network can use them to pose as Angelo's servers or to intercept their connections to your AI providers.
- Claude Sonnet 5.5's cache reads now cost $0.10 per million tokens in Angelo's cost figures, as Anthropic lists them, instead of twice that.

### For IT administrators
- Angelo's bundled root certificates are updated (certifi 2026.10.19, pip 26.2.2; GHSA-6q48-6c7w-9gw2), and the bundled Codex agent is rebuilt with them (webpki-roots 1.0.10, rustls-webpki 0.103.16).
- Angelo refuses certificate chains through the compromised eMudhra keys even when the operating system's certificate store or an ExtraCACertificates root would accept them. Updates were never affected: Angelo verifies its own signature on every update.
- On Windows, two helpers OpenAI ships with Codex still contain emSign ECC Root CA - G3: one makes no TLS connection when Codex runs it; the other sends only setup metrics, when Codex's usage analytics are on, without credentials or content. Both go when Angelo moves to stable Codex 0.164 or later.
- Claude Code, which you install separately, uses its own certificate store; this update doesn't change it.

## 1.0.4 — 2026-10-22
Based on Blender 5.2.2 LTS.

Angelo 1.0.4 keeps Blender's menus in Director, keeps an animation render's finished frames through a crash, shows what each chat uses, and renders on your GPU on every path.

### Highlights
- Director keeps Blender's menus, and the Chat button works in both modes; while the chat is hidden, Angelo waits in the 3D view's corner.
- An animation render job keeps every finished frame through a quit, crash or full disk, and offers to render only the missing frames.
- A meter at the top of the chat shows its tokens, the share that was cached, and an estimated cost or your plan's limit use.
- Cycles renders on your GPU from the first render on every path, background renders included; each render's card shows the device it used.

### Improved
- While the agent works, the chat shows what it is doing, for how long and how many tokens it has used, and says when the model has gone quiet.
- The welcome screen calls the second mode Manual and marks the mode you are in.
- In scenes with many objects, an object the agent has looked up stays editable in later steps, and new copies take the next free number instead of being refused.
- The agent can now look at OpenEXR images in the PIZ, PXR24, B44, B44A, DWAA, DWAB and HTJ2K codecs, and at tiled images.
- Mantaflow smoke, fire and liquid bakes that the agent starts run as background jobs, with progress and a Stop that works.
- On a Mac, Angelo keeps the computer awake while a background job runs, and lets you know when a job of a minute or more ends.

### Fixed
- The chat and Angelo's other panels scroll in your system's scroll direction, as Blender's own views do, and no longer scroll twice as far on high-resolution displays.
- In the chat's message box, the cursor, selections and clicks land on the characters as drawn, including accented, Japanese and emoji text.
- Animation render jobs saved as OpenEXR publish their frames again.

### Known issues
- When the agent assigns a material to several objects at once, the extra objects can keep their old look even though the agent reports its checks passed. This was already the case in 1.0.3; a fix is coming in a 1.0.4 update.

## 1.0.3 — 2026-10-21
Based on Blender 5.2.2 LTS.

Angelo 1.0.3 improves how the agent works in Blender, makes saving safer, and carries fixes from upstream Blender.

### Highlights
- Claude now works from Angelo's own instructions. In our timed tests it cost less, completed more test scenes and was no slower.
- Claude and coding tools that use Angelo over MCP now know Blender 5.2's current Python API, so their scripts avoid removed names.
- Saving a .blend file can no longer overwrite another file through a symbolic link left at its temporary file name.
- Fixes from the upcoming Blender 5.2.3 and 5.3, including crashes in texture painting, Cycles, Geometry Nodes and rendering.
- Scripts that add F-curve modifiers, drivers or driver variables, or bake F-curves, now update the scene at once.

### Improved
- Director and Manual now switch with Ctrl+Option+M, from any editor.
- The Chat button in the top bar now has a label, and a one-time hint under it shows how to show and hide the chat.
- In our tests, when the agent builds a sky, the Sky Texture's sun now lines up with the scene's sunlight.
- Claude knows how to start a render or another background job without looking it up first, as Codex already did.
- Claude and Codex read the same Blender facts, including how the Sky Texture's sun is aimed and how its single- and multiple-scattering skies differ.
- Coding tools that use Angelo over MCP also get these Blender facts.
- Media Studio's Prompt Editor sends only its own text to the model, never the chat agent's instructions.

## 1.0.2 — 2026-10-20
Based on Blender 5.2.2 LTS.

Angelo 1.0.2 connects Claude Code and Codex to Angelo over MCP, and renders on your GPU by default.

### Highlights
- Claude Code and Codex can now use Angelo over MCP: connect them under AI Providers in Preferences.
- Cycles now renders on your GPU by default (Metal on Apple silicon), also when a script or the agent starts a render.
- Start a character from one of 21 base meshes: press + in the chat and pick a head or body.
- On a first launch the chat no longer ignores clicks while the welcome screen or a notice card is open.
- Show or hide the chat from the top bar, or with Ctrl+Option+N.

## 1.0.1 — 2026-10-20
Based on Blender 5.2.2 LTS.

Angelo 1.0.1 updates Angelo's legal notices. Nothing changes in how Angelo works.

### Highlights
- Hyperbond Studio Pte. Ltd. is now named as Angelo's copyright holder, in About and the notices.
- Angelo's trademark policy is final.

## 1.0.0 — 2026-10-19
Based on Blender 5.2.2 LTS.

Angelo 1.0 is the first release: Blender with an AI assistant built in. Describe what you want, see the change as a preview, and approve it. Everything stays undoable, and all of Blender is still there.

### Highlights
- Ask in plain words. Angelo shows each change as a preview you approve, and one Undo takes it back.
- Use the AI you already have: connect Claude or ChatGPT in one click, or bring your own API key.
- Director mode keeps the 3D view and the chat in front. All of Blender is one click away.
- Angelo Teams: sign in with your organization, use the team's AI without handling keys, and share a library.
- Generate images and 3D models in Media Studio, and chain the steps in Generation Flows.

### New
- Angelo runs on Macs with Apple silicon and on 64-bit Windows, with all of Blender 5.2.2 LTS: Cycles, Eevee, physics, video, USD, Alembic and the usual import and export formats.
- Requires macOS 26 or later.
- A welcome screen on first launch shows the two ways to work, Director and the standard editor, and how to switch. Open it again from the Help menu.
- Connect Claude or ChatGPT from the empty chat. ChatGPT needs nothing installed: Angelo includes its Codex runtime. Claude Code is found on your computer. Angelo signs you in through your browser and picks a model for you.
- Or use an API key for Claude, OpenAI, Grok, MiniMax or Kimi. Keys are kept in your computer's secure storage: the Keychain on a Mac, Credential Manager on Windows.
- Claude Opus 5.5, GPT-6 Astra and GPT-6.1 Sol are the launch models.
- Choose how much Angelo does on its own: by default it approves small checked edits automatically; it can also ask before every change, or only look and talk. Chats from earlier builds switch to the new default once. Scripts ask first unless you choose Always allow scripts.
- Angelo works across Blender: modeling, modifiers, materials and node trees, sculpting, cameras and lights, keyframes, the NLA editor, markers, bones, constraints and particles.
- Angelo can look at the 3D view while it works, to check that a change came out as you asked.
- Checkpoints save your scene before larger changes, and you can return to any of them.
- Angelo keeps a short brief of what you are working on, so a long task or a reopened project picks up where it left off.
- Media Studio makes images and 3D models with your own Gemini, Tripo, Meshy or Seedream key. Generation Flows chain those steps in a node tree, with one approval for each run.
- Angelo Teams: sign in with your organization's account and use the team's AI without handling keys. The status bar shows your account, your usage and your signed-in computers.
- A team library: search your team's shared media and worlds, bring assets into a scene and save models back.
- Coding tools on your computer can read your open scene through the angelo command, after you turn it on in Preferences. It is off by default and can only read.
- Angelo keeps itself up to date: a new version downloads in the background, and Restart to Update installs it and opens your file again. It never restarts while the agent or a render is working.
- If Angelo quits unexpectedly, the next launch says so and shows exactly what a crash report contains: technical details only, never your scenes, prompts, chats, files or keys.
- What's New appears once after an update and is always in the Help menu.
- Angelo speaks 21 languages, including Arabic from right to left. Choose yours on first launch or in Preferences; Blender's menus and panels follow it too.

### For IT administrators
- Managed settings, through a macOS configuration profile or Windows Group Policy, can require team sign-in, limit personal AI connections and command-line access, require Ask before changes, turn script auto-approval, voice and collaborators off, name a contact and trust your TLS inspection root.
- RequireAskForEdits makes every chat ask before changes but still allows Trust scripts for this chat; add AllowAutoApproval false to have every script reviewed too.
- DisableUpdateCheck turns update checks and downloads off, UpdateFeedURL points them at your own copy of the signed update file, and DisableCrashReporting turns crash reports off.
- The Windows installer installs Angelo for the current user, with no administrator prompt. Install it for all users with msiexec ALLUSERS=1; that installation then gets its updates from you.

### Known issues
- Translations other than English are machine-made and still being reviewed by native speakers, so some wording may change.
- Crash reports can't be sent from the app yet. Save the report from the crash dialog and attach it on the website's Report a bug page.
