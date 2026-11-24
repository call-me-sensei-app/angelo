/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The confinement of agent scripts (doc/angelo/script_confinement.rst): its state, the trampoline, the methods of
 * `_bpy._angelo_guard` and the C API the window manager, operators and the file gate read. Every piece of state is
 * C; Python gets functions that read it or add to it (only while no agent code runs), never an object to rebind.
 */

#include <Python.h>

#include "BKE_appdir.hh"
#include "BLI_path_utils.hh"

#include <algorithm>
#include <cstdio>
#include <iterator>
#include <cctype>
#include <cstring>
#include <mutex>
#include <new>
#include <set>
#include <string>
#include <unordered_set>
#include <vector>

#include "BLI_fileops.hh"

#include "bpy_angelo_guard_authority.hh"
#include "bpy_angelo_guard_authority_core.hh"
#include "bpy_angelo_guard_confine.hh"
#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_made.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_remote.hh"
#include "bpy_angelo_guard_confine_tree.hh"
#include "bpy_angelo_guard_sha256.hh"
#include "bpy_angelo_guard_strict.hh"

namespace blender::angelo_guard {

const char *const PRIVATE =
    "An agent script can't open Angelo's private settings, keys, approvals or connection records.";
const char *const PROGRAMS =
    "An agent script can't start other programs. Ask the person to run it, or to approve it as its own step "
    "outside the script.";
const char *const NATIVE = "An agent script can't load native code.";
const char *const GUARD = "An agent script can't change how Angelo checks scripts.";
const char *const STARTUP = "An agent script can't change what Angelo loads at startup.";
const char *const AUTORUN = "An agent script can't make code run when a file opens.";
const char *const TOOL_OUTPUT =
    "An agent script can't change files Angelo's tools saved for it to read. Write a copy somewhere else.";
/* Angelo's own code (not a script) asked a Blender writer to write where C says it may not (writable.cc). The words
 * of owned_paths.require_writable's refusal, which the interface translates. */
const char *const WRITE = "Angelo writes only where it made something, where you chose, or in its own folders";
const char *const OPERATOR =
    "An agent script can't run this operator: it opens another program, installs code or saves preferences.";
const char *const GRANT = "Not while an agent script is running: only your own click in Angelo can do this.";
const char POINTER[] =
    "An agent script can't move the pointer, send input or open a menu under it: only your own click in Angelo "
    "can do this.";
const char *const NETWORK =
    "An agent script can't use the network. Downloads go through Angelo's download tool.";
/** Shares refused to scripts, for the person (the UI asks `network_shares_refused()`; never a script), and a line on
 * the console. Each refusal takes the next serial, repeats included, so a UI that remembers the highest serial it
 * showed tells the person once per refusal; a share is listed once, with its latest serial. Kept to the last 16. */
struct RefusedShare {
  uint64_t serial;
  std::string share;
};
static std::mutex refused_shares_lock;
static std::vector<RefusedShare> refused_shares;
static uint64_t refused_shares_serial = 0;

void person_network_share_refused(const std::string &share)
{
  {
    std::lock_guard guard(refused_shares_lock);
    for (auto known = refused_shares.begin(); known != refused_shares.end(); ++known) {
      if (known->share == share) {
        refused_shares.erase(known); /* Moved to the end with its new serial: the latest last. */
        break;
      }
    }
    refused_shares.push_back({++refused_shares_serial, share});
    if (refused_shares.size() > 16) {
      refused_shares.erase(refused_shares.begin());
    }
  }
  std::fprintf(stderr,
               "Angelo: an agent script was refused the network share %s.\n",
               share.c_str());
}

/** Whether Python code is running on this thread now (a frame on its stack): an open an agent tool, an add-on or any
 * Python asked for, whatever context it passed. The person's own (an event's operator, the file browser, drag and
 * drop, Recent Files, the command line) runs with none. */
bool python_on_stack()
{
  if (!Py_IsInitialized() || !PyGILState_Check()) {
    return false; /* No GIL on this thread: no Python running here (startup's own load runs so). */
  }
  PyThreadState *state = PyThreadState_GetUnchecked();
  PyFrameObject *frame = state ? PyThreadState_GetFrame(state) : nullptr;
  Py_XDECREF(frame);
  return frame != nullptr;
}

static PyObject *guard_network_shares_refused(PyObject * /*self*/, PyObject * /*args*/)
{
  if (refuse_if_confined("network_shares_refused")) {
    return nullptr;
  }
  std::lock_guard guard(refused_shares_lock);
  PyObject *list = PyList_New(Py_ssize_t(refused_shares.size()));
  for (size_t i = 0; list != nullptr && i < refused_shares.size(); i++) {
    PyObject *pair = Py_BuildValue("(Ks)", (unsigned long long)refused_shares[i].serial,
                                   refused_shares[i].share.c_str());
    if (pair == nullptr) {
      Py_DECREF(list);
      return nullptr;
    }
    PyList_SET_ITEM(list, Py_ssize_t(i), pair);
  }
  return list;
}
const char *const PROCESS =
    "An agent script can't change how Angelo's process runs: its limits, priority, identity or what runs at fork.";
const char *const PREFERENCES = "An agent script can't change Preferences. Ask the person to change them.";
const char *const DRIVER_CODE =
    "An agent script can't write a driver expression that runs Python. A simple expression (math on the driver's "
    "variables) works.";
const char *const STYLE_MODULE = "An agent script can't set a style module: its Python runs when the scene renders.";

const char *const MARK = "<angelo-reviewed-script>";
const char *const DRIVER = "<bpy driver>";

constinit thread_local ConfineState<int> depth;
constinit thread_local ConfineState<bool> pending;
thread_local bool busy = false;
thread_local bool quiet = false;
std::atomic<bool> armed{false};
PyObject *script_confined_type = nullptr;

/* Agent code other than by name, held only here (GIL): file names compiled while confined, and code objects run
 * while confined (exec of a string, a file the script wrote and imported). */
static PyObject *tainted_names = nullptr; /* set of str */

/* Agent code that outlived its region (a script's function held where the region's end can't replace it, a
 * generator) runs at depth zero, where only the stack test confines it; and it can be on a stack only while it
 * exists. Agent code by identity lives in one set, `counted_codes`, and each member holds
 * `BLI_fileops_confine_codes` while it is in the set: a code object is added once, when it becomes agent code, and
 * leaves only when the code watcher sees it freed (DESTROY), so a weak reference cleared before a finalizer
 * resurrects the code can't drop it (BREAKIT break-it-confine-count-14a2d702b19 SHOULD-2). Its name is never read a
 * second time to decide (`_imp._fix_co_filename` renames live code):
 * - named as agent code (MARK, DRIVER): the watcher adds each one made under those names, however it is made
 *   (compiling, unmarshalling, `code.replace`, the constants of any of them), and the rename wrapper adds each one
 *   renamed to them (`count_agent_code_tree`);
 * - tainted (`taint`: code a confined thread compiled or ran under a name of no file, `<string>`): the whole tree of
 *   nested code constants, however deep (MUST-1 of the same review: a walk that stopped at depth 64 let deeper code
 *   through as installed code);
 * - a tainted name: `BLI_fileops_confine_always`, since code made under it earlier is agent code too. Only a script
 *   compiling under a file name of its own (rare) taints a name.
 * `agent_code` reads the names, then the set. Held with the GIL. Assumes no other code watcher resurrects a code
 * object after ours saw it freed (only C can register one; `_testcapi` is not bundled). */
static std::unordered_set<const PyCodeObject *> *counted_codes = nullptr;

/** For good: the counting can't be exact, so every hot path asks (2.0.0's cost, never an unconfined set). */
void confine_ask_always()
{
  BLI_fileops_confine_always.store(true, std::memory_order_relaxed);
}

static void codes_add()
{
  BLI_fileops_confine_codes.fetch_add(1, std::memory_order_relaxed);
}

/** Never below zero: a give-back with nothing to give back is an accounting error, so every path asks for good. */
static void codes_give_back()
{
  if (BLI_fileops_confine_codes.fetch_sub(1, std::memory_order_relaxed) <= 0) {
    BLI_fileops_confine_codes.fetch_add(1, std::memory_order_relaxed);
    confine_ask_always();
  }
}

/** Set for good when a code object that is agent code could not be added to `counted_codes` (an allocation
 * failure): from then on `agent_code` takes every code object not from a trusted file for agent code, so what was
 * missed is still confined (BREAKIT break-it-confine-depth-5dc512c07e1 SHOULD-1). */
static std::atomic<bool> identity_incomplete{false};

std::atomic<int> hook_event_work{0};
constinit thread_local CountedFlag installed_bytes_verified;

CountedFlag &CountedFlag::operator=(const bool value)
{
  if (value != value_) {
    value_ = value;
    hook_event_work.fetch_add(value ? 1 : -1, std::memory_order_relaxed);
  }
  return *this;
}

/** Count `code` once, by identity (GIL). False only when it could not be added (then every path asks, and identity
 * is incomplete for good). */
static bool code_count(const PyCodeObject *code)
{
  try {
    if (counted_codes == nullptr) {
      counted_codes = new std::unordered_set<const PyCodeObject *>();
    }
    if (counted_codes->insert(code).second) {
      codes_add();
    }
    return true;
  }
  catch (const std::bad_alloc &) {
    identity_incomplete.store(true);
    confine_ask_always();
    return false;
  }
}

/** Give back `code`'s count if it was counted (GIL). */
static void code_uncount(const PyCodeObject *code)
{
  if (counted_codes != nullptr && counted_codes->erase(code) != 0) {
    codes_give_back();
  }
}

static bool named_agent(const PyCodeObject *code)
{
  return name_is(code->co_filename, MARK) || name_is(code->co_filename, DRIVER);
}

/** Visit `code` and every code object nested in its constants, however deep (a finite, acyclic graph), each once:
 * constants that are code, and code inside tuple and frozenset constants (marshal can put it there; BREAKIT
 * break-it-confine-depth-5dc512c07e1 MUST-A (b)). False when the walk could not finish (identity incomplete). */
template<typename Fn> static bool walk_code_tree(PyObject *code, const Fn &visit)
{
  if (code == nullptr || !PyCode_Check(code)) {
    return true;
  }
  try {
    std::vector<PyObject *> stack{code};
    std::unordered_set<const PyObject *> seen;
    while (!stack.empty()) {
      PyObject *item = stack.back();
      stack.pop_back();
      if (!seen.insert(item).second) {
        continue;
      }
      if (PyCode_Check(item)) {
        if (!visit((PyCodeObject *)item)) {
          return false;
        }
        item = ((PyCodeObject *)item)->co_consts;
      }
      if (item != nullptr && PyTuple_Check(item)) {
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(item); i++) {
          PyObject *constant = PyTuple_GET_ITEM(item, i);
          if (PyCode_Check(constant) || PyTuple_Check(constant) || PyFrozenSet_Check(constant)) {
            stack.push_back(constant);
          }
        }
      }
      else if (item != nullptr && PyFrozenSet_Check(item)) {
        /* Iterating a frozenset runs no Python code. Its members stay alive while the constant does, which the
         * code object being walked holds. */
        PyObject *iterator = PyObject_GetIter(item);
        if (iterator == nullptr) {
          PyErr_Clear();
          identity_incomplete.store(true);
          confine_ask_always();
          return false;
        }
        while (PyObject *member = PyIter_Next(iterator)) {
          if (PyCode_Check(member) || PyTuple_Check(member) || PyFrozenSet_Check(member)) {
            stack.push_back(member);
          }
          Py_DECREF(member);
        }
        Py_DECREF(iterator);
        PyErr_Clear();
      }
    }
    return true;
  }
  catch (const std::bad_alloc &) {
    identity_incomplete.store(true);
    confine_ask_always(); /* Not walked to the end: every path asks, and the caller refuses. */
    return false;
  }
}

