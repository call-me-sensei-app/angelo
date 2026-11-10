/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <string>

#include "BLI_function_ref.hh"
#include "BLI_span.hh"
#include "BLI_vector.hh"

#include "ED_sculpt_checkpoint.hh"

namespace blender {
struct bContext;
struct ReportList;
struct BlendFileData;
struct Main;
struct BLOCheckpointReadTokens;
struct WMCheckpointColdInstall;
struct WMCheckpointColdCallback;
struct WMCheckpointCapture;
struct CheckpointCaptureOwner;
struct WMCheckpointContent;
class XXH3_128Stream;
/** OS-available RAM policy, accounting for the live process before reserving new owners. */
bool WM_checkpoint_memory_limits(ed::sculpt_paint::checkpoint::CodecLimits &limits,
                                 uint64_t base_bytes, ReportList *reports,
                                 size_t *r_base_backing_bytes = nullptr);
/** The OS-available RAM (in a ``WITH_GTESTS`` build, #WM_checkpoint_test_available_memory's model
 * while one is set). #WM_checkpoint_memory_limits reads it shifted by any #WMCheckpointMemoryOffset. */
uint64_t WM_checkpoint_available_memory();
/** Main thread, scoped: every #WM_checkpoint_memory_limits inside reads the available RAM plus
 * ``offset`` (bytes, either sign, clamped to what a ``uint64_t`` holds). A Restore prepares its
 * target inside its safety capture; offsets let each side's admissions read the RAM as they did
 * when the target was prepared first: the capture's less the target's retained bound, the
 * target's plus what the capture took. Scopes nest (offsets add); each restores the previous. */
class WMCheckpointMemoryOffset {
  int64_t previous_;

