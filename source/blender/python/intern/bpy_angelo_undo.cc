/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Private synchronous Undo ownership for the typed commit operator. The opaque scope is
 * consumed exactly once, and reports publication before the provider receives its result.
 */

#include <Python.h>

#include <cstring>
#include <string>
#include <utility>

#include "BLI_threads.h"
#include "BLI_map.hh"
#include "BLI_set.hh"
#include "BKE_context.hh"
#include "BKE_collection.hh"
#include "BKE_idtype.hh"
#include "BKE_main.hh"
#include "BKE_undo_system.hh"
#include "DNA_ID.h"
#include "DNA_collection_types.h"
#include "BLI_string.h"
#include "BLI_string_utf8.h"
#include "RNA_access.hh"
#include "RNA_enum_types.hh"
#include "WM_api.hh"
#include "WM_types.hh"
#include "bpy_rna.hh"
#include "../../editors/undo/angelo_commit_rights.hh"
#include "ED_undo.hh"
#include "MEM_guardedalloc.h"

#include "bpy_angelo_undo.hh"
#include "bpy_capi_utils.hh"

#ifdef WITH_GTESTS
#  include "../../windowmanager/intern/wm_checkpoint.hh"
#  include "../../editors/sculpt_paint/mesh/sculpt_main_undo_grids.hh"
#  include "BKE_context.hh"
#  include "BKE_customdata.hh"
#  include "BKE_main.hh"
#  include "BKE_mesh.hh"
#  include "BKE_object_types.hh"
#  include "BKE_paint.hh"
#  include "BKE_undo_system.hh"
#  include "BLI_bitmap.h"
#  include "BLI_endian_defines.h"
#  include "BLI_map.hh"
#  include "BLI_set.hh"
#  include "DNA_meshdata_types.h"
#  include "DNA_object_types.h"
#  include "DNA_windowmanager_types.h"
#  include "ED_sculpt.hh"
#  include "WM_api.hh"
#  include "bmesh.hh"
#endif

namespace blender {

static constexpr const char *capsule_name = "angelo.native_commit_undo";

struct UndoScope {
  AngeloUndoCapture *capture = nullptr;
  bool finishing = false;
  bool terminal = false;
  bool publication_known = false;
  bool published = false;
  std::string finish_error;
  Map<ID *, AngeloCommitIDIdentity> stage_ids;
  /* Observations of failed native registration are not deletion authority. */
  Vector<AngeloCommitIDIdentity> stage_registration_residue;
  std::string stage_registration_error;
  Vector<AngeloCommitIDIdentity> approved_objects;
  Vector<AngeloCommitIDIdentity> created_collections;
  bool approved_objects_sealed = false;
};

static bool require_main_thread()
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "Native commit Undo requires Blender's main thread");
    return false;
  }
  return true;
}

bool BPY_angelo_undo_operation_begin(PyObject *capsule)
{
  if (!require_main_thread()) {
    return false;
  }
  auto *scope = static_cast<UndoScope *>(PyCapsule_GetPointer(capsule, capsule_name));
  if (!scope) {
    return false;
  }
  const char *error = nullptr;
  if (!scope->capture || scope->finishing || scope->terminal ||
      !ED_angelo_undo_scope_operation_begin(
          BPY_context_get(), scope->capture, nullptr, nullptr, &error))
  {
    PyErr_SetString(PyExc_RuntimeError, error ? error : "The native commit scope is consumed");
    return false;
  }
  return true;
}

void BPY_angelo_undo_operation_end(PyObject *capsule)
{
  auto *scope = static_cast<UndoScope *>(PyCapsule_GetPointer(capsule, capsule_name));
  BLI_assert(scope && scope->capture);
  ED_angelo_undo_scope_operation_end(scope->capture);
}

bool BPY_angelo_undo_register_stage_ids(PyObject *capsule,
                                         Main *bmain,
                                         Span<AngeloCommitIDIdentity> ids,
                                         const char *provenance_error)
{
  auto *scope = static_cast<UndoScope *>(PyCapsule_GetPointer(capsule, capsule_name));
  if (!scope) {
    return false;
  }
  auto refuse = [&](const char *error) {
    /* These identities were copied while the native stage/append owner still held them. Keep
     * the complete failed batch even when one candidate is now missing, changed or ineligible. */
    for (const AngeloCommitIDIdentity &identity : ids) {
      if (!scope->stage_registration_residue.contains(identity)) {
        scope->stage_registration_residue.append(identity);
      }
    }
    if (scope->stage_registration_error.empty()) {
      scope->stage_registration_error = error;
    }
    PyErr_SetString(PyExc_RuntimeError, error);
    return false;
  };
  Main *current = nullptr;
  const char *error = nullptr;
  if (!scope->capture || scope->finishing || scope->terminal ||
      !ED_angelo_undo_scope_operation_active(scope->capture) ||
      !ED_angelo_undo_scope_validate(
          BPY_context_get(), scope->capture, &current, nullptr, &error) || current != bmain)
  {
    return refuse(error ? error : "The stage loader has a different native owner");
  }
  /* This entry is C++ only: ids comes from a private Main or the actual append context,
   * never a Python-supplied authorization list or names/tags found after a callback. */
  if (ids.is_empty()) {
    return provenance_error ? refuse(provenance_error) : true;
  }
  Set<ID *> live;
  ID *id;
  FOREACH_MAIN_ID_BEGIN (bmain, id) {
    live.add(id);
  }
  FOREACH_MAIN_ID_END;
  const char *registration_error = provenance_error;
  for (const AngeloCommitIDIdentity &identity : ids) {
    ID *candidate = identity.pointer;
    if (!live.contains(candidate) || candidate->session_uid != identity.session_uid ||
        GS(candidate->name) != identity.id_type)
    {
      if (!registration_error) {
        registration_error = "A natively owned stage identity no longer matches the document";
      }
      continue;
    }
    if (!identity.session_uid || ED_angelo_undo_scope_id_is_protected(scope->capture, candidate)) {
      if (!registration_error) {
        registration_error = "A protected document owner cannot become private stage data";
      }
      continue;
    }
    scope->stage_ids.add_overwrite(candidate, identity);
  }
  return registration_error ? refuse(registration_error) : true;
}

static void capsule_free(PyObject *capsule)
{
  UndoScope *scope = static_cast<UndoScope *>(PyCapsule_GetPointer(capsule, capsule_name));
  /* The private caller uses a synchronous try/finally; no timer or provider owns this capsule. */
  BLI_assert(BLI_thread_is_main());
  ED_angelo_undo_discard(scope->capture);
  MEM_delete(scope);
}

static PyObject *undo_begin_impl(const bool synchronize)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  UndoScope *scope = MEM_new<UndoScope>(__func__);
  PyObject *capsule = PyCapsule_New(scope, capsule_name, capsule_free);
  if (!capsule) {
    MEM_delete(scope);
    return nullptr;
  }
  const char *error = nullptr;
  bContext *C = BPY_context_get();
  /* Worker drivers acquire the GIL while native evaluation waits for them. */
  Py_BEGIN_ALLOW_THREADS;
  scope->capture = ED_angelo_undo_begin(C, &error, synchronize);
  Py_END_ALLOW_THREADS;
  if (!scope->capture) {
    Py_DECREF(capsule);
    PyErr_SetString(PyExc_RuntimeError, error ? error : "Could not prepare native commit Undo");
    return nullptr;
  }
  return capsule;
}

