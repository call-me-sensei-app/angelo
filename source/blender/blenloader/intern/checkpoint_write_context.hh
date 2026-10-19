/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <cstdint>
#include <optional>

#include "BLI_span.hh"

namespace blender {

struct BlendFileWriteParams;
struct ID;
struct Main;
struct ReportList;
struct Scene;
struct ViewLayer;
struct WriteData;

/** Borrowed only for the synchronous checkpoint hook. Never retain this lookup or its ID span.
 * Queries only already-created serializer tokens, without modifying the stable-address map.
 * Inclusion is necessary: the writer precomputes ID tokens even for IDs it will omit. */
class CheckpointWriteTokenLookup {
 private:
  const WriteData &data_;
  Span<ID *> included_ids_;
  bool local_ids_written_;

 public:
  CheckpointWriteTokenLookup(const WriteData &data,
                             Span<ID *> included_ids,
                             bool local_ids_written)
      : data_(data), included_ids_(included_ids), local_ids_written_(local_ids_written)
  {
  }

  std::optional<uint64_t> id_token(const ID &id) const;
  /** Requires the actual included Scene and a current member of its ViewLayer list.
   * Available only after local Scene output. GLOB pointer remapping may already have created
   * a token before Scene output; map presence alone never permits early export.
   * This function never creates a token. */
  std::optional<uint64_t> view_layer_token(const Scene &scene, const ViewLayer &view_layer) const;
};

/** Private native transport, not Save handlers or a Python callback seam. Both hooks are required.
 * The integration owner validates its exact CaptureOwner and binds/retains the selected snapshot.
 * Hook arguments are valid only until return; retain snapshot ownership and copy token values.
 * Hooks must not mutate Main membership or selected source relations, evaluate, dispatch Python,
 * or invoke nested file operations. Capture inspects the final override-prepared local ID set. */
struct CheckpointWriteContext {
  void *user_data = nullptr;
  bool (*before_local_ids)(Main &main,
                           Span<ID *> included_ids,
                           const CheckpointWriteTokenLookup &tokens,
                           ReportList *reports,
                           void *user_data) = nullptr;
  bool (*after_local_ids)(Main &main,
                          Span<ID *> included_ids,
                          const CheckpointWriteTokenLookup &tokens,
                          ReportList *reports,
                          void *user_data) = nullptr;
  /** Optional native selection of unused local Object/Mesh/Key IDs. Never changes users.
   * Called after ordinary override preparation and again after newly selected ID preparation.
   * Return true only for current selected Sculpt owners and their actual Mesh/nullable Key.
   * Must be read-only/deterministic while source is unchanged; before_local_ids validates the
   * final snapshot relations. Linked/runtime/non-Main/other ID types are never eligible. */
  bool (*include_id)(Main &main, const ID &id, void *user_data) = nullptr;
};

/** Same admission/borrowed-descriptor/no-remap contract as BLO_write_file_to_descriptor.
 * Before-hook failure is partial BASE: headers/global output were already flushed.
 * The late hook observes real tokens after normal local Scene output, while WriteData is alive.
 * Success means stream acceptance only, not companion completion/durability/publication. */
bool BLO_write_checkpoint_to_descriptor(Main *main,
                                        const char *logical_filepath,
                                        int descriptor,
                                        int write_flags,
                                        const BlendFileWriteParams *params,
                                        ReportList *reports,
                                        const CheckpointWriteContext &context);

}  // namespace blender
