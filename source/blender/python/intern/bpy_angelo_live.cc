/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * A worker's saved part, live in Angelo without entering Angelo's own data (`_bpy._angelo_live`).
 *
 * Parallel agents (phase 1) show each worker Blender's part on the session rail and full screen. Linking a part into
 * the artist's Main put its objects, materials and collections under every `bpy.data` lookup, iteration and script
 * of the artist's own chat, and made each reload a whole-Main mutation that could race a bake thread (break-it P1-1,
 * H1 and H2). So a part is read into a **private Main** (#BLO_read_from_file, as a file open reads, never set up as
 * the application's data), with its own depsgraph on its own scene and view layer, evaluated without running any
 * add-on handler, and drawn offscreen with the artist's 3D View settings but always in Solid shading (pictures and
 * the live view never start a renderer of their own, break-it H3). Bounds, a ray cast and a summary are answered
 * here, so no Python object ever points into a private Main; `close` frees it (and its depsgraph). Material and icon
 * previews already render from a Main of their own the same way.
 *
 * Main thread only. The capsule frees its part if Python drops it unclosed.
 */

#include <Python.h>

#include <algorithm>
#include <cstring>
#include <limits>

#include "MEM_guardedalloc.h"

#include "BLI_math_matrix.hh"
#include "BLI_math_vector.h"
#include "BLI_listbase.h"
#include "BLI_path_utils.hh"
#include "BLI_string.h"
#include "BLI_threads.h"
#include "BLI_utildefines.h"

#include "BKE_anim_data.hh"
#include "BKE_bpath.hh"
#include "BKE_fcurve_driver.h"
#include "BKE_global.hh"
#include "DNA_userdef_types.h"
#include "BKE_layer.hh"
#include "BKE_main.hh"
#include "BKE_object.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"

#include "BLO_readfile.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_build.hh"
#include "DEG_depsgraph_query.hh"

#include "DNA_anim_types.h"
#include "DNA_material_types.h"
#include "DNA_mesh_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "ED_transform_snap_object_context.hh"


#include "../generic/py_capi_utils.hh"

#include "bpy_angelo_live.hh"
#include "bpy_angelo_live_part.hh"

namespace blender {

static constexpr const char *capsule_name = "angelo.live_part";
static constexpr int LIVE_SUBDIVISION = 0; /* Subdivision levels a live part shows at most by default (Simplify). */

static void part_free(LivePart *part)
{
  if (part->main != nullptr) {
    BKE_main_free(part->main); /* Its scenes free their depsgraphs. */
  }
  MEM_delete(part);
}

bool live_require_main_thread()
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "A live part is read and drawn on Blender's main thread only");
    return false;
  }
  return true;
}

static void capsule_free(PyObject *capsule)
{
  LivePart *part = static_cast<LivePart *>(PyCapsule_GetPointer(capsule, capsule_name));
  if (part == nullptr) {
    return;
  }
  if (G_MAIN == nullptr || !BLI_thread_is_main()) {
    /* A capsule Python collects on another thread (a reference cycle, any thread) is never freed there: the
     * part's data and GPU resources belong to the main thread. It is left, as a part still open at exit is: 
     * Python's last garbage collection runs after #BKE_blender_free (#WM_exit_ex), when the window manager and GPU
     * a part's own data would be freed against are gone. `live_view` closes every part before either. */
    return;
  }
  part_free(part);
}

/** The open part of \a capsule, or null with a Python error set. */
static LivePart *part_of(PyObject *capsule)
{
  if (!PyCapsule_IsValid(capsule, capsule_name)) {
    PyErr_SetString(PyExc_TypeError, "Expected a live part (open)");
    return nullptr;
  }
  LivePart *part = static_cast<LivePart *>(PyCapsule_GetPointer(capsule, capsule_name));
  if (part == nullptr || part->main == nullptr) {
    PyErr_SetString(PyExc_ReferenceError, "This live part was closed");
    return nullptr;
  }
  return part;
}

/**
 * Make a read part safe to evaluate in the artist's process (break-it P1-2):
 * - its relative paths resolve against its own file, never the artist's (images, volumes, fluid caches; Blender
 *   resolves `//` against the global file);
 * - no Python driver of the worker's runs here: evaluation would run it in the artist's process with `bpy.data` bound
 *   to the artist's document (the worker runs its own drivers); simple expressions, which need no Python, still run;
 * - every object is drawn as in Object Mode: a part saved in Edit or Sculpt Mode has no edit or sculpt data here.
 * The private data is Angelo's to change; the part file is never written.
 */
