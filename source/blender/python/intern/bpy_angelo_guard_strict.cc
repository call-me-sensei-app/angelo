/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * "Strict script protection" (Ruling 17, latency first): a confinement layer with a measurable loss against Angelo's
 * own speed runs only when the person turns this on (OFF by default). One relaxed atomic, read with no Python, no
 * lock and no GIL, so a hot path (the allocator hook) can read it per call. Shared by every lane that gates a layer
 * on it: the layers read `angelo_strict_script_protection()`.
 *
 * Who sets it (the person's presence is C's to say, never Python's: the reviews of 93ef5b1 and a121c9a, S2 and S8):
 * - `sync_strict_script_protection()`: C reads the value saved in Angelo's add-on preferences itself
 *   (`ANGELO_Preferences.strict_script_protection`, a system ID property of its `bAddon`), so no caller hands C a
 *   value the preference doesn't hold. On is taken from anyone; Off only from one of its two legitimate causes: the
 *   person on this setting's own control (their press on its page switch, whose grant scope says so, or the ticket C
 *   minted for their press on its checkbox, taken here)
 *   or a preference read the person started (`BPY_angelo_guard_preferences_read`, around the add-on reset of a read
 *   that loads preferences: Load Factory Preferences, Revert; not one Python started). An add-on entry with none yet
 *   reads as the default, off; only a missing entry keeps the flag (S6, S9).
 * - `set_strict_script_protection(on)`: on for anything; off only as the sync allows it.
 * Neither runs on a confined thread. Both go through `store_strict`, which acts only on a change: a layer that
 * installs or removes something with the flag does it there.
 */

#include <atomic>
#include <vector>

#include "BKE_addon.h"
#include "BKE_idprop.hh"
#include "BLI_assert.hh"
#include "BLI_threads.hh"
#include "BLI_time.hh"
#include "DNA_userdef_types.h"

#include "bpy_angelo_guard_authority_core.hh"
#include "bpy_angelo_guard_confine.hh"
#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_strict.hh"

