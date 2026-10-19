.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

Snapshot policy, storage and retention
======================================

``angelo_agent/checkpoint_policy.py`` owns validated, human-selected settings.
The private profile file is ``checkpoint-policy.json``; it contains no account
credentials. Missing settings use ``EVERY_REQUEST`` and the default retention
limits (2 GiB of snapshot storage, unlimited count and age). Malformed settings
use ``EVERY_REQUEST``, supply an actionable ``CheckpointPolicyStore.error`` and
**pause automatic removal** until the human saves the settings again, so a
damaged file can never delete history. No model tool may change these settings.

``EVERY_REQUEST`` remains the default: capture a complete scene copy on the main
thread before submitting each request. Capture failure preserves the draft and
does not start the request. ``BEFORE_FIRST_EDIT`` is an explicit human choice:
do not capture on message submission or read-only work. Capture immediately
before the first **approved** scene/file effect, including isolated native
program staging. It restores the state before that edit, not before the message.
Manual edits made between sending and the first approval are part of that
baseline. After successful capture, bind the actual UUID and
``before_first_approved_edit`` scope to the owning request's user message. Never
show a restore link or a made-up checkpoint for an unedited request.
``performance.rst`` records the measured capture time and retained bytes per
message under each policy for large synthetic documents, and the deduplicated
storage measurements.

Host integration
----------------

Private Sculpt checkpoint pairs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The private native route retains a WindowManager capture owner from before
modified-image saving and SAVE_PRE through serialization, publication, POST and
terminal observation. Normal artist Save is unchanged. At the actual writer
barrier it captures distinct live Sculpt owners and binds their Object, Mesh,
nullable Key and canonical Scene/ViewLayer identities to actual BASE writer
tokens. A version4 point contains both ``scene.blend`` and ``sculpt.runtime``.
Ordinary captures with no runtime owners complete as version3 and do not pay a
companion write or extra whole-BASE capture hash.

Version4 metadata validation is structural and never authorizes scene loading.
Restore uses ``CheckpointRecovery`` to verify private working copies of both
components, then the native reader decodes the companion and resolves selected
BASE tokens before replacing any document. The UI completes a protected safety
capture, saves its recovery notice and passes the actual retained native safety
capsule to Restore. A dictionary or a successful save submission cannot replace
that capability. The existing load-handler order and initial Undo boundary are
part of the native integration contract.

An object shown by more than one graph (the same Multires object in Sculpt
Mode in two Scenes or windows) restores every graph's grids exactly: install
admits every selected graph for materialization before evaluating any of them,
because admission tags the object in each graph that contains it. Every
refusal names its check, and Restore's error carries those reasons.

A refusal before replacement leaves the document untouched. A failure after
replacement (``WM_checkpoint_restore`` reports it) reloads the protected
safety capture of the pre-Restore document through the same native path:
native code admits exactly one reload of that Restore's own safety capture
into the failed document it left, without a new safety capture of it
(``reload_failed_restore``). The outcome is recorded as not applied and
reloaded, with no workspace file changes; only if the reload fails too is it
partial recovery with the durable safety link. The ``WITH_GTESTS`` seam
``_test_fail_next_checkpoint_install`` in ``_bpy._angelo_undo`` exercises it.

A restored document is never its private BASE file's content (that file lacks
the installed Sculpt state), so the read leaves it modified: quit prompts and
auto-save writes it.

Add-on handlers behave as with File > Save and File > Open: one that raises
during a capture (``save_pre``, ``save_post``) or during Restore, the safety
reload or Undo restore (``load_pre``, ``load_post``) is printed and, for Restore,
reported as an operator warning; it never fails the capture or the Restore. The
native callback observer still validates every effect a handler had on the data.

Recovery is never less available than File > Open and never claims exactness it
does not have. A load handler that edits a selected Sculpt object (its owners,
mode or coarse mesh, or the attribute layout of a Dyntopo mesh, which a Dyntopo
install would otherwise drop at the next flush) is refused by name, and the exact
pre-Restore state reloads. A failed reload is retried once (transient faults),
then retried with add-on load handlers suspended. A handler is Blender's or
Angelo's own, and keeps running, only when the file of its code object lies in
Blender's scripts folder or Angelo's package folder; its ``__module__``, which any
add-on can set, never decides. An add-on that edits on every load thus leaves
Restore not applied with nothing lost. The check sees the change, not its author,
so ``load_handlers_suspended`` lists every suspended add-on (a module name only
when that module's file is the handler's code file, otherwise the code file's
name) and the notice says one of them changes the file when it loads, to disable
that one and Restore again, and that their setup for a newly opened file (for
example message-bus subscriptions) was skipped for this document and runs again
at the next file open. It is not rerun after the reload, which would edit the
document the reload just made exact. Undo restore follows the same rule, so it never
loses the pre-Restore work. Only when every reload fails is the outcome
partial, and the notice then says the scene lacks recorded Sculpt work: a BASE
file never holds the Sculpt runtime and is never presented as the recorded
state.

Jobs: joining a job thread with the GIL held would never return when the job
needs the GIL to end (an icon preview freeing its depsgraph), so every job kill
releases the GIL around the join (``wm_jobs_kill_job``). This covers Restore,
the safety reload, ``open_mainfile`` and ``asset_generate_preview`` called from
Python.
Restore also stops window-manager jobs (``quiesce_jobs``) right before its
safety capture, so no preview written into Main afterwards makes the document
differ from that capture. ``test_blender_checkpoint_preview_jobs`` (GUI) and its
background variant repeat both.

Every READY record this build publishes, version 3 and version 4, carries
``component_seal``: ``<key id>`` then ``.`` or ``:`` then an HMAC-SHA256 over the
capture identity, version, storage, capture boundary, scene size and a binding
of the BASE, plus the companion declaration of a version 4 pair
(``checkpoint_seal.py``). Two bindings keep the request path free of any read:

* identity (``:``): an ordinary version 3 capture is sealed at publication, in
  the same atomic record write, over its BASE's size, modification time in
  microseconds (the precision time-preserving tools keep) and inode (the NTFS
  file index on Windows). Nothing is pending, so a crash or quit never leaves a
  capture unsealed. Restore checks the stable stat of the very file it copied.
  Where the BASE's timestamp has no sub-millisecond part (HFS+, FAT, exFAT, SMB:
  two captures can share one) publication binds content instead; only there
  does a request pay for a hash. A profile copied to another volume keeps no
  inodes, so its full copies that deduplication has not yet bound by content are
  refused; the snapshot service binds every full copy at start-up, but a
  session without it (a headless script) keeps identity seals.