static void prepare(LivePart &part, const char *filepath, const short subdivision)
{
  char folder[FILE_MAX];
  BLI_path_split_dir_part(filepath, folder, sizeof(folder));
  BKE_bpath_absolute_convert(part.main, folder, nullptr);
  BKE_fcurves_main_cb(part.main, [](ID * /*id*/, FCurve *fcurve) {
    ChannelDriver *driver = fcurve->driver;
    if (driver != nullptr && driver->type == DRIVER_TYPE_PYTHON && !BKE_driver_has_simple_expression(driver)) {
      driver->flag |= DRIVER_FLAG_INVALID;
    }
  });
  for (Object &object : part.main->objects) {
    object.mode = OB_MODE_OBJECT;
  }
  /* Blender's own Simplify, for Angelo's pictures and live view only: every read is a new Main, and each subdivided
   * mesh is set up again at its first draw (24 subdivided spheres: 0.3 s at levels 1-3 alike, 1.6 ms at 0,
   * measured). The worker's file and its own look are unchanged. */
  for (Scene &scene : part.main->scenes) {
    if (!(scene.r.mode & R_SIMPLIFY)) {
      /* Subdivision only: the scene's other stored Simplify values never apply (break-it P1-3 L8). */
      scene.r.simplify_particles = 1.0f;
      scene.r.simplify_volumes = 1.0f;
      scene.r.simplify_gpencil = 0;
      scene.r.simplify_subsurf = subdivision;
      scene.r.mode = (scene.r.mode | R_SIMPLIFY) & ~R_SIMPLIFY_NORMALS;
    }
    scene.r.simplify_subsurf = std::min(scene.r.simplify_subsurf, short(subdivision));
  }
}

static PyObject *live_open(PyObject * /*self*/, PyObject *args)
{
  const char *filepath;
  int subdivision = LIVE_SUBDIVISION;
  if (!PyArg_ParseTuple(args, "s|i:open", &filepath, &subdivision) || !live_require_main_thread()) {
    return nullptr;
  }
  subdivision = std::clamp(subdivision, 0, 6);
  ReportList reports;
  BKE_reports_init(&reports, RPT_STORE);
  BlendFileReadReport read_report{};
  read_report.reports = &reports;
  BlendFileData *bfd = BLO_read_from_file(filepath, BLO_READ_SKIP_USERDEF, &read_report);
  if (bfd == nullptr || bfd->main == nullptr || bfd->main->is_read_invalid) {
    char *why = BKE_reports_string(&reports, RPT_ERROR);
    PyErr_Format(PyExc_OSError, "The part file could not be read%s%s", why ? ": " : "", why ? why : "");
    if (why != nullptr) {
      MEM_delete(why);
    }
    BKE_reports_free(&reports);
    if (bfd != nullptr) {
      BLO_blendfiledata_free(bfd);
    }
    return nullptr;
  }
  BKE_reports_free(&reports);
  LivePart *part = MEM_new<LivePart>(__func__);
  part->main = bfd->main;
  bfd->main = nullptr; /* Owned by the part from here; the rest of the read is freed. */
  part->scene = bfd->curscene ? bfd->curscene : static_cast<Scene *>(part->main->scenes.first);
  part->view_layer = bfd->cur_view_layer;   /* The worker's own layer: what it saw. */
  BLO_blendfiledata_free(bfd);
  if (part->scene == nullptr) {
    part_free(part);
    PyErr_SetString(PyExc_ValueError, "The part file has no scene");
    return nullptr;
  }
  if (part->view_layer == nullptr || BLI_findindex(&part->scene->view_layers, part->view_layer) == -1) {
    part->view_layer = BKE_view_layer_default_view(part->scene);
  }
  prepare(*part, filepath, short(subdivision));
  return PyCapsule_New(part, capsule_name, capsule_free);
}

