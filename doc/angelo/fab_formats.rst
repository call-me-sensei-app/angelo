.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

Fab formats, unpacking and the converter
========================================

Fab listings ship each asset in several formats. Angelo adds a listing from any row of
its Download dialog in a format it reads: a converted GLB, FBX, ``.blend``, OBJ, glTF,
USD/USDZ, or a PBR texture set (Megascans surfaces and decals). How a row reaches the
scene is the same for all of them. The account, entitlement, approval and Undo contracts
are in :doc:`fab`.

Which formats, and in what order
--------------------------------

Fab's public listing data was sampled on 2026-11-11: the first page of 18 searches
across categories, 427 model listings (107 free), relevance-sorted, so a biased sample
and not a census. Listings Angelo could add, cumulatively:

====================================  =========
Formats                               Listings
====================================  =========
Converted GLB only (Angelo 1.0.10)    127 (30%)
+ FBX                                 189 (44%)
+ ``.blend``                          199 (47%)
+ OBJ, glTF, USD                      200 (47%)
====================================  =========

The other 227 listings carry only engine formats (Unreal, Unity) and are not offered.
Every Megascans surface (44 of 72 materials) and decal (51 of 51) carries a texture set.
Sellers' FBX, OBJ, glTF and ``.blend`` files are usually zips with their textures. The
largest file of a format is above the 256 MiB download limit for 41 of 170 FBX listings,
so a lower quality is often the one to add.

One format table
----------------

``fab_formats.FORMATS`` is the only place a format is described: the Fab labels that name
it (a group's name such as ``FBX``, ``Blender`` or ``Texture Set``, or a converted file's
badge such as ``glb`` or ``usdz``), the content kinds of its model file, whether it may
arrive as a zip or as one bare file, its side files (``.mtl``, ``.bin``, USD layers), its
importer, and whether it becomes a model or a material. The Download dialog observation,
the choices, the cache, the archive filter and the converter all read this table. No
listing, file name or seller is special-cased.

* **Detection by content.** ``sniff`` reads a file's first bytes: GLB, binary FBX,
  ``.blend`` (also gzip and Zstandard compressed), USDC, USDA, USDZ, zip, glTF JSON, OBJ
  and MTL text, and PNG, JPEG, WebP, EXR, TIFF, Radiance HDR, BMP and TGA images (TGA has
  no magic, so every header field must be a legal image description). A name only says
  which row a person picked. ASCII FBX is refused with its reason, since Blender cannot
  read it.
* **Choice order.** The choices put the cheapest path first: a converted GLB (imported
  directly), then the table's order. Within a format, Fab's own order is kept.
* **Model files in an archive.** Every file of the format's model kinds is imported. Level
  of detail siblings (``Rock_LOD0`` … ``Rock_LOD3``) keep only the most detailed. A pack
  imports up to 32 models; a larger pack is refused, never cut short. USD layers that
  another kept layer composes are not opened as roots.

Unpacking
---------

``fab_archive.extract`` writes into a new private folder and refuses, before writing a
byte, any archive that:

* names a file outside the folder: absolute paths, drive or UNC prefixes, ``..``,
  backslashes, colons (Windows streams), control or format characters, Windows device
  names, trailing dots or spaces, or more than 200 characters per part or 400 in all;
* holds two names that one case-insensitive, Unicode-normalising file system would merge,
  or uses one name for both a file and a folder;
* holds a symbolic link, device, socket or pipe, or an encrypted entry. Only an entry's
  Unix type bits count: an entry with permission bits and no type, as Python's own
  ``zipfile`` and other tools write it, is a regular file. Links are defused regardless,
  since the extractor never creates one;
* has more than 4,096 entries, more than 1 GiB declared in all, or an entry above 1 MiB
  that expands more than 200 times (zip bombs, including entries that share compressed
  data), or a PNG, JPEG or WebP texture above 16,384 pixels a side.

Only the format's model files, side files and images are written. A glTF's buffers have no
signature to recognise, so its side files are the ones its own buffer and image URIs name
(percent-decoded, relative to the ``.gltf``). Read-me files, other
formats and nested archives stay packed and are counted. Writing uses exclusive creation
without following links. The reader stops at each entry's declared size, and the CRC must
match. A damaged entry, a stop or any refusal removes the folder.

The converter
-------------