/** A background process that applies native commits keeps its document as a window does. */
static PyObject *undo_keep_background_document(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "The document is kept from the main thread only");
    return nullptr;
  }
  if (BKE_undosys_restore_phase() != UndoRestorePhase::Inactive) {
    PyErr_SetString(PyExc_RuntimeError, BKE_undosys_restore_refusal_message());
    return nullptr;
  }
  return PyBool_FromLong(WM_angelo_background_document_keep(BPY_context_get()));
}

/** The owner of a kept background document saved its state through a copy (#keep_background_document). */
static PyObject *undo_background_document_saved(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "The document is kept from the main thread only");
    return nullptr;
  }
  return PyBool_FromLong(WM_angelo_background_document_saved(BPY_context_get()));
}

static PyObject *undo_begin(PyObject * /*self*/, PyObject * /*args*/)
{
  return undo_begin_impl(false);
}

static PyObject *undo_begin_synchronized(PyObject * /*self*/, PyObject * /*args*/)
{
  return undo_begin_impl(true);
}

static PyObject *undo_publication_result(const bool published, const char *error)
{
  if (error && published) {
    /* Editor callback failure is not a failure to publish the already-owned Undo step. */
    return Py_BuildValue("{s:O,s:s}", "undo_published", Py_True, "editor_finalization_error", error);
  }
  if (error) {
    return Py_BuildValue("{s:O,s:s,s:s}", "undo_published", Py_False,
                        "error_code", "INTERNAL_ERROR", "error", error);
  }
  return Py_BuildValue("{s:O}", "undo_published", published ? Py_True : Py_False);
}

/** Pin the owner until callbacks and Python result allocation have both finished. */
class FinishOperation {
  PyObject *capsule_;
  UndoScope &scope_;

 public:
  FinishOperation(PyObject *capsule, UndoScope &scope) : capsule_(capsule), scope_(scope)
  {
    BLI_assert(!scope_.finishing);
    Py_INCREF(capsule_);
    scope_.finishing = true;
  }
  ~FinishOperation()
  {
    scope_.finishing = false;
    Py_DECREF(capsule_);
  }
};

static PyObject *undo_finish(PyObject * /*self*/, PyObject *args)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  PyObject *capsule, *py_keep;
  if (!PyArg_ParseTuple(args, "OO:finish", &capsule, &py_keep)) {
    return nullptr;
  }
  UndoScope *scope = static_cast<UndoScope *>(PyCapsule_GetPointer(capsule, capsule_name));
  if (!scope) {
    return nullptr;
  }
  if (!scope->capture) {
    PyErr_SetString(PyExc_RuntimeError, "The native commit Undo scope has already been consumed");
    return nullptr;
  }
  if (scope->finishing || scope->terminal || ED_angelo_undo_scope_operation_active(scope->capture)) {
    PyErr_SetString(PyExc_RuntimeError, "The native commit scope is busy or already finalized");
    return nullptr;
  }
  if (!BPY_angelo_undo_operation_begin(capsule)) {
    return nullptr;
  }
  const int keep = PyObject_IsTrue(py_keep);
  BPY_angelo_undo_operation_end(capsule);
  if (keep == -1) {
    return nullptr;
  }
  FinishOperation finishing(capsule, *scope);
  AngeloUndoCapture *capture = scope->capture;
  scope->capture = nullptr;
  bContext *C = BPY_context_get();
  const char *error = nullptr;
  bool published;
  Py_BEGIN_ALLOW_THREADS;
  published = ED_angelo_undo_finish(C, capture, keep, &error);
  Py_END_ALLOW_THREADS;
  scope->published = published;
  scope->publication_known = true;
  scope->finish_error = error ? error : "";
  scope->terminal = true;
  return undo_publication_result(published, error);
}


/** Retain ownership only until the same-dispatch trusted terminal pass and explicit close. */
static PyObject *undo_finish_synchronized(PyObject * /*self*/, PyObject *args)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  PyObject *capsule, *py_keep;
  if (!PyArg_ParseTuple(args, "OO:finish_synchronized", &capsule, &py_keep)) {
    return nullptr;
  }
  auto *scope = static_cast<UndoScope *>(PyCapsule_GetPointer(capsule, capsule_name));
  if (!scope) {
    return nullptr;
  }
  if (!scope->capture || scope->finishing || scope->terminal ||
      ED_angelo_undo_scope_operation_active(scope->capture))
  {
    PyErr_SetString(PyExc_RuntimeError, "The native commit scope is busy or already finalized");
    return nullptr;
  }
  if (!BPY_angelo_undo_operation_begin(capsule)) {
    return nullptr;
  }
  const int keep = PyObject_IsTrue(py_keep);
  BPY_angelo_undo_operation_end(capsule);
  if (keep == -1) {
    return nullptr;
  }
  FinishOperation finishing(capsule, *scope);
  bContext *C = BPY_context_get();
  const char *error = nullptr;
  Py_BEGIN_ALLOW_THREADS;
  scope->published = ED_angelo_undo_finish_prepare(C, scope->capture, keep, true, &error);
  Py_END_ALLOW_THREADS;
  scope->publication_known = true;
  scope->finish_error = error ? error : "";
  scope->terminal = true;
  return undo_publication_result(scope->published, error);
}

static PyObject *undo_publication(PyObject * /*self*/, PyObject *capsule)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  auto *scope = static_cast<UndoScope *>(PyCapsule_GetPointer(capsule, capsule_name));
  if (!scope) {
    return nullptr;
  }
  if (scope->finishing) {
    PyErr_SetString(PyExc_RuntimeError, "Native commit finalization is still active");
    return nullptr;
  }
  if (!scope->publication_known) {
    return Py_BuildValue("{s:O}", "undo_published", Py_None);
  }
  return undo_publication_result(
      scope->published, scope->finish_error.empty() ? nullptr : scope->finish_error.c_str());
}

static PyObject *undo_close(PyObject * /*self*/, PyObject *capsule)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  auto *scope = static_cast<UndoScope *>(PyCapsule_GetPointer(capsule, capsule_name));
  if (!scope) {
    return nullptr;
  }
  if (scope->finishing) {
    PyErr_SetString(PyExc_RuntimeError, "Native commit finalization is still active");
    return nullptr;
  }
  if (!scope->capture) {
    Py_RETURN_NONE; /* Finally may close after an earlier explicit close. */
  }
  if (ED_angelo_undo_scope_operation_active(scope->capture)) {
    PyErr_SetString(PyExc_RuntimeError, "A native commit capability is still active");
    return nullptr;
  }
  if (!scope->terminal) {
    PyErr_SetString(PyExc_RuntimeError, "Finalize the native commit before closing its scope");
    return nullptr;
  }
  AngeloUndoCapture *capture = scope->capture;
  scope->capture = nullptr;
  ED_angelo_undo_discard(capture);
  Py_RETURN_NONE;
}


/** Hold the capsule and the native busy bit through conversions, callbacks and result creation. */
class CommitOperation {
  PyObject *capsule_;