namespace blender::angelo_guard {

static std::atomic<bool> strict{false};
/* Main thread only: Blender's preference reads in progress, innermost last, each true when the person started it. */
static std::vector<bool> preferences_reads;

static const char *const ADDON = "angelo_agent";
static const char *const PREFERENCE = "strict_script_protection";
static const char *const TICKET = "prop.strict_script_protection"; /* Its checkbox's C ticket (prop.<name>). */
static const char *const NOT_THE_PERSON =
    "Only your own click in Angelo can turn Strict script protection off.";

/** The saved preference: 1 on, 0 off (an entry without preferences yet, or preferences without it: the default), -1
 * no add-on entry (the flag is kept). Main thread, with the GIL (preferences aren't shared). */
static int stored_preference()
{
  const bAddon *addon = BKE_addon_find(&U.addons, ADDON);
  if (addon == nullptr) {
    return -1;
  }
  const IDProperty *value = addon->prop ? IDP_GetPropertyFromGroup(addon->prop, PREFERENCE) : nullptr;
  return (value != nullptr && IDP_coerce_to_int_or_zero(value) != 0) ? 1 : 0;
}

/* Its switch on the AI Providers page: the grant scope of the person's press on it ("page.toggle" with the digest of
 * its value, grant_guard.value_digest("strict_script_protection") = sha256 of its repr). */
static const char *const PAGE_CONTROL =
    "page.toggle\n98bc0b87f7f29a8234786e35bb7aa81512c36a67bd46a06838a98079511af57a";

/** Whether turning it off is one of the person's on this setting's own control (never any other click of theirs:
 * the re-read of a121c9a, S8): Blender's preference read, their press on its switch on the AI Providers page (that
 * press's grant scope), their press on its checkbox in Blender's own Preferences (its ticket, taken now: single use),
 * or a test harness standing in for them. */
static bool off_allowed()
{
  const bool person_read = !preferences_reads.empty() && preferences_reads.back();
  return person_read || Scope::control() == PAGE_CONTROL || Scope::harness() ||
         authority().take(TICKET, "ui", BLI_time_now_seconds());
}

/** The one place the flag changes, from either entry point below, and only on a change: a layer that installs or
 * removes something with Strict (the reparse branch's wrappers) does it here. Never confined (both callers refuse a
 * confined thread first). Both callers are Python functions, so they hold the GIL, and the GIL is what makes the
 * load-then-store below correct: it serialises every call, so two calls can't both see one change and count Strict's
 * share of the hook's work word twice. (Both run on the main thread too, but that alone wouldn't serialise them.) */
static void store_strict(const bool on)
{
  if (strict.load(std::memory_order_relaxed) == on) {
    return;
  }
  /* The audit hook reads its per-event work as one word (`hook_event_work`): Strict's share is added before the
   * flag turns on and taken off after it turns off, so the word is nonzero whenever the flag is on. */
  if (on) {
    hook_event_work.fetch_add(HOOK_WORK_STRICT, std::memory_order_relaxed);
  }
  strict.store(on, std::memory_order_relaxed);
  if (!on) {
    hook_event_work.fetch_sub(HOOK_WORK_STRICT, std::memory_order_relaxed);
  }
  /* The reparse branch's wrappers exist only while it is on (wrap_lookup.cc, Ruling 17): installed or removed in one
   * batched walk, only on a real change. */
  if (on) {
    install_strict_lookups();
  }
  else {
    remove_strict_lookups();
  }
}

static PyObject *sync_strict(PyObject * /*self*/, PyObject * /*unused*/)
{
  BLI_assert(BLI_thread_is_main());
  if (refuse_if_confined("strict script protection")) {
    return nullptr;
  }
  const int stored = stored_preference();
  if (stored == 0 && strict.load(std::memory_order_relaxed) && !off_allowed()) {
    PyErr_SetString(PyExc_PermissionError, NOT_THE_PERSON);
    return nullptr;
  }
  if (stored >= 0) {
    store_strict(stored == 1);
  }
  return PyBool_FromLong(strict.load(std::memory_order_relaxed));
}

static PyObject *set_strict(PyObject * /*self*/, PyObject *value)
{
  if (refuse_if_confined("strict script protection")) {
    return nullptr;
  }
  const int on = PyObject_IsTrue(value);
  if (on < 0) {
    return nullptr;
  }
  if (on == 0 && strict.load(std::memory_order_relaxed) && !off_allowed()) {
    PyErr_SetString(PyExc_PermissionError, NOT_THE_PERSON);
    return nullptr;
  }
  store_strict(on != 0);
  Py_RETURN_NONE;
}

static PyObject *get_strict(PyObject * /*self*/, PyObject * /*unused*/)
{
  return PyBool_FromLong(strict.load(std::memory_order_relaxed));
}

PyMethodDef strict_methods[] = {
    {"sync_strict_script_protection", sync_strict, METH_NOARGS,
     "sync_strict_script_protection() -> bool: take \"Strict script protection\" from the saved preference, which C "
     "reads itself; off only from the person or a preference read (PermissionError otherwise; refused while "
     "confined)."},
    {"set_strict_script_protection", set_strict, METH_O,
     "set_strict_script_protection(on): on is open to anything; off only from the person on its own control or a "
     "preference read (refused while confined)."},
    {"strict_script_protection", get_strict, METH_NOARGS,
     "strict_script_protection() -> bool: whether \"Strict script protection\" is on."},
    {nullptr, nullptr, 0, nullptr},
};

}  // namespace blender::angelo_guard

bool angelo_strict_script_protection()
{
  return blender::angelo_guard::strict.load(std::memory_order_relaxed);
}

namespace blender {

void BPY_angelo_guard_preferences_read(const bool reading)
{
  std::vector<bool> &reads = angelo_guard::preferences_reads;
  if (!reading) {
    if (!reads.empty()) {
      reads.pop_back();
    }
    return;
  }
  /* A read the person started (a menu item or button: Blender's event loop runs the operator, no Python on the
   * stack) may turn Strict off; one Python started (an add-on, code a script left, Angelo's own settings import)
   * may not, and the preference gets On back (the re-read of 9b3f450, M3). */
  bool python = false;
  if (Py_IsInitialized()) {
    const PyGILState_STATE gil = PyGILState_Ensure();
    python = PyEval_GetFrame() != nullptr;
    PyGILState_Release(gil);
  }
  reads.push_back(!python);
}

}  // namespace blender
