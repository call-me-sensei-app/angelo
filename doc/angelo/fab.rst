.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

Fab assets on demand — source integration
=========================================

The source contains a cached human Fab interface, a serialized browser owner and the
Fab branches of the existing ``angelo_library_search`` and ``angelo_library_import``
tools (toolset 53). This describes implemented source contracts, not a validated live
connection or release acceptance. Actual product-account sign-in, selected download,
native import/Undo and combined-application validation remain open gates. No build or
working-live-Fab claim follows from the offline host checks.

Fab files are not added to the public Angelo catalog or redistributed. The initial
format is a self-contained GLB model, at most 256 MiB. PBR surface sets and other
formats remain future work with their own fixtures.

Cached human interface and agent tools
-------------------------------------

``fab_host`` is the shared owner behind the thin ``library_host`` facade. Registration
preloads its dependencies and captures the existing private state root. Drawing reads
bounded cached snapshots only; it performs no disk, account or browser work. Explicit
Connect, Check sign-in, Disconnect, search and lookup commands enqueue work on the same
serialized worker. Creating a host does not open a browser. Unsupported or unavailable
owners remain unavailable, rather than acquiring a synthetic connected state.

A sign-in acknowledgement is not account proof. Connected state requires the actual
account observer and a new Session. Fresh sign-in uses an owned, visible manual browser.
Check sign-in closes and drains that runtime, retains the same private profile and opens
a new managed runtime before observing the account. A retained profile resumes managed
without another automatic login attempt. Every transition preserves the actual owner,
profile and runtime fences. Source and mock-owner checks do not establish Windows support.

Human public browsing may show unverified listing metadata independently of the paid
suggestion policy. Those discoveries are not selectable/import-ready and are not
agent recommendations; their kind is omitted unless actually observed. Lookup and
selection require the actual immutable item/version facts. Hit metadata binds the
runtime generation, request, item and version; changed account or request state makes
old selections unusable. The UI displays the observed license/access and exact size,
or the honest maximum when size is unknown, before Use. Current errors remain visible
when earlier results or details are retained.

The same two library tools expose ``source="fab"`` search and exact ``variant_id``
import. Agent search requires a nonempty query and an already connected Session; it
never opens a login flow or changes preferences. Fab accepts no previews, offsets,
resolution or object targets. The public Angelo and Blender sources keep their own
browsing and import contracts. Connected MCP apps keep read-level search and
edit-level, individually approved, window-only import. Headless search cannot create
a UI-owned Fab connection, and an absent Session refuses. The existing worker-library
refusal remains; no second RPC import broker is introduced.

The saved **Suggest paid assets** preference defaults to off and belongs to the
person, not the tool arguments. Off retains agent suggestions only when observed as
free or already owned; unknown access is never guessed free. On can include observed
purchase suggestions, but never buys, acquires an item or accepts terms. Turning the
preference off filters retained and in-flight agent suggestions. Public human Browse
is not a route for an agent to evade this policy.

Observed permission and immutable selection
------------------------------------------

``fab_browser_transport`` admits exact private account/listing/choice records into the
existing ``fab_provider.Session``. A current normal-download UI observation is
``download_permission``: available, terms_required or unknown. It is not proof that the
latest EULA was accepted. The observed license label remains a label; historical legal
pages grant neither current permission nor entitlement. Unknown permission refuses
without inventing a terms gate. Every preparation revalidates the current account,
access, permission and exact chosen version, including cache hits.

A Session retains at most 200 immutable metadata records for at most 20 minutes.
Search is metadata-only, at most 20 results. Human lookup retains its own explicit
selection independently of the agent paid-suggestion filter. Browser version labels
come from the actual sanitized filename; no quality, file size or ready kind is
invented from an unverified discovery. Signed URLs, credentials, raw account identity
and cache paths are not model-facing fields.

Approval captures the exact Session/item/version plus scene/controller binding.
``prepare(..., expected=selection)`` uses that original selection. Changed facts require
a fresh choice and approval. The worker retains the already computed payload size and
SHA-256 in a private frozen ``PayloadProof``; Main does not read receipts or rehash files.
The existing cache lease stays alive through preparation and the verified private payload copy.
Cache receipts detect corruption, not a publisher signature or license grant.

Stock import and native Undo
----------------------------

The Fab path uses the same ``blender_asset_commit.AssetCommit`` with
``payload_format="glb"`` and the ordinary native asset transaction. Global Undo must be
enabled and Undo Steps above zero before approval/download. The shared transaction
capabilities and begin/finish/status/close/recover/pending methods must be present;
a missing contract refuses before download or scene mutation.

The worker prepares a verified private payload copy. Main captures a fresh native
before image, then calls Blender's stock glTF importer inside that scope through
``asset_stock_import.apply_glb``. The adapter checks actual new objects, packed images,
placement and provenance, and rejects executable data before native publication.
Nested Undo pushes are suppressed; a successful import publishes one native Undo step.
The original approval/scene binding and ``PreparedImport.consume`` Session/cache lease
remain in force. The stock importer runs inside the native dirty-before/restore/Undo
transaction.

On failure, native restoration must produce a checked RESTORED receipt before the
adapter claims the scene unchanged. Unknown or failed restoration keeps the exact
recovery owner and blocks another asset import. Explicit recovery queries that owner;
it never replays the import. Source-only checks and historical generic GLB imports do
not establish current dirty-preimage, rollback, single-Undo or platform acceptance.

Human and agent imports share one active owner. Cancel signals immediately, but the
exact retired job/native/cache owner is retained until the worker is terminal and
close is proved. Acquired native scopes require closed COMMITTED or RESTORED status
without recovery_required. A begin failure without a returned scope is treated as
pristine only after the native pending-owner query and original context checks succeed;
absence after an unknown native query is never proof.
Uncertain cleanup blocks new imports and retains the owner, even if the Controller
stops polling. Disconnect and unregister cannot discard that ownership.

Browser lifetime and remaining validation
-----------------------------------------

The existing serialized worker services the permanent download observer between jobs.
An idle timeout alone makes no health claim. Actual taint revokes the transport and
closes/drains that same browser owner on the same worker; cleanup failures remain
owned and visible. Main only consumes the coalesced cached failure.

User Disconnect/Cancel purges the owned profile. Ordinary shutdown may retain it only
through ``Connection.close_normal_shutdown`` and that owner's exact private verified
account/runtime witness; an absent witness or older method falls back to purge. A
public connected flag or human Done acknowledgement is not that witness. This API and
its offline fixtures do not prove restart sign-in persistence on an actual account.

Focused host fixtures exercise source boundaries, selection and cancellation rules,
the two-phase human/agent path, idle ownership and exact schemas with stand-ins. They
are not live account/download, native effects, native UI pixels or conversational
model evidence. The separate synthetic native-UI fixture can verify registered
controls without making its controlled replies a production backend. Actual account,
selected physical download, all exposed Blender effects, GLB Undo/recovery,
supported-platform checks and final combined-application validation remain required.
