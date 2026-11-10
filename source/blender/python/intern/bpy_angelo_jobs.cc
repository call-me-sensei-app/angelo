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
 * `quit_activity_set` and `quit_activities` name running work (a live motion capture with a phone,
 * an upload) that quitting, or reading another file, would end: the quit and close-file
 * confirmation lists it (`WM_quit_activity_set`, `wm_quit_activities.cc`), whatever the Save Prompt
 * preference says. `close_dialog_open`, `close_dialog_texts` and `quit_request_simulate` are test
 * seams that exist only with `--enable-event-simulate`.
 *
 * `msgbus_subscribers` lists the `bpy.msgbus` subscribers that writing given settings
 * would notify. A look's or strip's own temporary writes publish to the message bus, and
 * Python can subscribe to a setting but never list who did. Only subscribers made by
 * `bpy.msgbus.subscribe_rna` are listed (Blender's own C++ ones are not Python), with the
 * function they call, so the add-on decides which code ships with the application.
 *
 * `job_reports` returns a job's own ERROR and WARNING reports once it has ended
 * (`WM_angelo_job_reports.hh`): the Info log also holds what the person or another script
 * reported meanwhile, and a fluid bake clears it as it starts.
 *
 * `running_work` (every job type that is someone's work, whoever owns it) and the asset catalog
 * checks Restart to Update makes are in `bpy_angelo_jobs_work.cc`.
 */

#include <Python.h>

#include <cmath>

#include "BLI_string.h"
#include "BLI_threads.h"
#include "BLI_utildefines.h"
#include "BLI_vector.hh"

#include "BKE_angelo_job_deadline.hh"
#include "BKE_angelo_job_progress.hh"
#include "BKE_context.hh"
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

#include "UI_interface_c.hh"

#include "WM_angelo_job_reports.hh"
#include "WM_api.hh"
#include "WM_message.hh"
#include "WM_types.hh"

#include "bpy_angelo_desktop.hh"
#include "bpy_angelo_jobs.hh"
#include "bpy_angelo_jobs_caches.hh"
#include "bpy_angelo_jobs_work.hh"
#include "bpy_angelo_update_quit.hh"
#include "bpy_capi_utils.hh"
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

static PyObject *jobs_last_progress(PyObject * /*self*/, PyObject *args)
{
  Scene *scene;
  const AngeloJobType *type;
  wmWindowManager *wm;
  if (!require_main_thread() || !parse_job(args, "last_progress", &scene, &type, &wm)) {
    return nullptr;
  }
  if (type->type != WM_JOB_TYPE_RENDER) {
    PyErr_SetString(PyExc_ValueError, "last_progress: only RENDER jobs keep a record");
    return nullptr;
  }
  /* Read the scene first: a record written for another scene, or replaced meanwhile, is not this job's. */
  if (bke::angelo::job_progress_scene.load(std::memory_order_acquire) != scene->id.session_uid) {
    Py_RETURN_NONE;
  }
  const double progress = double(bke::angelo::job_progress_last.load(std::memory_order_relaxed));
  const int64_t first = bke::angelo::job_progress_first_ns.load(std::memory_order_relaxed);
  if (first == 0) {
    return Py_BuildValue("(dO)", progress, Py_None);
  }
  const double since = double(bke::angelo::job_deadline_now_ns() - first) / 1e9;
  return Py_BuildValue("(dd)", progress, since);
}

static PyObject *jobs_job_reports(PyObject * /*self*/, PyObject *args)
{
  Scene *scene;
  const AngeloJobType *type;
  wmWindowManager *wm;
  if (!require_main_thread() || !parse_job(args, "job_reports", &scene, &type, &wm)) {
    return nullptr;
  }
  const Vector<wm::angelo::JobReport> reports = wm::angelo::job_reports_take(
      int(type->type), scene->id.session_uid);
  PyObject *list = PyList_New(reports.size());
  if (list == nullptr) {
    return nullptr;
  }
  for (const int64_t i : reports.index_range()) {
    /* A report may carry bytes that are not UTF-8 (a path): replaced, never an exception. */
    PyObject *text = PyUnicode_DecodeUTF8(
        reports[i].text.data(), Py_ssize_t(reports[i].text.size()), "replace");
    /* A sequence Blender's decoder counts as one character may decode to several. */
    if (text && PyUnicode_GET_LENGTH(text) > wm::angelo::job_report_max_chars) {
      PyObject *clipped = PyUnicode_Substring(text, 0, wm::angelo::job_report_max_chars);
      Py_DECREF(text);
      text = clipped;
    }
    PyObject *item = text ? Py_BuildValue("(sN)",
                                          reports[i].type == RPT_ERROR ? "ERROR" : "WARNING",
                                          text) :
                            nullptr;
    if (item == nullptr) {
      Py_DECREF(list);
      return nullptr;
    }
    PyList_SET_ITEM(list, i, item);
  }
  return list;
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

static PyObject *jobs_tag_file_modified(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "tag_file_modified requires Blender's main thread");
    return nullptr;
  }
  if (G_MAIN == nullptr || G_MAIN->wm.first == nullptr) {
    PyErr_SetString(PyExc_RuntimeError, "tag_file_modified requires a window manager");
    return nullptr;
  }
  /* Marks the open file changed (the title, and quitting asks to save) with no Undo step: an Undo
   * push from a timer waits while any modal operator runs, which may be never. */
  WM_file_tag_modified();
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

static PyObject *jobs_quit_activity_set(PyObject * /*self*/, PyObject *args)
{
  const char *key = nullptr;
  PyObject *message_object = nullptr;
  if (!require_main_thread() || !PyArg_ParseTuple(args, "sO:quit_activity_set", &key, &message_object)) {
    return nullptr;
  }
  const char *message = "";   /* None or "": the activity ended. */
  Py_ssize_t message_size = 0;
  if (message_object != Py_None) {
    if (!PyUnicode_Check(message_object)) {
      PyErr_SetString(PyExc_TypeError, "quit_activity_set: the message is a str or None");
      return nullptr;
    }
    message = PyUnicode_AsUTF8AndSize(message_object, &message_size);
    if (message == nullptr) {
      return nullptr;
    }
  }
  if (!WM_quit_activity_set(key, StringRef(message, message_size))) {
    PyErr_Format(PyExc_ValueError,
                 "quit_activity_set: a key of 1-%d characters [a-z0-9._-], a message of at most %d "
                 "bytes, at most %d activities",
                 WM_QUIT_ACTIVITY_KEY_MAX,
                 WM_QUIT_ACTIVITY_MESSAGE_MAX,
                 WM_QUIT_ACTIVITIES_MAX);
    return nullptr;
  }
  Py_RETURN_NONE;
}

static PyObject *jobs_quit_activities(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  PyObject *list = PyList_New(0);
  if (list == nullptr) {
    return nullptr;
  }
  bool failed = false;
  WM_quit_activities_foreach([&](const StringRef key, const StringRef message) {
    if (failed) {
      return;
    }
    PyObject *item = Py_BuildValue(
        "(s#s#)", key.data(), Py_ssize_t(key.size()), message.data(), Py_ssize_t(message.size()));
    failed = item == nullptr || PyList_Append(list, item) == -1;
    Py_XDECREF(item);
  });
  if (failed) {
    Py_DECREF(list);
    return nullptr;
  }
  PyObject *result = PyList_AsTuple(list);
  Py_DECREF(list);
  return result;
}

/** The test seams below exist only with `--enable-event-simulate` (GUI tests). */
static bool require_event_simulate(const char *function)
{
  if ((G.f & G_FLAG_EVENT_SIMULATE) == 0) {
    PyErr_Format(PyExc_RuntimeError, "%s needs --enable-event-simulate", function);
    return false;
  }
  return true;
}

static PyObject *jobs_close_dialog_open(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread() || !require_event_simulate("close_dialog_open")) {
    return nullptr;
  }
  bContext *C = BPY_context_get();
  wmWindowManager *wm = CTX_wm_manager(C);
  for (wmWindow &win : wm->windows) {
    const bScreen *screen = WM_window_get_active_screen(&win);
    if (screen && ui::popup_block_name_exists(screen, "file_close_popup")) {
      Py_RETURN_TRUE;
    }
  }
  Py_RETURN_FALSE;
}

static PyObject *jobs_close_dialog_texts(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread() || !require_event_simulate("close_dialog_texts")) {
    return nullptr;
  }
  bContext *C = BPY_context_get();
  wmWindowManager *wm = CTX_wm_manager(C);
  PyObject *list = PyList_New(0);
  if (list == nullptr) {
    return nullptr;
  }
  for (wmWindow &win : wm->windows) {
    const bScreen *screen = WM_window_get_active_screen(&win);
    if (screen == nullptr) {
      continue;
    }
    /* The close-file confirmation, and an operator's own confirmation (Revert, Load Factory
     * Settings), which lists the activities in its message. */
    Vector<std::string> texts = ui::popup_block_name_texts(screen, "file_close_popup");
    texts.extend(ui::popup_block_name_texts(screen, "wm_block_dialog_create"));
    for (const std::string &text : texts) {
      PyObject *item = PyUnicode_FromStringAndSize(text.data(), Py_ssize_t(text.size()));
      if (item == nullptr || PyList_Append(list, item) == -1) {
        Py_XDECREF(item);
        Py_DECREF(list);
        return nullptr;
      }
      Py_DECREF(item);
    }
  }
  PyObject *result = PyList_AsTuple(list);
  Py_DECREF(list);
  return result;
}

