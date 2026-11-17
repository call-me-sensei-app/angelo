/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * `os.environ` and `os.environb` as views of the process's own environment (doc/angelo/script_confinement.rst, "The
 * process's state"). Python's own mapping keeps a copy (`_data`) that can be changed without touching the
 * environment, and every reader of the mapping (Angelo starting a program with `env=dict(os.environ)`, `requests`
 * finding a proxy or a certificate bundle) would see what a script put there. Here there is no copy: each read asks
 * the C environment, which a confined thread can't change (`os.putenv`, `os.unsetenv` refused), and a write goes
 * through `os.putenv`/`os.unsetenv`, so it is refused the same way. The view has no attributes to replace, its type
 * can't be changed, and a script that rebinds `os.environ` itself has the old view put back by the journal.
 *
 * It behaves as Python's mapping does: str keys and values (bytes for `os.environb`), the file-system encoding with
 * surrogate escapes; on Windows keys read back in upper case and match whatever their case. `keys()`, `items()` and
 * `values()` are views of a copy taken when called; a write by another program's library in the process (native
 * `setenv`) shows at once.
 */

#include <Python.h>

#include <cstring>
#include <utility>

#ifdef _WIN32
#  include <stdlib.h>
#  include <wchar.h>
#else
extern char **environ;
#endif

#include "bpy_angelo_guard_confine_intern.hh"

