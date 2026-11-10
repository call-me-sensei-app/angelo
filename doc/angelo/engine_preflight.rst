.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

#########################
Engine-contract preflight
#########################

Before a scene goes to a game engine or the web, the preflight says what that engine's importer will do with it,
offers a typed fix where one is safe, and after an export reads the written file back and compares it with the scene.
Targets: ``GODOT``, ``UNITY``, ``UNREAL`` and ``WEB`` (Three.js). Modules: ``engine_rules`` (the rules, no Blender),
``engine_facts`` (what the rules read), ``engine_fixes`` (the typed fixes), ``engine_reimport`` (the comparison),
``engine_preflight`` (profiles, the check and reports), ``engine_preflight_link`` (the background run after an
engine-link save), ``engine_check_spec`` and ``engine_check_host`` (the agent tools) and ``engine_fix_approvals``
(the fix card).

.. contents::
   :local:

Where it runs
=============

==============================  ============================================  ===============================
Surface                         What runs                                     Changes the scene?
==============================  ============================================  ===============================
``angelo_engine_check``         the rules for one target                      no (read-only, no card)
``angelo_engine_fix``           chosen typed fixes, then the rules again      yes, one Undo step, after a card
``angelo_web_export``           the ``WEB`` rules; the re-import in a         no (report-only: ``preflight``,
                                background Angelo after the file is written   then a notice and an observation)
Engine-link save                the engine's rules and the re-import, in a    no (report-only, a file beside
                                background Angelo after the write-back        the asset's working file)
MCP apps                        check at Read, fix at Edit (window only)      as above
==============================  ============================================  ===============================

Nothing runs on the everyday path: no handler or draw callback of the preflight exists, and its one timer
(``web_readback``) is registered only while a web export's read-back runs. The live web preview never runs it (it
re-exports after every change). An engine-link save starts it only after the engine already has the asset, so the
engine's re-import never waits for it, and a web export returns as soon as its .glb is written, with
``preflight.readback: "running"``.

Web export read-back (``web_readback``)
---------------------------------------

The export describes the scene before it writes (``engine_reimport.snapshot``, plain JSON). Once the .glb is
published, the export gets a revision (``preflight.readback_revision``) and the file is copied into a private folder
as it was written (``export_snapshot``: an APFS clone, which takes no time or space, else a plain copy), so a later
export or anything else writing over the same name never changes what is compared. A background Angelo reads that
copy back in an empty scene and compares it (``engine_preflight.readback_child``); a copy whose identity changed
before or during the read is reported as a failure, never compared. One runs at a time; the newest export during one
replaces the queued one, which ends at once as ``superseded`` (with ``superseded_by``, the newer revision) and its
folder is removed. A read-back whose thread can't start (``RuntimeError: can't start new thread``) leaves the runner
idle and its folder gone, and the export answers ``preflight.readback: "unavailable"``: the next export starts one
again (``thread_start``, "Background threads" in the agent integration document). When one ends, the chat that
asked gets a notice ("Read web/scene.glb back: 2 differences from the scene"; none for a superseded one), and the
result joins that chat's next request as the ``preflight`` event
observation (``[{file, revision, status, rules, total, ms}]``, grouped as in "Tool results"), once. A chat
closed meanwhile gets nothing; a chat keeps at most four results for its next request.

Profiles
========

The rules read the scene as the target's exporter will (``engine_preflight.profile``):

* ``GODOT``: ``.glb`` with the glTF options Godot's own Blender import uses (``godot_link_assets.godot_options``),
  from the asset's ``.import`` settings when the engine link has a Godot asset open.
* ``UNITY``: ``.fbx`` (``AUTO``) with Unity's own ``Unity-BlenderToFBX.py`` profile (``unity_link_assets.UNITY_FBX``),
  or ``.glb`` with the shared glTF profile (``GLTF_DURABLE``).
