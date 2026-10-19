/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * ``_angelo_stage.layer_flags`` and ``set_layer_flags``: a view layer's exclusion flags,
 * read and written natively for agent staging and rollback.
 */

#include <Python.h>

#include "BLI_span.hh"
#include "BLI_threads.h"
#include "BLI_utildefines.h"
#include "BLI_vector.hh"

#include "BKE_global.hh"
#include "BKE_layer.hh"
#include "BKE_lib_id.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_build.hh"

#include "DNA_layer_types.h"
#include "DNA_scene_types.h"

#include "ED_object.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "bpy_angelo_stage_layers.hh"
#include "bpy_rna.hh"

namespace blender {

static bool require_main_thread()
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "Native stage ownership requires Blender's main thread");
    return false;
  }
  return true;
}

/* -------------------------------------------------------------------- */
/** \name View-layer exclusion flags
 *
 * A view layer's layer collections below its master, depth first (the order of
 * ``LayerCollection.children``), each with its depth, collection name, library flag and
 * session UID, and two bits: 1 is the exclude flag RNA exposes, 2 is
 * LAYER_COLLECTION_PREVIOUSLY_EXCLUDED, which RNA does not expose. A collection excluded
 * before its parent keeps that flag, so including the parent again leaves it excluded.
 * Agent staging reads both to predict what a program's exclusion edits give, and a
 * rolled-back commit writes both back, because restoring through RNA re-runs the
 * recursive exclusion update and changes the descendants. Both calls sync the view
 * layer first.
 * \{ */

static constexpr int layer_exclude_bit = 1;
static constexpr int layer_previously_excluded_bit = 2;

static ViewLayer *view_layer_from_python(PyObject *py_scene, const char *name, Scene **r_scene)
{
  ID *id = nullptr;
  if (!BPy_StructRNA_Check(py_scene) ||
      pyrna_struct_validity_check(reinterpret_cast<BPy_StructRNA *>(py_scene)) == -1 ||
      !pyrna_id_FromPyObject(py_scene, &id) || id == nullptr)
  {
    if (!PyErr_Occurred()) {
      PyErr_SetString(PyExc_TypeError, "Expected a live Blender ID");
    }
    return nullptr;
  }
  if (GS(id->name) != ID_SCE || ID_IS_LINKED(id)) {
    PyErr_SetString(PyExc_TypeError, "Expected a local scene");
    return nullptr;
  }
  Scene *scene = reinterpret_cast<Scene *>(id);
  ViewLayer *view_layer = BKE_view_layer_find(scene, name);
  if (view_layer == nullptr) {
    PyErr_SetString(PyExc_KeyError, "The scene has no view layer of that name");
    return nullptr;
  }
  /* As RNA does before any layer-tree access: a collection linked, unlinked or removed since
   * the last sync would otherwise be missing, or read through a dangling layer collection. */
  if (!BKE_view_layer_synced_ensure(*G_MAIN, scene, view_layer)) {
    PyErr_SetString(PyExc_RuntimeError,
                    "The view layer cannot be synced now; nothing was read or written");
    return nullptr;
  }
  *r_scene = scene;
  return view_layer;
}

static LayerCollection *view_layer_master(ViewLayer *view_layer)
{
  return static_cast<LayerCollection *>(view_layer->layer_collections.first);
}

static bool collect_layer_flags(LayerCollection &parent, const int depth, PyObject *list)
{
  for (LayerCollection &child : parent.layer_collections) {
    const int bits = ((child.flag & LAYER_COLLECTION_EXCLUDE) ? layer_exclude_bit : 0) |
                     ((child.flag & LAYER_COLLECTION_PREVIOUSLY_EXCLUDED) ?
                          layer_previously_excluded_bit :
                          0);
    const ID &id = child.collection->id;
    /* (depth, name, bits, linked, session UID): one row per layer collection, so callers
     * need no RNA walk. */
    PyObject *item = Py_BuildValue("(isiOI)",
                                   depth,
                                   id.name + 2,
                                   bits,
                                   ID_IS_LINKED(&id) ? Py_True : Py_False,
                                   id.session_uid);
    if (item == nullptr || PyList_Append(list, item) == -1) {
      Py_XDECREF(item);
      return false;
    }
    Py_DECREF(item);
    if (!collect_layer_flags(child, depth + 1, list)) {
      return false;
    }
  }
  return true;
}