A converted GLB is imported in the app as before. Every other format is read by a child
process, never by the app. ``fab_convert`` starts Angelo itself with ``--background
--factory-startup --disable-autoexec --offline-mode -noaudio``. Its environment keeps
only ``PATH`` and the locale; home, temporary folder and Blender's user configuration,
scripts, data files and extensions folders are inside its work folder, so no add-on or
startup script of the person loads and no provider key is visible. On macOS it runs
under ``sandbox-exec``, denied by default: it may run the app's own program, read the
system's libraries and data (``/System``, ``/usr/lib``, ``/usr/share``, time zones,
``/dev``), the app and its work folder, write only its work folder, and has no network.
Mach lookups stay open, since the system's frameworks start through them. On Windows and
Linux it is isolated as a process only. Paths reach the sandbox profile only as ``-D``
parameters. The child runs at lowered priority. It has a deadline of two minutes plus a
second per unpacked megabyte (at most 15 minutes) and is killed when the person stops the
import. The child keeps the same deadline itself and ends when its parent is gone
(``fab_convert_guard.watchdog``, started before it reads anything). On Windows it also
sits in a Job Object that ends it when the app's handle closes, so an app that quits or
crashes takes the converter with it.

What a child can reach. On every platform it runs no Python from the download, has no
network client and is offline, and writes its answer only into its work folder, where the
app reads a size-capped, type-checked result. No texture from outside the download
reaches the scene (below). On macOS the sandbox also stops every read outside the
system, the app and the work folder, and every write outside the work folder.

Only a path shaped like a local file is ever opened, on any platform. On Windows, merely
opening a path that names another computer (``\\host\share\x.png``, ``\\?\UNC\…``, the
NT ``\??\`` prefix) tries SMB sign-in and sends the person's NTLM hash to that host. A
device name opens a device. Rather than list such forms, ``untrusted_paths.local_form``
allows exactly the shapes of a local file path:

* relative, drive-absolute (``C:/…``, also drive-relative ``C:x``) or from the POSIX root
  (one ``/``, written with forward slashes; a drive-less root in Windows form such as
  ``\Device\…`` or ``\GLOBALROOT\…`` is NT-native and refused);
* no control characters and none of Windows' reserved characters ``< > " | ? *``, which
  also spell ``\\?\`` and ``\??\`` (the UDIM tile tokens ``<UDIM>`` and ``<UVTILE>`` are
  the one allowed use of ``<``/``>``);
* no colon except a drive letter's, so no streams, device colons or URLs;
* no part that is a Windows device name (CON, NUL, COM1–9, COM¹–³, LPT…), with or without
  an extension or trailing dots and spaces;
* no surrounding Unicode whitespace, and its NFKC form passing the same checks (fullwidth
  ＼ ／ ： become ``\ / :``), so no reader's trimming or best-fit conversion turns an
  allowed text into another shape;
* none of the slash and colon lookalikes that no normalisation maps (``∕ ∖ ⧵ ∶ ⁄ ⧸ ⧹``),
  which a best-fit code page conversion would turn into separators or a colon.

Any other path refuses the download before anything opens or resolves it. The check
applies to FBX texture paths and OBJ ``mtllib`` and MTL map paths, which the app checks
before the child starts (``fab_convert.prescan``); the Team Library's FBX check uses the
same rule. The FBX check follows the importer's own choice of path
(``import_fbx.blen_read_texture_image``): ``RelativeFilename``, else ``FileName``, else
``Filename``, and with none of them non-empty the Texture or Video element's name, which the
importer then opens as the path. OBJ lines are read as Blender's OBJ reader reads them: a
backslash followed only by blanks (bytes up to space) to the end of the line becomes one
space and the next line continues the statement; any other backslash stays. It applies to every USD asset path as written, read unresolved with
``UsdUtils.ModifyAssetPaths`` layer by layer (sublayers, references, payloads, variants,
clips, shader inputs, UDIM patterns, MaterialX documents) before
``ComputeAllDependencies`` resolves anything. It covers glTF URIs, which
``gltf_checks`` refuses lexically when they leave the model's folder. It also covers
every path the child itself examines, such as a ``.blend``'s images and fonts. Where an
allowed path leads is checked afterwards: a crafted file can make an importer open only a
local file the person can read. An FBX or OBJ texture path outside the download is only
checked for existence by its importer, is never decoded or packed, and the image is
dropped before the result is written. The child has no channel to send anything
anywhere. The child's own bugs (a crash, a hang) end the child, not
the app.

The child (``fab_convert_child``, ``fab_convert_clean``, ``fab_convert_material``; nothing
in the add-on imports them):

