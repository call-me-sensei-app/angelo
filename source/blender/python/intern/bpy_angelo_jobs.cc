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
 * the main thread. Cycles' texture bake and Mantaflow's fluid bake check only
 * `G.is_break`; the others check their stop flag as well.
 *
 * `fluid_state` and `fluid_state_set` read and write a fluid domain's cache state (type,
 * baked flags, pause frames, directory) directly. Through RNA, setting the cache type frees
 * the cache, deleting the files in whatever directory the domain names at that moment
 * (`rna_Fluid_domain_data_reset`), and the baked flags and pause frames cannot be set at
 * all, so a fluid bake job could neither point a domain at its own folder safely nor put a
 * stopped bake's domain back as it was.
 *
 * `scripts_auto_execute` reports whether this document may run Python it carries
 * (`G_FLAG_SCRIPT_AUTOEXEC`). Without it, Blender evaluates a scripted driver only
 * when its compiled expression passes the restricted check in `bpy_driver.cc`, so
 * render admission needs the flag to know whether a driver's Python would run.
 * The preference, `--enable/--disable-autoexec` and a file's trusted reload all set
 * it; Python has no other way to read it.
 *
 * `deadline` sets the time budget of the render or bake job Angelo started (`BKE_angelo_job_deadline.hh`):
 * that job's break check reads it on the job's own thread, so the budget stops the job even when the main
 * thread is held up; `deadline_fired` then says the budget, not the job's own end, stopped it.
 * `render_stats` reads a scene's last render's memory (Cycles reports its peak while it renders), which
 * Python cannot reach.
 *
 * `desktop_*` post the operating system's own notification when a long job ends, and bounce
 * the Dock icon (`bpy_angelo_desktop.hh`).
 *
 * `msgbus_subscribers` lists the `bpy.msgbus` subscribers that writing given settings
 * would notify. A look's or strip's own temporary writes publish to the message bus, and
 * Python can subscribe to a setting but never list who did. Only subscribers made by
 * `bpy.msgbus.subscribe_rna` are listed (Blender's own C++ ones are not Python), with the
 * function they call, so the add-on decides which code ships with the application.
 */

#include <Python.h>

#include <cmath>

#include "BLI_string.h"
#include "BLI_threads.h"
#include "BLI_utildefines.h"
#include "BLI_vector.hh"

#include "BKE_angelo_job_deadline.hh"
#include "BKE_fluid.h"
#include "BKE_global.hh"
#include "BKE_modifier.hh"
#include "BKE_scene.hh"
#include "BKE_main.hh"
#include "BKE_wm_runtime.hh"

#include "DNA_ID.h"
#include "DNA_fluid_types.h"
#include "DNA_modifier_types.h"
#include "DNA_layer_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"
#include "DNA_windowmanager_types.h"

#include "DEG_depsgraph.hh"

#include "RE_pipeline.h"

#include "RNA_access.hh"

#include "WM_api.hh"
#include "WM_message.hh"
#include "WM_types.hh"

#include "bpy_angelo_desktop.hh"
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
    /* Mantaflow's bake checks `G.is_break` only (`physics_fluid.cc`, `fluid_bake_sequence`). */
    {"FLUID", WM_JOB_TYPE_OBJECT_SIM_FLUID, true},
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

static PyObject *jobs_deadline(PyObject * /*self*/, PyObject *args)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  if (PyTuple_GET_SIZE(args) == 1 && PyTuple_GET_ITEM(args, 0) == Py_None) {
    bke::angelo::job_deadline_set(0.0, 0, 0);
    Py_RETURN_NONE;
  }
  if (PyTuple_GET_SIZE(args) != 3) {
    PyErr_SetString(PyExc_TypeError, "deadline(scene, job_type, seconds) or deadline(None)");
    return nullptr;
  }
  PyObject *job_args = PyTuple_GetSlice(args, 0, 2);
  Scene *scene;
  const AngeloJobType *type;
  wmWindowManager *wm;
  const bool parsed = job_args != nullptr && parse_job(job_args, "deadline", &scene, &type, &wm);
  Py_XDECREF(job_args);
  if (!parsed) {
    return nullptr;
  }
  if (type->type != WM_JOB_TYPE_RENDER && type->type != WM_JOB_TYPE_OBJECT_BAKE) {
    PyErr_SetString(PyExc_ValueError, "deadline: only RENDER and OBJECT_BAKE jobs read a deadline");
    return nullptr;
  }
  if (PyTuple_GET_ITEM(args, 2) == Py_None) {
    /* That job's deadline only: another job's (a dropped manager's) stays until its own end. */
    bke::angelo::job_deadline_job_ended(int(type->type), scene->id.session_uid);
    Py_RETURN_NONE;
  }
  const double seconds = PyFloat_AsDouble(PyTuple_GET_ITEM(args, 2));
  if (seconds == -1.0 && PyErr_Occurred()) {
    return nullptr;
  }
  if (!std::isfinite(seconds) || seconds <= 0.0) {
    PyErr_SetString(PyExc_ValueError, "deadline: seconds must be a positive finite number");
    return nullptr;
  }
  bke::angelo::job_deadline_set(seconds, int(type->type), scene->id.session_uid);
  Py_RETURN_NONE;
}