namespace blender::angelo_guard {

struct EnvironView {
  PyObject_HEAD
  bool bytes; /* `os.environb`. */
};

static PyTypeObject *view_type = nullptr;
static PyObject *original_putenv = nullptr, *original_unsetenv = nullptr;

bool environ_view(PyObject *object)
{
  return view_type != nullptr && Py_IS_TYPE(object, view_type);
}

/* -------------------------------------------------------------------- */
/** \name Reading the C environment
 * \{ */

/** Every entry as a new dict of the view's types, or null with an exception. */
static PyObject *snapshot(EnvironView *self)
{
  PyObject *result = PyDict_New();
  if (result == nullptr) {
    return nullptr;
  }
#ifdef _WIN32
  _wgetenv(L"PATH"); /* The C runtime builds its wide table on first use. */
  for (wchar_t **entry = _wenviron; entry && *entry; entry++) {
    const wchar_t *equals = wcschr(*entry + 1, L'='); /* "=C:" style names start with '='. */
    if (equals == nullptr) {
      continue;
    }
    PyObject *key = PyUnicode_FromWideChar(*entry, equals - *entry);
    PyObject *upper = key ? PyObject_CallMethod(key, "upper", nullptr) : nullptr;
    PyObject *value = PyUnicode_FromWideChar(equals + 1, -1);
    const int failed = (upper && value) ? PyDict_SetDefault(result, upper, value) == nullptr : 1;
    Py_XDECREF(key);
    Py_XDECREF(upper);
    Py_XDECREF(value);
    if (failed) {
      Py_DECREF(result);
      return nullptr;
    }
  }
  (void)self;
#else
  for (char **entry = environ; entry && *entry; entry++) {
    const char *equals = strchr(*entry, '=');
    if (equals == nullptr) {
      continue;
    }
    PyObject *key = self->bytes ? PyBytes_FromStringAndSize(*entry, equals - *entry) :
                                  PyUnicode_DecodeFSDefaultAndSize(*entry, equals - *entry);
    PyObject *value = self->bytes ? PyBytes_FromString(equals + 1) : PyUnicode_DecodeFSDefault(equals + 1);
    const int failed = (key && value) ? PyDict_SetDefault(result, key, value) == nullptr : 1;
    Py_XDECREF(key);
    Py_XDECREF(value);
    if (failed) {
      Py_DECREF(result);
      return nullptr;
    }
  }
#endif
  return result;
}

/** The value of `key`, a new reference; null with KeyError (or TypeError for a key of another type). */
static PyObject *lookup(EnvironView *self, PyObject *key)
{
#ifdef _WIN32
  if (!PyUnicode_Check(key)) {
    PyErr_Format(PyExc_TypeError, "str expected, not %s", Py_TYPE(key)->tp_name);
    return nullptr;
  }
  wchar_t *name = PyUnicode_AsWideCharString(key, nullptr);
  if (name == nullptr) {
    return nullptr;
  }
  const wchar_t *value = wcslen(name) > 0 ? _wgetenv(name) : nullptr;
  PyMem_Free(name);
  if (value == nullptr) {
    PyErr_SetObject(PyExc_KeyError, key);
    return nullptr;
  }
  return PyUnicode_FromWideChar(value, -1);
#else
  PyObject *encoded = nullptr;
  if (self->bytes) {
    if (!PyBytes_Check(key)) {
      PyErr_Format(PyExc_TypeError, "bytes expected, not %s", Py_TYPE(key)->tp_name);
      return nullptr;
    }
    encoded = Py_NewRef(key);
  }
  else {
    if (!PyUnicode_Check(key)) {
      PyErr_Format(PyExc_TypeError, "str expected, not %s", Py_TYPE(key)->tp_name);
      return nullptr;
    }
    encoded = PyUnicode_EncodeFSDefault(key);
    if (encoded == nullptr) {
      return nullptr;
    }
  }
  const char *name = PyBytes_AS_STRING(encoded);
  const char *value = (strlen(name) == size_t(PyBytes_GET_SIZE(encoded)) && name[0] != '\0') ? getenv(name) :
                                                                                               nullptr;
  PyObject *result = nullptr;
  if (value == nullptr) {
    PyErr_SetObject(PyExc_KeyError, key);
  }
  else {
    result = self->bytes ? PyBytes_FromString(value) : PyUnicode_DecodeFSDefault(value);
  }
  Py_DECREF(encoded);
  return result;
#endif
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name The mapping
 * \{ */

static PyObject *view_getitem(PyObject *self, PyObject *key)
{
  return lookup((EnvironView *)self, key);
}

static int view_setitem(PyObject *self, PyObject *key, PyObject *value)
{
  if (value == nullptr) {
    /* Missing: KeyError as a dict's; present: `os.unsetenv` (its audit event: refused while confined). */
    PyObject *present = lookup((EnvironView *)self, key);
    if (present == nullptr) {
      return -1;
    }
    Py_DECREF(present);
    PyObject *done = PyObject_CallOneArg(original_unsetenv, key);
    Py_XDECREF(done);
    return done ? 0 : -1;
  }
  if (((EnvironView *)self)->bytes ? !PyBytes_Check(value) : !PyUnicode_Check(value)) {
    PyErr_Format(PyExc_TypeError,
                 "%s expected, not %s",
                 ((EnvironView *)self)->bytes ? "bytes" : "str",
                 Py_TYPE(value)->tp_name);
    return -1;
  }
  PyObject *done = PyObject_CallFunctionObjArgs(original_putenv, key, value, nullptr);
  Py_XDECREF(done);
  return done ? 0 : -1;
}

static Py_ssize_t view_length(PyObject *self)
{
  PyObject *all = snapshot((EnvironView *)self);
  const Py_ssize_t size = all ? PyDict_GET_SIZE(all) : -1;
  Py_XDECREF(all);
  return size;
}

static int view_contains(PyObject *self, PyObject *key)
{
  PyObject *value = lookup((EnvironView *)self, key);
  if (value != nullptr) {
    Py_DECREF(value);
    return 1;
  }
  if (PyErr_ExceptionMatches(PyExc_KeyError) || PyErr_ExceptionMatches(PyExc_TypeError)) {
    PyErr_Clear(); /* As a mapping's `in`: a key of another type is simply not there. */
    return 0;
  }
  return -1;
}

static PyObject *view_iter(PyObject *self)
{
  PyObject *all = snapshot((EnvironView *)self);
  PyObject *keys = all ? PyDict_Keys(all) : nullptr;
  PyObject *iterator = keys ? PyObject_GetIter(keys) : nullptr;
  Py_XDECREF(keys);
  Py_XDECREF(all);
  return iterator;
}

static PyObject *view_repr(PyObject *self)
{
  PyObject *all = snapshot((EnvironView *)self);
  PyObject *text = all ? PyUnicode_FromFormat("environ(%R)", all) : nullptr;
  Py_XDECREF(all);
  return text;
}

static PyObject *view_richcompare(PyObject *self, PyObject *other, int op)
{
  if (op != Py_EQ && op != Py_NE) {
    Py_RETURN_NOTIMPLEMENTED;
  }
  PyObject *all = snapshot((EnvironView *)self);
  PyObject *other_all = all ? (environ_view(other) ? snapshot((EnvironView *)other) : nullptr) : nullptr;
  PyObject *result = all ? PyObject_RichCompare(all, other_all ? other_all : other, op) : nullptr;
  Py_XDECREF(other_all);
  Py_XDECREF(all);
  return result;
}

/* Methods. */

static PyObject *view_copy(PyObject *self, PyObject * /*unused*/)
{
  return snapshot((EnvironView *)self);
}

static PyObject *view_of(PyObject *self, const char *method)
{
  PyObject *all = snapshot((EnvironView *)self);
  PyObject *result = all ? PyObject_CallMethod(all, method, nullptr) : nullptr;
  Py_XDECREF(all);
  return result;
}

static PyObject *view_keys(PyObject *self, PyObject * /*unused*/)
{
  return view_of(self, "keys");
}

static PyObject *view_items(PyObject *self, PyObject * /*unused*/)
{
  return view_of(self, "items");
}

static PyObject *view_values(PyObject *self, PyObject * /*unused*/)
{
  return view_of(self, "values");
}

static PyObject *view_get(PyObject *self, PyObject *const *args, Py_ssize_t nargs)
{
  if (nargs < 1 || nargs > 2) {
    PyErr_SetString(PyExc_TypeError, "get expected 1 or 2 arguments");
    return nullptr;
  }
  PyObject *value = lookup((EnvironView *)self, args[0]);
  if (value == nullptr && PyErr_ExceptionMatches(PyExc_KeyError)) {
    PyErr_Clear();
    return Py_NewRef(nargs > 1 ? args[1] : Py_None);
  }
  return value;
}

static PyObject *view_setdefault(PyObject *self, PyObject *const *args, Py_ssize_t nargs)
{
  if (nargs < 1 || nargs > 2) {
    PyErr_SetString(PyExc_TypeError, "setdefault expected 1 or 2 arguments");
    return nullptr;
  }
  PyObject *value = lookup((EnvironView *)self, args[0]);
  if (value != nullptr || !PyErr_ExceptionMatches(PyExc_KeyError)) {
    return value;
  }
  PyErr_Clear();
  PyObject *fallback = nargs > 1 ? args[1] : Py_None;
  if (view_setitem(self, args[0], fallback) < 0) {
    return nullptr;
  }
  return Py_NewRef(fallback);
}

static PyObject *view_pop(PyObject *self, PyObject *const *args, Py_ssize_t nargs)
{
  if (nargs < 1 || nargs > 2) {
    PyErr_SetString(PyExc_TypeError, "pop expected 1 or 2 arguments");
    return nullptr;
  }
  PyObject *value = lookup((EnvironView *)self, args[0]);
  if (value == nullptr) {
    if (nargs > 1 && PyErr_ExceptionMatches(PyExc_KeyError)) {
      PyErr_Clear();
      return Py_NewRef(args[1]);
    }
    return nullptr;
  }
  if (view_setitem(self, args[0], nullptr) < 0) {
    Py_DECREF(value);
    return nullptr;
  }
  return value;
}

static PyObject *view_popitem(PyObject *self, PyObject * /*unused*/)
{
  PyObject *all = snapshot((EnvironView *)self);
  if (all == nullptr) {
    return nullptr;
  }
  PyObject *key, *value;
  Py_ssize_t position = 0;
  if (!PyDict_Next(all, &position, &key, &value)) {
    Py_DECREF(all);
    PyErr_SetString(PyExc_KeyError, "popitem(): environment is empty");
    return nullptr;
  }
  PyObject *pair = PyTuple_Pack(2, key, value);
  if (pair != nullptr && view_setitem(self, key, nullptr) < 0) {
    Py_CLEAR(pair);
  }
  Py_DECREF(all);
  return pair;
}

static PyObject *view_clear(PyObject *self, PyObject * /*unused*/)
{
  PyObject *all = snapshot((EnvironView *)self);
  if (all == nullptr) {
    return nullptr;
  }
  PyObject *key, *value;
  Py_ssize_t position = 0;
  while (PyDict_Next(all, &position, &key, &value)) {
    if (view_setitem(self, key, nullptr) < 0) {
      Py_DECREF(all);
      return nullptr;
    }
  }
  Py_DECREF(all);
  Py_RETURN_NONE;
}

/** `update(other=(), /, **kwargs)`, as a dict's: a mapping (its keys), else pairs. */
static PyObject *view_update(PyObject *self, PyObject *args, PyObject *kwargs)
{
  PyObject *other = nullptr;
  if (!PyArg_UnpackTuple(args, "update", 0, 1, &other)) {
    return nullptr;
  }
  PyObject *pairs = nullptr;
  if (other != nullptr) {
    PyObject *copy = PyDict_New();
    if (copy == nullptr || PyDict_Update(copy, other) < 0) {
      PyErr_Clear();
      if (copy != nullptr && PyDict_MergeFromSeq2(copy, other, 1) < 0) {
        Py_DECREF(copy);
        return nullptr;
      }
    }
    pairs = copy;
  }
  for (PyObject *source : {pairs, kwargs}) {
    PyObject *key, *value;
    Py_ssize_t position = 0;
    while (source != nullptr && PyDict_Next(source, &position, &key, &value)) {
      if (view_setitem(self, key, value) < 0) {
        Py_XDECREF(pairs);
        return nullptr;
      }
    }
  }
  Py_XDECREF(pairs);
  Py_RETURN_NONE;
}

static PyObject *view_or(PyObject *left, PyObject *right)
{
  PyObject *self = environ_view(left) ? left : right;
  PyObject *all = snapshot((EnvironView *)self);
  if (all == nullptr) {
    return nullptr;
  }
  PyObject *result = self == left ? PyNumber_Or(all, right) : PyNumber_Or(left, all);
  Py_DECREF(all);
  return result;
}

static PyObject *view_ior(PyObject *self, PyObject *other)
{
  PyObject *args = PyTuple_Pack(1, other);
  PyObject *done = args ? view_update(self, args, nullptr) : nullptr;
  Py_XDECREF(args);
  if (done == nullptr) {
    return nullptr;
  }
  Py_DECREF(done);
  return Py_NewRef(self);
}

static PyObject *view_deepcopy(PyObject *self, PyObject * /*memo*/)
{
  return snapshot((EnvironView *)self);
}

static PyMethodDef view_methods[] = {
    {"copy", view_copy, METH_NOARGS, nullptr},
    {"__copy__", view_copy, METH_NOARGS, nullptr},
    {"__deepcopy__", view_deepcopy, METH_O, nullptr},
    {"keys", view_keys, METH_NOARGS, nullptr},
    {"items", view_items, METH_NOARGS, nullptr},
    {"values", view_values, METH_NOARGS, nullptr},
    {"get", (PyCFunction)(void (*)(void))view_get, METH_FASTCALL, nullptr},
    {"setdefault", (PyCFunction)(void (*)(void))view_setdefault, METH_FASTCALL, nullptr},
    {"pop", (PyCFunction)(void (*)(void))view_pop, METH_FASTCALL, nullptr},
    {"popitem", view_popitem, METH_NOARGS, nullptr},
    {"clear", view_clear, METH_NOARGS, nullptr},
    {"update", (PyCFunction)(void (*)(void))view_update, METH_VARARGS | METH_KEYWORDS, nullptr},
    {nullptr, nullptr, 0, nullptr},
};

static PyType_Slot view_slots[] = {
    {Py_mp_subscript, (void *)view_getitem},
    {Py_mp_ass_subscript, (void *)view_setitem},
    {Py_mp_length, (void *)view_length},
    {Py_sq_contains, (void *)view_contains},
    {Py_tp_iter, (void *)view_iter},
    {Py_tp_repr, (void *)view_repr},
    {Py_tp_richcompare, (void *)view_richcompare},
    {Py_tp_hash, (void *)PyObject_HashNotImplemented},
    {Py_nb_or, (void *)view_or},
    {Py_nb_inplace_or, (void *)view_ior},
    {Py_tp_methods, view_methods},
    {0, nullptr},
};

/* Immutable and not instantiable from Python: no attribute of the type, and no instance's `__class__`, can change. */
static PyType_Spec view_spec = {
    "_angelo_guard.Environ",
    sizeof(EnvironView),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION | Py_TPFLAGS_MAPPING,
    view_slots,
};

/** \} */

static PyObject *make_view(bool bytes)
{
  EnvironView *view = PyObject_New(EnvironView, view_type);
  if (view != nullptr) {
    view->bytes = bytes;
  }
  return (PyObject *)view;
}

void install_environ_view()
{
  if (view_type != nullptr) {
    return;
  }
#ifdef _WIN32
  const char *const os_module = "nt";
#else
  const char *const os_module = "posix";
#endif
  PyObject *native = PyImport_ImportModule(os_module);
  original_putenv = native ? PyObject_GetAttrString(native, "putenv") : nullptr;
  original_unsetenv = native ? PyObject_GetAttrString(native, "unsetenv") : nullptr;
  Py_XDECREF(native);
  PyObject *os = PyImport_ImportModule("os");
  view_type = (original_putenv && original_unsetenv && os) ? (PyTypeObject *)PyType_FromSpec(&view_spec) :
                                                             nullptr;
  if (view_type == nullptr) {
    Py_XDECREF(os);
    PyErr_Clear();
    return;
  }
  /* A registered MutableMapping, as Python's own is (`isinstance(os.environ, Mapping)`). */
  PyObject *abc = PyImport_ImportModule("collections.abc");
  PyObject *mutable_mapping = abc ? PyObject_GetAttrString(abc, "MutableMapping") : nullptr;
  Py_XDECREF(mutable_mapping ? PyObject_CallMethod(mutable_mapping, "register", "O", view_type) : nullptr);
  Py_XDECREF(mutable_mapping);
  Py_XDECREF(abc);
  PyErr_Clear();
  for (const auto &[name, bytes] : {std::pair<const char *, bool>{"environ", false}, {"environb", true}}) {
    PyObject *original = PyObject_GetAttrString(os, name);
    PyObject *view = original ? make_view(bytes) : nullptr;
    if (view != nullptr) {
      put_in("os", name, view, original);
    }
    Py_XDECREF(view);
    Py_XDECREF(original);
    PyErr_Clear();
  }
  Py_DECREF(os);
}

}  // namespace blender::angelo_guard
