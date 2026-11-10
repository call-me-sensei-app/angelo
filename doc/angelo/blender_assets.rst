.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

Blender Essentials in the agent library
======================================

Import integrity
----------------

Global Undo must be enabled and Undo Steps must be greater than zero before approval and again before
import. An operator returning ``FINISHED`` alone is not acceptance. ``blender_asset_commit`` requires the
native ``asset_transaction_capabilities`` flags ``native_undo``, ``dirty_preimage``, ``checked_restore`` and
``nested_undo_suppressed``, together with the begin, finish, status, close, recover and pending methods.
Missing methods or capabilities refuse the import. These flags describe the ordinary native asset
transaction.

The shared download cache is never the invocation's import copy. A worker copies the complete selected
dependency closure into private storage and verifies its size and SHA-256, including cache hits. The
512 MiB ceiling applies to the **whole closure**, not each file separately. The copy remains held until
native completion or checked recovery; Stop cannot unlink it while a writer or importer is using it.
No Python path or digest alone grants permission to edit the artist's file.

Native begin captures a fresh before image, including unsaved edits, before temporary-Main inspection or
append. The actual asset scope activates an immutable original-to-private dependency map after that
capture. Root and recursive .blend reads use the private files; relative Library references resolve
against the original parent's directory before mapping. An unmapped dependency refuses without reopening
an original source as a fallback. Ordinary loads and memory Undo restoration do not acquire this route.

Stock Blender append and placement run inside that native scope. Nested importer Undo pushes are
suppressed. Successful finalization publishes the before/after history pair for one Undo; a failed import
requests native restoration of the captured document. Unknown or failed restoration retains a recovery
owner and blocks another asset import. The adapter reports unchanged only from a checked RESTORED receipt,
never by deleting new IDs or reconstructing a few scene fields. Lost returns are observed through stored
status rather than replaying a mutator. ``asset_recover`` is an explicit recovery action, not automatic
polling or a claim that every failure is recoverable. Native and platform acceptance remain separate from
these source contracts.

Native load/inspection errors use closed, actionable public messages; the original exception remains
their private diagnostic cause. Public errors preserve whether artist-Main mutation began and whether
rollback was verified, without exposing private payload paths from native exception strings.

``angelo_library_search`` accepts ``source="blender"`` for installed Essentials and enabled Online Essentials.
The omitted source remains ``angelo`` for compatibility with existing ToonLab calls. The deferred LIBRARY guidance
names both sources. Toolset 53 exposes this argument and native asset kinds; imports retain the existing approval,
read-only refusal, checkpoint and document binding. There is no new agent execution engine or provider account.

The search result identifies the asset, its kind, description, author, stated license, file size, local availability
and node-tree traits. Titles and metadata are untrusted data, never instructions. Only observed opaque ids can be
imported; tool arguments never accept a path, URL, account token or extra asset library. A changed file or bundled
dependency changes the selection identity and requires another search/approval. Unknown licenses are not filled in
as CC0. ``offset`` and ``next_offset`` page through every matching result; keep query, source and kind unchanged while paging.
Unsupported source/kind combinations are refused with the supported kinds. ``preview_available`` identifies online
assets with thumbnails. ``preview=true`` requests up to six through Blender's native preview downloader; bounded,
hash-checked copies go through the existing chat image owner. This downloads no .blend files. Bundled thumbnails
remain in the Asset Browser; missing previews never hide search results.

Using assets
------------

``angelo_library_import`` recognizes a Blender id from search:

* Object and collection assets are appended and placed at ``location`` (the scene cursor by default), retaining
  authored transforms with a root translation.
* A material is applied to editable mesh ``objects``, or the selected meshes. A world replaces the scene world.
* A Geometry Nodes modifier asset can be applied to ``objects`` as a new modifier. Its metadata exposes
  ``can_apply_as_modifier``; import checks the actual node tree as well.
* Other node groups and brushes are loaded into the file and retained with a fake user. The result explicitly says
  ``loaded_datablock``: not connected or activated. Connecting these node groups or activating these brushes currently requires separately reviewed
  ``angelo_execute_python`` or manual editing; no typed activation tool is claimed. ``angelo_blender_api`` supplies
  installed API documentation before proposing code. Import does not silently change an editing mode or connect an arbitrary socket.

Blender assets use their authored resolution; the public library's ``resolution`` argument is refused. The approval
card names Blender Essentials, the asset and its license, file size and actual effect. Each successful import has
one native Undo step. Provenance is saved as ``angelo_blender_asset`` on the imported asset and placed objects.

