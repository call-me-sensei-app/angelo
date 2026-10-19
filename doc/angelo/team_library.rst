.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Team library and IPs
====================

While a person is signed in to an Angelo team (``ai_providers.rst``, "Team
account and team connections"), the agent can work with the team's shared
media library and IPs: search and read items, see their thumbnails, import an
asset into the scene, save a model, render or image back with metadata, and
read IPs (worlds and franchises), their entities, versions, variants and
references, resolving entities into generation prompts. The data lives in the
Angelo console; Angelo reads and writes it through the console's REST API as
the signed-in person, with the same visibility and permission rules as the
console itself.

Contract followed: the console's library and IP API and its MCP tool set
(library items in a team or personal space, IP entities with versions and
variants, references with review status, character readiness and the
``resolve`` generation gate), including the current console's additions
(upload checksums enforced by storage, content checked on completion, item
file names and verified digests, names instead of email addresses, paged IP
list and detail). Both console generations are served: see "Two console
generations" below.

Module ownership
----------------

===============================  ================================================================
Module                           Responsibility
===============================  ================================================================
``team_library_spec.py``         The ``angelo_team_*`` tool schemas, exact validation, the family's
                                 guidance. No Blender dependency.
``team_library_access.py``       Whether the family is offered (signed in with the console scope)
                                 and the request's allowed tool set. No Blender dependency.
``team_library_client.py``       Console API calls with the person's access token; refusals in the
                                 person's terms. Worker threads only.
``team_library_transfer.py``     Downloads from and uploads to presigned storage links (never with
                                 credentials). Worker threads only.
``team_library_files.py``        The format allow-list, size caps, decompression bounds and
                                 traversal checks for downloaded files. Worker threads only.
``team_library_results.py``      Compact, link-free results for models; the known-items cache that
                                 approval cards read.
``team_library_approvals.py``    Approval cards for import and save.
``team_library_host.py``         Main-thread binding: preconditions, one worker per call
                                 (``TeamPending``), temporary folders.
``team_library_import.py``       Blender import into the scene or Media Studio, the USD layer check,
                                 the safety scrub, Undo and read-back. Main thread.
``team_library_save.py``         Preparing an upload: Studio output, objects as GLB with a WebP
                                 thumbnail, a view image or an attachment. Main thread.
===============================  ================================================================

Tools
-----

=================================  ==========================================================  ==========
Tool                               What it does                                                Approval
=================================  ==========================================================  ==========
``angelo_team_library_search``     Text, kind, tag and IP-entity search of the team or the     none
                                   personal space, newest first, cursor paging (1-25 items)
``angelo_team_library_get``        One item: file facts, provenance, the IPs that use it;      none
                                   ``preview`` adds its thumbnail as an image
``angelo_team_ips_list``           The team's (or personal) IPs with entity counts, paged      none
``angelo_team_ip_get``             One IP and its entities by kind then name (kind filter,     none
                                   paged)
``angelo_team_ip_entity``          Resolved fields, prompt fragments, references, variants,    none
                                   the last ten versions and, for characters, readiness
``angelo_team_ips_resolve``        Prompt fragments, ordered reference items and the           none
                                   fingerprint for 1-20 entities (optionally pinned versions)
``angelo_team_library_import``     Download one item and import it (models into the scene;     always
                                   images as a scene reference image or a Studio asset)
``angelo_team_library_save``       Upload a Studio output, scene objects as GLB, an            always
                                   ``angelo_view`` image or a chat attachment, with title,
                                   description, tags and provenance
=================================  ==========================================================  ==========

* **When they exist.** The family is in a request only while the team account
  is signed in and its token carries the console scope ``api``
  (``team_library_access``; library and IPs need no seat). Signed out, the
  request's ``allowed_tools`` leaves the family out, so the Claude plan schema,
  the Codex tools, the Codex plan schema and voice tools are byte-identical to
  a build without it (``test_native_team_library_tools``), and
  ``angelo_agent_capabilities`` neither lists nor describes it. The client is
  the last gate: without a signed-in account every call answers "Sign in to
  your Angelo team…". A chat started before signing in sees the family at its
  next request on Claude; a Codex thread keeps the tools it started with, so
  the family appears in the next new chat.
* **Deferred.** The family is deferred like Media Studio's: a Claude plan names
  the eight tools only (exact schemas and ``TEAM_GUIDANCE`` come with a
  capabilities lookup), Codex threads declare them with ``deferLoading``. Codex
  sends a model no deferred tool at all (codex-cli 0.160, measured), so a Codex
  thread that declares the family also names it in its instructions' list of
  ``more__*`` families (``agent_guidance.codex_family_list``); other threads'
  instructions are unchanged. Voice threads never declare the family
  (``tool_specs.dynamic_tools`` declares every tool in full; the family would
  add about 5.7 KB to every voice request). Collaborators and the external CLI
  never get it.
* **Never automatic.** Import and save are network and file effects: they are
  approval-gated tools, never typed native edits, never in ``BOUNDED_MUTATIONS``
  (so Auto still asks), refused in read-only chats, and never retried.
  Reads need no approval.

Behaviour
---------

**Reads** run on a worker thread (``TeamPending``); the main thread only polls.
Results carry ids, titles, kinds, file facts, tags, provenance and IP links,
never a download, thumbnail or reference URL (they are short-lived
capabilities to private files and no model needs them). People are named as
"you", by name, or "a teammate" when the console has no name (it would
otherwise show an email address). Descriptions, prompts, field values and
lists are cut to fixed sizes. Each item says where it can be imported
(``import_into``) or why it can't (``import_note``).

**Readiness and resolve.** ``angelo_team_ip_entity`` reports a character's
readiness in the console's own sentences ("Add at least one close-up of the
face."). ``angelo_team_ips_resolve`` passes the console's
``409 CHARACTER_NOT_READY`` through as a result with ``not_ready`` (per
requested entity, what is missing), a ``fix`` telling the person to add the
approved references in the console, and ``retry: false``; the guidance tells
the model not to work around it with other images or a plain prompt.
References come back as library items in generation order; importing an image
into ``STUDIO`` makes it a Media Studio asset that ``angelo_studio_generate``
takes as a reference (source ``ASSET``). Saving a result can record the
entity versions and library items used (provenance ``ipEntities``,
``referenceItemIds``).

**Import.** The approval card names the item from what an earlier result
showed (title, type, size, space, who added it); an item no result has shown is
refused with "Look the item up first", so a precondition never touches the
network on Blender's main thread. After approval a worker fetches the item
again, gets a download link, streams the file into a private temporary folder
(``tempfile.mkdtemp``, mode 0700) under a fixed name and checks it. On the main
thread the file is imported into a new collection "Team · <title>" under the
active collection (models), or becomes a packed reference image at the 3D
cursor facing the Front view (images), or a Media Studio asset (``STUDIO``,
images only). New objects are tagged ``angelo_team_item`` (the library item
id), which a later save turns into ``parentItemIds``. One Undo step ("Import
"<title>" from team library") removes the import; the read-back lists the new
objects with handles, types and dimensions, packed images, materials, what the
scrub removed, and the scene's change diff. A failed import removes everything
it created. The temporary folder is deleted when the call ends, is stopped or
fails.

**Two console generations.** The current console pages the IP list
(``limit``, ``cursor``, ``meta.nextCursor``) and an IP's entities (``kind``,
``limit``, ``cursor``; ``media=0`` leaves the gallery out). The earlier
console answers ``400`` to an IP-list ``limit`` and ignores the detail's
parameters. ``team_library_client`` asks with the parameters; a
first-page ``400`` (or a detail without ``meta.nextCursor``) falls back to the
whole answer, filtered and cut on the client, with a ``local:<offset>`` cursor
the tools pass back unchanged. A refused later page with a console cursor is
reported, never hidden by the fallback. Items carry ``fileName`` (the format of
an ``application/octet-stream`` model is in its extension, so an ``.stl`` is
refused before download) and ``sha256``; both are absent on the earlier
console, where the format is known only after download. People come as
``createdBy {userId, name, label}``; the earlier console's label can be an
email address, which results never pass on.

**Save.** The approval card says what is uploaded, under which title and tags,
and who will see it ("everyone in <team>" or "only you"). On the main thread
the file is prepared: a Studio output as stored (GLB models with the Studio
preview as thumbnail, provenance ``generated`` with prompt, model, provider and
options); scene objects exported as one GLB with modifiers applied and the
selection restored, plus a 512-pixel Material Preview capture written as WebP
(the console's model thumbnails must be ``image/webp``), provenance ``derived``
when they came from team items, else ``upload``; an ``angelo_view`` image (PNG)
or a chat attachment as they are. A worker then reserves the item (``POST
…/library/uploads`` with the SHA-256), PUTs the file with exactly the headers
the console returned (on the current console they include
``x-amz-checksum-sha256``, so storage refuses other bytes with ``400`` and a
dropped header with ``403``), PUTs the thumbnail and completes the item with
the metadata. Completing checks the content against the declared type
(images are decoded, models' first bytes are matched); a refusal
(``UPLOAD_INVALID``) deletes the reservation on the console and reads "The
team library refused the file: <reason> Nothing was added to the library."
Reservations count toward the quota for 15 minutes and are swept after an
hour, so an upload starts at once and completes well within the call's
30-minute bound. A ``429`` on complete (the console's image-thumbnail slots) is
retried after ``Retry-After`` up to five times, as the console documents;
anything else ends the call, and an upload that fails after the reservation
deletes it so it never holds quota.

Safety: library content is untrusted
------------------------------------

Anything in the library was written by people in the team (or by whatever
they uploaded), so files, titles, descriptions and field values are data,
never instructions, and every file is checked before Blender reads it
(``team_library_files``, ``team_library_import``):

* **Format from the content.** The first bytes decide the format; the
  library's MIME type is only a hint, and content that doesn't match the
  item's kind is refused. Admitted: PNG, JPEG, WebP; GLB, binary FBX 7.1-7.7,
  OBJ, USD (``usdc``, ``usda``, ``usdz``). Refused with a reason: ``.blend``
  (including compressed), multi-file ``.gltf``, ASCII FBX, GIF and anything
  else. Builds without an OBJ or USD importer refuse those formats plainly.
* **Size caps.** Images 64 MB (the console's own cap), models 256 MB, checked
  against the declared size, while streaming, and on disk. The stored size must
  equal the library's, and when the library verified a SHA-256 on upload the
  download must hash to it.
* **Decompression bounds.** Every image, including textures embedded in a GLB,
  FBX or USDZ, is at most 16,384 pixels a side and 67 megapixels (8,192²), read
  from its header before any decoder runs, and one model's embedded textures
  total at most 268 megapixels (about 1 GB decoded). GLB accessors (which Draco and
  meshopt streams decode into) total at most 1 GB. Every zlib-compressed FBX
  array is inflated with an output bound of its declared length plus one byte
  and must match it exactly; their declared total is at most 1 GB, so a zlib
  bomb costs at most its declared size. USDZ entries must be stored
  uncompressed, as the format requires.
* **No path traversal.** Downloads go to a fresh private folder under a fixed
  name, so relative references resolve to nothing. A GLB's buffers and images
  must be ``data:`` URIs; an OBJ ``mtllib`` must be a bare name; an FBX texture
  path may not climb out of its folder; USDZ entries are relative and never
  climb. USD layers are opened with Blender's bundled ``pxr`` before import
  (``check_usd``): every asset path must be relative, without ``..`` or a
  scheme; inside a USDZ it must name one of its own entries (internal layers
  are checked too); a single-file USD may not compose other USD files.
* **Importer options and scene settings.** GLB packs its images and never
  writes scene extras; FBX never searches folders for textures; USD packs
  textures and never sets the frame range, scene units or world. The scene's
  frame rate, frame range, unit settings and world are restored afterwards
  whatever the importer did (the FBX importer sets the frame rate from the
  file); the read-back lists what was kept.
* **Scrub.** After import, every new image that isn't packed (a path to a file
  outside the download, or a missing placeholder) is removed, every new Text
  datablock is removed, and scripted drivers are removed from every new
  datablock. Blender's script auto-run setting is never touched, and nothing
  from a file is executed.
* **Credentials.** Only ``TeamAccount.access_token()`` is used, sent as a
  Bearer header to the console origin only (verified TLS, loopback http only
  for a development console, no redirects, no proxies, bounded answers, one
  refresh on a 401). Storage links never receive an Authorization header,
  cookie or token, and an upload link that asks for one is refused. No token
  is logged or shown: console text is bounded and redacted
  (``provider_errors.sanitize``, plus the request's own token).
* **Refusals.** Signed out: sign in in Preferences > AI Providers. No console
  scope: sign out and in again. ``403``: the console's reason and "Ask a team
  admin if you need access." Quota full: ask a team admin to free space or add
  seats. ``404``: it doesn't exist or isn't visible to you (personal items and
  IPs are visible only to their owner). ``429``: busy, with the wait.
  Refusals that leave the scene unchanged carry a closed code
  (``refusal_codes.py``), so later imports in the same request stay open: a file
  these checks refuse, a file with no scene objects (its rollback verified) and an
  import refused before Blender reads the file (no 3D Viewport, an editing mode, the window closed) are
  ``UNSUPPORTED``; a refused storage link or a download that fails its size or
  checksum check is ``UNSUPPORTED``; a file over the size cap and a fifth
  concurrent call are ``LIMIT_EXCEEDED``; a transfer that fails in transit
  (network, HTTP refusal, early end) is ``EXPIRED`` and a stopped one
  ``CANCELLED``. Any other failure stays unclassified and raises the request's
  recovery barrier (``agent_execution.rst``, "Correction after a changed scene").
* **Scene claims.** Download, checks and transfers run on a worker without
  Blender data, so their failures report ``scene_changed: false``. On the main
  thread, a failed import into the scene says so only when it was refused before
  Blender changed anything or when its rollback is verified (nothing it created
  remains, the scene settings are restored and the scene signature matches the
  one taken before); otherwise it reports ``partial_changes_possible`` and asks
  the person to check the scene.

Latency per use
---------------

=====================================  ===================================================================
Use                                    Cost
=====================================  ===================================================================
Search, get, IP list, IP, entity,      One console request each, on a worker; no approval. The first use
resolve                                of the family in a session costs one capabilities lookup (deferred
                                       schemas), as for Media Studio.
Get with ``preview``                   Plus one storage GET of the thumbnail (at most 5 MB; console
                                       thumbnails are 512-pixel WebP).
Import                                 One approval, two console requests (item, link), one download
                                       (size over bandwidth), the file checks (an FBX's compressed arrays
                                       are inflated once, bounded) and Blender's importer on the main
                                       thread.
Save                                   One approval, the main-thread preparation (a GLB export and one
                                       512-pixel capture for objects), three console requests and one or
                                       two uploads.
=====================================  ===================================================================

Measured host overhead (the native suite against the in-process fake console on
loopback, full-feature build; wall time from the call to its answer
with the approval clicked at once, polling every 20 ms, so about 50 ms is the
floor): a read 55 ms; imports of a 2 KB GLB 55 ms, an 18 KB FBX with an
embedded texture 56 ms, OBJ 51 ms, USDA 51 ms, USDZ 63 ms, USDC 392 ms (the USD
library's first load); saving one object as GLB with its WebP thumbnail 213 ms;
saving a view 53 ms. Production adds the console's and storage's network time.

Prompt bytes
------------

Measured with ``tests/python/angelo_agent/codex_prompt_probe.py`` (codex-cli
0.160.0, ``gpt-6-astra``, unbilled fake endpoint; Claude figures are the
compact JSON of the plan schema):

==========================================  ===========  ===========  =========
Part                                        Signed out   Signed in    Change
==========================================  ===========  ===========  =========
Claude plan schema (all tools)              40,463 B     40,668 B     +205 B
Claude appended instructions                10,617 B     10,617 B     0
Codex chat thread, first request            86,690 B     86,898 B     +208 B
  of which the plan's response format       29,764 B     29,820 B     +56 B
  of which the instructions' family list                              +153 B
Codex ``additional tools`` declarations     32,477 B     32,477 B     0
Voice tools                                 93,995 B     93,995 B     0
==========================================  ===========  ===========  =========

Deferred tools aren't sent among Codex's ``additional tools``, so those
declarations don't change. Signed out (anyone without a team) is
byte-identical to a build without the family. Exact schemas cost bytes only
when the model looks them up (about 1-1.5 KB per tool).

Validation
----------

* Host (``run_host_tests.py``): ``test_native_team_library_files`` (crafted
  files: allow-list, caps, pixel bombs, GLB external references and accessor
  bounds, an FBX zlib bomb inflated at most 81 bytes, FBX traversal and
  embedded-texture bombs, OBJ ``mtllib``, USDZ entries);
  ``test_native_team_library_client`` (the fake console: paging, visibility,
  credential-free storage links, one refresh on 401, quota, role and readiness
  refusals, token redaction); ``test_native_team_library_tools`` (signed-out
  byte identity of every declaration, deferral, discovery hiding, Codex
  instructions, approvals, link-free results, and the host path through the
  real ``Controller``: worker reads without approval, import and save always
  approved and refused in read-only chats, hostile downloads refused before
  Blender, a failed upload deleting its reservation).
* Native GUI (``test_blender_team_library.py``, ``run_native_suites.py --gui``):
  Blender exports a textured cube as GLB, FBX, OBJ, USDC, USDA and USDZ and
  each is imported through the real Controller and ``BlenderExecutor`` path;
  Undo removes an import; a non-embedded FBX texture leaves no image; GLB scene
  extras never reach the scene; the FBX frame rate never changes the scene; USD
  layers reaching outside the file are refused; scripted drivers are removed;
  objects save as GLB with a WebP thumbnail and ``derived`` provenance, a view
  as PNG and a Studio asset with ``generated`` provenance; discovery hides the
  family while signed out. On the development build (no OBJ or USD importer)
  those formats are checked to be refused plainly.
* Live, read-only (``live_team_library_check.py``, run with Blender's Python
  outside Blender, ``--console <origin> --report <file>``): a person signs in
  through the console's allow screen in the system browser; the script then
  runs team and personal library search, the newest team item's details and
  thumbnail through its storage link (no credentials sent to storage), the
  team IP list, the most recently changed IP and, when it has one, its first
  character's readiness, and checks the shaped results for links and email
  addresses. Every request is a read. The sign-in stays in memory and is
  revoked at the end; the report holds counts, timings, field names and
  checks, never content, links or tokens.
* Live write (``live_team_library_write_check.py``, run by Angelo with
  ``--python`` and ``ANGELO_CONNECTIONS_DIR`` naming a profile whose team
  sign-in is already in the OS store, report in ``ANGELO_LIVE_REPORT``): through
  the real tools and approvals it saves a small cube as GLB to the team space
  (upload headers as the console returned them, the checksum header included),
  searches for it, reads it with its thumbnail, imports it into the scratch
  scene with its digest checked, then deletes it and confirms a ``404``. No
  provider is called. ``ANGELO_LIVE_FAKE_CONSOLE=1`` with
  ``ANGELO_DISABLE_KEYCHAIN=1`` runs the same steps against the in-process fake
  console.
* ``tests/python/angelo_agent/fake_team_library.py`` implements the library,
  IP and storage-link routes on the fake console with the console's envelope,
  cursors, codes and local-storage links.

Limits
------

* On the earlier console the IP list and detail come whole (the client pages
  them itself), and an IP whose detail exceeds ``team_http``'s 1 MB answer
  bound fails with a clear message; the current console pages both and leaves
  the gallery out. On the earlier console, items have no file name or verified
  digest.
* Video, audio and documents are listed and described but not imported.
  Generation from IP references goes through a Studio import of each reference
  image; ``angelo_studio_generate`` takes no library item directly.
* Saves don't submit an on-model QC review; that is a separate console action.

Toolset 31
----------

The family is model-visible only while the team account is signed in
(``team_library_access``): signed out, every thread's instructions, tool
declarations and plan schemas are byte-identical to toolset 30, for personal
and team connections alike. Signed in, any connection's threads declare the
family, so ``TOOLSET_VERSION`` is 31 (a Codex thread recorded under 30 starts
fresh with the bounded handoff; a Claude session resumes and re-initializes its
plan schema). Measured sizes:

============================================  ================  ===========
Part (short identifiers)                      Toolset 30        Signed in
============================================  ================  ===========
Codex instructions (OpenAI, plan / no plan)   18,381 / 17,432   +153 B
Codex tools, deferred (OpenAI connections)    59,418            +6,140 B
Codex tools, not deferred (third-party APIs)  93,725            +5,954 B
Codex plan output schema                      29,689            +56 B
Claude plan schema                            40,463            +205 B
Claude instructions, voice tools              unchanged         unchanged
============================================  ================  ===========

A team Codex connection (``codex_team``) sends exactly what the personal
OpenAI API connection sends; the team route changes no model-visible byte. On
OpenAI connections the family sits in the ``deferLoading`` namespace, so its
schemas load on demand; the IP paging fields (``cursor``) add 177 B to those
schemas and, on function-calling connections, to every signed-in request.
