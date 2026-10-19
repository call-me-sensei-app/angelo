/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Save a picture as a render would, without writing the scene.
 *
 * `Image.save_render` takes its file format from the scene's render output, so a picture in
 * another format needed the scene's image format set for the save and put back. Those writes
 * notify the add-ons' `bpy.msgbus` subscribers and update the depsgraph, which runs their
 * depsgraph-update handlers. `save_copy` runs the same save (`BKE_image_save`, a copy) with a
 * format of its own: the scene's output format, then the given settings set on that copy through
 * RNA without an update, so nothing is published or tagged. With `display` the colours are the
 * scene's own view and display (what the render window shows), curves included. A `source` file
 * is loaded into an image outside `Main` (`BKE_image_load_nomain`) and freed afterwards, so no
 * data-block is added or removed either.
 */

#include <Python.h>

#include "MEM_guardedalloc.h"

#include "BLI_string.h"
#include "BLI_threads.h"

#include "BKE_colortools.hh"
#include "BKE_global.hh"
#include "BKE_image.hh"
#include "BKE_image_save.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_report.hh"

#include "DNA_ID.h"
#include "DNA_image_types.h"
#include "DNA_scene_types.h"

#include "RNA_access.hh"
#include "RNA_prototypes.hh"

#include "../generic/py_capi_utils.hh"

#include "bpy_angelo_picture.hh"
#include "bpy_capi_utils.hh"
#include "bpy_rna.hh"

