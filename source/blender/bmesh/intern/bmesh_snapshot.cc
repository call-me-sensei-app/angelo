/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bmesh
 *
 * Owned, immutable geometry for Main Undo adjuncts. Unlike BM_mesh_copy, this never uses source
 * element indices as scratch space. BMLog identity and swap-buffer ownership remain separate.
 */

#include <array>
#include <cstring>
#include <limits>
#include <memory>

#include "MEM_guardedalloc.h"

/* Existing BMeshes can retain versioned value layers. Preserve them rather than dropping data. */
#define DNA_DEPRECATED_ALLOW
#include "DNA_customdata_types.h"
#include "DNA_meshdata_types.h"

#include "BLI_bitmap.h"
#include "BLI_listbase.h"
#include "BLI_map.hh"
#include "BLI_mempool.h"
#include "BLI_vector.hh"

#include "BKE_customdata.hh"

#include "bmesh.hh"
#include "bmesh_snapshot.hh"
#include "bmesh_snapshot_io.hh"

namespace blender {

struct BMGeometrySnapshot {
  BMesh *mesh;
  size_t owned_bytes;
};

namespace {

struct MeshDeleter {
  void operator()(BMesh *bm) const
  {
    BM_mesh_free(bm);
  }
};
using OwnedMesh = std::unique_ptr<BMesh, MeshDeleter>;

static bool fail(const char **r_error, const char *message)
{
  if (r_error) {
    *r_error = message;
  }
  return false;
}

static bool add_bytes(size_t &total, const size_t bytes)
{
  if (bytes == std::numeric_limits<size_t>::max() ||
      bytes > std::numeric_limits<size_t>::max() - total)
  {
    return false;
  }
  total += bytes;
  return true;
}

static bool multiply_bytes(const size_t count, const size_t size, size_t &r_bytes)
{
  if (size && count > std::numeric_limits<size_t>::max() / size) {
    return false;
  }
  r_bytes = count * size;
  return true;
}

enum class LayerKind { Fixed, Deform, Displacements, GridMask, PythonPointer, Unsupported };

static LayerKind layer_kind(const eCustomDataType type)
{
  switch (type) {
    case CD_MDEFORMVERT:
      return LayerKind::Deform;
    case CD_MDISPS:
      return LayerKind::Displacements;
    case CD_GRID_PAINT_MASK:
      return LayerKind::GridMask;
    case CD_BM_ELEM_PYPTR:
      return LayerKind::PythonPointer;
    case CD_MVERT:
    case CD_MSTICKY:
    case CD_MEDGE:
    case CD_MFACE:
    case CD_MTFACE:
    case CD_MCOL:
    case CD_ORIGINDEX:
    case CD_NORMAL:
    case CD_FACEMAP:
    case CD_PROP_FLOAT:
    case CD_PROP_INT32:
    case CD_PROP_STRING:
    case CD_ORIGSPACE:
    case CD_ORCO:
    case CD_MTEXPOLY:
    case CD_MLOOPUV:
    case CD_PROP_BYTE_COLOR:
    case CD_TANGENT:
    case CD_PROP_FLOAT4X4:
    case CD_PROP_INT16_2D:
    case CD_CLOTH_ORCO:
    case CD_PROP_FLOAT4:
    case CD_MPOLY:
    case CD_MLOOP:
    case CD_SHAPE_KEYINDEX:
    case CD_SHAPEKEY:
    case CD_BWEIGHT:
    case CD_CREASE:
    case CD_ORIGSPACE_MLOOP:
    case CD_PAINT_MASK:
    case CD_MVERT_SKIN:
    case CD_FREESTYLE_EDGE:
    case CD_FREESTYLE_FACE:
    case CD_MLOOPTANGENT:
    case CD_TESSLOOPNORMAL:
    case CD_CUSTOMLOOPNORMAL:
    case CD_SCULPT_FACE_SETS:
    case CD_PROP_INT8:
    case CD_PROP_INT32_2D:
    case CD_PROP_COLOR:
    case CD_PROP_FLOAT3:
    case CD_PROP_FLOAT2:
    case CD_PROP_BOOL:
    case CD_PROP_QUATERNION:
      return LayerKind::Fixed;
    default:
      /* New owning types need an explicit copy/equality/accounting contract before admission. */
      return LayerKind::Unsupported;
  }
}

struct Schema {
  const CustomData *data;
  Vector<const CustomDataLayer *, 0> layers;
};

static bool schema_read(const CustomData &data, Schema &schema, const char **r_error,
                        const bool collect_layers = true)
{
  schema.data = &data;
  if (data.totlayer < 0 || data.totsize < 0 || (data.totlayer && !data.layers) ||
      (data.external &&
       !std::memchr(data.external->filepath, '\0', sizeof(data.external->filepath))))
  {
    return fail(r_error, "The BMesh snapshot has an invalid CustomData layout");
  }
  for (const CustomDataLayer &layer : Span(data.layers, data.totlayer)) {
    const LayerKind kind = layer_kind(layer.type);
    if (kind == LayerKind::PythonPointer) {
      continue;
    }
    if (kind == LayerKind::Unsupported) {
      return fail(r_error, "The BMesh snapshot contains an unsupported CustomData ownership type");
    }
    const int size = CustomData_sizeof(layer.type);
    if (size <= 0 || layer.offset < 0 || layer.offset > data.totsize ||
        size > data.totsize - layer.offset || layer.data || layer.sharing_info ||
        !std::memchr(layer.name, '\0', sizeof(layer.name)))
    {
      return fail(r_error, "The BMesh snapshot contains an invalid CustomData layer");
    }
    if (collect_layers) { schema.layers.append(&layer); }
  }
  return true;
}

static bool layer_metadata_equal(const CustomDataLayer &a, const CustomDataLayer &b)
{
  return a.type == b.type && a.flag == b.flag && a.uid == b.uid && a.active == b.active &&
         a.active_rnd == b.active_rnd && std::strcmp(a.name, b.name) == 0;
}

static bool schema_equal(const Schema &a, const Schema &b)
{
  if (a.layers.size() != b.layers.size() || bool(a.data->external) != bool(b.data->external)) {
    return false;
  }
  if (a.data->external && std::strcmp(a.data->external->filepath, b.data->external->filepath) != 0)
  {
    return false;
  }
  for (const int64_t i : a.layers.index_range()) {
    if (!layer_metadata_equal(*a.layers[i], *b.layers[i])) {
      return false;
    }
  }
  return true;
}

static bool schema_copy(const Schema &source,
                        CustomData &destination,
                        const int count,
                        const char domain,
                        const char **r_error)
{
  Vector<CustomDataLayer, 0> descriptors;
  descriptors.reserve(source.layers.size());
  for (const CustomDataLayer *layer : source.layers) {
    descriptors.append(*layer);
    descriptors.last().flag &= ~CD_FLAG_NOCOPY;
  }
  /* Only this shallow, non-owning view is edited. Layout creation duplicates external metadata;
   * it does not borrow source element blocks. Never free the view as an owning CustomData. */
  CustomData view = *source.data;
  view.layers = descriptors.data();
  view.totlayer = view.maxlayer = int(descriptors.size());
  CustomData_update_typemap(&view);
  CustomData_init_layout_from(&view, &destination, CD_MASK_ALL, CD_SET_DEFAULT, 0);
  /* BM_mesh_free expects every nonempty layout to have a pool, including on a later refusal. */
  CustomData_bmesh_init_pool(&destination, count, domain);
  if (destination.totlayer != source.layers.size()) {
    return fail(r_error, "The BMesh snapshot could not preserve every CustomData layer");
  }
  for (const int64_t i : source.layers.index_range()) {
    const CustomDataLayer &src = *source.layers[i];
    CustomDataLayer &dst = destination.layers[i];
    if (dst.type != src.type || std::strcmp(dst.name, src.name) != 0) {
      return fail(r_error, "The BMesh snapshot CustomData layer order changed during allocation");
    }
    dst.flag = src.flag;
    dst.uid = src.uid;
    dst.active = src.active;
    dst.active_rnd = src.active_rnd;
  }
  Schema copied;
  return schema_read(destination, copied, r_error) &&
         (schema_equal(source, copied) ||
          fail(r_error, "The BMesh snapshot CustomData schema was not preserved"));
}

static const void *layer_value(const void *block, const CustomDataLayer &layer)
{
  return static_cast<const char *>(block) + layer.offset;
}

static bool allocation_covers(const void *pointer, const size_t bytes)
{
  return pointer && MEM_allocN_len(pointer) >= bytes;
}

static bool grid_mask_bytes(const unsigned int level, size_t &r_bytes)
{
  if (level == 0 || level >= std::numeric_limits<size_t>::digits) {
    return false;
  }
  const size_t side = (size_t(1) << (level - 1)) + 1;
  size_t count;
  return multiply_bytes(side, side, count) && multiply_bytes(count, sizeof(float), r_bytes);
}

static bool payload_valid(const CustomDataLayer &layer, const void *value, const char **r_error)
{
  size_t bytes;
  switch (layer_kind(layer.type)) {
    case LayerKind::Fixed:
      return true;
    case LayerKind::Deform: {
      const MDeformVert &data = *static_cast<const MDeformVert *>(value);
      if (data.totweight < 0 ||
          !multiply_bytes(size_t(data.totweight), sizeof(MDeformWeight), bytes) ||
          (data.totweight && !allocation_covers(data.dw, bytes)))
      {
        return fail(r_error, "The BMesh snapshot has invalid vertex-group storage");
      }
      return true;
    }
    case LayerKind::Displacements: {
      const MDisps &data = *static_cast<const MDisps *>(value);
      if (data.totdisp < 0 || !multiply_bytes(size_t(data.totdisp), sizeof(float[3]), bytes) ||
          (data.disps && !allocation_covers(data.disps, bytes)))
      {
        return fail(r_error, "The BMesh snapshot has invalid displacement storage");
      }
      bytes = BLI_BITMAP_SIZE(size_t(data.totdisp));
      if (data.hidden && !allocation_covers(data.hidden, bytes)) {
        return fail(r_error, "The BMesh snapshot has invalid displacement visibility storage");
      }
      return true;
    }
    case LayerKind::GridMask: {
      const GridPaintMask &data = *static_cast<const GridPaintMask *>(value);
      if (data.data &&
          (!grid_mask_bytes(data.level, bytes) || !allocation_covers(data.data, bytes)))
      {
        return fail(r_error, "The BMesh snapshot has invalid grid-mask storage");
      }
      return true;
    }
    default:
      return fail(r_error, "The BMesh snapshot cannot prove CustomData ownership");
  }
}

static bool block_copy(const Schema &source,
                       const void *source_block,
                       CustomData &destination,
                       void **destination_block,
                       const char **r_error)
{
  if (source.layers.is_empty()) {
    return true;
  }
  if (!source_block) {
    return fail(r_error, "A BMesh element is missing its CustomData block");
  }
  for (const CustomDataLayer *layer : source.layers) {
    if (!payload_valid(*layer, layer_value(source_block, *layer), r_error)) {
      return false;
    }
  }
  CustomData_bmesh_alloc_block(&destination, destination_block);
  std::memset(*destination_block, 0, size_t(destination.totsize));
  for (const int64_t i : source.layers.index_range()) {
    const CustomDataLayer &layer = *source.layers[i];
    const void *src = layer_value(source_block, layer);
    void *dst = static_cast<char *>(*destination_block) + destination.layers[i].offset;
    switch (layer_kind(layer.type)) {
      case LayerKind::Fixed:
        std::memcpy(dst, src, size_t(CustomData_sizeof(layer.type)));
        break;
      case LayerKind::Deform: {
        const MDeformVert &a = *static_cast<const MDeformVert *>(src);
        MDeformVert &b = *static_cast<MDeformVert *>(dst);
        b.totweight = a.totweight;
        b.flag = a.flag;
        b.dw = a.dw ? MEM_dupalloc(a.dw) : nullptr;
        if (a.dw && !b.dw) {
          return fail(r_error, "The BMesh snapshot could not allocate vertex-group storage");
        }
        break;
      }
      case LayerKind::Displacements: {
        const MDisps &a = *static_cast<const MDisps *>(src);
        MDisps &b = *static_cast<MDisps *>(dst);
        b.totdisp = a.totdisp;
        b.level = a.level;
        /* Native generic copying drops hidden-only records; both owned arrays matter here. */
        b.disps = a.disps ? MEM_dupalloc(a.disps) : nullptr;
        b.hidden = a.hidden ? MEM_dupalloc(a.hidden) : nullptr;
        if ((a.disps && !b.disps) || (a.hidden && !b.hidden)) {
          return fail(r_error, "The BMesh snapshot could not allocate displacement storage");
        }
        break;
      }
      case LayerKind::GridMask: {
        const GridPaintMask &a = *static_cast<const GridPaintMask *>(src);
        GridPaintMask &b = *static_cast<GridPaintMask *>(dst);
        b.level = a.level;
        b.data = a.data ? MEM_dupalloc(a.data) : nullptr;
        if (a.data && !b.data) {
          return fail(r_error, "The BMesh snapshot could not allocate grid-mask storage");
        }
        break;
      }
      default:
        BLI_assert_unreachable();
    }
  }
  return true;
}

static bool bytes_equal(const void *a, const void *b, const size_t size)
{
  return bool(a) == bool(b) && (!a || std::memcmp(a, b, size) == 0);
}

static bool block_equal(const Schema &a_schema,
                        const void *a_block,
                        const Schema &b_schema,
                        const void *b_block)
{
  if (a_schema.layers.is_empty()) {
    return true;
  }
  if (!a_block || !b_block) {
    return false;
  }
  for (const int64_t i : a_schema.layers.index_range()) {
    const CustomDataLayer &a_layer = *a_schema.layers[i];
    const CustomDataLayer &b_layer = *b_schema.layers[i];
    const void *a = layer_value(a_block, a_layer);
    const void *b = layer_value(b_block, b_layer);
    if (!payload_valid(a_layer, a, nullptr) || !payload_valid(b_layer, b, nullptr)) {
      return false;
    }
    switch (layer_kind(a_layer.type)) {
      case LayerKind::Fixed:
        if (std::memcmp(a, b, size_t(CustomData_sizeof(a_layer.type))) != 0) {
          return false;
        }
        break;
      case LayerKind::Deform: {
        const MDeformVert &x = *static_cast<const MDeformVert *>(a);
        const MDeformVert &y = *static_cast<const MDeformVert *>(b);
        if (x.totweight != y.totweight || x.flag != y.flag ||
            !bytes_equal(x.dw, y.dw, size_t(x.totweight) * sizeof(MDeformWeight)))
        {
          return false;
        }
        break;
      }
      case LayerKind::Displacements: {
        const MDisps &x = *static_cast<const MDisps *>(a);
        const MDisps &y = *static_cast<const MDisps *>(b);
        if (x.totdisp != y.totdisp || x.level != y.level ||
            !bytes_equal(x.disps, y.disps, size_t(x.totdisp) * sizeof(float[3])) ||
            !bytes_equal(x.hidden, y.hidden, BLI_BITMAP_SIZE(size_t(x.totdisp))))
        {
          return false;
        }
        break;
      }
      case LayerKind::GridMask: {
        const GridPaintMask &x = *static_cast<const GridPaintMask *>(a);
        const GridPaintMask &y = *static_cast<const GridPaintMask *>(b);
        size_t bytes = 0;
        if (x.level != y.level || (x.data && !grid_mask_bytes(x.level, bytes)) ||
            !bytes_equal(x.data, y.data, bytes))
        {
          return false;
        }
        break;
      }
      default:
        return false;
    }
  }
  return true;
}

static std::array<const CustomData *, 4> mesh_data(const BMesh &bm)
{
  return {&bm.vdata, &bm.edata, &bm.pdata, &bm.ldata};
}

static bool schemas_read(const BMesh &bm, std::array<Schema, 4> &schemas, const char **r_error,
                         const bool collect_layers = true)
{
  if (bm.totvert < 0 || bm.totedge < 0 || bm.totface < 0 || bm.totloop < 0 || !bm.vpool ||
      !bm.epool || !bm.fpool || !bm.lpool || bm.totvert != BLI_mempool_len(bm.vpool) ||
      bm.totedge != BLI_mempool_len(bm.epool) || bm.totface != BLI_mempool_len(bm.fpool) ||
      bm.totloop != BLI_mempool_len(bm.lpool) || bm.toolflag_index != 0 ||
      !BLI_listbase_is_empty(&bm.errorstack))
  {
    return fail(r_error, "The BMesh snapshot requires valid counts and no pending operator state");
  }
  const std::array<const CustomData *, 4> data = mesh_data(bm);
  for (int i = 0; i < 4; i++) {
    if (!schema_read(*data[i], schemas[i], r_error, collect_layers)) {
      return false;
    }
  }
  return true;
}

/* BMIter's API takes a mutable mesh, but mesh iteration only changes the iterator. Do not call
 * index/table ensuring or BM_mesh_validate: the latter also writes source API tags. */
static BMesh *iteration_mesh(const BMesh &bm)
{
  return const_cast<BMesh *>(&bm);
}

static OwnedMesh mesh_clone(const BMesh &source, const char **r_error)
{
  std::array<Schema, 4> schemas;
  if (!schemas_read(source, schemas, r_error)) {
    return {};
  }
  const BMAllocTemplate allocsize = {
      source.totvert, source.totedge, source.totloop, source.totface};
  BMeshCreateParams params{};
  params.use_toolflags = source.use_toolflags;
  OwnedMesh owned(BM_mesh_create(&allocsize, &params));
  BMesh &destination = *owned;
  std::array<CustomData *, 4> data = {
      &destination.vdata, &destination.edata, &destination.pdata, &destination.ldata};
  const std::array<int, 4> counts = {
      source.totvert, source.totedge, source.totface, source.totloop};
  const std::array<char, 4> domains = {BM_VERT, BM_EDGE, BM_FACE, BM_LOOP};
  for (int i = 0; i < 4; i++) {
    if (!schema_copy(schemas[i], *data[i], counts[i], domains[i], r_error)) {
      return {};
    }
  }
  Map<const BMVert *, BMVert *> vertices;
  Map<const BMEdge *, BMEdge *> edges;
  Map<const BMFace *, BMFace *> faces;
  vertices.reserve(source.totvert);
  edges.reserve(source.totedge);
  faces.reserve(source.totface);
  BMIter iter;
  const BMVert *vert;
  BM_ITER_MESH (vert, &iter, iteration_mesh(source), BM_VERTS_OF_MESH) {
    BMVert *copy = BM_vert_create(&destination, vert->co, nullptr, BM_CREATE_SKIP_CD);
    std::memcpy(copy->no, vert->no, sizeof(copy->no));
    copy->head.hflag = vert->head.hflag;
    if (!block_copy(schemas[0], vert->head.data, destination.vdata, &copy->head.data, r_error)) {
      return {};
    }
    vertices.add_new(vert, copy);
  }
  const BMEdge *edge;
  BM_ITER_MESH (edge, &iter, iteration_mesh(source), BM_EDGES_OF_MESH) {
    BMVert *v1 = vertices.lookup_default(edge->v1, nullptr);
    BMVert *v2 = vertices.lookup_default(edge->v2, nullptr);
    if (!v1 || !v2) {
      fail(r_error, "A BMesh snapshot edge references a non-live vertex");
      return {};
    }
    BMEdge *copy = BM_edge_create(&destination, v1, v2, nullptr, BM_CREATE_SKIP_CD);
    copy->head.hflag = edge->head.hflag;
    if (!block_copy(schemas[1], edge->head.data, destination.edata, &copy->head.data, r_error)) {
      return {};
    }
    edges.add_new(edge, copy);
  }
  const BMFace *face;
  BM_ITER_MESH (face, &iter, iteration_mesh(source), BM_FACES_OF_MESH) {
    if (face->len < 2 || face->len > source.totloop || !BM_FACE_FIRST_LOOP(face)) {
      fail(r_error, "A BMesh snapshot face has invalid corner storage");
      return {};
    }
    Vector<BMVert *, 4> face_vertices;
    Vector<BMEdge *, 4> face_edges;
    const BMLoop *loop = BM_FACE_FIRST_LOOP(face);
    for (int i = 0; i < face->len; i++, loop = loop->next) {
      if (!loop) {
        fail(r_error, "A BMesh snapshot face has an incomplete corner cycle");
        return {};
      }
      BMVert *v = vertices.lookup_default(loop->v, nullptr);
      BMEdge *e = edges.lookup_default(loop->e, nullptr);
      if (!v || !e || loop->f != face) {
        fail(r_error, "A BMesh snapshot corner references a non-live element");
        return {};
      }
      face_vertices.append(v);
      face_edges.append(e);
    }
    if (loop != BM_FACE_FIRST_LOOP(face)) {
      fail(r_error, "A BMesh snapshot face has an inconsistent corner cycle");
      return {};
    }
    BMFace *copy = BM_face_create(&destination,
                                  face_vertices.data(),
                                  face_edges.data(),
                                  face->len,
                                  nullptr,
                                  BM_CREATE_SKIP_CD);
    if (!copy) {
      fail(r_error, "A BMesh snapshot face could not be copied");
      return {};
    }
    std::memcpy(copy->no, face->no, sizeof(copy->no));
    copy->mat_nr = face->mat_nr;
    copy->head.hflag = face->head.hflag;
    if (!block_copy(schemas[2], face->head.data, destination.pdata, &copy->head.data, r_error)) {
      return {};
    }
    BMLoop *copy_loop = BM_FACE_FIRST_LOOP(copy);
    for (int i = 0; i < face->len; i++, loop = loop->next, copy_loop = copy_loop->next) {
      copy_loop->head.hflag = loop->head.hflag;
      if (!block_copy(
              schemas[3], loop->head.data, destination.ldata, &copy_loop->head.data, r_error))
      {
        return {};
      }
    }
    faces.add_new(face, copy);
  }
  if (destination.totvert != source.totvert || destination.totedge != source.totedge ||
      destination.totface != source.totface || destination.totloop != source.totloop)
  {
    fail(r_error, "The BMesh snapshot element counts changed while copying");
    return {};
  }
  destination.act_face = faces.lookup_default(source.act_face, nullptr);
  if (source.act_face && !destination.act_face) {
    fail(r_error, "The BMesh snapshot active face is not live");
    return {};
  }
  for (const BMEditSelection &selection : source.selected) {
    BMHeader *copy = nullptr;
    switch (selection.htype) {
      case BM_VERT:
        copy = reinterpret_cast<BMHeader *>(
            vertices.lookup_default(reinterpret_cast<const BMVert *>(selection.ele), nullptr));
        break;
      case BM_EDGE:
        copy = reinterpret_cast<BMHeader *>(
            edges.lookup_default(reinterpret_cast<const BMEdge *>(selection.ele), nullptr));
        break;
      case BM_FACE:
        copy = reinterpret_cast<BMHeader *>(
            faces.lookup_default(reinterpret_cast<const BMFace *>(selection.ele), nullptr));
        break;
    }
    if (!copy) {
      fail(r_error, "The BMesh snapshot selection history references a non-live element");
      return {};
    }
    _bm_select_history_store_notest(&destination, copy);
  }
  destination.totvertsel = source.totvertsel;
  destination.totedgesel = source.totedgesel;
  destination.totfacesel = source.totfacesel;
  destination.selectmode = source.selectmode;
  destination.shapenr = source.shapenr;
  destination.uv_select_sync_valid = source.uv_select_sync_valid;
  destination.elem_index_dirty = BM_ALL;
  destination.elem_table_dirty = BM_VERT | BM_EDGE | BM_FACE;
  destination.spacearr_dirty = BM_SPACEARR_DIRTY_ALL;
  return owned;
}

static bool mesh_equal(const BMesh &a, const BMesh &b)
{
  if (a.totvert != b.totvert || a.totedge != b.totedge || a.totface != b.totface ||
      a.totloop != b.totloop || a.totvertsel != b.totvertsel || a.totedgesel != b.totedgesel ||
      a.totfacesel != b.totfacesel || a.selectmode != b.selectmode || a.shapenr != b.shapenr ||
      a.uv_select_sync_valid != b.uv_select_sync_valid || a.use_toolflags != b.use_toolflags)
  {
    return false;
  }
  std::array<Schema, 4> a_schemas, b_schemas;
  if (!schemas_read(a, a_schemas, nullptr) || !schemas_read(b, b_schemas, nullptr)) {
    return false;
  }
  for (int i = 0; i < 4; i++) {
    if (!schema_equal(a_schemas[i], b_schemas[i])) {
      return false;
    }
  }
  Map<const BMVert *, const BMVert *> vertices;
  Map<const BMEdge *, const BMEdge *> edges;
  Map<const BMFace *, const BMFace *> faces;
  vertices.reserve(a.totvert);
  edges.reserve(a.totedge);
  faces.reserve(a.totface);
  BMIter ai, bi;
  const BMVert *av = static_cast<const BMVert *>(
      BM_iter_new(&ai, iteration_mesh(a), BM_VERTS_OF_MESH, nullptr));
  const BMVert *bv = static_cast<const BMVert *>(
      BM_iter_new(&bi, iteration_mesh(b), BM_VERTS_OF_MESH, nullptr));
  for (; av && bv; av = static_cast<const BMVert *>(BM_iter_step(&ai)),
                   bv = static_cast<const BMVert *>(BM_iter_step(&bi)))
  {
    if (std::memcmp(av->co, bv->co, sizeof(av->co)) ||
        std::memcmp(av->no, bv->no, sizeof(av->no)) || av->head.hflag != bv->head.hflag ||
        !block_equal(a_schemas[0], av->head.data, b_schemas[0], bv->head.data))
    {
      return false;
    }
    vertices.add_new(av, bv);
  }
  if (av || bv || vertices.size() != a.totvert) {
    return false;
  }
  const BMEdge *ae = static_cast<const BMEdge *>(
      BM_iter_new(&ai, iteration_mesh(a), BM_EDGES_OF_MESH, nullptr));
  const BMEdge *be = static_cast<const BMEdge *>(
      BM_iter_new(&bi, iteration_mesh(b), BM_EDGES_OF_MESH, nullptr));
  for (; ae && be; ae = static_cast<const BMEdge *>(BM_iter_step(&ai)),
                   be = static_cast<const BMEdge *>(BM_iter_step(&bi)))
  {
    if (vertices.lookup_default(ae->v1, nullptr) != be->v1 ||
        vertices.lookup_default(ae->v2, nullptr) != be->v2 || ae->head.hflag != be->head.hflag ||
        !block_equal(a_schemas[1], ae->head.data, b_schemas[1], be->head.data))
    {
      return false;
    }
    edges.add_new(ae, be);
  }
  if (ae || be || edges.size() != a.totedge) {
    return false;
  }
  const BMFace *af = static_cast<const BMFace *>(
      BM_iter_new(&ai, iteration_mesh(a), BM_FACES_OF_MESH, nullptr));
  const BMFace *bf = static_cast<const BMFace *>(
      BM_iter_new(&bi, iteration_mesh(b), BM_FACES_OF_MESH, nullptr));
  int64_t loop_count = 0;
  for (; af && bf; af = static_cast<const BMFace *>(BM_iter_step(&ai)),
                   bf = static_cast<const BMFace *>(BM_iter_step(&bi)))
  {
    if (af->len < 2 || af->len != bf->len || af->len > a.totloop ||
        af->head.hflag != bf->head.hflag || af->mat_nr != bf->mat_nr ||
        std::memcmp(af->no, bf->no, sizeof(af->no)) ||
        !block_equal(a_schemas[2], af->head.data, b_schemas[2], bf->head.data))
    {
      return false;
    }
    const BMLoop *al = BM_FACE_FIRST_LOOP(af), *bl = BM_FACE_FIRST_LOOP(bf);
    if (!al || !bl) {
      return false;
    }
    for (int i = 0; i < af->len; i++, al = al->next, bl = bl->next) {
      if (!al || !bl || al->f != af || bl->f != bf ||
          vertices.lookup_default(al->v, nullptr) != bl->v ||
          edges.lookup_default(al->e, nullptr) != bl->e || al->head.hflag != bl->head.hflag ||
          !block_equal(a_schemas[3], al->head.data, b_schemas[3], bl->head.data))
      {
        return false;
      }
    }
    if (al != BM_FACE_FIRST_LOOP(af) || bl != BM_FACE_FIRST_LOOP(bf)) {
      return false;
    }
    loop_count += af->len;
    faces.add_new(af, bf);
  }
  if (af || bf || faces.size() != a.totface || loop_count != a.totloop ||
      faces.lookup_default(a.act_face, nullptr) != b.act_face)
  {
    return false;
  }
  const BMEditSelection *as = static_cast<const BMEditSelection *>(a.selected.first);
  const BMEditSelection *bs = static_cast<const BMEditSelection *>(b.selected.first);
  for (; as && bs; as = as->next, bs = bs->next) {
    if (as->htype != bs->htype) {
      return false;
    }
    const void *mapped = nullptr;
    switch (as->htype) {
      case BM_VERT:
        mapped = vertices.lookup_default(reinterpret_cast<const BMVert *>(as->ele), nullptr);
        break;
      case BM_EDGE:
        mapped = edges.lookup_default(reinterpret_cast<const BMEdge *>(as->ele), nullptr);
        break;
      case BM_FACE:
        mapped = faces.lookup_default(reinterpret_cast<const BMFace *>(as->ele), nullptr);
        break;
    }
    if (!mapped || mapped != bs->ele) {
      return false;
    }
  }
  return !as && !bs;
}

static bool block_memory(const Schema &schema, const void *block, size_t &bytes)
{
  for (const CustomDataLayer &raw_layer : Span(schema.data->layers, schema.data->totlayer)) {
    const CustomDataLayer *layer = &raw_layer;
    if (layer_kind(layer->type) == LayerKind::PythonPointer) { continue; }
    const void *value = layer_value(block, *layer);
    if (!payload_valid(*layer, value, nullptr)) { return false; }
    switch (layer_kind(layer->type)) {
      case LayerKind::Fixed:
        break;
      case LayerKind::Deform: {
        const MDeformVert &data = *static_cast<const MDeformVert *>(value);
        if (data.dw && !add_bytes(bytes, MEM_allocN_len(data.dw))) {
          return false;
        }
        break;
      }
      case LayerKind::Displacements: {
        const MDisps &data = *static_cast<const MDisps *>(value);
        if ((data.disps && !add_bytes(bytes, MEM_allocN_len(data.disps))) ||
            (data.hidden && !add_bytes(bytes, MEM_allocN_len(data.hidden))))
        {
          return false;
        }
        break;
      }
      case LayerKind::GridMask: {
        const GridPaintMask &data = *static_cast<const GridPaintMask *>(value);
        if (data.data && !add_bytes(bytes, MEM_allocN_len(data.data))) {
          return false;
        }
        break;
      }
      default:
        return false;
    }
  }
  return true;
}

static bool mesh_memory(const BMesh &bm, size_t &bytes)
{
  if (!add_bytes(bytes, MEM_allocN_len(&bm))) {
    return false;
  }
  for (const BLI_mempool *pool : {bm.vpool,
                                  bm.epool,
                                  bm.lpool,
                                  bm.fpool,
                                  bm.vtoolflagpool,
                                  bm.etoolflagpool,
                                  bm.ftoolflagpool})
  {
    if (!add_bytes(bytes, BLI_mempool_allocated_bytes(pool))) {
      return false;
    }
  }
#ifdef USE_BMESH_HOLES
  if (!add_bytes(bytes, BLI_mempool_allocated_bytes(bm.looplistpool))) {
    return false;
  }
#endif
  for (const void *table : {static_cast<const void *>(bm.vtable),
                            static_cast<const void *>(bm.etable),
                            static_cast<const void *>(bm.ftable)})
  {
    if (table && !add_bytes(bytes, MEM_allocN_len(table))) {
      return false;
    }
  }
  std::array<Schema, 4> schemas;
  if (!schemas_read(bm, schemas, nullptr, false)) {
    return false;
  }
  for (const Schema &schema : schemas) {
    const CustomData &data = *schema.data;
    if (!add_bytes(bytes, BLI_mempool_allocated_bytes(data.pool)) ||
        (data.layers && !add_bytes(bytes, MEM_allocN_len(data.layers))) ||
        (data.external && !add_bytes(bytes, MEM_allocN_len(data.external))))
    {
      return false;
    }
  }
  for (const BMEditSelection &selection : bm.selected) {
    if (!add_bytes(bytes, MEM_allocN_len(&selection))) {
      return false;
    }
  }
  BMIter iter;
  const BMVert *vert;
  BM_ITER_MESH (vert, &iter, iteration_mesh(bm), BM_VERTS_OF_MESH) {
    if (!block_memory(schemas[0], vert->head.data, bytes)) {
      return false;
    }
  }
  const BMEdge *edge;
  BM_ITER_MESH (edge, &iter, iteration_mesh(bm), BM_EDGES_OF_MESH) {
    if (!block_memory(schemas[1], edge->head.data, bytes)) {
      return false;
    }
  }
  const BMFace *face;
  BM_ITER_MESH (face, &iter, iteration_mesh(bm), BM_FACES_OF_MESH) {
    if (!block_memory(schemas[2], face->head.data, bytes)) {
      return false;
    }
    const BMLoop *loop = BM_FACE_FIRST_LOOP(face);
    for (int i = 0; i < face->len; i++, loop = loop->next) {
      if (!block_memory(schemas[3], loop->head.data, bytes)) {
        return false;
      }
    }
  }
  return true;
}

}  // namespace

BMGeometrySnapshot *BM_geometry_snapshot_create(const BMesh *bm, const char **r_error)
{
  if (r_error) {
    *r_error = nullptr;
  }
  if (!bm) {
    fail(r_error, "A BMesh is required for the geometry snapshot");
    return nullptr;
  }
  OwnedMesh copy = mesh_clone(*bm, r_error);
  if (!copy) {
    return nullptr;
  }
  size_t bytes = sizeof(BMGeometrySnapshot);
  if (!mesh_memory(*copy, bytes)) {
    fail(r_error, "The BMesh snapshot owned memory size cannot be represented");
    return nullptr;
  }
  BMGeometrySnapshot *snapshot = MEM_new<BMGeometrySnapshot>(__func__);
  snapshot->mesh = copy.release();
  snapshot->owned_bytes = bytes;
  return snapshot;
}

bool BM_geometry_snapshot_preallocation_bound(const BMesh *bm, size_t &r_bytes,
                                               const char **r_error)
{
  r_bytes = 0;
  if (r_error) { *r_error = nullptr; }
  if (!bm) { return fail(r_error, "A BMesh is required for bounded geometry capture"); }
  /* This traversal is allocation-free: schema validation does not collect layer pointers.
   * Clone pools use the same fixed chunk sizes and no more live elements than the source.
   * Charge twice the complete source backing for clone backing/layout temporaries. The
   * separate per-element reserve covers all three pointer maps (power-of-two reserve/load
   * factor), per-face vertex/edge scratch, and selection nodes. Per-layer reserve covers the
   * simultaneous schema vectors/descriptors; 64 KiB covers fixed empty pool/control scratch.
   * This is cumulative admission, deliberately not a measurement of the final snapshot. */
  size_t source_bytes = sizeof(BMGeometrySnapshot);
  size_t scratch = 64 * 1024;
  if (!mesh_memory(*bm, source_bytes) || source_bytes > SIZE_MAX / 2) {
    return fail(r_error, "BMesh preallocation backing size cannot be represented");
  }
  for (const int count : {bm->totvert, bm->totedge, bm->totface, bm->totloop}) {
    size_t bytes;
    if (count < 0 || !multiply_bytes(size_t(count), 512, bytes) || !add_bytes(scratch, bytes)) {
      return fail(r_error, "BMesh preallocation scratch size cannot be represented");
    }
  }
  for (const CustomData *data : mesh_data(*bm)) {
    size_t bytes;
    if (!multiply_bytes(size_t(data->totlayer), 256 + sizeof(CustomDataLayer) * 4, bytes) ||
        !add_bytes(scratch, bytes)) {
      return fail(r_error, "BMesh layer preallocation size cannot be represented");
    }
  }
  r_bytes = source_bytes * 2;
  return add_bytes(r_bytes, scratch) ||
         fail(r_error, "BMesh preallocation total cannot be represented");
}

BMGeometrySnapshot *BM_geometry_snapshot_create_bounded(const BMesh *bm, const size_t limit,
                                                       size_t &used, const char **r_error)
{
  size_t charge;
  if (!BM_geometry_snapshot_preallocation_bound(bm, charge, r_error)) { return nullptr; }
  if (used > limit || charge > limit - used) {
    fail(r_error, "BMesh capture exceeds finite preallocation admission");
    return nullptr;
  }
  used += charge;
  return BM_geometry_snapshot_create(bm, r_error);
}

BMesh *BM_geometry_snapshot_restore(const BMGeometrySnapshot *snapshot, const char **r_error)
{
  if (r_error) {
    *r_error = nullptr;
  }
  if (!snapshot) {
    fail(r_error, "A captured BMesh geometry snapshot is required");
    return nullptr;
  }
  return mesh_clone(*snapshot->mesh, r_error).release();
}

BMesh *BM_geometry_snapshot_restore_bounded(const BMGeometrySnapshot *snapshot,
                                            const size_t limit, size_t &used,
                                            const char **r_error)
{
  size_t charge;
  if (!snapshot || !BM_geometry_snapshot_preallocation_bound(snapshot->mesh, charge, r_error)) {
    fail(r_error, "Finite geometry restore requires a valid snapshot"); return nullptr;
  }
  if (used > limit || charge > limit - used) {
    fail(r_error, "Geometry replay exceeds finite preallocation admission"); return nullptr;
  }
  used += charge;
  return BM_geometry_snapshot_restore(snapshot, r_error);
}

bool BM_geometry_snapshot_equals(const BMGeometrySnapshot *snapshot, const BMesh *bm)
{
  return snapshot && bm && mesh_equal(*snapshot->mesh, *bm);
}

bool BM_geometry_snapshot_equals(const BMGeometrySnapshot *a, const BMGeometrySnapshot *b)
{
  return a && b && mesh_equal(*a->mesh, *b->mesh);
}

size_t BM_geometry_snapshot_memory_size(const BMGeometrySnapshot *snapshot)
{
  return snapshot ? snapshot->owned_bytes : 0;
}

bool BM_geometry_snapshot_encode(const BMGeometrySnapshot *snapshot,
                                 const BMGeometryCodecLimits &limits,
                                 FunctionRef<bool(Span<std::byte>)> append,
                                 const char **r_error)
{
  if (r_error) {
    *r_error = nullptr;
  }
  if (!snapshot) {
    return fail(r_error, "A captured BMesh geometry snapshot is required for encoding");
  }
  return bmesh::snapshot_io::encode_mesh(*snapshot->mesh, limits, append, r_error);
}

void BM_geometry_snapshot_free(BMGeometrySnapshot *snapshot)
{
  if (snapshot) {
    BM_mesh_free(snapshot->mesh);
    MEM_delete(snapshot);
  }
}

}  // namespace blender