* content (``.``): the BLAKE2b-256 of the BASE. A version 4 pair binds the
  digests its publication already computed. The snapshot worker's
  deduplication, which reads every full copy off the request path anyway,
  verifies the identity seal over the bytes it stores and re-seals the stored
  record with their digest; a full copy whose seal does not verify is kept as a
  full copy.

Measured with the product's snapshot benchmark (repeat-capture net
instructions, one binary, unsealed capture as the control), the identity
seal costs nothing measurable: 42.0M against 42.1M at 16 MB, 42.9M against
43.0M at 48 MB, 47.4M against 47.3M at 193 MB. Hashing the BASE on the request
path instead cost 259M, 675M and 2,546M (BLAKE2b) or, streamed through the
writer, 3.4M, 29.7M and 116M more (XXH3-128). The declared sizes and hashes
alone are plain metadata, and BASE writer tokens are stable across captures of
one session, so without the seal a ``scene.blend`` or companion of another
capture, or a record rewritten to another version or storage, would otherwise
pass.

Threat model: the seal binds components to their own capture and rejects
cross-profile replays and accidental mixes of checkpoint folders: another
capture's ``scene.blend``, or one rewritten in place, has another modification
time or digest. It does not defend against a same-user attacker with write
access to the profile, who can read the key, re-seal a forged record or reset a
file's modification time; the key therefore stays in the profile.

