/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Instance facts (`_bpy._angelo_live.instance_bounds`, `update_count`): for each object asked about that makes
 * instances, the world box of what they show, expanded alone, and the count of the evaluation they were read from.
 * An object's own facts (`Object.dimensions`, its bound box) leave instances out, so a Geometry Nodes instancer read 0
 * and a curve leaving only instances its control points padded by their radius (break-it REVIEW-BOUNDS M3). Moved
 * out of bpy_angelo_live_bounds.cc, which shares its geometry rule, to keep both within the size rule.
 */

#include <cstdint>
#include <optional>

#include "BLI_bounds_types.hh"
#include "BLI_listbase_iterator.hh"
#include "BLI_math_matrix.hh"
#include "BLI_math_vector.h"
#include "BLI_utildefines.h"

#include "BKE_curve.hh"
#include "BKE_curves.hh"
#include "BKE_duplilist.hh"
#include "BKE_geometry_set_instances.hh"
#include "BKE_layer.hh"
#include "BKE_object.hh"
#include "BKE_object_types.hh"
#include "BKE_volume.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_query.hh"

#include "../generic/py_capi_utils.hh"

#include "DNA_curve_types.h"
#include "DNA_grease_pencil_types.h"
#include "DNA_layer_types.h"
#include "DNA_mesh_types.h"
#include "DNA_object_types.h"
#include "DNA_particle_types.h"
#include "DNA_pointcloud_types.h"
#include "DNA_volume_types.h"

#include "bpy_angelo_live_part.hh"

