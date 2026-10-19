/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

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
/** OS-available RAM policy, accounting for the live process before reserving new owners. */
bool WM_checkpoint_memory_limits(ed::sculpt_paint::checkpoint::CodecLimits &limits,
                                 uint64_t base_bytes, ReportList *reports,
                                 size_t *r_base_backing_bytes = nullptr);
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
bool WM_checkpoint_capture_write(WMCheckpointCapture &capture,
                                 const char *logical_path,
                                 int descriptor,
                                 ReportList *reports);
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
#endif
}  // namespace blender
