.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Structured task memory
**********************

Angelo keeps a bounded task brief independently of the recent chat excerpt.
It does not implement a second agent runtime, read native rollout files, or
replace native thread/session resume. All providers consume the same escaped,
deterministic section from ``task_memory.render_context(state, document_identity)``.

``memory_context`` renders that section. A native context's first message gets
it complete; receipts of calls whose tool results the same message's handoff
carries are counted (``historical_native_results_in_handoff``) rather than
repeated. A memory with nothing in it (every brief field, list and receipt
empty: every new chat) is one line instead, ``memory_context.EMPTY``: "empty",
its revision and the untrusted-data label (113 characters instead of 574,
about 0.19k fewer tokens per new chat); the policy its full header states is
also in every runtime's instructions, and the context counts every line as
received, so a later delta sends what changed. A resumed Claude session then receives ``render_delta``: only lines
that are new or changed since the section it last received, under a short
untrusted-data header that says unlisted lines are unchanged (the full
section's header states the policy once per context). When only bookkeeping
moved (the revision and the omitted or in-handoff receipt counts) because the
memory recorded receipts of results the context already has, nothing is sent;
those lines wait, unreceived, for the next substantive change. The revision
only guards ``angelo_memory_update``'s ``expected_revision``, which rejects a
stale one and asks for a fresh read. A rebased context's first message counts
the historical receipts instead of listing them (readable with
``angelo_memory_read``). The objective, constraints and every
other brief field are resent whole whenever they change; new receipts of tool
results the context has already read are counted, and receipts that only
became stale are counted next to the changed ``stale`` line and its reason.
Codex chat threads receive the same delta from toolset 27. The delta
is a transport detail of one native context (``agent_integration.rst``,
"Per-message context deltas"); it changes neither the stored memory nor its
authority.
Opaque source identities and historical provenance live in
``memory_evidence.py``; native/saved-project continuity receipts live in
``memory_projects.py``. ``task_memory`` retains its public pure functions.
Runtime receipts cannot be rehydrated from JSON, and none grants policy authority.

``memory_host.precondition(controller, name, arguments)`` and
``memory_host.execute(controller, name, arguments)`` are the Blender-independent
dispatch adapter. Saving requires an exact pending, executing, individually
approved ToolCall, its durable execution-start marker, matching arguments/digest,
document/turn identity, revision and unchanged source availability. Auto bounded
approval does not authorize memory updates. The adapter holds the controller
lock across update/save and restores in-memory state on failed persistence.
Reads return public memory; writes return only ``success`` and ``memory``, not
scene-change metadata or a Blender Undo claim. Memory read/update results remain
in the ordinary invocation journal but are not recursively fed into the brief's
verified-result history or revision.

Tool integration contract
=========================

``memory_spec.MEMORY_TOOLS`` / ``MEMORY_NAMES`` define two closed tools:

* ``angelo_memory_read`` takes ``{}``; invoke ``read_memory(state, document)``.
* ``angelo_memory_update`` takes ``expected_revision`` and a ``patch``. Omitted
  fields remain; supplied fields and arrays replace their prior values. Run
  ``validate_memory(name, arguments)`` before queueing. Updates always require
  individual user approval, including in Auto mode. They are not bounded scene
  mutations and must never inherit scene-auto approval.

``memory_spec.approval_message`` and ``approval_details`` show every exact field
and value. Integrate them with the existing approval UI instead of a generic
"save memory" prompt. Reference/asset additions additionally require current
host-checked evidence; approving this memory patch is not itself approval to
use a new reference, import an asset, edit the scene, access files/network or
incur paid usage.

Pure integration functions
==========================

The session stores ``state['task_memory']``. The following functions return a
new session-state dictionary without mutating the input or the native result:

.. code-block:: python

   state = update_memory(
       state, arguments, document_identity,
       provenance={"kind": "approved_update", "id": call.id},
       evidence=host_evidence_resolver,
       profile_id=str(controller.store.directory.resolve()))

   state = on_verified_result(
       state, document_identity,
       call_id=call.id, tool=call.name, result=actual_native_result,
       profile_id=str(controller.store.directory.resolve()),
       object_handles=())  # Optional, actual observed IDs only.

   state = on_restore(
       state, new_document_identity, reason="restore",
       evidence_id="host:checkpoint-receipt")

Call ``on_verified_result`` only from actual host callbacks, not assistant
messages, script output text, or claimed model verification. It records a
whitelist of public measured fields. Failed, partial and uncertain read-backs
remain labelled history and are never promoted to success. Arbitrary stdout,
errors, keys, code, provider reasoning/configuration and runtime objects are
not memory inputs. This hook does not itself authorize execution.

Call ``on_restore`` after Undo/redo, snapshot restore, restart or an edit outside
the agent. The valid reasons are ``undo``, ``redo``, ``restore``, ``restart``,
``document_change`` and ``external_edit``. It retains the brief and historical
receipts, marks verified state stale, removes live object handles/mesh revisions,
and increments the generation. A fresh observation never revives older stale
receipts. For ``document_change`` the original scope is deliberately retained;
read/render omit the old brief for the different document. An explicit same-
project snapshot restore/restart may rebind a new native document identity only
through the host continuity flow below; merely opening a path is not continuity.
The one host rebinding outside that flow is a load of the *same document*: the
id the file carries (``document_identity``) at the path the chat last knew. It
rebinds the chat and its memory scope with reason ``restart``, so every
receipt, handle and source is stale and the brief stays; any other load keeps
``document_change``.

Saved-project continuity and explicit human transfer
====================================================

The durable project scope is separate from the ephemeral native document/Undo
generation. ``scope.project`` is an opaque canonical-path token;
``scope.project_revision`` is the exact saved file's SHA-256 content revision.
Automatic restart rebinding requires both to match a current native-attested
receipt. Another path with identical bytes, a changed/replaced file, an unsaved
factory document, and an unknown legacy scope do not automatically acquire a
prior brief. All restored native results, handles and source receipts remain
stale even when the public brief is retained.

The host lifecycle is deliberately two-phase:

.. code-block:: python

   # Main thread, at an actual native load/save boundary; no hashing/file read.
   loaded = capture_loaded_project(window, document_key, private_profile)
   # File-only worker: no bpy/context access.
   receipt = admit_saved_project(
       actual_loaded_filepath, document_key, private_profile,
       loaded_project=loaded)
   # Main thread: current document/profile and bounded file stamp recheck only.
   if controller.bind_project(receipt):
       controller.save()

``capture_loaded_project`` checks a real live Blender window and its actual
scene identity, and captures actual ``bpy.data.filepath``. Neither an arbitrary
caller path nor a JSON ``approved`` flag is a native attestation. Cache receipts
only at lifecycle boundaries with document/file-stamp invalidation; never hash
a whole project from drawing, ``get_controller``, or ordinary redraw ticks.
Automatic verification reads at most 64 MiB in 64 KiB chunks, checks a regular
recognized Blender file and refuses changing or over-budget files. Failure is
unsupported automatic continuity, not proof that notes are irrelevant or that
the project is different.

Large/changed/legacy/unsaved projects have an explicit human fallback:

* ``controller.project_rebind_review()`` returns the exact current revision and
  bounded public brief, references, assets and planned steps for the dialog.
* Show every objective/style/constraint/dimension/unit/step and state clearly
  that transferring notes grants **no scene/tool/file/paid-use approval**.
* After a real human confirms this is the same project, capture a fresh native
  ``LoadedProjectScope`` and call
  ``controller.confirm_project_rebind(loaded, reviewed_revision)``. This host-only
  interface is not a model tool. Wrong revisions, stale/native-mismatched
  attestations and plain JSON fail closed.
* The transfer retains public notes, stales all evidence/source receipts and
  drops automatic saved-file continuity proof. It does not hash or edit the
  scene. The UI saves separately and reports storage failure honestly.

Recovery copies do not become a new original project identity. Before opening
an explicitly selected ready snapshot, obtain
``controller.project_scope_for_restore(checkpoint_id)``. A sibling chat may use
``source_controller=selected_controller`` only when both are bound to the same
load of the current native document (any of its scenes; a reopen of the same
document moves its chats to the new load), share the profile, and the source
snapshot actually exists. The Restore UI asks only such chats; a chat waiting for
or bound to another file keeps its notes isolated without a warning. Each runtime
scope is bound to its target controller/document/checkpoint. After the scene
really loads, call
``controller.rebind_after_restore(new_document, checkpoint_id, project_scope=scope)``.
This completes in-memory epoch/generation/native-session revocation and stale
memory handling **without saving**. Independently persist each chat afterwards;
one failed write must not skip revocation for the others. Original verified
project tokens survive same-project recovery. No scope means unknown old notes
remain isolated. A new unsaved launch cannot manufacture a live recovery scope
that matches the old launch's memory.

Evidence is a runtime capability, not JSON
==========================================

``admit_evidence(source, id, profile_id, document_identity, origin)`` creates a
runtime-only ``AdmittedEvidence`` receipt. The host must call it only AFTER
checking both current existence and real user-origin/approval evidence. For
attachments check ``ReferenceStore.checked_path`` and the corresponding actual
user-message reference; for assets, files or URLs resolve the existing real
approval plus availability from their host-owned store. ``origin`` is exactly
``{'kind': 'user_message' | 'approved_update', 'id': real_ledger_pointer}``.
Provider/model arguments must never supply origin, profile, document or receipts.

``update_memory`` accepts a receipt iterable or a host resolver receiving
``(source, id)`` and returning a receipt. A receipt must match the exact pointer,
profile and document. JSON dictionaries such as ``{'approved': True}`` fail
closed. Do not construct receipts from prior memory provenance or rehydrate
them from saved JSON. Stored reference provenance records the historical source
of the note only, and never grants future policy authority. ``read_memory``
always reports ``authority='none'``.

The host adapter admits attachments only from actual user-message metadata or
the current user turn, after checking their private snapshot and exact UUID.
An arbitrary assistant observation or existing library ID is not sufficient.
Library outputs additionally need a matching completed, individually approved
generation/import receipt in this chat and current ready local files. Workspace
files need a matching real attachment snapshot or approved file-write/export
receipt, plus current FileWorkspace validation. A URL must be a literal public
HTTPS reference in an actual user message; the adapter never fetches it or
claims remote availability. User/evidence scans are bounded to 4,096 messages
and 256 journal entries. If old evidence has left those bounded histories,
reattach or supply a new real approval; historical memory cannot manufacture a
fresh receipt. Current runtime credentials are refused in proposed notes and
redacted from memory reads even when their format is not recognizable.

Persistence, bounds and isolation
=================================

``SessionStore`` whitelists and sanitizes memory on both save/load. Version-1
sessions without memory migrate to an empty brief without losing chat/native
session history. Unknown memory versions or corrupt fields fail closed. The
profile scope is a SHA-256 token of the application's private profile directory;
memory copied to another profile is not rehydrated. The one exception is the
profile's own move from the pre-rename state folder (``native_editor_build.rst``,
"Legacy names"): ``SessionStore`` re-scopes memory carrying the
token of its pre-rename sibling folder to the current folder on load, and only
without an explicit ``ANGELO_STATE_DIR``. Verified results recorded under
pre-rename tool names are kept under the current names. Document identities are
opaque SHA-256 tokens of bounded native-instance primitives, never runtime
objects or exposed filesystem paths. They intentionally change on a new native
load/launch; persistent saved-project continuity is a separate host-checked scope,
and a reopened same document rebinds the chat to the new identity (above).

