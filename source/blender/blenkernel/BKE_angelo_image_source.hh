/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup bke
 *
 * Angelo: the source revision of an image (`Image.angelo_source_revision`).
 *
 * It changes on reload, source type changes, packing/unpacking and a load whose descriptor
 * metadata, packed bytes or load settings differ from the previous load of that view, tile and
 * frame. It never changes on a plain re-load of unchanged evidence, on acquiring or releasing
 * buffers, on freeing them (memory pressure included), or on GPU texture rebuilds. Pixel edits
 * are `Image.angelo_pixel_revision`'s.
 *
 * File records describe the actual decoder descriptor before the read. Metadata is change
 * evidence, not an exact byte-content guarantee; a load that finds the file changed so recently
 * that a later write could share its time stamps also digests the bytes read ("racily clean").
 * Opaque movie decoders are unsupported.
 * This counter alone must not authorize exact byte/pixel-sensitive work.
 */

#include <array>
#include <cstdint>
#include <optional>

namespace blender {

struct Image;
struct PackedFile;

namespace bke {

/** Per-image record of the last load of each view, tile and frame (opaque; angelo_image_source.cc owns it). */
struct AngeloImageSourceLoads;

/** A value no image has held before in this process: every new image's first source revision. */
uint64_t angelo_image_source_revision_fresh();

/**
 * The image's source changed without a load (reload, source type, packing, unpacking): give it a
 * new revision and forget the recorded loads, whose sources are no longer what the image reads.
 */
void angelo_image_source_changed(Image &image);

/** Descriptor/path evidence captured before decoding, independent of settings resolved by the decoder. */
struct AngeloImageSourceFileWitness {
  std::array<uint8_t, 16> identity{};
  bool supported = false;
  /** The file had changed within the racy window (its time stamps cannot tell the next write). */
  bool racy = false;
  /** The bytes' digest, taken when this load or the slot's last one was racy. */
  std::optional<std::array<uint8_t, 16>> content;
};

/**
 * Capture the actual descriptor BEFORE decoding. Caller owns it and must pass this SAME open
 * descriptor to the decoder. A failed open/stat is unsupported, never a source witness. With the
 * image and the slot the load fills, a load right after a racy one digests the bytes too.
 */
AngeloImageSourceFileWitness angelo_image_source_prepare_file_load(int file_descriptor,
                                                                  const char *filepath,
                                                                  const Image *image = nullptr,
                                                                  int view = 0,
                                                                  int tile = 0,
                                                                  int frame = 0);

/**
 * Record captured source evidence AFTER decoding, with the effective color space the decoder used.
 * Resolving an inferred default must not masquerade as a source change at the next load.
 */
void angelo_image_source_note_file_load(Image &image,
                                        int view,
                                        int tile,
                                        int frame,
                                        const AngeloImageSourceFileWitness &witness,
                                        uint32_t load_flags);

/** Record an opaque movie decoder with no actual read-source witness. */
void angelo_image_source_note_unsupported_load(Image &image, int view);

/**
 * All recorded loads have descriptor/packed witnesses. False before any load, after invalidation,
 * on failed stat and for movies. Does not certify exact bytes, complete slot coverage, generated
 * pixels, successful decoding or current external contents. Exact consumers need independent
 * content evidence.
 */
bool angelo_image_source_loads_supported(const Image &image);

/** Call after decoding, while the same immutable packed bytes are held and color space is resolved. */
void angelo_image_source_note_packed_load(
    Image &image, int view, int tile, const PackedFile &packed_file, uint32_t load_flags);

/**
 * After an undo step re-read the image, restore the revision of the load record it carried over
 * (#image_foreach_cache moves the record together with the pixel cache).
 */
void angelo_image_source_restore_after_undo(Image &image);

/** Free the load record (the image's runtime data is being freed). */
void angelo_image_source_free(Image &image);

/** Load records allocated and not yet freed, in the whole process (leak checks). */
int64_t angelo_image_source_records_in_use();

/** Testing: how many file loads digested their bytes in this process. */
int64_t angelo_image_source_content_digests();

/** Testing: set the racy window (nanoseconds; default 3 s); returns the previous one. */
int64_t angelo_image_source_set_racy_window_ns(int64_t window_ns);

}  // namespace bke
}  // namespace blender