namespace blender {

/** Whether an instance draws the geometry it shows of its object: Blender's test on the instance's temporary object
 * (#BKE_object_visibility after #DEG_iterator_temp_object_from_dupli: an instanced object that itself makes instances
 * or particles draws its own geometry only with Show Instancer on; with its data replaced it has no geometry-set
 * instances, #BKE_object_replace_data_on_shallow_copy). REVIEW-INSTANCES-LATENCY L4. */
static bool dupli_shows_self(const DupliObject &dob, const eEvaluationMode mode)
{
  const Object &ob = *dob.ob;
  const bool replaced = dob.ob_data != nullptr && dob.ob_data != ob.data;
  const bool instancer = ob.particlesystem.first != nullptr || (ob.transflag & OB_DUPLI) ||
                         (!replaced && bke::object_has_geometry_set_instances(ob));
  return !instancer || (ob.duplicator_visibility_flag &
                        (mode == DAG_EVAL_RENDER ? OB_DUPLI_FLAG_RENDER : OB_DUPLI_FLAG_VIEWPORT));
}

/** Whether an instance draws hair: strands are no dupli and no evaluated geometry here, so what it shows cannot be
 * read (REVIEW-INSTANCES-LATENCY L-r2: a collection instance of a hair patch, Show Emitter off, read "makes none"). */
static bool dupli_draws_hair(const DupliObject &dob)
{
  if (dob.ob_data != nullptr && dob.ob_data != dob.ob->data) {
    return false; /* Its data replaced (a nodes instance): no particle system of its object is drawn. */
  }
  for (const ParticleSystem &psys : dob.ob->particlesystem) {
    if (psys.part != nullptr && psys.part->type == PART_HAIR && !(psys.flag & PSYS_DISABLED)) {
      return true;
    }
  }
  return false;
}

/** The bounds of the geometry one instance shows, before its matrix: its data when the instance replaced it (a mesh,
 * curves or points from nodes; #BKE_object_boundbox_get reads those by type), else the geometry its object owns, and
 * none for an object that draws no geometry (an armature's pose, a lattice's cage, an empty: REVIEW-INSTANCES-LATENCY
 * H1, as the depsgraph pass skips them). */
static std::optional<Bounds<float3>> dupli_bounds(const DupliObject &dob)
{
  if (dob.ob_data == nullptr || dob.ob_data == dob.ob->data) {
    if (!live_is_geometry(dob.ob)) {
      return std::nullopt;
    }
    return dob.ob->runtime->geometry_set_eval ? BKE_object_evaluated_geometry_bounds(dob.ob) :
                                                BKE_object_boundbox_get(dob.ob);
  }
  switch (GS(dob.ob_data->name)) {
    case ID_ME:
      return reinterpret_cast<const Mesh *>(dob.ob_data)->bounds_min_max();
    case ID_CU_LEGACY: {
      const Curve *curve = reinterpret_cast<const Curve *>(dob.ob_data);
      if (std::optional<Bounds<float3>> bounds = BKE_curve_minmax(curve, true)) {
        return bounds;
      }
      return curve->curve_eval ? curve->curve_eval->geometry.wrap().bounds_min_max(false) :
                                 std::optional<Bounds<float3>>();
    }
    case ID_CV:
      return reinterpret_cast<const Curves *>(dob.ob_data)->geometry.wrap().bounds_min_max();
    case ID_PT:
      return reinterpret_cast<const PointCloud *>(dob.ob_data)->bounds_min_max();
    case ID_VO:
      return BKE_volume_min_max(reinterpret_cast<const Volume *>(dob.ob_data));
    case ID_GP:
      return reinterpret_cast<const GreasePencil *>(dob.ob_data)->bounds_min_max_eval();
    default:
      return std::nullopt;
  }
}

static void merge_box(const Bounds<float3> &bounds, const float4x4 &matrix, float r_min[3], float r_max[3])
{
  for (int corner = 0; corner < 8; corner++) {
    const float3 local((corner & 1) ? bounds.max.x : bounds.min.x,
                       (corner & 2) ? bounds.max.y : bounds.min.y,
                       (corner & 4) ? bounds.max.z : bounds.min.z);
    minmax_v3v3_v3(r_min, r_max, math::transform_point(matrix, local));
  }
}

PyObject *live_instance_bounds(PyObject * /*self*/, PyObject *args)
{
  if (!live_require_main_thread()) {
    return nullptr;
  }
  PyObject *py_depsgraph, *py_objects;
  long long limit = 1000000;
  if (!PyArg_ParseTuple(args, "OO|L:instance_bounds", &py_depsgraph, &py_objects, &limit)) {
    return nullptr;
  }
  Depsgraph *depsgraph = static_cast<Depsgraph *>(PyC_RNA_AsPointer(py_depsgraph, "Depsgraph"));
  if (depsgraph == nullptr) {
    return nullptr;
  }
  ViewLayer *view_layer = DEG_get_input_view_layer(depsgraph);
  if (!DEG_is_fully_evaluated(depsgraph) || !BKE_view_layer_is_synced(*view_layer)) {
    /* Never facts from a depsgraph not evaluated since its last change, nor a view layer resynchronized by a read
     * (bpy_angelo_read_traversal_const.hh: withheld instead, REVIEW-INSTANCES-LATENCY round 2 B1). */
    Py_RETURN_NONE;
  }
  PyObject *objects = PySequence_Fast(py_objects, "instance_bounds: objects must be a sequence of objects");
  if (objects == nullptr) {
    return nullptr;
  }
  PyObject *result = PyDict_New();
  if (result == nullptr) {
    Py_DECREF(objects);
    return nullptr;
  }
  const eEvaluationMode mode = DEG_get_mode(depsgraph);
  const Py_ssize_t count = PySequence_Fast_GET_SIZE(objects);
  for (Py_ssize_t index = 0; index < count; index++) {
    Object *original = static_cast<Object *>(
        PyC_RNA_AsPointer(PySequence_Fast_GET_ITEM(objects, index), "Object"));
    if (original == nullptr) {
      Py_DECREF(objects);
      Py_DECREF(result);
      return nullptr;
    }
    /* Only the objects asked about, each expanded alone (#object_duplilist, the iterator's own source): the cost is
     * what that object makes, never the whole scene's instances. */
    Object *ob = DEG_get_evaluated(depsgraph, original);
    if (ob == nullptr || ob == original || !live_shown_here(view_layer, original, mode)) {
      continue; /* Not shown by this depsgraph's view layer (hidden, excluded, only a dependency). */
    }
    const int visibility = BKE_object_visibility(ob, mode);
    float own_min[3], own_max[3];
    INIT_MINMAX(own_min, own_max);
    bool owns = (visibility & OB_VISIBLE_SELF) && live_is_geometry(ob) && live_merge_owned_bounds(ob, own_min, own_max);
    if (!(visibility & OB_VISIBLE_INSTANCES) ||
        !((ob->transflag & OB_DUPLI) || ob->runtime->geometry_set_eval != nullptr))
    {
      continue;
    }
    DupliList duplis;
    object_duplilist(depsgraph, ob, nullptr, duplis);
    float min[3], max[3];
    INIT_MINMAX(min, max);
    bool found = false, hair = false;
    const bool too_many = limit > 0 && int64_t(duplis.size()) > limit;
    for (const DupliObject &dob : duplis) {
      if (too_many || !DEG_iterator_dupli_is_visible(&dob, mode)) {
        continue;
      }
      hair = hair || dupli_draws_hair(dob);
      if (!dupli_shows_self(dob, mode)) {
        continue;
      }
      const std::optional<Bounds<float3>> bounds = dupli_bounds(dob);
      if (!bounds) {
        continue;
      }
      if (dob.level == 0 && dob.ob == ob) {
        owns = true; /* A component of its own geometry drawn as a dupli (a curve's mesh): never an instance. */
        continue;
      }
      merge_box(*bounds, float4x4(dob.mat), min, max);
      found = true;
    }
    if (!found && !too_many && !hair) {
      continue; /* It makes none that show geometry. */
    }
    PyObject *uid = PyLong_FromUnsignedLong(original->id.session_uid);
    PyObject *value = too_many ? Py_BuildValue("(OO)", Py_None, owns ? Py_True : Py_False) :
                      hair     ? Py_BuildValue("(OOs)", Py_None, owns ? Py_True : Py_False, "hair") :
                                 Py_BuildValue("((fff)(fff)O)",
                                               min[0],
                                               min[1],
                                               min[2],
                                               max[0],
                                               max[1],
                                               max[2],
                                               owns ? Py_True : Py_False);
    if (uid == nullptr || value == nullptr || PyDict_SetItem(result, uid, value) != 0) {
      Py_XDECREF(uid);
      Py_XDECREF(value);
      Py_DECREF(objects);
      Py_DECREF(result);
      return nullptr;
    }
    Py_DECREF(uid);
    Py_DECREF(value);
  }
  Py_DECREF(objects);
  return result;
}

PyObject *live_update_count(PyObject * /*self*/, PyObject *py_depsgraph)
{
  if (!live_require_main_thread()) {
    return nullptr;
  }
  Depsgraph *depsgraph = static_cast<Depsgraph *>(PyC_RNA_AsPointer(py_depsgraph, "Depsgraph"));
  if (depsgraph == nullptr) {
    return nullptr;
  }
  if (!DEG_is_fully_evaluated(depsgraph) || !BKE_view_layer_is_synced(*DEG_get_input_view_layer(depsgraph))) {
    Py_RETURN_NONE;
  }
  return PyLong_FromUnsignedLongLong(DEG_get_update_count(depsgraph));
}

}  // namespace blender