bool count_agent_code_tree(PyObject *code)
{
  if (code != nullptr && PyCode_Check(code) && named_agent((PyCodeObject *)code)) {
    /* Agent code by name: its whole tree is agent code, whatever names its constants carry (MUST-A). */
    return walk_code_tree(code, [](PyCodeObject *item) { return code_count(item); });
  }
  return walk_code_tree(code, [](PyCodeObject *item) { return !named_agent(item) || code_count(item); });
}

/* The denied operators. */
static std::set<std::string> &denied_operators()
{
  static std::set<std::string> ids;
  return ids;
}
static std::mutex denied_lock;
static bool installed = false;

int refuse(const char *reason, const char *what)
{
  /* The share a refused path reaches (re-read MUST-B): told to the PERSON only, never in the text the agent reads,
   * which could otherwise ask the person to connect a share a script chose (head-10/11 re-read SHOULD). */
  const bool network = reason == NETWORK || (reason != nullptr && std::strcmp(reason, NETWORK) == 0);
  const std::string share = network ? network_refusal_share(what) : network_refusal_share(nullptr);
  if (!share.empty()) {
    person_network_share_refused(share); /* The agent reads NETWORK, as for any network refusal. */
  }
  if (network) {
    /* A network refusal names nothing, from every caller: no path, URL, share, host, pipe or entry point, in the
     * message or in `what`, since a script reads both and the agent reads what it reads. The script knows what it
     * asked for; a share goes to the person only, above (doc/angelo/script_confinement.rst, "The network"; the
     * Windows share-notice proof read the share in the agent's text when `what` was echoed). */
    what = nullptr;
  }
  const bool unreadable = judged_unreadable && reason == PRIVATE;
  judged_unreadable = false;
  if (unreadable) {
    /* Not a private folder: a link on the way Angelo can't read, or one whose target it won't follow (a device, a
     * `..` after a name). Refused, failing closed, and said as such (re-read NIT). */
    reason = "An agent script can't use this path: a link on the way leads somewhere Angelo can't tell (a link it can't "
             "read, or a target it won't follow), so it is refused. Use the folder's own path, on a local drive, "
             "instead.";
  }
  if (judged_unrecorded) {
    /* The path rules refused it only because Angelo can't record the change (its record file in the user config
     * folder can't be read or written): fail closed, and say so. */
    judged_unrecorded = false;
    reason = "Angelo can't record what scripts change right now (its record in the user config folder can't be "
             "read or written), so a script can't write files until that is fixed.";
  }
  /* "reason (what)", as the agent reads it, with `reason` and `what` on the exception for Angelo's own code. */
  PyObject *message = (what && what[0]) ? PyUnicode_FromFormat("%s (%s)", reason, what) :
                                          PyUnicode_FromString(reason);
  PyObject *error = message ? PyObject_CallOneArg(script_confined_type, message) : nullptr;
  Py_XDECREF(message);
  if (error == nullptr) {
    return -1; /* Whatever failed is the exception now: the action is refused either way. */
  }
  PyObject *reason_text = PyUnicode_FromString(reason);
  PyObject *what_text = PyUnicode_FromString(what ? what : "");
  if (reason_text && what_text) {
    PyObject_SetAttrString(error, "reason", reason_text);
    PyObject_SetAttrString(error, "what", what_text);
  }
  Py_XDECREF(reason_text);
  Py_XDECREF(what_text);
  PyErr_SetRaisedException(error); /* Steals. */
  return -1;
}