static int64_t count_layer_collections(LayerCollection &parent)
{
  int64_t count = 0;
  for (LayerCollection &child : parent.layer_collections) {
    count += 1 + count_layer_collections(child);
  }
  return count;
}

static void write_layer_flags(LayerCollection &parent, const Span<int> flags, int64_t &index)
{
  for (LayerCollection &child : parent.layer_collections) {
    const int bits = flags[index++];
    SET_FLAG_FROM_TEST(child.flag, (bits & layer_exclude_bit) != 0, LAYER_COLLECTION_EXCLUDE);
    SET_FLAG_FROM_TEST(child.flag,
                       (bits & layer_previously_excluded_bit) != 0,
                       LAYER_COLLECTION_PREVIOUSLY_EXCLUDED);
    write_layer_flags(child, flags, index);
  }
}

PyObject *BPY_angelo_stage_layer_flags(PyObject * /*self*/, PyObject *args)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  PyObject *py_scene;
  const char *name;
  if (!PyArg_ParseTuple(args, "Os:layer_flags", &py_scene, &name)) {
    return nullptr;
  }
  Scene *scene;
  ViewLayer *view_layer = view_layer_from_python(py_scene, name, &scene);
  if (view_layer == nullptr) {
    return nullptr;
  }
  PyObject *list = PyList_New(0);
  if (list == nullptr) {
    return nullptr;
  }
  LayerCollection *master = view_layer_master(view_layer);
  if (master != nullptr && !collect_layer_flags(*master, 1, list)) {
    Py_DECREF(list);
    return nullptr;
  }
  return list;
}

PyObject *BPY_angelo_stage_set_layer_flags(PyObject * /*self*/, PyObject *args)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  PyObject *py_scene, *py_flags;
  const char *name;
  if (!PyArg_ParseTuple(args, "OsO:set_layer_flags", &py_scene, &name, &py_flags)) {
    return nullptr;
  }
  Scene *scene;
  ViewLayer *view_layer = view_layer_from_python(py_scene, name, &scene);
  if (view_layer == nullptr) {
    return nullptr;
  }
  LayerCollection *master = view_layer_master(view_layer);
  PyObject *sequence = PySequence_Fast(py_flags, "Expected a flag for every layer collection");
  if (sequence == nullptr) {
    return nullptr;
  }
  const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
  if (master == nullptr || size != count_layer_collections(*master)) {
    Py_DECREF(sequence);
    PyErr_SetString(PyExc_ValueError, "The view layer's collections changed; no flag was written");
    return nullptr;
  }
  Vector<int> flags(size);
  for (Py_ssize_t i = 0; i < size; i++) {
    const long value = PyLong_AsLong(PySequence_Fast_GET_ITEM(sequence, i));
    if ((value == -1 && PyErr_Occurred()) || value < 0 || value > 3) {
      Py_DECREF(sequence);
      if (!PyErr_Occurred()) {
        PyErr_SetString(PyExc_ValueError, "Each flag is 0 to 3");
      }
      return nullptr;
    }
    flags[i] = int(value);
  }
  Py_DECREF(sequence);
  int64_t index = 0;
  write_layer_flags(*master, flags, index);
  /* As rna_LayerCollection_exclude_update: bases follow the flags on the next sync. */
  Main *bmain = G_MAIN;
  BKE_view_layer_need_resync_tag(view_layer);
  DEG_id_tag_update(&scene->id, ID_RECALC_BASE_FLAGS);
  BKE_view_layer_synced_ensure(*bmain, scene, view_layer);
  FOREACH_OBJECT_BEGIN (bmain, scene, view_layer, ob) {
    DEG_id_tag_update(&ob->id, ID_RECALC_ANIMATION);
  }
  FOREACH_OBJECT_END;
  DEG_relations_tag_update(bmain);
  WM_main_add_notifier(NC_SCENE | ND_LAYER_CONTENT, nullptr);
  ed::object::base_active_refresh(bmain, scene, view_layer);
  Py_RETURN_NONE;
}

/** \} */

}  // namespace blender
