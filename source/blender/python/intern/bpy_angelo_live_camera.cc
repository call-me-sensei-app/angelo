/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * The camera preview's picture (`_bpy._angelo_live.camera_draw`, `camera_preview.py`): a scene camera's view of the
 * artist's own scene drawn into an offscreen, lit by the scene's lights and world, while the 3D view itself stays in
 * Solid (doc/angelo/camera_preview.rst).
 *
 * Through #ED_view3d_draw_offscreen_simple with a 3D View and region of its own: no 3D view, region or scene setting
 * is written, so drawing the preview never sends a notifier, never makes an Undo step and never dirties the file.
 * The camera's view and projection are computed here as Blender's own camera render does
 * (#ED_view3d_draw_offscreen_imbuf_simple): the evaluated camera, the render resolution's aspect and pixel aspect.
 * Any object Render looks through is accepted (an Empty or a light as the scene camera: Blender's default lens or
 * the spot's cone, #BKE_camera_params_from_object).
 *
 * The draw is the progressive one of an XR surface, never a viewport image render (#DRW_draw_render_loop_offscreen
 * draws every other offscreen as #DRWContext::VIEWPORT_RENDER): each call renders one more anti-aliasing sample into
 * the engine data the offscreen's own #GPUViewport keeps, and the engine starts again by itself when the depsgraph
 * changed (EEVEE: `depsgraph_last_update_ != DEG_get_update_count`, eevee_instance.cc). So the caller chooses how
 * many samples a picture gets and when: one call per idle moment, never a long synchronous image render, and
 * material shaders EEVEE has not compiled yet are queued to Blender's background compile with placeholders drawn
 * meanwhile (`use_deferred_compilation = !is_viewport_image_render`, eevee_material.cc), never compiled inside the
 * call. The call returns the depsgraph's update count, so the caller knows when the engine started again.
 *
 * Looks: `LIT` draws EEVEE (Material Preview with the scene's lights and world) whatever engine the scene renders
 * with, the one real-time approximation of a Cycles render; `SOLID` draws Workbench with the scene's own Workbench
 * render settings (`scene.display.shading`), for a scene that renders with Workbench.
 *
 * `shadow_pool`: when above 0, EEVEE's shadow page pool for this draw in megabytes (the evaluated scene's
 * `eevee.shadow_pool_size`, put back after the call, also when drawing throws). Each offscreen keeps an EEVEE
 * instance of its own with a pool of the scene's size (512 MB by default) although a small picture needs a fraction
 * of it. Main thread only, never inside another offscreen draw.
 */

#include <Python.h>

#include "BKE_camera.h"
#include "BKE_scene.hh"

#include "BLI_listbase_iterator.hh"
#include "BLI_math_matrix.h"
#include "BLI_rect.h"
#include "BLI_utildefines.h"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_query.hh"

#include "DNA_camera_types.h"
#include "DNA_layer_types.h"
#include "DNA_object_types.h"
#include "DNA_screen_types.h"
#include "DNA_scene_types.h"
#include "DNA_view3d_enums.h"
#include "DNA_view3d_types.h"

#include "ED_screen_types.hh"
#include "ED_view3d_offscreen.hh"

#include "GPU_framebuffer.hh"
#include "GPU_viewport.hh"

#include "../generic/py_capi_utils.hh"
#include "../gpu/gpu_py_offscreen.hh"

#include "bpy_angelo_live_part.hh"

namespace blender {

PyObject *live_camera_draw(PyObject * /*self*/, PyObject *args, PyObject *kwds)
{
  PyObject *py_scene, *py_view_layer, *py_camera, *py_offscreen;
  const char *look = "LIT";
  int shadow_pool = 0;
  static const char *keywords[] = {
      "scene", "view_layer", "camera", "offscreen", "look", "shadow_pool", nullptr};
  if (!PyArg_ParseTupleAndKeywords(args,
                                   kwds,
                                   "OOOO|$si:camera_draw",
                                   const_cast<char **>(keywords),
                                   &py_scene,
                                   &py_view_layer,
                                   &py_camera,
                                   &py_offscreen,
                                   &look,
                                   &shadow_pool))
  {
    return nullptr;
  }
  if (!live_require_main_thread()) {
    return nullptr;
  }
  const bool lit = STREQ(look, "LIT");
  if (!lit && !STREQ(look, "SOLID")) {
    PyErr_SetString(PyExc_ValueError, "camera_draw: look must be 'LIT' or 'SOLID'");
    return nullptr;
  }
  if (shadow_pool < 0) {
    PyErr_SetString(PyExc_ValueError, "camera_draw: shadow_pool must be 0 (the scene's) or more megabytes");
    return nullptr;
  }
  if (!BPyGPUOffScreen_Check(py_offscreen)) {
    PyErr_SetString(PyExc_TypeError, "Expected a gpu.types.GPUOffScreen");
    return nullptr;
  }
  BPyGPUOffScreen *offscreen = reinterpret_cast<BPyGPUOffScreen *>(py_offscreen);
  if (offscreen->ofs == nullptr) {
    PyErr_SetString(PyExc_ReferenceError, "The offscreen was freed");
    return nullptr;
  }
  Scene *scene = static_cast<Scene *>(PyC_RNA_AsPointer(py_scene, "Scene"));
  if (scene == nullptr) {
    return nullptr;
  }
  ViewLayer *view_layer = static_cast<ViewLayer *>(PyC_RNA_AsPointer(py_view_layer, "ViewLayer"));
  if (view_layer == nullptr) {
    return nullptr;
  }
  Object *camera = static_cast<Object *>(PyC_RNA_AsPointer(py_camera, "Object"));
  if (camera == nullptr) {
    return nullptr;
  }
  Depsgraph *depsgraph = BKE_scene_get_depsgraph(scene, view_layer);
  if (depsgraph == nullptr || !DEG_is_fully_evaluated(depsgraph)) {
    PyErr_SetString(PyExc_RuntimeError, "The scene is not evaluated yet");
    return nullptr;
  }
  Object *camera_eval = DEG_get_evaluated(depsgraph, camera);
  if (camera_eval == nullptr || camera_eval == camera) {
    PyErr_SetString(PyExc_RuntimeError, "camera_draw: the camera is not in this scene's view layer");
    return nullptr;
  }
  if (ED_view3d_draw_offscreen_check_nested()) {
    PyErr_SetString(PyExc_RuntimeError, "Nested off-screen drawing not supported");
    return nullptr;
  }
  const int width = GPU_offscreen_width(offscreen->ofs);
  const int height = GPU_offscreen_height(offscreen->ofs);

  /* The camera's view and projection, as #ED_view3d_draw_offscreen_imbuf_simple computes them. */
  float viewinv[4][4], viewmat[4][4];
  copy_m4_m4(viewinv, camera_eval->object_to_world().ptr());
  normalize_m4(viewinv);
  invert_m4_m4(viewmat, viewinv);
  CameraParams params;
  BKE_camera_params_init(&params);
  BKE_camera_params_from_object(&params, camera_eval);
  BKE_camera_params_compute_viewplane(&params, width, height, scene->r.xasp, scene->r.yasp);
  BKE_camera_params_compute_matrix(&params);

  /* EEVEE's shadow pool for this draw only (see the file comment). */
  Scene *scene_eval = DEG_get_evaluated_scene(depsgraph);
  struct PoolRestore {
    Scene *scene;
    int size;
    ~PoolRestore()
    {
      if (scene != nullptr) {
        scene->eevee.shadow_pool_size = size;
      }
    }
  } pool_restore{shadow_pool > 0 ? scene_eval : nullptr, scene_eval->eevee.shadow_pool_size};
  if (shadow_pool > 0) {
    scene_eval->eevee.shadow_pool_size = shadow_pool;
  }

  if (!offscreen->viewport) {
    offscreen->viewport = GPU_viewport_create();
  }
  /* The engine data the viewport keeps accumulates samples until the depsgraph changes (EEVEE compares the
   * depsgraph's update count); #ED_view3d_draw_offscreen tags the viewport on every call, which nothing reads for
   * an offscreen. */
  GPU_offscreen_bind(offscreen->ofs, true);
  ED_view3d_draw_offscreen_simple(depsgraph,
                                  scene,
                                  nullptr,
                                  nullptr,
                                  lit ? OB_MATERIAL : OB_SOLID,
                                  0,
                                  0,
                                  width,
                                  height,
                                  V3D_OFSDRAW_NONE,
                                  viewmat,
                                  params.winmat,
                                  params.clip_start,
                                  params.clip_end,
                                  0.0f,
                                  true,
                                  false,
                                  true,
                                  "",
                                  true,
                                  camera,
                                  offscreen->ofs,
                                  offscreen->viewport);
  GPU_offscreen_unbind(offscreen->ofs, true);
  return PyLong_FromUnsignedLongLong(uint64_t(DEG_get_update_count(depsgraph)));
}

PyObject *live_region_tabs(PyObject * /*self*/, PyObject *py_area)
{
  /* The tabs Blender placed on this area for its hidden regions (a collapsed header, sidebar or toolbar: the small
   * arrows a click brings the region back with, #region_azone_tab_plus) and its full-screen corner, as
   * `[(x, y, width, height), ...]` in window pixels from the area's own action zones, so an overlay keeps clear of
   * them exactly (the camera preview, doc/angelo/camera_preview.rst). The edges of shown regions are left out:
   * they run along the regions an overlay already stays inside. */
  if (!live_require_main_thread()) {
    return nullptr;
  }
  const ScrArea *area = static_cast<const ScrArea *>(PyC_RNA_AsPointer(py_area, "Area"));
  if (area == nullptr) {
    return nullptr;
  }
  PyObject *result = PyList_New(0);
  if (result == nullptr) {
    return nullptr;
  }
  for (const AZone &zone : area->actionzones) {
    bool tab = zone.type == AZONE_FULLSCREEN;
    if (zone.type == AZONE_REGION && zone.region != nullptr) {
      tab = (zone.region->flag & (RGN_FLAG_HIDDEN | RGN_FLAG_TOO_SMALL)) != 0;
    }
    if (!tab || BLI_rcti_is_empty(&zone.rect)) {
      continue;
    }
    PyObject *rect = Py_BuildValue("(iiii)",
                                   zone.rect.xmin,
                                   zone.rect.ymin,
                                   BLI_rcti_size_x(&zone.rect),
                                   BLI_rcti_size_y(&zone.rect));
    if (rect == nullptr || PyList_Append(result, rect) != 0) {
      Py_XDECREF(rect);
      Py_DECREF(result);
      return nullptr;
    }
    Py_DECREF(rect);
  }
  return result;
}

}  // namespace blender