namespace blender {

/** The data-block of a live `bpy` ID object of type \a code, or null with a Python error set. */
static ID *live_id(PyObject *object, const short code, const char *what)
{
  ID *id = nullptr;
  if (!BPy_StructRNA_Check(object) ||
      pyrna_struct_validity_check(reinterpret_cast<BPy_StructRNA *>(object)) == -1 ||
      !pyrna_id_FromPyObject(object, &id) || id == nullptr || GS(id->name) != code)
  {
    if (!PyErr_Occurred()) {
      PyErr_Format(PyExc_TypeError, "Expected a live Blender %s", what);
    }
    return nullptr;
  }
  return id;
}

/** Set `{name: value}` on \a format through RNA, without an update; false with a Python error. */
static bool set_format(ImageFormatData *format, PyObject *settings)
{
  PointerRNA ptr = RNA_pointer_create_discrete(nullptr, RNA_ImageFormatSettings, format);
  PyObject *key, *value;
  Py_ssize_t position = 0;
  while (PyDict_Next(settings, &position, &key, &value)) {
    const char *name = PyUnicode_Check(key) ? PyUnicode_AsUTF8(key) : nullptr;
    PropertyRNA *prop = name ? RNA_struct_find_property(&ptr, name) : nullptr;
    if (prop == nullptr) {
      PyErr_Format(PyExc_ValueError, "No image format setting %R", key);
      return false;
    }
    switch (RNA_property_type(prop)) {
      case PROP_ENUM: {
        const char *identifier = PyUnicode_Check(value) ? PyUnicode_AsUTF8(value) : nullptr;
        int enum_value;
        if (identifier == nullptr ||
            !RNA_property_enum_value(BPY_context_get(), &ptr, prop, identifier, &enum_value))
        {
          PyErr_Format(PyExc_ValueError, "%s cannot be %R", name, value);
          return false;
        }
        RNA_property_enum_set(&ptr, prop, enum_value);
        break;
      }
      case PROP_INT: {
        const long number = PyLong_AsLong(value);
        if (number == -1 && PyErr_Occurred()) {
          return false;
        }
        RNA_property_int_set(&ptr, prop, int(number));
        break;
      }
      case PROP_BOOLEAN: {
        const int flag = PyObject_IsTrue(value);
        if (flag == -1) {
          return false;
        }
        RNA_property_boolean_set(&ptr, prop, flag);
        break;
      }
      default:
        PyErr_Format(PyExc_ValueError, "%s is not a plain setting", name);
        return false;
    }
  }
  return true;
}

/** Raise the save's own report (or \a fallback) as a RuntimeError. */
static void raise_reports(ReportList *reports, const char *fallback)
{
  char *text = BKE_reports_string(reports, RPT_ERROR);
  PyErr_SetString(PyExc_RuntimeError, text && text[0] ? text : fallback);
  if (text) {
    MEM_delete(text);
  }
}

static PyObject *picture_save_copy(PyObject * /*self*/, PyObject *args, PyObject *kwds)
{
  static const char *keywords[] = {
      "scene", "filepath", "image", "source", "settings", "display", nullptr};
  PyObject *scene_object = nullptr, *image_object = Py_None, *settings = nullptr;
  const char *filepath = nullptr, *source = nullptr;
  bool display = false;
  if (!PyArg_ParseTupleAndKeywords(args,
                                   kwds,
                                   "Os|$OzO!O&:save_copy",
                                   const_cast<char **>(keywords),
                                   &scene_object,
                                   &filepath,
                                   &image_object,
                                   &source,
                                   &PyDict_Type,
                                   &settings,
                                   PyC_ParseBool,
                                   &display))
  {
    return nullptr;
  }
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "save_copy requires Blender's main thread");
    return nullptr;
  }
  if ((image_object == Py_None) == (source == nullptr)) {
    PyErr_SetString(PyExc_TypeError, "save_copy: give an image or a source file, not both");
    return nullptr;
  }
  Scene *scene = reinterpret_cast<Scene *>(live_id(scene_object, ID_SCE, "Scene"));
  if (scene == nullptr) {
    return nullptr;
  }
  Image *image = nullptr;
  if (source == nullptr) {
    image = reinterpret_cast<Image *>(live_id(image_object, ID_IM, "Image"));
    if (image == nullptr) {
      return nullptr;
    }
  }
  else if ((image = BKE_image_load_nomain(source)) == nullptr) {
    PyErr_Format(PyExc_RuntimeError, "Cannot read '%s'", source);
    return nullptr;
  }

  ImageSaveOptions opts;
  ReportList reports;
  BKE_reports_init(&reports, RPT_STORE);
  bool ok = BKE_image_save_options_init(&opts, G_MAIN, scene, image, nullptr, false, true);
  if (!ok) {
    PyErr_Format(
        PyExc_RuntimeError, "Image '%s' does not have any image data", image->id.name + 2);
  }
  else if (settings == nullptr || set_format(&opts.im_format, settings)) {
    if (display) {
      /* The scene's own view and display, curves included, whatever its output overrides; the
       * output colour space stays the save's, as #Image.save_render leaves it. */
      BKE_color_managed_display_settings_copy(&opts.im_format.display_settings,
                                              &scene->display_settings);
      BKE_color_managed_view_settings_free(&opts.im_format.view_settings);
      BKE_color_managed_view_settings_copy(&opts.im_format.view_settings, &scene->view_settings);
    }
    opts.save_copy = true;
    STRNCPY(opts.filepath, filepath);
    ok = BKE_image_save(&reports, G_MAIN, image, nullptr, &opts);
    if (!ok) {
      raise_reports(&reports, "The picture could not be saved");
    }
  }
  else {
    ok = false;
  }
  const bool multilayer = image->type == IMA_TYPE_MULTILAYER;
  BKE_image_save_options_free(&opts);
  BKE_reports_free(&reports);
  if (source != nullptr) {
    BKE_id_free(nullptr, image);
  }
  if (!ok) {
    return nullptr;
  }
  return Py_BuildValue("{s:O}", "multilayer", multilayer ? Py_True : Py_False);
}

static PyMethodDef methods[] = {
    {"save_copy",
     reinterpret_cast<PyCFunction>(reinterpret_cast<void *>(picture_save_copy)),
     METH_VARARGS | METH_KEYWORDS,
     "save_copy(scene, filepath, *, image=None, source=None, settings=None, display=False): save "
     "an image, or a file loaded outside the document, as Image.save_render would with the "
     "scene's output format and the given image format settings, without writing the scene. "
     "``display``: in the scene's own view and display. Returns {'multilayer': bool}."},
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_bpy._angelo_picture",
    nullptr,
    0,
    methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

PyObject *BPY_angelo_picture_module()
{
  return PyModule_Create(&module);
}

}  // namespace blender
