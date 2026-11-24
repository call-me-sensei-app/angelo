/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Where code comes from (doc/angelo/script_confinement.rst, "Agent code"). Agent code is what a confined thread
 * compiled or ran, never what a file name says: so a confined thread cannot make code that looks installed.
 *
 * - `compile` under a `<frozen …>` name is refused (frozen modules are never compiled at run time), and under an
 *   installed file's name only when the source is that file's bytes (the import system loading it);
 * - a code object made by `code.replace`/`CodeType` keeps an agent name, or is made by installed code from an
 *   installed name (`types.coroutine`); otherwise it is refused;
 * - `marshal.loads` runs only in importlib's own bytecode loader (its code object, held here), on an installed
 *   `.pyc`'s exact bytes, or a `.pyc` beside a module that is not installed (whose code then carries that module's
 *   name and is the agent's); `_imp._fix_co_filename` likewise (bpy_angelo_guard_confine_wrap_io.cc).
 * - Code `builtins.compile` makes while confined is tainted by object (bpy_angelo_guard_confine_wrap_io.cc), and so
 *   is code `exec` runs (the hook).
 */

#include <Python.h>

#include <cstdio>
#include <cstring>
#include <optional>
#include <string>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_paths.hh"

namespace blender::angelo_guard {

static const char *text_of(PyObject *name)
{
  if (name == nullptr || !PyUnicode_Check(name)) {
    return nullptr;
  }
  const char *text = PyUnicode_AsUTF8(name);
  PyErr_Clear();
  return text;
}

/* The code objects of the installed bytecode loaders: importlib's `_compile_bytecode` and Angelo's own shipped-file
 * loader (sitecustomize's `get_code`), registered at install (`add_bytecode_loaders`). */
static PyObject *bytecode_loaders = nullptr; /* list */

/* -------------------------------------------------------------------- */
/** \name An installed function's closure: not read while confined
 *
 * Installed code keeps what a script must not call directly in closures (a grant entry's original, a wrapped
 * operator). `function.__closure__` is replaced by a getter that refuses a confined thread an installed function's
 * cells, whoever reads them (`inspect.getclosurevars` too); a script's own functions stay readable. Defaults and
 * globals stay readable (`inspect.signature`, `typing` need them).
 * \{ */

static PyObject *original_closure = nullptr; /* The member descriptor. */

static PyObject *guarded_closure(PyObject *function, void * /*closure*/)
{
  if (PyFunction_Check(function) && confined_now() &&
      !agent_code((PyCodeObject *)PyFunction_GET_CODE(function)))
  {
    refuse(GUARD, "an installed function's __closure__");
    return nullptr;
  }
  return Py_TYPE(original_closure)->tp_descr_get(original_closure, function, (PyObject *)Py_TYPE(function));
}

static PyGetSetDef closure_getset = {"__closure__", guarded_closure, nullptr, nullptr, nullptr};

static void closure_guard_install()
{
  /* A static built-in type's dict lives with the interpreter since 3.12: `tp_dict` may be null. */
  PyObject *names = PyType_GetDict(&PyFunction_Type); /* New reference. */
  PyObject *member = names ? PyDict_GetItemString(names, "__closure__") : nullptr; /* Borrowed. */
  if (member == nullptr || original_closure != nullptr || Py_TYPE(member)->tp_descr_get == nullptr) {
    Py_XDECREF(names);
    PyErr_Clear();
    return;
  }
  PyObject *getter = PyDescr_NewGetSet(&PyFunction_Type, &closure_getset);
  if (getter != nullptr) {
    original_closure = Py_NewRef(member);
    PyDict_SetItemString(names, "__closure__", getter);
    PyType_Modified(&PyFunction_Type);
    Py_DECREF(getter);
  }
  Py_DECREF(names);
  PyErr_Clear();
}

/** \} */

void provenance_install()
{
  if (bytecode_loaders != nullptr) {
    return;
  }
  import_frames_install();
  closure_guard_install();
  bytecode_loaders = PyList_New(0);
  PyObject *module = PyImport_ImportModule("importlib._bootstrap_external");
  PyObject *function = module ? PyObject_GetAttrString(module, "_compile_bytecode") : nullptr;
  PyObject *code = function ? PyObject_GetAttrString(function, "__code__") : nullptr;
  if (code != nullptr && bytecode_loaders != nullptr) {
    PyList_Append(bytecode_loaders, code);
  }
  Py_XDECREF(code);
  Py_XDECREF(function);
  Py_XDECREF(module);
  PyErr_Clear();
}

PyObject *guard_add_bytecode_loaders(PyObject * /*self*/, PyObject *codes)
{
  if (confined_now()) {
    refuse(GUARD, "add_bytecode_loaders");
    return nullptr;
  }
  provenance_install();
  PyObject *iterator = PyObject_GetIter(codes);
  while (PyObject *item = iterator ? PyIter_Next(iterator) : nullptr) {
    const char *file = PyCode_Check(item) ? text_of(((PyCodeObject *)item)->co_filename) : nullptr;
    if (file != nullptr && folders().trusted(file) && !agent_code((PyCodeObject *)item) &&
        PySequence_Contains(bytecode_loaders, item) == 0)
    {
      PyList_Append(bytecode_loaders, item);
    }
    Py_DECREF(item);
  }
  Py_XDECREF(iterator);
  if (PyErr_Occurred()) {
    return nullptr;
  }
  Py_RETURN_NONE;
}

bool in_bytecode_loader()
{
  PyFrameObject *frame = PyEval_GetFrame(); /* Borrowed. */
  if (frame == nullptr || bytecode_loaders == nullptr) {
    return false;
  }
  PyCodeObject *code = PyFrame_GetCode(frame);
  bool inside = false;
  for (Py_ssize_t i = 0; i < PyList_GET_SIZE(bytecode_loaders); i++) {
    inside = inside || PyList_GET_ITEM(bytecode_loaders, i) == (PyObject *)code;
  }
  Py_DECREF(code);
  return inside;
}

#ifdef _WIN32
static const char *const SEP_TEXT = "\\";
#else
static const char *const SEP_TEXT = "/";
#endif

static bool frozen_name(const char *text)
{
  return strncmp(text, "<frozen", 7) == 0;
}

static bool generic(const char *text)
{
  return text[0] == '<';
}

static std::optional<std::string> file_bytes(const char *path)
{
  FILE *file = lookup_fopen_read(path);
  if (file == nullptr) {
    return std::nullopt;
  }
  std::string data;
  char buffer[65536];
  size_t got;
  while ((got = fread(buffer, 1, sizeof(buffer), file)) > 0) {
    data.append(buffer, got);
  }
  fclose(file);
  return data;
}

/** Whether `source` (str, bytes or a buffer) is exactly the file at `path` from byte `skip` on. */
static bool same_as_file(PyObject *source, const char *path, size_t skip = 0)
{
  const std::optional<std::string> data = file_bytes(path);
  if (!data || data->size() < skip) {
    return false;
  }
  if (PyUnicode_Check(source)) {
    Py_ssize_t size = 0;
    const char *text = PyUnicode_AsUTF8AndSize(source, &size);
    PyErr_Clear();
    return text != nullptr && data->compare(skip, std::string::npos, text, size_t(size)) == 0;
  }
  Py_buffer view;
  if (PyObject_GetBuffer(source, &view, PyBUF_SIMPLE) != 0) {
    PyErr_Clear();
    return false;
  }
  const bool same = data->compare(skip, std::string::npos, (const char *)view.buf, size_t(view.len)) == 0;
  PyBuffer_Release(&view);
  return same;
}


int provenance_compile(PyObject *args)
{
  PyObject *source = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  const char *name = text_of(PyTuple_GET_SIZE(args) > 1 ? PyTuple_GET_ITEM(args, 1) : nullptr);
  if (name == nullptr || source == nullptr) {
    return 0;
  }
  if (frozen_name(name)) {
    return refuse(GUARD, "compile under a <frozen> name");
  }
  if (!generic(name) && folders().trusted(name)) {
    if (!same_as_file(source, name)) {
      return refuse(GUARD, name); /* Only an installed file's own source compiles under its name. */
    }
    installed_bytes_verified = true; /* That file's own source: what it compiles to is that file's. */
  }
  return 0;
}

/** Whether the innermost Python frame runs installed code (not the agent's). */
static bool innermost_installed()
{
  PyFrameObject *frame = PyEval_GetFrame(); /* Borrowed. */
  if (frame == nullptr) {
    return false;
  }
  PyCodeObject *code = PyFrame_GetCode(frame);
  bool installed = !agent_code(code);
  if (installed) {
    const char *file = text_of(code->co_filename);
    installed = file != nullptr && folders().trusted(file);
  }
  Py_DECREF(code);
  return installed;
}

int provenance_code_new(PyObject *args)
{
  PyObject *name = PyTuple_GET_SIZE(args) > 1 ? PyTuple_GET_ITEM(args, 1) : nullptr;
  const char *text = text_of(name);
  if (text == nullptr) {
    return refuse(GUARD, "code.__new__");
  }
  if (agent_name(name)) {
    return 0; /* Still the agent's by its name. */
  }
  if (frozen_name(text) || generic(text)) {
    return refuse(GUARD, "code.__new__");
  }
  if (folders().trusted(text)) {
    /* An installed helper changing installed code's flags (`types.coroutine`); never the agent's own frame. */
    return innermost_installed() ? 0 : refuse(GUARD, "code.__new__");
  }
  taint_name(name); /* Any other name: whatever carries it is the agent's from now on. */
  return 0;
}

/** A local of the innermost frame as text (the bytecode loader's own arguments). */
static std::optional<std::string> frame_local(const char *key)
{
  PyFrameObject *frame = PyEval_GetFrame(); /* Borrowed. */
  PyObject *locals = frame ? PyFrame_GetLocals(frame) : nullptr;
  PyObject *value = nullptr;
  if (locals != nullptr) {
    PyMapping_GetOptionalItemString(locals, key, &value);
  }
  Py_XDECREF(locals);
  std::optional<std::string> result;
  if (const char *text = text_of(value)) {
    result = text;
  }
  Py_XDECREF(value);
  PyErr_Clear();
  return result;
}

int provenance_marshal(const char *event, PyObject *args)
{
  if (loading_frozen > 0) {
    installed_bytes_verified = true;
    return 0; /* A frozen module's own bytes, compiled into the binary (bpy_angelo_guard_confine_wrap.cc). */
  }
  if (strcmp(event, "marshal.loads") != 0 || !in_bytecode_loader()) {
    return refuse(GUARD, event);
  }
  PyObject *data = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  std::optional<std::string> bytecode = frame_local("bytecode_path");
  const std::optional<std::string> source = frame_local("source_path");
  if (!bytecode) {
    /* The shipped-file loader names its .pyc as importlib does: <folder>/__pycache__/<name>.<cache tag>.pyc. */
    const std::optional<std::string> folder = frame_local("folder"), name = frame_local("name");
    const std::optional<std::string> tag = frame_local("cache_tag");
    if (folder && name && tag && name->size() > 3) {
      bytecode = *folder + SEP_TEXT + "__pycache__" + SEP_TEXT + name->substr(0, name->size() - 3) + "." + *tag +
                 ".pyc";
    }
  }
  if (data != nullptr && bytecode && folders().trusted(bytecode->c_str())) {
    /* An installed .pyc: only its own bytes (after the 16-byte header). */
    if (!same_as_file(data, bytecode->c_str(), 16)) {
      return refuse(GUARD, bytecode->c_str());
    }
    installed_bytes_verified = true; /* That .pyc's own bytes: what they unmarshal to is that file's. */
    return 0;
  }
  if (source && !folders().trusted(source->c_str())) {
    return 0; /* Beside a module that is not installed: its code gets that module's name and is the agent's. */
  }
  return refuse(GUARD, bytecode ? bytecode->c_str() : event);
}

}  // namespace blender::angelo_guard