* imports with Blender's own importer: FBX (image search off; only binary FBX 7.1 to 7.7
  reaches it, since ASCII FBX is refused when the file is recognised, as Blender cannot read
  it; the app's path-only walk uses the same record layout as Blender's FBX parser and
  checks every ``FileName``, ``Filename`` and ``RelativeFilename`` node at any depth, so
  where the two would disagree the walk refuses the file), OBJ, glTF (every URI must
  stay inside its folder, as ``gltf_checks`` reads it; a file with morph targets is welded,
  as every Angelo glTF import is, ``gltf_morph.import_options``), USD (cameras off; every
  dependency ``UsdUtils.ComputeAllDependencies`` finds, including sublayers, references,
  payloads, value clips, shader asset paths and UDIM sets, must resolve inside the folder,
  or the file is refused before the importer reads it; one it cannot resolve is refused
  when its text names a URL, ``..`` or a root or drive in either system's form, ``/x``,
  ``\x``, ``C:\x`` or ``C:x``, ``fab_convert_guard.names_a_root``, whichever system runs
  the child), or ``.blend``, appended scene by
  scene with nothing run. A ``.blend`` that links other ``.blend`` files is refused
  before any is read;
* keeps the asset's objects (geometry, armatures, lattices, the empties that hold or
  instance them, every ancestor, and lights carried by a kept object). It leaves out
  cameras, light probes, speakers, volumes (external grids) and the world. From a
  ``.blend`` it also leaves out objects in collections the author excluded and objects
  disabled in viewports, and it applies the scene's unit scale. The objects are gathered
  into one collection named for the listing;
* removes Text blocks, every driver whose expression needs Python (Blender's simple
  expressions and the average, sum, minimum and maximum kinds stay, since they never call
  Python), script (OSL) nodes, Mesh Cache and Mesh Sequence Cache modifiers, cache
  files, movie clips, sounds and volumes;
* packs every texture from inside the folder. A path elsewhere (an author's own disk) is
  relinked by file name to the one image of that name in the download; an image with no
  such file, a movie or a sequence is dropped and counted. An image an importer packed by
  itself stays only when it holds embedded data or its source, resolved with links
  followed, is in the download or the child's own temporary folder (where Blender's USD
  reader copies a USDZ package's textures). Any other source, absolute, ``..`` or a link
  leading out, is dropped and counted (``outside_removed``) and logged. Packed data an
  importer took from the downloaded file itself is kept whatever author path it records:
  the FBX importer's embedded ``Content`` (it never packs a file from disk) and a
  ``.blend``'s own packed images (appending reads no other file: linked libraries are
  refused before it, and appended images that are not packed are only paths). A ``.blend``'s
  packed fonts are kept the same way, and its sounds, volumes, movie clips and cache files
  are removed. The USD and glTF importers get no such allowance. This source check comes
  first for every image holding any packed data, before its source or layout is looked
  at, so nothing bypasses it; a generated image still holding bytes from before its Source was
  set back to Generated keeps its pattern and loses only those bytes (removed without
  writing a file), and packed data on a viewer image is dropped. One texture never refuses a download: a packed movie or image
  sequence is dropped like an unpacked one, a tiled image packed only in part loses the
  tiles that have no packed data (counted), and a multiview image keeps its first view as a
  plain packed image with its colour space and alpha mode, copied only within the Team
  Library's image caps (16384 pixels a side, 8192 × 8192 in all). A tiled image is dropped
  when its tile set is not one Blender makes (more than 1000 tiles, a repeated number or one
  outside 1001–2000) or its path pattern has more than one ``<UDIM>``/``<UVTILE>`` token or
  any ``%``: Blender formats that path with one tile number, so such a pattern is undefined
  there. A tiled (UDIM or UVTILE) image that is not packed has each tile's own file checked:
  inside the download, or relinked by name to the download folder holding most of its
  tiles. Tiles with no file are removed and the rest are packed. A font from inside the
  folder is packed, any other falls back to Blender's own;
* gives meshes whose materials use no texture the material of the loose texture set that
  came with them (a Megascans FBX zip ships its maps beside the mesh);
* writes only the asset with ``bpy.data.libraries.write``: one asset-marked collection
  (or one material for a texture set) and what it uses. Objects are linked into the
  collection, never moved, so a collection that a kept empty instances keeps its members.
  Such an instanced collection comes along whole, author alternatives included. It passes
  the same scrubbing and the app's inspection, which cover every datablock in the file,
  not only the kept objects.

The result file is at most 64 KiB. Only known fields of known types are read. An error is
one of a closed set of codes, each shown as a fixed sentence, and the child's log never
reaches the person or a model; after a failure its last 64 KiB stay beside the download
as ``convert-failed.log`` for support, replaced by the next failure, with the person's
home folder written as ``~`` however the log spells it (resolved, ``USERPROFILE``, the
Windows short name; single, doubled or mixed separators; any case where the file system
ignores case), followed by anything but a letter, digit, ``_`` or ``-`` (punctuation
included), never a longer name that only starts the same. It can still hold text the
downloaded file chose and other local paths: anything that ever shows it must treat it as untrusted text, never as model
input. The clean ``.blend`` (``asset.blend``) is cached beside the
download with a receipt: download digest, converter version, size and digest. A repeated
add reuses it without starting the converter. The unpacked files are removed either way.

