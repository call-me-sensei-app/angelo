/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The file a module name resolves to, decided without running any Python (doc/angelo/script_confinement.rst,
 * "Work that outlives the script"). A `sys.modules` name a region gained stays only if what the import system loaded
 * for it is what the name resolves to through the installed finders; an installed file loaded under a name the
 * script chose (`_bootstrap._load` of `pickle`'s file as `plistlib`) is dropped at the region's end.
 *
 * - As the import system adds a module (`_load_unlocked`, judged installed: _journal.cc), the spec it loaded is
 *   recorded: its origin, whether its loader reads a file, and a namespace package's locations.
 * - At the region's end the name is resolved on the file system alone, as the path-based finder does it: the
 *   directories searched are `sys.path` as the region found it (then installed folders added since) or the
 *   package's `__path__`, and in each the package directory's `__init__` before a module file, by extension, source
 *   and bytecode suffix. Built-in and frozen modules are the interpreter's own lists. No finder, hook or
 *   descriptor runs: what runs at a region's end is not journaled, so the script's code must not run there.
 */

#include <Python.h>

#ifdef _WIN32
#  include <windows.h>
#endif

#include <cstring>
#include <string>
#include <unordered_map>
#include <vector>

#include "BLI_fileops.hh"
#include "BLI_path_utils.hh"
#include "BLI_string.hh"

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_paths.hh"

namespace blender::angelo_guard {

namespace {

/** How the module's body was loaded, judged by the loader that reads it (a wrapper's held loader, not the wrapper),
 * never by the origin the spec names: a spec's origin is any text its maker gave. */
enum class LoadKind {
  Unknown,   /* Any other loader: its name can't be resolved without running it, so it is dropped. */
  File,      /* A path-based file loader: `file` is the file it reads, `loader_name` the one name it loads. */
  BuiltIn,   /* The interpreter's built-in importer. */
  Frozen,    /* The interpreter's frozen importer. */
  Namespace, /* No body: a namespace package and its places. */
  Finder,    /* A finder the region found in `sys.meta_path` loaded it itself (`six.moves`). */
};

struct LoadRecord {
  LoadKind kind = LoadKind::Unknown;
  std::string file;                /* For File: the loader's own `path`. */
  std::string loader_name;         /* For File: the loader's own `name` (the only name its `get_code` loads). */
  std::vector<std::string> places; /* For Namespace: its locations. */
  bool finder_held = false;        /* For Finder: its maps held what they held when the region began, both when the
                                    * module was noted and at the region's end (`finder_ok`). */
};

/* Captured at install, before any script: what the path-based finder searches and in which order. */
PyObject *file_loader_types = nullptr; /* (FileLoader, ExtensionFileLoader) */
PyObject *lazy_loader_type = nullptr;  /* importlib.util.LazyLoader: a wrapper whose loader is its `loader` */
PyObject *namespace_loader_type = nullptr;
PyObject *builtin_importer = nullptr;
PyObject *frozen_importer = nullptr;
std::vector<std::string> suffixes;     /* Extension, then source, then bytecode suffixes. */
PyObject *builtin_names = nullptr;     /* frozenset of `sys.builtin_module_names` */
PyObject *frozen_names = nullptr;      /* frozenset of `_imp._frozen_module_names()` */

/* Modules the import system added in the running regions (held), and what it loaded for each. */
std::unordered_map<PyObject *, LoadRecord> loaded;
thread_local PyObject *path_at_start = nullptr; /* sys.path as this thread's outermost region found it */
thread_local PyObject *meta_at_start = nullptr; /* sys.meta_path as it found it */
/* Each finder's own state as the region found it (its instance dict, with copies of the dicts and lists in it), or
 * None for a class (classes are journaled): a name map changed in place (`six`'s `known_modules`) loads nothing a
 * region keeps. */
thread_local PyObject *finders_at_start = nullptr;
/* The modules a finder of this thread's region loaded, with that finder's place: judged at this region's end on
 * this thread, whose snapshot it is (a module another thread noted is judged by that thread's region). */
thread_local std::vector<std::pair<PyObject *, Py_ssize_t>> finder_seen;

bool text_of(PyObject *value, std::string &out)
{
  if (value == nullptr || !PyUnicode_Check(value)) {
    return false;
  }
  const char *text = PyUnicode_AsUTF8(value);
  if (text == nullptr) {
    PyErr_Clear();
    return false;
  }
  out = text;
  return true;
}

void append_strings(PyObject *list, std::vector<std::string> &out)
{
  for (Py_ssize_t i = 0; list != nullptr && PyList_CheckExact(list) && i < PyList_GET_SIZE(list); i++) {
    std::string text;
    if (text_of(PyList_GET_ITEM(list, i), text)) {
      out.push_back(text);
    }
  }
}

std::string absolute(const std::string &entry)
{
  if (!entry.empty() && BLI_path_is_abs_from_cwd(entry.c_str())) {
    return entry;
  }
  char cwd[FILE_MAX];
  if (BLI_current_working_dir(cwd, sizeof(cwd)) == nullptr) {
    return entry;
  }
  if (entry.empty() || entry == ".") {
    return cwd;
  }
  char joined[FILE_MAX];
  BLI_path_join(joined, sizeof(joined), cwd, entry.c_str());
  return joined;
}

std::string join(const std::string &a, const std::string &b)
{
  char joined[FILE_MAX];
  BLI_path_join(joined, sizeof(joined), a.c_str(), b.c_str());
  return joined;
}

#ifdef _WIN32
/** A file's volume serial and file ID (Windows' `stat` has no inode: a path spelled another way, a short 8.3 name,
 * another case or separator, is still the same file). False when it can't be opened. */
bool win_file_id(const std::string &path, FILE_ID_INFO &id)
{
  const int size = MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, nullptr, 0);
  std::wstring wide(size > 0 ? size : 1, L'\0');
  if (size <= 0 || MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, wide.data(), size) <= 0) {
    return false;
  }
  HANDLE handle = CreateFileW(wide.c_str(), 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                              OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
  if (handle == INVALID_HANDLE_VALUE) {
    return false;
  }
  bool known = GetFileInformationByHandleEx(handle, FileIdInfo, &id, sizeof(id)) != 0;
  if (!known) {
    BY_HANDLE_FILE_INFORMATION info;
    known = GetFileInformationByHandle(handle, &info) != 0;
    if (known) {
      memset(&id, 0, sizeof(id));
      id.VolumeSerialNumber = info.dwVolumeSerialNumber;
      const uint64_t index = (uint64_t(info.nFileIndexHigh) << 32) | info.nFileIndexLow;
      memcpy(id.FileId.Identifier, &index, sizeof(index));
    }
  }
  CloseHandle(handle);
  return known;
}
#endif

/** Whether two paths are the same file (device and inode where the system has them; on Windows the volume serial
 * and file ID; else the normalized path). */
bool same_file(const std::string &a, const std::string &b)
{
#ifdef _WIN32
  FILE_ID_INFO ia, ib;
  if (win_file_id(a, ia) && win_file_id(b, ib)) {
    return ia.VolumeSerialNumber == ib.VolumeSerialNumber &&
           memcmp(ia.FileId.Identifier, ib.FileId.Identifier, sizeof(ia.FileId.Identifier)) == 0;
  }
#endif
  BLI_stat_t sa, sb;
  if (BLI_stat(a.c_str(), &sa) != 0 || BLI_stat(b.c_str(), &sb) != 0) {
    return false;
  }
  if (sa.st_ino != 0 || sb.st_ino != 0) {
    return sa.st_dev == sb.st_dev && sa.st_ino == sb.st_ino;
  }
  char na[FILE_MAX], nb[FILE_MAX];
  STRNCPY(na, a.c_str());
  STRNCPY(nb, b.c_str());
  BLI_path_normalize(na);
  BLI_path_normalize(nb);
  return BLI_path_cmp(na, nb) == 0;
}

/** The directories a name is searched in: the package's `__path__` (a list, or a namespace package's own list of
 * places, read from its instance dict without running it), or `sys.path` as the region found it followed by any
 * installed folder added since. */
bool search_path(PyObject *name, Py_ssize_t dot, std::vector<std::string> &entries)
{
  if (dot > 0) {
    PyObject *parent_name = PyUnicode_Substring(name, 0, dot);
    PyObject *modules = journal_modules(); /* Never aborts during finalization. */
    PyObject *parent = parent_name && modules ? PyDict_GetItemWithError(modules, parent_name) : nullptr;
    Py_XDECREF(parent_name);
    PyObject *names = parent != nullptr && PyModule_CheckExact(parent) ? PyModule_GetDict(parent) : nullptr;
    PyObject *path = names ? PyDict_GetItemString(names, "__path__") : nullptr;
    PyErr_Clear();
    if (path != nullptr && !PyList_CheckExact(path)) {
      /* importlib's `_NamespacePath`: its places are its own `_path` list. */
      PyObject *own = PyObject_GenericGetDict(path, nullptr);
      PyObject *places = own ? PyDict_GetItemString(own, "_path") : nullptr;
      path = places != nullptr && PyList_CheckExact(places) ? places : nullptr;
      append_strings(path, entries);
      Py_XDECREF(own);
      PyErr_Clear();
      return path != nullptr;
    }
    append_strings(path, entries);
    return path != nullptr;
  }
  append_strings(path_at_start, entries);
  std::vector<std::string> now;
  append_strings(PySys_GetObject("path"), now);
  PyErr_Clear();
  for (const std::string &entry : now) {
    bool known = false;
    for (const std::string &old : entries) {
      known = known || old == entry;
    }
    if (!known && folders().trusted(absolute(entry))) {
      entries.push_back(entry);
    }
  }
  return true;
}

}  // namespace

void origins_install()
{
  if (file_loader_types != nullptr) {
    return;
  }
  PyObject *external = PyImport_ImportModule("_frozen_importlib_external");
  PyObject *file_loader = external ? PyObject_GetAttrString(external, "FileLoader") : nullptr;
  PyObject *extension_loader = external ? PyObject_GetAttrString(external, "ExtensionFileLoader") : nullptr;
  if (file_loader != nullptr && extension_loader != nullptr) {
    file_loader_types = PyTuple_Pack(2, file_loader, extension_loader);
  }
  namespace_loader_type = external ? PyObject_GetAttrString(external, "NamespaceLoader") : nullptr;
  PyObject *bootstrap = PyImport_ImportModule("_frozen_importlib");
  builtin_importer = bootstrap ? PyObject_GetAttrString(bootstrap, "BuiltinImporter") : nullptr;
  frozen_importer = bootstrap ? PyObject_GetAttrString(bootstrap, "FrozenImporter") : nullptr;
  Py_XDECREF(bootstrap);
  PyObject *util = PyImport_ImportModule("importlib.util");
  lazy_loader_type = util ? PyObject_GetAttrString(util, "LazyLoader") : nullptr;
  Py_XDECREF(util);
  for (const char *name : {"EXTENSION_SUFFIXES", "SOURCE_SUFFIXES", "BYTECODE_SUFFIXES"}) {
    PyObject *list = external ? PyObject_GetAttrString(external, name) : nullptr;
    append_strings(list, suffixes);
    Py_XDECREF(list);
  }
  PyObject *builtins = PySys_GetObject("builtin_module_names"); /* Borrowed. */
  builtin_names = builtins ? PyFrozenSet_New(builtins) : nullptr;
  PyObject *imp = PyImport_ImportModule("_imp");
  PyObject *frozen = imp ? PyObject_CallMethod(imp, "_frozen_module_names", nullptr) : nullptr;
  frozen_names = frozen ? PyFrozenSet_New(frozen) : nullptr;
  Py_XDECREF(frozen);
  Py_XDECREF(imp);
  Py_XDECREF(file_loader);
  Py_XDECREF(extension_loader);
  Py_XDECREF(external);
  PyErr_Clear();
}

/** A finder's own state, no Python run: None for a class; else its instance dict's items, a dict or list value copied
 * (its entries are compared by identity later). New reference. */
static PyObject *finder_state(PyObject *finder)
{
  PyObject *own = !PyType_Check(finder) && Py_TYPE(finder)->tp_dictoffset != 0 ?
                      PyObject_GenericGetDict(finder, nullptr) :
                      nullptr;
  if (own == nullptr || !PyDict_CheckExact(own)) {
    Py_XDECREF(own);
    PyErr_Clear();
    return Py_NewRef(Py_None);
  }
  PyObject *state = PyList_New(0);
  PyObject *key, *value;
  Py_ssize_t position = 0;
  while (state != nullptr && PyDict_Next(own, &position, &key, &value)) {
    PyObject *held = PyDict_Check(value) ? PyDict_Copy(value) :
                     PyList_CheckExact(value) ? PyList_GetSlice(value, 0, PY_SSIZE_T_MAX) :
                                                Py_NewRef(value);
    PyObject *pair = held != nullptr ? PyTuple_Pack(3, key, value, held) : nullptr;
    if (pair != nullptr) {
      PyList_Append(state, pair);
    }
    Py_XDECREF(pair);
    Py_XDECREF(held);
  }
  Py_DECREF(own);
  PyErr_Clear();
  return state != nullptr ? state : Py_NewRef(Py_None);
}

/** Whether two dicts hold the same entries in the same places (keys and values by identity: nothing runs). */
static bool same_entries(PyObject *now, PyObject *then)
{
  if (PyDict_GET_SIZE(now) != PyDict_GET_SIZE(then)) {
    return false;
  }
  PyObject *k1, *v1, *k2, *v2;
  Py_ssize_t p1 = 0, p2 = 0;
  while (PyDict_Next(now, &p1, &k1, &v1)) {
    if (!PyDict_Next(then, &p2, &k2, &v2) || k1 != k2 || v1 != v2) {
      return false;
    }
  }
  return true;
}

/** Whether the finder at `index` of the region's `sys.meta_path` holds what it held when the region began. */
static bool finder_unchanged(Py_ssize_t index)
{
  if (finders_at_start == nullptr || index < 0 || index >= PyList_GET_SIZE(finders_at_start)) {
    return false;
  }
  PyObject *then = PyList_GET_ITEM(finders_at_start, index);
  PyObject *now = finder_state(PyList_GET_ITEM(meta_at_start, index));
  bool same = then == Py_None ? now == Py_None :
                                now != Py_None && PyList_GET_SIZE(now) == PyList_GET_SIZE(then);
  for (Py_ssize_t i = 0; same && then != Py_None && i < PyList_GET_SIZE(then); i++) {
    PyObject *a = PyList_GET_ITEM(now, i), *b = PyList_GET_ITEM(then, i);
    PyObject *held_now = PyTuple_GET_ITEM(a, 2), *held_then = PyTuple_GET_ITEM(b, 2);
    same = PyTuple_GET_ITEM(a, 0) == PyTuple_GET_ITEM(b, 0) && PyTuple_GET_ITEM(a, 1) == PyTuple_GET_ITEM(b, 1);
    if (same && PyDict_CheckExact(held_then)) {   /* A copy (of a dict or a dict subclass's entries). */
      same = PyDict_CheckExact(held_now) && same_entries(held_now, held_then);
    }
    else if (same && PyList_CheckExact(held_then)) {
      same = PyList_CheckExact(held_now) && PyList_GET_SIZE(held_now) == PyList_GET_SIZE(held_then);
      for (Py_ssize_t j = 0; same && j < PyList_GET_SIZE(held_then); j++) {
        same = PyList_GET_ITEM(held_now, j) == PyList_GET_ITEM(held_then, j);
      }
    }
  }
  Py_DECREF(now);
  return same;
}

void import_snapshot()
{
  if (Py_IsFinalizing()) {
    return; /* The interpreter is going: nothing to keep as it was. */
  }
  Py_CLEAR(path_at_start);
  PyObject *path = PySys_GetObject("path"); /* Borrowed. */
  path_at_start = path != nullptr && PyList_Check(path) ? PyList_GetSlice(path, 0, PY_SSIZE_T_MAX) : nullptr;
  Py_CLEAR(meta_at_start);
  PyObject *meta = PySys_GetObject("meta_path"); /* Borrowed. */
  meta_at_start = meta != nullptr && PyList_Check(meta) ? PyList_GetSlice(meta, 0, PY_SSIZE_T_MAX) : nullptr;
  Py_CLEAR(finders_at_start);
  finders_at_start = meta_at_start != nullptr ? PyList_New(PyList_GET_SIZE(meta_at_start)) : nullptr;
  for (Py_ssize_t i = 0; finders_at_start != nullptr && i < PyList_GET_SIZE(meta_at_start); i++) {
    PyList_SET_ITEM(finders_at_start, i, finder_state(PyList_GET_ITEM(meta_at_start, i)));
  }
  PyErr_Clear();
}

/** A value an object holds in its own instance dict (no descriptor or `__getattr__` runs); new reference. */
static PyObject *own_item(PyObject *object, const char *name)
{
  PyObject *own = Py_TYPE(object)->tp_dictoffset != 0 ? PyObject_GenericGetDict(object, nullptr) : nullptr;
  PyObject *value = own != nullptr && PyDict_CheckExact(own) ? PyDict_GetItemString(own, name) : nullptr;
  Py_XINCREF(value);
  Py_XDECREF(own);
  PyErr_Clear();
  return value;
}

static bool is_kind(PyObject *object, PyObject *type)
{
  return type != nullptr && PyType_Check(type) && PyObject_TypeCheck(object, (PyTypeObject *)type);
}

/** Whether `object` is a file loader (by its type's MRO alone: no `__class__` or `__instancecheck__` runs). */
static bool is_file_loader(PyObject *object)
{
  for (Py_ssize_t i = 0; file_loader_types != nullptr && i < PyTuple_GET_SIZE(file_loader_types); i++) {
    if (is_kind(object, PyTuple_GET_ITEM(file_loader_types, i))) {
      return true;
    }
  }
  return false;
}

void origins_note(PyObject *module, PyObject *spec)
{
  if (module == nullptr || spec == nullptr || loaded.count(module) != 0) {
    return;
  }
  /* The spec is an installed kind holding no code of its own (judged before this is called): plain reads. */
  LoadRecord record;
  PyObject *loader = PyObject_GetAttrString(spec, "loader");
  PyErr_Clear();
  /* A wrapper is judged by the loader it holds: that one reads the body (a `LazyLoader` runs it later). */
  for (int depth = 0; depth < 8 && loader != nullptr && lazy_loader_type != nullptr &&
                      Py_TYPE(loader) == (PyTypeObject *)lazy_loader_type;
       depth++)
  {
    PyObject *inner = own_item(loader, "loader");
    Py_SETREF(loader, inner);
  }
  if (loader != nullptr && loader == builtin_importer) {
    record.kind = LoadKind::BuiltIn;
  }
  else if (loader != nullptr && loader == frozen_importer) {
    record.kind = LoadKind::Frozen;
  }
  else if (loader != nullptr && is_file_loader(loader)) {
    PyObject *path = own_item(loader, "path");
    PyObject *name = own_item(loader, "name");
    if (text_of(path, record.file) && text_of(name, record.loader_name)) {
      record.kind = LoadKind::File;
    }
    Py_XDECREF(path);
    Py_XDECREF(name);
  }
  else if (loader == Py_None || loader == nullptr || is_kind(loader, namespace_loader_type)) {
    PyObject *origin = PyObject_GetAttrString(spec, "origin");
    PyObject *places = origin == Py_None ? PyObject_GetAttrString(spec, "submodule_search_locations") : nullptr;
    PyObject *list = places != nullptr && places != Py_None ? PySequence_List(places) : nullptr;
    append_strings(list, record.places);
    if (list != nullptr) {
      record.kind = LoadKind::Namespace;
    }
    Py_XDECREF(list);
    Py_XDECREF(places);
    Py_XDECREF(origin);
  }
  else {
    /* A finder the region found in `sys.meta_path` that loads what it finds itself (`six`'s importer). */
    for (Py_ssize_t i = 0; meta_at_start != nullptr && i < PyList_GET_SIZE(meta_at_start); i++) {
      if (PyList_GET_ITEM(meta_at_start, i) == loader) {
        record.kind = LoadKind::Finder;
        record.finder_held = finder_unchanged(i); /* Now, as it loads: a name added, imported and removed counts. */
        finder_seen.emplace_back(Py_NewRef(module), i);
      }
    }
  }
  Py_XDECREF(loader);
  PyErr_Clear();
  loaded[Py_NewRef(module)] = std::move(record);
}

static bool finder_unchanged(Py_ssize_t index);

/** Whether `module`, loaded by a finder of this thread's region, still has that finder's maps as the region found
 * them; a module this thread did not see loaded is not judged as a finder's. */
static bool finder_ok(PyObject *module)
{
  for (const auto &[seen, index] : finder_seen) {
    if (seen == module) {
      return finder_unchanged(index);
    }
  }
  return false;
}

void origins_release()
{
  std::unordered_map<PyObject *, LoadRecord> held;
  held.swap(loaded);
  for (auto &[module, record] : held) {
    Py_DECREF(module);
  }
  Py_CLEAR(path_at_start);
  Py_CLEAR(meta_at_start);
  Py_CLEAR(finders_at_start);
  for (auto &[module, index] : finder_seen) {
    Py_DECREF(module);
  }
  finder_seen.clear();
}

bool origin_honest(PyObject *name, PyObject *module)
{
  const auto found = loaded.find(module);
  if (found == loaded.end() || !PyUnicode_Check(name)) {
    return false;
  }
  const LoadRecord &record = found->second;
  const Py_ssize_t length = PyUnicode_GET_LENGTH(name);
  const Py_ssize_t dot = PyUnicode_FindChar(name, '.', 0, length, -1);
  PyErr_Clear();
  switch (record.kind) {
    case LoadKind::BuiltIn:
      return builtin_names != nullptr && dot < 0 && PySet_Contains(builtin_names, name) == 1;
    case LoadKind::Frozen:
      return frozen_names != nullptr && PySet_Contains(frozen_names, name) == 1;
    case LoadKind::Finder:
      /* The finder the region found resolved and loaded it, from name maps that held, as it loaded and at the
       * region's end, what they held when the region began (both checked on the thread whose region it is). */
      return record.finder_held && finder_ok(module);
    case LoadKind::Unknown:
      return false; /* Not resolvable without running it: dropped, and imported again clean when needed. */
    case LoadKind::File: {
      std::string own;
      if (!text_of(name, own) || own != record.loader_name) {
        return false; /* The loader loads only its own name. */
      }
      break;
    }
    case LoadKind::Namespace:
      break;
  }
  const bool file = record.kind == LoadKind::File;
  std::string tail;
  PyObject *tail_text = PyUnicode_Substring(name, dot + 1, length);
  const bool have_tail = text_of(tail_text, tail);
  Py_XDECREF(tail_text);
  std::vector<std::string> entries;
  if (!have_tail || !search_path(name, dot, entries)) {
    return false;
  }
  std::vector<std::string> portions;
  for (const std::string &entry : entries) {
    const std::string folder = absolute(entry);
    if (!BLI_is_dir(folder.c_str())) {
      continue; /* A zip or a missing folder: the path-based finder's file loaders don't read it. */
    }
    const std::string base = join(folder, tail);
    if (BLI_is_dir(base.c_str())) {
      for (const std::string &suffix : suffixes) {
        const std::string init = join(base, "__init__" + suffix);
        if (BLI_is_file(init.c_str())) {
          return file && same_file(init, record.file);
        }
      }
      portions.push_back(base);
    }
    for (const std::string &suffix : suffixes) {
      const std::string path = join(folder, tail + suffix);
      if (BLI_is_file(path.c_str())) {
        return file && same_file(path, record.file);
      }
    }
  }
  if (file || portions.empty()) {
    return false;
  }
  /* A namespace package: each place it was given is one the name resolves to. */
  for (const std::string &place : record.places) {
    bool known = false;
    for (const std::string &portion : portions) {
      known = known || same_file(place, portion);
    }
    if (!known) {
      return false;
    }
  }
  return true;
}

}  // namespace blender::angelo_guard