static PyObject *live_evaluate(PyObject * /*self*/, PyObject *args)
{
  PyObject *capsule;
  bool gpu_subdivision = false;
  if (!PyArg_ParseTuple(args, "O|O&:evaluate", &capsule, PyC_ParseBool, &gpu_subdivision)) {
    return nullptr;
  }
  LivePart *part = part_of(capsule);
  if (part == nullptr || !live_require_main_thread()) {
    return nullptr;
  }
  if (part->depsgraph == nullptr) {
    part->depsgraph = BKE_scene_ensure_depsgraph(part->main, part->scene, part->view_layer);
  }
  /* Built and evaluated directly: no add-on's depsgraph handler ever sees a part. Subdivision is evaluated on the
   * CPU here (the preference is read at evaluation): on the GPU, every read's new meshes are set up again at their
   * first draw, about 0.3 s for 24 subdivided spheres at any level, measured, inside a draw. */
  struct Restore {   /* Also when evaluation throws (break-it P1-3 L7). */
    eUserpref_GPU_Flag flag = U.gpu_flag;
    ~Restore()
    {
      U.gpu_flag = flag;
    }
  } restore;
  if (!gpu_subdivision) {
    U.gpu_flag = eUserpref_GPU_Flag(U.gpu_flag & ~USER_GPU_FLAG_SUBDIVISION_EVALUATION);
  }
  DEG_graph_relations_update(part->depsgraph);
  DEG_evaluate_on_refresh(part->depsgraph);
  part->evaluated = true;
  Py_RETURN_NONE;
}

static PyObject *live_close(PyObject * /*self*/, PyObject *capsule)
{
  if (!PyCapsule_IsValid(capsule, capsule_name) || !live_require_main_thread()) {
    if (!PyErr_Occurred()) {
      PyErr_SetString(PyExc_TypeError, "Expected a live part (open)");
    }
    return nullptr;
  }
  LivePart *part = static_cast<LivePart *>(PyCapsule_GetPointer(capsule, capsule_name));
  if (part != nullptr && part->main != nullptr) {
    BKE_main_free(part->main);
    part->main = nullptr;
    part->scene = nullptr;
    part->view_layer = nullptr;
    part->depsgraph = nullptr;
    part->evaluated = false;
  }
  Py_RETURN_NONE;
}

bool live_is_part(PyObject *object)
{
  return PyCapsule_IsValid(object, capsule_name);
}

LivePart *live_evaluated_part(PyObject *capsule)
{
  if (!live_require_main_thread()) {
    return nullptr;
  }
  LivePart *part = part_of(capsule);
  if (part != nullptr && !part->evaluated) {
    PyErr_SetString(PyExc_RuntimeError, "Evaluate the live part before reading or drawing it");
    return nullptr;
  }
  return part;
}

/** Visible evaluated objects of \a part with their world-space bounds; returns false when there are none. */
static PyObject *live_bounds(PyObject * /*self*/, PyObject *capsule)
{
  LivePart *part = live_evaluated_part(capsule);
  if (part == nullptr) {
    return nullptr;
  }
  float min[3], max[3];
  if (!live_depsgraph_bounds(part->depsgraph, min, max)) {
    Py_RETURN_NONE;
  }
  return Py_BuildValue("((fff)(fff))", min[0], min[1], min[2], max[0], max[1], max[2]);
}

static PyObject *live_summary(PyObject * /*self*/, PyObject *capsule)
{
  LivePart *part = live_evaluated_part(capsule);
  if (part == nullptr) {
    return nullptr;
  }
  PyObject *objects = PyList_New(0);
  if (objects == nullptr) {
    return nullptr;
  }
  int meshes = 0;
  long long faces = 0;   /* Of the evaluated meshes, as drawn (Simplify applied). */
  DEGObjectIterSettings settings = {nullptr};
  settings.depsgraph = part->depsgraph;
  settings.flags = DEG_ITER_OBJECT_FLAG_LINKED_DIRECTLY | DEG_ITER_OBJECT_FLAG_VISIBLE;
  DEG_OBJECT_ITER_BEGIN (&settings, ob) {
    /* An old file's name may not be valid UTF-8: replaced, never a null in the list. */
    PyObject *name = PyUnicode_DecodeUTF8(ob->id.name + 2, strlen(ob->id.name + 2), "replace");
    if (name == nullptr || PyList_Append(objects, name) != 0) {
      PyErr_Clear();   /* A name that cannot be listed is left out, never an error set on success. */
    }
    Py_XDECREF(name);
    meshes += ob->type == OB_MESH;
    if (ob->type == OB_MESH) {
      if (const Mesh *mesh = BKE_object_get_evaluated_mesh(ob)) {
        faces += mesh->faces_num;
      }
    }
  }
  DEG_OBJECT_ITER_END;
  int materials = 0;
  for (const Material &material : part->main->materials) {
    materials += material.id.us > 0;
  }
  float min[3], max[3];
  const bool found = live_depsgraph_bounds(part->depsgraph, min, max);
  float size[3] = {0.0f, 0.0f, 0.0f};
  if (found) {
    sub_v3_v3v3(size, max, min);
  }
  return Py_BuildValue("{s:N,s:i,s:L,s:i,s:(fff)}",
                       "objects",
                       objects,
                       "meshes",
                       meshes,
                       "faces",
                       faces,
                       "materials",
                       materials,
                       "size",
                       size[0],
                       size[1],
                       size[2]);
}

