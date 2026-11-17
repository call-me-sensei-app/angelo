/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * What a confined region made and left alive, made safe when it ends (doc/angelo/script_confinement.rst, "Work that
 * outlives the script"; bpy_angelo_guard_confine_region.cc runs it): a class the region made gets confining
 * descriptors for what is not installed code, a weak reference's callback and a callable the region made get the
 * trampoline wherever they are held, and a finalizer it made never runs at exit.
 */

#include <Python.h>

#include <iterator>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <unordered_map>
#include <utility>
#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_record.hh"

namespace blender::angelo_guard {

/* -------------------------------------------------------------------- */
/** \name Making leftovers safe
 * \{ */

bool installed_function(PyObject *value)
{
  if (!PyFunction_Check(value)) {
    return false;
  }
  PyCodeObject *code = (PyCodeObject *)PyFunction_GetCode(value);
  if (agent_code(code)) {
    return false;
  }
  const char *file = PyUnicode_AsUTF8(code->co_filename);
  /* An installed file's code, or code under a generic name (`<string>`) that is not the agent's: compiled or run
   * under such a name while confined it is tainted, unless an installed import made it as it loaded (`@dataclass`'s
   * generated methods, journal.cc), and a script can't rename code to one (provenance.cc). */
  const bool installed = file != nullptr && (file[0] == '<' || folders().trusted(file));
  PyErr_Clear();
  return installed;
}

/** The callable kinds whose effect is chosen by what they hold rather than by code of their own: a partial (and a
 * partial method) and the operator module's callers. Looked up once. */
static bool holder_callable(PyObject *value)
{
  static PyObject *kinds = nullptr;
  if (kinds == nullptr) {
    kinds = PyList_New(0);
    const std::pair<const char *, const char *> names[] = {{"functools", "partial"},
                                                           {"functools", "partialmethod"},
                                                           {"operator", "methodcaller"},
                                                           {"operator", "attrgetter"},
                                                           {"operator", "itemgetter"}};
    for (const auto &[module_name, name] : names) {
      PyObject *module = PyImport_ImportModule(module_name);
      PyObject *kind = module ? PyObject_GetAttrString(module, name) : nullptr;
      if (kind != nullptr && PyType_Check(kind)) {
        PyList_Append(kinds, kind);
      }
      Py_XDECREF(kind);
      Py_XDECREF(module);
    }
    PyErr_Clear();
  }
  for (Py_ssize_t i = 0; kinds != nullptr && i < PyList_GET_SIZE(kinds); i++) {
    if (PyObject_TypeCheck(value, (PyTypeObject *)PyList_GET_ITEM(kinds, i))) {
      return true;
    }
  }
  return false;
}

/** A callable whose effect the script chose: made in the region, and either the script's code (an agent function, or
 * a method, `lru_cache`, partial or wrapper around one) or made by the script's own code from installed parts (a
 * method of an object the script made, a partial or operator caller it made). What installed code made on the
 * script's behalf from installed parts (`importlib.metadata` caching an `lru_cache` of a bound method on the objects
 * it keeps) is installed code's, and so are an installed function, a class, a module, a weak reference, a trampoline
 * and an object of an installed class (a `typing` alias a module the script imported cached): those keep their
 * identity. */
static bool loose_callable(PyObject *value, int level = 0)
{
  /* The cheap tests first: a script's containers hold mostly plain data (a million numbers). A slot still empty (a
   * tuple or list some C code is filling, an empty cell) holds nothing. */
  if (value == nullptr || !PyCallable_Check(value) || PyType_Check(value) || PyModule_Check(value) || PyWeakref_Check(value) ||
      level > 4 || confining(value) || !made_in_region(value))
  {
    return false;
  }
  if (PyFunction_Check(value)) {
    return !installed_function(value);
  }
  if (PyMethod_Check(value)) {
    PyObject *self = PyMethod_GET_SELF(value);
    return agent_callable(PyMethod_GET_FUNCTION(value)) || made_by_script(self) || made_by_script(value);
  }
  if (PyCFunction_Check(value)) {
    PyObject *self = PyCFunction_GET_SELF(value);
    return self != nullptr && !PyModule_Check(self) && !PyType_Check(self) && made_by_script(self);
  }
  if (holder_callable(value)) {
    return made_by_script(value) || agent_callable(value);
  }
  if (PyObject *function = lru_function(value)) {
    return made_by_script(value) || loose_callable(function, level + 1) || agent_callable(function);
  }
  /* What it wraps, read without running anyone's code (this runs at a region's end, where nothing is journaled):
   * a static or class method's function (a C field), or `__wrapped__` from the object's own dict. */
  const bool wrapper = Py_TYPE(value) == &PyStaticMethod_Type || Py_TYPE(value) == &PyClassMethod_Type;
  PyObject *inner = nullptr;
  if (wrapper) {
    inner = PyObject_GetAttrString(value, "__func__");
  }
  else if (PyObject *own = PyObject_GenericGetDict(value, nullptr)) {
    inner = Py_XNewRef(PyDict_GetItemString(own, "__wrapped__"));
    Py_DECREF(own);
  }
  PyErr_Clear();
  const bool loose = inner != nullptr && (loose_callable(inner, level + 1) ||
                                          (PyFunction_Check(inner) && !installed_function(inner)));
  Py_XDECREF(inner);
  return loose;
}

/** A weak reference's callback runs when the collector frees its object: confined unless it is installed code (the
 * standard library's own cleanup closures). */
static PyObject *pick_callback(PyObject *value)
{
  if (value == nullptr || confining(value) || installed_function(value) ||
      (PyCFunction_Check(value) && (PyCFunction_GET_SELF(value) == nullptr ||
                                    PyModule_Check(PyCFunction_GET_SELF(value)))))
  {
    return nullptr;
  }
  return defer(value);
}

/** The trampoline for a loose callable, or a rebuilt tuple of trampolines for a tuple holding one (new refs; null:
 * nothing to replace). */
static PyObject *confined_replacement(PyObject *value, int level = 0)
{
  /* `gc.get_objects()` also returns a tuple or list C code has made and not yet filled: its empty slots are null. */
  if (value == nullptr || (!PyCallable_Check(value) && !PyTuple_CheckExact(value))) {
    return nullptr; /* Plain data: nothing to confine. */
  }
  if (loose_callable(value)) {
    return defer(value);
  }
  if (PyTuple_CheckExact(value) && level < 4) { /* A tuple may come from a free list: judged by what it holds. */
    const Py_ssize_t size = PyTuple_GET_SIZE(value);
    PyObject *rebuilt = nullptr;
    for (Py_ssize_t i = 0; i < size; i++) {
      PyObject *item = PyTuple_GET_ITEM(value, i);
      PyObject *replacement = confined_replacement(item, level + 1);
      if (replacement != nullptr && rebuilt == nullptr) {
        rebuilt = PyTuple_New(size); /* A new tuple: never the original (a whole-tuple slice is the same object). */
        for (Py_ssize_t j = 0; rebuilt != nullptr && j < size; j++) {
          PyTuple_SET_ITEM(rebuilt, j, Py_XNewRef(PyTuple_GET_ITEM(value, j)));
        }
      }
      if (replacement != nullptr && rebuilt != nullptr) {
        PyObject *old = PyTuple_GET_ITEM(rebuilt, i);
        PyTuple_SET_ITEM(rebuilt, i, replacement);
        Py_XDECREF(old);
      }
      else {
        Py_XDECREF(replacement);
      }
    }
    return rebuilt;
  }
  return nullptr;
}

/** Replace, in one holder, every value `pick` gives a replacement for. Returns how many it replaced, or -1 when the
 * holder is of a kind that cannot be changed in place. */
static int confine_in(PyObject *holder, PyObject *(*pick)(PyObject *))
{
  int replaced = 0;
  auto swap = [&](PyObject *value, auto &&store) {
    if (PyObject *replacement = pick(value)) {
      store(replacement);
      Py_DECREF(replacement);
      replaced++;
    }
  };
  if (PyDict_Check(holder)) {
    std::vector<std::pair<PyObject *, PyObject *>> changes;
    PyObject *key, *value;
    Py_ssize_t position = 0;
    while (PyDict_Next(holder, &position, &key, &value)) {
      if (PyObject *replacement = pick(value)) {
        changes.emplace_back(Py_NewRef(key), replacement);
      }
    }
    for (auto &[key_ref, replacement] : changes) {
      PyDict_SetItem(holder, key_ref, replacement);
      Py_DECREF(key_ref);
      Py_DECREF(replacement);
      replaced++;
    }
  }
  else if (PyList_Check(holder)) {
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(holder); i++) {
      swap(PyList_GET_ITEM(holder, i), [&](PyObject *replacement) {
        PyList_SetItem(holder, i, Py_NewRef(replacement));
      });
    }
  }
  else if (PyCell_Check(holder)) {
    if (PyObject *content = PyCell_GET(holder)) {
      swap(content, [&](PyObject *replacement) { PyCell_Set(holder, replacement); });
    }
  }
  else if (PyAnySet_Check(holder) && !PyFrozenSet_Check(holder)) {
    std::vector<PyObject *> items;
    PyObject *iterator = PySet_Type.tp_iter(holder); /* The set's own items: never a subclass's `__iter__`. */
    while (PyObject *item = iterator ? PyIter_Next(iterator) : nullptr) {
      items.push_back(item);
    }
    Py_XDECREF(iterator);
    for (PyObject *item : items) {
      swap(item, [&](PyObject *replacement) {
        PySet_Discard(holder, item);
        PySet_Add(holder, replacement);
      });
      Py_DECREF(item);
    }
  }
  else if (PyWeakref_Check(holder)) {
    PyWeakReference *reference = (PyWeakReference *)holder;
    if (reference->wr_callback != nullptr && made_in_region(holder)) {
      PyObject *replacement = pick_callback(reference->wr_callback);
      if (replacement != nullptr) {
        PyObject *old = reference->wr_callback;
        reference->wr_callback = replacement;
        Py_DECREF(old);
        replaced++;
      }
    }
  }
  else if (!PyType_Check(holder) && !PyTuple_Check(holder) && !PyFrozenSet_Check(holder) &&
           !PyModule_Check(holder) && !PyFrame_Check(holder) && !PyCode_Check(holder))
  {
    bool reached = false;
    /* An instance's attributes (its own dict, managed or not) and its slots. */
    if (PyObject *names = PyObject_GenericGetDict(holder, nullptr)) {
      reached = true;
      const int count = confine_in(names, pick);
      replaced += count > 0 ? count : 0;
      Py_DECREF(names);
    }
    PyErr_Clear();
    for (PyTypeObject *type = Py_TYPE(holder); type != nullptr; type = type->tp_base) {
      if (!(type->tp_flags & Py_TPFLAGS_HEAPTYPE) || type->tp_members == nullptr) {
        continue;
      }
      for (PyMemberDef *member = type->tp_members; member->name != nullptr; member++) {
        if ((member->type != Py_T_OBJECT_EX && member->type != _Py_T_OBJECT) ||
            (member->flags & (Py_READONLY | Py_RELATIVE_OFFSET)))
        {
          continue;
        }
        reached = true;
        PyObject **slot = (PyObject **)((char *)holder + member->offset);
        if (*slot != nullptr) {
          swap(*slot, [&](PyObject *replacement) {
            PyObject *old = *slot;
            *slot = Py_NewRef(replacement);
            Py_DECREF(old);
          });
        }
      }
    }
    return reached ? replaced : -1;
  }
  else {
    return PyTuple_Check(holder) || PyFrozenSet_Check(holder) ? -1 : replaced;
  }
  PyErr_Clear();
  return replaced;
}


PyObject *gc_call(const char *function, PyObject *argument)
{
  PyObject *module = PyImport_ImportModule("gc");
  if (module == nullptr) {
    return nullptr;
  }
  const bool was_busy = busy;
  busy = true; /* Our own calls: the hook refuses these to scripts only. */
  PyObject *result = argument ? PyObject_CallMethod(module, function, "O", argument) :
                                PyObject_CallMethod(module, function, nullptr);
  busy = was_busy;
  Py_DECREF(module);
  return result;
}

static void note(PyObject *findings, const char *text, Py_ssize_t count)
{
  if (findings == nullptr || count == 0) {
    return;
  }
  if (PyObject *line = PyUnicode_FromFormat(text, count)) {
    PyList_Append(findings, line);
    Py_DECREF(line);
  }
  PyErr_Clear();
}

static PyObject *pick_loose(PyObject *value)
{
  return confined_replacement(value);
}

/* `weakref.finalize` and its record type, found once. */
static PyObject *finalize = nullptr, *info_type = nullptr;

static void finalize_find()
{
  if (finalize == nullptr) {
    PyObject *module = PyImport_ImportModule("weakref");
    finalize = module ? PyObject_GetAttrString(module, "finalize") : nullptr;
    info_type = finalize ? PyObject_GetAttrString(finalize, "_Info") : nullptr;
    Py_XDECREF(module);
    PyErr_Clear();
  }
}

/** A `weakref.finalize` record the region made, however it was made (`finalize(...)`, or a hand-built `_Info` put in
 * `finalize._registry`): its function runs confined when its object is collected and never at interpreter exit,
 * where nothing could confine it. Judged on the record (`_Info`, large enough for the allocation record) that both
 * the collector's callback and `_exitfunc` call, not on the finalizer object (`__slots__ = ()`, too small). */
static void no_run_at_exit(PyObject *object)
{
  finalize_find();
  if (finalize != nullptr && PyType_Check(finalize) && PyObject_TypeCheck(object, (PyTypeObject *)finalize)) {
    PyObject_SetAttrString(object, "atexit", Py_False);
    PyErr_Clear();
    return;
  }
  if (info_type == nullptr || !PyType_Check(info_type) || !PyObject_TypeCheck(object, (PyTypeObject *)info_type)) {
    return;
  }
  PyObject_SetAttrString(object, "atexit", Py_False);
  PyErr_Clear();
  PyObject *function = PyObject_GetAttrString(object, "func");
  if (function != nullptr && PyCallable_Check(function) && !confining(function)) {
    if (PyObject *deferred = defer(function)) {
      PyObject_SetAttrString(object, "func", deferred);
      Py_DECREF(deferred);
    }
  }
  Py_XDECREF(function);
  PyErr_Clear();
}

/** `finalize._registry` itself, read from the class's own dict (no descriptor of anyone's runs). */
static PyObject *finalize_registry()
{
  finalize_find();
  if (finalize == nullptr || !PyType_Check(finalize)) {
    return nullptr;
  }
  PyObject *registry = PyDict_GetItemString(((PyTypeObject *)finalize)->tp_dict, "_registry"); /* Borrowed. */
  PyErr_Clear();
  return registry != nullptr && PyDict_Check(registry) ? registry : nullptr;
}

/* What `finalize._registry` held when this thread's outermost region began, and each genuine record's fields. */
static thread_local PyObject *finalize_before = nullptr;
static thread_local PyObject *finalize_fields = nullptr; /* key -> tuple of the record's fields */
static const char *const RECORD_FIELDS[] = {"weakref", "func", "args", "kwargs", "atexit", "index"};

/** A genuine record's fields as a tuple (slots of exactly `_Info`: no code runs reading them), or null. */
static PyObject *record_fields(PyObject *info)
{
  if (info_type == nullptr || Py_TYPE(info) != (PyTypeObject *)info_type) {
    return nullptr;
  }
  PyObject *fields = PyTuple_New(std::size(RECORD_FIELDS));
  for (size_t i = 0; fields != nullptr && i < std::size(RECORD_FIELDS); i++) {
    PyObject *value = PyObject_GetAttrString(info, RECORD_FIELDS[i]);
    PyErr_Clear();
    PyTuple_SET_ITEM(fields, i, value ? value : Py_NewRef(Py_None));
  }
  return fields;
}

void finalize_snapshot()
{
  if (Py_IsFinalizing()) {
    return; /* The interpreter is going: nothing to keep as it was. */
  }
  Py_CLEAR(finalize_before);
  Py_CLEAR(finalize_fields);
  if (PyObject *registry = finalize_registry()) {
    finalize_before = PyDict_Copy(registry);
    finalize_fields = PyDict_New();
    PyObject *key, *info;
    Py_ssize_t position = 0;
    while (finalize_fields != nullptr && PyDict_Next(registry, &position, &key, &info)) {
      if (PyObject *fields = record_fields(info)) {
        PyDict_SetItem(finalize_fields, key, fields);
        Py_DECREF(fields);
      }
    }
  }
  PyErr_Clear();
}

/** A record the region found, changed in place since (its function, arguments or `atexit`: an installed cleanup's
 * record made to run the script's call at exit): every field goes back. Whether anything was put back. */
static bool record_put_back(PyObject *key, PyObject *info)
{
  PyObject *before = finalize_fields ? PyDict_GetItemWithError(finalize_fields, key) : nullptr; /* Borrowed. */
  PyErr_Clear();
  if (before == nullptr || !PyTuple_Check(before) || Py_TYPE(info) != (PyTypeObject *)info_type) {
    return false;
  }
  bool changed = false;
  for (size_t i = 0; i < std::size(RECORD_FIELDS); i++) {
    PyObject *now = PyObject_GetAttrString(info, RECORD_FIELDS[i]);
    PyErr_Clear();
    if (now != PyTuple_GET_ITEM(before, i)) {
      PyObject_SetAttrString(info, RECORD_FIELDS[i], PyTuple_GET_ITEM(before, i));
      PyErr_Clear();
      changed = true;
    }
    Py_XDECREF(now);
  }
  return changed;
}

/** Every record the region put in `finalize._registry`, whatever made it: `_exitfunc` runs any record with a true
 * `atexit` at interpreter exit (and `__call__` any `func`), whatever its type. A genuine record (exactly `_Info`,
 * under exactly a `finalize`) runs confined when its object goes and never at exit; anything else (another type, a
 * subclass whose `atexit` is a property, a key of a subclass with its own `__call__`) is removed. */
void finalize_settle(PyObject *findings)
{
  if (Py_IsFinalizing()) {
    return; /* The interpreter is going: nothing to keep as it was. */
  }
  PyObject *registry = finalize_registry();
  PyObject *items = registry != nullptr ? PyDict_Items(registry) : nullptr;
  Py_ssize_t removed = 0;
  for (Py_ssize_t i = 0; items != nullptr && i < PyList_GET_SIZE(items); i++) {
    PyObject *key = PyTuple_GET_ITEM(PyList_GET_ITEM(items, i), 0);
    PyObject *info = PyTuple_GET_ITEM(PyList_GET_ITEM(items, i), 1);
    PyObject *had = finalize_before ? PyDict_GetItemWithError(finalize_before, key) : nullptr;
    PyErr_Clear();
    if (had == info) {
      removed += record_put_back(key, info) ? 1 : 0;
      continue;
    }
    const bool genuine = Py_TYPE(key) == (PyTypeObject *)finalize && info_type != nullptr &&
                         Py_TYPE(info) == (PyTypeObject *)info_type;
    if (genuine) {
      no_run_at_exit(info);
    }
    else if (PyDict_DelItem(registry, key) == 0) {
      removed++;
    }
    PyErr_Clear();
  }
  Py_XDECREF(items);
  Py_CLEAR(finalize_before);
  Py_CLEAR(finalize_fields);
  if (removed > 0 && findings != nullptr) {
    if (PyObject *line = PyUnicode_FromFormat("weakref.finalize records (%zd removed or put back)", removed)) {
      PyList_Append(findings, line);
      Py_DECREF(line);
    }
  }
  PyErr_Clear();
}

/** Namespaces that are not plain holders: a class's own dict (its attributes are confined as descriptors) and a
 * module's (the journal puts back what a script rebinds there; what a module defines for itself stays). */
static void namespaces_of(PyObject *objects, PointerSet &skip)
{
  for (Py_ssize_t i = 0; i < PyList_GET_SIZE(objects); i++) {
    PyObject *object = PyList_GET_ITEM(objects, i);
    if (PyType_Check(object) && ((PyTypeObject *)object)->tp_dict != nullptr) {
      skip.insert(((PyTypeObject *)object)->tp_dict);
    }
  }
  PyObject *name, *module;
  Py_ssize_t position = 0;
  while (PyDict_Next(PyImport_GetModuleDict(), &position, &name, &module)) {
    if (PyModule_Check(module) && journal_watches(PyModule_GetDict(module))) {
      skip.insert(PyModule_GetDict(module)); /* The agent's own modules are plain holders. */
    }
  }
}

/** What the outermost region on this thread made and left alive, made safe (see the file comment). */
void confine_leftovers(PyObject *findings, bool last, PyObject *script_namespace, PyObject *objects)
{
  Py_ssize_t classes = 0, inside = 0;
  PointerSet skip;
  namespaces_of(objects, skip);
  PyObject *loose = PyList_New(0);
  for (Py_ssize_t i = 0; i < PyList_GET_SIZE(objects); i++) {
    PyObject *object = PyList_GET_ITEM(objects, i);
    if (object == loose || !made_in_region(object)) {
      continue;
    }
    if (PyType_Check(object)) {
      confine_class((PyTypeObject *)object);
      classes++;
      continue;
    }
    if (skip.contains(object)) {
      continue;
    }
    resume_note(object); /* A generator or coroutine it left: its later steps run as regions (_resume.cc). */
    no_run_at_exit(object);
    const int count = confine_in(object, pick_loose);
    inside += count > 0 ? count : 0;
    PyObject *replacement = loose != nullptr ? confined_replacement(object) : nullptr;
    if (replacement != nullptr) {
      PyList_Append(loose, object);
    }
    Py_XDECREF(replacement);
  }
  /* Only what something else still holds needs the pass over older objects: a callable held by nothing but the
   * script's own namespace, our lists and the trampolines that now wrap it can't be reached by Angelo. */
  if (loose != nullptr) {
    std::unordered_map<PyObject *, Py_ssize_t> known;
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(objects); i++) {
      PyObject *object = PyList_GET_ITEM(objects, i);
      PyObject *inner = deferred_function(object);
      inner = inner ? inner : confined_attr_original(object);
      if (inner != nullptr) {
        known[inner]++;
      }
    }
    if (script_namespace != nullptr && PyDict_Check(script_namespace)) {
      PyObject *key, *value;
      Py_ssize_t position = 0;
      while (PyDict_Next(script_namespace, &position, &key, &value)) {
        known[value]++;
      }
    }
    PyObject *held = PyList_New(0);
    for (Py_ssize_t i = 0; held != nullptr && i < PyList_GET_SIZE(loose); i++) {
      PyObject *object = PyList_GET_ITEM(loose, i);
      const Py_ssize_t ours = 2 + known[object]; /* `objects` and `loose` hold one reference each. */
      if (Py_REFCNT(object) > ours) {
        PyList_Append(held, object);
      }
    }
    if (held != nullptr) {
      Py_SETREF(loose, held);
    }
    PyErr_Clear();
  }
  /* The older objects that hold what the region made: one pass, once nothing is frozen (the last region). */
  Py_ssize_t outside = 0, unreachable = 0;
  if (last && loose != nullptr && PyList_GET_SIZE(loose) > 0) {
    if (regions_frozen) {
      Py_XDECREF(gc_call("unfreeze")); /* Referrers are found among every object, the older ones too. */
      regions_frozen = false;
    }
    PointerSet everywhere; /* Every class's and module's namespace, now that nothing is frozen. */
    if (PyObject *all = gc_call("get_objects")) {
      namespaces_of(all, everywhere);
      Py_DECREF(all);
    }
    /* Two rounds: a tuple holding a callable (a free-list tuple is never recorded) is rebuilt where it is held. */
    PyObject *round = Py_NewRef(loose);
    for (int pass = 0; pass < 2 && round != nullptr && PyList_GET_SIZE(round) > 0; pass++) {
      PyObject *targets = PyList_AsTuple(round);
      PyObject *holders = nullptr;
      PyObject *module = PyImport_ImportModule("gc");
      PyObject *referrers = module ? PyObject_GetAttrString(module, "get_referrers") : nullptr;
      if (targets != nullptr && referrers != nullptr) {
        const bool was_busy = busy;
        busy = true;
        holders = PyObject_CallObject(referrers, targets);
        busy = was_busy;
      }
      Py_XDECREF(referrers);
      Py_XDECREF(module);
      PyObject *next = PyList_New(0);
      for (Py_ssize_t i = 0; holders != nullptr && i < PyList_GET_SIZE(holders); i++) {
        PyObject *holder = PyList_GET_ITEM(holders, i);
        if (holder == objects || holder == loose || holder == round || holder == targets || holder == next ||
            made_in_region(holder) || PyFrame_Check(holder) || confining(holder) || everywhere.contains(holder))
        {
          continue;
        }
        if (PyTuple_CheckExact(holder) && pass == 0 && next != nullptr) {
          PyList_Append(next, holder); /* Rebuilt in its own holders, next round. */
          continue;
        }
        const int count = confine_in(holder, pick_loose);
        if (count < 0) {
          unreachable++;
        }
        outside += count > 0 ? count : 0;
      }
      Py_XDECREF(holders);
      Py_XDECREF(targets);
      Py_DECREF(round);
      round = next;
    }
    Py_XDECREF(round);
  }
  Py_XDECREF(loose);
  PyErr_Clear();
  note(findings, "classes the script made, confined: %zd", classes);
  note(findings, "callables the script left, confined: %zd", inside + outside);
  note(findings, "callables the script left where they cannot be confined: %zd", unreachable);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Regions
 * \{ */

}  // namespace blender::angelo_guard
