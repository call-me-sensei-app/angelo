/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The pipeline libraries' special cases (doc/angelo/script_confinement_pipeline.rst): files a library finds by
 * itself, from a search path, a document's XIncludes or a color configuration, judged where they are found.
 *
 * - A name searched in folders (OSL queries, OpenImageIO's caches, MaterialX's readers): the name, and the name in
 *   each folder, judged.
 * - MaterialX follows XIncludes with a reader function held in its read options: a confined call's options hold the
 *   gated reader, and the options' own attribute gives it to a confined script.
 * - OpenColorIO reads LUTs when a processor is made, found through a configuration's search paths: a confined
 *   call's configurations are copies that read every LUT through a judging IO proxy (OpenColorIO's own hook for
 *   archived configurations), so the path OpenColorIO resolved is judged. OpenImageIO's color operations use
 *   OpenColorIO without the proxy: their configuration is first made in OpenColorIO's Python module with the proxy,
 *   and every transform it holds (or the LUT named) is made once, judged.
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
  PyObject *module = PyDict_GetItemString(PyImport_GetModuleDict(), module_name); /* Borrowed. */
  return module;
}

/** A new argument tuple (or keyword dict) with `value` where `arg` is given (or as its keyword). */
static void put_argument(PyObject **args, PyObject **kwargs, Py_ssize_t shift, const Arg &arg, PyObject *value)
{
  if (arg.position >= 0 && arg.position + shift < PyTuple_GET_SIZE(*args)) {
    PyObject *copy = PyTuple_GetSlice(*args, 0, PyTuple_GET_SIZE(*args));
    if (copy != nullptr) {
      PyObject *old = PyTuple_GET_ITEM(copy, arg.position + shift);
      PyTuple_SET_ITEM(copy, arg.position + shift, Py_NewRef(value));
      Py_DECREF(old);
      Py_SETREF(*args, copy);
    }
    return;
  }
  if (arg.keyword == nullptr) {
    return;
  }
  PyObject *copy = *kwargs ? PyDict_Copy(*kwargs) : PyDict_New();
  if (copy != nullptr) {
    PyDict_SetItemString(copy, arg.keyword, value);
    Py_XSETREF(*kwargs, copy);
  }
}

/* -------------------------------------------------------------------- */
/** \name MaterialX
 * \{ */

static PyObject *xinclude_property = nullptr; /* The original `XmlReadOptions.readXIncludeFunction`. */

/** The gated `readFromXmlFileBase` (a new reference), or null: the gate object itself, never looked up on the module
 * (a script may rebind the module's name). */
static PyObject *gated_xml_reader()
{
  for (size_t i = 0; i < gate_count; i++) {
    const Gate &gate = gates[i];
    if (gate.owner == nullptr && strcmp(gate.name, "readFromXmlFileBase") == 0 && gate.object != nullptr) {
      return Py_NewRef(gate.object);
    }
  }
  return nullptr;
}

/** A reader the agent's own code may keep: its own function (confined) or the gate. */
static bool kept_reader(PyObject *reader)
{
  if (reader == nullptr || reader == Py_None) {
    return false;
  }
  if (PyMethod_Check(reader)) {
    reader = PyMethod_GET_FUNCTION(reader);
  }
  return PyFunction_Check(reader) || Py_TYPE(reader) == &gate_type;
}

static PyObject *xinclude_get(PyObject * /*self*/, PyObject *options)
{
  descrgetfunc get = Py_TYPE(xinclude_property)->tp_descr_get;
  PyObject *reader = get(xinclude_property, options, (PyObject *)Py_TYPE(options));
  if (reader != nullptr && confined_now() && !kept_reader(reader)) {
    /* MaterialX's native reader would read any file: a confined script gets the gated one, or nothing. */
    PyObject *gated = gated_xml_reader();
    Py_SETREF(reader, gated);
    if (reader == nullptr) {
      refuse(NATIVE, "readXIncludeFunction");
    }
  }
  return reader;
}

static PyObject *xinclude_set(PyObject * /*self*/, PyObject *args)
{
  PyObject *options, *value;
  if (!PyArg_ParseTuple(args, "OO", &options, &value)) {
    return nullptr;
  }
  descrsetfunc set = Py_TYPE(xinclude_property)->tp_descr_set;
  if (set(xinclude_property, options, value) != 0) {
    return nullptr;
  }
  Py_RETURN_NONE;
}

