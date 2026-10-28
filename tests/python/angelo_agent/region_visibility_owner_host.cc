/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */

/* Only dependencies are doubled. ACTUAL_RNA_CALLBACKS is replaced with current
 * rna_space.cc definitions by test_native_region_visibility_owner.py. */
#include <cstdio>
#include <functional>
#include <memory>
#include <stdexcept>
#include <vector>

enum eRegion_Flag { RGN_FLAG_HIDDEN = 1, RGN_FLAG_HIDDEN_BY_USER = 2 };
enum { RGN_TYPE_HEADER, RGN_TYPE_TOOL_HEADER, RGN_TYPE_HUD, RGN_TYPE_WINDOW };
enum { RGN_ALIGN_NONE = 0, RGN_ALIGN_TOP = 1, RGN_SPLIT_SCALE_PREV = 2,
       RGN_ALIGN_HIDE_WITH_PREV = 4, USER_REDUCE_MOTION = 1 };
struct { int uiflag = 0; } U;
struct ARegion { int flag = 0, alignment = RGN_ALIGN_TOP; bool overlap = false; };
struct RegionList { ARegion *first; int size = 4; };
struct ScrArea { ARegion regions[4]; RegionList regionbase{regions}; };
struct bScreen { int id; RegionList regionbase{nullptr, 0}; };
struct wmWindow { bScreen *screen; int scene; };
struct wmWindowManager { std::vector<wmWindow> windows; };
struct PythonOverrides { int mutations = 0; };
struct bContext {
  wmWindowManager *wm;
  wmWindow *win;
  ScrArea *area;
  ARegion *region;
  int scene;
  PythonOverrides *python;
  const void *store;
  void *popup = nullptr;
  void *gizmo = nullptr;
  bool python_initialized = true;
};
struct bContext_PyState { PythonOverrides *python; };
struct PointerRNA { void *owner_id; ScrArea *area; };
template<typename T> T id_cast(void *p) { return static_cast<T>(p); }
static ScrArea *rna_area_from_space(PointerRNA *p) { return p->area; }
static ARegion *BKE_area_find_region_type(ScrArea *a, int type) { return a ? &a->regions[type] : nullptr; }
static int BLI_findindex(const RegionList *list, ARegion *region) {
  for (int i = 0; i < list->size; i++) { if (&list->first[i] == region) { return i; } }
  return -1;
}
#define SET_FLAG_FROM_TEST(flags, test, flag) ((test) ? ((flags) |= (flag)) : ((flags) &= ~(flag)))
static void ED_region_tag_redraw(ARegion *) {}
static wmWindowManager *CTX_wm_manager(bContext *c) { return c->wm; }
static wmWindow *CTX_wm_window(bContext *c) { return c->win; }
static ScrArea *CTX_wm_area(bContext *c) { return c->area; }
static ARegion *CTX_wm_region(bContext *c) { return c->region; }
static ARegion *CTX_wm_region_popup(bContext *c) { return static_cast<ARegion *>(c->popup); }
static bScreen *WM_window_get_active_screen(wmWindow *w) { return w->screen; }
static int copies = 0, frees = 0;
#ifdef WITH_PYTHON
static bContext *global_python_context = nullptr;
static bool is_main_thread = true;
static int python_gets = 0, python_sets = 0, freed_global_contexts = 0;
static int BLI_thread_is_main() { return is_main_thread; }
static bool CTX_py_init_get(bContext *c) { return c->python_initialized; }
static bContext *BPY_context_get() { python_gets++; return global_python_context; }
static void BPY_context_set(bContext *c) { python_sets++; global_python_context = c; }
#endif
static bContext *CTX_copy(bContext *c) { copies++; return new bContext(*c); }
static void CTX_free(bContext *c) {
#ifdef WITH_PYTHON
  if (global_python_context == c) { freed_global_contexts++; }
#endif
  frees++; delete c;
}
static void CTX_py_state_push(bContext *c, bContext_PyState *s, void *) {
  s->python = c->python; c->python = nullptr;
}
static void CTX_store_set(bContext *c, const void *s) { c->store = s; }
static void CTX_wm_window_set(bContext *c, wmWindow *w) {
  if (c->python) { c->python->mutations++; }
  c->win = w; if (w) { c->scene = w->scene; } c->area = nullptr; c->region = nullptr;
}
static void CTX_wm_area_set(bContext *c, ScrArea *a) {
  if (c->python) { c->python->mutations++; }
  c->area = a; c->region = nullptr;
}
static void CTX_wm_region_set(bContext *c, ARegion *r) { c->region = r; }
static void CTX_wm_region_popup_set(bContext *c, void *r) { c->popup = r; }
static void CTX_wm_gizmo_group_set(bContext *c, void *g) { c->gizmo = g; }
struct Observation { int plain = 0, animated = 0, hud = 0; bContext context{}; ScrArea *area = nullptr; } seen;
static bool remove_popup = false;
static std::function<void(bContext *)> region_callback;
static void ED_region_visibility_change_update(bContext *c, ScrArea *a, ARegion *) {
  seen.plain++; seen.context = *c; seen.area = a;
  if (region_callback) { region_callback(c); }
  if (remove_popup && c->win) {
    ARegion *removed = c->win->screen->regionbase.first;
    c->win->screen->regionbase.size = 0;
    if (c->region == removed) { c->region = nullptr; }
    if (c->popup == removed) { c->popup = nullptr; }
  }
}
static void ED_region_visibility_change_update_animated(bContext *c, ScrArea *a, ARegion *) {
  seen.animated++; seen.context = *c; seen.area = a;
  if (region_callback) { region_callback(c); }
}
static void ED_region_toggle_hidden(bContext *c, ARegion *r) {
  r->flag ^= RGN_FLAG_HIDDEN; seen.context = *c;
}
namespace ui { static void ED_area_type_hud_ensure(bContext *, ScrArea *) { seen.hud++; } }