 public:
  explicit WMCheckpointMemoryOffset(int64_t offset);
  ~WMCheckpointMemoryOffset();
  WMCheckpointMemoryOffset(const WMCheckpointMemoryOffset &) = delete;
  WMCheckpointMemoryOffset &operator=(const WMCheckpointMemoryOffset &) = delete;
};
/** Count a refusal by a comparison with a budget from #WM_checkpoint_memory_limits (that call's
 * own refusal included; BLI_checkpoint_budget.hh, which the codecs and the BASE read count
 * through). A Restore asks whether its safety capture or its target's preparation was refused by
 * such a comparison: only then can another order of the two change the verdict. A failure for
 * another reason (I/O, a changed owner, a malformed structure) does not count. The count only
 * grows; callers compare it across their work. */
void WM_checkpoint_budget_refused();
uint64_t WM_checkpoint_budget_refusals();
/** ``a + b`` clamped to ``[INT64_MIN, INT64_MAX]``; ``value`` clamped to ``INT64_MAX``. */
int64_t WM_checkpoint_memory_offset_add(int64_t a, int64_t b);
int64_t WM_checkpoint_memory_offset_of(uint64_t value);
/** Test seam, defined only in ``WITH_GTESTS`` builds (declared for the GTest sources, which do not
 * see that definition): while set, the available RAM is ``available`` less what Blender's
 * allocator holds beyond what it held when this was called (a model in which every guarded
 * allocation is what the OS sees, so footprints and admissions can be exercised at a chosen
 * limit); ``std::nullopt`` reads the OS again. Main thread. */
void WM_checkpoint_test_available_memory(std::optional<uint64_t> available);
/** Whether #WM_checkpoint_test_available_memory's model is in use (always false outside
 * ``WITH_GTESTS`` builds). The model counts guarded allocations only, so a caller crediting RAM
 * the model never charged (a Python buffer) must not credit it then. */
bool WM_checkpoint_memory_model_active();
/** Callback-free serialized Main + native Sculpt witness. Never publishes an Undo step. */
WMCheckpointContent *WM_checkpoint_content_capture(Main &main, ReportList *reports);
WMCheckpointContent *WM_checkpoint_content_capture(
    Main &main, ed::sculpt_paint::undo::MainUndoState *existing_sculpt, ReportList *reports);
WMCheckpointContent *WM_checkpoint_capture_content(WMCheckpointCapture &capture, ReportList *reports);
enum class WMCheckpointContentMatch { Match, Different, Unverified };
/** RetainedUI is only admissible during no-UI Restore teardown, after a Full source check. */
enum class WMCheckpointContentScope { Full, RetainedUI };
WMCheckpointContentMatch WM_checkpoint_content_matches(Main &main, const WMCheckpointContent &content,
                                                        ReportList *reports,
                                                        WMCheckpointContentScope scope = WMCheckpointContentScope::Full);
void WM_checkpoint_content_free(WMCheckpointContent *content);
/** Private synchronous ownership. Caller retains the actual store resources throughout. */
WMCheckpointCapture *WM_checkpoint_capture_begin(
    bContext *C, const ed::sculpt_paint::checkpoint::CaptureID &capture_id);
/** A capture component's content binding: the XXH3-128 of exactly the bytes the capture wrote to
 * it, recorded as each write was accepted. It never leaves native code (the Python bridge compares
 * it with a hash of the published bytes inside native, bpy_angelo_checkpoint.cc). */
struct WMCheckpointContentBinding {
  std::array<uint8_t, 16> hash{};
  uint64_t bytes = 0;
  bool present = false;
};
enum class WMCheckpointRole { Base, Runtime };
/** Write BASE. ``bind_content``: the caller will compare the published BASE with what was written
 * (a safety capture, or a volume whose timestamps two captures can share). BASE is also bound
 * whenever an Object is in Sculpt mode with a Sculpt session, decided before the writer runs: the
 * only state from which the capture can select Sculpt owners, whose pair is always compared. A
 * capture that selects Sculpt owners without a BASE binding is refused. */
bool WM_checkpoint_capture_write(WMCheckpointCapture &capture,
                                 const char *logical_path,
                                 int descriptor,
                                 bool bind_content,
                                 ReportList *reports);
/** The component's binding; ``present`` is false when the capture did not bind (or write) it. */
const WMCheckpointContentBinding &WM_checkpoint_capture_binding(const WMCheckpointCapture &capture,
                                                                WMCheckpointRole role);
/** write(2) semantics for one call. */
using WMCheckpointWriteFn = FunctionRef<int64_t(int descriptor, const void *data, size_t size)>;
/** This platform's write(2) (Windows: _write, at most INT_MAX bytes per call). */
int64_t WM_checkpoint_descriptor_write(int descriptor, const void *data, size_t size);
/** Write all of ``bytes`` with ``write_fn``, retrying interrupted (EINTR) and short writes, and
 * append to ``content`` (when given) exactly the bytes each call accepted, in order. False on an
 * error, a zero-byte write or an impossible count; the bytes accepted before it stay appended. */
bool WM_checkpoint_write_all(int descriptor,
                             Span<std::byte> bytes,
                             XXH3_128Stream *content,
                             WMCheckpointWriteFn write_fn);
bool WM_checkpoint_capture_runtime_write(WMCheckpointCapture &capture,
                                         int descriptor,
                                         ReportList *reports,
                                         FunctionRef<bool()> seal_encoded);
bool WM_checkpoint_capture_validate(const WMCheckpointCapture &capture);
uint64_t WM_checkpoint_capture_owner_count(const WMCheckpointCapture &capture);
/** POST/FAIL consumed once; false means invalid/already-consumed owner. */
bool WM_checkpoint_capture_post(WMCheckpointCapture &capture, bool success);
/** Release only: no callback, evaluation, flush, or history mutation. */
void WM_checkpoint_capture_close(WMCheckpointCapture *capture);
/** Consumes a fully prepared detached BASE. Install runs after normal LOAD_POST/evaluation/
 * editor initialization and before initial Undo. False after replacement is partial failure:
 * ``r_replaced`` then reports that the previous document was already freed, so the caller must
 * reload its protected safety capture. A successful Restore marks the document modified. */
/** Angelo: remember the document open now (its path, the relative spelling of its paths, its
 * compression), just before a Restore replaces it. Nothing a snapshot recorded is read. */
void WM_angelo_restore_begin(Main *bmain);
/** Angelo: make a Restore's working copy (the open document) the document remembered by
 * #WM_angelo_restore_begin: its path (empty: Untitled), relative paths and compression, an unsaved
 * state and a first Save over its file that asks once. Refused unless the open document is a
 * working copy and that document's path is absolute, not a working copy and a regular file (or
 * gone). */
bool WM_angelo_adopt_restored_document(bContext *C, ReportList *reports);
/** Angelo: whether ``filepath`` is a Restore's private working copy (wm_files.hh). */
bool WM_angelo_recovery_copy(const char *filepath);
/** Angelo's state folder, under whose ``checkpoints`` a Restore's working copies live. */
void WM_angelo_state_root_set(const char *root);
/** Whether the open document is a restored one whose first Save over its file is unconfirmed. */
bool WM_angelo_restored_save_pending(const Main *bmain);
bool WM_checkpoint_restore(bContext *C,
                           BlendFileData *prepared_base,
                           const char *logical_path,
                           FunctionRef<bool(Main &, WMCheckpointContentScope)> validate_source,
                           FunctionRef<bool(Main &)> validate_after_load_post,
                           FunctionRef<bool(Main &, CheckpointCaptureOwner *)> initialize_runtime,
                           FunctionRef<bool(Main &, CheckpointCaptureOwner *)> install,
                           FunctionRef<bool(Main &, CheckpointCaptureOwner *)> preserve_callback_edits,
                           FunctionRef<void()> end_runtime,
                           ReportList *reports,
                           bool *r_replaced = nullptr);
WMCheckpointColdInstall *WM_checkpoint_cold_prepare(
    Main &main, ed::sculpt_paint::checkpoint::DecodePlan &plan,
    const BLOCheckpointReadTokens &tokens, ReportList *reports);
bool WM_checkpoint_cold_runtime_begin(Main &main, WMCheckpointColdInstall &cold,
                                       CheckpointCaptureOwner *owner, ReportList *reports);
/** Retire the call-local evaluation hook before its owner/reports expire; idempotent. */
void WM_checkpoint_cold_runtime_end(WMCheckpointColdInstall &cold);
bool WM_checkpoint_cold_validate(Main &main, const WMCheckpointColdInstall &cold,
                                 bool before_editors, ReportList *reports);
bool WM_checkpoint_cold_install(bContext *C, Main &main, WMCheckpointColdInstall &cold,
                                CheckpointCaptureOwner *owner, ReportList *reports);
WMCheckpointColdCallback *WM_checkpoint_cold_callback_begin(
    Main &main, WMCheckpointColdInstall &cold, ReportList *reports);
/** Consumes the owned witness even if live_main is null; never dereferences saved stale owners. */
bool WM_checkpoint_cold_callback_end(Main *live_main, WMCheckpointColdInstall &cold,
                                     WMCheckpointColdCallback *owned, ReportList *reports);
/** After the current native operation unwinds, before releasing the outer Restore owner.
 * Must preserve retained after-callback edits without callbacks/evaluation; no-op without conflict. */
bool WM_checkpoint_cold_preserve_callback_edits(Main &main, WMCheckpointColdInstall &cold,
                                                CheckpointCaptureOwner *owner, ReportList *reports);
void WM_checkpoint_cold_free(WMCheckpointColdInstall *cold);
#ifdef WITH_GTESTS
/** Test seam: the next cold install refuses after replacement, before changing Sculpt state. */
void WM_checkpoint_test_fail_next_install();
/** Test seam: the next capture's BASE binding gate ignores Sculpt sessions, as if one appeared after the gate was
 * decided; the capture must then refuse its Sculpt owners. */
void WM_checkpoint_test_skip_next_sculpt_gate();
#endif
}  // namespace blender
