/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edundo
 *
 * Finite effects inside the synchronous native commit owner. Public admissions stay active:
 * these rights call stock BKE deletion and the exact RNA engine setter/update directly.
 */

#include "angelo_commit_rights.hh"

#include <cstring>
#include <utility>

#include "MEM_guardedalloc.h"

#include "BLI_map.hh"
#include "BLI_set.hh"
#include "BLI_threads.h"

#include "DNA_collection_types.h"
#include "DNA_key_types.h"
#include "DNA_scene_types.h"

#include "BKE_key.hh"
#include "BKE_lib_id.hh"
#include "BKE_lib_override.hh"
#include "BKE_lib_query.hh"
#include "BKE_main.hh"

#include "ED_undo.hh"

#include "RNA_access.hh"
#include "RNA_prototypes.hh"

namespace blender {

bool AngeloCommitIDIdentity::operator==(const AngeloCommitIDIdentity &other) const
{
  return pointer == other.pointer && session_uid == other.session_uid && id_type == other.id_type;
}

namespace {

struct ScopeIdentity {
  AngeloUndoCapture *capture = nullptr;
  Main *main = nullptr;
  UndoStack *stack = nullptr;
};

static bool operation_scope(bContext *C,
                            AngeloUndoCapture *capture,
                            ScopeIdentity &r_scope,
                            const char **r_error)
{
  if (!BLI_thread_is_main() || !capture || !ED_angelo_undo_scope_operation_active(capture)) {
    *r_error = "Native commit rights require the capture's exclusive main-thread operation";
    return false;
  }
  Main *main = nullptr;
  UndoStack *stack = nullptr;
  if (!ED_angelo_undo_scope_validate(C, capture, &main, &stack, r_error)) {
    return false;
  }
  r_scope = {capture, main, stack};
  return true;
}

static bool same_scope(bContext *C,
                       AngeloUndoCapture *capture,
                       const ScopeIdentity &expected,
                       const char **r_error)
{
  ScopeIdentity current;
  if (!operation_scope(C, capture, current, r_error)) {
    return false;
  }
  if (current.capture != expected.capture || current.main != expected.main ||
      current.stack != expected.stack)
  {
    *r_error = "The native commit right belongs to another capture, Main or Undo stack";
    return false;
  }
  return true;
}

static AngeloCommitIDIdentity identity_of(ID *id)
{
  return {id, id->session_uid, short(GS(id->name))};
}

struct IDLink {
  ID *owner;
  ID *self;
  ID *target;
  LibraryForeachIDCallbackFlag flags;
};

/**
 * One current native inventory, including embedded ownership. Candidate pointers are never read
 * until matched against this inventory. No Python/RNA wrapper or cached Main relation is used.
 */
struct LiveIDs {
  Set<ID *> main_ids;
  Map<ID *, AngeloCommitIDIdentity> identities;
  Map<ID *, Vector<ID *>> embedded;
  Set<ID *> invalid_embedded_owners;
  Map<ID *, Vector<ID *>> never_null_users;
  Vector<IDLink> links;

  explicit LiveIDs(Main *main)
  {
    ID *id;
    FOREACH_MAIN_ID_BEGIN (main, id) {
      main_ids.add(id);
      identities.add(id, identity_of(id));
    }
    FOREACH_MAIN_ID_END;
    FOREACH_MAIN_ID_BEGIN (main, id) {
      BKE_library_foreach_ID_link(
          main,
          id,
          [this](LibraryIDLinkCallbackData *data) {
            ID *target = *data->id_pointer;
            if (!target) {
              return IDWALK_RET_NOP;
            }
            links.append({data->owner_id, data->self_id, target, data->cb_flag});
            if (data->cb_flag & IDWALK_CB_EMBEDDED) {
              identities.add(target, identity_of(target));
              embedded.lookup_or_add_default(data->owner_id).append(target);
              if (!(target->flag & ID_FLAG_EMBEDDED_DATA) ||
                  BKE_id_owner_get(target, false) != data->owner_id || main_ids.contains(target))
              {
                invalid_embedded_owners.add(data->owner_id);
              }
            }
            /* Match lib_remap's owning-embedded and embedded-to-owner exclusions. Runtime
             * NEVER_NULL links still count: stock deletion forces those remaps as well. */
            const bool embedded_loopback = data->self_id != data->owner_id &&
                                           (data->cb_flag & IDWALK_CB_LOOPBACK) &&
                                           target == data->owner_id;
            if ((data->cb_flag & IDWALK_CB_NEVER_NULL) &&
                !(data->cb_flag & IDWALK_CB_EMBEDDED) && !embedded_loopback)
            {
              never_null_users.lookup_or_add_default(target).append(data->owner_id);
            }
            return IDWALK_RET_NOP;
          },
          nullptr,
          IDWALK_READONLY | IDWALK_INCLUDE_UI | IDWALK_DO_INTERNAL_RUNTIME_POINTERS |
              IDWALK_DO_LIBRARY_POINTER);
    }
    FOREACH_MAIN_ID_END;
  }

