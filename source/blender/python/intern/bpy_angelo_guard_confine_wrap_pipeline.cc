/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Path-level gates for the pipeline libraries (USD, OpenImageIO, OpenVDB, MaterialX, OpenColorIO, OpenEXR,
 * OpenTimelineIO, OSL queries, Audaspace, Mantaflow; doc/angelo/script_confinement_pipeline.rst): their file entry
 * points (bpy_angelo_guard_confine_pipeline_table.cc) judged like `open`, so a confined script uses them on its own
 * files and is refused private ones.
 *
 * A gate goes in when its library's extension module is made, whoever imports it and however (an `import`, an
 * `importlib.import_module`, which raises no audit event for the module itself, or Angelo before any script):
 * `_imp.create_dynamic`/`exec_dynamic` and their builtin twins are wrapped on top of the confinement's own wrappers,
 * and modules already loaded are gated at install. The originals stay in C.
 *
 * USD's own I/O (composition, Blender's importer and exporter) goes through the confined resolver
 * (io/usd/usd_confined_resolver.hh), judged here by `usd_judge`, from any thread.
 */

#include <Python.h>

#include <atomic>
#include <cstring>
#include <deque>
#include <string>
#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_pipeline.hh"
#include "bpy_angelo_guard_confine_record.hh"

#ifdef WITH_USD
#  include "usd_confined_resolver.hh"
#endif