* ``UNREAL``: ``.glb`` (``AUTO``, the Unreal link's kind) with ``GLTF_DURABLE``, or ``.fbx`` with Blender's FBX
  exporter defaults.
* ``WEB``: Angelo's web export (visible objects only, modifiers applied, no custom properties).

``exported`` follows the exporters as measured in Blender 5.2.2: the glTF exporter without ``use_visible`` writes
every object of the scene, hidden ones and those in excluded collections included; the FBX exporter writes every
object of the view layer, hidden ones included, but not excluded collections.

Rules
=====

Every rule comes from an engine's documented import contract or its import code (``engine_rules.SOURCES``), never
from forum threads, and every finding carries ``source`` and ``versions``: the engine versions its source was read
from (Godot 4.0 to 4.7, Unity 2020.3 to 6000.5, Unreal 5.0 to 5.8, three.js r152 to r185). An ``engine_version``
outside that range still runs the rules and adds ``version_note``. A finding is ``{code, severity, subject, message,
fix, facts {looked_for, found}, source, versions, fixable}``; ``fixable`` names the typed fix, or is null.

========================  ==================================  ================================================  ====================
Code                      Targets (formats)                   Looks for                                         Fix
========================  ==================================  ================================================  ====================
``unit_scale``            all (.glb)                          Unit Scale 1: glTF writes 1 unit as 1 m           none
``transform_unapplied``   all                                 an armature at rotation 0, scale 1 in the world   ``apply_transform``
                                                              (its own transform or its parents')               (no parent)
``collider_scale``        Godot                               uniform scale on a ``-col``/``-colonly`` mesh     ``apply_scale``
``leaf_bones``            Unity, Unreal (.fbx)                ``add_leaf_bones`` off                            none (exporter)
``armature_extra_root``   Unreal (.fbx)                       an armature named ``armature``, top level, one    ``rename_armature``
                                                              root bone (else it becomes a bone)
``root_bones``            Unreal                              one root bone                                     none
``constraints_live``      all                                 no live constraints, or an animation that         none
                                                              samples them (warn only where something
                                                              animates and nothing samples them)
``actions_one_take``      Unity (.fbx)                        one action per model (Unity's FBX profile writes  none
                                                              one take)
``action_unsaved``        all                                 a user for every action of an exported object     ``stash_action``
``loop_name``             Godot                               a ``-loop``/``_loop``/``-cycle`` suffix on an     ``loop_name``
                                                              action that ends where it starts
``smooth_modifier``       all                                 a shading modifier the export applies             ``apply_modifier``
``curve_no_surface``      all                                 faces on a curve, text, surface or metaball       none
``instancing_lost``       all                                 one mesh shared by identical geometry             ``link_data``
``hidden_exported``       Godot, Unity, Unreal                only objects the viewport shows (the glTF         none
                                                              exporter skips its importer's
                                                              ``glTF_not_exported`` collection)
``absolute_paths``        Unity, Unreal (.fbx)                packed images (FBX writes each one's full path)   none
``path_properties``       where custom properties are         no local paths in exported custom properties      none
                          written
``collision_empty``       Godot (.glb)                        a mesh, not an empty, with a collision suffix     ``collision_mesh``
                                                              (an empty written as an OMI collider is fine)
``suffix_numbered``       Godot 4.0 (stated version)          a Godot suffix at the very end: 4.0 reads none    none
                                                              before Blender's ``.001``
``collision_unmatched``   Unreal                              a render mesh for every ``UCX_``/``UBX_``/        none
                                                              ``USP_``/``UCP_`` name
``collision_foreign``     all                                 collision names the target reads (Godot           ``hide_collider``
                                                              suffixes, Unreal prefixes)                        (web only)
``influences``            all                                 at most the engine's influences per vertex        ``limit_influences``
                                                              (three.js 4, Godot 8, Unity 4, Unreal 12; glTF's
                                                              exporter 4 unless all are kept)
``colour_space``          all                                 sRGB for colour maps, Non-Color for data maps     ``colour_space``
========================  ==================================  ================================================  ====================

The name matching is the engines' own: Godot's ``_teststr`` (trailing digits, spaces and underscores dropped, so
Blender's ``.001`` still matches; ``-``, ``_`` or ``$`` before the token) and Interchange's collision match (the rest
of the name, or it without its last ``_suffix``, equals a mesh name in any case). An object named for one engine's
collision convention and exported to another (``UCX_`` in Godot, ``-col`` in Unity or the web) is a visible mesh
there. Godot reads ``-colonly`` on an empty only from COLLADA, whose importer sets the empty's draw type; glTF has
none, so the empty arrives as a plain Node3D.

Not every report item is a rule. Curves and text: Blender 5.2's glTF and FBX exporters both write their evaluated
mesh, so only a curve with no faces loses anything. Shape keys: the glTF exporter keeps them with modifiers applied.
Actions: the glTF exporter's ``ACTIONS`` mode writes every action with a slot for the object, so only an action with
no user (lost when the file is saved, which is what an engine imports) is a finding.

Fixes
=====

``angelo_engine_fix`` takes ``{code, subject}`` items from a check. The host runs the check again on the current
scene and refuses the call when any item is no longer found (``STALE_REVISION``) or has no safe fix there
(``VALIDATION``), so a fix never acts on a scene it did not see. It runs in Object Mode only. Subjects are local data
blocks by name: a linked block of the same name is never the one changed (its findings carry its library's name,
``Skin [kit.blend]``, and no fix). Renames run last, so the other fixes find their subjects under the checked names.
One Undo step covers everything that ran, a fix that failed part way included: in a window it is the approval's own
(the Approve click's apply operator, "Apply Approved Angelo Change"), which the route never doubles, so one Ctrl+Z
restores every fix; a background Blender, with no operator around the call, gets the route's "Engine fixes". The
result lists
what each changed (before and after), each failure (a refusal changed nothing; "failed part way" says Undo restores
it) and the findings that remain.

==================  ===================================================  =============================================
Fix                 What it does                                         Offered only when
==================  ===================================================  =============================================
apply_transform     Object > Apply rotation and scale on the armature    neither the armature nor a mesh child is
                    (its children keep their place), then on each        animated (object transforms, bone locations)
                    mesh child                                           or constrained, every mesh is single-user
                                                                         without shape keys, and it has no parent
apply_scale         Object > Apply scale                                 the mesh is single-user and editable
rename_armature     names the armature object ``Armature``               it is top level with one root bone and the
                                                                         name is free
stash_action        keeps the action in a new muted NLA track            the action has no user
                    ("[Action Stash]", as Blender's Stash)
loop_name           renames the action ``<name>-loop``                   always (Godot's suffix)
apply_modifier      applies the flagged shading modifiers only (the      the mesh is single-user with no shape keys
                    ones the export wouldn't apply)
link_data           every identical mesh object uses the first one's     the objects key equal: positions, topology,
                    mesh (Link Object Data); the group is keyed again    every attribute (per-face material indices
                    from the objects as they are just before linking,    too), UVs and colours in use, mesh custom
                    and only those still equal are linked                properties, the mesh's materials and each
                                                                         slot's link and resolved material; no
                                                                         modifiers, shape keys or vertex groups;
                                                                         never a collider
collision_mesh      replaces the empty with a box or sphere mesh of its  the empty is drawn as a cube or sphere and has
                    drawn size; whatever pointed at the empty points at  no animation, constraints, bone parent, custom
                    the mesh (``ID.user_remap``: collections, children,  properties or linked data or collections
                    constraint and modifier targets); same name, place,
                    parent and visibility; wire display
hide_collider       hides the object in the viewport (web only: the      always
                    web export writes visible objects)
limit_influences    keeps the largest weights of the armature the        the mesh is single-user and no deform group
                    export uses (its first viewport-enabled Armature     is locked
                    modifier, as the check and read-back read it) up to
                    the engine's limit, normalized over those
colour_space        sets the image's colour space to sRGB or Non-Color   every use of the image in any material of the
                                                                         file is one role (a factor, mask or vector
                                                                         input is data)
==================  ===================================================  =============================================

The approval card lists one translated line per fix with its subject (``engine_fix_approvals``).

Re-import comparison
====================

``engine_reimport`` describes the exported objects before the export, then imports the written file with Blender's
own importer into a temporary collection of the active scene (never a temporary scene, whose context the importers
don't follow) and describes what arrived the same way, in metres. It compares:

* hierarchy: objects missing from the file, objects the scene doesn't have, changed parents, and objects that arrive
  as another kind (``reimport_kind``: a mesh with no faces arrives as an empty). The source still exists while the
  file is read, so Blender renames every imported object (``Cube`` arrives as ``Cube.001``, ``Cube.001`` as
  ``Cube.007``); objects are paired within each name stem, same kind first, by the nearest box centre;
* bounds: each object's world box within 1 % of its largest side (and 1 mm); a uniform factor is a units finding, the
  same sides on other axes a facing finding;
* bones added (FBX leaf bones) or lost;
* skin weights: the most influences per vertex the file keeps against the scene's (up to 20,000 evenly spaced
  vertices per skinned mesh): the exporters keep each vertex's largest influences, so the vertices over the file's
  limit and the largest weight they lose (0.01 or more) are exact without pairing vertices, which compression moves
  (the web export's meshopt moved a character 40 m from the origin by up to 2.6 cm);
* materials per object and each image's colour space as the importer reads it;
* the written file's bytes: the home folder or the .blend's folder in it (``path_in_file``).

The read-back runs in Object Mode, or in Edit Mode, which it leaves and enters again as the web export does (the
importers switch modes); in any other mode (Sculpt, Paint, Pose) it is skipped with ``reimport_skipped``. The glTF
import draws no bone shapes (an operator would add them to a mesh in Edit Mode). After the comparison every data
block the import made is removed (objects first), links the import added to existing objects are undone (the glTF
importer's helper collection), the view layer is synced, and the selection, active object, active layer
collection, frame, frame range and frame rate are restored. Operators called from Python push no Undo step, so the
import leaves no history. An animated object's place is not compared, only its size: where it sits depends on the
frame, on each format's curve sampling and on which action the importer activates. A file the importer can't read is a ``reimport_failed``
finding, never a failed export. The web export's merge joins static objects, so for a merged web export objects are
compared as one whole by their bounds, and one by one only where they arrived by name.

Blender's importer stands in for the engine's: a finding here is a fact about the file, not about the engine's
renderer.

Engine-link saves
=================

After a linked asset is saved (``engine_link_saves``), ``engine_preflight_link`` starts a background Angelo on
the saved file once the engine has the asset: after the write-back finished for a .glb or .fbx (the comparison
reads the asset the write-back wrote), at once for a .blend, which the engine converts itself (the child first
exports it with the engine's own Blender profile to a private temporary folder it removes after). One runs at a
time; the newest save during one runs once it ends, and covers the queued one it replaces (whose folder is removed).
A write-back that ends while a newer one runs starts none: the newer one's covers it. A preflight or write-back
whose thread can't start leaves its runner idle (the next save starts one) and is reported: the preflight's reason
goes to the link's error, and the write-back ends as unwritten (``write_failed`` to the editors). The report is
written beside the working file, ``<project>/.angelo/<relative path>.preflight.json`` (through a new private file,
never through a
link), and ``engine_preflight.link_report(asset)`` reads it: the engine-link agent tool returns it. The engine never
hears of it and the scene is never changed.

Each report names the save it describes: ``scene_version`` is the link's scene version the save covered (the
``saved`` message's ``scene_version``); for a .glb or .fbx asset ``target_sha256`` is the SHA-256 of the asset file it
read back, byte for byte the one the write-back wrote; ``status`` is ``done``, or ``failed`` with ``error`` when the
child couldn't run; ``save_key`` (every report) is the key the link passed for the save, written unchanged (the
exact key: None until the link numbers its saves); ``blend_identity`` (every report) is the saved .blend's identity when the save was made
(``export_snapshot.identity``: device, inode, size, modification time in nanoseconds), which tells every save apart,
two saves at the same scene version and saves in other runs too; ``saved_at`` (every report) is the wall time it was written, since the link's versions restart
with each launch: a report from an earlier run is older than any save of this one. The asset the write-back wrote is read from a private copy made when that write-back finished, so the next
write-back over it never changes what is compared. The saved .blend's descriptor-origin witness reaches the runner
unchanged and is settled on that thread, so a coarse-volume step never blocks the UI or substitutes a later path stat.
The child requires the native identity of the file Blender actually loaded (``_bpy._angelo_saved.read_file``) and the
current path identity to match that save before reporting or exporting. Missing descriptor evidence or either mismatch
writes nothing: a restored path cannot vouch for a different loaded scene. So a report whose
``scene_version`` is older than a save's is not that save's report: it is still running or covered by a newer one.

For agents and apps
===================

Tool results
------------

A scene can break one rule many times, and each finding repeats its rule's explanation, fix and engine source. Tool
results (the check, the fix's remaining findings, the web export's preflight and the read-back observation) send
them grouped by rule (``engine_findings.summarize``): per rule its code, severity, count (when more than one), the
``looked_for``, fix text, versions and engine source its findings share, ``fixable`` (its typed fix) when any has
one, and three examples (subject, message, what was found), with ``more`` counting the rest; only when some but not
all have the fix, ``fixable_count`` and ``fixable`` on each example. Nothing is sent empty, so a report of a finding
or two costs about what the findings did, and a clean one less. ``angelo_engine_check`` with ``code`` returns that rule's findings in full, the first 100
(more than one fix call takes; ``more`` counts the rest), its source and versions once. Stored reports
(``link_report``) keep every finding. Measured in "Measurements".

Both tools are deferred (``plan_kinds.DEFERRED_TOOLS``): a turn that never uses them sends only their names.
``angelo_engine_check`` is at the Read level for MCP apps, headless too; ``angelo_engine_fix`` is at Edit and only
with a window (its card needs the person; ``external_clients.WINDOW_ONLY_EDITS``). Plain-data entry points for other
modules: ``engine_preflight.check(window, target, fmt="AUTO", engine_version=None, selection_only=False)``,
``engine_preflight.run`` (with the facts and options), ``engine_preflight.link_report(asset)`` and
``engine_preflight.TARGET_FOR_ENGINE``. Results carry object, action and image names, never a local path.

Validation
==========

``test_native_engine_preflight`` (host) checks that each rule fires for exactly the targets and formats its source
covers, every finding's shape, the name conventions against the engines' own cases (Godot 4.0's reading against 4.1
and later), the tools' arguments, their families and MCP levels, and that no preflight module runs by itself.
``test_blender_engine_fix_gui`` (GUI) draws the card for two fixes in the default Director view, clicks Approve, and
checks that one Ctrl+Z over the 3D view restores both and Ctrl+Shift+Z applies them again.
``test_blender_engine_preflight`` (BACKGROUND) applies every fix through the host route and checks one Undo step
restores them, the stale and unsafe refusals, real .glb and .fbx round trips that leave nothing behind, and the
cases an independent break-it review reproduced (a read-back in Edit and Sculpt Mode, a collision empty other
objects point at, an animated or scaled child under Apply, a fix that fails part way, a mask through a Mix factor,
a rename beside another fix of the same armature, an armature modifier that is off, any numbered name).

Held out (2026-11-02, Angelo 1.0.8's build b59129e3eb2 with this add-on): assets and rigs none of the rules were
written against, each checked for every target and format, exported with the target's profile and read back, and
the fixable ones fixed and read back again.

=====================  ====================================  ================================================
Asset                  Rig                                   What the preflight found (true unless noted)
=====================  ====================================  ================================================
Mixamo Idle, Walking   Mixamo (FBX, cm units)                armature at scale 0.01 and 90°: every target;
                                                             animated, so no fix offered; FBX leaf bones
                                                             read back (``_end``); the same rigs without their
                                                             clip: Apply offered, finding gone, clean read-back
Furina (Sketchfab)     a converted anime rig under a         the armature turned 90° in the world: every
                       rotated root empty                    target; the .glb read back with five skinned
                                                             meshes turned (confirmed from the file by the
                                                             spec's skinning sum); extra root for Unreal FBX
Rigify human           Rigify (generated, automatic          10 root bones for Unreal; up to 9 influences
                       weights)                              (0.166 dropped by four-bone engines; limit fixed
                                                             and read back clean); 221 hidden widget objects
                                                             written by every engine profile; 346 FBX leaf
                                                             bones; widgets arrive as empties (no faces)
Lumine, burned cars,   none / static kit and scanned props   nothing, every target, clean read-back
3 scanned props
=====================  ====================================  ================================================

Engine versions held out: Godot 4.0.4, 4.1, 4.2.2 and 4.4.1's ``resource_importer_scene.cpp`` against 4.7.2's (4.0
reads a suffix differently: ``suffix_numbered``); Godot 4.2.2 and 4.4.1's ``gltf_document.cpp`` (8 bones, no empty
draw type); Unity 6000.2.6f2's ``Unity-BlenderToFBX.py`` (identical to 6000.5's). Unreal: only 5.8's Interchange
source was available.

Measurements
============

``bench_engine_preflight.py`` builds a production-size scene (880 objects, 856,264 triangles, 40 materials with
1024² textures, two skinned characters of 30,000 vertices) and alternates without and with the preflight after an
untimed warm-up of each, 5 pairs. Mac 1 (M4 Pro), Angelo 1.0.8 build b59129e3eb2 with this add-on, 2026-11-02;
the machine was shared (load average 14 to 17 from other work); the spread is the min to max of 5.

=====================================  ==================  ==================  ========================================
Path                                   Without (median)    With (median)       What it costs
=====================================  ==================  ==================  ========================================
``angelo_web_export``, what the        3,889 ms            4,164 ms            +0.28 s: rules 0.21 s, the scene's
agent waits for                        (3,835 to 3,928)    (4,145 to 4,324)    description for the read-back
Web export read-back, afterwards       none                2,503 ms in a       background, after the tool answered
                                                           background Angelo   (1.86 s of it the comparison)
Engine-link save, what the engine      3,354 ms            3,354 ms            0: the write-back is unchanged
waits for (the write-back)             (3,346 to 3,384)
Engine-link save, after the engine     none                3,493 ms in a       background, after the engine has the
has the asset                                              background Angelo   asset
Engine-link save, main thread          none                0.1 ms (0.1 to      starting the child: a folder check and
                                                           0.6, 50 saves)      a thread
Everything else                        none                none                no handler or draw callback; a timer only
                                                                               while a web read-back runs
=====================================  ==================  ==================  ========================================

The read-backs grow with the scene (they are imports); both run in a background Angelo, never on the path the agent
or the engine waits for, never in the live web preview and never on the everyday path.

The copy a read-back reads (``export_snapshot.take``, on the main thread once the file is written) is an APFS clone
on macOS: 0.11 ms for a 58.5 MB file and 0.14 ms for 3.8 MB (median of 15, Mac 2 M5 Max under the maintainer's load,
so contended); a plain copy there took 12.5 ms and 1.0 ms. On Windows it is the system's own copy (``CopyFile2``),
which clones the blocks on ReFS and Dev Drive volumes; elsewhere ``shutil.copyfile``. Measured with
``bench_export_snapshot.py`` on 2026-11-04 on a GCP c2-standard-60 (Windows Server 2022, NTFS on a 300 GB pd-ssd:
network-attached block storage, slower than the local SSD most people have, so the times are an upper estimate),
files just written as an export leaves them, median of 5 in alternating order; ``take`` with 1.0.9's module
(``shutil.copyfile``) between two runs with this one (``CopyFile2``), all three on one boot:

=========  ==========================  =========================  ==========================
File       1.0.9's take                take now (two runs)        A hard link (for scale)
=========  ==========================  =========================  ==========================
1 MB       6.3 ms                      6.1 and 6.6 ms             0.2 ms
10 MB      14.0 ms                     10.8 and 10.8 ms           0.2 ms
50 MB      37.3 ms                     24.4 and 24.3 ms           0.2 ms
200 MB     124.6 ms                    72.1 and 71.7 ms           0.2 ms
500 MB     294.6 ms                    160.9 and 161.7 ms         0.3 ms
=========  ==========================  =========================  ==========================

The production-size scene above writes a 58.5 MB .glb. The copy stays on the main thread: a copy in the background
would race the next export, which Windows cannot publish over a file that is open (module docstring of
``export_snapshot``); a hard link is free but shares later in-place writes, so it is no snapshot.

**Tool results** (``engine_findings``), compact JSON bytes of the check's result for each target, the full findings
with the earlier reading note against the grouped result, on scenes the preflight was never developed on: two Rigify
rigs other than the human (basic quadruped, wolf; generated), three Mixamo characters with clips (dance, boxing,
swimming), a downloaded .blend kit of 10 stones, three downloaded scans, and three generated kitbash scenes (props
duplicated with Shift+D and given materials at random from a palette, per object, per face and through object
slots; seeds 1 to 3). Five target and format pairs each, 60 results:

======================  =======================  ==============================  ===============================
Scene                   Findings per pair        Full, bytes                     Grouped, bytes
======================  =======================  ==============================  ===============================
Rigify wolf (305        1 to 21                  1,128 to 14,924                 1,130 to 3,717
objects)
Rigify quadruped        1 to 15                  1,126 to 10,807                 1,128 to 3,632
Stones .blend kit       20                       12,509 to 12,670                1,455 to 1,464
Kitbash scenes          4 to 7                   2,881 to 4,798                  1,436 to 1,467
Mixamo characters       1 to 3                   1,066 to 2,227                  1,068 to 2,222
Scans (no findings)     0                        350 to 353                      183 to 186
All 60                                           212,911                         79,549
======================  =======================  ==============================  ===============================

The largest growth on any result is 7 bytes (a result of two findings of different rules); 1,000 findings of two
rules (``test_native_engine_preflight``) are 599,500 bytes in full and 2,112 grouped, and 1,002 cost the same.
Applying every ``link_data`` fix on the three kitbash scenes left every object's faces with the materials they had
(54 to 24, 24 to 10 and 40 to 18 meshes); the earlier key linked twins across materials there and changed 9, 2 and
11 objects' materials.

Limits
======

* The comparison uses Blender's importers; an engine's own importer may still differ (Unreal's Interchange options,
  Unity's import settings such as Bake Axis Conversion, which only the engine records).
* ``.blend`` assets are compared through the engine's Blender profile, as the engine converts them; Unreal has no
  .blend kind.
* Weights are compared on a sample (20,000 vertices per mesh) and influences counted on up to 250,000 vertices over
  all meshes, every n-th past that.
* No rule changes an engine's import settings or project files: Angelo never edits ``project.godot``, ``.uproject``,
  ``Packages/manifest.json`` or ``.import`` and ``.meta`` files.
* The link child's check that the .blend is still the one saved compares its identity (device, inode, size,
  modification time): the stat taken when the save was made, settled on the preflight's own thread once the
  change clock has passed its stamp (``export_snapshot.settled_identity``, ``checkpoint_policy.rst`` "Differences
  and limits"), since on a volume with coarse times (FAT and exFAT, HFS+, a network share) Blender's main thread
  cannot wait out that step. A later same-size save then always changes it; one made inside the step of the
  stamp itself, before the settle, keeps it, so that child can describe the newer file under the earlier save's
  ``save_key``, and the newer save's own report follows.
