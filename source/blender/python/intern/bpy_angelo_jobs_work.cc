/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * What quitting now would lose, as far as Blender itself knows: the window-manager jobs that are
 * someone's work, and asset catalogs with unsaved changes. Restart to Update asks both before it
 * quits (`work_guard.py`, `update_restart.py`; doc/angelo/updates_and_reports.rst).
 *
 * `running_work` classifies every #eWM_JobType in one `switch` with no `default`, compiled with
 * the missing-enumerator warning as an error: a job type Blender adds (a base move to a newer
 * Blender included) does not build until someone decides whether it is work. A job that only
 * recomputes what a relaunch recomputes by itself (a directory listing, an asset library load, a
 * preview or thumbnail, shader compilation, a clip prefetch, a sound wave) is not work; every
 * other job is, so a job nobody thought about stops the restart rather than being lost.
 *
 * `running_scene_jobs` answers a different question with its own switch, built the same way:
 * does a running job evaluate the document's scene, or write document data, on its own thread?
 * The parallel sessions' live view and bindings then leave the scene alone (`scene_jobs.py`).
 *
 * `catalogs_unsaved` is the check Blender's own quit dialog makes
 * (`AS_asset_library_has_any_unsaved_catalogs`). Saving the .blend file writes the loaded
 * libraries' catalogs when `AssetLibrary::save_catalogs_when_file_is_saved` is set (the quit
 * dialog's "Save modified asset catalogs" checkbox, which keeps its last state for the session);
 * `catalogs_save_with_file` reads and sets it so a restart's save writes them too.
 */

#include <Python.h>

#include "BLI_threads.h"

#include "BKE_global.hh"
#include "BKE_main.hh"

#include "AS_asset_library.hh"

#include "WM_api.hh"

#include "bpy_angelo_jobs_work.hh"

