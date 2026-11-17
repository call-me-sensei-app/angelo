/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * What a confined script changes in Angelo's and the standard library's module namespaces, the builtins and `sys`,
 * journaled in C as it happens and put back when the script ends
 * (doc/angelo/script_confinement.rst, "Work that outlives the script").
 *
 * A module's namespace can be changed with no audit event (`module.name = x`, a function's `__globals__`), so the
 * hook cannot refuse it. CPython's dict watchers (3.12+) tell C *before* a watched dict changes; while the changing
 * thread is confined, the old value is journaled. When the outermost `run_confined` (or a trampoline) ends, the
 * journal is undone for what a rebinding replaces: a function, class, module or other callable, an UPPER_CASE
 * constant's immutable value, and any new callable that is not installed Python code. Data Angelo's own code
 * changes while the script runs stays as it set it. The journal costs nothing while no script runs, and nothing
 * per script: no snapshot is taken.
 */

#include <Python.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_record.hh"

namespace blender::angelo_guard {

static int watcher = -1;
static thread_local std::vector<JournalEntry> journal;
static PointerSet &namespaces() /* The module namespaces watched (the rest watched are module-level dicts). */
{
  static PointerSet set;
  return set;
}

std::vector<JournalEntry> &journal_entries()
{
  return journal;
}

/** Not `confined_now()`, by design: the journal is undone when a region ends, so it records only inside one. Code the
 * script left that resumes outside a region (a generator) is refused by every gate through `confined_now()`; there is
 * no region end to undo its changes, which doc/angelo/script_confinement.rst lists as a residual; its changes to
 * `sys.modules` are settled all the same (bpy_angelo_guard_confine_modules.cc). */
bool journaling()
{
  return (depth > 0 || pending) && !quiet;
}

/* Whether the script's code changed watched state while a region was settling (late), since the last take. */
static thread_local bool late_changed = false;

/* The frame running when this thread's region began to settle: frames below it are its caller's (a generator step's
 * own frame, when its region ends in a monitoring callback), not code run by the settling. */
static thread_local PyFrameObject *settle_base = nullptr;
static thread_local bool settling = false; /* An outermost region's end is settling (not any quiet work). */

void settle_begin()
{
  settle_base = PyEval_GetFrame(); /* Borrowed: alive while the region settles. */
  settling = true;
}

void settle_end()
{
  settle_base = nullptr;
  settling = false;
}

/** Whether a frame of the script's code started running since the region began to settle. */
static bool agent_on_stack()
{
  PyFrameObject *frame = (PyFrameObject *)Py_XNewRef(PyEval_GetFrame());
  bool found = false;
  for (int hops = 0; frame != nullptr && frame != settle_base && hops < 256 && !found; hops++) {
    PyCodeObject *code = PyFrame_GetCode(frame);
    found = agent_code(code);
    Py_DECREF(code);
    PyFrameObject *back = PyFrame_GetBack(frame);
    Py_DECREF(frame);
    frame = back;
  }
  Py_XDECREF(frame);
  return found;
}

bool journaling_late()
{
  /* A region's end runs its own work unjournaled (`quiet`), but the script's code can still run there (a key's
   * `__hash__`, an object's `__del__` as the undo releases it, a descriptor): what it changes is journaled anyway,
   * and undone once the region has settled (region.cc). */
  if (settling && quiet && depth > 0 && !Py_IsFinalizing() && agent_on_stack()) {
    late_changed = true;
    return true;
  }
  return false;
}

bool late_changes_take()
{
  const bool changed = late_changed;
  late_changed = false;
  return changed;
}

void journal_record(PyObject *owner, PyObject *key, PyObject *old, bool definition, bool type_entry, bool tool)
{
  journal.push_back({Py_NewRef(owner), Py_XNewRef(key), Py_XNewRef(old), definition || tool, type_entry, tool});
}

static void record(PyObject *dict, PyObject *key, PyObject *old, bool definition = false)
{
  const bool tool = !definition && angelo_writes(PyDict_GetItemString(dict, "__name__"));
  journal_record(dict, key, old, definition, false, tool);
}

/* The import system's own frames that execute a module: `_load_unlocked` (an import) and `_exec` (a reload), held
 * from install, before any script could replace them. */
static PyObject *import_codes[2] = {nullptr, nullptr};
static PyObject *frozen_files = nullptr; /* "<frozen NAME>" for each module frozen into the interpreter. */

void import_frames_install()
{
  PyObject *bootstrap = PyImport_ImportModule("_frozen_importlib");
  int i = 0;
  for (const char *name : {"_load_unlocked", "_exec"}) {
    PyObject *function = bootstrap ? PyObject_GetAttrString(bootstrap, name) : nullptr;
    PyObject *code = function ? PyObject_GetAttrString(function, "__code__") : nullptr;
    if (import_codes[i] == nullptr && code != nullptr && PyCode_Check(code)) {
      import_codes[i] = Py_NewRef(code);
    }
    Py_XDECREF(code);
    Py_XDECREF(function);
    i++;
  }
  Py_XDECREF(bootstrap);
  /* The interpreter's frozen modules, named as their code is (`<frozen os>`), held from install. */
  PyObject *imp = frozen_files == nullptr ? PyImport_ImportModule("_imp") : nullptr;
  PyObject *names = imp ? PyObject_CallMethod(imp, "_frozen_module_names", nullptr) : nullptr;
  PyObject *iterator = names ? PyObject_GetIter(names) : nullptr;
  PyObject *files = iterator ? PySet_New(nullptr) : nullptr;
  while (PyObject *name = files ? PyIter_Next(iterator) : nullptr) {
    if (PyObject *file = PyUnicode_FromFormat("<frozen %S>", name)) {
      PySet_Add(files, file);
      Py_DECREF(file);
    }
    Py_DECREF(name);
  }
  if (files != nullptr && !PyErr_Occurred()) {
    frozen_files = files;
  }
  else {
    Py_XDECREF(files);
  }
  Py_XDECREF(iterator);
  Py_XDECREF(names);
  Py_XDECREF(imp);
  PyErr_Clear();
  origins_install(); /* What the path-based finder searches, captured before any script (origins.cc). */
}

/** An installed file's own top level running (a module body), however it was started: the import system, a
 * loader's `exec_module` called directly, `runpy`. Its code is the file's (compiling under an installed name takes
 * the file's own bytes: bpy_angelo_guard_confine_provenance.cc), or a module frozen into the interpreter (`<frozen
 * os>`: its code comes only from the interpreter's own data, and the caller has already ruled out agent code, which
 * a script compiling under that name would be). */
static bool installed_module_body(PyCodeObject *code);

bool installed_body(PyCodeObject *code)
{
  return installed_module_body(code);
}

static bool installed_module_body(PyCodeObject *code)
{
  if (!PyUnicode_Check(code->co_name) || PyUnicode_CompareWithASCIIString(code->co_name, "<module>") != 0) {
    return false;
  }
  if (frozen_files != nullptr && PyUnicode_Check(code->co_filename) &&
      PySet_Contains(frozen_files, code->co_filename) == 1)
  {
    return true;
  }
  const char *file = PyUnicode_AsUTF8(code->co_filename);
  PyErr_Clear();
  return file != nullptr && file[0] != '<' && folders().trusted(file);
}

bool in_installed_import()
{
  PyFrameObject *innermost = PyEval_GetFrame(); /* Borrowed. */
  if (extension_initializing > 0) {
    /* An extension's native initialization (pybind11 setting its classes' methods), whoever loaded it: importlib,
     * `module_from_spec` with `exec_module`, a gate. Never what agent code running inside it does. */
    PyCodeObject *code = innermost ? PyFrame_GetCode(innermost) : nullptr;
    const bool agent = code != nullptr && agent_code(code);
    Py_XDECREF(code);
    if (!agent) {
      return true;
    }
  }
  if (innermost == nullptr) {
    return false;
  }
  bool importing = false;
  PyFrameObject *frame = (PyFrameObject *)Py_NewRef(innermost);
  for (int hops = 0; frame != nullptr && hops < 256; hops++) {
    PyCodeObject *code = PyFrame_GetCode(frame);
    const bool agent = agent_code(code);
    importing = !agent && ((PyObject *)code == import_codes[0] || (PyObject *)code == import_codes[1] ||
                           installed_module_body(code));
    Py_DECREF(code);
    if (agent || importing) {
      break;
    }
    PyFrameObject *back = PyFrame_GetBack(frame); /* New reference or null. */
    Py_DECREF(frame);
    frame = back;
  }
  Py_XDECREF(frame);
  PyErr_Clear();
  return importing;
}

thread_local bool region_seeded = false;
/* What seeded the region ("json.dumps"), named in the refusal of a later fresh import. */
static thread_local std::string seed_cause;

void note_seed(PyObject *owner, PyObject *key)
{
  const char *owner_text = owner != nullptr && PyUnicode_Check(owner) ? PyUnicode_AsUTF8(owner) : nullptr;
  const char *key_text = key != nullptr && PyUnicode_Check(key) ? PyUnicode_AsUTF8(key) : nullptr;
  PyErr_Clear();
  seed_cause = std::string(owner_text ? owner_text : "a module-level dict") + (owner_text ? "." : ": ") +
               (key_text ? key_text : "?");
  region_seeded = true;
}

void seed_reset()
{
  region_seeded = false;
  seed_cause.clear();
}

int refuse_seeded_import()
{
  const std::string what = "a module imported after the script put its own code into installed modules (" +
                           seed_cause + "): import what it needs first, then change things";
  return refuse(GUARD, what.c_str());
}

/** Whether the running Python frame's module is the one whose namespace is `dict`: a module changing its own
 * namespace with its own code (`typing.__getattr__` caching `typing.Match`, a lazy attribute) is its own business,
 * whoever asked for the attribute. */
static bool writer_owns(PyObject *dict)
{
  PyFrameObject *innermost = PyEval_GetFrame(); /* Borrowed. */
  PyObject *globals = innermost ? PyFrame_GetGlobals(innermost) : nullptr;
  const bool owns = globals == dict && globals != nullptr;
  Py_XDECREF(globals);
  return owns;
}

/* Angelo's own add-on package: its code changing its own modules while a script runs is its tool state. */
static const char *const ANGELO_PACKAGE = "angelo_agent";

static bool in_angelo_package(PyObject *module_name)
{
  if (module_name == nullptr || !PyUnicode_Check(module_name)) {
    return false;
  }
  const char *text = PyUnicode_AsUTF8(module_name);
  PyErr_Clear();
  const size_t size = strlen(ANGELO_PACKAGE);
  return text != nullptr && strncmp(text, ANGELO_PACKAGE, size) == 0 && (text[size] == '\0' || text[size] == '.');
}

bool angelo_writes(PyObject *target_module_name)
{
  if (!in_angelo_package(target_module_name)) {
    return false;
  }
  PyFrameObject *innermost = PyEval_GetFrame(); /* Borrowed. */
  PyCodeObject *code = innermost ? PyFrame_GetCode(innermost) : nullptr;
  const bool agent = code == nullptr || agent_code(code);
  Py_XDECREF(code);
  if (agent || agent_on_stack()) {
    return false; /* The script's write, or Angelo code it called (with what it supplied): goes back. */
  }
  PyObject *globals = PyFrame_GetGlobals(innermost);
  const bool own = globals != nullptr && in_angelo_package(PyDict_GetItemString(globals, "__name__"));
  Py_XDECREF(globals);
  PyErr_Clear();
  return own;
}

bool writer_in_module(PyObject *module_name)
{
  PyFrameObject *innermost = PyEval_GetFrame(); /* Borrowed. */
  PyObject *globals = innermost ? PyFrame_GetGlobals(innermost) : nullptr;
  PyObject *name = globals ? PyDict_GetItemString(globals, "__name__") : nullptr; /* Borrowed. */
  const bool in = name != nullptr && module_name != nullptr && PyUnicode_Check(name) &&
                  PyUnicode_Check(module_name) && PyUnicode_Compare(name, module_name) == 0;
  Py_XDECREF(globals);
  PyErr_Clear();
  return in;
}

/** One of the interpreter's own hooks in `sys` (`excepthook`, `displayhook`, `unraisablehook`, `breakpointhook`:
 * each has its original as `sys.__name__`): the interpreter calls it, an import's top level doesn't. */
static bool interpreter_hook(PyObject *dict, PyObject *key)
{
  PyObject *owner = PyDict_GetItemString(dict, "__name__"); /* Borrowed. */
  if (owner == nullptr || !PyUnicode_Check(owner) || PyUnicode_CompareWithASCIIString(owner, "sys") != 0 ||
      key == nullptr || !PyUnicode_Check(key))
  {
    PyErr_Clear();
    return false;
  }
  PyObject *original = PyUnicode_FromFormat("__%U__", key);
  const bool hook = original != nullptr && PyDict_Contains(dict, original) == 1;
  Py_XDECREF(original);
  PyErr_Clear();
  return hook;
}

/** Whether a spec or loader holds code of its own that is not installed: a callable as an instance attribute other
 * than installed code (Angelo's `sitecustomize` gives shipped sources' loaders their own `get_code`, and wraps the
 * `ssl` loader's `exec_module`) or an installed class (`BuiltinImporter`, `FrozenImporter` are loaders that are
 * classes). A builtin bound to the script's data (a prepared dict's `get` returning a planted module) is not. */
static bool holds_code(PyObject *object)
{
  PyObject *names = PyObject_GenericGetDict(object, nullptr);
  if (names == nullptr) {
    PyErr_Clear();
    return false; /* No instance namespace: nothing of the script's own on it. */
  }
  bool code = false;
  PyObject *key, *value;
  Py_ssize_t position = 0;
  while (!code && PyDict_Next(names, &position, &key, &value)) {
    code = PyCallable_Check(value) && (PyType_Check(value) ? script_type((PyTypeObject *)value) :
                                                             !installed_attribute(value));
  }
  Py_DECREF(names);
  return code;
}

bool installed_kind(PyObject *object)
{
  if (object == Py_None) {
    return true;
  }
  if (PyType_Check(object)) {
    return !script_type((PyTypeObject *)object);
  }
  return !script_type(Py_TYPE(object)) && !made_by_script(object) && !holds_code(object);
}

/** Whether the import system loading at `frame` (`_load_unlocked`) runs only installed parts: its spec and loader
 * are installed kinds holding no code of their own. A finder the script put on `sys.meta_path` (or in
 * `sys.path_importer_cache`) may return a loader whose `create_module` fills the new module's namespace before its
 * top level runs; that import is then the script's, not the module's own definition. */
static bool installed_spec(PyFrameObject *frame, PyObject **spec_out = nullptr)
{
  PyObject *spec = PyFrame_GetVarString(frame, "spec"); /* New reference, or null with an error set. */
  if (spec == nullptr) {
    PyErr_Clear();
    return false;
  }
  PyObject *loader = PyObject_GetAttrString(spec, "loader");
  PyErr_Clear();
  const bool installed = !script_type(Py_TYPE(spec)) && !holds_code(spec) &&
                         (loader == nullptr || loader == Py_None ||
                          (PyType_Check(loader) ? !script_type((PyTypeObject *)loader) :
                                                  !script_type(Py_TYPE(loader)) && !holds_code(loader)));
  Py_XDECREF(loader);
  if (spec_out != nullptr && installed) {
    *spec_out = spec; /* What the import system loads, as it loads it (origins.cc). */
  }
  else {
    Py_DECREF(spec);
  }
  PyErr_Clear();
  return installed;
}

static bool import_system_adding(PyObject **spec)
{
  if (region_seeded) {
    return false; /* After the script put code into installed modules, nothing an import loads is kept. */
  }
  PyFrameObject *frame = (PyFrameObject *)Py_XNewRef(PyEval_GetFrame());
  bool adding = false;
  for (int hops = 0; frame != nullptr && hops < 256; hops++) {
    PyCodeObject *code = PyFrame_GetCode(frame);
    const bool agent = agent_code(code);
    const bool loading = !agent && (PyObject *)code == import_codes[0];
    Py_DECREF(code);
    if (agent || loading) {
      adding = loading && installed_spec(frame, spec);
      break;
    }
    PyFrameObject *back = PyFrame_GetBack(frame);
    Py_DECREF(frame);
    frame = back;
  }
  Py_XDECREF(frame);
  PyErr_Clear();
  return adding;
}

bool import_system_adding()
{
  return import_system_adding(nullptr);
}

PyObject *installed_import_globals()
{
  if (region_seeded) {
    return nullptr; /* The script changed installed code in this region: no import of it defines anything. */
  }
  PyFrameObject *frame = (PyFrameObject *)Py_XNewRef(PyEval_GetFrame());
  PyObject *globals = nullptr;
  bool fresh = false;
  for (int hops = 0; frame != nullptr && hops < 256; hops++) {
    PyCodeObject *code = PyFrame_GetCode(frame);
    const bool agent = agent_code(code);
    const bool body = !agent && globals == nullptr && installed_module_body(code);
    const bool loading = !agent && (PyObject *)code == import_codes[0]; /* `_load_unlocked`: a fresh import. */
    const bool other = !agent && (PyObject *)code == import_codes[1];   /* `_exec`: a reload. */
    Py_DECREF(code);
    if (body) {
      globals = PyFrame_GetGlobals(frame); /* New reference. */
      Py_DECREF(globals);                  /* The module keeps its namespace alive. */
    }
    if (agent || other || (loading && globals != nullptr)) {
      fresh = loading && installed_spec(frame);
      break;
    }
    if (loading) {
      break; /* The import system's own bookkeeping, before or after a body. */
    }
    PyFrameObject *back = PyFrame_GetBack(frame);
    Py_DECREF(frame);
    frame = back;
  }
  Py_XDECREF(frame);
  PyErr_Clear();
  /* Only a module the import system itself is loading for the first time: never a body a script ran with
   * `runpy`, `exec_module`, a fresh copy under a name it chose, or a reload (each lets it seed the namespace). */
  return fresh ? globals : nullptr;
}

/** Whether a change is an installed import's own definition: made while the import system loads an installed module
 * for the first time (its top level, or what that top level calls: a package patching its own C++ classes from a
 * sibling module, OpenTimelineIO's `_core_utils`), or by an extension module's native initialization filling its own
 * namespace. A script can't steer that: a body it ran itself (`runpy`, `exec_module`, a fresh copy under a name it
 * chose, a reload) never counts, and once it has put a callable into installed code no module is loaded afresh
 * (`region_seeded`). Any other change is journaled and put back like the script's own, whatever the value. */
/** The top-level package of a module name (`pxr` of `pxr.UsdUtils`), as a new reference; null for none. */
static PyObject *top_package(PyObject *name)
{
  if (name == nullptr || !PyUnicode_Check(name)) {
    return nullptr;
  }
  const Py_ssize_t dot = PyUnicode_FindChar(name, '.', 0, PyUnicode_GET_LENGTH(name), 1);
  PyObject *top = dot >= 0 ? PyUnicode_Substring(name, 0, dot) : Py_NewRef(name);
  PyErr_Clear();
  return top;
}

bool package_of_import(PyObject *globals, PyObject *module_name)
{
  PyObject *own = top_package(globals ? PyDict_GetItemString(globals, "__name__") : nullptr);
  PyObject *other = top_package(module_name);
  const bool same = own != nullptr && other != nullptr && PyUnicode_Compare(own, other) == 0;
  Py_XDECREF(own);
  Py_XDECREF(other);
  PyErr_Clear();
  return same;
}

/** Whether module namespace `dict` belongs to the package of the module whose import `globals` is defining: a
 * package defines itself (a sibling patching its classes: OpenTimelineIO, USD's `Tf.PrepareModule`), never another
 * package's modules (whatever steered its top level, such a change goes back). */
static bool same_package(PyObject *dict, PyObject *globals)
{
  return package_of_import(globals, PyDict_GetItemString(dict, "__name__"));
}

bool plain_installed(PyObject *value)
{
  if (PyFunction_Check(value)) {
    return installed_function(value); /* An installed function: its code is the module's, its data its own. */
  }
  if (PyCFunction_Check(value)) {
    PyObject *self = PyCFunction_GET_SELF(value); /* A module's or a class's builtin, not one bound to data. */
    return self == nullptr || PyModule_Check(self) || PyType_Check(self);
  }
  return PyWeakref_CheckRef(value); /* Calling a weak reference only returns what it refers to. */
}

/** Whether placing `value` lets the script choose what a later import calls: a callable that is not plain installed
 * code (a function of the script's, a partial, a builtin bound to data, a mock). */
bool seeds(PyObject *value)
{
  return value != nullptr && PyCallable_Check(value) && !PyType_Check(value) && !PyModule_Check(value) &&
         !plain_installed(value);
}

/** Whether the running frame is the script's own code. */
static bool innermost_agent()
{
  PyFrameObject *innermost = PyEval_GetFrame(); /* Borrowed. */
  PyCodeObject *code = innermost ? PyFrame_GetCode(innermost) : nullptr;
  const bool agent = code != nullptr && agent_code(code);
  Py_XDECREF(code);
  return agent;
}

static bool own_definition(PyObject *dict)
{
  if (dict == defining_namespace && defining_namespace != nullptr) {
    PyFrameObject *innermost = PyEval_GetFrame(); /* Borrowed. */
    PyCodeObject *code = innermost ? PyFrame_GetCode(innermost) : nullptr;
    const bool agent = code != nullptr && agent_code(code);
    Py_XDECREF(code);
    return !agent;
  }
  PyObject *globals = dict != nullptr ? installed_import_globals() : nullptr;
  return globals != nullptr &&
         (dict == globals || !namespaces().contains(dict) || same_package(dict, globals) || writer_owns(dict));
}


bool journal_watches(PyObject *dict)
{
  return namespaces().contains(dict);
}

/** Watch a module's namespace and the dicts it holds at module level (registries: `grant_guard.ENTRIES`). */
static void watch_module(PyObject *module)
{
  PyObject *names = PyModule_GetDict(module); /* Borrowed. */
  if (names == nullptr || namespaces().contains(names) || agent_module(module)) {
    return;
  }
  PyDict_Watch(watcher, names);
  namespaces().insert(names);
  PyObject *key, *value;
  Py_ssize_t position = 0;
  while (PyDict_Next(names, &position, &key, &value)) {
    if (PyDict_CheckExact(value) && !namespaces().contains(value)) {
      PyDict_Watch(watcher, value);
    }
  }
  PyErr_Clear();
}

/* Modules put into `sys.modules` by anything but the import system loading installed parts (#import_system_adding):
 * a module the script made and registered itself, or one a loader of the script's made. Held until the regions have
 * ended, so no address is reused meanwhile. */
static std::vector<PyObject *> assembled_list;
static PointerSet assembled_set;
static void note_assembled(PyObject *value)
{
  if (value == nullptr || !PyModule_Check(value) || assembled_set.contains(value)) {
    return;
  }
  PyObject *spec = nullptr;
  if (!import_system_adding(&spec)) {
    Py_XDECREF(spec);
    assembled_list.push_back(Py_NewRef(value));
    assembled_set.insert(value);
    return;
  }
  origins_note(value, spec); /* What it loaded, judged against the name at the region's end (origins.cc). */
  Py_XDECREF(spec);
}

bool module_assembled(PyObject *module)
{
  return !assembled_list.empty() && assembled_set.contains(module);
}

void assembled_release()
{
  if (depth != 0 || pending) {
    return;
  }
  std::vector<PyObject *> held;
  held.swap(assembled_list);
  assembled_set.clear();
  for (PyObject *module : held) {
    Py_DECREF(module);
  }
  origins_release();
}


/** A module a confined script's import puts in `sys.modules` is watched from then on: its namespace is filled
 * after it is added, and a rebinding the script makes in it afterwards is journaled like any other. */
static void watch_new_module(PyObject *dict, PyObject *key, PyObject *value)
{
  if (dict != PyImport_GetModuleDict() || value == nullptr || !PyModule_Check(value) || !PyUnicode_Check(key)) {
    return;
  }
  note_assembled(value);
  watch_module(value);
}

/** A module-level dict a watched namespace gains while its module's body runs (`multiprocessing.util`'s
 * `_finalizer_registry`, defined after the module entered `sys.modules`): watched from then on, as `watch_module`
 * watches the ones a namespace held already, so what a script puts in it is journaled too. */
static void watch_new_registry(PyObject *dict, PyObject *new_value)
{
  if (new_value != nullptr && PyDict_CheckExact(new_value) && namespaces().contains(dict) &&
      !namespaces().contains(new_value))
  {
    PyDict_Watch(watcher, new_value);
    PyErr_Clear();
  }
}

static void journal_event(PyDict_WatchEvent event, PyObject *dict, PyObject *key, PyObject *new_value)
{
  switch (event) {
    case PyDict_EVENT_ADDED:
      record(dict, key, nullptr, own_definition(dict));
      watch_new_module(dict, key, new_value);
      watch_new_registry(dict, new_value);
      break;
    case PyDict_EVENT_MODIFIED:
      watch_new_module(dict, key, new_value);
      watch_new_registry(dict, new_value);
      [[fallthrough]];
    case PyDict_EVENT_DELETED: {
      PyObject *old = PyDict_GetItemWithError(dict, key); /* Borrowed; the dict is not changed yet. */
      if (old == nullptr) {
        PyErr_Clear();
      }
      record(dict, key, old, own_definition(dict));
      break;
    }
    case PyDict_EVENT_CLEARED:
    case PyDict_EVENT_CLONED: {
      PyObject *copy = PyDict_Copy(dict);
      if (copy == nullptr) {
        PyErr_Clear();
        break;
      }
      record(dict, nullptr, copy);
      Py_DECREF(copy);
      break;
    }
    default:
      break;
  }
}

/** `sys.modules` as watched (the interpreter's own dict). */
static PyObject *watched_modules = nullptr;

PyObject *journal_modules()
{
  return watched_modules;
}

/* Code the script left that runs outside a region (a generator resumed later): its changes to installed modules'
 * namespaces are journaled from `outside_mark` and undone, by a region end's own rule, the next time the main thread
 * runs Python (`Py_AddPendingCall`) or when a region next starts, whichever is first. */
static thread_local size_t outside_mark = SIZE_MAX; /* The journal is this thread's. */
static bool outside_queued = false;
bool outside_settling = false;

void journal_settle_outside()
{
  if (outside_mark == SIZE_MAX || outside_settling || Py_IsFinalizing()) {
    return;
  }
  const size_t mark = outside_mark;
  outside_mark = SIZE_MAX;
  outside_settling = true;
  journal_undo(mark, nullptr);
  outside_settling = false;
}

static int settle_outside_pending(void * /*unused*/)
{
  outside_queued = false;
  /* The main thread's (another thread's are settled when a region next starts on it). */
  if (depth == 0 && !pending) {
    journal_settle_outside();
  }
  return 0;
}

static void journal_event(PyDict_WatchEvent event, PyObject *dict, PyObject *key, PyObject *new_value);

static int on_dict_event(PyDict_WatchEvent event, PyObject *dict, PyObject *key, PyObject *new_value)
{
  steering_modules_changed(dict, key); /* Before anything returns: the packages a region snapshots. */
  trace_hooks_changed(dict, key, new_value); /* Angelo's own tracer and profiler for new threads. */
  if (dict == watched_modules && key != nullptr && new_value != nullptr &&
      (event == PyDict_EVENT_ADDED || event == PyDict_EVENT_MODIFIED))
  {
    PyObject *raised = PyErr_GetRaisedException();
    opaque_module_added(key, new_value); /* A module of Angelo's package: opaque to scripts (opaque.cc). */
    PyErr_SetRaisedException(raised);
  }
  if (!journaling() && journaling_late()) {
    PyObject *raised = PyErr_GetRaisedException();
    journal_event(event, dict, key, new_value);
    PyErr_Clear();
    PyErr_SetRaisedException(raised);
    return 0;
  }
  if (!journaling()) {
    /* Never `PyImport_GetModuleDict()` here: it aborts once finalization has dropped `sys.modules`, and module
     * namespaces send events while they are cleared. */
    if (quiet || outside_settling || depth != 0 || pending || Py_IsFinalizing()) {
      return 0;
    }
    if (dict == watched_modules) {
      if (key != nullptr && confined_now()) {
        note_assembled(new_value);
        modules_changed_outside(key); /* Code the script left, resumed outside a region (a generator). */
      }
    }
    else if (namespaces().contains(dict) && confined_now()) {
      PyObject *raised = PyErr_GetRaisedException();
      if (outside_mark == SIZE_MAX) {
        outside_mark = journal.size();
      }
      journal_event(event, dict, key, new_value);
      if (!outside_queued) {
        outside_queued = Py_AddPendingCall(settle_outside_pending, nullptr) == 0;
      }
      PyErr_Clear();
      PyErr_SetRaisedException(raised);
    }
    return 0;
  }
  PyObject *raised = PyErr_GetRaisedException(); /* Whatever the changing code holds stays as it was. */
  journal_event(event, dict, key, new_value);
  /* A callable put into installed code (a module's namespace, the builtins) outside an import's own definition,
   * by the script or by installed code it called (`unittest.mock`), that is not plain installed code: what an
   * import would call from now on is the script's choosing, so nothing an import does for the rest of the region
   * is kept (no definition; its module is dropped at the end). Into a module-level registry (`encodings._cache`)
   * only the script's own placement, or its code, seeds: libraries keep installed callables there as they work.
   * Data (`mimetypes.init`) and installed functions (`logging.captureWarnings`) seed nothing. */
  if (!region_seeded && seeds(new_value) && dict != journal_modules() && !writer_owns(dict) &&
      !interpreter_hook(dict, key) && !own_definition(dict) &&
      (namespaces().contains(dict) || innermost_agent() || agent_callable(new_value)))
  {
    note_seed(PyDict_GetItemString(dict, "__name__"), key);
  }
  PyErr_Clear();
  PyErr_SetRaisedException(raised);
  return 0;
}

static bool immutable_constant(PyObject *value)
{
  return value == Py_None || PyUnicode_Check(value) || PyBytes_Check(value) || PyLong_Check(value) ||
         PyFloat_Check(value) || PyBool_Check(value) || PyTuple_Check(value) || PyFrozenSet_Check(value);
}

/** `str.isupper()`, computed here: a name a `str` subclass holds can't run its own `isupper` at a region's end. */
static bool upper_name(PyObject *key)
{
  if (key == nullptr || !PyUnicode_Check(key)) {
    return false;
  }
  bool cased = false;
  const int kind = PyUnicode_KIND(key);
  const void *data = PyUnicode_DATA(key);
  for (Py_ssize_t i = 0; i < PyUnicode_GET_LENGTH(key); i++) {
    const Py_UCS4 character = PyUnicode_READ(kind, data, i);
    if (Py_UNICODE_ISLOWER(character) || Py_UNICODE_ISTITLE(character)) {
      return false;
    }
    cased = cased || Py_UNICODE_ISUPPER(character);
  }
  return cased;
}

/** A function, class, module or other callable: what a rebinding replaces. */
static bool code_like(PyObject *value)
{
  return value != nullptr && (PyType_Check(value) || PyModule_Check(value) || PyCallable_Check(value));
}

/** Installed Python code (Angelo's, the standard library's): may stay where a script put it. */
static bool installed_callable(PyObject *value)
{
  /* The function a bound, static or class method holds (C fields: no code of anyone's runs here). */
  PyObject *function = nullptr;
  if (PyMethod_Check(value)) {
    function = Py_NewRef(PyMethod_GET_FUNCTION(value));
  }
  else if (Py_TYPE(value) == &PyStaticMethod_Type || Py_TYPE(value) == &PyClassMethod_Type) {
    function = PyObject_GetAttrString(value, "__func__");
    PyErr_Clear();
  }
  if (function == nullptr) {
    function = Py_NewRef(value);
  }
  bool installed = false;
  if (PyFunction_Check(function)) {
    PyObject *code = PyFunction_GetCode(function); /* Borrowed. */
    if (code != nullptr && !agent_code((PyCodeObject *)code)) {
      const char *file = PyUnicode_AsUTF8(((PyCodeObject *)code)->co_filename);
      installed = file != nullptr && folders().trusted(file);
      PyErr_Clear();
    }
  }
  Py_DECREF(function);
  return installed;
}

/** Whether to put `key` back to `old` in `dict` (the script's change), given what it holds now. */
void note_finding(PyObject *findings, PyObject *owner_name, PyObject *key, int *noted)
{
  if (findings == nullptr || *noted >= 20) {
    return;
  }
  PyObject *text = owner_name ? PyUnicode_FromFormat("%S: %S", owner_name, key) :
                                PyUnicode_FromFormat("a module-level dict: %S", key);
  if (text != nullptr) {
    PyList_Append(findings, text);
    Py_DECREF(text);
    (*noted)++;
  }
  PyErr_Clear();
}

/** "module: name" for a finding (the dict's own `__name__`). */
void note(PyObject *findings, PyObject *dict, PyObject *key, int *noted)
{
  PyObject *owner = PyDict_GetItemString(dict, "__name__"); /* Borrowed; null for a registry or sys.modules. */
  note_finding(findings, (owner && PyUnicode_Check(owner)) ? owner : nullptr, key, noted);
}

/** `sys`'s import-steering names: what the import system reads to find code (`path`, `meta_path`, `path_hooks`,
 * `path_importer_cache`), where it writes and reads bytecode (`pycache_prefix`), and whether (`dont_write_bytecode`).
 * Rebound by a script, each goes back exactly, whatever it holds (the sweep puts their contents back). */
static bool import_steering(PyObject *dict, PyObject *key)
{
  static PyObject *sys_names = nullptr;
  if (sys_names == nullptr) {
    PyObject *sys = PyImport_ImportModule("sys");
    sys_names = sys ? Py_NewRef(PyModule_GetDict(sys)) : nullptr;
    Py_XDECREF(sys);
    PyErr_Clear();
  }
  if (dict != sys_names || key == nullptr || !PyUnicode_Check(key)) {
    return false;
  }
  for (const char *name :
       {"path", "meta_path", "path_hooks", "path_importer_cache", "pycache_prefix", "dont_write_bytecode"})
  {
    if (PyUnicode_CompareWithASCIIString(key, name) == 0) {
      return true;
    }
  }
  return false;
}

/** What the import system itself adds to a module's namespace as it loads modules: `__builtins__` (the
 * interpreter's own builtins, put in by `exec` before a body runs) and a package's attribute for its submodule (the
 * module `sys.modules` holds under that name; dropped with it at the region's end: modules.cc). */
static bool import_bookkeeping(PyObject *dict, PyObject *key, PyObject *now)
{
  if (dict == nullptr || key == nullptr || now == nullptr || !PyUnicode_Check(key)) {
    return false;
  }
  if (PyUnicode_CompareWithASCIIString(key, "__builtins__") == 0) {
    static PyObject *builtins_name = PyUnicode_InternFromString("builtins");
    PyObject *builtins = builtins_name ? PyImport_GetModule(builtins_name) : nullptr;
    const bool own = builtins != nullptr && (now == builtins || now == PyModule_GetDict(builtins));
    Py_XDECREF(builtins);
    PyErr_Clear();
    return own;
  }
  if (!PyModule_Check(now)) {
    return false;
  }
  PyObject *owner = PyDict_GetItemString(dict, "__name__"); /* Borrowed. */
  PyObject *full = owner != nullptr && PyUnicode_Check(owner) ? PyUnicode_FromFormat("%U.%U", owner, key) : nullptr;
  PyObject *modules = journal_modules(); /* Never aborts during finalization. */
  PyObject *held = full && modules ? PyDict_GetItemWithError(modules, full) : nullptr;
  Py_XDECREF(full);
  PyErr_Clear();
  return held == now;
}

bool undo_wanted(PyObject *key, PyObject *old, PyObject *now, bool registry, bool exact, PyObject *dict)
{
  if (old != nullptr && environ_view(old)) {
    return now != old; /* `os.environ` replaced: the view of the process's environment comes back. */
  }
  if (exact && !registry && old == nullptr && import_bookkeeping(dict, key, now)) {
    return false;
  }
  if (exact && !registry) {
    /* A module's namespace is as the region found it, data included: what a script left there (an opener, a
     * default context, a flag) never outlives it. (A module's own import, and Angelo's own code changing its own
     * package, are definitions: kept.) */
    return now != old;
  }
  if (old != nullptr && now == nullptr) {
    /* A name removed comes back, whatever it held; from a module-level dict of data only what held code (Angelo's
     * own code removes its finished entries while a script runs). */
    return !registry || code_like(old);
  }
  if (old != nullptr && (code_like(old) || (upper_name(key) && immutable_constant(old)))) {
    return now != old;
  }
  /* New or replaced data stays, unless what is there now is a callable that is not installed code. */
  return now != nullptr && now != old && PyCallable_Check(now) && !PyType_Check(now) && !PyModule_Check(now) &&
         !installed_callable(now);
}

size_t journal_mark()
{
  return journal.size();
}

void journal_undo(size_t mark, PyObject *findings)
{
  if (mark <= outside_mark && outside_mark != SIZE_MAX && !outside_settling) {
    outside_mark = SIZE_MAX; /* Undone with this region's own. */
  }
  if (journal.size() <= mark) {
    return;
  }
  PyObject *raised = PyErr_GetRaisedException(); /* A trampoline's call may have raised: keep it. */
  int changed = 0, noted = 0;
  replace_planted(mark, findings, &changed, &noted);
  PyObject *modules_before = PyDict_New(); /* name -> what it held when the region began. */
  PyErr_Clear();
  while (journal.size() > mark) {
    JournalEntry entry = journal.back();
    journal.pop_back();
    if (entry.key == nullptr) {
      /* Cleared or cloned: every code-like entry the copy held goes back. */
      PyObject *name, *value;
      Py_ssize_t position = 0;
      const bool registry = !namespaces().contains(entry.dict) && entry.dict != PyImport_GetModuleDict();
      while (PyDict_Next(entry.old, &position, &name, &value)) {
        PyObject *now = PyDict_GetItemWithError(entry.dict, name);
        PyErr_Clear();
        if (undo_wanted(name, value, now, registry, true, entry.dict)) {
          PyDict_SetItem(entry.dict, name, value);
          note(findings, entry.dict, name, &noted);
          changed++;
        }
      }
    }
    else if (entry.type_entry) {
      changed += undo_type_entry(entry, findings, &noted) ? 1 : 0;
    }
    else if (entry.dict == PyImport_GetModuleDict()) {
      /* Settled per name below, from what it held when the region began (the earliest entry, popped last). */
      if (modules_before != nullptr) {
        PyDict_SetItem(modules_before, entry.key, entry.old ? entry.old : modules_absent);
      }
    }
    else if (import_steering(entry.dict, entry.key)) {
      PyObject *now = PyDict_GetItemWithError(entry.dict, entry.key);
      PyErr_Clear();
      if (now != entry.old) {
        if (entry.old != nullptr) {
          PyDict_SetItem(entry.dict, entry.key, entry.old);
        }
        else {
          PyDict_DelItem(entry.dict, entry.key);
        }
        note(findings, entry.dict, entry.key, &noted);
        changed++;
      }
    }
    else if (!entry.definition || agent_callable(PyDict_GetItemWithError(entry.dict, entry.key)) ||
             (entry.tool && made_by_script(PyDict_GetItemWithError(entry.dict, entry.key))))
    {
      /* A definition stays, unless what it put there is the script's code (an import that loaded a script's file). */
      PyErr_Clear();
      PyObject *now = PyDict_GetItemWithError(entry.dict, entry.key);
      PyErr_Clear();
      const bool registry = !namespaces().contains(entry.dict) && entry.dict != PyImport_GetModuleDict();
      if (undo_wanted(entry.key, entry.old, now, registry, true, entry.dict)) {
        if (entry.old != nullptr) {
          PyDict_SetItem(entry.dict, entry.key, entry.old);
        }
        else {
          PyDict_DelItem(entry.dict, entry.key);
        }
        note(findings, entry.dict, entry.key, &noted);
        changed++;
      }
    }
    PyErr_Clear();
    Py_DECREF(entry.dict);
    Py_XDECREF(entry.key);
    Py_XDECREF(entry.old);
  }
  if (modules_before != nullptr) {
    modules_settle(modules_before, findings, &changed, &noted);
    Py_DECREF(modules_before);
  }
  if (changed > noted && findings != nullptr) {
    PyObject *text = PyUnicode_FromFormat("and %d more rebinding(s) put back", changed - noted);
    if (text != nullptr) {
      PyList_Append(findings, text);
      Py_DECREF(text);
    }
    PyErr_Clear();
  }
  PyErr_SetRaisedException(raised);
}

/** Watch every loaded module's namespace and its module-level dicts, `sys.modules` itself and `sys.monitoring`.
 * Cheap: one pass over sys.modules; a namespace already watched is skipped. */
void journal_watch_loaded()
{
  if (watcher < 0) {
    return;
  }
  PyObject *modules = PyImport_GetModuleDict();
  PyDict_Watch(watcher, modules);
  watched_modules = modules;
  PyObject *name, *module;
  Py_ssize_t position = 0;
  while (PyDict_Next(modules, &position, &name, &module)) {
    if (PyModule_Check(module)) {
      watch_module(module);
    }
  }
  if (PyObject *monitoring = PySys_GetObject("monitoring")) { /* Borrowed. */
    if (PyModule_Check(monitoring)) {
      watch_module(monitoring);
    }
  }
  PyErr_Clear();
  trace_hooks_know(); /* Before any script runs: the values `threading` holds now are Angelo's. */
}

/** `watch_modules(names)`: start the journal (Angelo's own code, at install). Every module is watched, whatever
 * `names` lists (kept for the callers); refused while confined. */
PyObject *guard_watch_modules(PyObject * /*self*/, PyObject * /*names*/)
{
  if (confined_now()) {
    refuse(GUARD, "watch_modules");
    return nullptr;
  }
  if (watcher < 0) {
    watcher = PyDict_AddWatcher(on_dict_event);
    if (watcher < 0) {
      return nullptr;
    }
  }
  journal_watch_loaded();
  Py_RETURN_NONE;
}

}  // namespace blender::angelo_guard