bool name_is(PyObject *name, const char *text)
{
  return name != nullptr && PyUnicode_Check(name) && PyUnicode_CompareWithASCIIString(name, text) == 0;
}

bool agent_code(PyCodeObject *code)
{
  PyObject *name = code->co_filename;
  if (name_is(name, MARK) || name_is(name, DRIVER)) {
    return true;
  }
  if (tainted_names != nullptr && name != nullptr && PySet_Contains(tainted_names, name) == 1) {
    return true;
  }
  /* No lookup here can fail or set an exception: this runs inside the hook and from C callers that may hold one. */
  if (counted_codes != nullptr && counted_codes->count(code) != 0) {
    return true;
  }
  return identity_incomplete.load(std::memory_order_relaxed) && !trusted_name(name);
}

bool taint(PyObject *code)
{
  const bool whole = walk_code_tree(code, [](PyCodeObject *item) { return code_count(item); });
  verdicts_forget();
  return whole;
}

bool agent_name(PyObject *name)
{
  return name != nullptr && PyUnicode_Check(name) &&
         (name_is(name, MARK) || name_is(name, DRIVER) ||
          (tainted_names != nullptr && PySet_Contains(tainted_names, name) == 1));
}

void taint_name(PyObject *name)
{
  if (tainted_names != nullptr && name != nullptr && PyUnicode_Check(name)) {
    /* Before the name is agent code: code made under it earlier can't be counted, so every path asks for good. */
    confine_ask_always();
    PySet_Add(tainted_names, name);
    PyErr_Clear();
    verdicts_forget();
  }
}

bool frames_confined()
{
  PyThreadState *state = PyThreadState_Get();
  PyFrameObject *frame = PyThreadState_GetFrame(state); /* New reference, or null. */
  while (frame != nullptr) {
    PyCodeObject *code = PyFrame_GetCode(frame);
    const bool hit = agent_code(code);
    Py_DECREF(code);
    if (hit) {
      Py_DECREF(frame);
      return true;
    }
    PyFrameObject *back = PyFrame_GetBack(frame);
    Py_DECREF(frame);
    frame = back;
  }
  return false;
}