namespace blender {

namespace {

enum class JobWork {
  /** Recomputed by a relaunch by itself (or not a job type at all): quitting loses nothing. */
  None,
  /** A render, bake, composite or export: the update card names it so. */
  Render,
  /** Any other job someone started (a proxy build, tracking, an import, a remesh...). */
  Task,
};

struct JobClass {
  /** The enumerator without its `WM_JOB_TYPE_` prefix, as `bpy.app.is_job_running` spells the ones it knows. */
  const char *name;
  JobWork work;
};

/** The last #eWM_JobType: `running_work` asks for every value up to it. `test_native_work_guard.py` checks it
 * is still the enum's last enumerator and that the switch below names every enumerator. */
constexpr int LAST_JOB_TYPE = WM_JOB_TYPE_SOUND_MIXDOWN;

#if defined(__clang__)
#  pragma clang diagnostic push
#  pragma clang diagnostic error "-Wswitch"
#elif defined(__GNUC__)
#  pragma GCC diagnostic push
#  pragma GCC diagnostic error "-Wswitch"
#elif defined(_MSC_VER)
#  pragma warning(push)
#  pragma warning(default : 4062)
#  pragma warning(error : 4062)
#endif

/** Whether a job of this type is someone's work. No `default`: a new type is a build error until it is listed. */
JobClass classify(const eWM_JobType type)
{
  switch (type) {
    case WM_JOB_TYPE_ANY:
      return {"ANY", JobWork::None}; /* A query wildcard, never a job's own type. */

    /* Recomputed by a relaunch when it is next needed. */
    case WM_JOB_TYPE_RENDER_PREVIEW:
      return {"RENDER_PREVIEW", JobWork::None}; /* Material, icon and node previews. */
    case WM_JOB_TYPE_LOAD_PREVIEW:
      return {"LOAD_PREVIEW", JobWork::None};
    case WM_JOB_TYPE_FILESEL_READDIR:
      return {"FILESEL_READDIR", JobWork::None};
    case WM_JOB_TYPE_ASSET_LIBRARY_LOAD:
      return {"ASSET_LIBRARY_LOAD", JobWork::None};
    case WM_JOB_TYPE_ASSET_LIBRARY_GLOBAL_LISTING_LOAD:
      return {"ASSET_LIBRARY_GLOBAL_LISTING_LOAD", JobWork::None};
    case WM_JOB_TYPE_CLIP_PREFETCH:
      return {"CLIP_PREFETCH", JobWork::None};
    case WM_JOB_TYPE_SEQ_BUILD_PREVIEW:
      return {"SEQ_BUILD_PREVIEW", JobWork::None}; /* Sound waveforms. */
    case WM_JOB_TYPE_SHADER_COMPILATION:
      return {"SHADER_COMPILATION", JobWork::None};
    case WM_JOB_TYPE_STUDIOLIGHT:
      return {"STUDIOLIGHT", JobWork::None};
    case WM_JOB_TYPE_FSMENU_BOOKMARK_VALIDATE:
      return {"FSMENU_BOOKMARK_VALIDATE", JobWork::None};
    case WM_JOB_TYPE_SEQ_DRAW_THUMBNAIL:
      return {"SEQ_DRAW_THUMBNAIL", JobWork::None};
    case WM_JOB_TYPE_SEQ_DRAG_DROP_PREVIEW:
      return {"SEQ_DRAG_DROP_PREVIEW", JobWork::None};

    /* Renders, bakes, composites and exports. */
    case WM_JOB_TYPE_RENDER:
      return {"RENDER", JobWork::Render};
    case WM_JOB_TYPE_COMPOSITE:
      return {"COMPOSITE", JobWork::Render};
    case WM_JOB_TYPE_OBJECT_BAKE:
      return {"OBJECT_BAKE", JobWork::Render};
    case WM_JOB_TYPE_OBJECT_BAKE_TEXTURE:
      return {"OBJECT_BAKE_TEXTURE", JobWork::Render};
    case WM_JOB_TYPE_LIGHT_BAKE:
      return {"LIGHT_BAKE", JobWork::Render};
    case WM_JOB_TYPE_POINTCACHE:
      return {"POINTCACHE", JobWork::Render};
    case WM_JOB_TYPE_BAKE_GEOMETRY_NODES:
      return {"BAKE_GEOMETRY_NODES", JobWork::Render};
    case WM_JOB_TYPE_OBJECT_SIM_FLUID:
      return {"OBJECT_SIM_FLUID", JobWork::Render};
    case WM_JOB_TYPE_OBJECT_SIM_OCEAN:
      return {"OBJECT_SIM_OCEAN", JobWork::Render};
    case WM_JOB_TYPE_DPAINT_BAKE:
      return {"DPAINT_BAKE", JobWork::Render};
    case WM_JOB_TYPE_LINEART:
      return {"LINEART", JobWork::Render}; /* Bake Line Art. */
    case WM_JOB_TYPE_ALEMBIC_EXPORT:
      return {"ALEMBIC_EXPORT", JobWork::Render};
    case WM_JOB_TYPE_USD_EXPORT:
      return {"USD_EXPORT", JobWork::Render};
    case WM_JOB_TYPE_SOUND_MIXDOWN:
      return {"SOUND_MIXDOWN", JobWork::Render}; /* Writes the audio file. */

    /* Other work someone started. */
    case WM_JOB_TYPE_CLIP_BUILD_PROXY:
      return {"CLIP_BUILD_PROXY", JobWork::Task};
    case WM_JOB_TYPE_CLIP_TRACK_MARKERS:
      return {"CLIP_TRACK_MARKERS", JobWork::Task};
    case WM_JOB_TYPE_CLIP_SOLVE_CAMERA:
      return {"CLIP_SOLVE_CAMERA", JobWork::Task};
    case WM_JOB_TYPE_SEQ_BUILD_PROXY:
      return {"SEQ_BUILD_PROXY", JobWork::Task};
    case WM_JOB_TYPE_ALEMBIC_IMPORT:
      return {"ALEMBIC_IMPORT", JobWork::Task};
    case WM_JOB_TYPE_USD_IMPORT:
      return {"USD_IMPORT", JobWork::Task};
    case WM_JOB_TYPE_QUADRIFLOW_REMESH:
      return {"QUADRIFLOW_REMESH", JobWork::Task};
    case WM_JOB_TYPE_TRACE_IMAGE:
      return {"TRACE_IMAGE", JobWork::Task};
    case WM_JOB_TYPE_CALCULATE_SIMULATION_NODES:
      return {"CALCULATE_SIMULATION_NODES", JobWork::Task};
    case WM_JOB_TYPE_UV_PACK:
      return {"UV_PACK", JobWork::Task};
    case WM_JOB_TYPE_GENERATE_TEXTURE_CACHE:
      return {"GENERATE_TEXTURE_CACHE", JobWork::Task};
  }
  /* Not an enumerator (no job is created with one): fail closed. */
  return {"UNKNOWN", JobWork::Task};
}

/**
 * Whether a job of this type evaluates the document's scene on its own thread (a depsgraph of it,
 * its frames), or writes document data the main thread's evaluation reads. An evaluation on the
 * main thread meanwhile would race it, so the parallel sessions' live view waits, and a binding
 * does not settle (`scene_jobs.py`). A job that only reads original data (a listing, a texture
 * cache, a proxy build), or that evaluated its own copy before its thread started, does not race
 * one. No `default`: a new type is a build error until it is listed; unsure is true.
 */
bool evaluates_document(const eWM_JobType type)
{
  switch (type) {
    case WM_JOB_TYPE_ANY:
      return false; /* A query wildcard, never a job's own type. */

    /* Files, listings and caches of their own, or the document's data read only. */
    case WM_JOB_TYPE_LOAD_PREVIEW:
    case WM_JOB_TYPE_FILESEL_READDIR:
    case WM_JOB_TYPE_ASSET_LIBRARY_LOAD:
    case WM_JOB_TYPE_ASSET_LIBRARY_GLOBAL_LISTING_LOAD:
    case WM_JOB_TYPE_CLIP_BUILD_PROXY:
    case WM_JOB_TYPE_CLIP_PREFETCH:
    case WM_JOB_TYPE_SEQ_BUILD_PROXY:
    case WM_JOB_TYPE_SEQ_BUILD_PREVIEW:
    case WM_JOB_TYPE_SHADER_COMPILATION:
    case WM_JOB_TYPE_STUDIOLIGHT:
    case WM_JOB_TYPE_FSMENU_BOOKMARK_VALIDATE:
    case WM_JOB_TYPE_SEQ_DRAW_THUMBNAIL:
    case WM_JOB_TYPE_SEQ_DRAG_DROP_PREVIEW:
    case WM_JOB_TYPE_GENERATE_TEXTURE_CACHE:
      return false;
    /* Evaluates its own compositor depsgraph in its init callback, on the main thread; its thread
     * composites that evaluated copy (`node_compositor_job.cc`). */
    case WM_JOB_TYPE_COMPOSITE:
      return false;

    /* Material and world previews render copies in their own Main, but scene and pose (action)
     * previews draw, and pose ones evaluate, the view layer's own depsgraph on the job's thread
     * (`render_preview.cc`, `scene_preview_render`, `action_preview_render`). */
    case WM_JOB_TYPE_RENDER_PREVIEW:
      return true;
    /* The bake fills the cache the modifier's evaluation reads (`ocean_bake_exec` sets it first). */
    case WM_JOB_TYPE_OBJECT_SIM_OCEAN:
      return true;
    case WM_JOB_TYPE_RENDER:
    case WM_JOB_TYPE_OBJECT_SIM_FLUID:
    case WM_JOB_TYPE_OBJECT_BAKE_TEXTURE:
    case WM_JOB_TYPE_OBJECT_BAKE:
    case WM_JOB_TYPE_POINTCACHE:
    case WM_JOB_TYPE_DPAINT_BAKE:
    case WM_JOB_TYPE_ALEMBIC_EXPORT:
    case WM_JOB_TYPE_USD_EXPORT:
    case WM_JOB_TYPE_LIGHT_BAKE:
    case WM_JOB_TYPE_LINEART:
    case WM_JOB_TYPE_CALCULATE_SIMULATION_NODES:
    case WM_JOB_TYPE_BAKE_GEOMETRY_NODES:
      return true; /* Evaluate the document's scene, often frame by frame. */
    case WM_JOB_TYPE_ALEMBIC_IMPORT:
    case WM_JOB_TYPE_USD_IMPORT:
    case WM_JOB_TYPE_CLIP_TRACK_MARKERS:
    case WM_JOB_TYPE_CLIP_SOLVE_CAMERA:
    case WM_JOB_TYPE_QUADRIFLOW_REMESH:
    case WM_JOB_TYPE_TRACE_IMAGE:
    case WM_JOB_TYPE_UV_PACK:
      return true; /* Write document data (new objects, tracks, meshes, strokes, UVs). */
    case WM_JOB_TYPE_SOUND_MIXDOWN:
      return true; /* Mixes the scene's own audio scene, which its evaluation updates. */
  }
  /* Not an enumerator (no job is created with one): fail closed. */
  return true;
}

#if defined(__clang__)
#  pragma clang diagnostic pop
#elif defined(__GNUC__)
#  pragma GCC diagnostic pop
#elif defined(_MSC_VER)
#  pragma warning(pop)
#endif

bool require_main_thread()
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "Work status requires Blender's main thread");
    return false;
  }
  return true;
}