static PyObject *jobs_deadline_fired(PyObject * /*self*/, PyObject * /*args*/)
{
  return PyBool_FromLong(bke::angelo::job_deadline_fired.load(std::memory_order_relaxed));
}

static PyObject *jobs_render_stats(PyObject * /*self*/, PyObject *arg)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  ID *id = nullptr;
  if (!pyrna_id_FromPyObject(arg, &id) || id == nullptr || GS(id->name) != ID_SCE) {
    PyErr_SetString(PyExc_TypeError, "render_stats: expected a Scene");
    return nullptr;
  }
  Render *re = RE_GetSceneRender(reinterpret_cast<Scene *>(id));
  if (re == nullptr) {
    Py_RETURN_NONE;
  }
  const RenderStats *stats = RE_GetStats(re);
  if (stats == nullptr) {
    Py_RETURN_NONE;
  }
  /* Megabytes, as the renderer reported them (Cycles: device memory in use and its peak). */
  return Py_BuildValue("{s:i,s:i}", "mem_used_mb", stats->mem_used, "mem_peak_mb", stats->mem_peak);
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

static PyObject *jobs_desktop_permission(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  return PyUnicode_FromString(angelo_desktop_permission());
}

static PyObject *jobs_desktop_request_permission(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  angelo_desktop_request_permission();
  Py_RETURN_NONE;
}

static PyObject *jobs_desktop_notify(PyObject * /*self*/, PyObject *args)
{
  const char *identifier = nullptr;
  const char *title = nullptr;
  const char *body = nullptr;
  if (!require_main_thread() || !PyArg_ParseTuple(args, "sss:desktop_notify", &identifier, &title, &body))
  {
    return nullptr;
  }
  return PyUnicode_FromString(angelo_desktop_notify(identifier, title, body));
}

static PyObject *jobs_desktop_attention(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  return PyUnicode_FromString(angelo_desktop_attention());
}

/** The fluid domain settings of a live Object, or null with a Python error set. */
static FluidModifierData *parse_fluid_domain(PyObject *object, Object **r_object)
{
  ID *id = nullptr;
  if (!BPy_StructRNA_Check(object) ||
      pyrna_struct_validity_check(reinterpret_cast<BPy_StructRNA *>(object)) == -1 ||
      !pyrna_id_FromPyObject(object, &id) || id == nullptr || GS(id->name) != ID_OB)
  {
    if (!PyErr_Occurred()) {
      PyErr_SetString(PyExc_TypeError, "Expected a live Blender Object");
    }
    return nullptr;
  }
  Object *ob = reinterpret_cast<Object *>(id);
  FluidModifierData *fmd = reinterpret_cast<FluidModifierData *>(
      BKE_modifiers_findby_type(ob, eModifierType_Fluid));
  if (fmd == nullptr || fmd->type != MOD_FLUID_TYPE_DOMAIN || fmd->domain == nullptr) {
    PyErr_SetString(PyExc_ValueError, "The object has no fluid domain");
    return nullptr;
  }
  *r_object = ob;
  return fmd;
}

static PyObject *jobs_fluid_state(PyObject * /*self*/, PyObject *object)
{
  Object *ob;
  if (!require_main_thread()) {
    return nullptr;
  }
  FluidModifierData *fmd = parse_fluid_domain(object, &ob);
  if (fmd == nullptr) {
    return nullptr;
  }
  const FluidDomainSettings *fds = fmd->domain;
  return Py_BuildValue("{s:i,s:i,s:(iiiii),s:s,s:s}",
                       "cache_type",
                       int(fds->cache_type),
                       "cache_flag",
                       int(fds->cache_flag),
                       "pause",
                       fds->cache_frame_pause_data,
                       fds->cache_frame_pause_noise,
                       fds->cache_frame_pause_mesh,
                       fds->cache_frame_pause_particles,
                       fds->cache_frame_pause_guide,
                       "directory",
                       fds->cache_directory,
                       "error",
                       fds->error);
}