 public:
  UndoScope *scope = nullptr;
  explicit CommitOperation(PyObject *capsule) : capsule_(capsule)
  {
    if (BPY_angelo_undo_operation_begin(capsule)) {
      Py_INCREF(capsule_);
      scope = static_cast<UndoScope *>(PyCapsule_GetPointer(capsule_, capsule_name));
    }
  }
  ~CommitOperation()
  {
    if (scope) {
      BPY_angelo_undo_operation_end(capsule_);
      Py_DECREF(capsule_);
    }
  }
};

static bool commit_identities(UndoScope &scope,
                                PyObject *sequence,
                                Vector<AngeloCommitIDIdentity> &r_ids)
{
  /* Exact tuples avoid user iteration while native ownership is borrowed. */
  if (!PyTuple_CheckExact(sequence)) {
    PyErr_SetString(PyExc_TypeError, "Native commit IDs must be an exact tuple");
    return false;
  }
  Main *bmain = nullptr;
  const char *error = nullptr;
  if (!ED_angelo_undo_scope_validate(
          BPY_context_get(), scope.capture, &bmain, nullptr, &error))
  {
    PyErr_SetString(PyExc_RuntimeError, error);
    return false;
  }
  Set<ID *> live;
  ID *id;
  FOREACH_MAIN_ID_BEGIN (bmain, id) {
    live.add(id);
  }
  FOREACH_MAIN_ID_END;
  for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(sequence); i++) {
    PyObject *item = PyTuple_GET_ITEM(sequence, i);
    ID *candidate = nullptr;
    if (!BPy_StructRNA_Check(item) ||
        pyrna_struct_validity_check(reinterpret_cast<BPy_StructRNA *>(item)) == -1 ||
        !pyrna_id_FromPyObject(item, &candidate) || !live.contains(candidate))
    {
      if (!PyErr_Occurred()) {
        PyErr_SetString(PyExc_ValueError, "A commit ID no longer belongs to the current Main");
      }
      return false;
    }
    r_ids.append({candidate, candidate->session_uid, short(GS(candidate->name))});
  }
  return true;
}

static PyObject *identity_rows(const Span<AngeloCommitIDIdentity> ids)
{
  PyObject *result = PyList_New(ids.size());
  if (!result) {
    return nullptr;
  }
  for (const int i : ids.index_range()) {
    const char *identifier = nullptr;
    if (!RNA_enum_identifier(rna_enum_id_type_items, ids[i].id_type, &identifier)) {
      Py_DECREF(result);
      PyErr_SetString(PyExc_RuntimeError, "A native commit identity has no RNA ID family");
      return nullptr;
    }
    PyObject *row = Py_BuildValue("(KIs)",
                                  uint64_t(uintptr_t(ids[i].pointer)),
                                  ids[i].session_uid,
                                  identifier);
    if (!row) {
      Py_DECREF(result);
      return nullptr;
    }
    PyList_SET_ITEM(result, i, row);
  }
  return result;
}

static PyObject *undo_stage_owned(PyObject * /*self*/, PyObject *capsule)
{
  CommitOperation operation(capsule);
  if (!operation.scope) {
    return nullptr;
  }
  UndoScope &scope = *operation.scope;
  Main *bmain = nullptr;
  const char *error = nullptr;
  if (!ED_angelo_undo_scope_validate(BPY_context_get(), scope.capture, &bmain, nullptr, &error)) {
    PyErr_SetString(PyExc_RuntimeError, error);
    return nullptr;
  }
  Set<ID *> live;
  ID *id;
  FOREACH_MAIN_ID_BEGIN (bmain, id) {
    live.add(id);
  }
  FOREACH_MAIN_ID_END;
  Vector<AngeloCommitIDIdentity> owned;
  for (const AngeloCommitIDIdentity &identity : scope.stage_ids.values()) {
    ID *candidate = identity.pointer;
    if (live.contains(candidate) && candidate->session_uid == identity.session_uid &&
        GS(candidate->name) == identity.id_type)
    {
      owned.append(identity);
    }
  }
  /* Complete current native provenance. The trusted stage owner applies its existing size
   * admission; this transport never truncates rows or uses observation-only failure evidence. */
  return identity_rows(owned);
}

static PyObject *undo_stage_residue(PyObject * /*self*/, PyObject *capsule)
{
  CommitOperation operation(capsule);
  if (!operation.scope) {
    return nullptr;
  }
  UndoScope &scope = *operation.scope;
  Main *bmain = nullptr;
  const char *error = nullptr;
  if (!ED_angelo_undo_scope_validate(BPY_context_get(), scope.capture, &bmain, nullptr, &error)) {
    PyErr_SetString(PyExc_RuntimeError, error);
    return nullptr;
  }
  Vector<AngeloCommitIDIdentity> remaining, unknown;
  if (!scope.stage_registration_residue.is_empty()) {
    Set<ID *> live;
    ID *id;
    FOREACH_MAIN_ID_BEGIN (bmain, id) {
      live.add(id);
    }
    FOREACH_MAIN_ID_END;
    for (const AngeloCommitIDIdentity &identity : scope.stage_registration_residue) {
      ID *candidate = identity.pointer;
      if (live.contains(candidate) && candidate->session_uid == identity.session_uid &&
          GS(candidate->name) == identity.id_type)
      {
        remaining.append(identity);
      }
      else {
        unknown.append(identity); /* No matching identity is not a verified cleanup receipt. */
      }
    }
  }
  PyObject *py_remaining = identity_rows(remaining);
  PyObject *py_unknown = identity_rows(unknown);
  if (!py_remaining || !py_unknown) {
    Py_XDECREF(py_remaining);
    Py_XDECREF(py_unknown);
    return nullptr;
  }
  return Py_BuildValue("{s:N,s:N,s:s}", "remaining", py_remaining, "unknown", py_unknown,
                       "error", scope.stage_registration_error.c_str());
}

static PyObject *undo_seal_approved_objects(PyObject * /*self*/, PyObject *args)
{
  PyObject *capsule, *objects;
  if (!PyArg_ParseTuple(args, "OO:seal_approved_objects", &capsule, &objects)) {
    return nullptr;
  }
  CommitOperation operation(capsule);
  if (!operation.scope) {
    return nullptr;
  }
  UndoScope &scope = *operation.scope;
  if (scope.approved_objects_sealed) {
    PyErr_SetString(PyExc_RuntimeError, "The approved Object identities are already sealed");
    return nullptr;
  }
  Vector<AngeloCommitIDIdentity> identities;
  if (!commit_identities(scope, objects, identities)) {
    return nullptr;
  }
  for (const AngeloCommitIDIdentity &identity : identities) {
    if (identity.id_type != ID_OB) {
      PyErr_SetString(PyExc_ValueError, "Approved object deletion requires Object identities");
      return nullptr;
    }
  }
  /* The trusted controller supplies the original sealed program/binding once, before apply.
   * This records that transition authority; it is not a public model-facing approval API. */
  scope.approved_objects = std::move(identities);
  scope.approved_objects_sealed = true;
  Py_RETURN_NONE;
}