PyObject *work_running_work(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  PyObject *found = PyList_New(0);
  if (found == nullptr) {
    return nullptr;
  }
  const wmWindowManager *wm = G_MAIN ? static_cast<const wmWindowManager *>(G_MAIN->wm.first) :
                                       nullptr;
  for (int value = WM_JOB_TYPE_ANY + 1; wm != nullptr && value <= LAST_JOB_TYPE; value++) {
    const eWM_JobType type = eWM_JobType(value);
    const JobClass job = classify(type);
    /* Running or waiting for another job to finish (suspended), whoever owns it. */
    if (job.work == JobWork::None || !WM_jobs_has_running(wm, nullptr, type)) {
      continue;
    }
    PyObject *item = Py_BuildValue("(ss)", job.name, job.work == JobWork::Render ? "render" : "task");
    if (item == nullptr || PyList_Append(found, item) < 0) {
      Py_XDECREF(item);
      Py_DECREF(found);
      return nullptr;
    }
    Py_DECREF(item);
  }
  PyObject *result = PyList_AsTuple(found);
  Py_DECREF(found);
  return result;
}

PyObject *work_running_scene_jobs(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  PyObject *found = PyList_New(0);
  if (found == nullptr) {
    return nullptr;
  }
  const wmWindowManager *wm = G_MAIN ? static_cast<const wmWindowManager *>(G_MAIN->wm.first) :
                                       nullptr;
  for (int value = WM_JOB_TYPE_ANY + 1; wm != nullptr && value <= LAST_JOB_TYPE; value++) {
    const eWM_JobType type = eWM_JobType(value);
    if (!evaluates_document(type) || !WM_jobs_has_running(wm, nullptr, type)) {
      continue;
    }
    PyObject *name = PyUnicode_FromString(classify(type).name);
    if (name == nullptr || PyList_Append(found, name) < 0) {
      Py_XDECREF(name);
      Py_DECREF(found);
      return nullptr;
    }
    Py_DECREF(name);
  }
  PyObject *result = PyList_AsTuple(found);
  Py_DECREF(found);
  return result;
}