static PyObject *live_ray_cast(PyObject * /*self*/, PyObject *args)
{
  PyObject *capsule;
  float origin[3], direction[3];
  if (!PyArg_ParseTuple(args,
                        "O(fff)(fff):ray_cast",
                        &capsule,
                        &origin[0],
                        &origin[1],
                        &origin[2],
                        &direction[0],
                        &direction[1],
                        &direction[2]))
  {
    return nullptr;
  }
  LivePart *part = live_evaluated_part(capsule);
  if (part == nullptr || !live_require_main_thread()) {
    return nullptr;
  }
  normalize_v3(direction);
  ed::transform::SnapObjectContext *sctx = ed::transform::snap_object_context_create();
  ed::transform::SnapObjectParams params{};
  params.snap_target_select = SCE_SNAP_TARGET_ALL;
  params.ignore_editmode_filtering = true;
  float depth = std::numeric_limits<float>::max(), location[3], normal[3], matrix[4][4];
  int index = -1;
  const Object *hit = nullptr;
  const bool found = ed::transform::snap_object_project_ray_ex(sctx,
                                                               part->depsgraph,
                                                               nullptr,
                                                               &params,
                                                               origin,
                                                               direction,
                                                               &depth,
                                                               location,
                                                               normal,
                                                               &index,
                                                               &hit,
                                                               matrix);
  ed::transform::snap_object_context_destroy(sctx);
  if (!found || hit == nullptr) {
    Py_RETURN_NONE;
  }
  const Object *original = DEG_get_original(hit);
  PyObject *name = PyUnicode_DecodeUTF8(original->id.name + 2, strlen(original->id.name + 2), "replace");
  return name ? Py_BuildValue("(Ni)", name, int(original->type)) : nullptr;
}