static PyObject *undo_delete_ids(PyObject *args, const AngeloCommitDeletePurpose purpose)
{
  PyObject *capsule, *roots, *keep = nullptr;
  if (!PyArg_ParseTuple(args, "OO|O:delete_commit_ids", &capsule, &roots, &keep)) {
    return nullptr;
  }
  CommitOperation operation(capsule);
  if (!operation.scope) {
    return nullptr;
  }
  UndoScope &scope = *operation.scope;
  Vector<AngeloCommitIDIdentity> root_ids, keep_ids, authorized;
  if (!commit_identities(scope, roots, root_ids) ||
      (keep && !commit_identities(scope, keep, keep_ids)))
  {
    return nullptr;
  }
  switch (purpose) {
    case AngeloCommitDeletePurpose::PrivateStage:
      for (const AngeloCommitIDIdentity &identity : scope.stage_ids.values()) {
        authorized.append(identity);
      }
      break;
    case AngeloCommitDeletePurpose::ApprovedObject:
      authorized = scope.approved_objects;
      break;
    case AngeloCommitDeletePurpose::CreatedCollection:
      authorized = scope.created_collections;
      break;
  }
  const char *error = nullptr;
  AngeloCommitDeleteResult outcome;
  AngeloCommitDeleteRight *right = ED_angelo_commit_delete_prepare(
      BPY_context_get(), scope.capture, purpose, root_ids, authorized, keep_ids, &error);
  bool success = false;
  if (right) {
    success = ED_angelo_commit_delete(
        BPY_context_get(), scope.capture, *right, outcome, &error);
    ED_angelo_commit_delete_free(right);
  }
  else if (purpose == AngeloCommitDeletePurpose::PrivateStage) {
    /* A refused request can contain unapproved artist IDs. Only exact native stage authority
     * is temporary residue; rejecting the whole request does not establish ownership of it. */
    for (const AngeloCommitIDIdentity &identity : root_ids) {
      const AngeloCommitIDIdentity *authorized = scope.stage_ids.lookup_ptr(identity.pointer);
      if (authorized && *authorized == identity) {
        outcome.remaining.append(identity);
      }
    }
  }
  else {
    outcome.remaining = root_ids;
  }
  if (purpose == AngeloCommitDeletePurpose::PrivateStage && outcome.kernel_called &&
      outcome.scope_valid_after)
  {
    /* Only this helper's exact post-call removal proof retires a failed-registration row. */
    for (const AngeloCommitIDIdentity &removed : outcome.removed) {
      if (scope.stage_registration_residue.contains(removed)) {
        scope.stage_registration_residue.remove_first_occurrence_and_reorder(removed);
      }
    }
  }
  /* A native/Python callback error is not evidence that deletion did not happen. Keep actual
   * native effects in the receipt, without invoking an exception's user-defined __str__. */
  PyObject *error_type = nullptr, *error_value = nullptr, *error_traceback = nullptr;
  PyErr_Fetch(&error_type, &error_value, &error_traceback);
  const std::string callback_error = error_type ? PyExceptionClass_Name(error_type) : "";
  PyObject *remaining = identity_rows(outcome.remaining);
  PyObject *removed = identity_rows(outcome.removed);
  PyObject *result = nullptr;
  if (remaining && removed) {
    result = Py_BuildValue("{s:O,s:O,s:O,s:O,s:N,s:N,s:K,s:s,s:s}",
                           "success", success && callback_error.empty() ? Py_True : Py_False,
                           "kernel_called", outcome.kernel_called ? Py_True : Py_False,
                           "scope_valid_after", outcome.scope_valid_after ? Py_True : Py_False,
                           "unexpected_scope", outcome.unexpected_scope ? Py_True : Py_False,
                           "remaining", remaining, "removed", removed,
                           "kernel_deleted_ids", uint64_t(outcome.kernel_deleted_ids),
                           "error", error ? error : "", "callback_error", callback_error.c_str());
  }
  else {
    Py_XDECREF(remaining);
    Py_XDECREF(removed);
  }
  Py_XDECREF(error_type);
  Py_XDECREF(error_value);
  Py_XDECREF(error_traceback);
  return result;
}

static PyObject *undo_delete_stage(PyObject * /*self*/, PyObject *args)
{
  return undo_delete_ids(args, AngeloCommitDeletePurpose::PrivateStage);
}
static PyObject *undo_delete_approved_objects(PyObject * /*self*/, PyObject *args)
{
  return undo_delete_ids(args, AngeloCommitDeletePurpose::ApprovedObject);
}
static PyObject *undo_delete_created_collections(PyObject * /*self*/, PyObject *args)
{
  return undo_delete_ids(args, AngeloCommitDeletePurpose::CreatedCollection);
}

static PyObject *undo_new_collection(PyObject * /*self*/, PyObject *args)
{
  PyObject *capsule, *py_name;
  if (!PyArg_ParseTuple(args, "OO:new_collection", &capsule, &py_name)) {
    return nullptr;
  }
  CommitOperation operation(capsule);
  if (!operation.scope) {
    return nullptr;
  }
  Py_ssize_t length;
  const char *name = PyUnicode_AsUTF8AndSize(py_name, &length);
  if (!name) {
    return nullptr;
  }
  if (memchr(name, '\0', length)) {
    PyErr_SetString(PyExc_ValueError, "Collection names cannot contain NUL");
    return nullptr;
  }
  Main *bmain = nullptr;
  const char *error = nullptr;
  if (!ED_angelo_undo_scope_validate(
          BPY_context_get(), operation.scope->capture, &bmain, nullptr, &error))
  {
    PyErr_SetString(PyExc_RuntimeError, error);
    return nullptr;
  }
  char safe_name[MAX_ID_NAME - 2];
  BLI_strncpy(safe_name, name, sizeof(safe_name));
  BLI_str_utf8_invalid_strip(safe_name, strlen(safe_name));
  Collection *collection = BKE_collection_add(bmain, nullptr, safe_name);
  operation.scope->created_collections.append(
      {&collection->id, collection->id.session_uid, ID_GR});
  WM_main_add_notifier(NC_ID | NA_ADDED, nullptr);
  return pyrna_id_CreatePyObject(&collection->id);
}