static PyMethodDef xinclude_get_definition = {"readXIncludeFunction", xinclude_get, METH_O, nullptr};
static PyMethodDef xinclude_set_definition = {"readXIncludeFunction", xinclude_set, METH_VARARGS, nullptr};

static void install_xinclude_property(PyObject *module)
{
  PyObject *type = PyObject_GetAttrString(module, "XmlReadOptions");
  PyObject *names = type && PyType_Check(type) ? ((PyTypeObject *)type)->tp_dict : nullptr;
  PyObject *raw = names ? PyDict_GetItemString(names, "readXIncludeFunction") : nullptr;
  if (raw != nullptr && xinclude_property == nullptr && Py_TYPE(raw)->tp_descr_get && Py_TYPE(raw)->tp_descr_set) {
    PyObject *getter = PyCFunction_New(&xinclude_get_definition, nullptr);
    PyObject *setter = PyCFunction_New(&xinclude_set_definition, nullptr);
    PyObject *property = getter && setter ? PyObject_CallFunctionObjArgs((PyObject *)&PyProperty_Type, getter,
                                                                         setter, nullptr) :
                                            nullptr;
    if (property != nullptr) {
      xinclude_property = Py_NewRef(raw);
      PyObject_SetAttrString(type, "readXIncludeFunction", property);
    }
    Py_XDECREF(property);
    Py_XDECREF(getter);
    Py_XDECREF(setter);
  }
  Py_XDECREF(type);
  PyErr_Clear();
}