/** The one test of "a script is running here" every gate uses (doc/angelo/script_confinement.rst, "Regions"): this
 * thread is in a region (or ran a script outside one), or, with the GIL, agent code is on its stack (a generator or
 * callback the script left, resumed later). Safe without the GIL (a job's thread, a C caller): then only the depth
 * answers. The region's own machinery (the journal, the allocation record) keeps to regions, where it can be undone. */
/** A confined thread inside a call it made that runs without the GIL (an operator): still confined there. */
static constinit thread_local ConfineState<int> held;

bool confined_now()
{
  if (!BLI_fileops_confine_possible()) {
    return false; /* No thread holds a state below, no agent code exists for the stack test, nothing asks always. */
  }
  if (depth > 0 || pending || held > 0) {
    return true;
  }
  if (!armed.load(std::memory_order_relaxed)) {
    return false;
  }
  /* (`PyGILState_Check` says yes during finalization whatever the thread holds: check the thread's state.) */
  const bool python_here = Py_IsInitialized() && !Py_IsFinalizing() && PyThreadState_GetUnchecked() != nullptr &&
                           PyGILState_Check();
  return python_here && frames_confined();
}

/* -------------------------------------------------------------------- */
/** \name Module methods
 * \{ */

bool refuse_if_confined(const char *what)
{
  if (confined_now()) {
    refuse(GUARD, what);
    return true;
  }
  return false;
}

/** `native_file_modules()`: the modules refused on import while confined (read-only; for the inventory). */
static PyObject *guard_native_file_modules(PyObject * /*self*/, PyObject * /*args*/)
{
  PyObject *result = PyTuple_New(Py_ssize_t(std::size(NATIVE_FILE_MODULES)));
  for (size_t i = 0; result != nullptr && i < std::size(NATIVE_FILE_MODULES); i++) {
    PyTuple_SET_ITEM(result, Py_ssize_t(i), PyUnicode_FromString(NATIVE_FILE_MODULES[i]));
  }
  return result;
}

/** `wrapped_modules()`: the modules whose functions Angelo replaced, which a confined thread never makes again, sorted
 * (read-only; for the inventory; bpy_angelo_guard_confine_wrap_owners.cc). */
static PyObject *sorted_names(std::vector<std::string> names);

static PyObject *guard_wrapped_modules(PyObject * /*self*/, PyObject * /*args*/)
{
  return sorted_names(wrapped_module_names());
}

/** `note_replaced_module(name)`: Angelo's own Python replaced something in module `name` (a class it rebound): noted
 * like a C install site, so a confined thread never makes that module again. Refused while confined. */
static PyObject *guard_note_replaced_module(PyObject * /*self*/, PyObject *name)
{
  if (refuse_if_confined("note_replaced_module")) {
    return nullptr;
  }
  const char *text = PyUnicode_Check(name) ? PyUnicode_AsUTF8(name) : nullptr;
  if (text == nullptr) {
    PyErr_SetString(PyExc_TypeError, "note_replaced_module(name): a module name");
    return nullptr;
  }
  note_install_site(text);
  Py_RETURN_NONE;
}

/** `install_site_modules()`: every module an install site imported to replace something in, sorted (read-only; the
 * test checks `wrapped_modules()` against it). */
static PyObject *guard_install_site_modules(PyObject * /*self*/, PyObject * /*args*/)
{
  return sorted_names(install_site_modules());
}

static PyObject *sorted_names(std::vector<std::string> names)
{
  std::sort(names.begin(), names.end());
  PyObject *result = PyTuple_New(Py_ssize_t(names.size()));
  for (size_t i = 0; result != nullptr && i < names.size(); i++) {
    PyObject *text = PyUnicode_FromString(names[i].c_str());
    if (text == nullptr) {
      Py_CLEAR(result);
      break;
    }
    PyTuple_SET_ITEM(result, Py_ssize_t(i), text);
  }
  return result;
}

/** `denied_operators()`: the operators a confined thread may never run, sorted (read-only; for the inventory). */
static PyObject *guard_denied_operators(PyObject * /*self*/, PyObject * /*args*/)
{
  std::lock_guard lock(denied_lock);
  PyObject *result = PyTuple_New(Py_ssize_t(denied_operators().size()));
  Py_ssize_t i = 0;
  for (const std::string &id : denied_operators()) {
    PyObject *text = result ? PyUnicode_FromString(id.c_str()) : nullptr;
    if (text == nullptr) {
      Py_XDECREF(result);
      return nullptr;
    }
    PyTuple_SET_ITEM(result, i++, text);
  }
  return result;
}

static PyObject *guard_confined(PyObject * /*self*/, PyObject * /*args*/)
{
  return PyBool_FromLong(confined_now());
}

/** `confine_open()`: what hot paths read first (BLI_fileops.hh), as one number: the thread states, plus the agent
 * code objects alive, plus one if every path asks for good. Zero: no thread is confined. Read-only, for the suites. */
static PyObject *guard_confine_open(PyObject * /*self*/, PyObject * /*args*/)
{
  return PyLong_FromLong(BLI_fileops_confine_open.load(std::memory_order_relaxed) +
                         BLI_fileops_confine_codes.load(std::memory_order_relaxed) +
                         (BLI_fileops_confine_always.load(std::memory_order_relaxed) ? 1 : 0));
}

