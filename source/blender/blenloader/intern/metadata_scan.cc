/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup blenloader
 * Bounded file metadata scanning, without creating a Main or entering library loading.
 */

#include <algorithm>
#include <array>
#include <climits>
#include <cstring>
#include <memory>
#include <string_view>
#include <sys/stat.h>

#include "BLI_endian_defines.h"
#include "BLI_span.hh"

#include "DNA_ID.h"
#include "DNA_genfile.h"
#include "DNA_sdna_types.h"

#include "BKE_idtype.hh"

#include "BLO_core_bhead.hh"
#include "BLO_core_blend_header.hh"
#include "BLO_metadata_scan.hh"

#include "metadata_scan_dna.hh"
#include "metadata_scan_reader.hh"

namespace blender {
namespace {

using blo::MetadataScanBudget;
using blo::MetadataScanReader;

struct MetadataField {
  int offset = -1;
  int bytes = 0;
  int end() const
  {
    return offset < 0 ? 0 : offset + bytes;
  }
};

/** Query native SDNA offsets without alias-map initialization or reconstructing any ID. */
static MetadataField field(const SDNA &dna,
                           const char *structure,
                           const char *type,
                           const char *member,
                           const char *member_base)
{
  MetadataField result;
  const int struct_index = DNA_struct_find_index_without_alias(&dna, structure);
  if (struct_index < 0) {
    return {};
  }
  result.offset = DNA_struct_member_offset_by_name_without_alias(&dna, structure, type, member);
  if (result.offset < 0) {
    return result;
  }
  const SDNA_Struct &definition = *dna.structs[struct_index];
  for (int i = 0; i < definition.members_num; i++) {
    const SDNA_StructMember &entry = definition.members[i];
    const std::string_view name(dna.members[entry.member_index]);
    const size_t bracket = name.find('[');
    if (std::string_view(dna.types[entry.type_index]) == type &&
        name.substr(0, bracket) == member_base)
    {
      result.bytes = DNA_struct_member_size(&dna, entry.type_index, entry.member_index);
      return result;
    }
  }
  return {};
}

struct MetadataLayout {
  MetadataField name;
  MetadataField flag;
  MetadataField library_path;
  int id_prefix_bytes = 0;
  int library_prefix_bytes = 0;
};

static std::optional<MetadataLayout> metadata_layout(const SDNA &dna)
{
  MetadataLayout layout;
  layout.name = field(dna, "ID", "char", "name[]", "name");
  layout.flag = field(dna, "ID", "short", "flag", "flag");
  /* The on-disk name predates the RNA-facing `filepath` name (dna_rename_defs.h). */
  layout.library_path = field(dna, "Library", "char", "filepath[]", "filepath");
  if (layout.library_path.offset < 0) {
    layout.library_path = field(dna, "Library", "char", "name[]", "name");
  }
  if (layout.name.offset < 0 || layout.name.bytes < 3 ||
      (layout.flag.offset >= 0 && layout.flag.bytes != sizeof(short)))
  {
    return std::nullopt;
  }
  /* Native library listing excludes names that do not terminate within MAX_ID_NAME. */
  layout.name.bytes = std::min(layout.name.bytes, MAX_ID_NAME);
  layout.id_prefix_bytes = std::max(layout.name.end(), layout.flag.end());
  layout.library_prefix_bytes = std::max(layout.id_prefix_bytes, layout.library_path.end());
  return layout;
}

static std::optional<BlenderHeader> read_header(MetadataScanReader &reader,
                                               MetadataScanBudget &budget)
{
  /* Native header decoding uses ctype functions on char. Keep untrusted high-bit bytes away
   * from those calls; all defined header formats consist solely of ASCII bytes. */
  std::array<unsigned char, MAX_SIZEOFBLENDERHEADER> bytes{};
  if (!reader.read_exact(bytes.data(), MIN_SIZEOFBLENDERHEADER)) {
    return std::nullopt;
  }
  size_t size = MIN_SIZEOFBLENDERHEADER;
  if (bytes[7] != '_' && bytes[7] != '-') {
    if (!reader.read_exact(bytes.data() + size, MAX_SIZEOFBLENDERHEADER - size)) {
      return std::nullopt;
    }
    size = MAX_SIZEOFBLENDERHEADER;
  }
  if (std::any_of(bytes.begin(), bytes.begin() + size, [](unsigned char c) { return c > 127; })) {
    budget.fail(BLOMetadataScanStatus::INVALID, "Invalid blend header encoding");
    return std::nullopt;
  }
  FileReader *header_reader = BLI_filereader_new_memory(bytes.data(), size);
  const BlenderHeaderVariant header = BLO_readfile_blender_header_decode(header_reader);
  header_reader->close(header_reader);
  if (!std::holds_alternative<BlenderHeader>(header)) {
    budget.fail(BLOMetadataScanStatus::INVALID, "Invalid or unsupported blend header");
    return std::nullopt;
  }
  const BlenderHeader value = std::get<BlenderHeader>(header);
  /* Match the current native reader: cross-endian .blend support was removed in Blender 5.0. */
  if (value.endian != ENDIAN_ORDER) {
    budget.fail(BLOMetadataScanStatus::INVALID, "Unsupported blend byte order");
    return std::nullopt;
  }
  return value;
}

static std::optional<BHead> read_block(MetadataScanReader &reader,
                                     const BlenderHeader &header,
                                     MetadataScanBudget &budget)
{
  if (!budget.charge(budget.result.blocks, 1, budget.limits.block_count,
                      "Block count limit exceeded"))
  {
    return std::nullopt;
  }
  const auto block = BLO_readfile_read_bhead(reader.file(), header.bhead_type());
  if (!block || block->len < 0 || block->nr < 0 || block->SDNAnr < 0) {
    budget.fail(BLOMetadataScanStatus::INVALID, "Invalid or incomplete block header");
    return std::nullopt;
  }
  if (uint64_t(block->len) > budget.limits.block_bytes) {
    budget.fail(BLOMetadataScanStatus::LIMIT_EXCEEDED, "Block size limit exceeded");
    return std::nullopt;
  }
  return block;
}

using DnaPointer = std::unique_ptr<SDNA, decltype(&DNA_sdna_free)>;

static DnaPointer read_dna(int descriptor,
                          uint64_t file_bytes,
                          MetadataScanBudget &budget,
                          BlenderHeader &header)
{
  DnaPointer dna(nullptr, DNA_sdna_free);
  MetadataScanReader reader(descriptor, file_bytes, budget);
  budget.result.passes++;
  if (!reader.initialize()) {
    return dna;
  }
  const auto decoded_header = read_header(reader, budget);
  if (!decoded_header) {
    return dna;
  }
  header = *decoded_header;
  budget.result.file_version = header.file_version;
  while (budget.check()) {
    const auto block = read_block(reader, header, budget);
    if (!block) {
      break;
    }
    if (block->code == BLO_CODE_ENDB) {
      budget.fail(BLOMetadataScanStatus::INVALID, "Missing DNA block");
      break;
    }
    if (block->code == BLO_CODE_DNA1) {
      if (uint64_t(block->len) > budget.limits.dna_bytes) {
        budget.fail(BLOMetadataScanStatus::LIMIT_EXCEEDED, "DNA size limit exceeded");
        break;
      }
      std::vector<std::byte> encoded(size_t(block->len));
      if (!reader.read_exact(encoded.data(), encoded.size())) {
        break;
      }
      bool dna_limit = false;
      if (!blo::metadata_scan_validate_dna(Span(encoded.data(), encoded.size()),
                                            budget.limits.dna_entries,
                                            &dna_limit))
      {
        budget.fail(dna_limit ? BLOMetadataScanStatus::LIMIT_EXCEEDED :
                               BLOMetadataScanStatus::INVALID,
                    dna_limit ? "DNA table limit exceeded" : "Invalid DNA schema");
        break;
      }
      const char *error = nullptr;
      /* Own a writable copy: legacy SDNA gravity repair edits its own serialized storage. */
      dna.reset(DNA_sdna_from_data(encoded.data(), int(encoded.size()), true, false, &error));
      if (!dna || dna->pointer_size != header.pointer_size) {
        dna.reset();
        budget.fail(BLOMetadataScanStatus::INVALID, "DNA does not match blend header");
      }
      break;
    }
    if (block->code == BLO_CODE_GLOB && header.file_version > 242) {
      std::array<char, 5> subversion{};
      if (block->len < 4 || !reader.read_exact(subversion.data(), 4)) {
        budget.fail(BLOMetadataScanStatus::INVALID, "Invalid global block");
        break;
      }
      /* The first four bytes are the version string, as in native read_file_dna(). */
      budget.result.subversion = std::atoi(subversion.data());
      if (!reader.skip(uint64_t(block->len) - 4)) {
        break;
      }
    }
    else if (!reader.skip(uint64_t(block->len))) {
      break;
    }
  }
  return dna;
}

struct TypeState {
  int code;
  BLOMetadataScanType *output;
  uint64_t seen = 0;
  uint32_t capacity = 0;
};

/** Retain a tail of fixed capacity; reversing it later reproduces native prepend order. */
static void keep_tail(std::vector<std::string> &names, std::string name, uint32_t capacity)
{
  if (!capacity) {
    return;
  }
  if (names.size() == capacity) {
    names.erase(names.begin());
  }
  names.push_back(std::move(name));
}

static bool scan_names(int descriptor,
                       uint64_t file_bytes,
                       const BlenderHeader &expected_header,
                       const SDNA &dna,
                       const MetadataLayout &layout,
                       const BLOMetadataScanRequest &request,
                       MetadataScanBudget &budget,
                       std::vector<TypeState> &types,
                       bool collect_page)
{
  MetadataScanReader reader(descriptor, file_bytes, budget);
  budget.result.passes++;
  if (!reader.initialize()) {
    return false;
  }
  const auto header = read_header(reader, budget);
  if (!header || header->file_version != expected_header.file_version ||
      header->pointer_size != expected_header.pointer_size ||
      header->file_format_version != expected_header.file_format_version)
  {
    return budget.fail(BLOMetadataScanStatus::INVALID, "Blend header changed between passes");
  }
  std::vector<std::byte> prefix(size_t(layout.library_prefix_bytes));
  while (budget.check()) {
    const auto block = read_block(reader, *header, budget);
    if (!block) {
      return false;
    }
    if (block->code == BLO_CODE_ENDB) {
      return reader.finish();
    }
    /* ID codes have two characters; DATA, GLOB, DNA1 and the other block codes have four. Most
     * blocks are DATA, so skip them before looking for a listed type. */
    TypeState *type = nullptr;
    if (block->code > 0xFFFF) {
      if (!reader.skip(uint64_t(block->len))) {
        return false;
      }
      continue;
    }
    for (TypeState &candidate : types) {
      if (candidate.code == block->code) {
        type = &candidate;
        break;
      }
    }
    const bool library = block->code == ID_LI;
    if (!type && !library) {
      if (!reader.skip(uint64_t(block->len))) {
        return false;
      }
      continue;
    }
    const int prefix_bytes = library ? layout.library_prefix_bytes : layout.id_prefix_bytes;
    if (block->len < prefix_bytes || block->SDNAnr >= dna.structs_num || block->nr < 1) {
      return budget.fail(BLOMetadataScanStatus::INVALID, "Invalid datablock metadata");
    }
    if (!reader.read_exact(prefix.data(), size_t(prefix_bytes)) ||
        !reader.skip(uint64_t(block->len) - uint64_t(prefix_bytes)))
    {
      return false;
    }
    const char *name = reinterpret_cast<const char *>(prefix.data() + layout.name.offset);
    const char *end = static_cast<const char *>(std::memchr(name, '\0', layout.name.bytes));
    short flag = 0;
    if (layout.flag.offset >= 0) {
      std::memcpy(&flag, prefix.data() + layout.flag.offset, sizeof(flag));
    }
    if (!end || end - name < 2 || (flag & ID_FLAG_LINKED_AND_PACKED)) {
      continue;
    }
    if (library) {
      if (!collect_page && layout.library_path.offset >= 0) {
        const char *path = reinterpret_cast<const char *>(prefix.data() + layout.library_path.offset);
        const char *path_end = static_cast<const char *>(
            std::memchr(path, '\0', layout.library_path.bytes));
        if (!path_end) {
          return budget.fail(BLOMetadataScanStatus::INVALID, "Unterminated library path");
        }
        keep_tail(budget.result.library_paths,
                  std::string(path, path_end),
                  request.libraries_limit);
      }
      continue;
    }
    const uint64_t index = type->seen++;
    if (collect_page) {
      if (index >= type->output->total) {
        return budget.fail(BLOMetadataScanStatus::INVALID, "Metadata changed between passes");
      }
      const uint64_t reverse_index = type->output->total - 1 - index;
      if (type->capacity && reverse_index >= request.offset &&
          reverse_index - request.offset < request.limit)
      {
        type->output->names.emplace_back(name + 2, end);
      }
    }
    else {
      type->output->total++;
      if (type->capacity && (request.list.empty() || request.offset == 0)) {
        keep_tail(type->output->names, std::string(name + 2, end), type->capacity);
      }
    }
  }
  return false;
}

}  // namespace

BLOMetadataScanResult BLO_metadata_scan_from_descriptor(int descriptor,
                                                       const BLOMetadataScanRequest &request,
                                                       const BLOMetadataScanLimits &limits,
                                                       BLOMetadataScanCancelled cancelled,
                                                       void *userdata)
{
  BLOMetadataScanResult result;
  result.status = BLOMetadataScanStatus::OK;
  MetadataScanBudget budget{limits, result, cancelled, userdata};
  if (descriptor < 0 || !limits.decoded_bytes || limits.decoded_bytes > INT64_MAX ||
      !limits.input_bytes || !limits.block_count || !limits.block_bytes ||
      limits.block_bytes > INT64_MAX || !limits.dna_bytes || limits.dna_bytes > INT_MAX ||
      !limits.dna_entries || !limits.decoder_bytes || !limits.zstd_window_bytes ||
      limits.zstd_window_bytes > SIZE_MAX || !limits.result_names || !request.limit ||
      !request.summary_names || !request.libraries_limit)
  {
    budget.fail(BLOMetadataScanStatus::INVALID, "Invalid metadata scan limits or request");
    return result;
  }
#ifdef WIN32
  struct _stat64 info;
  const bool stat_ok = _fstat64(descriptor, &info) == 0 && (info.st_mode & _S_IFREG);
#else
  struct stat info;
  const bool stat_ok = fstat(descriptor, &info) == 0 && S_ISREG(info.st_mode);
#endif
  if (!stat_ok || info.st_size < 0) {
    budget.fail(BLOMetadataScanStatus::IO_ERROR, "Descriptor is not a regular file");
    return result;
  }
  std::vector<int> codes;
  for (int i = 0, code; (code = BKE_idtype_idcode_iter_step(&i));) {
    if (code != ID_LI && BKE_idtype_idcode_is_linkable(code)) {
      codes.push_back(code);
      result.types.push_back({BKE_idtype_idcode_to_name_plural(code), 0, {}});
    }
  }
  std::vector<TypeState> types;
  uint64_t retained_names = request.libraries_limit;
  for (size_t i = 0; i < codes.size(); i++) {
    const uint32_t capacity = request.list.empty() ? request.summary_names :
                              result.types[i].list_name == request.list ? request.limit : 0;
    retained_names += capacity;
    types.push_back({codes[i], &result.types[i], 0, capacity});
  }
  if (retained_names > limits.result_names) {
    budget.fail(BLOMetadataScanStatus::LIMIT_EXCEEDED, "Metadata result limit exceeded");
    result.types.clear();
    return result;
  }
  BlenderHeader header;
  DnaPointer dna = read_dna(descriptor, uint64_t(info.st_size), budget, header);
  const auto layout = dna ? metadata_layout(*dna) : std::nullopt;
  if (!layout) {
    budget.fail(BLOMetadataScanStatus::INVALID, "Required metadata fields are unavailable");
  }
  else if (scan_names(descriptor, uint64_t(info.st_size), header, *dna, *layout, request,
                      budget, types, false) && !request.list.empty() && request.offset > 0 &&
           std::any_of(types.begin(), types.end(), [&](const TypeState &type) {
             return type.capacity > 0 && request.offset < type.output->total;
           }))
  {
    for (TypeState &type : types) {
      type.seen = 0;
    }
    if (scan_names(descriptor, uint64_t(info.st_size), header, *dna, *layout, request,
                   budget, types, true))
    {
      for (const TypeState &type : types) {
        if (type.seen != type.output->total) {
          budget.fail(BLOMetadataScanStatus::INVALID, "Metadata changed between passes");
        }
      }
    }
  }
  if (budget.check()) {
    for (BLOMetadataScanType &type : result.types) {
      std::reverse(type.names.begin(), type.names.end());
    }
    std::reverse(result.library_paths.begin(), result.library_paths.end());
  }
  else {
    result.file_version = 0;
    result.subversion = 0;
    result.types.clear();
    result.library_paths.clear();
  }
  return result;
}

}  // namespace blender