PyObject *work_catalogs_unsaved(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  return PyBool_FromLong(AS_asset_library_has_any_unsaved_catalogs());
}

PyObject *work_catalogs_save_with_file(PyObject * /*self*/, PyObject *args)
{
  int enable = -1;
  if (!require_main_thread() || !PyArg_ParseTuple(args, "|p:catalogs_save_with_file", &enable)) {
    return nullptr;
  }
  const bool previous = asset_system::AssetLibrary::save_catalogs_when_file_is_saved;
  if (enable != -1) {
    asset_system::AssetLibrary::save_catalogs_when_file_is_saved = enable != 0;
  }
  return PyBool_FromLong(previous);
}

PyMethodDef methods[] = {
    {"running_work",
     work_running_work,
     METH_NOARGS,
     "running_work(): ((job_type, kind), ...) for each type of window-manager job that runs or "
     "waits to run, whoever started it, and is someone's work; kind is 'render' (a render, bake, "
     "composite or export) or 'task' (any other). Previews, listings and other jobs a relaunch "
     "repeats by itself are left out."},
    {"running_scene_jobs",
     work_running_scene_jobs,
     METH_NOARGS,
     "running_scene_jobs(): (job_type, ...) for each type of window-manager job that runs or "
     "waits to run, whoever started it, and evaluates the document's scene on its own thread or "
     "writes document data its evaluation reads: an evaluation on the main thread now would race "
     "it."},
    {"catalogs_unsaved",
     work_catalogs_unsaved,
     METH_NOARGS,
     "catalogs_unsaved(): whether a loaded asset library has asset catalog changes not written "
     "to its catalog file, as Blender's quit dialog checks."},
    {"catalogs_save_with_file",
     work_catalogs_save_with_file,
     METH_VARARGS,
     "catalogs_save_with_file([enable]): whether saving the .blend file also writes the loaded "
     "libraries' modified asset catalogs (the quit dialog's 'Save modified asset catalogs'); with "
     "an argument, sets it. Returns the value before the call."},
    {nullptr, nullptr, 0, nullptr},
};

}  // namespace

PyMethodDef *BPY_angelo_jobs_work_methods()
{
  return methods;
}

}  // namespace blender
