/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * OpenColorIO under the confinement (doc/angelo/script_confinement_pipeline.rst): LUTs are read when a processor is
 * made, found through a configuration's search paths and working folder. A confined call's configurations are copies
 * that read every LUT through a judging IO proxy (OpenColorIO's own hook for archived configurations), so the path
 * OpenColorIO resolved is judged before any byte is read. OpenImageIO's color operations use OpenColorIO without the
 * proxy: their configuration is first made in OpenColorIO's Python module with the proxy, and every transform it
 * holds (or the LUT named) is made once, judged.
 */

#include <Python.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <sys/stat.h>
#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_pipeline.hh"

namespace blender::angelo_guard {

static PyObject *loaded(const char *module_name)
{
  return PyDict_GetItemString(PyImport_GetModuleDict(), module_name); /* Borrowed. */
}

/* -------------------------------------------------------------------- */
/** \name OpenColorIO: LUTs read through a judging IO proxy
 * \{ */

static PyObject *proxy_class = nullptr;

/* The judging reads, in C; the proxy's methods (Python, so pybind11 takes them as overrides) call these. */
static PyObject *proxy_lut(PyObject * /*self*/, PyObject *path_object)
{
  const std::string path = path_text(path_object);
  if (confined_now() && judge_text(path, false, "an OpenColorIO LUT") != 0) {
    return nullptr;
  }
  FILE *file = fopen(path.c_str(), "rb");
  if (file == nullptr) {
    return PyErr_Format(PyExc_FileNotFoundError, "The LUT '%s' could not be read.", path.c_str());
  }
  std::vector<char> data;
  char buffer[65536];
  size_t count;
  while ((count = fread(buffer, 1, sizeof(buffer), file)) > 0) {
    data.insert(data.end(), buffer, buffer + count);
  }
  fclose(file);
  return PyByteArray_FromStringAndSize(data.data(), Py_ssize_t(data.size()));
}

static PyObject *proxy_hash(PyObject * /*self*/, PyObject *path_object)
{
  const std::string path = path_text(path_object);
  if (confined_now() && judge_text(path, false, "an OpenColorIO LUT") != 0) {
    return nullptr;
  }
  struct stat info;
  if (path.empty() || stat(path.c_str(), &info) != 0) {
    return PyUnicode_FromString(""); /* Not there: OpenColorIO tries its next search folder. */
  }
  return PyUnicode_FromFormat("%s:%lld:%lld", path.c_str(), (long long)info.st_size, (long long)info.st_mtime);
}

static PyMethodDef proxy_lut_definition = {"judged_lut", proxy_lut, METH_O, nullptr};
static PyMethodDef proxy_hash_definition = {"judged_hash", proxy_hash, METH_O, nullptr};
static PyObject *proxy_maker = nullptr; /* `make(base, lut, hash)` -> the proxy class (compiled at install). */

/** The judging proxy (a new reference), or null with an exception. One instance for the process, never freed: a
 * configuration holds only its C++ side, and pybind11 finds the Python methods through the live Python object. */
static PyObject *judging_proxy(PyObject *ocio)
{
  static PyObject *instance = nullptr;
  if (instance != nullptr) {
    return Py_NewRef(instance);
  }
  if (proxy_class == nullptr) {
    PyObject *base = PyObject_GetAttrString(ocio, "PyConfigIOProxy");
    PyObject *lut = PyCFunction_New(&proxy_lut_definition, nullptr);
    PyObject *hash = PyCFunction_New(&proxy_hash_definition, nullptr);
    proxy_class = base && lut && hash && proxy_maker ?
                      PyObject_CallFunctionObjArgs(proxy_maker, base, lut, hash, nullptr) :
                      nullptr;
    Py_XDECREF(hash);
    Py_XDECREF(lut);
    Py_XDECREF(base);
    if (proxy_class == nullptr) {
      if (!PyErr_Occurred()) {
        refuse(NATIVE, "an OpenColorIO configuration");
      }
      return nullptr;
    }
  }
  instance = PyObject_CallNoArgs(proxy_class);
  return Py_XNewRef(instance);
}

/** A copy of `config` whose LUTs are read through the judging proxy (a new reference), or null. A configuration
 * read from an archive keeps its own proxy (the archive was judged when it was read). */
static PyObject *judged_config(PyObject *ocio, PyObject *config)
{
  PyObject *memo = PyDict_New();
  PyObject *copy = memo ? PyObject_CallMethod(config, "__deepcopy__", "O", memo) : nullptr;
  Py_XDECREF(memo);
  PyObject *current = copy ? PyObject_CallMethod(copy, "getConfigIOProxy", nullptr) : nullptr;
  if (current != nullptr && current == Py_None) {
    PyObject *proxy = judging_proxy(ocio);
    PyObject *result = proxy ? PyObject_CallMethod(copy, "setConfigIOProxy", "O", proxy) : nullptr;
    if (result == nullptr) {
      Py_CLEAR(copy);
    }
    Py_XDECREF(result);
    Py_XDECREF(proxy);
  }
  Py_XDECREF(current);
  return copy;
}

/** Every OpenColorIO `Config` among the arguments (and `self`), replaced by its judged copy; a `Baker` by a copy
 * that bakes from one. */
int judge_configs(const Gate &gate, PyObject **args, PyObject **kwargs)
{
  PyObject *ocio = loaded("PyOpenColorIO.PyOpenColorIO");
  PyObject *config_type = ocio ? PyObject_GetAttrString(ocio, "Config") : nullptr;
  PyObject *baker_type = ocio ? PyObject_GetAttrString(ocio, "Baker") : nullptr;
  if (config_type == nullptr || baker_type == nullptr) {
    Py_XDECREF(config_type);
    Py_XDECREF(baker_type);
    return refuse(NATIVE, gate.name);
  }
  int result = 0;
  PyObject *items = PySequence_List(*args);
  for (Py_ssize_t i = 0; items && result == 0 && i < PyList_GET_SIZE(items); i++) {
    PyObject *item = PyList_GET_ITEM(items, i);
    PyObject *replacement = nullptr;
    if (PyObject_IsInstance(item, config_type) == 1) {
      replacement = judged_config(ocio, item);
    }
    else if (PyObject_IsInstance(item, baker_type) == 1) {
      PyObject *config = PyObject_CallMethod(item, "getConfig", nullptr);
      if (config == Py_None) {
        Py_DECREF(config);
        continue; /* No configuration: it reads no LUT. */
      }
      PyObject *judged = config ? judged_config(ocio, config) : nullptr;
      PyObject *memo = judged ? PyDict_New() : nullptr;
      replacement = memo ? PyObject_CallMethod(item, "__deepcopy__", "O", memo) : nullptr;
      Py_XDECREF(memo);
      PyObject *set = replacement ? PyObject_CallMethod(replacement, "setConfig", "O", judged) : nullptr;
      if (set == nullptr) {
        Py_CLEAR(replacement);
      }
      Py_XDECREF(set);
      Py_XDECREF(judged);
      Py_XDECREF(config);
    }
    else {
      continue;
    }
    if (replacement == nullptr) {
      result = -1;
    }
    else {
      PyList_SetItem(items, i, replacement);
    }
  }
  PyObject *key, *value;
  Py_ssize_t position = 0;
  PyObject *keywords = *kwargs ? PyDict_Copy(*kwargs) : nullptr;
  while (result == 0 && keywords && PyDict_Next(*kwargs, &position, &key, &value)) {
    if (PyObject_IsInstance(value, config_type) == 1) {
      PyObject *judged = judged_config(ocio, value);
      result = judged ? PyDict_SetItem(keywords, key, judged) : -1;
      Py_XDECREF(judged);
    }
  }
  if (result == 0 && items != nullptr) {
    Py_SETREF(*args, PyList_AsTuple(items));
    if (keywords != nullptr) {
      Py_SETREF(*kwargs, Py_NewRef(keywords));
    }
  }
  Py_XDECREF(keywords);
  Py_XDECREF(items);
  Py_DECREF(config_type);
  Py_DECREF(baker_type);
  return result == 0 && *args != nullptr ? 0 : -1;
}

/** After a confined OpenColorIO call failed: OpenColorIO remembers a LUT it could not locate, so a later call fails
 * without asking the proxy again. A "missing file" error that names a path the confinement refuses is the refusal. */
void ocio_missing_refusal(const Gate &gate)
{
  PyObject *ocio = loaded("PyOpenColorIO.PyOpenColorIO");
  PyObject *missing = ocio ? PyObject_GetAttrString(ocio, "ExceptionMissingFile") : nullptr;
  if (missing == nullptr || !PyErr_ExceptionMatches(missing)) {
    Py_XDECREF(missing);
    PyErr_Clear();
    return;
  }
  Py_DECREF(missing);
  PyObject *raised = PyErr_GetRaisedException();
  PyObject *text_object = PyObject_Str(raised);
  const std::string text = text_object ? text_of(text_object) : std::string();
  Py_XDECREF(text_object);
  PyErr_Clear();
  /* OpenColorIO quotes every path it tried. */
  for (size_t open = text.find('\''); open != std::string::npos; open = text.find('\'', open + 1)) {
    const size_t close = text.find('\'', open + 1);
    if (close == std::string::npos) {
      break;
    }
    if (judge_text(text.substr(open + 1, close - open - 1), false, gate.name) != 0) {
      Py_DECREF(raised);
      return; /* The refusal is the exception now. */
    }
    open = close;
  }
  PyErr_SetRaisedException(raised);
}

/** `$OCIO`, the configuration OpenColorIO and OpenImageIO read when none is given. */
std::string environment_config()
{
  const char *value = getenv("OCIO");
  return value ? value : "";
}

bool builtin_config(const std::string &config)
{
  return config.empty() || config.rfind("ocio://", 0) == 0;
}

/** Makes every transform a configuration holds (or a FileTransform of one LUT) once: compiled at install, before
 * any script, so it is the confinement's own code. */
static PyObject *audit_function = nullptr;

void special_prepare()
{
  if (audit_function != nullptr) {
    return;
  }
  static const char *audit =
      "def audit(ocio, config, lut):\n"
      "    def made(transform, *direction):\n"
      "        if transform is not None:\n"
      "            config.getProcessor(transform, *direction)\n"
      "    if lut is not None:\n"
      "        return made(ocio.FileTransform(src=lut), ocio.TRANSFORM_DIR_FORWARD)\n"
      "    for space in config.getColorSpaces():\n"
      "        for direction in (ocio.COLORSPACE_DIR_TO_REFERENCE, ocio.COLORSPACE_DIR_FROM_REFERENCE):\n"
      "            made(space.getTransform(direction), ocio.TRANSFORM_DIR_FORWARD)\n"
      "    for look in config.getLooks():\n"
      "        made(look.getTransform(), ocio.TRANSFORM_DIR_FORWARD)\n"
      "        made(look.getInverseTransform(), ocio.TRANSFORM_DIR_FORWARD)\n"
      "    for view in config.getViewTransforms():\n"
      "        for direction in (ocio.VIEWTRANSFORM_DIR_TO_REFERENCE, ocio.VIEWTRANSFORM_DIR_FROM_REFERENCE):\n"
      "            made(view.getTransform(direction), ocio.TRANSFORM_DIR_FORWARD)\n"
      "    for named in config.getNamedTransforms():\n"
      "        for direction in (ocio.TRANSFORM_DIR_FORWARD, ocio.TRANSFORM_DIR_INVERSE):\n"
      "            made(named.getTransform(direction), ocio.TRANSFORM_DIR_FORWARD)\n";
  PyObject *globals = PyDict_New();
  PyObject *code = globals ? Py_CompileString(audit, "<angelo-guard-ocio-audit>", Py_file_input) : nullptr;
  PyObject *defined = code ? PyEval_EvalCode(code, globals, globals) : nullptr;
  PyObject *function = defined ? PyDict_GetItemString(globals, "audit") : nullptr;
  audit_function = Py_XNewRef(function);
  Py_XDECREF(defined);
  Py_XDECREF(code);
  Py_XDECREF(globals);
  PyErr_Clear();
  /* The judging IO proxy's class, made from OpenColorIO's base when first needed. Its methods are Python functions:
   * pybind11 takes a C function found on the class for the base's own method, not an override. */
  static const char *maker =
      "def make(base, judged_lut, judged_hash):\n"
      "    class AngeloJudgedLutReader(base):\n"
      "        def __init__(self):\n"
      "            base.__init__(self)\n"
      "        def getLutData(self, path):\n"
      "            return judged_lut(path)\n"
      "        def getFastLutFileHash(self, path):\n"
      "            return judged_hash(path)\n"
      "        def getConfigData(self):\n"
      "            return ''\n"
      "    return AngeloJudgedLutReader\n";
  globals = PyDict_New();
  code = globals ? Py_CompileString(maker, "<angelo-guard-ocio-proxy>", Py_file_input) : nullptr;
  defined = code ? PyEval_EvalCode(code, globals, globals) : nullptr;
  function = defined ? PyDict_GetItemString(globals, "make") : nullptr;
  proxy_maker = Py_XNewRef(function);
  Py_XDECREF(defined);
  Py_XDECREF(code);
  Py_XDECREF(globals);
  PyErr_Clear();
}

/** OpenImageIO's color configuration `path` (a file, a built-in URI or empty), made in OpenColorIO's own module with
 * the judging proxy, with every transform it holds made once (`name`: only a FileTransform of that LUT): any LUT
 * it would read from a refused path is refused here. */
int judge_color_config(const Gate &gate, std::string path, const char *lut)
{
  if (path.empty()) {
    path = environment_config();
  }
  if (!builtin_config(path) && judge_text(path, false, gate.name) != 0) {
    return -1;
  }
  PyObject *ocio = PyImport_ImportModule("PyOpenColorIO.PyOpenColorIO");
  if (ocio == nullptr) {
    PyErr_Clear();
    return refuse(NATIVE, gate.name); /* Nothing to judge its LUTs with: fail closed. */
  }
  PyObject *config_type = PyObject_GetAttrString(ocio, "Config");
  PyObject *config = nullptr;
  pending_refusal = nullptr;
  if (config_type != nullptr) {
    config = path.empty() ? PyObject_CallMethod(config_type, "CreateFromBuiltinConfig", "s", "default") :
                            PyObject_CallMethod(config_type, "CreateFromFile", "s", path.c_str());
  }
  if (config == nullptr) {
    /* A refusal stands; a configuration OpenColorIO can't read, OpenImageIO can't either. */
    const bool refused = PyErr_ExceptionMatches(script_confined_type) || pending_refusal != nullptr;
    PyErr_Clear();
    if (refused) {
      refuse(pending_refusal ? pending_refusal : PRIVATE, gate.name);
    }
    Py_XDECREF(config_type);
    Py_DECREF(ocio);
    return refused ? -1 : 0;
  }
  pending_refusal = nullptr;
  PyObject *judged = judged_config(ocio, config);
  int result = judged ? 0 : -1;
  if (result == 0) {
    PyObject *lut_object = lut ? PyUnicode_FromString(lut) : Py_NewRef(Py_None);
    PyObject *audited = audit_function && lut_object ?
                            PyObject_CallFunctionObjArgs(audit_function, ocio, judged, lut_object, nullptr) :
                            nullptr;
    if (audited == nullptr) {
      /* OpenColorIO wraps a refusal raised in the proxy in its own exception: the refusal recorded in C decides. */
      if (pending_refusal != nullptr) {
        PyErr_Clear();
        result = refuse(pending_refusal, gate.name);
      }
      else {
        PyErr_Clear(); /* A transform OpenColorIO can't make, OpenImageIO can't either. */
      }
    }
    Py_XDECREF(audited);
    Py_XDECREF(lut_object);
  }
  Py_XDECREF(judged);
  Py_DECREF(config);
  Py_XDECREF(config_type);
  Py_DECREF(ocio);
  return result;
}

/** \} */

}  // namespace blender::angelo_guard
