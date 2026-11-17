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
#include <iterator>
#include <cstring>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include "BLI_fileops.hh"

#include "bpy_angelo_guard_authority.hh"
#include "bpy_angelo_guard_authority_core.hh"
#include "bpy_angelo_guard_confine.hh"
#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_sha256.hh"

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
const char *const PROCESS =
    "An agent script can't change how Angelo's process runs: its limits, priority, identity or what runs at fork.";
const char *const PREFERENCES = "An agent script can't change Preferences. Ask the person to change them.";
const char *const DRIVER_CODE =
    "An agent script can't write a driver expression that runs Python. A simple expression (math on the driver's "
    "variables) works.";
const char *const STYLE_MODULE = "An agent script can't set a style module: its Python runs when the scene renders.";

const char *const MARK = "<angelo-reviewed-script>";
const char *const DRIVER = "<bpy driver>";

thread_local int depth = 0;
thread_local bool pending = false;
thread_local bool busy = false;
thread_local bool quiet = false;
std::atomic<bool> armed{false};
PyObject *script_confined_type = nullptr;

/* Agent code other than by name, held only here (GIL): file names compiled while confined, and code objects run
 * while confined (exec of a string, a file the script wrote and imported). */
static PyObject *tainted_names = nullptr; /* set of str */
static PyObject *tainted_codes = nullptr; /* dict: id -> weak reference to the code (gone when the code is) */

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
  /* Neither lookup can fail (a str's and an int's hash never raise), so no exception is set or cleared here: this
   * runs inside the hook and from C callers that may hold one. */
  if (tainted_codes != nullptr && PyDict_GET_SIZE(tainted_codes) > 0) {
    PyObject *key = PyLong_FromVoidPtr(code);
    if (key != nullptr) {
      const bool found = PyDict_Contains(tainted_codes, key) == 1;
      Py_DECREF(key);
      return found;
    }
  }
  return false;
}

static void taint_depth(PyObject *code, int level)
{
  if (level > 64 || !PyCode_Check(code) || tainted_codes == nullptr) {
    return;
  }
  PyObject *key = PyLong_FromVoidPtr(code);
  if (key == nullptr) {
    PyErr_Clear();
    return;
  }
  if (PyDict_Contains(tainted_codes, key) == 1) {
    Py_DECREF(key);
    return;
  }
  /* Held weakly (N1): an entry goes when its code does, before its address can be reused. */
  static PyMethodDef forget = {"forget",
                               [](PyObject *dead_key, PyObject * /*reference*/) -> PyObject * {
                                 if (tainted_codes != nullptr) {
                                   PyDict_DelItem(tainted_codes, dead_key);
                                 }
                                 PyErr_Clear();
                                 Py_RETURN_NONE;
                               },
                               METH_O,
                               nullptr};
  PyObject *callback = PyCFunction_New(&forget, key);
  PyObject *reference = callback ? PyWeakref_NewRef(code, callback) : nullptr;
  if (reference != nullptr) {
    PyDict_SetItem(tainted_codes, key, reference);
  }
  Py_XDECREF(reference);
  Py_XDECREF(callback);
  Py_DECREF(key);
  PyObject *constants = ((PyCodeObject *)code)->co_consts;
  if (constants != nullptr && PyTuple_Check(constants)) {
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(constants); i++) {
      PyObject *item = PyTuple_GET_ITEM(constants, i);
      if (PyCode_Check(item)) {
        taint_depth(item, level + 1);
      }
    }
  }
  PyErr_Clear();
}

void taint(PyObject *code)
{
  taint_depth(code, 0);
  verdicts_forget();
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
static thread_local int held = 0;

bool confined_now()
{
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
  const bool done = install_wrappers() == 0 && install_wrappers_io() == 0 && install_wrappers_opaque() == 0;
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
  return BPY_angelo_guard_path_refused(path, write);
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
                                      script_property_set};
    BLI_fileops_gate_set(&gate);
    /* Blender's writers ask with the path they are about to write (BLI_angelo_writers.hh, _writers.cc). */
    writers_install();
  }
}

void BPY_angelo_guard_confine_add(PyObject *module)
{
  BPY_angelo_guard_confine_install(); /* A `bpy` module build starts Python elsewhere. */
  if (tainted_names == nullptr) {
    tainted_names = PySet_New(nullptr);
    tainted_codes = PyDict_New();
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
   * any script ran is covered too. */
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

bool BPY_angelo_guard_path_refused(const char *path, bool write)
{
  if (path == nullptr || path[0] == '\0') {
    return false;
  }
  if (!confined_now()) {
    return false;
  }
  return folders().judge(path, write) != Refusal::None;
}

void BPY_angelo_guard_raise(const char *reason, const char *what)
{
  refuse(reason, what);
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
