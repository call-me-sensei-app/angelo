/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Read the progress of, and stop, Blender's own window-manager jobs (renders,
 * bakes and background exports) that the Angelo job contract started, and
 * redraw job listeners when a job's state changes. Python has
 * `bpy.app.is_job_running` but no progress or stop for these job types.
 *
 * Only a closed table of job types is reachable, always owned by a Scene (each
 * operator registers its job with the scene as owner). Stopping asks the job to
 * finish at its next break check, exactly as Escape or the status bar's cancel
 * button do: nothing is freed here, so the job's own end callbacks still run on
 * the main thread. Cycles' texture bake checks only `G.is_break`; the others
 * check their stop flag as well.
 *
 * `scripts_auto_execute` reports whether this document may run Python it carries
 * (`G_FLAG_SCRIPT_AUTOEXEC`). Without it, Blender evaluates a scripted driver only
 * when its compiled expression passes the restricted check in `bpy_driver.cc`, so
 * render admission needs the flag to know whether a driver's Python would run.
 * The preference, `--enable/--disable-autoexec` and a file's trusted reload all set
 * it; Python has no other way to read it.
 *
 * `msgbus_subscribers` lists the `bpy.msgbus` subscribers that writing given settings
 * would notify. A look's or strip's own temporary writes publish to the message bus, and
 * Python can subscribe to a setting but never list who did. Only subscribers made by
 * `bpy.msgbus.subscribe_rna` are listed (Blender's own C++ ones are not Python), with the
 * function they call, so the add-on decides which code ships with the application.
 */

#include <Python.h>

#include "BLI_string.h"
#include "BLI_threads.h"
#include "BLI_utildefines.h"
#include "BLI_vector.hh"

#include "BKE_global.hh"
#include "BKE_main.hh"
#include "BKE_wm_runtime.hh"

#include "DNA_ID.h"
#include "DNA_scene_types.h"
#include "DNA_windowmanager_types.h"

#include "RNA_access.hh"

#include "WM_api.hh"
#include "WM_message.hh"
#include "WM_types.hh"

#include "bpy_angelo_jobs.hh"
#include "bpy_msgbus.hh"
#include "bpy_rna.hh"

namespace blender {

struct AngeloJobType {
  const char *name;
  eWM_JobType type;
  /** The job checks `G.is_break` only, never its own stop flag. */
  bool global_break;
};

static const AngeloJobType job_types[] = {
    {"RENDER", WM_JOB_TYPE_RENDER, false},
    {"OBJECT_BAKE", WM_JOB_TYPE_OBJECT_BAKE, true},
    {"POINTCACHE", WM_JOB_TYPE_POINTCACHE, false},
    {"BAKE_GEOMETRY_NODES", WM_JOB_TYPE_BAKE_GEOMETRY_NODES, false},
    {"USD_EXPORT", WM_JOB_TYPE_USD_EXPORT, false},
    {"ALEMBIC_EXPORT", WM_JOB_TYPE_ALEMBIC_EXPORT, false},
};

static bool require_main_thread()
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "Job status requires Blender's main thread");
    return false;
  }
  return true;
}

/** Parse `(scene, job_type)`; false with a Python error set when either is invalid. */
static bool parse_job(PyObject *args,
                      const char *function,
                      Scene **r_scene,
                      const AngeloJobType **r_type,
                      wmWindowManager **r_wm)
{
  PyObject *scene_object = nullptr;
  const char *name = nullptr;
  char format[64];
  SNPRINTF(format, "Os:%s", function);
  if (!PyArg_ParseTuple(args, format, &scene_object, &name)) {
    return false;
  }
  ID *id = nullptr;
  if (!BPy_StructRNA_Check(scene_object) ||
      pyrna_struct_validity_check(reinterpret_cast<BPy_StructRNA *>(scene_object)) == -1 ||
      !pyrna_id_FromPyObject(scene_object, &id) || id == nullptr || GS(id->name) != ID_SCE)
  {
    if (!PyErr_Occurred()) {
      PyErr_SetString(PyExc_TypeError, "Expected a live Blender Scene");
    }
    return false;
  }
  *r_type = nullptr;
  for (const AngeloJobType &item : job_types) {
    if (STREQ(item.name, name)) {
      *r_type = &item;
      break;
    }
  }
  if (*r_type == nullptr) {
    PyErr_Format(PyExc_ValueError, "Unsupported job type '%s'", name);
    return false;
  }
  *r_wm = static_cast<wmWindowManager *>(G_MAIN->wm.first);
  if (*r_wm == nullptr) {
    PyErr_SetString(PyExc_RuntimeError, "No window manager");
    return false;
  }
  *r_scene = reinterpret_cast<Scene *>(id);
  return true;
}

static PyObject *jobs_progress(PyObject * /*self*/, PyObject *args)
{
  Scene *scene;
  const AngeloJobType *type;
  wmWindowManager *wm;
  if (!require_main_thread() || !parse_job(args, "progress", &scene, &type, &wm)) {
    return nullptr;
  }
  if (!WM_jobs_test(wm, scene, type->type)) {
    Py_RETURN_NONE;
  }
  return PyFloat_FromDouble(double(WM_jobs_progress_type(wm, scene, type->type)));
}

static PyObject *jobs_stop(PyObject * /*self*/, PyObject *args)
{
  Scene *scene;
  const AngeloJobType *type;
  wmWindowManager *wm;
  if (!require_main_thread() || !parse_job(args, "stop", &scene, &type, &wm)) {
    return nullptr;
  }
  if (!WM_jobs_test(wm, scene, type->type)) {
    Py_RETURN_FALSE;
  }
  WM_jobs_stop_type(wm, scene, type->type);
  if (type->global_break) {
    G.is_break = true;
  }
  Py_RETURN_TRUE;
}

