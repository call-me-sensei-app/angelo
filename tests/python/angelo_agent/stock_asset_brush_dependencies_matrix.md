<!-- SPDX-FileCopyrightText: 2026 Angelo Contributors -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Brush dependency and history supplement

`test_blender_stock_asset_brush_dependencies.py` uses the schema1 native asset
CAPI and actual stock `bpy.data.libraries.load` append. It covers Object Mode
with a load-only Brush, without paint activation, providers, mocks, or native
testing hooks. Its execution contract is serial BACKGROUND with the matching
native build and isolated runner resources.

| Case | Actual operations | Required acceptance |
| --- | --- | --- |
| Shared dirty state and commit | Create two artist Brushes sharing a real Texture, packed Image and Material; push an older history boundary; make unrecorded Brush, image, material, mesh and ID-property edits; begin, append a serialized Brush and bind it to existing shared IDs while retaining its own new dependencies; finish/close | Old data/reference graph and selection remain unchanged; old Texture, Material and Image gain exactly their one intentional user; real imported IDs exist |
| Undo and Redo | One actual `ed.undo`, then one `ed.redo` | Undo restores exact pre-import inventories, old native UIDs, dirty content, shared references and user counts; Redo restores imported UIDs, new dependencies, metadata, fake user, weak library reference and actual sharing |
| Committed save/reopen | Save actual result and reopen without UI/scripts | Old and new required IDs, packed bytes/pixels, scalar/string asset metadata, tags, custom properties, fake users and reference relationships survive; session UID equality is not required across reopen |
| Branch retirement | Start fresh, commit, Undo, then make a real scene-property edit/new history branch; lower ordinary history limit and make six bounded pushes; Undo/Redo the remaining ordinary branch | Import Redo is unavailable after branching; pruning and ordinary branch Undo/Redo preserve old/shared IDs and user counts and do not resurrect imported IDs; baseline data survives save/reopen |
| Checked rollback | Start fresh, begin, perform the real append and shared-reference writes, then request `keep=False`; finish/close; save/reopen | Stored native receipt is RESTORED with exact flags, no pending owner; no new Brush/dependency residue, old/shared data and user counts equal the dirty baseline |

Every transaction requires `asset_begin`, `asset_finish`, `asset_status`,
`asset_close`, `asset_pending`, `keep_background_document`, and the current native
capabilities. Missing APIs fail. Terminal status must be stored rather than
replayed; close must return a matching closed receipt. An unsettled scope stays
in `RETAINED_SCOPES` and aborts the fixture. There is no fallback import or guessed
successful cleanup.

The source contains a new packed Image/Texture/Material closure. After append,
the new Brush uses the existing artist Texture as its primary texture and also
holds real ID-property references to the artist Image and Material. Its untouched
mask texture and asset-material reference retain genuinely new dependencies.
Thus this exercises both preserving baseline shared IDs and retiring/recreating
new IDs; ordinary append name matching is not mistaken for dependency reuse.
All RNA references are reacquired after Undo and file reads.

The six ordinary pushes exercise actual branch/pruning lifecycles through native
history. Without test-only hooks they do not report a private destructor count,
prove memory safety, or measure allocation reclamation. Memory-safety validation still requires the matching native build and its
normal diagnostics. This fixture
covers local appended dependencies; externally linked/packed libraries and
paint-mode activation remain separate validation scopes.

The runner supplies factory startup, isolated provider-free HOME/resources,
`ANGELO_QA_DIR`, disabled autoexec, and a process-tree timeout. The 300-second
limit is a conservative maximum for three tiny append transactions, six memfile
reads, three save/reopen cycles, and bounded branch pushes; it is not a measured
or expected duration. Missing APIs, operator poll failures, and timeouts fail;
there are no platform or missing-feature skips.

The current native adjunct deliberately preserves each imported template's
recorded session UID across Undo/Redo: recreation assigns that UID before Main
insertion and postdecode checks its presence, type, and reference graph.
Ordinary dependent IDs retain their recorded UID through memfile decoding.
The fixture therefore checks new UID equality across Undo/Redo as well as old
artist UID preservation. Normal file reopen comparisons omit all session UIDs
and compare persisted content and resolved weak-reference paths instead.

Only after all three case groups finish does the script write
`stock-asset-brush-dependencies.json` with `success: true`, `phase: complete`,
`mode: OBJECT_LOAD_ONLY`, and these exact labels:

- `shared_dirty_commit_undo_redo_save_reopen`
- `history_branch_retirement_pruning_save_reopen`
- `actual_append_then_checked_rollback_save_reopen`

A successful run requires a fresh runner summary and this complete receipt.
Syntax parsing does not execute the native assertions.