/** `confine_counts()`: the three parts apart, so a report shows a leaked state, live agent code or "always". */
static PyObject *guard_confine_counts(PyObject * /*self*/, PyObject * /*args*/)
{
  return Py_BuildValue("{s:i,s:i,s:O}",
                       "states",
                       BLI_fileops_confine_open.load(std::memory_order_relaxed),
                       "codes",
                       BLI_fileops_confine_codes.load(std::memory_order_relaxed),
                       "always",
                       BLI_fileops_confine_always.load(std::memory_order_relaxed) ? Py_True : Py_False);
}

static PyObject *guard_defer(PyObject * /*self*/, PyObject *function)
{
  if (!PyCallable_Check(function)) {
    PyErr_SetString(PyExc_TypeError, "defer: not callable");
    return nullptr;
  }
  return defer(function);
}

static PyObject *guard_is_deferred(PyObject * /*self*/, PyObject *value)
{
  return PyBool_FromLong(Py_TYPE(value) == &deferred_type);
}

static PyObject *guard_is_agent_code(PyObject * /*self*/, PyObject *value)
{
  return PyBool_FromLong(PyCode_Check(value) && agent_code((PyCodeObject *)value));
}

static PyObject *guard_add_denied_operators(PyObject * /*self*/, PyObject *ids)
{
  if (refuse_if_confined("add_denied_operators")) {
    return nullptr;
  }
  PyObject *iterator = PyObject_GetIter(ids);
  if (iterator == nullptr) {
    return nullptr;
  }
  std::lock_guard guard(denied_lock);
  while (PyObject *item = PyIter_Next(iterator)) {
    if (PyUnicode_Check(item)) {
      if (const char *text = PyUnicode_AsUTF8(item)) {
        denied_operators().insert(text);
      }
    }
    PyErr_Clear();
    Py_DECREF(item);
  }
  Py_DECREF(iterator);
  Py_RETURN_NONE;
}

static PyObject *guard_install_wrappers(PyObject * /*self*/, PyObject * /*args*/)
{
  if (refuse_if_confined("install_wrappers")) {
    return nullptr;
  }
  replace_references_begin(); /* One walk over the loaded modules for every entry point wrapped below. */
  const bool done = install_wrappers() == 0 && install_wrappers_io() == 0 && install_wrappers_lookup() == 0 &&
                   (install_makers(), true) &&
                   install_wrappers_opaque() == 0;
  replace_references_end();
  if (!done) {
    return nullptr;
  }
  recording_install(); /* Its own self-test: when it fails, every script is refused (run_confined). */
  Py_RETURN_NONE;
}

/** The person forgets what agent scripts changed in a folder (Preferences: "Forget Agent Changes in a Folder"):
 * only their own grant scope (never a script, never Angelo's automatic decision). Returns the records forgotten. */
static PyObject *guard_forget_changes(PyObject * /*self*/, PyObject *arg)
{
  if (refuse_if_confined("forget_changes")) {
    return nullptr;
  }
  if (!Scope::person()) {
    PyErr_SetString(PyExc_PermissionError, "Only your own click in Angelo can forget what agent scripts changed.");
    return nullptr;
  }
  const char *text = PyUnicode_Check(arg) ? PyUnicode_AsUTF8(arg) : nullptr;
  if (text == nullptr) {
    if (!PyErr_Occurred()) {
      PyErr_SetString(PyExc_TypeError, "forget_changes(folder): a folder path");
    }
    return nullptr;
  }
  int forgotten = -2;
  Py_BEGIN_ALLOW_THREADS; /* A walk of the folder's files. */
  forgotten = folders().forget(text);
  Py_END_ALLOW_THREADS;
  if (forgotten == -2) {
    PyErr_SetString(PyExc_NotADirectoryError, text);
    return nullptr;
  }
  if (forgotten < 0) {
    PyErr_SetString(PyExc_OSError, "The record of what agent scripts changed can't be written.");
    return nullptr;
  }
  return PyLong_FromLong(forgotten);
}