static PyObject *jobs_notify(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  /* The notifier Blender's own jobs send on progress: the status bar (a global area that
   * `Screen.areas` does not list) redraws its job item from it. */
  WM_main_add_notifier(NC_WM | ND_JOB, nullptr);
  Py_RETURN_NONE;
}

static PyObject *jobs_scripts_auto_execute(PyObject * /*self*/, PyObject * /*args*/)
{
  return PyBool_FromLong((G.f & G_FLAG_SCRIPT_AUTOEXEC) != 0);
}

/** A setting a view writes: its struct, and the property, or any property of it (null name). */
struct WrittenKey {
  const StructRNA *type;
  const PropertyRNA *prop;
  bool any_property;
};

/**
 * Parse `[(struct identifier, property identifier or None), ...]`, leaving out the structs that
 * are not registered.
 */
static bool parse_written_keys(PyObject *keys, Vector<WrittenKey> &r_keys)
{
  PyObject *items = PySequence_Fast(
      keys, "msgbus_subscribers: expected a sequence of (struct, property)");
  if (items == nullptr) {
    return false;
  }
  for (Py_ssize_t index = 0; index < PySequence_Fast_GET_SIZE(items); index++) {
    const char *struct_name = nullptr;
    const char *property_name = nullptr;
    if (!PyArg_ParseTuple(PySequence_Fast_GET_ITEM(items, index),
                          "sz:msgbus_subscribers",
                          &struct_name,
                          &property_name))
    {
      Py_DECREF(items);
      return false;
    }
    StructRNA *type = RNA_struct_find(struct_name);
    if (type == nullptr) {
      continue; /* Nothing can subscribe to a struct that is not registered. */
    }
    /* An unknown property still matches the subscribers to every property of its struct. */
    PropertyRNA *prop = property_name ? RNA_struct_type_find_property(type, property_name) :
                                        nullptr;
    r_keys.append({type, prop, property_name == nullptr});
  }
  Py_DECREF(items);
  return true;
}

static PyObject *jobs_msgbus_subscribers(PyObject * /*self*/, PyObject *keys)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  Vector<WrittenKey> written;
  if (!parse_written_keys(keys, written)) {
    return nullptr;
  }
  PyObject *found = PyList_New(0);
  wmWindowManager *wm = G_MAIN ? static_cast<wmWindowManager *>(G_MAIN->wm.first) : nullptr;
  if (found == nullptr || wm == nullptr || wm->runtime == nullptr ||
      wm->runtime->message_bus == nullptr)
  {
    return found;
  }
  bool failed = false;
  /* A write publishes to the subscribers of that property of that struct, of every property of
   * it, of one instance or of the type (`WM_msg_publish_rna_params`). Any instance counts here. */
  WM_msgbus_foreach_rna_subscriber(
      wm->runtime->message_bus,
      [&](const wmMsgParams_RNA &key, const wmMsgSubscribeValue &subscriber) {
        PyObject *function = failed ? nullptr : BPY_msgbus_subscriber_function(&subscriber);
        if (function == nullptr) {
          return;
        }
        for (const WrittenKey &item : written) {
          if (key.ptr.type != item.type ||
              !(item.any_property || key.prop == nullptr || key.prop == item.prop))
          {
            continue;
          }
          PyObject *entry = Py_BuildValue("(szO)",
                                          RNA_struct_identifier(key.ptr.type),
                                          key.prop ? RNA_property_identifier(key.prop) : nullptr,
                                          function);
          failed = entry == nullptr || PyList_Append(found, entry) == -1;
          Py_XDECREF(entry);
          return;
        }
      });
  if (failed) {
    Py_DECREF(found);
    return nullptr;
  }
  return found;
}

static PyMethodDef methods[] = {
    {"progress",
     jobs_progress,
     METH_VARARGS,
     "progress(scene, job_type): the running job's progress from 0 to 1, or None when no such "
     "job runs for this scene."},
    {"stop",
     jobs_stop,
     METH_VARARGS,
     "stop(scene, job_type): ask the running job to stop at its next break check. True when a "
     "job was asked; the job ends, and runs its own end callbacks, on a later main-loop tick."},
    {"notify",
     jobs_notify,
     METH_NOARGS,
     "notify(): tell job listeners (the status bar) that a job's state changed, so they redraw."},
    {"scripts_auto_execute",
     jobs_scripts_auto_execute,
     METH_NOARGS,
     "scripts_auto_execute(): whether this document may run its Python (scripted drivers in "
     "full, text blocks); False when auto-execution is off for it."},
    {"msgbus_subscribers",
     jobs_msgbus_subscribers,
     METH_O,
     "msgbus_subscribers(keys): the bpy.msgbus subscribers that writing the given settings would "
     "notify, as (struct, property or None, function) for each; keys are (struct identifier, "
     "property identifier or None for any property) pairs."},
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_bpy._angelo_jobs",
    nullptr,
    0,
    methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

PyObject *BPY_angelo_jobs_module()
{
  PyObject *mod = PyModule_Create(&module);
  if (mod == nullptr) {
    return nullptr;
  }
  PyObject *names = PyTuple_New(ARRAY_SIZE(job_types));
  for (int index = 0; index < int(ARRAY_SIZE(job_types)); index++) {
    PyTuple_SET_ITEM(names, index, PyUnicode_FromString(job_types[index].name));
  }
  PyModule_AddObject(mod, "JOB_TYPES", names);
  return mod;
}

}  // namespace blender