static PyMethodDef methods[] = {
    {"open",
     reinterpret_cast<PyCFunction>(live_open),
     METH_VARARGS,
     "open(filepath, subdivision=0): read a saved part into a private Main of its own, subdivision shown at most "
     "at that level (Simplify); a live part."},
    {"evaluate",
     reinterpret_cast<PyCFunction>(live_evaluate),
     METH_VARARGS,
     "evaluate(part, gpu_subdivision=False): build and evaluate the part's own depsgraph (no add-on handler "
     "runs); subdivision on the CPU unless asked."},
    {"close",
     reinterpret_cast<PyCFunction>(live_close),
     METH_O,
     "close(part): free the part's Main and depsgraph now (closing twice does nothing)."},
    {"bounds",
     reinterpret_cast<PyCFunction>(live_bounds),
     METH_O,
     "bounds(part): ((min x, y, z), (max x, y, z)) of its visible objects, or None."},
    {"summary",
     reinterpret_cast<PyCFunction>(live_summary),
     METH_O,
     "summary(part): {'objects': visible names, 'meshes': n, 'materials': n used, 'size': (x, y, z)}."},
    {"ray_cast",
     reinterpret_cast<PyCFunction>(live_ray_cast),
     METH_VARARGS,
     "ray_cast(part, origin, direction): (object name, object type value) of the first hit, or None."},
    {"draw",
     reinterpret_cast<PyCFunction>(reinterpret_cast<void *>(live_draw)),
     METH_VARARGS | METH_KEYWORDS,
     "draw(target, offscreen, view3d, view_matrix, projection_matrix, *, view_layer=None, grid=False): draw a "
     "live part (or a scene of the document with its view_layer) into offscreen with a view of its own, always in "
     "Solid with view3d's Solid settings; view3d is only read."},
    {"hold",
     reinterpret_cast<PyCFunction>(live_hold),
     METH_VARARGS,
     "hold(window, area, seconds): no event reaches area's own handlers for seconds from now, and the window's key "
     "maps run only the allowed operators over it (a lease; area None releases the window's hold)."},
    {"release_holds",
     reinterpret_cast<PyCFunction>(live_release_holds),
     METH_NOARGS,
     "release_holds(): release every window's hold."},
    {"allow",
     reinterpret_cast<PyCFunction>(live_allow),
     METH_O,
     "allow(idnames): the operators a held area lets the window's key maps run."},
    {"held",
     reinterpret_cast<PyCFunction>(live_held),
     METH_VARARGS,
     "held(window, area): whether area is held now."},
    {"depsgraph_size",
     reinterpret_cast<PyCFunction>(live_depsgraph_size),
     METH_O,
     "depsgraph_size(depsgraph): (x, y, z) of the world box around every visible object and instance of an "
     "evaluated depsgraph, in C++ (a worker's own part, measured with each save), helper geometry left out (Boolean "
     "operands, wire or bounds display, hidden from renders), or None when it shows none."},
    {"depsgraph_bounds",
     reinterpret_cast<PyCFunction>(live_depsgraph_bounds_py),
     METH_VARARGS,
     "depsgraph_bounds(depsgraph[, helpers_alone]): ((min x, y, z), (max x, y, z)) world box around every visible "
     "object and instance of an evaluated depsgraph, as depsgraph_size measures it (helper geometry left out, counted "
     "when it is all there is; with helpers_alone False only what a Solid draw shows: no wire or bounds display), "
     "or None when it shows none."},
    {"depsgraph_sees",
     reinterpret_cast<PyCFunction>(live_depsgraph_sees_py),
     METH_VARARGS,
     "depsgraph_sees(depsgraph, perspective_matrix): whether a view with that 4x4 matrix sees geometry depsgraph_bounds "
     "counts without helpers_alone: one counted object's world box meets its frustum."},
    {"instance_bounds",
     reinterpret_cast<PyCFunction>(live_instance_bounds),
     METH_VARARGS,
     "instance_bounds(depsgraph, objects[, limit]): {session_uid: ((min x, y, z), (max x, y, z), owns_geometry) or "
     "(None, owns_geometry[, \"hair\"])} for each of objects whose instances show geometry in an evaluated "
     "depsgraph: the world box of those instances (nested ones count) and whether the object shows geometry of its "
     "own; (None, ...) when it makes more than limit (1,000,000; 0: no limit), or (None, ..., \"hair\") when an "
     "instance draws hair, which is not read. Each object is expanded alone: the cost is what "
     "it makes, never the scene's. None when the depsgraph is not fully evaluated."},
    {"shader_state", reinterpret_cast<PyCFunction>(live_shader_state), METH_VARARGS,
     "shader_state(depsgraph[, view3d]): (materials EEVEE asks for, missing, queued, [their session UIDs], [the "
     "missing ones' UIDs], [failed ones' names], [the drawn world's name if it failed], [pending names], whether "
     "that world is the view layer's override) or None."},
    {"shown_state", reinterpret_cast<PyCFunction>(live_shown_state), METH_VARARGS,
     "shown_state(depsgraph): a number that changes when which objects it shows changes (never selection), or None."},
    {"update_count",
     reinterpret_cast<PyCFunction>(live_update_count),
     METH_O,
     "update_count(depsgraph): the count Blender gave depsgraph's last evaluation, which no evaluation of any "
     "depsgraph shares (a fact read from it is current while it holds), or None when it is not fully evaluated."},
    {"window_pixels",
     reinterpret_cast<PyCFunction>(live_window_pixels),
     METH_O,
     "window_pixels(window): (width, height, RGBA bytes bottom row first) of window drawn again now into an "
     "offscreen, never read from the front buffer."},
    {"text_info_end",
     reinterpret_cast<PyCFunction>(live_text_info_end),
     METH_O,
     "text_info_end(region): region pixels from its bottom to the lowest point of the text info the 3D view drew "
     "in region's last draw (the visible top when it drew none), or None before its first draw."},
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_bpy._angelo_live",
    nullptr,
    0,
    methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

PyObject *BPY_angelo_live_module()
{
  return PyModule_Create(&module);
}

}  // namespace blender