/** The read options a confined MaterialX read runs with: XIncludes through the gated reader. */
static int gate_xincludes(const Gate &gate, PyObject **args, PyObject **kwargs)
{
  const Arg &arg = gate.args[2];
  PyObject *options = gate_argument(*args, *kwargs, 0, arg);
  PyObject *module = loaded("MaterialX.PyMaterialXFormat");
  PyObject *reader = gated_xml_reader();
  if (module == nullptr || reader == nullptr || xinclude_property == nullptr) {
    Py_XDECREF(reader);
    return refuse(NATIVE, gate.name); /* No gated reader to give MaterialX: fail closed. */
  }
  PyObject *made = nullptr;
  if (options == nullptr || options == Py_None) {
    made = PyObject_CallMethod(module, "XmlReadOptions", nullptr);
    options = made;
  }
  int result = -1;
  if (options != nullptr) {
    descrgetfunc get = Py_TYPE(xinclude_property)->tp_descr_get;
    PyObject *current = get(xinclude_property, options, (PyObject *)Py_TYPE(options));
    if (current != nullptr && !kept_reader(current)) {
      Py_TYPE(xinclude_property)->tp_descr_set(xinclude_property, options, reader);
    }
    Py_XDECREF(current);
    if (!PyErr_Occurred()) {
      put_argument(args, kwargs, 0, arg, options);
      result = 0;
    }
  }
  Py_XDECREF(made);
  Py_DECREF(reader);
  return result;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name OpenImageIO
 * \{ */

/** `attribute(name, value)` or `attribute(name, type, value)`: a search path names folders read; a plugin search path
 * loads native code from its folders. */
static int judge_attribute(const Gate &gate, PyObject *args, Py_ssize_t shift)
{
  const Py_ssize_t count = PyTuple_GET_SIZE(args) - shift;
  if (count < 2) {
    return 0;
  }
  const std::string name = text_of(PyTuple_GET_ITEM(args, shift));
  PyObject *value = PyTuple_GET_ITEM(args, PyTuple_GET_SIZE(args) - 1);
  if (name.find("plugin_searchpath") != std::string::npos) {
    return folder_list(value).empty() ? 0 : refuse(NATIVE, gate.name);
  }
  if (name.size() >= 10 && name.compare(name.size() - 10, 10, "searchpath") == 0) {
    for (const std::string &folder : folder_list(value)) {
      if (judge_text(folder, false, gate.name) != 0) {
        return -1;
      }
    }
  }
  return 0;
}

/** The search path of an ImageCache or TextureSystem (`holder`), or of the shared ImageCache. */
static std::vector<std::string> cache_folders(PyObject *holder)
{
  PyObject *made = nullptr;
  if (holder == nullptr) {
    PyObject *module = loaded("OpenImageIO.OpenImageIO");
    made = module ? PyObject_CallMethod(module, "ImageCache", "O", Py_True) : nullptr;
    holder = made;
  }
  PyObject *value = holder ? PyObject_CallMethod(holder, "getattribute", "s", "searchpath") : nullptr;
  std::vector<std::string> folders = folder_list(value);
  Py_XDECREF(value);
  Py_XDECREF(made);
  PyErr_Clear();
  return folders;
}

/** \} */

static int judge_named(const Gate &gate, PyObject *args, PyObject *kwargs, const std::vector<std::string> &folders)
{
  const Py_ssize_t shift = gate.kind == Kind::Function || gate.kind == Kind::Static ? 0 : 1;
  const std::string name = path_text(gate_argument(args, kwargs, shift, gate.args[0]));
  return judge_search(name, folders, gate.name);
}

int special_before(Gate &gate, PyObject **args, PyObject **kwargs)
{
  const Py_ssize_t shift = gate.kind == Kind::Function || gate.kind == Kind::Static ? 0 : 1;
  PyObject *self = shift ? PyTuple_GET_ITEM(*args, 0) : nullptr;
  switch (gate.special) {
    case Special::None:
      return 0;
    case Special::Refuse:
      return refuse(NATIVE, gate.name);
    case Special::RefuseGlobal:
      return refuse(GUARD, gate.name);
    case Special::SearchName: {
      const std::vector<std::string> folders = folder_list(gate_argument(*args, *kwargs, shift, gate.args[1]));
      std::string name = path_text(gate_argument(*args, *kwargs, shift, gate.args[0]));
      if (judge_search(name, folders, gate.name) != 0) {
        return -1;
      }
      return name.empty() || (name.size() > 4 && name.compare(name.size() - 4, 4, ".oso") == 0) ?
                 0 :
                 judge_search(name + ".oso", folders, gate.name);
    }
    case Special::MxRead: {
      const std::vector<std::string> search = folder_list(gate_argument(*args, *kwargs, 0, gate.args[1]));
      PyObject *files = gate_argument(*args, *kwargs, 0, gate.args[0]);
      std::vector<std::string> names = gate.args[0].role == Role::Folders ? folder_list(files) :
                                                                             std::vector<std::string>{path_text(files)};
      for (const std::string &name : names) {
        if (judge_search(name, search, gate.name) != 0) {
          return -1;
        }
      }
      return gate_xincludes(gate, args, kwargs);
    }
    case Special::MxXInclude:
      return gate_xincludes(gate, args, kwargs);
    case Special::MxSelf:
      return judge_text(path_text(self), gate.args[0].role == Role::Write, gate.name);
    case Special::OiioAttribute:
    case Special::OiioCacheAttribute:
      return judge_attribute(gate, *args, shift);
    case Special::OiioCacheName:
      return judge_named(gate, *args, *kwargs, cache_folders(self));
    case Special::OiioSharedName:
      return judge_named(gate, *args, *kwargs, cache_folders(nullptr));
    case Special::OiioColorConfig: {
      std::string file = path_text(gate_argument(*args, *kwargs, shift, gate.args[0]));
      if (gate.kind != Kind::Init) {
        /* A color operation: its `colorconfig` (a ColorConfig, judged when made) or the default. */
        PyObject *given = *kwargs ? PyDict_GetItemString(*kwargs, "colorconfig") : nullptr;
        if (given != nullptr && given != Py_None) {
          return 0;
        }
      }
      return judge_color_config(gate, file, nullptr);
    }
    case Special::OiioLut: {
      const std::string lut = path_text(gate_argument(*args, *kwargs, shift, gate.args[0]));
      PyObject *given = *kwargs ? PyDict_GetItemString(*kwargs, "colorconfig") : nullptr;
      PyObject *name = given && given != Py_None ? PyObject_CallMethod(given, "configname", nullptr) : nullptr;
      const std::string config = text_of(name);
      Py_XDECREF(name);
      PyErr_Clear();
      return lut.empty() ? 0 : judge_color_config(gate, config, lut.c_str());
    }
    case Special::OcioEnv: {
      const std::string config = environment_config();
      return builtin_config(config) ? 0 : judge_text(config, false, gate.name);
    }
    case Special::OcioProcessor:
    case Special::OcioSetCurrent:
      return judge_configs(gate, args, kwargs);
  }
  return 0;
}

void special_install(PyObject *module, const char *module_name)
{
  if (strcmp(module_name, "MaterialX.PyMaterialXFormat") == 0) {
    install_xinclude_property(module);
  }
}

}  // namespace blender::angelo_guard