Loading a session is not native verification: every old receipt/source is stale
and live object handles/revisions are removed even if JSON claims otherwise.
Scope/provenance fields and model-authored notes remain historical untrusted
data, not a policy/approval ledger. Credential-like text and private/absolute
filesystem paths are refused in patches or redacted in admitted read-backs.
Workspace evidence permits only non-hidden relative paths. No tools read
credentials, execute code, access a URL or perform paid calls.

The brief has 1,024 objective characters, 512 style characters, 16 constraints
(160 characters each), 12 labelled dimensions, 8 references, 12 assets and 12
planned steps. Results retain at most 24 receipts and 12 provenance-history
entries; older omissions are counted. The durable memory budget is 64 KiB.
The escaped context is at most 32,768 characters by default. Complete objective,
style, units, dimensions and constraints precede optional source/step/result
history; optional omissions are explicit rather than silently dropping old user
constraints. A smaller caller budget that cannot fit the core brief is rejected.

Request fences and failure recovery
===================================

Each provider request receives an immutable epoch/generation fence. Starting
a new request cannot revive old contexts when the host cancellation flag resets.
Tool admission captures that fence, document, session and metrics before guard
lookup, rechecks after yielding guard/journal work, and publishes the captured
identity instead of relabelling an old delivery with current host fields.
Codex tool/events must also match the exact native thread/turn. Early turn
messages wait in a bounded queue for the authoritative ``turn/start`` reply;
voice uses a fresh thread per activation on the same validated connection,
closes admission after each turn and refuses already-seen voice turn IDs. Stop
captures its original fence so a delayed interrupt cannot target the next turn.

