/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Drawing a live part (or the artist's own scene, for the session rail's main picture) into an offscreen
 * (`_bpy._angelo_live.draw`).
 *
 * Through #ED_view3d_draw_offscreen_simple, which draws with a 3D View and region of its own: the artist's view, its
 * region and its matrices are never written (break-it P1-2 M8, L5). Always Solid, with the artist's Solid settings
 * (lighting, colour, cavity, X-ray) as the shading override; overlays off except an optional grid floor; anti-aliasing
 * held to FXAA for the draw (a viewport image render otherwise draws every anti-aliasing sample, 8 by default, in one
 * call, break-it P1-2 M5). Main thread only; a part is drawn only once evaluated. With `background=False` what shows
 * no geometry is left transparent and geometry is drawn opaque, so the picture's alpha says where it shows geometry
 * (the Main picture's choice of view is read from it, REVIEW-MAIN-THUMB-2 H1).
 *
 * With `compile_ahead=True` the draw is EEVEE's, lit by the scene's lights and world, in the one offscreen mode that
 * is not a viewport image render: an XR surface's (#DRW_draw_render_loop_offscreen draws every other offscreen as
 * #DRWContext::VIEWPORT_RENDER). EEVEE then queues every material shader it has not compiled to Blender's background
 * compilation and draws placeholders, where an image render (`angelo_view`'s MATERIAL and LIT, and any other offscreen
 * draw) compiles each one on the main thread inside the call (`use_deferred_compilation =
 * !is_viewport_image_render`, eevee_material.cc). Its pixels are never used: it only gets the shaders compiling
 * before a view needs them (`view_warm.py`). With `only`, every other object of the scene is hidden from this one
 * draw (its evaluated visibility flags cleared, then put back): the first draw of newly compiled shaders builds their
 * Metal pipeline states on the main thread, and drawing a few objects at a time spreads that over frames.
 */

#include <Python.h>

#include "BLI_threads.h"

#include "BKE_global.hh"
#include "BKE_material.hh"
#include "BKE_object.hh"
#include "BKE_particle.h"
#include "BKE_scene.hh"

#include "BLI_index_range.hh"
#include "BLI_listbase_iterator.hh"
#include "BLI_set.hh"
#include "BLI_vector.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_query.hh"

#include "DNA_layer_types.h"
#include "DNA_material_types.h"
#include "DNA_object_types.h"
#include "DNA_curves_types.h"
#include "DNA_particle_types.h"
#include "DNA_pointcloud_types.h"
#include "DNA_volume_types.h"
#include "DNA_world_types.h"
#include "DNA_scene_types.h"
#include "DNA_userdef_types.h"
#include "DNA_view3d_enums.h"
#include "DNA_view3d_types.h"

#include "ED_view3d_offscreen.hh"

#include "GPU_framebuffer.hh"
#include "GPU_material.hh"
#include "GPU_pass.hh"
#include "GPU_viewport.hh"

#include "../generic/py_capi_utils.hh"
#include "../gpu/gpu_py_offscreen.hh"
#include "../mathutils/mathutils.hh"

#include "bpy_angelo_live_part.hh"

namespace blender {

PyObject *live_draw(PyObject * /*self*/, PyObject *args, PyObject *kwds)
{
  PyObject *target, *py_offscreen, *py_view3d, *py_view_layer = Py_None, *py_only = Py_None;
  MatrixObject *view_matrix, *projection_matrix;
  bool grid = false, background = true, compile_ahead = false, lit = true;
  static const char *keywords[] = {"target",
                                   "offscreen",
                                   "view3d",
                                   "view_matrix",
                                   "projection_matrix",
                                   "view_layer",
                                   "grid",
                                   "background",
                                   "compile_ahead",
                                   "only",
                                   "lit",
                                   nullptr};
  if (!PyArg_ParseTupleAndKeywords(args,
                                   kwds,
                                   "OOOO&O&|$OO&O&O&OO&:draw",
                                   const_cast<char **>(keywords),
                                   &target,
                                   &py_offscreen,
                                   &py_view3d,
                                   Matrix_Parse4x4,
                                   &view_matrix,
                                   Matrix_Parse4x4,
                                   &projection_matrix,
                                   &py_view_layer,
                                   PyC_ParseBool,
                                   &grid,
                                   PyC_ParseBool,
                                   &background,
                                   PyC_ParseBool,
                                   &compile_ahead,
                                   &py_only,
                                   PyC_ParseBool,
                                   &lit))
  {
    return nullptr;
  }
  if (!live_require_main_thread()) {
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
  /* Read only: the shading override and the clip range are copied from it. */
  View3D *v3d = static_cast<View3D *>(PyC_RNA_AsPointer(py_view3d, "SpaceView3D"));
  if (v3d == nullptr) {
    return nullptr;
  }
  Depsgraph *depsgraph;
  Scene *scene;
  if (live_is_part(target)) {
    LivePart *part = live_evaluated_part(target);
    if (part == nullptr) {
      return nullptr;
    }
    depsgraph = part->depsgraph;
    scene = part->scene;
  }
  else {
    scene = static_cast<Scene *>(PyC_RNA_AsPointer(target, "Scene"));
    ViewLayer *view_layer = py_view_layer != Py_None ?
                                static_cast<ViewLayer *>(PyC_RNA_AsPointer(py_view_layer, "ViewLayer")) :
                                nullptr;
    if (scene == nullptr || view_layer == nullptr) {
      if (!PyErr_Occurred()) {
        PyErr_SetString(PyExc_TypeError, "Drawing a scene needs its view_layer");
      }
      return nullptr;
    }
    depsgraph = BKE_scene_get_depsgraph(scene, view_layer);
    if (depsgraph == nullptr || !DEG_is_fully_evaluated(depsgraph)) {
      PyErr_SetString(PyExc_RuntimeError, "The scene is not evaluated yet");
      return nullptr;
    }
  }
  if (ED_view3d_draw_offscreen_check_nested()) {
    PyErr_SetString(PyExc_RuntimeError, "Nested off-screen drawing not supported");
    return nullptr;
  }
  View3DShading shading = v3d->shading;
  shading.type = compile_ahead ? OB_MATERIAL : OB_SOLID;
  if (compile_ahead) {
    /* The look the view asked for: LIT with the scene's lights and world, MATERIAL with neither, so warming a
     * MATERIAL view never compiles a world it does not show (EEVEE compiles world shaders synchronously in any draw,
     * eevee_world.cc; BREAKIT-viewwarm VW-M-1). */
    if (lit) {
      shading.flag |= V3D_SHADING_SCENE_LIGHTS | V3D_SHADING_SCENE_WORLD;
    }
    else {
      shading.flag &= ~(V3D_SHADING_SCENE_LIGHTS | V3D_SHADING_SCENE_WORLD);
    }
  }
  if (!background) {
    /* A picture that says where geometry shows, never how it is shaded: opaque, one colour, no X-ray and no
     * culled back faces, so see-through shading never hides geometry from it (REVIEW-MAIN-THUMB-3 M1). */
    shading.flag &= ~(V3D_SHADING_XRAY | V3D_SHADING_BACKFACE_CULLING);
    shading.color_type = V3D_SHADING_SINGLE_COLOR;
    shading.light = V3D_LIGHTING_FLAT;
  }
  const unsigned int flags = V3D_OFSDRAW_OVERRIDE_SCENE_SETTINGS | (grid ? V3D_OFSDRAW_SHOW_GRIDFLOOR : 0);
  struct Restore {   /* Also when drawing throws (break-it P1-3 L7). */
    char antialiasing = U.viewport_aa;
    ~Restore()
    {
      U.viewport_aa = antialiasing;
    }
  } restore;
  U.viewport_aa = SCE_DISPLAY_AA_FXAA;
  if (!offscreen->viewport) {
    offscreen->viewport = GPU_viewport_create();
  }
  else {
    GPU_viewport_tag_update(offscreen->viewport);
  }
  Set<const Object *> shown;
  if (py_only != Py_None) {
    PyObject *items = PySequence_Fast(py_only, "draw: only must be a sequence of objects");
    if (items == nullptr) {
      return nullptr;
    }
    for (Py_ssize_t index = 0; index < PySequence_Fast_GET_SIZE(items); index++) {
      Object *object = static_cast<Object *>(
          PyC_RNA_AsPointer(PySequence_Fast_GET_ITEM(items, index), "Object"));
      if (object == nullptr) {
        Py_DECREF(items);
        return nullptr;
      }
      shown.add(object);
    }
    Py_DECREF(items);
  }
  struct Hidden { /* Put back also when drawing throws. */
    Vector<std::pair<Object *, decltype(Object::base_flag)>> objects;
    ~Hidden()
    {
      for (const auto &[object, flag] : objects) {
        object->base_flag = flag;
      }
    }
  } hidden;
  if (py_only != Py_None) {
    DEGObjectIterSettings settings = {nullptr};
    settings.depsgraph = depsgraph;
    settings.flags = DEG_ITER_OBJECT_FLAG_LINKED_DIRECTLY | DEG_ITER_OBJECT_FLAG_LINKED_VIA_SET |
                     DEG_ITER_OBJECT_FLAG_VISIBLE;
    DEG_OBJECT_ITER_BEGIN (&settings, ob) {
      if (!shown.contains(DEG_get_original(ob))) {
        hidden.objects.append({ob, ob->base_flag});
      }
    }
    DEG_OBJECT_ITER_END;
    for (const auto &[object, flag] : hidden.objects) {
      object->base_flag &= ~(BASE_ENABLED_AND_MAYBE_VISIBLE_IN_VIEWPORT | BASE_ENABLED_AND_VISIBLE_IN_DEFAULT_VIEWPORT);
    }
  }
  GPU_offscreen_bind(offscreen->ofs, true);
  ED_view3d_draw_offscreen_simple(depsgraph,
                                  scene,
                                  &shading,
                                  nullptr,
                                  compile_ahead ? OB_MATERIAL : OB_SOLID,
                                  v3d->object_type_exclude_viewport,
                                  v3d->object_type_exclude_select,
                                  GPU_offscreen_width(offscreen->ofs),
                                  GPU_offscreen_height(offscreen->ofs),
                                  flags,
                                  reinterpret_cast<const float (*)[4]>(view_matrix->matrix),
                                  reinterpret_cast<const float (*)[4]>(projection_matrix->matrix),
                                  v3d->clip_start,
                                  v3d->clip_end,
                                  0.0f,
                                  compile_ahead,
                                  !compile_ahead,
                                  background,
                                  "",
                                  true,
                                  nullptr,
                                  offscreen->ofs,
                                  offscreen->viewport);
  GPU_offscreen_unbind(offscreen->ofs, true);
  Py_RETURN_NONE;
}

/** The material slots EEVEE asks a GPU material for when it draws \a ob in a material preview (eevee_sync.cc,
 * eevee_material.cc): a mesh's used slots unless it is displayed below Solid, the first slot of hair curves, a point
 * cloud or a volume, and the own slot of a hair particle system EEVEE draws (foreach_hair_particle, eevee_sync.cc:
 * enabled in the viewport, drawn as paths; none in particle edit mode, where what shows depends on its settings:
 * VW9-H-1b, a slot no draw asks for was waited on forever). Other types (Grease Pencil, empties) ask for none. */
static Vector<int, 8> eevee_material_slots(Object *ob)
{
  Vector<int, 8> slots;
  switch (ob->type) {
    case OB_MESH:
      if (ob->dt >= OB_SOLID) {
        for (const int slot : IndexRange(BKE_object_material_used_with_fallback_eval(*ob))) {
          slots.append(slot + 1);
        }
      }
      for (ParticleSystem &psys : ob->particlesystem) {
        const ParticleSettings *part = psys.part;
        if (part == nullptr || part->type != PART_HAIR || ob->mode == OB_MODE_PARTICLE_EDIT ||
            !psys_check_enabled(ob, &psys, false))
        {
          continue;
        }
        const int draw_as = part->draw_as == PART_DRAW_REND ? part->ren_as : part->draw_as;
        if (draw_as == PART_DRAW_PATH) {
          slots.append(part->omat);
        }
      }
      break;
    case OB_CURVES:
      slots.append(CURVES_MATERIAL_NR);
      break;
    case OB_POINTCLOUD:
      slots.append(POINTCLOUD_MATERIAL_NR);
      break;
    case OB_VOLUME:
      slots.append(VOLUME_MATERIAL_NR);
      break;
    default:
      break;
  }
  return slots;
}

PyObject *live_shader_state(PyObject * /*self*/, PyObject *args)
{
  /* The materials EEVEE asks for when it draws an evaluated depsgraph's visible objects and instances
   * (`eevee_material_slots`, the view layer's material override, the 3D view's excluded types): how many, how many
   * have no GPU material yet (a draw has to queue it), how many have one queued for Blender's background compile
   * (its base pass; the optional optimization pass never counts), the names of those two kinds, the names of the
   * missing ones alone (what a queue draw has to draw, REVIEW-VIEW-SPREAD-2 HA), the names of those whose compile
   * failed (EEVEE draws its error material for them, eevee_material.cc: a view says so, BREAKIT-viewwarm VW-H-1),
   * and the name of the world EEVEE draws (the view layer's world override, else the scene's, eevee_world.cc,
   * VW9-H-3) when its compile failed (EEVEE draws its default world instead, VW8-H-1), and whether that world is the
   * override (Material Preview draws an override too, else a studio light: VW10-H-3). Materials are named by their
   * original's session UID, never their name (linked libraries hold same-named ones, VW9-H-1a); their names last,
   * for a person to read. A view of this
   * scene waits only on these, never on other scenes' or optimization compiles, nor on a material EEVEE never asks
   * for (REVIEW-VIEW-WARM H2, REVIEW-VIEW-WARM-2 H-1). The set of materials is kept until the depsgraph changes (a
   * walk of 1M instances takes ~80 ms, REVIEW-VIEW-WARM-2 M-3); their status is read on every call, since a draw
   * can give a material its GPU material from a pass Blender already compiled, which changes neither the depsgraph
   * nor any compile count (a value edit, a copy, an undo: REVIEW-VIEW-SPREAD H3). */
  PyObject *py_depsgraph, *py_view3d = Py_None;
  if (!PyArg_ParseTuple(args, "O|O:shader_state", &py_depsgraph, &py_view3d)) {
    return nullptr;
  }
  if (!live_require_main_thread()) {
    return nullptr;
  }
  Depsgraph *depsgraph = static_cast<Depsgraph *>(PyC_RNA_AsPointer(py_depsgraph, "Depsgraph"));
  if (depsgraph == nullptr) {
    return nullptr;
  }
  int excluded = 0;
  if (py_view3d != Py_None) {
    const View3D *v3d = static_cast<const View3D *>(PyC_RNA_AsPointer(py_view3d, "SpaceView3D"));
    if (v3d == nullptr) {
      return nullptr;
    }
    excluded = v3d->object_type_exclude_viewport;
  }
  if (!DEG_is_fully_evaluated(depsgraph)) {
    Py_RETURN_NONE;
  }
  if (G.background) {
    /* No window loop finishes the compiles that are ready (#GPU_render_step does after each frame). */
    GPU_pass_cache_update();
  }
  static struct {
    const Main *main = nullptr; /* A file load or an undo replaces it: what was kept went with it (VW8-M-4). */
    const Depsgraph *depsgraph = nullptr;
    uint64_t update = 0;
    int excluded = 0;
    Vector<Material *> materials; /* Evaluated: valid while the depsgraph is not evaluated again. */
  } kept;
  const uint64_t update = DEG_get_update_count(depsgraph);
  if (kept.main != G_MAIN || kept.depsgraph != depsgraph || kept.update != update || kept.excluded != excluded) {
    const ViewLayer *view_layer = DEG_get_evaluated_view_layer(depsgraph);
    Material *override = view_layer->mat_override != nullptr ?
                             DEG_get_evaluated(depsgraph, view_layer->mat_override) :
                             nullptr;
    Set<const Material *> seen;
    kept.materials.clear();
    DEGObjectIterSettings settings = {nullptr};
    settings.depsgraph = depsgraph;
    settings.flags = DEG_ITER_OBJECT_FLAG_LINKED_DIRECTLY | DEG_ITER_OBJECT_FLAG_LINKED_VIA_SET |
                     DEG_ITER_OBJECT_FLAG_VISIBLE | DEG_ITER_OBJECT_FLAG_DUPLI;
    DEG_OBJECT_ITER_BEGIN (&settings, ob) {
      if (excluded & (1 << ob->type)) {
        continue;
      }
      for (const int slot : eevee_material_slots(ob)) {
        Material *material = override ? override : BKE_object_material_get_eval(ob, short(slot));
        if (material != nullptr && seen.add(material)) { /* None: EEVEE's default, compiled with the engine. */
          kept.materials.append(material);
        }
      }
    }
    DEG_OBJECT_ITER_END;
    kept.main = G_MAIN;
    kept.depsgraph = depsgraph;
    kept.update = update;
    kept.excluded = excluded;
  }
  int missing = 0, queued = 0;
  PyObject *names = PyList_New(0);  /* The missing and queued ones' session UIDs. */
  PyObject *absent = PyList_New(0); /* The missing ones' session UIDs. */
  PyObject *failed = PyList_New(0); /* Those whose compile failed: names. */
  PyObject *readable = PyList_New(0); /* The missing and queued ones' names. */
  if (names == nullptr || absent == nullptr || failed == nullptr || readable == nullptr) {
    Py_XDECREF(names);
    Py_XDECREF(absent);
    Py_XDECREF(failed);
    Py_XDECREF(readable);
    return nullptr;
  }
  for (Material *material : kept.materials) {
    bool pending = material->gpumaterial.first == nullptr, broken = false;
    if (pending) {
      missing++;
    }
    else {
      for (LinkData &link : material->gpumaterial) {
        const GPUMaterialStatus status = GPU_material_status(static_cast<GPUMaterial *>(link.data));
        broken |= status == GPU_MAT_FAILED;
        if (status == GPU_MAT_QUEUED && !pending) {
          queued++;
          pending = true;
        }
      }
    }
    if (!broken && !pending) {
      continue;
    }
    PyObject *name = PyC_UnicodeFromBytes(material->id.name + 2); /* Never fails on a name that is no UTF-8. */
    PyObject *uid = PyLong_FromUnsignedLong(DEG_get_original(material)->id.session_uid);
    const bool ok = name != nullptr && uid != nullptr && (!broken || PyList_Append(failed, name) == 0) &&
                    (!pending || (PyList_Append(names, uid) == 0 && PyList_Append(readable, name) == 0 &&
                                  (material->gpumaterial.first != nullptr || PyList_Append(absent, uid) == 0)));
    Py_XDECREF(name);
    Py_XDECREF(uid);
    if (!ok) {
      Py_DECREF(names);
      Py_DECREF(absent);
      Py_DECREF(failed);
      Py_DECREF(readable);
      return nullptr;
    }
  }
  PyObject *world_failed = PyList_New(0);
  if (world_failed == nullptr) {
    Py_DECREF(names);
    Py_DECREF(absent);
    Py_DECREF(failed);
    Py_DECREF(readable);
    return nullptr;
  }
  const Scene *scene = DEG_get_evaluated_scene(depsgraph);
  const ViewLayer *layer = DEG_get_evaluated_view_layer(depsgraph);
  World *world = layer != nullptr && layer->world_override != nullptr ?
                     DEG_get_evaluated(depsgraph, layer->world_override) :
                     (scene != nullptr ? scene->world : nullptr);
  if (world != nullptr) {
    for (LinkData &link : world->gpumaterial) {
      if (GPU_material_status(static_cast<GPUMaterial *>(link.data)) == GPU_MAT_FAILED) {
        PyObject *name = PyC_UnicodeFromBytes(world->id.name + 2);
        if (name == nullptr || PyList_Append(world_failed, name) != 0) {
          Py_XDECREF(name);
          Py_DECREF(names);
          Py_DECREF(absent);
          Py_DECREF(failed);
          Py_DECREF(readable);
          Py_DECREF(world_failed);
          return nullptr;
        }
        Py_DECREF(name);
        break;
      }
    }
  }
  const bool overridden = layer != nullptr && layer->world_override != nullptr;
  return Py_BuildValue("(iiiNNNNNO)",
                       int(kept.materials.size()),
                       missing,
                       queued,
                       names,
                       absent,
                       failed,
                       world_failed,
                       readable,
                       overridden ? Py_True : Py_False);
}

PyObject *live_shown_state(PyObject * /*self*/, PyObject *args)
{
  /* A number that changes when which objects an evaluated depsgraph shows in a 3D view changes, and only then: each
   * object's own visibility flags (an eye toggle among them, which reaches Python as a bare Scene update, the same
   * as a selection: the session rail's Main picture redraws on the one and not the other), never its selection. */
  PyObject *py_depsgraph;
  if (!PyArg_ParseTuple(args, "O:shown_state", &py_depsgraph)) {
    return nullptr;
  }
  if (!live_require_main_thread()) {
    return nullptr;
  }
  Depsgraph *depsgraph = static_cast<Depsgraph *>(PyC_RNA_AsPointer(py_depsgraph, "Depsgraph"));
  if (depsgraph == nullptr) {
    return nullptr;
  }
  if (!DEG_is_fully_evaluated(depsgraph)) {
    Py_RETURN_NONE;
  }
  constexpr int shown = BASE_ENABLED_AND_MAYBE_VISIBLE_IN_VIEWPORT | BASE_ENABLED_AND_VISIBLE_IN_DEFAULT_VIEWPORT;
  uint64_t hash = 1469598103934665603ull;
  DEGObjectIterSettings settings = {nullptr};
  settings.depsgraph = depsgraph;
  settings.flags = DEG_ITER_OBJECT_FLAG_LINKED_DIRECTLY | DEG_ITER_OBJECT_FLAG_LINKED_VIA_SET;
  DEG_OBJECT_ITER_BEGIN (&settings, ob) {
    const uint64_t item = (uint64_t(DEG_get_original(ob)->id.session_uid) << 8) | uint64_t(ob->base_flag & shown);
    hash = (hash ^ item) * 1099511628211ull;
  }
  DEG_OBJECT_ITER_END;
  return PyLong_FromUnsignedLongLong(hash);
}

}  // namespace blender
