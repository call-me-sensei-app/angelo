<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Stock asset Undo fixture matrix

`test_blender_stock_asset_undo.py` uses the real native asset transaction and stock
Blender importers. It is registered as a serial BACKGROUND suite with a
600-second timeout in `native_suite_manifest.py`. It tests the native primitive;
it does not test approval, authenticated download, product UI wiring or mouse
interaction.

| Real import | Actual effect | Required checks |
| --- | --- | --- |
| Object | Append selected mesh Object, link Destination, add the placement point to its authored location | dirty preservation, one Undo/Redo action, sequential import, save/reopen, restoration after creation |
| Collection | Append selected Collection, link Destination, offset roots | same |
| Material | Append Material, replace the existing active mesh slot, preserve other slots | same |
| World | Append World, replace current scene World | same |
| Geometry Node group | Append actual pass-through group, add Nodes modifier | same |
| Shader Node group | Append selected group, load only | same |
| Brush | Append selected Brush, load only, no activation | same; no exemption for NO_MEMFILE_UNDO |
| GLB | Stock operator reads a generated binary triangle | same; actual scene mesh required |

Each baseline has an older history entry followed by unrecorded mesh-coordinate,
nested ID-property, image-pixel, camera/light, selection and frame edits. The
fixture reacquires RNA references after Undo. Its projection includes ID
inventories, collection edges, materials, modifiers, scene World, selection and
active object. Source files have distinct private copies; begin receives exact
original/private routing pairs.

No transaction is manually undo-pushed. The one explicit push precedes the dirty
edits and exposes a stale before-history boundary. Sequential checks require one
Undo action back to the first import result. Save/reopen compares content rather
than pointer or session UID identity. Its inventory expectation follows stock
user/fake-user retention: an unused World replaced by a later import may be
omitted on save. The baseline World/Material and load-only Shader/Brush have
explicit fake users. Required load-only assets cannot silently disappear. Only
new unused source Library rows are removed, matching the product helper.

The fixture requires schema1 transaction capabilities, `asset_begin`,
`asset_finish(scope, keep=bool)`, `asset_status`, `asset_close` and `asset_pending`.
It checks COMMITTED/RESTORED and exact published/restored/recovery_required flags,
then requires a closed native receipt, matching stored status and no pending
native owner before dropping its Python reference. Missing APIs are failures;
there is no older API fallback or mocked native outcome.

## Running the fixture

Use `run_native_suites.py` from a checkout matching the compiled binary and its
bundled add-on. Do not bypass the add-on guard or run the script directly. The
stock glTF importer must be available. Use a writable, short output root with no
other test or benchmark workload running. This fixture needs no provider login,
real-provider opt-in or network access.

The runner supplies isolated home/resources, factory startup, disabled autoexec,
`ANGELO_QA_DIR`, exit-code enforcement and process-tree timeout. The 600-second
bound is a maximum, not a measured duration.

macOS, with the matching binary's absolute path:

```sh
python3 tests/python/angelo_agent/run_native_suites.py --binary /absolute/path/to/Angelo.app/Contents/MacOS/Angelo --include test_blender_stock_asset_undo --jobs 1 --machine-factor off --timeout-scale 1 --output /private/tmp/angelo-stock-asset-qa
```

Windows PowerShell, with the matching binary and a short writable output root:

```powershell
py -3 tests/python/angelo_agent/run_native_suites.py --binary C:\path\to\angelo.exe --include test_blender_stock_asset_undo --jobs 1 --machine-factor off --timeout-scale 1 --output C:\aq
```

Both platforms must produce clean runner summaries/logs and a fresh
`stock-asset-undo.json` with success exactly true, phase complete and all eight
mode labels. Missing APIs, a missing glTF importer, failed operator polls,
timeouts and incomplete modes are failures, not platform skips.

## Coverage boundaries

The injected caller exception occurs after actual import and target writes. It
checks native restoration and absence of residue. It does not prove native
publication failure, restore failure or lost terminal delivery. Those cases need
actual native fault hooks and must query stored status without replay, report
RECOVERY_REQUIRED truthfully, refuse unsettled close and retain the scope.

Brush creation uses the focused noUndo delta adjunct because ordinary MemFile
preserves these IDs. Linked dependencies, old/shared Brush data, paint-mode
activation and history-branch retirement require the adjunct's additional native
controls. This load-only fixture does not cover them.

Dirty image bytes are explicitly packed before begin and compared numerically
after restoration, Undo/Redo and save/reopen. An Image ID's presence alone does
not establish preservation of its edited pixels.

Product producer invocation remains separate from this stock primitive. Recovery
UI also requires a GUI fixture: the background suite cannot verify visible
recovery notices, explicit UI/console recovery or document-replacement settlement.
A failed or unsettled native receipt stays retained and aborts the test. It never
earns successful cleanup by replaying an import, suppressing teardown or exiting
early.


## Recovery GUI companion

`test_blender_stock_asset_recovery_gui.py` is registered as the separate serial GUI
companion to this background suite. It uses an actual private Object append
and a bounded `bpy.context.temp_override(blend_data=None)` around native finish.
That context causes the prepublication Main check and the subsequent restore
admission to refuse before any decode, leaving a real RECOVERY_REQUIRED receipt
with published false, restored false and recoverable true. Restoring the context
allows one explicit `angelo.library_recover` operator invocation to restore and
close the actual retained native owner. No import, finish or recovery is replayed.

The fixture selects the real Angelo sidebar category after natural draw passes,
captures the actual panel pixels, checks production Panel/operator polls, and
compares mesh coordinates, nested custom properties, packed dirty pixels,
selection/active object, frame, camera/light, collection links and native ID
inventory against the fresh dirty preimage. It does not claim physical mouse
interaction. Captured screenshots still require visual review of labels, button
visibility and clipping; successful logical checks alone do not settle that review.

The safe unavailable-action case is the actual COMMITTED-before-close state.
The production panel shows an unavailable message and omits the recovery action;
the operator poll is false. It is not a disabled visible button, and it does not
establish a nonrecoverable RECOVERY_REQUIRED failure. A known committed owner is
closed through its actual discovered-owner path, then one ordinary Undo checks
the dirty preimage again.

There is no new shipping fault hook, OOM simulation or document replacement. Scene
or ViewLayer mismatch alone is unsuitable for this scenario because current native
finish immediately attempts a checked restore. The explicit Main=None override
instead refuses admission before dispatch while leaving the original native guard
valid. Native execution is still required to confirm this source-derived path.
The GUI companion has a 240-second outer bound, a 150-second logic deadline and
uses the existing GUI boot gate.
An exceptional begin return queries and retains the actual native pending scope.
An unobservable pending owner remains unknown; a failed receipt never treats that
query failure as absence. An unresolved failure retains its scope and writes a
failed receipt; a supervisor timeout is never credited as successful recovery or
cleanup.
