/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * `os.DirEntry`'s `is_dir`, `is_file` and `stat` (what `os.walk`, `os.fwalk`, `glob` and `pathlib` ask of each entry),
 * checked as the lookups they can be (bpy_angelo_guard_confine_wrap_lookup.cc; break-it-confine-reparse M7, head 14
 * MUST-3): an entry of a listing by name by its path, an entry of `os.scandir(fd)` inside the listed folder.
 */

#include <Python.h>

#include <cstring>
#include <iterator>
#include <string>

#ifndef _WIN32
#  include <fcntl.h>
#  include <unistd.h>
#endif

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_remote.hh"

namespace blender::angelo_guard {

namespace {

/* -------------------------------------------------------------------- */
/** \name `os.DirEntry` (what `os.walk`, `glob` and `pathlib` ask of each entry)
 * \{ */

struct EntryWrap {
  const char *name;
  PyObject *original;               /* The method descriptor. */
  PyTypeObject *installed = nullptr; /* The entry type it was replaced in (a strong reference), or null. */
};

EntryWrap entry_wraps[] = {{"is_dir", nullptr}, {"is_file", nullptr}, {"stat", nullptr}};
constexpr size_t entry_count = sizeof(entry_wraps) / sizeof(entry_wraps[0]);

#ifndef _WIN32
/* An entry of `os.scandir(fd)` has only its name as `path`; CPython looks it up in the listing's folder by the
 * descriptor the entry keeps (`fstatat(dir_fd, name)`), which Python can't read. Where the entry keeps it is found once
 * at install, by listing `/` through two descriptors of known, different numbers and finding the one int field that
 * holds each (break-it-confine-reparse head 14, MUST-3). Not found (another CPython layout): -1, and a bare-name entry
 * is refused while confined. */
Py_ssize_t entry_fd_offset = -1;

PyObject *first_entry_of(PyObject *scandir, const int fd)
{
  PyObject *iterator = PyObject_CallFunction(scandir, "i", fd);
  PyObject *entry = iterator ? PyIter_Next(iterator) : nullptr;
  if (iterator != nullptr) {
    Py_XDECREF(PyObject_CallMethod(iterator, "close", nullptr));
    Py_DECREF(iterator);
  }
  PyErr_Clear();
  return entry;
}

void find_entry_fd_offset(PyObject *scandir)
{
  const int root = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (root < 0) {
    return;
  }
  const int first = fcntl(root, F_DUPFD_CLOEXEC, 733), second = fcntl(root, F_DUPFD_CLOEXEC, 911);
  close(root);
  PyObject *a = first >= 0 ? first_entry_of(scandir, first) : nullptr;
  PyObject *b = second >= 0 ? first_entry_of(scandir, second) : nullptr;
  if (a != nullptr && b != nullptr && first != second && Py_TYPE(a) == Py_TYPE(b)) {
    const Py_ssize_t size = Py_TYPE(a)->tp_basicsize;
    Py_ssize_t found = -1;
    int matches = 0;
    const Py_ssize_t step = Py_ssize_t(sizeof(int));
    for (Py_ssize_t at = Py_ssize_t(sizeof(PyObject)); at + step <= size; at += step) {
      int in_a, in_b;
      memcpy(&in_a, reinterpret_cast<const char *>(a) + at, sizeof(int));
      memcpy(&in_b, reinterpret_cast<const char *>(b) + at, sizeof(int));
      if (in_a == first && in_b == second) {
        found = at;
        matches++;
      }
    }
    entry_fd_offset = matches == 1 ? found : -1;
  }
  Py_XDECREF(a);
  Py_XDECREF(b);
  for (const int fd : {first, second}) {
    if (fd >= 0) {
      close(fd);
    }
  }
}

/** The descriptor a bare-name entry was listed from, or -1 when it can't be read. */
int entry_dir_fd(PyObject *entry)
{
  if (entry_fd_offset < 0 || Py_TYPE(entry)->tp_basicsize < entry_fd_offset + Py_ssize_t(sizeof(int))) {
    return -1;
  }
  int fd;
  memcpy(&fd, reinterpret_cast<const char *>(entry) + entry_fd_offset, sizeof(int));
  return fd;
}
#endif

#ifdef _WIN32
bool call_true(PyObject *object, const char *method)
{
  PyObject *result = PyObject_CallMethod(object, method, nullptr);
  const bool yes = result != nullptr && PyObject_IsTrue(result) == 1;
  Py_XDECREF(result);
  PyErr_Clear();
  return yes;
}
#endif

PyObject *entry_call(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const EntryWrap &wrap = entry_wraps[PyLong_AsSsize_t(index)];
  if (confined_now() && PyTuple_GET_SIZE(args) > 0) {
    PyObject *entry = PyTuple_GET_ITEM(args, 0);
    PyObject *value = PyObject_GetAttrString(entry, "path");
    PyObject *bytes = nullptr;
    std::string path;
    if (value != nullptr && PyUnicode_FSConverter(value, &bytes)) {
      path.assign(PyBytes_AS_STRING(bytes), size_t(PyBytes_GET_SIZE(bytes)));
      Py_DECREF(bytes);
    }
    Py_XDECREF(value);
    PyErr_Clear();
    bool follow = true;
    if (kwargs != nullptr) {
      if (PyObject *flag = PyDict_GetItemString(kwargs, "follow_symlinks")) {
        follow = PyObject_IsTrue(flag) == 1;
      }
    }
    Refusal refusal = names_a_remote_file(path) ? Refusal::Network : Refusal::None;
#ifdef _WIN32
    /* Windows answers a plain entry from the listing's own data: only a link (or a junction) followed can lead
     * elsewhere. */
    const bool walk = follow && (call_true(entry, "is_symlink") ||
                                 (PyObject_HasAttrString(entry, "is_junction") && call_true(entry, "is_junction")));
#else
    /* POSIX: `stat` always asks the system, `is_dir` and `is_file` when the listing gave no type (some file systems
     * never do) or the entry is a link, and the name may have become a link since the listing: every call is checked
     * as the lookup it can be (break-it-confine-reparse M7 read, SHOULD). */
    const bool walk = true;
#endif
    if (refusal == Refusal::None && walk && !path.empty()) {
      int dir_fd = -1;
#ifndef _WIN32
      if (path.find('/') == std::string::npos) {
        /* A bare name: listed from a descriptor (`os.scandir(fd)`, `os.fwalk`, `glob`'s `dir_fd`); a listing by name
         * always gives a `path` with the folder in it. Judged inside the listed folder, never the working one. */
        dir_fd = entry_dir_fd(entry);
        if (dir_fd < 0) {
          judged_unreadable = true;
          refusal = Refusal::Private; /* The folder can't be told: refused rather than judged in the wrong place. */
        }
      }
#endif
      if (refusal == Refusal::None) {
        refusal = network_lookup(path, dir_fd, follow);
      }
    }
    if (refusal != Refusal::None) {
      if (strcmp(wrap.name, "stat") != 0) {
        Py_RETURN_FALSE; /* `is_dir`, `is_file`: as for an entry that is neither. */
      }
      refuse(refusal == Refusal::Network ? NETWORK : PRIVATE, "os.DirEntry.stat");
      return nullptr;
    }
  }
  return PyObject_Call(wrap.original, args, kwargs);
}

/** The method Python calls on an entry (a method descriptor: no bound method, no tuple): straight to the original
 * unless a confined call runs the network check, which is Strict script protection's (Ruling 17: listing a folder
 * costs as in 2.0.1 by default). */
template<int I>
PyObject *entry_method(PyObject *self, PyObject *const *args, const Py_ssize_t nargs, PyObject *kwnames)
{
  const EntryWrap &wrap = entry_wraps[I];
  const Py_ssize_t keywords = kwnames ? PyTuple_GET_SIZE(kwnames) : 0;
  if (!strict_lookups() || !confined_now()) { /* The switch first: a held copy costs nothing once it is off. */
    PyObject *stack[8];
    if (1 + nargs + keywords <= Py_ssize_t(std::size(stack))) {
      stack[0] = self;
      for (Py_ssize_t i = 0; i < nargs + keywords; i++) {
        stack[1 + i] = args[i];
      }
      return PyObject_Vectorcall(wrap.original, stack, size_t(1 + nargs), kwnames);
    }
  }
  PyObject *tuple = PyTuple_New(1 + nargs);
  if (tuple == nullptr) {
    return nullptr;
  }
  PyTuple_SET_ITEM(tuple, 0, Py_NewRef(self));
  for (Py_ssize_t i = 0; i < nargs; i++) {
    PyTuple_SET_ITEM(tuple, 1 + i, Py_NewRef(args[i]));
  }
  PyObject *dict = keywords > 0 ? PyDict_New() : nullptr;
  for (Py_ssize_t i = 0; dict != nullptr && i < keywords; i++) {
    if (PyDict_SetItem(dict, PyTuple_GET_ITEM(kwnames, i), args[nargs + i]) != 0) {
      Py_CLEAR(dict);
    }
  }
  if (keywords > 0 && dict == nullptr) {
    Py_DECREF(tuple);
    return nullptr;
  }
  PyObject *index = PyLong_FromLong(I);
  PyObject *result = index ? entry_call(index, tuple, dict) : nullptr;
  Py_XDECREF(index);
  Py_DECREF(tuple);
  Py_XDECREF(dict);
  return result;
}

PyMethodDef entry_methods[] = {
    {"is_dir", (PyCFunction)(void (*)(void))entry_method<0>, METH_FASTCALL | METH_KEYWORDS, nullptr},
    {"is_file", (PyCFunction)(void (*)(void))entry_method<1>, METH_FASTCALL | METH_KEYWORDS, nullptr},
    {"stat", (PyCFunction)(void (*)(void))entry_method<2>, METH_FASTCALL | METH_KEYWORDS, nullptr},
};
static_assert(sizeof(entry_methods) / sizeof(entry_methods[0]) == entry_count, "one method per wrapped name");

/** \} */

}  // namespace

int install_wrappers_entry()
{
#ifndef _WIN32
  if (entry_fd_offset < 0) {
    PyObject *posix = PyImport_ImportModule("posix");
    PyObject *scandir = posix ? PyObject_GetAttrString(posix, "scandir") : nullptr;
    Py_XDECREF(posix);
    if (scandir != nullptr) {
      find_entry_fd_offset(scandir); /* Before the entry methods are wrapped: nothing judged yet. */
    }
    Py_XDECREF(scandir);
    PyErr_Clear();
  }
#endif
  for (const char *module_name : {"posix", "nt"}) {
    PyObject *module = PyImport_ImportModule(module_name);
    PyObject *type = module ? PyObject_GetAttrString(module, "DirEntry") : nullptr;
    Py_XDECREF(module);
    PyErr_Clear();
    if (type == nullptr || !PyType_Check(type) || ((PyTypeObject *)type)->tp_dict == nullptr) {
      Py_XDECREF(type);
      continue;
    }
    PyTypeObject *entry_type = (PyTypeObject *)type;
    for (size_t i = 0; i < entry_count; i++) {
      EntryWrap &wrap = entry_wraps[i];
      PyObject *raw = PyDict_GetItemString(entry_type->tp_dict, wrap.name); /* Borrowed. */
      if (raw == nullptr || wrap.installed != nullptr || (wrap.original != nullptr && raw != wrap.original)) {
        continue; /* Installed, or something else stands there now: CPython's first original only (no recursion). */
      }
      /* A method descriptor of the entry's own type, as the original is: the entry comes as `self`. */
      PyObject *method = PyDescr_NewMethod(entry_type, &entry_methods[i]);
      if (method != nullptr) {
        if (wrap.original == nullptr) {
          wrap.original = Py_NewRef(raw); /* CPython's own: kept for the life of the process (a held copy calls it). */
        }
        wrap.installed = (PyTypeObject *)Py_NewRef(type);
        note_wrapped_owner(raw); /* posix's or nt's: never made again while confined. */
        PyDict_SetItemString(entry_type->tp_dict, wrap.name, method); /* Immutable to Python; changed here, in C. */
        PyType_Modified(entry_type);
      }
      Py_XDECREF(method);
    }
    Py_DECREF(type);
  }
  PyErr_Clear();
  return 0;
}

void remove_wrappers_entry()
{
  for (EntryWrap &wrap : entry_wraps) {
    if (wrap.installed == nullptr) {
      continue;
    }
    note_wrapped_owner(wrap.original); /* posix's or nt's, as at the install: never made again while confined. */
    PyDict_SetItemString(wrap.installed->tp_dict, wrap.name, wrap.original); /* The original descriptor again. */
    PyType_Modified(wrap.installed);
    Py_CLEAR(wrap.installed);
  }
  PyErr_Clear();
}

}  // namespace blender::angelo_guard