static PyMethodDef methods[] = {
    {"run_confined", (PyCFunction)(void (*)(void))guard_run_confined, METH_VARARGS | METH_KEYWORDS,
     "run_confined(source, filename, globals, call_id, compiler=None) -> findings: run an admitted script's text "
     "confined; the sweeps run before and after it."},
    {"confined", guard_confined, METH_NOARGS, "Whether this thread runs agent code now."},
    {"network_shares_refused", guard_network_shares_refused, METH_NOARGS,
     "network_shares_refused() -> [(serial, share), ...]: the network shares scripts were refused, latest last, each "
     "with the serial of its latest refusal (one per refusal, repeats included), for the person (refused while "
     "confined)."},
    {"confine_open", guard_confine_open, METH_NOARGS,
     "Confined states set on any thread, plus agent code objects alive, plus one if every path asks for good: zero, no "
     "thread is confined."},
    {"confine_counts", guard_confine_counts, METH_NOARGS,
     "confine_counts() -> {'states', 'codes', 'always'}: the parts of confine_open() apart."},
    {"defer", guard_defer, METH_O, "defer(callable): it runs confined wherever it runs later."},
    {"is_deferred", guard_is_deferred, METH_O, "Whether a callable is a confined trampoline."},
    {"is_agent_code", guard_is_agent_code, METH_O, "Whether a code object is agent code."},
    {"add_roots", guard_add_roots, METH_VARARGS,
     "add_roots(private, startup, families, trusted, tool_output=()): add folders (add-only; refused while "
     "confined)."},
    {"add_bytecode_loaders", guard_add_bytecode_loaders, METH_O,
     "add_bytecode_loaders(codes): installed loaders that may unmarshal a .pyc's verified bytes (add-only)."},
    {"set_compiler", guard_set_compiler, METH_O,
     "set_compiler(function): Angelo's script compiler (once; refused while confined)."},
    {"native_file_modules", guard_native_file_modules, METH_NOARGS,
     "native_file_modules() -> tuple: modules refused on import while confined."},
    {"wrapped_modules", guard_wrapped_modules, METH_NOARGS,
     "wrapped_modules() -> tuple: modules whose functions are replaced, never made again while confined, sorted."},
    {"note_replaced_module", guard_note_replaced_module, METH_O,
     "note_replaced_module(name): Angelo's Python replaced something in this module (refused while confined)."},
    {"install_site_modules", guard_install_site_modules, METH_NOARGS,
     "install_site_modules() -> tuple: every module an install site imported to replace something in, sorted."},
    {"denied_operators", guard_denied_operators, METH_NOARGS,
     "denied_operators() -> tuple: the operators a confined thread may never run, sorted."},
    {"add_open_roots", guard_add_open_roots, METH_VARARGS,
     "add_open_roots(folders, private_names): folders inside a private one that scripts use freely (add-only; "
     "refused while confined)."},
    {"set_sweep", guard_set_sweep, METH_VARARGS, "set_sweep(begin, end): refused while confined."},
    {"add_denied_operators", guard_add_denied_operators, METH_O,
     "add_denied_operators(ids): operators a confined thread may never run (add-only)."},
    {"install_wrappers", guard_install_wrappers, METH_NOARGS,
     "Replace the Python entry points a script could escape through (refused while confined)."},
    {"watch_modules", guard_watch_modules, METH_O,
     "watch_modules(names): journal what a confined script changes in these modules' namespaces (and their "
     "submodules'), the builtins and sys; refused while confined."},
    {"run_work", run_work, METH_VARARGS,
     "run_work(item, *extra): run a WorkItem, confined when a confined thread made it; anything else is refused."},
    {"judge_path", guard_judge_path, METH_VARARGS, "judge_path(path, write=False) -> 'private', 'startup' or ''."},
    {"angelo_package", guard_angelo_package, METH_O,
     "angelo_package(name): Angelo's package, whose frames read what a script's can't (once; refused while "
     "confined)."},
    {"forget_changes", guard_forget_changes, METH_O,
     "forget_changes(folder) -> int: forget what agent scripts changed in a folder (only the person's own grant "
     "scope)."},
    {nullptr, nullptr, 0, nullptr},
};

/** \} */

}  // namespace blender::angelo_guard