In the app, the clean file goes through the Essentials append path
(``blender_asset_import.apply_item``) inside the native asset transaction of :doc:`fab`:
it is inspected again in temporary data (no Text block, linked library, cache file, movie
clip, volume, unpacked image or font, script node or driver that needs Python), appended,
stood at the drop point or 3D cursor, sized like the scene's objects when it is more than
twice off (``scene_fit``), stamped with Fab provenance, and published as one Undo step.
The collection's asset mark is cleared once it is placed.

Texture sets
------------

A texture set becomes one Principled material (``fab_surface`` decides the roles,
``fab_convert_material`` wires them):

* **Names first.** The map-type words every major texture source writes, as a whole word
  of the file stem, nearest the end winning: base colour (albedo, basecolor, diffuse,
  color), normal, roughness, gloss, metalness, ambient occlusion, displacement or height,
  opacity, specular, cavity, translucency, emission, bump, and channel-packed ORM, ARM,
  MRA and RMA maps. The one-letter suffixes of Unreal's texture naming (``T_Name_D``,
  ``_N``, ``_R``…) count only on a ``T_`` file. Resolution and level words (``2k``,
  ``4096``, ``LOD0``) are skipped.
* **Pixels check and complete.** A small sample of each map says whether it is grey,
  whether it is a tangent-space normal map (unit vectors around +Z), whether its alpha
  varies and whether it is near binary. A map named normal that is not one is not used as
  one. A scalar-named map in colour is not trusted ("Metal_Plate_2K" names a material,
  not a metalness map). An unnamed normal-like map is the normal. An unnamed colour map is
  the base colour when it is the only one. Of two maps for one role, the larger is used.
* **OpenGL or DirectX normals.** The curl test decides: a real height field's gradient
  has no curl, so the green reading that leaves the least curl is right. When the test is
  undecided (a flat or striped map), the name's ``gl``/``dx`` word decides, else OpenGL.
* **Wiring.** Base colour in sRGB, times ambient occlusion. The rest as data: roughness,
  or one minus gloss; metalness; packed maps split into their roles; normals through a
  Normal Map node (DirectX with green inverted first); a bump map when there is no normal
  map; opacity, or the base colour's own varying alpha, into Alpha; emission; displacement
  as bump through a Displacement node (0.02 m). A Mapping node comes first so tiling
  stays editable. Cavity, specular and translucency are left out and counted.

The material goes on the editable meshes selected when the import starts. With none
selected, it is kept in the file (fake user) and marked as an asset, so it appears in the
Asset Browser's Current File library.

Agent tools (toolset 54)
------------------------

``angelo_library_search`` with ``source="fab"`` returns each variant's ``format`` (``glb``,
``fbx``, ``blend``, ``obj``, ``gltf``, ``usd`` or ``surface``) and ``kind`` (model or
material). ``angelo_library_import`` takes the exact ``variant_id`` as before. ``objects``
now applies to a Fab texture set, with the Angelo Library's texture-set semantics: the
named meshes, else the selected meshes. With neither, the material joins the file's asset
library. It is refused for a model variant. The meshes are chosen when the call starts and
found again by identity at import; a deleted or changed mesh refuses the import. Approval,
the paid-suggestion policy and the account rechecks are unchanged (:doc:`fab`).

Latency and the main thread
---------------------------

Downloading, unpacking and converting run on the Fab worker. The main thread only appends
the clean ``.blend``, which holds packed images and no importer work, so its cost is the
append and placement. ``test_blender_fab_formats.py`` records per format the time from the
downloaded file to visible objects, the main thread's part of it, the worst gap between
main-thread ticks during conversion, and the time to prepare the same file again from the
cached conversion.

Validation boundaries
---------------------

Host tests (``test_native_fab_formats``, ``_fab_archive``, ``_fab_surface``,
``_fab_convert``, ``_fab_listing_formats``) use generated bytes only. The texture-set
rules are checked on naming conventions they were not developed on (Poly Haven,
Substance, ambientCG, Unreal letters). The in-app fixture uses samples written by
Blender's own exporters, plus real Fab downloads when ``ANGELO_QA_FAB_SAMPLES`` lists
them (local files, never committed). The generated samples alone cover both texture-set
paths: a four-map set in full words goes into the asset library, and a seven-channel set
in abbreviated names (base colour, OpenGL normal, roughness, metallic, ambient occlusion,
height and a cut-out mask, as JPEG and PNG) goes on the selected mesh, where every map
must be classified and reach the material output. Real texture sets alternate between
the two, the first on the mesh. Neither exercises the Fab account, its browser or
the dialog click; those need a signed-in Fab profile. The Download dialog's row shapes
come from a signed-in observation on 2026-11-11. The OS sandbox is macOS only.