static constexpr const char *engine_capsule_name = "angelo.native_commit_engine";
struct CommitEngineScope {
  PyObject *owner;
  AngeloCommitEngineRight *right;
};
static void engine_capsule_free(PyObject *capsule)
{
  auto *engine = static_cast<CommitEngineScope *>(PyCapsule_GetPointer(capsule, engine_capsule_name));
  if (engine) {
    ED_angelo_commit_engine_free(engine->right);
    Py_DECREF(engine->owner);
    MEM_delete(engine);
  }
}
static PyObject *undo_engine_prepare(PyObject * /*self*/, PyObject *args)
{
  PyObject *capsule, *scene, *expected, *target;
  if (!PyArg_ParseTuple(args, "OOOO:engine_prepare", &capsule, &scene, &expected, &target)) {
    return nullptr;
  }
  CommitOperation operation(capsule);
  if (!operation.scope) {
    return nullptr;
  }
  PyObject *tuple = PyTuple_Pack(1, scene);
  if (!tuple) {
    return nullptr;
  }
  Vector<AngeloCommitIDIdentity> scene_ids;
  const bool valid = commit_identities(*operation.scope, tuple, scene_ids);
  Py_DECREF(tuple);
  Py_ssize_t before_length, after_length;
  const char *before = valid ? PyUnicode_AsUTF8AndSize(expected, &before_length) : nullptr;
  const char *after = before ? PyUnicode_AsUTF8AndSize(target, &after_length) : nullptr;
  if (!after) {
    return nullptr;
  }
  if (memchr(before, '\0', before_length) || memchr(after, '\0', after_length)) {
    PyErr_SetString(PyExc_ValueError, "Engine identifiers cannot contain NUL");
    return nullptr;
  }
  const char *error = nullptr;
  AngeloCommitEngineRight *right = ED_angelo_commit_engine_prepare(
      BPY_context_get(), operation.scope->capture, scene_ids[0], before, after, &error);
  if (!right) {
    PyErr_SetString(PyExc_RuntimeError, error ? error : "The exact engine transition was refused");
    return nullptr;
  }
  auto *engine = MEM_new<CommitEngineScope>(__func__);
  engine->owner = Py_NewRef(capsule);
  engine->right = right;
  PyObject *result = PyCapsule_New(engine, engine_capsule_name, engine_capsule_free);
  if (!result) {
    ED_angelo_commit_engine_free(right);
    Py_DECREF(engine->owner);
    MEM_delete(engine);
  }
  return result;
}
static PyObject *undo_engine_apply(PyObject * /*self*/, PyObject *args)
{
  PyObject *capsule, *engine_capsule, *py_rollback = nullptr;
  if (!PyArg_ParseTuple(args, "OO|O:engine_apply", &capsule, &engine_capsule, &py_rollback)) {
    return nullptr;
  }
  CommitOperation operation(capsule);
  if (!operation.scope) {
    return nullptr;
  }
  const int rollback = py_rollback ? PyObject_IsTrue(py_rollback) : 0;
  if (rollback == -1) {
    return nullptr;
  }
  auto *engine = static_cast<CommitEngineScope *>(
      PyCapsule_GetPointer(engine_capsule, engine_capsule_name));
  if (!engine) {
    return nullptr;
  }
  if (engine->owner != capsule) {
    PyErr_SetString(PyExc_ValueError, "The engine transition belongs to another native commit");
    return nullptr;
  }
  const char *error = nullptr;
  AngeloCommitEngineResult outcome;
  const bool success = ED_angelo_commit_engine_apply(BPY_context_get(), operation.scope->capture,
                                                     *engine->right, rollback, outcome, &error);
  PyObject *error_type = nullptr, *error_value = nullptr, *error_traceback = nullptr;
  PyErr_Fetch(&error_type, &error_value, &error_traceback);
  const std::string callback_error = error_type ? PyExceptionClass_Name(error_type) : "";
  PyObject *result = Py_BuildValue("{s:O,s:O,s:O,s:O,s:s,s:s,s:s}",
      "success", success && callback_error.empty() ? Py_True : Py_False,
      "setter_called", outcome.setter_called ? Py_True : Py_False,
      "update_called", outcome.update_called ? Py_True : Py_False,
      "scope_valid_after", outcome.scope_valid_after ? Py_True : Py_False,
      "actual_engine", outcome.actual_engine.c_str(), "error", error ? error : "",
      "callback_error", callback_error.c_str());
  Py_XDECREF(error_type);
  Py_XDECREF(error_value);
  Py_XDECREF(error_traceback);
  return result;
}

#ifdef WITH_GTESTS
namespace sculpt_undo_fixture = ed::sculpt_paint::undo;
static PyObject *test_fail_next_checkpoint_install(PyObject * /*self*/, PyObject * /*args*/)
{
  WM_checkpoint_test_fail_next_install();
  Py_RETURN_NONE;
}

static PyObject *test_restore_phase(PyObject * /*self*/, PyObject * /*args*/)
{
  switch (BKE_undosys_restore_phase()) {
    case UndoRestorePhase::Inactive:
      return PyUnicode_FromString("Inactive");
    case UndoRestorePhase::Preparing:
      return PyUnicode_FromString("Preparing");
    case UndoRestorePhase::Finalizing:
      return PyUnicode_FromString("Finalizing");
    case UndoRestorePhase::Checkpoint:
      return PyUnicode_FromString("Checkpoint");
  }
  Py_UNREACHABLE();
}

static constexpr const char *fault_capsule_name = "angelo.test_undo_fault";
struct TestFaultScope {
  std::shared_ptr<sculpt_undo_fixture::MainUndoTestFault> fault;
  int step_index;
  unsigned int object_index;
  int grid_owner_index;
};

static void test_fault_free(PyObject *capsule)
{
  auto *scope = static_cast<TestFaultScope *>(PyCapsule_GetPointer(capsule, fault_capsule_name));
  if (scope) {
    sculpt_undo_fixture::main_undo_state_test_fault_disarm(*scope->fault);
    MEM_delete(scope);
  }
}

static PyObject *test_arm_restore_failure(PyObject * /*self*/, PyObject *args)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  int step_index;
  unsigned int object_index;
  int grid_owner_index = -1;
  if (!PyArg_ParseTuple(args, "iI|i", &step_index, &object_index, &grid_owner_index)) {
    return nullptr;
  }
  const char *error = nullptr;
  auto fault = sculpt_undo_fixture::main_undo_test_fault_arm_index(
      BPY_context_get(), step_index, object_index, &error, grid_owner_index);
  if (!fault) {
    PyErr_SetString(PyExc_RuntimeError, error ? error : "Cannot arm the fixture fault");
    return nullptr;
  }
  auto *scope = MEM_new<TestFaultScope>(__func__);
  scope->fault = std::move(fault);
  scope->step_index = step_index;
  scope->object_index = object_index;
  scope->grid_owner_index = grid_owner_index;
  PyObject *capsule = PyCapsule_New(scope, fault_capsule_name, test_fault_free);
  if (!capsule) {
    sculpt_undo_fixture::main_undo_state_test_fault_disarm(*scope->fault);
    MEM_delete(scope);
  }
  return capsule;
}

static PyObject *test_disarm_restore_failure(PyObject * /*self*/, PyObject *capsule)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  auto *scope = static_cast<TestFaultScope *>(PyCapsule_GetPointer(capsule, fault_capsule_name));
  if (!scope) {
    return nullptr;
  }
  const bool consumed = sculpt_undo_fixture::main_undo_state_test_fault_consumed(*scope->fault);
  sculpt_undo_fixture::main_undo_state_test_fault_disarm(*scope->fault);
  return Py_BuildValue("{s:O,s:i,s:I,s:i,s:I,s:I}",
                       "consumed",
                       consumed ? Py_True : Py_False,
                       "step_index",
                       scope->step_index,
                       "object_index", scope->object_index,
                       "grid_owner_index", scope->grid_owner_index,
                       "object_uid", sculpt_undo_fixture::main_undo_state_test_fault_object_uid(*scope->fault),
                       "owner_scene_uid", sculpt_undo_fixture::main_undo_state_test_fault_scene_uid(*scope->fault));
}