// ACTUAL_RNA_CALLBACKS

static int failures = 0, cases = 0;
static void check(bool good, const char *name) {
  cases++;
  if (!good) { std::printf("FAIL: %s\n", name); failures++; }
}
static bool unchanged(const bContext &a, const bContext &b) {
  return a.wm == b.wm && a.win == b.win && a.area == b.area && a.region == b.region &&
         a.scene == b.scene && a.python == b.python && a.store == b.store &&
         a.popup == b.popup && a.gizmo == b.gizmo && a.python_initialized == b.python_initialized;
}

int main() {
  bScreen active{1}, other{2}, inactive{3};
  ScrArea a{}, unrelated{};
  wmWindowManager wm{{{&active, 10}, {&other, 20}}};
  PythonOverrides python;
  bContext c{&wm, &wm.windows[0], &a, &a.regions[RGN_TYPE_WINDOW], 99, &python, &unrelated};
  c.popup = &unrelated; c.gizmo = &unrelated;
  PointerRNA p{&inactive, &a};
  const bContext original = c;
  rna_Space_show_region_header_set(&p, false);
  rna_Space_show_region_header_update(&c, &p);
  check(!rna_Space_show_region_header_get(&p), "inactive header flag saved");
  check((a.regions[RGN_TYPE_TOOL_HEADER].flag & RGN_FLAG_HIDDEN) != 0, "inactive header also hides tool header");
  check(seen.plain == 0 && seen.animated == 0 && copies == 0, "inactive owner creates no runtime context or handlers");
  check(unchanged(c, original) && python.mutations == 0, "inactive update preserves caller");

  rna_Space_show_region_tool_header_set(&p, false);
  rna_Space_show_region_tool_header_update(&c, &p);
  rna_Space_show_region_header_set(&p, true);
  check(rna_Space_show_region_header_get(&p) && !rna_Space_show_region_tool_header_get(&p), "header show preserves tool-header preference");
  check(a.regions[RGN_TYPE_TOOL_HEADER].flag & RGN_FLAG_HIDDEN, "user-hidden tool header stays hidden");
  rna_Space_show_region_tool_header_set(&p, true);
  rna_Space_show_region_header_set(&p, false);
  rna_Space_show_region_header_set(&p, true);
  check(!(a.regions[RGN_TYPE_TOOL_HEADER].flag & RGN_FLAG_HIDDEN), "tool header returns with parent header");

  p.owner_id = &other;
  seen = {};
  a.regions[RGN_TYPE_HEADER].overlap = true;
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(seen.plain == 1 && seen.animated == 0, "second-window update does not gain animation");
  check(seen.context.win == &wm.windows[1] && seen.context.area == &a && seen.context.scene == 20,
        "second-window runtime uses actual owner");
  check(!seen.context.python && !seen.context.store && !seen.context.region, "foreign overrides are detached");
  check(!seen.context.popup && !seen.context.gizmo, "foreign popup and gizmo pointers are detached");
  check(unchanged(c, original) && python.mutations == 0, "custom scene and override dictionary stay unchanged");
  check(copies == frees, "temporary owner context released");

  c.win = nullptr;
  const bContext null_original = c;
  seen = {};
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(seen.context.win == &wm.windows[1] && seen.plain == 1, "null-window caller resolves owner");
  check(unchanged(c, null_original) && python.mutations == 0, "null-window custom scene preserved");

  c = original;
  p.owner_id = &active;
  seen = {};
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(seen.animated == 1 && seen.plain == 0, "current area retains animation");
  check(seen.context.region == original.region, "current region is retained for runtime callbacks");
  check(unchanged(c, original), "animated update preserves caller");
  c.region = &unrelated.regions[RGN_TYPE_WINDOW]; seen = {};
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(!seen.context.region && c.region == &unrelated.regions[RGN_TYPE_WINDOW], "foreign region is not transplanted or changed");
  c = original;
  for (int flag : {RGN_SPLIT_SCALE_PREV, RGN_ALIGN_HIDE_WITH_PREV}) {
    seen = {}; a.regions[RGN_TYPE_HEADER].alignment = flag;
    rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
    check(seen.plain == 1 && seen.animated == 0, "split and dependent regions do not animate");
  }
  a.regions[RGN_TYPE_HEADER].alignment = RGN_ALIGN_TOP;
  U.uiflag = USER_REDUCE_MOTION; seen = {};
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(seen.plain == 1 && seen.animated == 0, "Reduce Motion retained");
  U.uiflag = 0; c.area = &unrelated; seen = {};
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(seen.plain == 1 && seen.animated == 0, "same-window other area remains nonanimated");

  c = original; c.wm = nullptr; seen = {};
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(seen.plain == 0 && seen.animated == 0, "no window manager means no runtime owner");
  c = original; p.owner_id = &inactive; seen = {};
  const int before_copies = copies;
  a.regions[RGN_TYPE_HUD].flag = RGN_FLAG_HIDDEN;
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HUD, RGN_FLAG_HIDDEN_BY_USER);
  check(seen.hud == 1 && !(a.regions[RGN_TYPE_HUD].flag & RGN_FLAG_HIDDEN) && copies == before_copies,
        "HUD branch retains existing separate behavior");
  p.owner_id = &active;
  for (int i = 0; i < 100; i++) {
    rna_Space_show_region_header_set(&p, i % 2);
    rna_Space_show_region_header_update(&c, &p);
  }
  check(copies == frees && python.mutations == 0 && unchanged(c, original), "repeated updates retain no context and change no caller state");
  ARegion popup;
  active.regionbase = {&popup, 1};
  c.region = &popup; c.popup = &popup;
  a.regions[RGN_TYPE_HEADER].overlap = false;
  remove_popup = true;
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(!c.region && !c.popup, "removed owner popover is cleared from original context");
  other.regionbase = {&popup, 1};
  c.region = &popup; c.popup = &popup;
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(c.region == &popup && c.popup == &popup, "another window's live popover is unchanged");
  check(copies == frees, "popover cleanup retains no temporary context");