namespace blender::angelo_guard {

/** Whether USD's primary resolver is the confined one (set at install). Without it, USD is refused to scripts. */
bool usd_confined = false;

/* -------------------------------------------------------------------- */
/** \name Judging paths
 * \{ */

thread_local const char *pending_refusal = nullptr;

int judge_text(const std::string &path, bool write, const char *what)
{
  if (path.empty()) {
    return 0;
  }
  if (names_a_url(path.c_str())) { /* A native reader would open it with no audit event (names_a_url). */
    pending_refusal = NETWORK;
    return refuse(NETWORK, what);
  }
  const char *reason = nullptr;
  switch (folders().judge(path, write)) {
    case Refusal::Private:
      reason = PRIVATE;
      break;
    case Refusal::Startup:
      reason = STARTUP;
      break;
    case Refusal::ToolOutput:
      reason = TOOL_OUTPUT;
      break;
    case Refusal::Network:
      reason = NETWORK;
      break;
    case Refusal::None:
      return 0;
  }
  pending_refusal = reason;
  return refuse(reason, what);
}

static bool absolute(const std::string &path)
{
  return !path.empty() && (path[0] == '/' || path[0] == '\\' || (path.size() > 1 && path[1] == ':'));
}

int judge_search(const std::string &name, const std::vector<std::string> &folders, const char *what)
{
  if (judge_text(name, false, what) != 0) {
    return -1;
  }
  if (name.empty() || absolute(name)) {
    return 0;
  }
  for (const std::string &folder : folders) {
    if (!folder.empty() && judge_text(folder + "/" + name, false, what) != 0) {
      return -1;
    }
  }
  return 0;
}

std::string text_of(PyObject *value)
{
  std::string text;
  if (value == nullptr) {
    return text;
  }
  if (PyUnicode_Check(value)) {
    if (const char *utf8 = PyUnicode_AsUTF8(value)) {
      text = utf8;
    }
  }
  else if (PyBytes_Check(value)) {
    text.assign(PyBytes_AS_STRING(value), size_t(PyBytes_GET_SIZE(value)));
  }
  PyErr_Clear();
  return text;
}

std::string path_text(PyObject *value)
{
  if (value == nullptr || value == Py_None) {
    return {};
  }
  if (PyUnicode_Check(value) || PyBytes_Check(value)) {
    return text_of(value);
  }
  std::string text;
  if (PyObject *path = PyOS_FSPath(value)) {
    text = text_of(path);
    Py_DECREF(path);
  }
  else if (PyErr_Clear(), PyObject_HasAttrString(value, "asString")) {
    /* MaterialX's FilePath and FileSearchPath. */
    PyObject *string = PyObject_CallMethod(value, "asString", nullptr);
    text = text_of(string);
    Py_XDECREF(string);
  }
  PyErr_Clear();
  return text;
}

static void split_into(const std::string &text, std::vector<std::string> &out)
{
#ifdef _WIN32
  const char *separators = ";";
#else
  const char *separators = ":;";
#endif
  size_t start = 0;
  while (start <= text.size()) {
    const size_t end = text.find_first_of(separators, start);
    const std::string part = text.substr(start, end == std::string::npos ? std::string::npos : end - start);
    if (!part.empty()) {
      out.push_back(part);
    }
    if (end == std::string::npos) {
      break;
    }
    start = end + 1;
  }
}

std::vector<std::string> folder_list(PyObject *value)
{
  std::vector<std::string> folders;
  if (value == nullptr || value == Py_None) {
    return folders;
  }
  if (PyUnicode_Check(value) || PyBytes_Check(value) || PyObject_HasAttrString(value, "asString")) {
    split_into(path_text(value), folders);
    return folders;
  }
  if (PyObject *iterator = PyObject_GetIter(value)) {
    while (PyObject *item = PyIter_Next(iterator)) {
      split_into(path_text(item), folders);
      Py_DECREF(item);
    }
    Py_DECREF(iterator);
  }
  PyErr_Clear();
  return folders;
}

PyObject *gate_argument(PyObject *args, PyObject *kwargs, Py_ssize_t shift, const Arg &arg)
{
  if (arg.position >= 0 && arg.position + shift < PyTuple_GET_SIZE(args)) {
    return PyTuple_GET_ITEM(args, arg.position + shift);
  }
  if (kwargs != nullptr && arg.keyword != nullptr) {
    PyObject *value = PyDict_GetItemString(kwargs, arg.keyword);
    return value;
  }
  return nullptr;
}

static Py_ssize_t shift_of(const Gate &gate)
{
  return gate.kind == Kind::Function || gate.kind == Kind::Static ? 0 : 1;
}

static int judge_every_string(PyObject *args, PyObject *kwargs, Py_ssize_t shift, bool write, const char *what)
{
  for (Py_ssize_t i = shift; i < PyTuple_GET_SIZE(args); i++) {
    if (judge_text(path_text(PyTuple_GET_ITEM(args, i)), write, what) != 0) {
      return -1;
    }
  }
  PyObject *key, *value;
  Py_ssize_t position = 0;
  while (kwargs != nullptr && PyDict_Next(kwargs, &position, &key, &value)) {
    if (judge_text(path_text(value), write, what) != 0) {
      return -1;
    }
  }
  return 0;
}

/** The special case, then every argument the table names. */
int gate_check(Gate &gate, PyObject **args, PyObject **kwargs)
{
  if (!usd_confined && strncmp(gate.module, "pxr.", 4) == 0) {
    return refuse(NATIVE, gate.name);
  }
  if (special_before(gate, args, kwargs) != 0) {
    return -1;
  }
  const Py_ssize_t shift = shift_of(gate);
  for (const Arg &arg : gate.args) {
    PyObject *value = gate_argument(*args, *kwargs, shift, arg);
    int refused = 0;
    switch (arg.role) {
      case Role::None:
        break;
      case Role::Read:
      case Role::Write:
        refused = judge_text(path_text(value), arg.role == Role::Write, gate.name);
        break;
      case Role::Folders:
        for (const std::string &folder : folder_list(value)) {
          if ((refused = judge_text(folder, false, gate.name)) != 0) {
            break;
          }
        }
        break;
      case Role::Native:
        if (!folder_list(value).empty()) {
          refused = refuse(NATIVE, gate.name);
        }
        break;
      case Role::AnyRead:
      case Role::AnyWrite:
        refused = judge_every_string(*args, *kwargs, shift, arg.role == Role::AnyWrite, gate.name);
        break;
    }
    if (refused != 0) {
      return -1;
    }
  }
  return 0;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name The gate: a callable that binds like a function
 * \{ */

struct GateObject {
  PyObject_HEAD
  Gate *gate;
};

PyTypeObject gate_type = {PyVarObject_HEAD_INIT(nullptr, 0)};

static PyObject *call_original(Gate &gate, PyObject *args, PyObject *kwargs)
{
  if (gate.kind == Kind::ClassMethod) {
    /* Bound to the class it is called on, as the class method would be. */
    PyObject *cls = PyTuple_GET_ITEM(args, 0);
    descrgetfunc get = Py_TYPE(gate.raw)->tp_descr_get;
    PyObject *bound = get ? get(gate.raw, nullptr, cls) : Py_NewRef(gate.raw);
    PyObject *rest = bound ? PyTuple_GetSlice(args, 1, PyTuple_GET_SIZE(args)) : nullptr;
    PyObject *result = rest ? PyObject_Call(bound, rest, kwargs) : nullptr;
    Py_XDECREF(rest);
    Py_XDECREF(bound);
    return result;
  }
  return PyObject_Call(gate.original, args, kwargs);
}

PyObject *gate_call(Gate &gate, PyObject *args, PyObject *kwargs)
{
  if (!confined_now()) {
    return call_original(gate, args, kwargs);
  }
  PyObject *call_args = Py_NewRef(args);
  PyObject *call_kwargs = Py_XNewRef(kwargs);
  const char *outer_refusal = pending_refusal;
  pending_refusal = nullptr;
  PyObject *result = gate_check(gate, &call_args, &call_kwargs) == 0 ?
                         call_original(gate, call_args, call_kwargs) :
                         nullptr;
  if (result == nullptr && pending_refusal == nullptr &&
      (gate.special == Special::OcioProcessor || gate.special == Special::OcioSetCurrent))
  {
    ocio_missing_refusal(gate);
  }
  if (result == nullptr && pending_refusal != nullptr && !PyErr_ExceptionMatches(script_confined_type)) {
    /* Refused inside the library (a LUT through OpenColorIO's proxy, an XInclude through MaterialX), which raised
     * its own exception around ours: the refusal is the confinement's. */
    PyErr_Clear();
    refuse(pending_refusal, gate.name);
  }
  pending_refusal = result == nullptr ? pending_refusal : outer_refusal;
  Py_DECREF(call_args);
  Py_XDECREF(call_kwargs);
  return result;
}

static PyObject *gate_object_call(PyObject *self, PyObject *args, PyObject *kwargs)
{
  return gate_call(*((GateObject *)self)->gate, args, kwargs);
}

static PyObject *gate_object_get(PyObject *self, PyObject *instance, PyObject * /*type*/)
{
  if (instance == nullptr || instance == Py_None) {
    return Py_NewRef(self);
  }
  return PyMethod_New(self, instance);
}

static void gate_object_dealloc(PyObject *self)
{
  Py_TYPE(self)->tp_free(self);
}

/** Reading an attribute the gate lacks (`__doc__`, `__name__`, `__module__`, a signature) reads the original's, so
 * `help()` and a package's re-export (`Tf.PrepareModule`) see the function they expect. */
static PyObject *gate_object_getattr(PyObject *self, PyObject *name)
{
  PyObject *found = PyObject_GenericGetAttr(self, name);
  const Gate &gate = *((GateObject *)self)->gate;
  if (found == nullptr && gate.original != nullptr && PyErr_ExceptionMatches(PyExc_AttributeError)) {
    PyErr_Clear();
    found = PyObject_GetAttr(gate.original, name);
  }
  return found;
}

static PyObject *gate_object_repr(PyObject *self)
{
  const Gate &gate = *((GateObject *)self)->gate;
  return PyUnicode_FromFormat("<gated %s.%s>", gate.owner ? gate.owner : gate.module, gate.name);
}

int gate_type_ready()
{
  if (gate_type.tp_flags & Py_TPFLAGS_READY) {
    return 0;
  }
  gate_type.tp_name = "_angelo_guard.PipelineGate";
  gate_type.tp_basicsize = sizeof(GateObject);
  gate_type.tp_flags = Py_TPFLAGS_DEFAULT;
  gate_type.tp_call = gate_object_call;
  gate_type.tp_descr_get = gate_object_get;
  gate_type.tp_dealloc = gate_object_dealloc;
  gate_type.tp_repr = gate_object_repr;
  gate_type.tp_getattro = gate_object_getattr;
  gate_type.tp_doc = "A pipeline library's file entry point, judged by the agent-script confinement.";
  return PyType_Ready(&gate_type);
}

PyObject *gate_object_new(Gate &gate)
{
  GateObject *object = PyObject_New(GateObject, &gate_type);
  if (object != nullptr) {
    object->gate = &gate;
  }
  return (PyObject *)object;
}

/** Whether a class dict entry is a gate already (directly, or inside a static or class method). */
bool gated(PyObject *raw)
{
  if (raw == nullptr) {
    return false;
  }
  if (Py_TYPE(raw) == &gate_type) {
    return true;
  }
  if (Py_TYPE(raw) == &PyStaticMethod_Type || Py_TYPE(raw) == &PyClassMethod_Type) {
    PyObject *function = PyObject_GetAttrString(raw, "__func__");
    const bool inner = function != nullptr && Py_TYPE(function) == &gate_type;
    Py_XDECREF(function);
    PyErr_Clear();
    return inner;
  }
  return false;
}

/** \} */

const char *usd_judge(const char *path, bool write)
{
  /* Composition runs on USD's worker threads, which hold no GIL and no region of their own: while any confined
   * region runs, every asset USD touches is judged. */
  if (active_regions.load(std::memory_order_relaxed) == 0) {
    return nullptr;
  }
  if (names_a_url(path)) {
    return NETWORK; /* USD's resolver would fetch it natively (names_a_url). */
  }
  switch (folders().judge(path, write)) {
    case Refusal::Private:
      return PRIVATE;
    case Refusal::Startup:
      return STARTUP;
    case Refusal::ToolOutput:
      return TOOL_OUTPUT;
    case Refusal::Network:
      return NETWORK;
    case Refusal::None:
      break;
  }
  return nullptr;
}

#ifdef WITH_USD
/** USD without the confined resolver (io/usd/usd_confined_resolver.hh `confined_resolver_refusal`): while any
 * confined region runs, as `usd_judge` judges, no USD entry point is a script's. */
static const char *usd_unavailable()
{
  return !usd_confined && active_regions.load(std::memory_order_relaxed) > 0 ? NATIVE : nullptr;
}
#endif

void install_pipeline_entries()
{
  if (gate_type_ready() != 0) {
    PyErr_Clear();
    return;
  }
#ifdef WITH_USD
  io::usd::confined_resolver_set_judge(usd_judge, usd_unavailable);
  usd_confined = io::usd::confined_resolver_active(); /* Made now, before any script can change the environment. */
#endif
  special_prepare();
  install_hooks();
  gate_loaded_modules();
}

/** \} */

}  // namespace blender::angelo_guard