/* Fixture-only observation: no index/table ensure, evaluation, flush, or history mutation. */
static PyObject *test_sculpt_surface(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  Object *object = CTX_data_active_object(BPY_context_get());
  SculptSession *ss = object ? object->runtime->sculpt_session : nullptr;
  if (!ss || !ss->bm || ss->cache || ss->filter_cache || ss->expand_cache) {
    PyErr_SetString(PyExc_RuntimeError, "The fixture requires an idle live Dyntopo surface");
    return nullptr;
  }
  BMesh *bm = ss->bm;
  Map<const BMVert *, int> indices;
  PyObject *positions = PyList_New(bm->totvert);
  PyObject *faces = PyList_New(bm->totface);
  PyObject *masks = PyList_New(bm->totvert);
  PyObject *hidden = PyList_New(bm->totvert);
  if (!positions || !faces || !masks || !hidden) {
    Py_XDECREF(positions);
    Py_XDECREF(faces);
    Py_XDECREF(masks);
    Py_XDECREF(hidden);
    return nullptr;
  }
  const int mask_offset = CustomData_get_offset_named(&bm->vdata, CD_PROP_FLOAT, ".sculpt_mask");
  BMIter iter;
  BMVert *vert;
  int index;
  BM_ITER_MESH_INDEX (vert, &iter, bm, BM_VERTS_OF_MESH, index) {
    indices.add_new(vert, index);
    PyList_SET_ITEM(
        positions, index, Py_BuildValue("(fff)", vert->co[0], vert->co[1], vert->co[2]));
    PyList_SET_ITEM(
        masks,
        index,
        PyFloat_FromDouble(mask_offset == -1 ? 0.0 : BM_ELEM_CD_GET_FLOAT(vert, mask_offset)));
    PyList_SET_ITEM(hidden, index, PyBool_FromLong(BM_elem_flag_test(vert, BM_ELEM_HIDDEN) != 0));
  }
  if (PyErr_Occurred()) {
    Py_DECREF(positions);
    Py_DECREF(faces);
    Py_DECREF(masks);
    Py_DECREF(hidden);
    return nullptr;
  }
  BMFace *face;
  BM_ITER_MESH_INDEX (face, &iter, bm, BM_FACES_OF_MESH, index) {
    PyObject *corners = PyTuple_New(face->len);
    if (!corners) {
      Py_DECREF(positions);
      Py_DECREF(faces);
      Py_DECREF(masks);
      Py_DECREF(hidden);
      return nullptr;
    }
    const BMLoop *loop = BM_FACE_FIRST_LOOP(face);
    for (int corner = 0; corner < face->len; corner++, loop = loop->next) {
      PyTuple_SET_ITEM(corners, corner, PyLong_FromLong(indices.lookup(loop->v)));
    }
    PyList_SET_ITEM(faces, index, corners);
  }
  if (PyErr_Occurred()) {
    Py_DECREF(positions);
    Py_DECREF(faces);
    Py_DECREF(masks);
    Py_DECREF(hidden);
    return nullptr;
  }
  return Py_BuildValue("{s:N,s:N,s:N,s:N}",
                       "positions",
                       positions,
                       "topology",
                       faces,
                       "masks",
                       masks,
                       "hidden",
                       hidden);
}

/* Consume one new reference on both success and failure. This also handles allocation failure
 * before PyDict_SetItemString, without clearing the original Python exception. */
static bool test_dict_set_owned(PyObject *dict, const char *key, PyObject *value)
{
  if (!value) {
    return false;
  }
  const int result = PyDict_SetItemString(dict, key, value);
  Py_DECREF(value);
  return result == 0;
}

static PyObject *test_optional_storage_bytes(const void *data, const size_t bytes, size_t &budget)
{
  if (!data) {
    Py_RETURN_NONE;
  }
  if (bytes > size_t(PY_SSIZE_T_MAX) || bytes > budget) {
    PyErr_SetString(PyExc_ValueError, "Original-storage fixture payload exceeds 64 MiB");
    return nullptr;
  }
  budget -= bytes;
  return PyBytes_FromStringAndSize(static_cast<const char *>(data), Py_ssize_t(bytes));
}

static PyObject *test_original_grid_layers(const Mesh &mesh, size_t &budget)
{
  PyObject *result = PyDict_New();
  if (!result) {
    return nullptr;
  }
  const CustomData &data = mesh.corner_data;
  if (!test_dict_set_owned(
          result,
          "external_path",
          data.external ? PyUnicode_FromStringAndSize(
                              data.external->filepath,
                              strnlen(data.external->filepath, sizeof(data.external->filepath))) :
                          Py_NewRef(Py_None)))
  {
    Py_DECREF(result);
    return nullptr;
  }
  for (const eCustomDataType type : {CD_MDISPS, CD_GRID_PAINT_MASK}) {
    const char *name = type == CD_MDISPS ? "mdisps" : "grid_paint_mask";
    const CustomDataLayer *layer = nullptr;
    for (int i = 0; i < data.totlayer; ++i) {
      if (data.layers[i].type == type) {
        if (layer) {
          PyErr_SetString(PyExc_ValueError, "Fixture requires one original grid layer per type");
          Py_DECREF(result);
          return nullptr;
        }
        layer = &data.layers[i];
      }
    }
    if (!layer) {
      if (!test_dict_set_owned(result, name, Py_NewRef(Py_None))) {
        Py_DECREF(result);
        return nullptr;
      }
      continue;
    }
    PyObject *record = Py_BuildValue("{s:i,s:i,s:s,s:O}",
                                     "flag",
                                     layer->flag,
                                     "uid",
                                     layer->uid,
                                     "name",
                                     layer->name,
                                     "data_present",
                                     layer->data ? Py_True : Py_False);
    if (!record || !test_dict_set_owned(result, name, record)) {
      Py_DECREF(result);
      return nullptr;
    }
    /* Null external payload is observed as absent, never loaded. */
    PyObject *values = PyList_New(layer->data ? mesh.corners_num : 0);
    if (!values || !test_dict_set_owned(record, "values", values)) {
      Py_DECREF(result);
      return nullptr;
    }
    for (int i = 0; layer->data && i < mesh.corners_num; ++i) {
      PyObject *item = PyDict_New();
      if (!item) {
        Py_DECREF(result);
        return nullptr;
      }
      PyList_SET_ITEM(values, i, item);
      if (type == CD_MDISPS) {
        const MDisps &grid = static_cast<const MDisps *>(layer->data)[i];
        if (grid.totdisp < 0 || grid.totdisp > 1024 * 1024) {
          PyErr_SetString(PyExc_ValueError, "Original MDisps fixture size is invalid");
          Py_DECREF(result);
          return nullptr;
        }
        if (!test_dict_set_owned(item, "count", PyLong_FromLong(grid.totdisp)) ||
            !test_dict_set_owned(item, "level", PyLong_FromLong(grid.level)) ||
            !test_dict_set_owned(item,
                                 "positions",
                                 test_optional_storage_bytes(
                                     grid.disps, size_t(grid.totdisp) * sizeof(float[3]), budget)))
        {
          Py_DECREF(result);
          return nullptr;
        }
        if (grid.hidden && size_t(grid.totdisp) > budget) {
          PyErr_SetString(PyExc_ValueError, "Original hidden payload exceeds fixture budget");
          Py_DECREF(result);
          return nullptr;
        }
        budget -= grid.hidden ? size_t(grid.totdisp) : 0;
        PyObject *hidden = grid.hidden ? PyBytes_FromStringAndSize(nullptr, grid.totdisp) :
                                         Py_NewRef(Py_None);
        if (!hidden) {
          Py_DECREF(result);
          return nullptr;
        }
        if (grid.hidden) {
          char *bytes = PyBytes_AS_STRING(hidden);
          for (int bit = 0; bit < grid.totdisp; ++bit) {
            bytes[bit] = BLI_BITMAP_TEST(grid.hidden, bit) ? 1 : 0;
          }
        }
        if (!test_dict_set_owned(item, "hidden", hidden)) {
          Py_DECREF(result);
          return nullptr;
        }
      }
      else {
        const GridPaintMask &grid = static_cast<const GridPaintMask *>(layer->data)[i];
        if (grid.level > 10 || (grid.data && grid.level < 1)) {
          PyErr_SetString(PyExc_ValueError, "Original mask fixture level is invalid");
          Py_DECREF(result);
          return nullptr;
        }
        const size_t side = grid.level ? (size_t(1) << (grid.level - 1)) + 1 : 0;
        if (!test_dict_set_owned(item, "level", PyLong_FromUnsignedLong(grid.level)) ||
            !test_dict_set_owned(
                item,
                "masks",
                test_optional_storage_bytes(grid.data, side * side * sizeof(float), budget)))
        {
          Py_DECREF(result);
          return nullptr;
        }
      }
    }
  }
  return result;
}