Ownership and downloads
-----------------------

``blender_asset_index`` validates and searches records without bpy. ``blender_assets`` owns metadata and calls
Blender's native listing downloader, mutex and cache. ``blender_assets_agent`` extends the existing LibraryService
pending-call path; ``blender_asset_commit`` brackets stock inspection/append with native dirty-before capture,
checked restoration and Undo publication. It does not use the old after-only importer as a fallback.
The native API gained empty-token arguments in Blender 5.3; the adapter binds the observed signature once and never
retries a submitted transfer. Essentials receives no account credentials.

Bundled files are hashed on a worker, then indexed one file per controller poll in temporary Main data. No asset
is added to the artist's file by search. Valid dependencies inside the installed Essentials directory are bound
transitively and hashed before use, including dependency files with no asset-marked IDs. Missing dependency
closures omit only affected assets, never unrelated bundled or online entries. Omission notes survive warm searches.
A bundled file with an unreadable or external dependency is omitted and reported
without preventing discovery of the other files. This is conservative: a stale external Library record in a file
can make it unavailable even if its particular assets would not need that record.

Online discovery uses Blender's completed, paged v1 metadata and validates page hashes and asset version intervals.
It does not download .blend payloads. Online Essentials and internet-access preferences are respected and never
changed by the tools. Turning Online Essentials off invalidates retained online selections. With internet access
off, an already downloaded and verified file can still be used. No catalog or asset is redistributed through Angelo's
public library.

An approved import uses Blender's native asset downloader and cache, followed by an exact size and SHA-256 check on
a worker, including cache hits. Completion watches the selected file's atomic publication and digest, not the
whole shared download queue; unrelated downloads do not delay a ready selection. The admission bound is 512 MiB for the complete dependency closure; network streaming behavior
and cache management remain Blender's. One .blend file can contain several assets: selecting one downloads that
file, but appends only the selected asset and its dependencies. Stop prevents the pending import; a native transfer
shared with the Asset Browser may finish into the cache. Repeated completion polling cannot repeat the append.

Before mutation, the live scene and target handles are checked again. Temporary-Main inspection accepts only
Library dependencies covered by the selected private-copy map and refuses unpacked media, drivers and script shaders. Asset-only header filtering must not be
used for dependency inspection, since it can hide Library records. Unreferenced source Library bookkeeping records
are removed after append; linked dependencies are not mistaken for those records. The stock adapters check actual created IDs, material application, modifiers, world assignment, placement and
provenance before requesting native publication. A failure claims an unchanged scene only after checked native
restoration; an uncertain result retains its recovery owner.

Discovery and external coding apps
---------------------------------

The FEATURES library guide describes both sources, the Asset Browser entry point, preferences and the load-only
fallback. For people, choose Asset Browser from an editor's type selector, then Essentials or Online Essentials.
The Angelo Library overlay continues to show its public catalog; the guide makes the distinction explicit.

Connected MCP coding apps receive search with their read level and import with edit permission. Import remains a
window-only, individually approved action in Ask and Auto, with the existing controller, checkpoint and Undo.
An external task can use the same library family. Headless apps can search but cannot import without a person;
unkeyed legacy CLI clients retain their phase-1 allowlist. Toolset 53 adds a separate
``source="fab"`` branch to the same tools; it neither changes this Blender source nor
uses a public listing as account permission. See :doc:`fab` for its remaining live and
native acceptance gates.

Validation and release boundary
-------------------------------

Host: ``test_native_blender_assets.py`` and the existing ``test_native_library_*`` tests.
Native: ``run_native_suites.py --gui --include test_blender_essentials``. Inspect ``essentials.json`` for
``success=true`` and ``phase=complete``. Its receipt records source hashes and the compiled binary identity.
The default fixture is offline. Explicit ``ANGELO_QA_ESSENTIALS_ONLINE=1`` additionally verifies the public live
catalog, zero payloads during search and previews, all pages reachable, decoded thumbnail images, real model/material/world imports, cache reuse, cached offline import and
save/reopen after removing its isolated source files. No login, paid service or inference is involved.

The fixture exercises BlenderExecutor dispatch, preconditions and the approval-card builder; it does not simulate
a conversational model choosing assets or the person's approval-card click. Release acceptance still needs the
full CI/instruction ratchet, supported-platform native runs and localized UI review. Fab's actual account, download
and native acceptance remains separate and unproved (:doc:`fab`); the Essentials source does not establish Fab access.