The key is 32 random bytes in the profile's private ``checkpoint-seal`` folder
(0600, a file-journal target never). Only sealing creates it, atomically (a
temporary file, fsync, then a no-overwrite link); verification never does. A
key file that is not exactly 32 bytes is moved aside (``damaged-<id>.key``) and a
new key starts for new captures, with a chat notice; a damaged key never blocks
a capture or a request. A seal names its key by an identifier derived from it,
so Restore says either that the seal key is missing or damaged and earlier
checkpoints can't be verified, or that a record is not bound to its own capture
(a mismatch). The process keeps every key it used, so a Restore and its Undo
restore stay verifiable for the rest of the session even if the key file is
lost meanwhile. A request seals with the key its process loaded after one
``stat`` of the key file, compared with the file's device, inode, size and
modification time when the process last read or wrote it (no extra system call);
the snapshot worker re-reads the file on every seal. A missing file is written
again from that key, without overwriting, and the snapshot worker does the same
for a missing or damaged one, so a Sculpt-only session that loses the file keeps
every checkpoint verifiable after a restart. A file that now holds another valid
key (a second Angelo on the profile, a profile synced from another computer) is
never overwritten: its key seals new captures, the process keeps its own in
memory and beside it on disk (``retired-<id>.key``, no overwrite), and a chat
notice says so; every record of both keys stays verifiable after a restart. A
process that quits before its next seal after such a replacement keeps no copy,
and its records are then reported as unverifiable; an in-place rewrite within
one timestamp tick of a coarse file system is seen at the worker's next seal.
Only a process without the key starts a
new one, with a chat notice whenever sealed
checkpoints exist whose key is gone (the snapshot worker's pump posts it). A
seal from a key this profile never had (not in ``keys.json``, the list of its
keys, nor on another of its records) is reported as sealed by another profile.
``cutoff.json`` records the capture boundary at which sealing began: an
unsealed version 1/2 record, or an unsealed version 3 record from before it, is
a legacy capture and is admitted as one; every later READY record, and every
version 4 record, must be sealed (no unsealed version 4 record is valid). A missing
or damaged cutoff file is derived from the earliest sealed record on disk (so
deleting it neither strands legacy captures nor admits later unsealed ones) and
rewritten at the next seal outside a request; a profile with no sealed record
has no cutoff. ``CheckpointRecovery.prepare`` and ``recovery_copy`` refuse
before any byte is restored; ``availability`` reports a missing required seal or
an unavailable key without reading the BASE.

Limitation: ordinary Multires Sculpt Redo is not guaranteed bit-for-bit lossless
through dependency evaluation and displacement writeback. This is separate from
the exact checkpoint capture, Restore and Undo Restore guarantee. Recovery
acceptance compares continued brush/Undo/Redo byte-for-byte against an ordinary
reference from the same scene, retaining exact Undo and both owners' data. The
ordinary stroke-versus-Redo difference is recorded explicitly; no geometric
tolerance or false lossless-completion claim is permitted.

The first Undo after cold recovery targets the complete checkpoint baseline;
its whole state is compared with that captured baseline, including original
shared displacement data. Later Sculpt-delta Undo steps use the corresponding
ordinary reference. The expected target is declared before applying the edit.

The strict Dyntopo continuation fixture uses native fixed-Y Draw and retains
dynamic topology refinement. Only newly created vertices from independently
executed strokes may be matched by a unique, exact float32 correspondence;
original vertex slots, masks, hidden flags and every oriented face remain exact.
Ambiguous correspondence fails. Capture, Restore, Undo Restore and the complete
Undo baseline keep their original byte-exact checks, including coarse storage.
AREA Draw is not bitwise repeatable in a zero-checkpoint ordinary control. That
is a measurement limitation, separate from the Redo limitation; it does not restrict
available brushes or certify arbitrary brush re-execution as deterministic.

The safety capsule retains the exact directory, component descriptors and
capture pin after WindowManager capture ownership closes. Immediately before
Restore consumes it, the original safety purpose, protected terminal metadata,
verified component receipts and held files are checked again; native code also
compares current Main and Sculpt content with its owned safety witness. Journals
may be freshly added without changing those component receipts. Explicit
``capture_release`` closes the retained resources after native scene consumption,
before its safety journal participates in file restore, and on every failure;
ordinary request captures release them immediately.

Allowed v4 journal/protection changes preserve fresh capture fields. File
restore checks the selected capture, each selected before-image owner and each
current-effect owner before changing files; those private bindings are separate
from the public journal entries. A ready journal on an unavailable v4 scene may
still be needed by a retained legacy restore point. Active capture pins are
checked with independent nonblocking opens under the profile transaction.

Unavailable capture bytes remain actual storage usage and keep their unpruned
manifest references, but they do not by themselves justify evicting usable
history. For example, 80 MiB usable plus 40 MiB pending under a 100 MiB budget is
reported as 120 MiB and unmet; the pending 40 alone does not evict the usable 80.
Independent age/count limits and existing usable-byte overage still apply.

Private capture destinations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``checkpoint_capture.CaptureOwner.reserve`` owns a fresh private v4 reservation,
an existing capture pin and a held checkpoint directory. It freezes the profile,
session, capture and ordered boundary independently of mutable store attributes.
Each metadata operation takes the existing profile transaction and uses the held
directory with a fresh lexical identity check; the transaction is not retained
across native callbacks. Public version3 reservation and completion keep
their existing metadata and I/O behavior.

The closed roles are BASE (``scene.blend@`` to ``scene.blend``) and RUNTIME
(``sculpt.runtime@`` to ``sculpt.runtime``). ``declare_output`` records intent;
``create_output`` then creates an empty anchored file exclusively, retains its
descriptor, synchronizes it and records its actual identity before returning an
``OwnedOutput``. ``borrow_descriptor`` hands that resource to the synchronous
private writer once. The writer must not close it or retain it beyond the owner.
The native result is opaque and bound to this exact owner and descriptors.
``publish`` checks native verification facts, hashes the actual held pair,
synchronizes it and publishes the fixed names without overwrite. Only then does
fresh metadata become READY/PENDING. Native POST admission requires those real
published resources. ``finish`` records VALIDATED or INTERRUPTED_VALID from the
actual native POST outcome; a failed POST still fails the request (a SAVE_POST
handler that raises is printed, not a POST failure). Original capture
purpose and the immutable verified component receipt remain bound through fresh
terminal readback, while file journals and protection retain later changes.

``capture_outputs`` is an append-once v4 ledger with at most two role slots.
Directory/file identity words are canonical decimal strings, preserving signed
values and bits outside the signed63 component-count range. They are bounded
metadata observations, not persistent generation tokens. Fresh ledger writes
preserve current file journals and protection. Structural validation returns
independent copies and cannot turn a caller-supplied dictionary into ownership.

Creation and metadata replacement are separate operations. A failed metadata
acknowledgment can follow visible replacement; the attempt then returns no
writer, permits no retry, and keeps truthful partial state. Intent-only residue
is measurable under its exact declared name but never becomes a creation fact
from file presence. Unknown files and links retain the existing refusal rules.

Close the native owner before calling ``abandon`` in ``finally``; active native
ownership prevents premature descriptor closure. Abandon consumes resources before cleanup, closes
each held resource once, and uses the existing close-only pin abandonment when
no profile transaction is available. It leaves metadata and output names intact;
close failures remain observable and are not retried. An unrelated reused
descriptor is not handed out or deliberately closed. This is a synchronous,
quiescent resource contract, not protection against a caller illegally closing
and reopening the same borrowed file behind the owner.

``test_native_checkpoint_capture_outputs.py`` exercises real temporary profiles
and the existing NT adapter stand-in, including metadata acknowledgment failures,
stale bindings, live descriptor ownership, cleanup and unchanged legacy routes.
Its arbitrary payload bytes test resources only; actual native companion and
Windows runtime behavior require their separate integration checks.

Completed full pairs participate in existing retention limits. Before any batch
tombstone, removal checks capture pins, the original output/directory identities,
the finite published names and complete component hashes. Unknown files,
temporary residue or changed bytes refuse removal. The v4 tombstone discharges
the output ledger and keeps the same ordered history boundary. Availability and
inventory checks are bounded presence/size observations; Restore still performs
the complete byte and native verification.

.. code-block:: python

   policies = CheckpointPolicyStore(private_profile)
   policy = policies.load()
   # Only the human settings operator performs this write after confirmation:
   policies.save({"capture_mode": "BEFORE_FIRST_EDIT"}, user_confirmed=True)
   # Freeze the loaded policy for each request. The main-thread controller hook
   # ensures a real checkpoint before effects, never on a provider worker.
   capture_at_submit = requires_capture(policy)
   scope = capture_scope(policy)
   record, path = checkpoints.reserve(original_project, capture_mode=policy["capture_mode"])
   # Native save_as_mainfile(copy=True) writes path, on the main thread.
   record = checkpoints.complete(record["id"], capture_ms=measured_capture_ms)
   # Existing callers saving scene_bytes remain supported and measure capture
   # duration from reserve to successful metadata persistence automatically.
   # The full copy is now a complete restore point. Hand it to the snapshot
   # worker; this never blocks or fails the capture (see Deduplicated storage).
   snapshot_service.after_capture(controller, record)

File tools durably journal the first pre-write version in each global capture
window before modifying a file. A capture in **any** chat starts a new window,
including for a continuing request that began earlier. Thus request A can write
once before B's capture and again after it; restoring B recovers A's intermediate
file contents rather than A's original version. Restore selects before-images
by their actual globally ordered write sequence, not by the originating request's
date. A separate last-effect sequence identifies the current output kind when a
before-image is reused within the same window; its backup kind stays immutable.
Scene and file restore still cannot undo arbitrary Python/network/export effects.
Native scene staging must never exist when the baseline is serialized. A safety
snapshot uses ``reserve(..., purpose="safety")`` or ``mark_protected(id)`` before
restore changes begin. Main-thread native save is never moved to a worker.

Unsaved image pixels
--------------------

A snapshot is a private save, and a save keeps an image's pixels only as its
own file or its packed data holds them: pixel edits that were unsaved when a
snapshot was taken (painting, a bake, a script writing ``Image.pixels``) are in
no snapshot. Unlike a normal Save, a capture never writes the person's image
files, whatever the Save Modified Images preference: saving them would
overwrite an original with pixels the person never chose to keep
(``WM_checkpoint_capture_write``; ``ui.capture_checkpoint`` also holds the
preference at Never Save while it captures). Nor does a capture pack external
files into the live document when Automatically Pack Resources is on, as a
normal Save does: a checkpoint never changes the document. The snapshot
references each external file by its absolute path, as the live document
references it (relative paths are written absolute because the snapshot lives
in a private folder), and keeps the person's Automatically Pack setting, so
the restored document packs on their next Save, as the original would. Builds whose native
module says ``capture_never_packs`` skip the packing in C++; on older builds
``ui.capture_checkpoint`` holds the setting off around the capture, and their
snapshots record it off. Packing a private copy instead would make Restore
inexact: the live document held those files unpacked. Blender itself still
packs an image when it loads its pixels with the setting on, as it always
does. An image kept in its own file opens as that file is now. This is honest
partial recovery by design, not a pixel backup (``checkpoint_images``):

* the Restore and Undo restore dialog states that rule, names the images whose
  unsaved pixel edits a restore would discard now (they are in no snapshot,
  so Undo restore cannot bring them back) and, for Undo restore, the images
  whose unsaved edits its safety snapshot never held;
* the restore notice records ``unsaved_images``, the images unsaved when
  Restore began (read before the safety capture), and once the recovery scene has opened the chat
  says which unsaved edits were discarded (``unsaved_images_discarded``; a
  warning report too) and, after an Undo restore, which ones its safety
  snapshot did not hold (``unsaved_images_not_restored``). A restore that never
  opened its scene discarded nothing and says nothing of the kind.

The Render Result and the compositor's Viewer are not counted: no save keeps
them. Bake jobs keep their own pixel backup for Cancel (``long_jobs.rst``).

Ordered journal contract
------------------------

Snapshot metadata version 3 adds an integer ``boundary``. Its journal versions
add ``sequence``, ``window``, ``phase``, ``effect_sequence`` and ``effect_kind``.
The private ``checkpoint-timeline.json`` assigns profile-wide sequence numbers
under the same transaction as capture/file effects. Before allocating, the
clock is checked against every recorded capture and write order whenever the
process cannot trust it: on its first allocation, and whenever the file is not
exactly the clock this process last validated or wrote (device, inode, size,
modification and change times, and content). Any other writer replaces the file
(another app instance, a restored backup, a stale copy, a hand edit), so a stale
but structurally valid clock cannot recycle an old gap. A trusted clock is newer
than every order a record can hold, so allocation then reads and writes only the
clock: a request's capture costs the same with 10 or 10,000 stored snapshots
(see "Per-request cost and stored history" below). Failed operations may leave
unused sequence numbers, never silently reset or reconstruct an ordered clock.

``journal(..., pending=True)`` returns the canonical prepared entry. Actual file
effect callers hold the profile transaction through preparation, anchored
publication/deletion and ``commit_journal(id, entry)``. FileWorkspace and Studio
already do this; applications should use those owners rather than reproduce the
protocol. Direct protected safety journaling remains ready immediately by
default because it records the state before Restore, not a proposed export.
``rollback_journal(id, entry, previous=None)`` discards an unused prepared version;
when reusing a version, ``previous`` must be that same version's prior receipt,
not an earlier-window before-image.

The closed phases are ``pending``, ``ready`` and ``discarded``. Discarded entries
never authorize restoration or deletion of a later foreign collision. Cleanup
ownership is recorded before removing a backup, so an unavailable unlink leaves
known inert storage, not an unexplained file. An uncertain applied effect keeps
its before-image pending and blocks combined restore/retention until reconciled;
the host must not blindly retry or release it. A proven successful publication
or unlink followed by a failed acknowledgement keeps its ready recovery image
and reports possible partial effects. Streamed before-images enforce actual byte
counts and source stability, not just a size observed before copying.

Ordinary existing-file backups remain at most 20 MiB. Agent Studio exports may
create new outputs up to 150 MiB, never overwrite. ``restore_plan`` still returns
``(target, backup, entry)`` tuples, with host-derived ``current_kind`` evidence.
This live-output bound is independent of the historical backup kind: deleting a
small file in A and exporting a large file to its absent name in B does not
invalidate A's ordinary backup. Passing that plan entry to protected safety
journaling permits a marked ``studio_export_safety`` copy up to 150 MiB. Ordinary
file-tool reads/writes/deletes do not acquire that larger limit.

File effects use held non-symlink directory descriptors and no-follow handles;
workspace symlinks are rejected even when their destination is in the root.
Hidden/blocked paths and excluded application directories remain unavailable.
No race-prone path fallback is used for scoped tools on platforms lacking safe
descriptor-relative primitives. Directory listing admits at most 4096 entries
before sorting and returns at most 200; individual file bounds remain unchanged.

``restore_since`` retains its integer success result: the journal entries it
applied. Its ``changed`` attribute (``checkpoint_restore.RestoredFiles``) counts
the files it actually changed, and the restore notice reports that count: a
file already holding the restored bytes and mode, or a new file already absent,
is restored without a change (an Undo restore after a Restore that never
reached its files reverts 0 file changes, not 1). During application,
``OSError``/``ValueError`` retains its type and may carry ``restored_files`` and
``restored_paths`` for confirmed effects. ``restore_progress_exact=False`` means
additional effects are uncertain; do not interpret the confirmed count as an
exact total. Whole-plan admission failures may lack these fields. Native UI owns
the separate document-open outcome, durable pre-open safety link and all-chat
rebinding; a file error must never be represented as an unchanged scene after a
successful scene load. There is no automatic live rollback or retry.

Legacy version 1/2 scenes, metadata and backups remain readable and are not
rewritten to invent missing write order. Their journals cannot serve as writable
request baselines. An admitted legacy scene without ambiguous journals can still
recover subsequent ordered file changes; a combined legacy-point restore with
unprovable old journal timing fails with an explicit limitation while preserving
the scene/history. Ambiguous old journals stay protected. Timeline initialization
records a digest of the admitted legacy capture/journal metadata; later legacy
additions/changes cannot silently be treated as pre-migration writes: restore and
retention check the digest every time and refuse. A capture in a process that
already trusts its clock is still admitted, with an order later than every
recorded one; the next locked whole-history check (the snapshot worker's
maintenance after each capture, a restore, Review Snapshot Retention) or the
next process start makes further captures refuse. The same holds for any other
damaged record. Do not share a profile concurrently with an older application
that cannot honor this protocol.

Journal roots are absolute. When ``state_migration.py`` moves the pre-rename
profile to ``angelo-agent`` (``native_editor_build.rst``, "Legacy names"), it
rewrites, under this profile transaction, every ordered record's journal
``root`` (and every record's ``original_path``) that pointed into the old
folder, so restoring writes into the moved default workspace. Legacy version 1/2
journals keep their recorded roots, because their content is the timeline's
legacy admission evidence. Records are rewritten through the same atomic
metadata replace; a marker in the new profile makes an interrupted rewrite
resume on the next launch (``test_native_state_migration.py``).

Deduplicated storage
--------------------

For version3 points, the native main-thread copy completes as READY. That full
copy *is* the checkpoint until a background
worker (``snapshot_worker.py``, one thread per profile, never touching ``bpy``)
moves it into a private profile-wide store, ``<profile>/snapshot-store``:

* ``snapshot_blocks.py`` cuts the file at its own block headers (``BLENDER17-01``
  and pre-5.0 ``BLENDER_V``/``-`` headers). Blocks of at least 64 KiB are split
  into 1 MiB pieces so a partial edit of a large array only stores the touched
  pieces; smaller blocks are grouped per ID (a group starts at every non-``DATA``
  block and closes at 16 KiB). Unknown or compressed formats, truncated blocks
  and impossible lengths fall back to fixed 1 MiB chunks; segmentation never
  fails, and the segments always concatenate to the exact input.
* ``snapshot_store.py`` / ``snapshot_store_writer.py`` keep one copy of every
  unique chunk (BLAKE2b-256) in append-only ``packs/pack-<id>.dat`` files of about
  64 MiB, indexed by ``index.sqlite`` (WAL, ``synchronous=FULL``). New chunks use
  zlib level 1 only when that saves at least an eighth. Each snapshot has a
  manifest: whole-file BLAKE2b-256 digest, size and ordered chunk IDs.
* ``snapshot_ingest.py`` streams the full copy (no-follow, single link,
  unchanged while read), commits chunks and the manifest, **reassembles the
  manifest and requires the exact whole-file digest, size and every chunk
  digest**, then, under the profile transaction, re-checks the record and file
  identity, records ``storage: "store"`` and ``scene_hash`` and only then deletes
  the full copy. Any failure keeps the full copy authoritative and reports a
  concise chat notice.

Restore uses the same ``recovery_copy`` → ``open_mainfile`` path. For a stored
point it writes the reassembled bytes into the working copy and refuses to open
anything whose whole-file digest or size differs from ``scene_hash`` /
``scene_bytes``; there is no semantic diff or patch of Blender data. Every
intermediate state restores: before ingestion (full copy), after a manifest
commit but before adoption (full copy; the next start re-ingests and replaces the
manifest), and after adoption with a leftover full copy (the store is
authoritative; start-up recovery deletes the leftover once the manifest is
confirmed). A crash while writing leaves only unreachable pack bytes, which the
next writer truncates or removes. ``availability`` checks that the manifest
exists; restore still verifies every byte.

At start-up the worker queues every READY version-3 full copy oldest first,
including snapshots captured before deduplication existed (migration). Each full
copy is deleted only after its reassembly verified byte-identical. Legacy
version 1/2 records are never rewritten and stay full copies.
Version4 pairs remain full copies; BASE adoption and leftover-copy deletion do
not yet accept that format. Their complete-pair retention remains available.

Store maintenance (``snapshot_maintenance.py``) runs on the worker after
retention: manifests whose record is PRUNED or gone are dropped (READY records
are never touched; unreadable records are kept), garbage collection rebuilds
reference counts from the manifests before deleting unreferenced chunks/packs,
and compaction copies the verified live chunks of packs with at least 25% (and
1 MiB) dead space into the newest pack and then deletes the old pack. Store
directories are 0700 and files 0600; the directory chain is opened without
following links, index files are checked as single-link private regular files
before SQLite opens them by path (it cannot open a descriptor), and unknown
files in ``packs`` stop maintenance without being removed. Writers hold a store
lock (thread lock plus an inter-process file lock) that Blender's main thread
never takes. Compaction relocates live data and therefore also holds the
profile transaction, which restore holds while it reads; the lock order is
always profile transaction, then store lock, and the worker only tries the store
lock briefly while holding the transaction. Profiles holding deduplicated
snapshots cannot be read by builds older than this storage format.

File-journal before-images remain separate small files beside each record and
are not deduplicated.

Not provided by this storage format: instant restore from Blender's in-memory
undo steps, or capturing directly from the undo memfile in C++ instead of a
second serialization.

Retention
---------

``max_bytes``, ``max_count`` and ``max_age_days`` are non-negative integer
settings; zero means unlimited. The default is a **2 GiB storage budget** with
unlimited count and age. The saved policy is the human's standing choice and is
**enforced automatically** (``snapshot_retention.py``): after every ingestion,
at start-up and when settings change, the worker removes the oldest unprotected
restore points until every limit is met. Policy files from the earlier
proposal-only format (version 1) keep their count/age limits; their
``max_bytes`` of zero, which was the only default then, becomes the 2 GiB
default. Users can raise it or set zero (unlimited) in Snapshot Settings, which
explains the default beside the field: unchanged scene data is stored once, so
2 GiB usually holds many restore points.

Automatic removal never touches:

* durably protected safety snapshots;
* the current/active request snapshot of **every** chat; the main thread
  collects these references for each run (``snapshot_service.request_maintenance``)
  together with the timeline position at that moment, and points captured after
  that position are protected too because their chats may not have bound them
  yet;
* checkpoints owning write versions at or after the earliest retained capture
  boundary (the file-restore dependency rule below), and legacy points whose
  journal order cannot be proved.

When protection prevents meeting a limit, the run says so: a concise chat notice
names the usage, the limit and how many points are protected (active requests,
safety snapshots or file-restore chains). Each removal run posts one notice
with the number of points and bytes released; an unchanged over-limit state is
reported once, not after every capture. Removed points keep PRUNED tombstones,
so their Restore links show unavailable. A malformed history blocks removal and
is reported; nothing is deleted.

Git cleanup signal (``snapshot_git.py``): when a removal is needed, the worker
asks, for each snapshot's saved project path, whether the file lives in a git
work tree and HEAD's committed version is byte-identical to the latest saved
file. Snapshots of that project captured before the commit time of that version
are removed first; protections still apply and nothing is removed only because
of git. It uses only the ``git`` executable on PATH, read-only plumbing
commands (``rev-parse``, ``cat-file``, ``log``) with a 10-second timeout, no
shell, no stdin, ``GIT_OPTIONAL_LOCKS=0`` and a sanitized ``GIT_*``
environment. The file is hashed in Python (Git LFS pointers by SHA-256 and
size) instead of ``git hash-object``, whose clean filters can write into the
repository. Missing git, timeouts, untracked or modified files mean no signal.

Planning (shared by automatic runs and the manual review) is pure Python over
filesystem metadata and the store index:

.. code-block:: python

   active = [(controller.state["id"], controller.request_checkpoint)]
   plan = checkpoints.prune_plan(policy, protected_refs=active)
   # Manual review: show candidates[].ref, bytes, protection reasons and
   # budget_met, ask the human to confirm, then apply that exact proposal.
   result = checkpoints.prune(plan, user_confirmed=True, protected_refs=active)
   # Automatic: snapshot_retention.enforce_retention(checkpoints, policy,
   #   protected_refs=active, watermark=timeline_sequence) plans and removes in
   #   one profile transaction.

Automatic runs first plan without the profile transaction (a probe). When the
probe finds nothing to remove, the budget met and no issue, the run ends there:
it deletes nothing, so a read racing a concurrent write cannot do harm, and a
change it missed is seen by the next run. Otherwise the run plans and removes
under the transaction exactly as above; the probe only stands in for the first,
advisory plan that feeds the git preference, and not when it saw an issue that
could be a write in progress. Orphan-manifest cleanup selects its suspects the
same way and re-checks only those under the transaction.

A reference is a ``(session_uuid, checkpoint_uuid)`` pair or
``session_uuid/checkpoint_uuid`` string. Do not pass ``None`` references. The
dependency rule is deliberately conservative and can leave a budget unmet; it
does not compact or rewrite history. When it reinstates a candidate, selection
repeats so that a later unprotected point can still be chosen.

Byte accounting: the budget covers snapshot folders (metadata, journal
before-images, full copies awaiting ingestion) plus the store's live chunk bytes
(stored size, after compression) and index files. Shared chunks are counted
once. A candidate's ``bytes`` are its own files plus the store bytes its removal
actually releases after the earlier candidates, that is, the chunks no remaining
snapshot references (``store_bytes``). Removing an old point whose blocks newer
points still use therefore releases little. Manifests of already removed points
count as released. Pack space freed by removed chunks is reclaimed by compaction,
so the store's physical size can briefly exceed its live bytes. The planned
``remaining_bytes`` is an upper bound: it reserves 1 KiB for each new tombstone.
``budget_met`` can therefore be false for a very small byte budget even when
every unprotected point is proposed for removal.

``prune`` and automatic runs report ``bytes_freed`` (snapshot files removed
immediately) and ``store_bytes_pending`` (shared store bytes released by the
following garbage collection). Small metadata tombstones remain. Their
``PRUNED`` state, and ``availability(id)`` result, let the UI mark restore
unavailable without deleting the user's transcript or pretending that history
still has backing data. Deleted points cannot be used as file-journal anchors.
Already-completed restore copies in ``recoveries`` are not candidates and are
not included in these budgets.

The manual review still requires ``user_confirmed=True`` and rejects a plan if
the inventory, exact candidates or supplied protected references changed (a
finished ingestion also changes the inventory). Malformed, missing, linked or
unexpected storage fails closed. Only named regular files in validated UUID
snapshot directories are unlinked, never arbitrary directories, credentials or
original user projects. Removal is unavailable when the platform can anchor
neither descriptor-relative (POSIX) nor handle-relative (Windows, see "Windows
file anchoring") deletion; history stays intact. Cooperating application
processes serialize journal/retention transactions with a private profile lock;
contention returns an actionable error after two seconds instead of blocking
the UI indefinitely. Several app instances sharing one profile do not see each
other's active requests; do not run them concurrently on one profile.

``incomplete_plan`` proposes at most 16 empty reservations older than 24 hours by
default. Its explicit bounds are age >=300 seconds and count 1..64. It excludes
scenes, journal-bearing records and protected references, requires the same human
confirmation, and leaves unavailable tombstones. It never sweeps usable history.

Per-request cost and stored history
-----------------------------------

Every request's capture runs on Blender's main thread before the model is
called, so its cost is added to every message. It must not depend on how many
snapshots the profile already holds:

* **Capture (main thread), O(1):** ``reserve`` reads and writes the trusted
  timeline clock and writes the new record; ``save`` rewrites that record;
  ``snapshot_service.after_capture`` queues ingestion. No other record is
  listed or read. Only the first capture of a process (and one after a foreign
  clock or a confirmed history problem) validates the whole history once.
* **Whole history, O(n), never per capture on the main thread:** restore plans,
  retention plans and Review Snapshot Retention, worker start-up recovery, and
  the worker's per-capture maintenance (one lock-free probe for retention and
  orphan manifests, store accounting when an upper bound does not settle the
  byte limit, and, after a dropped manifest, garbage collection; see "The
  worker's per-capture probes" below). The profile
  scan (``checkpoint_inventory.py``) walks session and snapshot folders with
  held POSIX descriptors or Windows handles
  (``checkpoint_filesystem._private_child``), a few system calls per snapshot.
  Each child is opened relative to its held parent with the checks the backend's
  own walk applies to every component (on Windows ``anchored_windows.open_child``:
  opened as itself, no link, junction or mount point, its own long name; see
  "Windows file anchoring" below); the legacy path backend keeps validating each
  folder's whole chain.
* **Profile transaction:** per capture the worker holds it only briefly, to
  adopt the ingested snapshot and to look for packs to compact (orphan suspects,
  usually none, are re-checked one by one). It holds it for a whole-history scan
  only when retention must remove points or its probe met a write in progress,
  and once at start-up recovery, so a capture normally never waits behind the
  worker's scans.

The bound matters because history grows without limit: when every allocation
scans every record and the worker's maintenance holds the transaction for three
scans, each message waits about 1.2 s once 700 snapshots are stored
(``performance.rst``, "Request capture cost and stored history").
``test_native_snapshot_history_scaling.py`` pins the contract
by counting the capture's system calls and record reads with 10 and 1,000 stored
snapshots on the POSIX backend, on the Windows backend through the host tests' NT
stand-in (``fake_nt_files.py``) and on the legacy path backend; the snapshot
benchmark's history phase ratchets the native capture with 10 and 1,000 stored
snapshots.

A new per-capture step must not list or read other records; whole-history work
belongs on the snapshot worker, lock-free where it only decides that nothing
needs doing.

The worker's per-capture probes
-------------------------------

The worker's maintenance runs after every capture and shares the GIL with
Blender's main thread, so its cost per capture is what a long session pays in
the background (``performance.rst``, "Per-edit cost over a long session"):

* **Probes re-read only changed records** (``checkpoint_inventory_cache``).
  The lock-free retention and orphan probes (``inventory(..., probe=True)``)
  keep each record's measurement with the state it was measured from: the
  ``lstat`` of its folder (device, inode, modification and change time) and of
  every file in it (device, inode, size, mode, links, modification and change
  time), taken before the record is read. A record whose folder and files are
  all unchanged gives exactly the measurement kept; anything added, removed,
  replaced or written in place changes one of them and is measured again. A
  measurement is kept only when all those times are more than two seconds
  older than its scan, so a later change cannot share a timestamp with it on a
  coarse file-system clock, and the change time cannot be set back by a
  program (FAT and exFAT report the modification time as the change time; on
  macOS snapshot storage on those volumes already fails closed on their
  AppleDouble ``._`` files, and the profile lives on the system volume).
  Checking a record costs one ``lstat`` of its folder and one of each
  file (two system calls for a deduplicated snapshot), nothing read. A probe
  has no inventory token (it never backs a removal). Every scan under the
  profile transaction (restore plans, removal plans and reviews, worker
  recovery, timeline validation) still measures every record afresh. Only the
  POSIX descriptor backend keeps measurements; on Windows and the legacy path
  backend every probe measures every record.
* **Garbage collection only after a dropped manifest.** Only dropping a
  manifest (retention's orphan cleanup, or an ingestion replacing one) leaves
  chunks unreferenced, so ``clean_store`` recounts the store only when this
  process dropped one since it last collected it, and at least once per process
  (``snapshot_store_writer.garbage_possible``). A skipped collection deletes
  nothing: it can only leave garbage for later, never lose data.
* **One probe scan per run, and store bytes from a bound first.** When
  retention ends at its probe, the orphan probe uses that same inventory
  instead of scanning again (both are advisory). The retention probe first
  plans with an upper bound of the store's bytes (every stored chunk counted
  live, two aggregate queries, ``snapshot_accounting.StoreBound``); it reads
  every manifest for the exact accounting only when that bound does not
  already show the history within its limits. Store bytes matter only to the
  byte limit and the bound is never below them, so a plan the bound finds
  nothing to do for is one the exact accounting would find nothing to do for
  too; that run's summary then reports the bound's byte figures.
* **Pauses for the main thread** (``background_pauses``). The probes, store
  accounting and retention planning sleep 0.2 ms after every half millisecond
  of their own work, which hands the GIL to a waiting main thread at once
  instead of after Python's 5 ms switch interval. The budget is measured time:
  a sleep that overruns (0.2 ms asked takes about 0.3 ms on a Mac) earns more
  work before the next (up to 2 ms), so pauses take at most 30% of a pass.
  They never pause while holding the profile transaction, which the main
  thread's next capture needs, and garbage collection, which holds the store's
  writer lock that another app's ingestion waits for, does not pause at all;
  only the worker's thread pauses.
* **A damaged manifest** (a chunk list that does not decompress) is a store
  accounting issue, which blocks removal plans as any unreadable store does.
  A probe the store bound ends never reads manifests, so it does not report
  one; the next exact plan does.

A probe still calls ``lstat`` for every record, so it stays linear in the history,
but a few system calls per record (which release the GIL) replace reading,
validating and fingerprinting each one: in the long-session replay a capture's
maintenance at 500 edits (about 600 snapshots) takes about 0.1 billion
instructions, against about 2.0 billion without the measures above
(``performance.rst``, "Per-edit cost over a long session"). ``test_native_snapshot_probe_cache.py``
pins that a probe always equals a fresh scan through every kind of change,
reads an unchanged settled history not at all and measures recent or locked
records afresh, that a run that removes nothing scans once, the bound and its
exact fallback, when garbage collection runs, and when the worker pauses.

Windows file anchoring
----------------------

The file tools, ``angelo_studio_export``, file journals and restores, and every
snapshot read, write and removal go through ``checkpoint_filesystem``. On macOS
and Linux a folder is a directory descriptor (``openat``/``O_NOFOLLOW``). On
Windows (``anchored_windows.py`` over the ctypes binding ``windows_nt.py``) it is
a directory handle with the same guarantee; there is no path-string fallback on
either.

**Threat model.** The inputs are untrusted: the relative paths a model chooses,
and a workspace that may hold hostile entries (a cloned project with symbolic
links, junctions or mount points pointing outside it, 8.3 short-name aliases,
alternate data streams) or be changed by another process while Angelo works
(folders renamed or replaced by links between Angelo's check and its effect).
The guarantee: every read, write, publication, replacement and deletion acts on
an object reached from the volume root through real directories only, so nothing
outside the selected workspace (or Angelo's private profile) is read, created,
changed or deleted, whatever is swapped during the operation. Out of scope, as on
POSIX: a program running as the same user can change the workspace directly, and
kernel filter drivers are trusted.

**How Windows keeps it.**

* *One name at a time, relative to a verified handle.* A path is opened from its
  volume root (``CreateFileW`` with ``FILE_FLAG_BACKUP_SEMANTICS`` and
  ``FILE_FLAG_OPEN_REPARSE_POINT``) one component at a time with NtCreateFile and
  ``OBJECT_ATTRIBUTES.RootDirectory``, the counterpart of ``openat``: no step
  resolves a path string again, so renaming or replacing an ancestor after it was
  opened changes nothing. This is the recipe Rust's standard library adopted for
  CVE-2022-21658.
* *No link is followed.* Every component and file is opened with
  ``FILE_OPEN_REPARSE_POINT`` and ``OBJ_DONT_REPARSE`` (Windows 10 1709 and later;
  an older Windows rejects the flag and is retried without it, which keeps the
  guarantee because each open names one component that is itself opened as a
  reparse point). A name-surrogate reparse point (symbolic link, junction, mount
  point; ``IsReparseTagNameSurrogate``) is refused, never followed.
  ``FileWorkspace`` also refuses junctions lexically (``Path.is_junction``), as it
  refuses symbolic links.
* *Each component is its own long name.* GetFinalPathNameByHandleW (normalized,
  no volume) must name the opened object as requested, so an 8.3 alias such as
  ``GIT~1`` for ``.git`` or ``ENV~1`` for ``.env`` is refused even if it appeared
  after lexical admission. Paths Angelo walks are first written with long names
  (GetLongPathNameW, which expands aliases and resolves no link).
* *Inside the workspace by the handles, not the strings.* File tools, export and
  restore pass the workspace as ``within``: the final path of the opened folder
  (GetFinalPathNameByHandleW) must lie inside the final path of the workspace root
  opened in the same walk.
* *Effects act on the verified handle.* Rename-over (FileRenameInformationEx with
  POSIX semantics), no-overwrite publication (a new hard link, FileLinkInformation)
  and deletion (FileDispositionInformationEx) are NtSetInformationFile requests on
  the handle that was checked, so the object changed is the object checked.
  Background jobs (``long_jobs.rst``) add three effects with the same rule:
  creating a new child folder (NtCreateFile with ``FILE_CREATE`` relative to the
  verified parent; ``mkdirat`` on POSIX), a no-overwrite hard link into another
  verified folder (FileLinkInformation naming that folder's handle; ``linkat``)
  and deleting one entry itself, an empty folder or a link included
  (FileDispositionInformationEx on a handle opened as itself; ``unlinkat``).
  A job never moves, renames or replaces a file: a taken output name gets the
  next free one (``long_jobs.rst``, "Taken names").
  File systems without the extended classes use the legacy ones. Truncation
  (``O_TRUNC``) happens through the handle after the checks: NtCreateFile's
  overwrite dispositions act first, and on NTFS ``FILE_OVERWRITE`` with
  ``FILE_OPEN_REPARSE_POINT`` empties a symbolic link before it can be refused.
  Every handle carries ``FILE_READ_ATTRIBUTES`` (a write-only handle could not be
  checked).
* *Names Windows would change are refused.* Alternate data streams (``:``),
  device names (``NUL``, ``con.txt``, ``COM1``...), trailing dots or spaces,
  separators and wildcards: the file tools never create a file Windows tools would
  see under another name. A drive-relative (``C:notes``) or root-relative
  (``\notes``) path is refused lexically.
* *The POSIX checks carry over unchanged:* regular single-link files only,
  identity rechecks before and after reads and copies, bounded sizes, and journals
  committed before an effect is acknowledged.

**Differences and limits.**

* A data reparse point that is not a link (OneDrive placeholder, deduplicated or
  compressed file) is a file: it is read through a reopened handle that must be
  the same file (same volume and file ID), and is never written in place; writes
  publish a new file. No OneDrive folder has been tested.
* Windows keeps only the owner-write bit of a POSIX mode (as read-only). Private
  storage relies on the per-user profile folder's access list (``%LOCALAPPDATA%``).
* NTFS journals directory metadata itself; Angelo does not flush directories
  (FlushFileBuffers would need write access to them).
* A file system without hard links (FAT, exFAT) refuses new-file publication, as
  on POSIX: the file tools and Studio export say so and change nothing.
* Handles are opened sharing read, write and delete (POSIX behaviour), so another
  program can still rename an open folder; the walk is unaffected because nothing
  is resolved again. NTFS itself refuses to rename an ancestor of a folder Angelo
  holds open (access denied, on Server 2022), so that swap cannot even begin
  while an operation runs.
* A file another program holds open without delete sharing (Python's ``open()``
  and many editors open files that way) cannot be replaced or deleted on Windows,
  which POSIX never refuses. The file tools then fail with nothing changed and the
  journal entry released. Snapshot retention opens every file of a snapshot for
  deletion before it writes the snapshot's tombstone, so such a file refuses the
  whole removal ("Another program has this file open; nothing was removed") and the
  next retention run tries again; otherwise the tombstone would outlive the file
  and the inventory would refuse all later retention.
* FAT and exFAT have no FileAttributeTagInfo; attributes come from FileBasicInfo,
  and a reparse point whose tag cannot be read counts as a link.
* WOF compression (``compact /exe``) is invisible to the backend: wof.sys hides its
  reparse point even from an open with ``FILE_OPEN_REPARSE_POINT``, so such a file
  reads and is replaced like any other. A reparse point no driver handles fails
  closed on read.

Validation
----------

Run the dependency-free policy, adversarial storage and restore-chain suite::

   python3 -m unittest discover -s tests/python/angelo_agent -p test_native_checkpoint_policy.py -v
   python3 -m unittest discover -s tests/python/angelo_agent -p test_native_files_checkpoints.py -v
   python3 -m unittest discover -s tests/python/angelo_agent -p test_native_ordered_recovery.py -v
   python3 -m unittest discover -s tests/python/angelo_agent -p test_native_file_anchoring.py -v
   python3 -m unittest discover -s tests/python/angelo_agent -p test_native_recovery_acknowledgements.py -v
   python3 -m unittest discover -s tests/python/angelo_agent -p 'test_native_snapshot_*.py' -v
   python3 -m unittest discover -s tests/python/angelo_agent -p 'test_native_windows_*.py' -v

``test_native_windows_anchoring.py`` drives the Windows backend on any host
through ``fake_nt_files.FakeNtApi`` (POSIX descriptors standing in for handles):
link and file refusals in the chain, links reported but never followed, 8.3
aliases, Windows-invalid names, publication/replacement/removal, placeholder
reads, ``within`` on final paths, and the file tools, restore and retention end
to end. ``test_native_windows_nt.py`` checks the binding's marshalling through
fake ntdll/kernel32 (relative opens, the OBJ_DONT_REPARSE fallback, rename/link
buffer layouts, legacy classes, directory records, error mapping). They prove the
logic, not NTFS. The stand-in keeps access rights per handle and models files
another program holds without delete sharing, so a handle that cannot read its
own attributes, or a retention that tombstones before it can delete, fails there
too. It needs ``O_PATH`` or ``O_SYMLINK`` and skips on Windows.

On Windows, ``tests/python/angelo_agent/windows_agent_checks.py ntfs``
(``windows_ntfs_checks.py``; an administrator) drives the real backend on NTFS
and on a FAT32 virtual disk, with CI's Python and inside ``Angelo.exe``: symbolic
links and junctions in the chain and as entries, a truncating open of a link, a
reparse tag no driver handles, a WOF-compressed file, 8.3 aliases, reserved and
stream names, Unicode names and case, a directory listing larger than one 64 KiB
record batch, a path of more than 400 characters with long paths off, a file held
open with and without delete sharing, an ancestor renamed under a held handle,
retention with a snapshot held open, the file tools with restore, publication,
read-only replacement and removal, and FAT32's legacy classes and refused
publication. The host suite, including these storage suites, also runs on
Windows (see "Windows validation" in ``agent_execution.rst``).

The ``test_native_snapshot_*`` suites cover segmentation (current, legacy,
compressed, truncated and corrupt files), dedup and sub-chunks, exact reassembly
and corruption detection, every crash/adoption state, verification failure,
concurrent capture and ingestion, garbage collection and compaction, automatic
retention with each protection, the 2 GiB default and unlimited settings, the
git signal against a fake ``git``, migration that deletes a full copy only after
verification, permissions, link refusal and the worker/host glue.
``test_native_snapshot_history_scaling.py`` adds the per-request contract above:
identical capture work with 10 and 1,000 stored snapshots on the POSIX, Windows
(NT stand-in) and legacy path backends, the whole-history check for a foreign
clock and after a locked check found damage, a link or junction in place of a
snapshot folder refused by the per-child walk, and lock-free maintenance that
falls back to the locked plan.

The existing ``test_blender_checkpoints.py`` must be run in an isolated native
Blender process with ``--background --factory-startup`` to verify real scene/file
restore and the unchanged original project. It signs in to nothing and calls no
provider.
``test_blender_ordered_recovery.py`` additionally verifies actual native
intermediate-scene/file recovery and a 22 MiB same-filename export/safety round
trip. ``test_blender_snapshot_dedup.py`` captures real snapshots after typical
edits, lets the worker migrate and ingest them, checks each recorded hash against
the captured file and restores every point through the product operator to its
exact captured scene. Run the native suites through ``run_native_suites.py``.
All fixtures are temporary and no provider, account or user project is used.
Controller tests additionally pin default submission capture and the deferred
capture-before-approval-effects/staging ordering. UI must verify truthful scope,
unavailable restore links, and exact user-confirmation candidates in the app.