#ifdef WITH_PYTHON
  remove_popup = false; c = original; p.owner_id = &active;
  bContext prior = original;
  // Match the real Python poll boundary: it publishes the supplied context,
  // and the existing bpy_context_clear deliberately does not restore it.
  region_callback = [](bContext *temporary) { BPY_context_set(temporary); };
  global_python_context = &prior;
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(global_python_context == &prior, "exact prior global context restored, not merely caller C");
  check(freed_global_contexts == 0, "global context restored before temporary free");
  check(unchanged(c, original), "Python poll leaves caller context intact");

  global_python_context = nullptr;
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(global_python_context == nullptr, "null prior global context is restored exactly");

  global_python_context = &prior;
  region_callback = [](bContext *temporary) {
    BPY_context_set(temporary);
    throw std::runtime_error("region callback failure");
  };
  bool raised = false;
  try { rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN); }
  catch (const std::runtime_error &) { raised = true; }
  check(raised && global_python_context == &prior, "callback exception preserves exception and prior global context");
  check(copies == frees && freed_global_contexts == 0, "exception restores global before exactly one free");

  int depth = 0;
  region_callback = [&](bContext *temporary) {
    BPY_context_set(temporary);
    if (depth++ == 0) {
      rna_Space_bool_from_region_flag_update_by_type(temporary, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
      check(global_python_context == temporary, "nested update restores its outer live temporary context");
    }
    depth--;
  };
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(global_python_context == &prior && freed_global_contexts == 0, "nested unwind leaves no freed global context");
  check(copies == frees, "nested contexts are all freed");

  region_callback = nullptr;
  const int gets_before = python_gets, sets_before = python_sets;
  c.python_initialized = false;
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(python_gets == gets_before && python_sets == sets_before, "uninitialized Python never touches its module pointer");
  c.python_initialized = true; is_main_thread = false;
  rna_Space_bool_from_region_flag_update_by_type(&c, &p, RGN_TYPE_HEADER, RGN_FLAG_HIDDEN);
  check(python_gets == gets_before && python_sets == sets_before, "non-main thread never touches global Python context");
  is_main_thread = true;
  check(global_python_context == &prior && copies == frees, "guarded paths preserve state and free their contexts");
#endif
  std::printf("%d checks, %d failures\n", cases, failures);
  if (!failures) { std::puts("all region visibility owner cases passed"); }
  return failures ? 1 : 0;
}