static PyObject *jobs_quit_request_simulate(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread() || !require_event_simulate("quit_request_simulate")) {
    return nullptr;
  }
  WM_quit_request_simulate(BPY_context_get());
  Py_RETURN_NONE;
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
    {"_update_quit_catalog_generation", BPY_angelo_update_quit_catalog_generation, METH_NOARGS,
     "Private supported catalog edit generation, or zero when unavailable."},
    {"_update_quit_protocol", BPY_angelo_update_quit_protocol, METH_NOARGS,
     "Private updater native hand-over protocol version."},
    {"_update_quit_request", BPY_angelo_update_quit_request, METH_VARARGS,
     "Private updater: one owned delayed quit, no caller-supplied callback."},
    {"_update_quit_revoke", BPY_angelo_update_quit_revoke, METH_O,
     "Private updater: revoke only this opaque pending quit owner."},
    {"_update_quit_status", BPY_angelo_update_quit_status, METH_O,
     "Private updater: read this exact quit owner without replay."},
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
    {"last_progress",
     jobs_last_progress,
     METH_VARARGS,
     "last_progress(scene, 'RENDER'): (progress, seconds since its first sample or None) of that scene's "
     "latest render job, as its thread last reported them, also once the job has ended; None when the "
     "latest render job was another scene's or there was none."},
    {"job_reports",
     jobs_job_reports,
     METH_VARARGS,
     "job_reports(scene, job_type): that scene's job's own ERROR and WARNING reports from its last "
     "run, once it has ended, as (type, text): 'ERROR' or 'WARNING', Blender's English where it "
     "translated the report, else the stored message; oldest first, at most 8 (a WARNING never "
     "displaces an ERROR), each at most 500 characters. Reading them removes them: a second call "
     "returns []. [] when the run reported none, or another run of that scene and type was asked "
     "for since."},
    {"render_stats",
     jobs_render_stats,
     METH_O,
     "render_stats(scene): {'mem_used_mb', 'mem_peak_mb'} of the scene's last or running render, as "
     "the renderer reported them; None when the scene has no render."},
    {"notify",
     jobs_notify,
     METH_NOARGS,
     "notify(): tell job listeners (the status bar) that a job's state changed, so they redraw."},
    {"tag_file_modified",
     jobs_tag_file_modified,
     METH_NOARGS,
     "tag_file_modified(): mark the open file as changed (bpy.data.is_dirty becomes True; quitting "
     "asks to save) without an Undo step. Main thread only (RuntimeError elsewhere). Returns None."},
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
    {"quit_activity_set",
     jobs_quit_activity_set,
     METH_VARARGS,
     "quit_activity_set(key, message): name running work that quitting, or reading another file, "
     "would end; the quit and close-file confirmation list each message (an English msgid, "
     "translated where drawn). None or '' removes the key. Raises ValueError for a key that is not "
     "1-64 characters of [a-z0-9._-] starting with a letter or digit, a message over 200 bytes, "
     "one with control or bidirectional-control characters or only spaces, or a 17th key."},
    {"quit_activities",
     jobs_quit_activities,
     METH_NOARGS,
     "quit_activities(): every registered activity as (key, message), in the order first "
     "registered."},
    {"close_dialog_open",
     jobs_close_dialog_open,
     METH_NOARGS,
     "close_dialog_open(): (tests, --enable-event-simulate only) whether the quit or close-file "
     "confirmation is open in any window."},
    {"close_dialog_texts",
     jobs_close_dialog_texts,
     METH_NOARGS,
     "close_dialog_texts(): (tests, --enable-event-simulate only) every text the open quit or "
     "close-file confirmation, or an operator's own confirmation, shows (labels and buttons, as "
     "drawn), in every window."},
    {"quit_request_simulate",
     jobs_quit_request_simulate,
     METH_NOARGS,
     "quit_request_simulate(): (tests, --enable-event-simulate only) what the operating system's "
     "quit request does (the macOS application menu or Dock)."},
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
  if (PyModule_AddFunctions(mod, BPY_angelo_jobs_work_methods()) < 0 ||
      PyModule_AddFunctions(mod, BPY_angelo_jobs_caches_methods()) < 0)
  {
    Py_DECREF(mod);
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