  bool matches(const AngeloCommitIDIdentity &identity) const
  {
    const AngeloCommitIDIdentity *current = identities.lookup_ptr(identity.pointer);
    return current && *current == identity;
  }
};

struct DeleteProof {
  Set<ID *> main_ids;
  Vector<AngeloCommitIDIdentity> identities;
};

static bool is_local_owned_id(const ID *id)
{
  return !ID_IS_LINKED(id) && !ID_IS_OVERRIDE_LIBRARY(id) &&
         !(id->tag & (ID_TAG_NO_MAIN | ID_TAG_NO_USER_REFCOUNT | ID_TAG_TEMP_MAIN));
}

static bool purpose_allows(const ScopeIdentity &scope,
                           const AngeloCommitDeletePurpose purpose,
                           ID *id,
                           const char **r_error)
{
  if (ELEM(GS(id->name), ID_LI, ID_WM, ID_SCR, ID_WS) ||
      ED_angelo_undo_scope_id_is_protected(scope.capture, id))
  {
    *r_error = "The deletion closure contains a protected document, editor or graph owner";
    return false;
  }
  switch (purpose) {
    case AngeloCommitDeletePurpose::PrivateStage:
      if (!is_local_owned_id(id)) {
        *r_error = "Private stage cleanup requires native-owned local IDs";
        return false;
      }
      break;
    case AngeloCommitDeletePurpose::ApprovedObject:
      /* Existing approved Object deletion also admits linked/local-override Objects. Its
       * caller supplies that approval; do not silently replace it with a local-only policy. */
      if (GS(id->name) != ID_OB) {
        *r_error = "Approved Object deletion cannot authorize another ID family";
        return false;
      }
      break;
    case AngeloCommitDeletePurpose::CreatedCollection:
      if (GS(id->name) != ID_GR || !is_local_owned_id(id)) {
        *r_error = "Collection cleanup requires an exact newly created local Collection";
        return false;
      }
      if (const auto *collection = reinterpret_cast<const Collection *>(id);
          !collection->gobject.is_empty() || !collection->children.is_empty())
      {
        *r_error = "The newly created Collection is no longer empty";
        return false;
      }
      break;
  }
  return true;
}

static bool identity_map(const LiveIDs &live,
                          const Span<AngeloCommitIDIdentity> identities,
                          const bool require_live,
                          Map<ID *, AngeloCommitIDIdentity> &r_map,
                          const char **r_error)
{
  for (const AngeloCommitIDIdentity &identity : identities) {
    if (!identity.pointer || !identity.session_uid || !identity.id_type ||
        (require_live && !live.matches(identity)))
    {
      *r_error = "A native commit identity is missing, stale or has changed ID family";
      return false;
    }
    if (!r_map.add(identity.pointer, identity)) {
      *r_error = "A native commit identity occurs more than once in its manifest";
      return false;
    }
  }
  return true;
}

static bool prove_deletion(const ScopeIdentity &scope,
                            const AngeloCommitDeletePurpose purpose,
                            const Span<AngeloCommitIDIdentity> roots,
                            const Span<AngeloCommitIDIdentity> authorized,
                            const Span<AngeloCommitIDIdentity> keep,
                            const LiveIDs &live,
                            DeleteProof &r_proof,
                            const char **r_error)
{
  Map<ID *, AngeloCommitIDIdentity> authority, kept;
  if (!identity_map(live, authorized, false, authority, r_error) ||
      !identity_map(live, keep, true, kept, r_error))
  {
    return false;
  }
  Vector<ID *> pending;
  auto add_main_id = [&](ID *id) {
    if (r_proof.main_ids.contains(id)) {
      return true;
    }
    const AngeloCommitIDIdentity *authorized_id = authority.lookup_ptr(id);
    /* A native ownership ledger may retain earlier removed identities. Only a currently live,
     * exact candidate can exercise authority, including after address reuse. */
    const bool is_authorized = authorized_id && live.matches(*authorized_id);
    if (!live.main_ids.contains(id) || !is_authorized) {
      *r_error = "The deletion cascade reaches an ID outside the immutable authorized manifest";
      return false;
    }
    if (kept.contains(id)) {
      *r_error = "The deletion cascade reaches ownership retained by the live result";
      return false;
    }
    if (!purpose_allows(scope, purpose, id, r_error)) {
      return false;
    }
    if (live.invalid_embedded_owners.contains(id)) {
      *r_error = "A deletion owner no longer has exact native embedded ownership";
      return false;
    }
    r_proof.main_ids.add(id);
    pending.append(id);
    return true;
  };

  Set<ID *> root_pointers;
  for (const AngeloCommitIDIdentity &root : roots) {
    if (!live.matches(root) || !root_pointers.add(root.pointer)) {
      *r_error = "A deletion root is stale or repeated";
      return false;
    }
    if (!add_main_id(root.pointer)) {
      return false;
    }
  }
  /* The queue is a proof of stock's initial cascade, not another deletion implementation.
   * All actual remapping, cleanup and free ordering remain inside BKE_id_multi_delete. */
  for (int64_t index = 0; index < pending.size(); index++) {
    ID *id = pending[index];
    if (Key *key = BKE_key_from_id(id)) {
      /* Keys are separate Main IDs. A Mesh.key pointer can be changed, so native ownership
       * provenance must authorize the Key too; the pointer alone cannot grant deletion rights. */
      if (!add_main_id(&key->id)) {
        return false;
      }
    }
    if (const Vector<ID *> *users = live.never_null_users.lookup_ptr(id)) {
      for (ID *user : *users) {
        if (!add_main_id(user)) {
          return false;
        }
      }
    }
  }

  Set<ID *> all_deleted;
  for (ID *id : pending) {
    all_deleted.add(id);
    r_proof.identities.append(live.identities.lookup(id));
    if (const Vector<ID *> *children = live.embedded.lookup_ptr(id)) {
      for (ID *child : *children) {
        if (kept.contains(child) ||
            ED_angelo_undo_scope_id_is_protected(scope.capture, child))
        {
          *r_error = "The deletion closure contains a retained or protected embedded ID";
          return false;
        }
        if (all_deleted.add(child)) {
          r_proof.identities.append(live.identities.lookup(child));
        }
      }
    }
  }

  if (purpose == AngeloCommitDeletePurpose::ApprovedObject) {
    /* This is the independent fresh counterpart of program_commit._unlink_deleted's check. */
    for (const Scene &scene : scope.main->scenes) {
      if (scene.camera && r_proof.main_ids.contains(reinterpret_cast<ID *>(scene.camera))) {
        *r_error = "An approved Object is now a Scene's active camera";
        return false;
      }
    }
  }
  for (const IDLink &link : live.links) {
    if (!all_deleted.contains(link.target) || r_proof.main_ids.contains(link.owner)) {
      continue;
    }
    /* Runtime/ownership hints are remappable. Any NEVER_NULL user was already included above
     * and had to prove its own authority. Library-override references remain artist dependencies
     * even when they are marked weak. The approved-Object route retains user_map's checks for
     * non-Scene/non-Collection users, including their ordinary weak references. */
    const bool runtime_hint = link.flags &
                              (IDWALK_CB_INTERNAL | IDWALK_CB_LOOPBACK |
                               IDWALK_CB_EMBEDDED_NOT_OWNING);
    const bool weak_hint = (link.flags & IDWALK_CB_DIRECT_WEAK_LINK) &&
                           !(link.flags & IDWALK_CB_OVERRIDE_LIBRARY_REFERENCE) &&
                           (purpose != AngeloCommitDeletePurpose::ApprovedObject ||
                            ELEM(GS(link.owner->name), ID_SCE, ID_WM, ID_SCR, ID_WS));
    /* Editor caches that never reach a file (the Outliner tree-store: IDWALK_CB_WRITEFILE_IGNORE)
     * keep nothing alive; the stock deletion clears them through its editor remap callback. Any
     * Outliner that has drawn an Object holds one, so without this every approved deletion in a
     * window was retained. Only a screen owner (the space data holding that tree-store): a
     * window-manager or workspace link is never treated as such a cache. */
    const bool editor_cache = (link.flags & IDWALK_CB_WRITEFILE_IGNORE) &&
                              GS(link.owner->name) == ID_SCR;
    if (runtime_hint || weak_hint || editor_cache)
    {
      continue;
    }
    if (purpose == AngeloCommitDeletePurpose::ApprovedObject &&
        GS(link.owner->name) == ID_SCE && GS(link.self->name) != ID_GR)
    {
      /* Scene users are permitted by the existing approved-delete route, except cameras and
       * Collection membership. Scene master-Collection links arrive with self_id == Collection. */
      continue;
    }
    *r_error = "A surviving ID now depends on the deletion closure; cleanup was retained";
    return false;
  }
  return true;
}

static bool proof_matches(const DeleteProof &expected, const DeleteProof &current)
{
  if (expected.main_ids.size() != current.main_ids.size() ||
      expected.identities.size() != current.identities.size())
  {
    return false;
  }
  Map<ID *, AngeloCommitIDIdentity> identities;
  for (const AngeloCommitIDIdentity &identity : current.identities) {
    identities.add(identity.pointer, identity);
  }
  for (const AngeloCommitIDIdentity &identity : expected.identities) {
    const AngeloCommitIDIdentity *other = identities.lookup_ptr(identity.pointer);
    if (!other || *other != identity ||
        expected.main_ids.contains(identity.pointer) != current.main_ids.contains(identity.pointer))
    {
      return false;
    }
  }
  return true;
}

static void observe_deletion(const DeleteProof &proof,
                             const LiveIDs &live,
                             AngeloCommitDeleteResult &r_result)
{
  for (const AngeloCommitIDIdentity &identity : proof.identities) {
    (live.matches(identity) ? r_result.remaining : r_result.removed).append(identity);
  }
}

static Scene *resolve_scene(Main *main, const AngeloCommitIDIdentity &identity)
{
  /* Only Scene membership is needed here; render-engine changes have no deletion closure. */
  for (Scene &scene : main->scenes) {
    if (&scene.id == identity.pointer && identity_of(&scene.id) == identity) {
      return &scene;
    }
  }
  return nullptr;
}

static PointerRNA render_pointer(Scene &scene)
{
  return RNA_pointer_create_discrete(&scene.id, RNA_RenderSettings, &scene.r);
}

static bool engine_value(bContext *C,
                          Scene &scene,
                          const char *name,
                          int &r_value,
                          const char **r_error)
{
  if (!name || !name[0] || std::strlen(name) >= sizeof(scene.r.engine)) {
    *r_error = "The render engine identity is empty or exceeds its native storage";
    return false;
  }
  PointerRNA pointer = render_pointer(scene);
  PropertyRNA *property = RNA_struct_find_property(&pointer, "engine");
  if (!property || !RNA_property_enum_value(C, &pointer, property, name, &r_value)) {
    *r_error = "The exact render engine is no longer registered in RNA";
    return false;
  }
  return true;
}

static bool engine_owner_editable(Main *main, Scene &scene, const char **r_error)
{
  PointerRNA pointer = render_pointer(scene);
  PropertyRNA *property = RNA_struct_find_property(&pointer, "engine");
  if (!property || RNA_property_type(property) != PROP_ENUM) {
    *r_error = "The native render engine enum is unavailable";
    return false;
  }
  /* The property-specific editable callback is the capture admission itself. Preserve the
   * remaining RNA ownership checks while invoking this one private native setter. Scene is not
   * an asset-editable ID family, so its ID_IS_EDITABLE condition is exactly !ID_IS_LINKED. */
  const int flags = RNA_property_flag(property);
  const bool linked_exception = flags & PROP_LIB_EXCEPTION;
  if (!(flags & PROP_EDITABLE) || (flags & PROP_REGISTER) ||
      (ID_IS_LINKED(&scene.id) && !linked_exception) ||
      (ID_IS_OVERRIDE_LIBRARY(&scene.id) &&
       (!RNA_property_overridable_get(&pointer, property) ||
        (BKE_lib_override_library_is_system_defined(main, &scene.id) && !linked_exception))))
  {
    *r_error = "The render engine is not editable under its ordinary RNA ownership rules";
    return false;
  }
  return true;
}

}  // namespace

struct AngeloCommitDeleteRight {
  ScopeIdentity scope;
  AngeloCommitDeletePurpose purpose;
  Vector<AngeloCommitIDIdentity> roots;
  Vector<AngeloCommitIDIdentity> authorized;
  Vector<AngeloCommitIDIdentity> keep;
  DeleteProof proof;
  bool consumed = false;
};

struct AngeloCommitEngineRight {
  ScopeIdentity scope;
  AngeloCommitIDIdentity scene;
  std::string before;
  std::string target;
  bool forward_attempted = false;
  bool forward_set = false;
  bool rollback_attempted = false;
};

AngeloCommitDeleteRight *ED_angelo_commit_delete_prepare(
    bContext *C,
    AngeloUndoCapture *capture,
    const AngeloCommitDeletePurpose purpose,
    const Span<AngeloCommitIDIdentity> roots,
    const Span<AngeloCommitIDIdentity> authorized,
    const Span<AngeloCommitIDIdentity> keep,
    const char **r_error)
{
  ScopeIdentity scope;
  if (!operation_scope(C, capture, scope, r_error)) {
    return nullptr;
  }
  const LiveIDs live(scope.main);
  DeleteProof proof;
  if (!prove_deletion(scope, purpose, roots, authorized, keep, live, proof, r_error)) {
    return nullptr;
  }
  auto *right = MEM_new<AngeloCommitDeleteRight>(__func__);
  right->scope = scope;
  right->purpose = purpose;
  right->roots = roots;
  right->authorized = authorized;
  right->keep = keep;
  right->proof = std::move(proof);
  return right;
}

bool ED_angelo_commit_delete(bContext *C,
                             AngeloUndoCapture *capture,
                             AngeloCommitDeleteRight &right,
                             AngeloCommitDeleteResult &r_result,
                             const char **r_error)
{
  r_result = {};
  r_result.expected_main_ids = right.proof.main_ids.size();
  if (right.consumed) {
    *r_error = "The native deletion right has already been consumed";
    return false;
  }
  right.consumed = true;
  if (!same_scope(C, capture, right.scope, r_error)) {
    return false;
  }
  const LiveIDs before(right.scope.main);
  DeleteProof current;
  if (!prove_deletion(right.scope,
                      right.purpose,
                      right.roots,
                      right.authorized,
                      right.keep,
                      before,
                      current,
                      r_error) ||
      !proof_matches(right.proof, current))
  {
    if (!*r_error) {
      *r_error = "The native deletion closure changed after its right was prepared";
    }
    r_result.scope_valid_after = true;
    observe_deletion(right.proof, before, r_result);
    return false;
  }
  Set<ID *> deleting;
  for (const AngeloCommitIDIdentity &root : right.roots) {
    deleting.add(root.pointer);
  }
  if (!deleting.is_empty()) {
    r_result.kernel_called = true;
    r_result.kernel_deleted_ids = BKE_id_multi_delete(right.scope.main, deleting);
  }
  /* The kernel expands this set. Reading its pointer values is safe after free; reading any ID
   * through them is not. Never change the stock per-ID free or Python finalizer ordering. */
  for (ID *pointer : deleting) {
    if (!right.proof.main_ids.contains(pointer)) {
      r_result.unexpected_scope = true;
      r_result.unexpected_kernel_pointers.append(reinterpret_cast<uintptr_t>(pointer));
    }
  }
  r_result.unexpected_scope |= deleting.size() != right.proof.main_ids.size();
  if (!same_scope(C, capture, right.scope, r_error)) {
    return false;
  }
  r_result.scope_valid_after = true;
  const LiveIDs after(right.scope.main);
  observe_deletion(right.proof, after, r_result);
  if (r_result.unexpected_scope || !r_result.remaining.is_empty()) {
    *r_error = "Native deletion completed with unexpected scope or retained identities";
    return false;
  }
  return true;
}

void ED_angelo_commit_delete_free(AngeloCommitDeleteRight *right)
{
  MEM_delete(right);
}

AngeloCommitEngineRight *ED_angelo_commit_engine_prepare(bContext *C,
                                                         AngeloUndoCapture *capture,
                                                         const AngeloCommitIDIdentity identity,
                                                         const char *expected,
                                                         const char *target,
                                                         const char **r_error)
{
  ScopeIdentity scope;
  if (!operation_scope(C, capture, scope, r_error)) {
    return nullptr;
  }
  Scene *scene = resolve_scene(scope.main, identity);
  if (!scene || !expected || std::strcmp(scene->r.engine, expected) != 0) {
    *r_error = "The exact Scene or render engine preimage changed before preparation";
    return nullptr;
  }
  int before_value, target_value;
  if (!engine_owner_editable(scope.main, *scene, r_error) ||
      !engine_value(C, *scene, expected, before_value, r_error) ||
      !engine_value(C, *scene, target, target_value, r_error))
  {
    return nullptr;
  }
  auto *right = MEM_new<AngeloCommitEngineRight>(__func__);
  right->scope = scope;
  right->scene = identity;
  right->before = expected;
  right->target = target;
  return right;
}

bool ED_angelo_commit_engine_apply(bContext *C,
                                   AngeloUndoCapture *capture,
                                   AngeloCommitEngineRight &right,
                                   const bool rollback,
                                   AngeloCommitEngineResult &r_result,
                                   const char **r_error)
{
  r_result = {};
  bool &attempted = rollback ? right.rollback_attempted : right.forward_attempted;
  if (attempted || (rollback && !right.forward_set)) {
    *r_error = "The render engine right is consumed or has no applied forward transition";
    return false;
  }
  attempted = true;
  if (!same_scope(C, capture, right.scope, r_error)) {
    return false;
  }
  Scene *scene = resolve_scene(right.scope.main, right.scene);
  const std::string &expected = rollback ? right.target : right.before;
  const std::string &target = rollback ? right.before : right.target;
  if (!scene || expected != scene->r.engine) {
    *r_error = "The render engine transition no longer has its exact Scene and preimage";
    return false;
  }
  int value;
  if (!engine_owner_editable(right.scope.main, *scene, r_error) ||
      !engine_value(C, *scene, target.c_str(), value, r_error))
  {
    return false;
  }
  PointerRNA pointer = render_pointer(*scene);
  PropertyRNA *property = RNA_struct_find_property(&pointer, "engine");
  /* RNA's native enum setter performs its ordinary render-data write and dependency tag. The
   * exact private right is why it may be invoked here while public engine editing stays denied. */
  r_result.setter_called = true;
  RNA_property_enum_set(&pointer, property, value);
  if (!rollback) {
    right.forward_set = true;
  }
  r_result.update_called = true;
  RNA_property_update(C, &pointer, property);
  /* Updates can invoke renderer/editor callbacks. Resolve the Scene again before read-back. */
  if (!same_scope(C, capture, right.scope, r_error)) {
    return false;
  }
  r_result.scope_valid_after = true;
  scene = resolve_scene(right.scope.main, right.scene);
  if (!scene) {
    *r_error = "The render engine Scene could not be resolved after its update";
    return false;
  }
  r_result.scene_resolved_after = true;
  r_result.actual_engine = scene->r.engine;
  if (r_result.actual_engine != target) {
    *r_error = "The exact render engine value was not retained after its update";
    return false;
  }
  return true;
}

void ED_angelo_commit_engine_free(AngeloCommitEngineRight *right)
{
  MEM_delete(right);
}

}  // namespace blender