Admission/execution-start/commit-start journal writes remain strict before side
effects. After an actual outcome, a failed final save does not hide success or
strand callbacks: ``journal_durable=false`` plus ``journal_warning`` accompany
the real result/read-back/recovery link. Durable earlier ID protection remains;
there is no operation retry or automatic Undo. In-memory replay returns the
same warned result, and restart treats an unfinished durable marker as unknown,
not permission to execute again. Outcome/UI persistence is best-effort; exact
approved memory writes remain strict and restore RAM state on save failure.

Malformed stored message rows/UUIDs keep valid readable history but close
mutation coverage and discard native resume identities. A saved pending/partial
recovery notice likewise revokes old native resume, preserves its safety link
and requires a fresh context/observation. Historical memory never resolves an
unknown scene/file recovery outcome or weakens the invocation ledger.

Offline regression entry points are ``test_native_request_fences.py``,
``test_native_admission_fences.py``, ``test_native_outcome_durability.py``,
``test_native_commit_outcome_race.py``, ``test_native_memory_projects.py`` and
``test_native_session_recovery.py``. ``test_blender_runtime_recovery.py`` requires
the exact Angelo binary in isolated ``--background --factory-startup
--disable-autoexec`` mode; it saves/opens only private temporary fixture projects
and uses no providers, authentication, paid calls or user projects.