namespace blender {

using namespace angelo_guard;

static bool gate_refused(const char *path, bool write)
{
  /* A content write keeps the resolved folders: only a change of which folders exist or where they are forgets
   * them (BLI_FileGate::folders_changed, below; head-14 read SHOULD: a render frame cleared them every time). */
  return BPY_angelo_guard_path_refused(path, write);
}

/** Blender's own moves (BLI_rename, BLI_path_move) on a confined thread: no folder holding a link (tree.hh). A move
 * can change where a folder's name leads (Blender's C makes no link: a copied link is copied as content), so it
 * starts a new generation of the resolved folders (wrap_lookup.cc), as a folder made or anything removed does through
 * BLI_FileGate::folders_changed; a plain content write never does (head 14, SHOULD-2). */
static bool move_refused_by_gate(const char *from)
{
  if (from != nullptr && confined_now()) {
    lookup_cache_forget();
  }
  return from != nullptr && confined_now() && move_tree_verdict(from, -1) != TreeVerdict::Plain;
}

void BPY_angelo_guard_confine_install()
{
#ifdef ANGELO_GUARD_DIAG_NO_INSTALL
  return; /* A diagnostic build (CMake ANGELO_GUARD_DIAG_NO_INSTALL): never released, no confinement at all. */
#endif
  if (!installed) {
    installed = true;
    PySys_AddAuditHook(hook, nullptr);
    install_open_code_hook(); /* Before Python starts, as the audit hook. */
    /* The list of what confined threads change, kept across sessions (Folders::agent_made). */
    if (const std::optional<std::string> config = BKE_appdir_folder_id_create(BLENDER_USER_CONFIG, nullptr)) {
      char record[FILE_MAX];
      BLI_path_join(record, sizeof(record), config->c_str(), "angelo_agent_changed.txt");
      changes_record_set(record);
    }
    types_install(); /* Before Python starts: every class and metaclass inherits the journaling setattr. */
    /* Blender's own file operations ask the confinement too (`BLI_fileops_gate_set`). */
    static const BLI_FileGate gate = {gate_refused,
                                      BPY_angelo_guard_thread_depth,
                                      BPY_angelo_guard_thread_enter,
                                      BPY_angelo_guard_thread_leave,
                                      load_time_kept,
                                      driver_kept,
                                      style_module_kept,
                                      BPY_angelo_guard_confined,
                                      autorun_trusted,
                                      autorun_trust,
                                      script_id_made,
                                      script_property_set,
                                      lookup_refused_by_gate,
                                      move_refused_by_gate,
                                      lookup_cache_forget};
    BLI_fileops_gate_set(&gate);
    /* Blender's writers ask with the path they are about to write (BLI_angelo_writers.hh, _writers.cc). */
    writers_install();
  }
}

/** Whether `code`, being made now, is the agent's by birth: a confined thread made it (compiling, unmarshalling,
 * `code.replace`, each nested constant as it is made), whatever its name (BREAKIT break-it-confine-depth-5dc512c07e1
 * MUST-A: marshal can give a nested constant any name, a tuple can hide it from a walk, and code can be made from
 * bytes under `<string>`). The exceptions are decided by the bytes, never by the importing objects (a script can
 * build a loader, a spec and the frames around them; BREAKIT break-it-confine-fixes-b31e34840ca MUST-1): code
 * unmarshalled or compiled from an installed file's own content, as provenance just proved
 * (`installed_bytes_verified`), and code a fresh installed module's body generates as it loads. So the standard
 * library's own code stays its own. Saves and restores any exception the caller holds; never re-entered. */
static bool born_confined(PyCodeObject *code)
{
  static thread_local bool judging = false;
  if (judging || !armed.load(std::memory_order_relaxed) || !BLI_fileops_confine_possible()) {
    return false;
  }
  judging = true;
  PyObject *held = PyErr_GetRaisedException();
  bool born = confined_now();
  /* An installed file's own bytes, proved by provenance for this one call; a frozen module's bytes, compiled into the
   * binary, while Angelo's frozen-loader wrapper runs (`loading_frozen`); or code a fresh installed module's own body
   * generates as it loads (`namedtuple`'s and `@dataclass`'s methods: installed_import_globals, journal.cc). */
  if (born && (installed_bytes_verified || loading_frozen > 0 || installed_import_globals() != nullptr)) {
    born = false;
  }
  PyErr_Clear();
  PyErr_SetRaisedException(held);
  judging = false;
  return born;
}

/** Counts agent code as it is made, by name or by birth, and gives back each counted one as it is freed (above
 * `counted_codes`): the name is read only on CREATE; DESTROY asks the set. Called with the GIL. Never raises. */
static int agent_code_watch(PyCodeEvent event, PyCodeObject *code)
{
  if (event == PY_CODE_EVENT_CREATE) {
    if (named_agent(code) || born_confined(code)) {
      code_count(code);
    }
  }
  else if (counted_codes != nullptr && !counted_codes->empty()) {
    code_uncount(code);
  }
  return 0;
}

/** Watch code objects from before any agent code can exist (this runs as `_bpy` is made, before any add-on), once
 * per interpreter: the interpreter's own dict carries the mark, so a runtime started again (whose ids start again)
 * gets its watcher too. Code counted under an earlier interpreter is given back. Without a watcher every path asks
 * for good, as in 2.0.0. */
static bool agent_code_watch_install()
{
  PyObject *state = PyInterpreterState_GetDict(PyInterpreterState_Get()); /* Borrowed. */
  if (state != nullptr && PyDict_GetItemString(state, "_angelo_code_watch") != nullptr) {
    return false;
  }
  if (counted_codes != nullptr) {
    for (size_t i = counted_codes->size(); i > 0; i--) {
      codes_give_back();
    }
    counted_codes->clear();
  }
  if (state == nullptr || PyCode_AddWatcher(agent_code_watch) < 0 ||
      PyDict_SetItemString(state, "_angelo_code_watch", Py_True) != 0)
  {
    PyErr_Clear();
    confine_ask_always();
  }
  return true;
}

void BPY_angelo_guard_confine_add(PyObject *module)
{
  BPY_angelo_guard_confine_install(); /* A `bpy` module build starts Python elsewhere. */
  /* A new interpreter (Blender never starts one twice; a `bpy` module host might) gets its own tainted-name set: the
   * earlier one belongs to an interpreter that is gone, so it is left, never freed from this one. */
  if (agent_code_watch_install() || tainted_names == nullptr) {
    tainted_names = PySet_New(nullptr);
  }
  if (script_confined_type == nullptr) {
    script_confined_type = PyErr_NewExceptionWithDoc(
        "_bpy._angelo_guard.ScriptConfined",
        "An action the confinement refuses inside an agent script: args are (reason, what).",
        PyExc_PermissionError,
        nullptr);
    if (script_confined_type != nullptr) {
      /* Immutable: a script cannot change how a refusal is made. */
      ((PyTypeObject *)script_confined_type)->tp_flags |= Py_TPFLAGS_IMMUTABLETYPE;
      PyType_Modified((PyTypeObject *)script_confined_type);
    }
  }
  if (deferred_type.tp_flags == 0 && deferred_type_ready() != 0) {
    PyErr_Clear();
  }
  if (region_types_ready() != 0) {
    PyErr_Clear();
  }
  PyModule_AddFunctions(module, methods);
  PyModule_AddFunctions(module, owned_methods); /* What Angelo may destroy or signal (_owned.cc). */
  PyModule_AddFunctions(module, writable_methods); /* Where Angelo may write a path from its state (writable.cc). */
  PyModule_AddFunctions(module, writers_methods); /* The gate at Blender's writers (_writers.cc). */
  PyModule_AddFunctions(module, strict_methods);  /* "Strict script protection" (strict.cc, Ruling 17). */
  PyModule_AddObjectRef(module, "ScriptConfined", script_confined_type);
  PyModule_AddObjectRef(module, "Deferred", (PyObject *)&deferred_type);
  if (work_item_type.tp_flags == 0 && work_item_type_ready() != 0) {
    PyErr_Clear();
  }
  PyModule_AddObjectRef(module, "WorkItem", (PyObject *)&work_item_type);
  const std::pair<const char *, const char *> reasons[] = {
      {"PRIVATE", PRIVATE}, {"PROGRAMS", PROGRAMS}, {"NATIVE", NATIVE}, {"GUARD", GUARD},
      {"STARTUP", STARTUP}, {"TOOL_OUTPUT", TOOL_OUTPUT}, {"AUTORUN", AUTORUN}, {"OPERATOR", OPERATOR},
      {"GRANT", GRANT}, {"NETWORK", NETWORK}, {"PROCESS", PROCESS}, {"PREFERENCES", PREFERENCES},
      {"DRIVER_CODE", DRIVER_CODE}, {"STYLE_MODULE", STYLE_MODULE}, {"MARK", MARK}, {"DRIVER", DRIVER}};
  for (const auto &[name, text] : reasons) {
    PyModule_AddStringConstant(module, name, text);
  }
#ifdef ANGELO_GUARD_DIAG_NO_INSTALL
  PyModule_AddIntConstant(module, "DIAG_NO_INSTALL", 1); /* The add-on installs nothing either. */
#endif
}

bool BPY_angelo_guard_confined()
{
  return confined_now();
}

const char *BPY_angelo_guard_library_write_refusal()
{
  return new_library_refusal();
}

bool BPY_angelo_guard_hold()
{
  if (!confined_now()) {
    return false;
  }
  held++;
  return true;
}

void BPY_angelo_guard_release()
{
  held--;
}

void BPY_angelo_guard_arm()
{
  /* Code that runs from file data (a driver's Python) is the confinement's from its first run: a file opened before
   * any script ran is covered too. The driver's code is `<bpy driver>` (DRIVER), so the watcher counts it while it
   * exists; arming adds nothing to the count. */
  armed.store(true);
}

bool BPY_angelo_guard_thread_confined()
{
  return confined_now();
}

int BPY_angelo_guard_thread_depth()
{
  return confined_now() ? 1 : 0;
}

void BPY_angelo_guard_thread_enter(int added)
{
  depth += added;
}

void BPY_angelo_guard_thread_leave(int added)
{
  depth -= added;
}

PyObject *BPY_angelo_guard_defer_if_confined(PyObject *function)
{
  if (confined_now()) {
    return defer(function);
  }
  Py_INCREF(function);
  return function;
}

namespace angelo_guard {

bool names_a_url(const char *path)
{
  if (path == nullptr || !isalpha(static_cast<unsigned char>(path[0]))) {
    return false;
  }
  size_t length = 1;
  while (isalnum(static_cast<unsigned char>(path[length])) || path[length] == '+' || path[length] == '-' ||
         path[length] == '.' || path[length] == ',')
  {
    length++;
  }
  return path[length] == ':' && length >= 2;
}

}  // namespace angelo_guard

bool BPY_angelo_guard_path_refused(const char *path, bool write)
{
  if (path == nullptr || path[0] == '\0') {
    return false;
  }
  if (!confined_now()) {
    return false;
  }
  return angelo_guard::names_a_url(path) || folders().judge(path, write) != Refusal::None;
}

void BPY_angelo_guard_raise(const char *reason, const char *what)
{
  refuse(reason, what);
}

bool BPY_angelo_guard_read_refused_raise(const char *path)
{
  if (path == nullptr || path[0] == '\0' || !confined_now()) {
    return false;
  }
  if (angelo_guard::names_a_url(path)) {
    refuse(NETWORK, path);
    return true;
  }
  switch (folders().judge(path, false)) {
    case Refusal::None:
      return false;
    case Refusal::Network:
      refuse(NETWORK, path);
      return true;
    case Refusal::Startup:
    case Refusal::ToolOutput:
    case Refusal::Private:
      refuse(PRIVATE, path);
      return true;
  }
  refuse(PRIVATE, path);
  return true;
}

const char *BPY_angelo_guard_operator_refusal_id(const char *idname, const char *py_idname)
{
  {
    std::lock_guard guard(denied_lock);
    if (denied_operators().count(idname) || denied_operators().count(py_idname)) {
      return GRANT;
    }
  }
  static const std::set<std::string> fixed = {
      "WM_OT_url_open", "WM_OT_url_open_preset", "WM_OT_path_open", "WM_OT_save_userpref", "WM_OT_read_userpref",
      /* read_factory_settings resets the preferences and reloads every add-on inside the region (as does
       * read_homefile naming an app template, BPY_angelo_guard_operator_refusal): their re-run module bodies are no
       * import's own definition, so the region's end puts the old classes and handlers back and leaves the
       * person's add-ons broken (stale `bl_rna`) until a restart. */
      "WM_OT_read_factory_userpref", "WM_OT_read_factory_settings", "WM_OT_save_homefile", "WM_OT_quit_blender",
      "WM_OT_app_template_install",
      "RENDER_OT_play_rendered_anim", "FILE_OT_external_operation", "IMAGE_OT_external_edit", "SCRIPT_OT_reload",
      "SCRIPT_OT_python_file_run", "PREFERENCES_OT_addon_install", "PREFERENCES_OT_addon_enable",
      "PREFERENCES_OT_addon_disable", "PREFERENCES_OT_addon_remove", "PREFERENCES_OT_addon_refresh",
      "PREFERENCES_OT_app_template_install", "PREFERENCES_OT_studiolight_install",
      "PREFERENCES_OT_keyconfig_import", "PREFERENCES_OT_theme_install", "PREFERENCES_OT_script_directory_add",
      "PREFERENCES_OT_script_directory_remove", "PREFERENCES_OT_copy_prev",
      "PREFERENCES_OT_reset_default_preferences", "WM_OT_keyconfig_preset_add", "WM_OT_operator_preset_add",
      "SCRIPT_OT_execute_preset", "WM_OT_console_toggle"};
  if (fixed.count(idname) || strncmp(idname, "EXTENSIONS_OT_", 14) == 0) {
    return OPERATOR;
  }
  /* A menu or panel under the person's pointer takes their next click: never an agent script's to open. */
  static const std::set<std::string> popups = {"WM_OT_call_menu", "WM_OT_call_menu_pie", "WM_OT_call_panel",
                                               "WM_OT_call_asset_shelf_popover"};
  if (popups.count(idname)) {
    return POINTER;
  }
  /* Every other change to Preferences or the keymap: the person's configuration, saved at quit. */
  for (const char *prefix : {"PREFERENCES_OT_", "WM_OT_keyconfig_", "WM_OT_keymap_", "WM_OT_keyitem_"}) {
    if (strncmp(idname, prefix, strlen(prefix)) == 0) {
      return PREFERENCES;
    }
  }
  /* A project saved while it links a library the script brought in, with code that runs when a file opens. */
  if (strcmp(idname, "WM_OT_save_mainfile") == 0 || strcmp(idname, "WM_OT_save_as_mainfile") == 0) {
    return new_library_refusal();
  }
  return nullptr;
}

}  // namespace blender