/* Registration: _test_original_sculpt_storage, METH_NOARGS, WITH_GTESTS only.
 * The fixture compares this result with original Key RNA coordinates/scalars and artist
 * selection. Limits below are fixture admission, not production capture limits. */
static PyObject *test_original_sculpt_storage(PyObject *, PyObject *)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  bContext *C = BPY_context_get();
  Main *bmain = C ? CTX_data_main(C) : nullptr;
  if (!bmain) {
    PyErr_SetString(PyExc_RuntimeError, "The original-storage fixture requires a document");
    return nullptr;
  }
  for (const wmWindowManager &wm : bmain->wm) {
    if (WM_jobs_has_running(&wm)) {
      PyErr_SetString(PyExc_RuntimeError, "The original-storage fixture requires idle jobs");
      return nullptr;
    }
  }
  Set<const Mesh *> meshes;
  for (const Object &object : bmain->objects) {
    if ((object.mode & OB_MODE_SCULPT) && object.type == OB_MESH && object.data) {
      const Mesh *mesh = id_cast<Mesh *>(object.data);
      if (mesh->corners_num < 0 || mesh->corners_num > 4096 ||
          (!meshes.contains(mesh) && meshes.size() >= 4))
      {
        PyErr_SetString(PyExc_ValueError, "Original-storage fixture mesh count/size exceeded");
        return nullptr;
      }
      meshes.add(mesh);
    }
  }
  PyObject *result = PyDict_New();
  if (!result) {
    return nullptr;
  }
  size_t budget = 64 * 1024 * 1024;
  for (const Mesh *mesh : meshes) {
    PyObject *uid = PyLong_FromUnsignedLong(mesh->id.session_uid);
    PyObject *record = test_original_grid_layers(*mesh, budget);
    if (!uid || !record || PyDict_SetItem(result, uid, record) < 0) {
      Py_XDECREF(uid);
      Py_XDECREF(record);
      Py_DECREF(result);
      return nullptr;
    }
    Py_DECREF(uid);
    Py_DECREF(record);
  }
  return result;
}

template<typename T> static PyObject *test_grid_array_bytes(const Span<T> values)
{
  if (uint64_t(values.size()) > uint64_t(PY_SSIZE_T_MAX) / sizeof(T)) {
    PyErr_SetString(PyExc_OverflowError, "The fixture grid array exceeds Python's byte limit");
    return nullptr;
  }
  return PyBytes_FromStringAndSize(reinterpret_cast<const char *>(values.data()),
                                   Py_ssize_t(values.size() * sizeof(T)));
}

static PyObject *test_grid_hidden_bytes(const BitGroupVector<0> &hidden)
{
  if (hidden.group_size() != 0 &&
      uint64_t(hidden.size()) > uint64_t(PY_SSIZE_T_MAX) / uint64_t(hidden.group_size()))
  {
    PyErr_SetString(PyExc_OverflowError,
                    "The fixture hidden grid array exceeds Python's byte limit");
    return nullptr;
  }
  const Py_ssize_t size = Py_ssize_t(hidden.size() * hidden.group_size());
  PyObject *result = PyBytes_FromStringAndSize(nullptr, size);
  if (!result) {
    return nullptr;
  }
  /* Export only logical bits, never allocator padding or a view into native storage. */
  char *data = PyBytes_AS_STRING(result);
  Py_ssize_t index = 0;
  for (const int64_t group : hidden.index_range()) {
    const BoundedBitSpan bits = hidden[group];
    for (const int64_t bit : bits.index_range()) {
      data[index++] = bits[bit] ? 1 : 0;
    }
  }
  return result;
}

static PyObject *test_grid_observation_dict(
    const sculpt_undo_fixture::MainUndoGridObservation &observation)
{
  static_assert(sizeof(float) == 4 && sizeof(int) == 4 && sizeof(float3) == 12);
  PyObject *result = PyDict_New();
  if (!result) {
    return nullptr;
  }
  const bool success =
      test_dict_set_owned(result, "object_uid", PyLong_FromUnsignedLong(observation.object_uid)) &&
      test_dict_set_owned(result, "mesh_uid", PyLong_FromUnsignedLong(observation.mesh_uid)) &&
      test_dict_set_owned(result, "scene_uid", PyLong_FromUnsignedLong(observation.scene_uid)) &&
      test_dict_set_owned(result,
                          "scene_original_session_uid",
                          PyLong_FromUnsignedLong(observation.scene_original_session_uid)) &&
      test_dict_set_owned(
          result, "view_layer", PyUnicode_FromString(observation.view_layer.c_str())) &&
      test_dict_set_owned(
          result, "graph_address", PyLong_FromUnsignedLongLong(observation.graph_address)) &&
      test_dict_set_owned(
          result, "ccg_address", PyLong_FromUnsignedLongLong(observation.ccg_address)) &&
      test_dict_set_owned(result, "frame", PyFloat_FromDouble(observation.frame)) &&
      test_dict_set_owned(result, "ctime", PyFloat_FromDouble(observation.ctime)) &&
      test_dict_set_owned(result, "canonical", PyBool_FromLong(observation.canonical)) &&
      test_dict_set_owned(result, "viewport", PyBool_FromLong(observation.viewport)) &&
      test_dict_set_owned(result, "active", PyBool_FromLong(observation.active)) &&
      test_dict_set_owned(result,
                          "visibility_optimization",
                          PyBool_FromLong(observation.visibility_optimization)) &&
      test_dict_set_owned(
          result, "render_pipeline", PyBool_FromLong(observation.render_pipeline)) &&
      test_dict_set_owned(result, "editors_update", PyBool_FromLong(observation.editors_update)) &&
      test_dict_set_owned(result, "sync_writeback", PyLong_FromLong(observation.sync_writeback)) &&
      test_dict_set_owned(result, "debug_flags", PyLong_FromLong(observation.debug_flags)) &&
      test_dict_set_owned(
          result, "pending_updates", PyBool_FromLong(observation.pending_updates)) &&
      test_dict_set_owned(result, "authority", PyBool_FromLong(observation.authority)) &&
      test_dict_set_owned(result, "dirty_coords", PyBool_FromLong(observation.dirty_coords)) &&
      test_dict_set_owned(result, "dirty_hidden", PyBool_FromLong(observation.dirty_hidden)) &&
      test_dict_set_owned(
          result, "session_needs_flush", PyBool_FromLong(observation.session_needs_flush)) &&
      test_dict_set_owned(result, "level", PyLong_FromLong(observation.level)) &&
      test_dict_set_owned(result, "grid_size", PyLong_FromLong(observation.grid_size)) &&
      test_dict_set_owned(result, "grids_num", PyLong_FromLong(observation.grids_num)) &&
      test_dict_set_owned(
          result, "hidden_groups", PyLong_FromLongLong(observation.hidden.size())) &&
      test_dict_set_owned(
          result, "hidden_group_size", PyLong_FromLongLong(observation.hidden.group_size())) &&
      test_dict_set_owned(
          result, "face_offsets", test_grid_array_bytes(observation.face_offsets.as_span())) &&
      test_dict_set_owned(
          result, "grid_to_face", test_grid_array_bytes(observation.grid_to_face.as_span())) &&
      test_dict_set_owned(
          result, "positions", test_grid_array_bytes(observation.positions.as_span())) &&
      test_dict_set_owned(
          result, "normals", test_grid_array_bytes(observation.normals.as_span())) &&
      test_dict_set_owned(result, "masks", test_grid_array_bytes(observation.masks.as_span())) &&
      test_dict_set_owned(result, "hidden", test_grid_hidden_bytes(observation.hidden)) &&
      test_dict_set_owned(result,
                          "pending_normal_faces",
                          test_grid_array_bytes(observation.pending_normal_faces.as_span()));
  if (!success) {
    Py_DECREF(result);
    return nullptr;
  }
  return result;
}

