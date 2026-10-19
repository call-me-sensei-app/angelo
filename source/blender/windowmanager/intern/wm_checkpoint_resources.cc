/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
/** Private resource admission and immutable pre-POST safety content witness. */
#include <algorithm>
#include <cstring>
#include <limits>
#include <memory>
#ifdef WIN32
#  include <windows.h>
#elif defined(__APPLE__)
#  include <mach/mach.h>
#  include <sys/sysctl.h>
#else
#  include <sys/sysinfo.h>
#endif
#include "MEM_guardedalloc.h"
#include "DNA_ID.h"
#include "DNA_object_types.h"
#include "BLI_function_ref.hh"
#include "BLI_listbase.h"
#include "BLI_set.hh"
#include "BKE_global.hh"
#include "BKE_lib_query.hh"
#include "BKE_main.hh"
#include "BKE_report.hh"
#include "BLO_undofile.hh"
#include "BLO_writefile.hh"
#include "ED_sculpt.hh"
#include "ED_util.hh"
#include "wm_checkpoint.hh"
#include "../../editors/sculpt_paint/mesh/sculpt_main_undo_snapshot_access.hh"

namespace blender {
static uint64_t checkpoint_available_memory()
{
#ifdef WIN32
  MEMORYSTATUSEX status{};
  status.dwLength = sizeof(status);
  return GlobalMemoryStatusEx(&status) ? status.ullAvailPhys : 0;
#elif defined(__APPLE__)
  vm_statistics64_data_t status{};
  mach_msg_type_number_t count = HOST_VM_INFO64_COUNT;
  vm_size_t page_size = 0;
  const mach_port_t host = mach_host_self();
  const bool ok = host_page_size(host, &page_size) == KERN_SUCCESS &&
      host_statistics64(host, HOST_VM_INFO64, reinterpret_cast<host_info64_t>(&status), &count) == KERN_SUCCESS;
  mach_port_deallocate(mach_task_self(), host);
  /* macOS exposes free + inactive reclaimable pages rather than Windows' AvailPhys estimate.
   * The admission policy reserves half of this estimate for reclaim/allocator/other-app costs. */
  return ok ? (uint64_t(status.free_count) + status.inactive_count) * page_size : 0;
#else
  struct sysinfo status{};
  return sysinfo(&status) == 0 ? uint64_t(status.freeram) * status.mem_unit : 0;
#endif
}

bool WM_checkpoint_memory_limits(ed::sculpt_paint::checkpoint::CodecLimits &limits,
                                 const uint64_t base_bytes, ReportList *reports,
                                 size_t *r_base_backing_bytes)
{
  /* Available OS RAM already excludes the current Main, prepared targets and retained safety
   * buffers. Leave half for other applications/allocator overhead; split the other half among
   * BASE expansion, codec backing/scratch and cold graph/PBVH + safety/initial-Undo staging.
   * This is a finite caller policy, not a promise of successful OS allocation. */
  const uint64_t available = checkpoint_available_memory();
  const uint64_t reserve = available / 2;
  const uint64_t base_reserve = reserve / 4;
  if (!reserve || base_bytes > base_reserve)
  {
    BKE_report(reports, RPT_ERROR, "Insufficient available RAM for complete checkpoint preparation and safety");
    return false;
  }
  const size_t codec = size_t(std::min<uint64_t>(reserve / 8, SIZE_MAX / 8));
  if (!codec) { return false; }
  limits = {uint64_t(codec), codec, codec / 2};
  if (r_base_backing_bytes) { *r_base_backing_bytes = size_t(std::min<uint64_t>(base_reserve, SIZE_MAX)); }
  return true;
}

struct WMCheckpointContent {
  struct RetainedUI { const ID *id; uint session_uid; ID_Type code; };
  Main *source = nullptr;
  MemFile file{};
  ed::sculpt_paint::undo::MainUndoState *sculpt = nullptr;
  Vector<RetainedUI, 16> retained_ui;
  ~WMCheckpointContent()
  {
    BLO_memfile_free(&file);
    ed::sculpt_paint::undo::main_undo_state_free(sculpt);
  }
};

static bool checkpoint_retains_ui(const ID &id)
{
  return ELEM(GS(id.name), ID_WM, ID_SCR, ID_WS);
}

static bool checkpoint_ui_capture(Main &main, WMCheckpointContent &content,
                                  const size_t limit, size_t &charged)
{
  int64_t count = 0;
  ID *id;
  FOREACH_MAIN_ID_BEGIN (&main, id) {
    if (checkpoint_retains_ui(*id)) {
      if (count == INT64_MAX || size_t(count) >= limit / sizeof(WMCheckpointContent::RetainedUI)) {
        return false;
      }
      count++;
    }
  }
  FOREACH_MAIN_ID_END;
  charged = 0;
  if (count > content.retained_ui.capacity()) {
    const int64_t capacity = std::max(count, content.retained_ui.capacity() * 2);
    if (uint64_t(capacity) > limit / sizeof(WMCheckpointContent::RetainedUI)) { return false; }
    charged = size_t(capacity) * sizeof(WMCheckpointContent::RetainedUI);
    content.retained_ui.reserve(count);
  }
  FOREACH_MAIN_ID_BEGIN (&main, id) {
    if (checkpoint_retains_ui(*id)) { content.retained_ui.append({id, id->session_uid, GS(id->name)}); }
  }
  FOREACH_MAIN_ID_END;
  std::sort(content.retained_ui.begin(), content.retained_ui.end(),
            [](const auto &a, const auto &b) { return a.session_uid < b.session_uid; });
  uint previous = 0;
  for (const auto &entry : content.retained_ui) {
    if (!entry.session_uid || entry.session_uid == previous) { return false; }
    previous = entry.session_uid;
  }
  return true;
}

static const WMCheckpointContent::RetainedUI *checkpoint_ui_find(
    const WMCheckpointContent &content, const uint uid)
{
  const auto *found = std::lower_bound(content.retained_ui.begin(), content.retained_ui.end(), uid,
                                      [](const auto &entry, const uint value) {
                                        return entry.session_uid < value;
                                      });
  return found != content.retained_ui.end() && found->session_uid == uid ? found : nullptr;
}

static bool checkpoint_ui_still_retained(Main &main, const WMCheckpointContent &content)
{
  if (!(G.fileflags & G_FILE_NO_UI)) { return false; }
  int64_t count = 0;
  ID *id;
  FOREACH_MAIN_ID_BEGIN (&main, id) {
    if (checkpoint_retains_ui(*id)) {
      const auto *saved = checkpoint_ui_find(content, id->session_uid);
      /* Compare live membership before ever dereferencing an old saved pointer. */
      if (!saved || saved->id != id || saved->code != GS(id->name)) { return false; }
      count++;
    }
    else if (checkpoint_ui_find(content, id->session_uid)) { return false; }
  }
  FOREACH_MAIN_ID_END;
  return count == content.retained_ui.size();
}

/* Match Angelo's callback-free Undo serializer bookkeeping restoration. Embedded IDs matter. */
class CheckpointRecalcRestore {
  struct Saved { ID *id; uint before, after; };
  Vector<Saved> saved_;
  size_t charged_ = 0;
  int64_t expected_ = 0;
  bool valid_ = false;
  void remember(ID *id)
  {
    if (id) {
      if (saved_.size() >= expected_) { valid_ = false; return; }
      saved_.append({id, id->recalc_up_to_undo_push, id->recalc_after_undo_push});
    }
  }
  static void foreach_owner(Main &main, FunctionRef<void(ID *)> visit)
  {
    ID *id;
    FOREACH_MAIN_ID_BEGIN (&main, id) {
      visit(id);
      BKE_library_foreach_ID_link(&main, id, [&](LibraryIDLinkCallbackData *data) {
        if (data->cb_flag & IDWALK_CB_EMBEDDED) { visit(*data->id_pointer); }
        return IDWALK_RET_NOP;
      }, nullptr, IDWALK_READONLY);
    }
    FOREACH_MAIN_ID_END;
  }
 public:
  explicit CheckpointRecalcRestore(Main &main, const size_t limit)
  {
    /* Duplicate embedded references need no Set: all values are recorded before the writer
     * changes bookkeeping, so restoring an ID repeatedly restores the same exact values.
     * Count first without growing owned containers; reserve the complete vector before append. */
    bool admitted = true;
    foreach_owner(main, [&](ID *id) {
      if (!id || !admitted) { return; }
      if (expected_ == std::numeric_limits<int64_t>::max() ||
          size_t(expected_) >= limit / sizeof(Saved)) { admitted = false; return; }
      expected_++;
    });
    if (!admitted) { return; }
    if (expected_ > saved_.capacity()) {
      /* BLI Vector reserve grows to max(requested, 2*capacity). Charge the whole fresh buffer,
       * including its overlap with inline storage; allocator overhead has a separate reserve. */
      const int64_t capacity = std::max(expected_, saved_.capacity() * 2);
      if (uint64_t(capacity) > limit / sizeof(Saved)) { return; }
      charged_ = size_t(capacity) * sizeof(Saved);
      saved_.reserve(expected_);
    }
    valid_ = true;
    foreach_owner(main, [&](ID *id) { remember(id); });
    valid_ = valid_ && saved_.size() == expected_;
  }
  bool valid() const { return valid_; }
  size_t charged_bytes() const { return charged_; }
  ~CheckpointRecalcRestore()
  {
    for (const Saved &saved : saved_) {
      saved.id->recalc_up_to_undo_push = saved.before;
      saved.id->recalc_after_undo_push = saved.after;
    }
  }
};

static WMCheckpointContent *checkpoint_content_capture(
    Main &main, ed::sculpt_paint::undo::MainUndoState *existing_sculpt,
    const bool use_existing, ReportList *reports)
{
  ed::sculpt_paint::checkpoint::CodecLimits limits{};
  if (!WM_checkpoint_memory_limits(limits, 0, reports)) { return nullptr; }
  auto content = std::make_unique<WMCheckpointContent>();
  content->source = &main;
  const char *error = nullptr;
  content->sculpt = use_existing ? ed::sculpt_paint::undo::main_undo_state_retain(existing_sculpt) :
                                  ed::sculpt_paint::undo::main_undo_state_capture_checkpoint(
                                      main, limits.max_backing_bytes, &error);
  if (error) { BKE_report(reports, RPT_ERROR, error); return nullptr; }
  CheckpointRecalcRestore recalc(main, limits.max_backing_bytes);
  if (!recalc.valid()) {
    BKE_report(reports, RPT_ERROR, "Safety recalc witness could not be admitted within available RAM");
    return nullptr;
  }
  size_t ui_bytes = 0;
  if (!checkpoint_ui_capture(main, *content, limits.max_backing_bytes - recalc.charged_bytes(), ui_bytes)) {
    BKE_report(reports, RPT_ERROR, "Safety UI identity witness exceeds available RAM");
    return nullptr;
  }
  /* Reserve separate incremental owners: existing Sculpt/shared Main backing already reduces
   * OS availability. The bounded writer admits each fresh chunk/map/owner before allocation;
   * logical MemFile size includes retained sharing and must not be charged as a second copy. */
  const BlendMemFileWriteResult written = BLO_write_file_mem_bounded(
      &main, &content->file, G.fileflags | G_FILE_RECOVER_WRITE,
      limits.max_backing_bytes - recalc.charged_bytes() - ui_bytes);
  if (written != BlendMemFileWriteResult::Success) {
    BKE_report(reports, RPT_ERROR, written == BlendMemFileWriteResult::BudgetExceeded ?
        "Safety MemFile witness could not be admitted within available RAM" :
        "Safety MemFile witness could not be serialized for verification");
    return nullptr;
  }
  return content.release();
}

WMCheckpointContent *WM_checkpoint_content_capture(Main &main, ReportList *reports)
{
  return checkpoint_content_capture(main, nullptr, false, reports);
}
WMCheckpointContent *WM_checkpoint_content_capture(
    Main &main, ed::sculpt_paint::undo::MainUndoState *existing_sculpt, ReportList *reports)
{
  return checkpoint_content_capture(main, existing_sculpt, true, reports);
}

static bool checkpoint_memfiles_equal(const MemFile &a, const MemFile &b,
                                     const WMCheckpointContent *retained_ui)
{
  const MemFileChunk *x = static_cast<const MemFileChunk *>(a.chunks.first);
  const MemFileChunk *y = static_cast<const MemFileChunk *>(b.chunks.first);
  while (true) {
    /* Only the exact UI owners proven retained by no-UI Restore may differ during teardown.
     * Session UID zero (headers/DNA) and every replaced ID remain byte-exact. */
    while (x && retained_ui && checkpoint_ui_find(*retained_ui, x->id_session_uid)) {
      x = static_cast<const MemFileChunk *>(x->next);
    }
    while (y && retained_ui && checkpoint_ui_find(*retained_ui, y->id_session_uid)) {
      y = static_cast<const MemFileChunk *>(y->next);
    }
    if (!x || !y) { break; }
    if (x->id_session_uid != y->id_session_uid || x->size != y->size ||
        (x->size && std::memcmp(x->buf, y->buf, x->size) != 0)) { return false; }
    x = static_cast<const MemFileChunk *>(x->next);
    y = static_cast<const MemFileChunk *>(y->next);
  }
  if (x || y) { return false; }
  const int64_t n = a.shared_storage ? a.shared_storage->sharing_info_by_address_id.size() : 0;
  if (n != (b.shared_storage ? b.shared_storage->sharing_info_by_address_id.size() : 0)) { return false; }
  if (n) {
    for (const auto &item : a.shared_storage->sharing_info_by_address_id.items()) {
      const ImplicitSharingInfoAndData *other = b.shared_storage->sharing_info_by_address_id.lookup_ptr(item.key);
      if (!other || item.value.sharing_info != other->sharing_info || item.value.data != other->data) {
        return false;
      }
    }
  }
  return true;
}

WMCheckpointContentMatch WM_checkpoint_content_matches(Main &main, const WMCheckpointContent &content,
                                                        ReportList *reports,
                                                        const WMCheckpointContentScope scope)
{
  if (&main != content.source) { return WMCheckpointContentMatch::Different; }
  const bool retain_ui = scope == WMCheckpointContentScope::RetainedUI;
  if (retain_ui && !checkpoint_ui_still_retained(main, content)) {
    BKE_report(reports, RPT_ERROR, "No-UI Restore no longer retains the exact safety UI owners");
    return WMCheckpointContentMatch::Different;
  }
  /* Normal checkpoint Save already wrote these edit owners back into Main. Materialize any
   * later non-Sculpt edit changes before comparing that actual persistent source boundary. */
  for (Object &object : main.objects) {
    if (!(object.mode & OB_MODE_SCULPT)) { ED_editors_flush_edits_for_object(&main, &object); }
  }
  std::unique_ptr<WMCheckpointContent> current(WM_checkpoint_content_capture(main, reports));
  if (!current) {
    BKE_report(reports, RPT_ERROR, "Current safety contents could not be verified within the available resources/runtime");
    return WMCheckpointContentMatch::Unverified;
  }
  const bool equal = checkpoint_memfiles_equal(content.file, current->file, retain_ui ? &content : nullptr) &&
      bool(content.sculpt) == bool(current->sculpt) &&
      (!content.sculpt || ed::sculpt_paint::undo::main_undo_state_equals(*content.sculpt, *current->sculpt));
  if (!equal) {
    BKE_report(reports, RPT_ERROR, "Current document contents differ from the complete safety capture; capture safety again");
  }
  return equal ? WMCheckpointContentMatch::Match : WMCheckpointContentMatch::Different;
}
void WM_checkpoint_content_free(WMCheckpointContent *content) { delete content; }
}  // namespace blender