static PyObject *jobs_fluid_state_set(PyObject * /*self*/, PyObject *args)
{
  PyObject *object = nullptr;
  int cache_type, cache_flag;
  int pause[5];
  const char *directory = nullptr;
  if (!require_main_thread() || !PyArg_ParseTuple(args,
                                                  "O(ii(iiiii)s):fluid_state_set",
                                                  &object,
                                                  &cache_type,
                                                  &cache_flag,
                                                  &pause[0],
                                                  &pause[1],
                                                  &pause[2],
                                                  &pause[3],
                                                  &pause[4],
                                                  &directory))
  {
    return nullptr;
  }
  Object *ob;
  FluidModifierData *fmd = parse_fluid_domain(object, &ob);
  if (fmd == nullptr) {
    return nullptr;
  }
  FluidDomainSettings *fds = fmd->domain;
  if (!ELEM(cache_type, FLUID_DOMAIN_CACHE_REPLAY, FLUID_DOMAIN_CACHE_MODULAR, FLUID_DOMAIN_CACHE_ALL)) {
    PyErr_SetString(PyExc_ValueError, "Unknown fluid cache type");
    return nullptr;
  }
  if (strlen(directory) >= sizeof(fds->cache_directory)) {
    PyErr_SetString(PyExc_ValueError, "The fluid cache directory is too long");
    return nullptr;
  }
  fds->cache_type = eFluidDomain_CacheType(cache_type);
  fds->cache_flag = eFluidDomain_CacheFlag(cache_flag);
  fds->cache_frame_pause_data = pause[0];
  fds->cache_frame_pause_noise = pause[1];
  fds->cache_frame_pause_mesh = pause[2];
  fds->cache_frame_pause_particles = pause[3];
  fds->cache_frame_pause_guide = pause[4];
  STRNCPY(fds->cache_directory, directory);
  /* The solver in memory belongs to the old state: free it (no file is touched), so the domain
   * reads its cache again from the directory it now names. */
  BKE_fluid_modifier_reset(fmd);
  /* Re-evaluate as a side effect, never as a user edit: a user edit also invalidates the point
   * cache (`depsgraph_tag.cc`), and an invalidated Replay domain frees its cache at the start
   * frame, deleting the files in the directory it now names (`fluid.cc`). */
  for (Scene &scene : G_MAIN->scenes) {
    for (ViewLayer &view_layer : scene.view_layers) {
      if (Depsgraph *depsgraph = BKE_scene_get_depsgraph(&scene, &view_layer)) {
        DEG_id_tag_update_for_side_effect_request(
            depsgraph, &ob->id, ID_RECALC_GEOMETRY | ID_RECALC_SYNC_TO_EVAL);
      }
    }
  }
  WM_main_add_notifier(NC_OBJECT | ND_MODIFIER, ob);
  Py_RETURN_NONE;
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
    {"deadline",
     jobs_deadline,
     METH_VARARGS,
     "deadline(scene, job_type, seconds): that scene's RENDER or OBJECT_BAKE job stops by itself this "
     "many seconds from now (its break check reads it on the job's thread; no other job does), and the "
     "job's end clears it; deadline(scene, job_type, None) clears it if it is that job's; "
     "deadline(None) clears any."},
    {"deadline_fired",
     jobs_deadline_fired,
     METH_NOARGS,
     "deadline_fired(): True when the last deadline set stopped its job (a break check returned true "
     "because of it), False when it did not (yet); kept after the job ends until the next deadline."},
    {"render_stats",
     jobs_render_stats,
     METH_O,
     "render_stats(scene): {'mem_used_mb', 'mem_peak_mb'} of the scene's last or running render, as "
     "the renderer reported them; None when the scene has no render."},
    {"notify",
     jobs_notify,
     METH_NOARGS,
     "notify(): tell job listeners (the status bar) that a job's state changed, so they redraw."},
    {"scripts_auto_execute",
     jobs_scripts_auto_execute,
     METH_NOARGS,
     "scripts_auto_execute(): whether this document may run its Python (scripted drivers in "
     "full, text blocks); False when auto-execution is off for it."},
    {"fluid_state",
     jobs_fluid_state,
     METH_O,
     "fluid_state(object): a fluid domain's cache state, read directly: cache_type (0 replay, 1 "
     "modular, 2 all), cache_flag (baked, baking, outdated bits), pause (the data, noise, mesh, "
     "particles and guide pause frames), directory, and error (Blender's last bake error, or '')."},
    {"fluid_state_set",
     jobs_fluid_state_set,
     METH_VARARGS,
     "fluid_state_set(object, (cache_type, cache_flag, (pause * 5), directory)): write a fluid "
     "domain's cache state directly, without RNA updates or a user-edit tag, so nothing is freed "
     "or deleted; the domain is re-evaluated as a side effect."},
    {"desktop_permission",
     jobs_desktop_permission,
     METH_NOARGS,
     "desktop_permission(): whether the operating system lets Angelo post notifications: "
     "'authorized', 'denied', 'not_determined', 'unknown' (no answer yet), 'unavailable' or "
     "'unsupported'. Each call also asks the system again for a later call."},
    {"desktop_request_permission",
     jobs_desktop_request_permission,
     METH_NOARGS,
     "desktop_request_permission(): ask once per process for permission to post notifications; "
     "the system shows its question only the first time ever."},
    {"desktop_notify",
     jobs_desktop_notify,
     METH_VARARGS,
     "desktop_notify(identifier, title, body): post an operating-system notification. Returns "
     "'posted', or the permission state that kept it from being posted."},
    {"desktop_attention",
     jobs_desktop_attention,
     METH_NOARGS,
     "desktop_attention(): bounce the Dock icon once when Angelo is not in front. Returns "
     "'bounced', 'foreground', 'unavailable' or 'unsupported'."},
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