/* The factory fixture owns an idle Main. Main-thread access alone does not provide exclusive
 * access against jobs; the inventory retains its own production admission requirements. */
static PyObject *test_sculpt_grid_surfaces(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  bContext *context = BPY_context_get();
  Main *bmain = context ? CTX_data_main(context) : nullptr;
  if (!bmain) {
    PyErr_SetString(PyExc_RuntimeError, "The grid fixture requires an active document");
    return nullptr;
  }
  const bool main_flush_needed = bmain->is_memfile_undo_flush_needed;
  Vector<sculpt_undo_fixture::MainUndoGridObservation> observations;
  const char *error = nullptr;
  if (!sculpt_undo_fixture::main_undo_grids_observe(*bmain, observations, &error)) {
    PyErr_SetString(PyExc_RuntimeError,
                    error ? error : "The fixture grid inventory is unavailable");
    return nullptr;
  }
  if (uint64_t(observations.size()) > uint64_t(PY_SSIZE_T_MAX)) {
    PyErr_SetString(PyExc_OverflowError, "The fixture grid inventory exceeds Python's list limit");
    return nullptr;
  }
  PyObject *surfaces = PyList_New(Py_ssize_t(observations.size()));
  if (!surfaces) {
    return nullptr;
  }
  for (const int64_t index : observations.index_range()) {
    PyObject *surface = test_grid_observation_dict(observations[index]);
    if (!surface) {
      Py_DECREF(surfaces);
      return nullptr;
    }
    PyList_SET_ITEM(surfaces, Py_ssize_t(index), surface);
  }
  PyObject *result = PyDict_New();
  if (!result) {
    Py_DECREF(surfaces);
    return nullptr;
  }
  /* Set the list first so every later failure is handled by decrefing its owning dictionary. */
  if (!test_dict_set_owned(result, "surfaces", surfaces) ||
      !test_dict_set_owned(result, "format_version", PyLong_FromLong(1)) ||
      !test_dict_set_owned(result,
                           "byte_order",
                           PyUnicode_FromString(ENDIAN_ORDER == L_ENDIAN ? "little" : "big")) ||
      !test_dict_set_owned(result, "profile_complete", PyBool_FromLong(false)) ||
      !test_dict_set_owned(result, "main_flush_needed", PyBool_FromLong(main_flush_needed)))
  {
    Py_DECREF(result);
    return nullptr;
  }
  return result;
}
#endif

static PyMethodDef methods[] = {
    {"publication", undo_publication, METH_O,
     "Read this consumed scope publication outcome; never inspect history names or positions."},
    {"seal_approved_objects", undo_seal_approved_objects, METH_VARARGS,
     "Seal the trusted original approved Object identities once for this commit."},
    {"stage_owned", undo_stage_owned, METH_O,
     "Read complete current native stage identities; import callbacks do not grant stage ownership."},
    {"stage_residue", undo_stage_residue, METH_O,
     "Observe exact unresolved native stage registration identities without granting deletion rights."},
    {"delete_stage", undo_delete_stage, METH_VARARGS, "Delete exact native-owned stage data."},
    {"delete_approved_objects", undo_delete_approved_objects, METH_VARARGS,
     "Delete exact originally approved Objects."},
    {"new_collection", undo_new_collection, METH_VARARGS,
     "Create a Collection and retain its native creation identity for rollback."},
    {"delete_created_collections", undo_delete_created_collections, METH_VARARGS,
     "Delete exact empty/unlinked Collections created by this commit."},
    {"engine_prepare", undo_engine_prepare, METH_VARARGS, "Bind one exact admitted engine transition."},
    {"engine_apply", undo_engine_apply, METH_VARARGS, "Apply the bound engine transition or its inverse."},
    {"begin_synchronized", undo_begin_synchronized, METH_NOARGS,
     "Protect typed commit owners, synchronize Sculpt graphs, and capture the exact before state."},
    {"finish_synchronized", undo_finish_synchronized, METH_VARARGS,
     "Publish and finalize editors, retaining ownership for a synchronous terminal readback."},
    {"close", undo_close, METH_O,
     "Release a finalized typed commit scope without evaluation or callbacks."},
    {"keep_background_document", undo_keep_background_document, METH_NOARGS,
     "Keep this background process's document as a window does: every load starts the loaded "
     "document's own Undo history and reads as saved, as every save of the open file does; True "
     "when the open document has an active Undo step."},
    {"background_document_saved", undo_background_document_saved, METH_NOARGS,
     "In a kept background document's process: its state was saved through a copy, so it reads as "
     "saved. False, changing nothing, in any other process."},
    {"begin", undo_begin, METH_NOARGS, "Begin one synchronous main-thread native commit scope."},
    {"finish", undo_finish, METH_VARARGS, "Consume the scope and report exact Undo publication."},
#ifdef WITH_GTESTS
    {"_test_restore_phase", test_restore_phase, METH_NOARGS, "Observe the scoped restore phase."},
    {"_test_fail_next_checkpoint_install",
     test_fail_next_checkpoint_install,
     METH_NOARGS,
     "Make the next Sculpt checkpoint install refuse after replacing the document."},
    {"_test_original_sculpt_storage",
     test_original_sculpt_storage,
     METH_NOARGS,
     "Copy existing original Sculpt Mesh displacement/mask storage; never load or evaluate."},
    {"_test_arm_restore_failure",
     test_arm_restore_failure,
     METH_VARARGS,
     "Arm one exact fixture snapshot validator."},
    {"_test_disarm_restore_failure",
     test_disarm_restore_failure,
     METH_O,
     "Disarm and report the fixture's consumed boundary."},
    {"_test_sculpt_surface",
     test_sculpt_surface,
     METH_NOARGS,
     "Read the live BMesh for native fixtures."},
    {"_test_sculpt_grid_surfaces",
     test_sculpt_grid_surfaces,
     METH_NOARGS,
     "Copy existing graph-bound Sculpt grids for idle factory fixtures; never evaluate."},
#endif
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_bpy._angelo_undo",
    nullptr,
    0,
    methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

PyObject *BPY_angelo_undo_module()
{
  return PyModule_Create(&module);
}

}  // namespace blender
