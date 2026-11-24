/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edobj
 *
 * Object > Retopology: field-aligned quad retopology of the active mesh
 * (`doc/angelo/retopology.rst`). From the interface it runs as a window-manager job with progress
 * and Cancel and pushes its own Undo step when it ends (the UV packing job's pattern); from Python
 * it runs at once. By default the result is a new object beside the source, which stays untouched.
 */

#include <cmath>
#include <string>

#include "MEM_guardedalloc.h"

#include "DNA_mesh_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "BLI_math_constants.hh"
#include "BLI_math_matrix_c.hh"
#include "BLI_string.hh"

#include "BLT_translation.hh"

#include "BKE_context.hh"
#include "BKE_global.hh"
#include "BKE_idprop.hh"
#include "BKE_layer.hh"
#include "BKE_lib_id.hh"
#include "BKE_library.hh"
#include "BKE_main.hh"
#include "BKE_mesh.hh"
#include "BKE_mesh_remesh_voxel.hh"
#include "BKE_modifier.hh"
#include "BKE_object.hh"
#include "BKE_object_types.hh"
#include "BKE_paint.hh"
#include "BKE_report.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_build.hh"

#include "ED_object.hh"
#include "ED_screen.hh"
#include "ED_undo.hh"

#include "GEO_mesh_retopology.hh"

#include "RNA_access.hh"
#include "RNA_define.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "object_intern.hh" /* own include */

namespace blender::ed::object {

struct RetopologyJob {
  wmWindowManager *wm = nullptr;
  Main *bmain = nullptr;
  Scene *scene = nullptr;
  ViewLayer *view_layer = nullptr;
  Object *owner = nullptr;
  /** A copy of the source mesh made on the main thread; the job reads only this. */
  Mesh *source = nullptr;
  geometry::RetopologyOptions options;
  bool preserve_attributes = true;
  bool smooth_normals = false;
  bool keep_original = true;
  bool use_job = false;
  /** Written with the facts on the result, so a script or the agent's job finds its own result. */
  std::string tag;
  geometry::RetopologyResult result;
  wmJobWorkerStatus *worker_status = nullptr;
  bContext *undo_context = nullptr;
  const char *undo_str = nullptr;
  ReportList *reports = nullptr;
};

static bool retopology_poll(bContext *C)
{
  Object *ob = CTX_data_active_object(C);
  if (ob == nullptr || ob->data == nullptr || ob->type != OB_MESH) {
    return false;
  }
  if (!ID_IS_EDITABLE(ob) || !ID_IS_EDITABLE(ob->data) || ID_IS_OVERRIDE_LIBRARY(ob->data)) {
    CTX_wm_operator_poll_msg_set(C, "Retopology cannot work on linked or override data");
    return false;
  }
  if (ob->mode != OB_MODE_OBJECT) {
    CTX_wm_operator_poll_msg_set(C, "Retopology runs in Object Mode");
    return false;
  }
  return ED_operator_object_active_editable_mesh(C);
}

static void retopology_startjob(void *customdata, wmJobWorkerStatus *worker_status)
{
  RetopologyJob *job = static_cast<RetopologyJob *>(customdata);
  job->worker_status = worker_status;
  worker_status->progress = 0.01f;
  job->result = geometry::mesh_retopology(
      *job->source,
      job->options,
      {},
      [&]() { return job->use_job ? (G.is_break || worker_status->stop) : false; },
      [&](const float progress) {
        worker_status->progress = progress;
        worker_status->do_update = true;
      });
  if (job->result.mesh && job->preserve_attributes) {
    bke::mesh_remesh_reproject_attributes(*job->source, *job->result.mesh);
  }
  worker_status->progress = 1.0f;
  worker_status->do_update = true;
}

/** The facts as one line of the report, the same words in every language through #RPT_. */
static void report_result(ReportList *reports, const geometry::RetopologyResult &r)
{
  switch (r.status) {
    case geometry::retopo::Status::Ok:
      BKE_reportf(reports,
                  RPT_INFO,
                  RPT_("Retopology: %d faces, %.0f%% quads, %d irregular vertices, deviation "
                       "%.2f%% of the size"),
                  r.facts.faces,
                  r.facts.quad_ratio * 100.0,
                  r.facts.irregular_vertices,
                  r.facts.hausdorff_max_of_diag * 100.0);
      break;
    case geometry::retopo::Status::Cancelled:
      BKE_report(reports, RPT_WARNING, RPT_("Retopology canceled"));
      break;
    case geometry::retopo::Status::EmptyInput:
      BKE_report(reports,
                 RPT_ERROR,
                 RPT_("Retopology: the mesh has no faces with an area to retopologize"));
      break;
    case geometry::retopo::Status::Failed:
      BKE_report(reports, RPT_ERROR, RPT_("Retopology failed: try a higher face count"));
      break;
  }
}

/**
 * The facts on the result object as the hidden custom property `_angelo_retopology` (read by the
 * agent's job and by scripts; underscore properties are not drawn in the Custom Properties panel).
 * Measured on the result as written.
 */
static void store_facts(Object &target, const RetopologyJob &job)
{
  namespace idprop = bke::idprop;
  const geometry::RetopologyResult &r = job.result;
  auto group = idprop::create_group("_angelo_retopology");
  IDProperty *g = group.get();
  IDP_AddToGroup(g, idprop::create("tag", StringRefNull(job.tag.c_str())).release());
  IDP_AddToGroup(g, idprop::create("target_faces", int32_t(job.options.target_faces)).release());
  IDP_AddToGroup(g, idprop::create("faces", int32_t(r.facts.faces)).release());
  IDP_AddToGroup(g, idprop::create("quad_ratio", r.facts.quad_ratio).release());
  IDP_AddToGroup(g, idprop::create("vertices", int32_t(r.facts.vertices)).release());
  IDP_AddToGroup(
      g, idprop::create("irregular_vertices", int32_t(r.facts.irregular_vertices)).release());
  IDP_AddToGroup(g, idprop::create("poles_3", int32_t(r.facts.interior_valence[0])).release());
  IDP_AddToGroup(g, idprop::create("poles_5", int32_t(r.facts.interior_valence[2])).release());
  IDP_AddToGroup(g,
                 idprop::create("poles_6_plus", int32_t(r.facts.interior_valence[3])).release());
  IDP_AddToGroup(
      g, idprop::create("boundary_vertices", int32_t(r.facts.boundary_vertices)).release());
  IDP_AddToGroup(g,
                 idprop::create("hausdorff_max_of_size", r.facts.hausdorff_max_of_diag).release());
  IDP_AddToGroup(
      g, idprop::create("hausdorff_mean_of_size", r.facts.hausdorff_mean_of_diag).release());
  IDP_AddToGroup(g, idprop::create("symmetry_axes", int32_t(job.options.symmetry_axes)).release());
  IDP_AddToGroup(g, idprop::create("shells", int32_t(r.intake.shells)).release());
  IDP_AddToGroup(g, idprop::create("shells_dropped", int32_t(r.intake.shells_dropped)).release());
  IDP_AddToGroup(g, idprop::create("feature_edges", int32_t(r.intake.feature_edges)).release());
  const double ms = r.times.intake_ms + r.times.hierarchy_ms + r.times.orientation_ms +
                    r.times.position_ms + r.times.extraction_ms + r.times.finish_ms +
                    r.times.facts_ms;
  IDP_AddToGroup(g, idprop::create("milliseconds", ms).release());
  /* Where the time went: the field solves (hierarchy and both fields, every solve) against the
   * rest, and how many solves the face target took. */
  IDP_AddToGroup(g,
                 idprop::create("milliseconds_fields",
                                r.times.hierarchy_ms + r.times.orientation_ms + r.times.position_ms)
                     .release());
  IDP_AddToGroup(g, idprop::create("milliseconds_orientation", r.times.orientation_ms).release());
  IDP_AddToGroup(g, idprop::create("milliseconds_hierarchy", r.times.hierarchy_ms).release());
  IDP_AddToGroup(g, idprop::create("field_solves", int32_t(r.field_solves)).release());
  IDP_ReplaceInGroup(IDP_EnsureProperties(&target.id), group.release());
}

static void retopology_endjob(void *customdata)
{
  RetopologyJob *job = static_cast<RetopologyJob *>(customdata);
  ReportList *reports = job->worker_status && job->worker_status->reports ?
                            job->worker_status->reports :
                            job->reports;
  report_result(reports, job->result);
  Mesh *new_mesh = job->result.mesh;
  job->result.mesh = nullptr;
  if (new_mesh == nullptr) {
    return;
  }
  Object *ob = job->owner;
  Object *target = ob;
  if (job->keep_original) {
    /* The result becomes the only selected and the active object (#BKE_object_add_from selects
     * it). */
    BKE_view_layer_base_deselect_all(*job->bmain, job->scene, job->view_layer);
    const std::string name = std::string(ob->id.name + 2) + "_retopo";
    target = BKE_object_add_from(
        job->bmain, job->scene, job->view_layer, OB_MESH, name.c_str(), ob);
    /* Same parent, place, rotation and scale as the source, so the result lies on it. */
    BKE_object_transform_copy(target, ob);
    target->parent = ob->parent;
    target->partype = ob->partype;
    STRNCPY(target->parsubstr, ob->parsubstr);
    copy_m4_m4(target->parentinv, ob->parentinv);
  }
  BKE_mesh_nomain_to_mesh(new_mesh, id_cast<Mesh *>(target->data), target);
  store_facts(*target, *job);
  bke::mesh_smooth_set(*id_cast<Mesh *>(target->data), job->smooth_normals);
  BKE_mesh_batch_cache_dirty_tag(id_cast<Mesh *>(target->data), BKE_MESH_BATCH_DIRTY_ALL);
  DEG_id_tag_update(&target->id, ID_RECALC_GEOMETRY | ID_RECALC_TRANSFORM);
  if (job->keep_original) {
    DEG_relations_tag_update(job->bmain);
    WM_main_add_notifier(NC_SCENE | ND_OB_ACTIVE, job->scene);
    WM_main_add_notifier(NC_SCENE | ND_LAYER_CONTENT, job->scene);
  }
  WM_main_add_notifier(NC_GEOM | ND_DATA, target->data);
  if (job->undo_str) {
    ED_undo_push(job->undo_context, job->undo_str);
  }
}

static void retopology_freejob(void *customdata)
{
  RetopologyJob *job = static_cast<RetopologyJob *>(customdata);
  if (job->use_job) {
    WM_locked_interface_set(job->wm, false);
  }
  if (job->result.mesh) {
    BKE_id_free(nullptr, job->result.mesh);
  }
  if (job->source) {
    BKE_id_free(nullptr, job->source);
  }
  MEM_delete(job);
}

static wmOperatorStatus retopology_exec(bContext *C, wmOperator *op)
{
  Object *ob = CTX_data_active_object(C);
  Mesh *mesh = id_cast<Mesh *>(ob->data);
  if (mesh->faces_num == 0) {
    BKE_report(op->reports, RPT_ERROR, RPT_("Retopology: the mesh has no faces"));
    return OPERATOR_CANCELLED;
  }
  RetopologyJob *job = MEM_new<RetopologyJob>(__func__);
  job->wm = CTX_wm_manager(C);
  job->bmain = CTX_data_main(C);
  job->scene = CTX_data_scene(C);
  job->view_layer = CTX_data_view_layer(C);
  job->owner = ob;
  job->source = BKE_mesh_copy_for_eval(*mesh);
  job->reports = op->reports;
  job->options.target_faces = RNA_int_get(op->ptr, "target_faces");
  /* The mesh's own symmetry (Mesh > Symmetry), or the axes the caller names. */
  job->options.symmetry_axes = RNA_boolean_get(op->ptr, "use_mesh_symmetry") ?
                                   int(mesh->symmetry) & 7 :
                                   RNA_enum_get(op->ptr, "symmetry");
  job->options.sharp_angle = RNA_float_get(op->ptr, "sharp_angle");
  job->options.use_marked_edges = RNA_boolean_get(op->ptr, "use_marked_edges");
  job->options.curvature_weight = RNA_float_get(op->ptr, "curvature_weight");
  job->options.pure_quads = RNA_boolean_get(op->ptr, "pure_quads");
  job->preserve_attributes = RNA_boolean_get(op->ptr, "preserve_attributes");
  job->smooth_normals = RNA_boolean_get(op->ptr, "smooth_normals");
  job->keep_original = RNA_boolean_get(op->ptr, "keep_original");
  job->use_job = (op->flag & OP_IS_INVOKE) != 0 || RNA_boolean_get(op->ptr, "as_background_job");
  job->tag = RNA_string_get(op->ptr, "tag");
  G.is_break = false;

  if (job->use_job) {
    /* One Undo step, pushed when the result is in place, however the job was started (a click, the
     * chat bar's + menu, the agent's job): it ends after its caller returned, so no caller can push
     * it then, and a step pushed now would hold the scene before the result. Blender suppresses the
     * pushes of an operator a script runs, which left the + menu's and the agent's runs with no
     * step at all (2026-11-18). */
    job->undo_context = C;
    job->undo_str = op->type->name;
    wmJob *wm_job = WM_jobs_get(job->wm,
                                CTX_wm_window(C),
                                job->scene,
                                "Retopologizing...",
                                WM_JOB_PROGRESS,
                                WM_JOB_TYPE_RETOPOLOGY);
    WM_jobs_customdata_set(wm_job, job, retopology_freejob);
    WM_jobs_timer(wm_job, 0.1, NC_GEOM | ND_DATA, NC_GEOM | ND_DATA);
    WM_jobs_callbacks(wm_job, retopology_startjob, nullptr, nullptr, retopology_endjob);
    WM_locked_interface_set(job->wm, true);
    WM_jobs_start(job->wm, wm_job);
    return OPERATOR_FINISHED;
  }

  /* Blocking: one step like any operator's, unless a script runs it (its caller owns the Undo). */
  if (job->wm->op_undo_depth == 0) {
    job->undo_context = C;
    job->undo_str = op->type->name;
  }
  wmJobWorkerStatus worker_status = {};
  worker_status.reports = op->reports;
  retopology_startjob(job, &worker_status);
  const bool ok = job->result.status == geometry::retopo::Status::Ok;
  retopology_endjob(job);
  retopology_freejob(job);
  return ok ? OPERATOR_FINISHED : OPERATOR_CANCELLED;
}

static wmOperatorStatus retopology_invoke(bContext *C, wmOperator *op, const wmEvent *event)
{
  return WM_operator_props_popup_confirm_ex(
      C, op, event, IFACE_("Retopologize the Active Mesh"), IFACE_("Retopologize"));
}

void OBJECT_OT_retopology(wmOperatorType *ot)
{
  ot->name = "Retopology";
  ot->description =
      "Make clean quad topology that follows the shape of the active mesh, its sharp edges and "
      "the edges marked "
      "Sharp, Seam or Crease. Works on scanned and generated meshes with holes, loose parts or "
      "broken faces";
  ot->idname = "OBJECT_OT_retopology";

  ot->poll = retopology_poll;
  ot->invoke = retopology_invoke;
  ot->exec = retopology_exec;
  /* No OPTYPE_UNDO: the operator pushes its own step when the result is in place (see exec); the
   * window manager's would come before a background job's result. */
  ot->flag = OPTYPE_REGISTER;

  RNA_def_int(
      ot->srna,
      "target_faces",
      5000,
      4,
      10000000,
      "Faces",
      "Approximate number of faces of the result (both sides of a symmetric result together)",
      100,
      200000);
  RNA_def_boolean(
      ot->srna,
      "use_mesh_symmetry",
      true,
      "Use Mesh Symmetry",
      "Make the result exactly symmetric across the mesh's symmetry axes (Mesh > Symmetry)");
  static const EnumPropertyItem symmetry_items[] = {
      {1 << 0, "X", 0, "X", ""},
      {1 << 1, "Y", 0, "Y", ""},
      {1 << 2, "Z", 0, "Z", ""},
      {0, nullptr, 0, nullptr, nullptr},
  };
  RNA_def_enum_flag(
      ot->srna,
      "symmetry",
      symmetry_items,
      0,
      "Symmetry",
      "Mirror planes through the object's origin when not using the mesh's own symmetry");
  PropertyRNA *sharp_angle = RNA_def_float_rotation(
      ot->srna,
      "sharp_angle",
      0,
      nullptr,
      0.0f,
      float(M_PI),
      "Sharp Angle",
      "Edges where faces meet at more than this angle become edges of the result; 0 follows "
      "only marked edges",
      0.0f,
      float(M_PI));
  RNA_def_property_float_default(sharp_angle, DEG2RADF(35.0f));
  RNA_def_boolean(ot->srna,
                  "use_marked_edges",
                  true,
                  "Follow Marked Edges",
                  "Keep the edges marked Sharp, Seam or Crease as edges of the result");
  RNA_def_float(ot->srna,
                "curvature_weight",
                1.0f,
                0.0f,
                10.0f,
                "Follow Curvature",
                "How strongly the flow follows the directions the surface curves in (0 for the "
                "smoothest flow)",
                0.0f,
                4.0f);
  RNA_def_boolean(ot->srna, "pure_quads", true, "Only Quads", "Make every face a quad");
  RNA_def_boolean(
      ot->srna,
      "preserve_attributes",
      true,
      "Preserve Attributes",
      "Carry vertex groups and other attributes over to the result from the nearest surface");
  RNA_def_boolean(ot->srna, "smooth_normals", false, "Smooth Normals", "Shade the result smooth");
  PropertyRNA *prop = RNA_def_boolean(
      ot->srna,
      "as_background_job",
      false,
      "Run as Background Job",
      "Run as a background job with progress and Cancel instead of blocking Angelo");
  RNA_def_property_flag(prop, PROP_HIDDEN | PROP_SKIP_SAVE);
  prop = RNA_def_string(ot->srna,
                        "tag",
                        nullptr,
                        64,
                        "Tag",
                        "Written with the result's facts so the caller can find its own result");
  RNA_def_property_flag(prop, PROP_HIDDEN | PROP_SKIP_SAVE);
  RNA_def_boolean(ot->srna,
                  "keep_original",
                  true,
                  "Keep Original",
                  "Put the result in a new object and leave the active mesh unchanged");
}

}  // namespace blender::ed::object
