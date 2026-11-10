/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * What a bake read that Python cannot reach, for the baked-material digest (`_bpy._angelo_digest`, read by
 * `export_baked_images.py` and `export_baked_hidden.py`; contract: doc/angelo/long_jobs.rst, "Baked materials at
 * export").
 *
 * - `pixels(image, image_user, scene_frame, tiles)`: the pixels a bake reads of an image Cycles takes from Blender's
 *   memory (`image_is_builtin`, intern/cycles/blender/util.h: packed, generated, a movie, unsaved edits, a UDIM image
 *   with a generated tile), read as Cycles' #BlenderImageLoader reads them: the node's image user at the frame a
 *   render computes (#BKE_image_user_frame_calc), each UDIM tile's buffer through #BKE_image_acquire_ibuf, loaded
 *   from the image's source when not held. A buffer the read loaded leaves the image's cache again right after it is
 *   hashed, so a read never keeps pixels in memory or holds more than one extra tile at a time; an image that held
 *   nothing before also closes what loading opened (a movie, a multilayer file), as that loader frees it. A buffer is hashed as stored (XXH3-128 over its floats, else its bytes): what Blender holds can
 *   differ from the image's packed bytes or files (a file saved over while the image holds the old pixels, packing
 *   after that), and only the buffer is what the bake reads. Copying a 4K tile into Python as floats took about a
 *   quarter of a second (measured, doc/angelo/long_jobs.rst); XXH3 reads one in milliseconds.
 * - `holds(image)`: whether a read of the image would decode its source as it is now: no cached buffer, no open
 *   movie decoder, no multilayer file's render result (each outlives a change to the source until a reload).
 * - `packed(image)`: each packed file's XXH3-128 (tile, view, size, digest), the source a packed image's pixels load
 *   from, without copying it into Python (`PackedFile.data` copies the bytes).
 * - `instances(depsgraph, objects, callback)`: the geometry a render shows of objects `Depsgraph.object_instances`
 *   leaves out because the viewport hides them, read as the viewport evaluates a shown object. One hidden with the
 *   eye is still evaluated by the viewport's depsgraph (measured) but never iterated, so its instances (geometry
 *   nodes, a collection instance, particles) were not seen; one disabled in viewports is not evaluated there at all,
 *   so it is evaluated here in a temporary depsgraph built for those objects alone, with the visibility
 *   optimization off, as Blender's own Apply Modifier does for a hidden object (#modifier_apply). Each piece of
 *   geometry is handed to `callback(geometry, original, matrix, is_instance)` while it exists: `geometry` is the
 *   evaluated object (its own geometry, or an instanced object's) or the data an instance replaced it with,
 *   `original` the original object it belongs to and `matrix` its world matrix (rows). Neither the geometry nor
 *   anything read from it is valid after the callback returns.
 *
 * Main thread only.
 */

#include <Python.h>

#include <cstdint>
#include <mutex>

#include <xxhash.h>

#include "BLI_math_matrix_types.hh"
#include "BLI_threads.h"
#include "BLI_vector.hh"

#include "BKE_duplilist.hh"
#include "BKE_geometry_set_instances.hh"
#include "BKE_image.hh"
#include "BKE_layer.hh"
#include "BKE_object.hh"
#include "BKE_object_types.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_build.hh"
#include "DEG_depsgraph_query.hh"

#include "DNA_image_types.h"
#include "DNA_packedFile_types.h"
#include "DNA_object_types.h"

#include "IMB_cache.hh"
#include "IMB_imbuf.hh"
#include "IMB_imbuf_types.hh"

#include "../generic/py_capi_utils.hh"

#include "bpy_angelo_digest.hh"
#include "bpy_angelo_live_part.hh"
#include "bpy_rna.hh"

namespace blender {

static bool digest_require_main_thread()
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "A bake's digest is read on Blender's main thread only");
    return false;
  }
  return true;
}

/* -------------------------------------------------------------------- */
/** \name Pixels a bake reads from memory
 * \{ */

struct ReadBuffer {
  int tile = 0;
  int width = 0;
  int height = 0;
  int channels = 0;
  bool is_float = false;
  bool found = false;
  XXH128_hash_t hash{};
};

/** Hash \a ibuf as Cycles' loader copies it: the floats when there are any, else the bytes (four channels). */
static void read_buffer(const ImBuf *ibuf, ReadBuffer &buffer)
{
  buffer.found = true;
  buffer.width = ibuf->x;
  buffer.height = ibuf->y;
  const size_t pixels = IMB_get_pixel_count(ibuf);
  if (const float *data = ibuf->float_data()) {
    buffer.channels = ibuf->channels > 0 ? ibuf->channels : 4;
    buffer.is_float = true;
    buffer.hash = XXH3_128bits(data, sizeof(float) * pixels * size_t(buffer.channels));
  }
  else if (const uint8_t *data = ibuf->byte_data()) {
    buffer.channels = 4;
    buffer.hash = XXH3_128bits(data, pixels * 4);
  }
  else {
    buffer.hash = XXH3_128bits(nullptr, 0);
  }
}

/** The image cache's entries by key: the cache key is `ImageCacheKey {int index;}` (image.cc), the entry (tile or
 * frame) and view or pass a buffer stands for. */
static Vector<int> cached_entries(Image *image)
{
  Vector<int> entries;
  std::scoped_lock lock(image->runtime->cache_mutex);
  if (image->runtime->cache != nullptr) {
    ImBufCacheIter *iter = IMB_cacheIter_new(image->runtime->cache);
    while (!IMB_cacheIter_done(iter)) {
      entries.append(*static_cast<const int *>(IMB_cacheIter_getUserKey(iter)));
      IMB_cacheIter_step(iter);
    }
    IMB_cacheIter_free(iter);
  }
  return entries;
}

/** Remove the cache entries a read added (absent from \a before), unless edited meanwhile. */
static void drop_added(Image *image, const Vector<int> &before)
{
  std::scoped_lock lock(image->runtime->cache_mutex);
  if (image->runtime->cache == nullptr) {
    return;
  }
  Vector<int> added;
  ImBufCacheIter *iter = IMB_cacheIter_new(image->runtime->cache);
  while (!IMB_cacheIter_done(iter)) {
    const int index = *static_cast<const int *>(IMB_cacheIter_getUserKey(iter));
    const ImBuf *ibuf = IMB_cacheIter_getImBuf(iter);
    if (!before.contains(index) && (ibuf == nullptr || !(ibuf->userflags & IB_BITMAPDIRTY))) {
      added.append(index);
    }
    IMB_cacheIter_step(iter);
  }
  IMB_cacheIter_free(iter);
  for (int index : added) {
    IMB_cache_remove(image->runtime->cache, &index);
  }
}

static PyObject *digest_pixels(PyObject * /*self*/, PyObject *args)
{
  if (!digest_require_main_thread()) {
    return nullptr;
  }
  PyObject *py_image, *py_user, *py_tiles;
  int scene_frame;
  if (!PyArg_ParseTuple(args, "OOiO:pixels", &py_image, &py_user, &scene_frame, &py_tiles)) {
    return nullptr;
  }
  Image *image = static_cast<Image *>(PyC_RNA_AsPointer(py_image, "Image"));
  if (image == nullptr) {
    return nullptr;
  }
  ImageUser user;
  if (py_user == Py_None) {
    BKE_imageuser_default(&user);
  }
  else {
    const ImageUser *given = static_cast<const ImageUser *>(PyC_RNA_AsPointer(py_user, "ImageUser"));
    if (given == nullptr) {
      return nullptr;
    }
    user = *given;
  }
  /* The frame Cycles reads (`image_user_frame_number`): computed for a movie or a sequence, 0 for a still. */
  BKE_image_user_frame_calc(image, &user, scene_frame);

  PyObject *tiles = PySequence_Fast(py_tiles, "pixels: tiles must be a sequence of tile numbers");
  if (tiles == nullptr) {
    return nullptr;
  }
  Vector<ReadBuffer> buffers;
  const Py_ssize_t count = PySequence_Fast_GET_SIZE(tiles);
  for (Py_ssize_t index = 0; index < count; index++) {
    const long tile = PyLong_AsLong(PySequence_Fast_GET_ITEM(tiles, index));
    if (tile == -1 && PyErr_Occurred()) {
      Py_DECREF(tiles);
      return nullptr;
    }
    ReadBuffer buffer;
    buffer.tile = int(tile);
    buffers.append(buffer);
  }
  Py_DECREF(tiles);

  /* What the image held before: a buffer this read loads leaves again once hashed (one tile at a time), so a read
   * never keeps pixels in memory that nothing else holds, and never holds a whole tile set at once. */
  const bool held = BKE_image_has_loaded_ibuf(image);
  Py_BEGIN_ALLOW_THREADS;
  const Vector<int> before = cached_entries(image);
  for (ReadBuffer &buffer : buffers) {
    ImageUser tile_user = user;
    if (image->source == IMA_SRC_TILED) {
      tile_user.tile = buffer.tile; /* Each UDIM tile has a buffer of its own. */
    }
    void *lock;
    ImBuf *ibuf = BKE_image_acquire_ibuf(image, &tile_user, &lock);
    if (ibuf != nullptr) {
      read_buffer(ibuf, buffer);
    }
    BKE_image_release_ibuf(image, ibuf, lock);
    drop_added(image, before);
  }
  if (!held) {
    /* As #BlenderImageLoader frees an image it loaded: what loading opened besides buffers (a movie's decoder, a
     * multilayer file's render result) goes too. */
    BKE_image_free_buffers_ex(image, true);
  }
  Py_END_ALLOW_THREADS;

  PyObject *result = PyList_New(buffers.size());
  if (result == nullptr) {
    return nullptr;
  }
  for (const int64_t i : buffers.index_range()) {
    const ReadBuffer &buffer = buffers[i];
    PyObject *digest;
    if (buffer.found) {
      XXH128_canonical_t canonical;
      XXH128_canonicalFromHash(&canonical, buffer.hash);
      digest = PyBytes_FromStringAndSize(reinterpret_cast<const char *>(canonical.digest), sizeof(canonical.digest));
    }
    else {
      digest = Py_NewRef(Py_None); /* No buffer: a missing file, unreadable data, no such tile. */
    }
    PyObject *item = Py_BuildValue("(iiiiON)",
                                   buffer.tile,
                                   buffer.width,
                                   buffer.height,
                                   buffer.channels,
                                   buffer.is_float ? Py_True : Py_False,
                                   digest);
    if (item == nullptr) {
      Py_DECREF(result);
      return nullptr;
    }
    PyList_SET_ITEM(result, i, item);
  }
  return result;
}

/** Whether the image holds anything a load reads instead of its source: a cached buffer, an open movie decoder
 * (`anims`, kept until the image is freed), a multilayer file's render result (`rr`). A read of an image holding
 * none of these decodes its source as it is now. */
static PyObject *digest_holds(PyObject * /*self*/, PyObject *py_image)
{
  if (!digest_require_main_thread()) {
    return nullptr;
  }
  Image *image = static_cast<Image *>(PyC_RNA_AsPointer(py_image, "Image"));
  if (image == nullptr) {
    return nullptr;
  }
  const bool holds = BKE_image_has_loaded_ibuf(image) || image->anims.first != nullptr || image->rr != nullptr;
  return PyBool_FromLong(holds);
}

static PyObject *digest_packed(PyObject * /*self*/, PyObject *py_image)
{
  if (!digest_require_main_thread()) {
    return nullptr;
  }
  Image *image = static_cast<Image *>(PyC_RNA_AsPointer(py_image, "Image"));
  if (image == nullptr) {
    return nullptr;
  }
  PyObject *result = PyList_New(0);
  if (result == nullptr) {
    return nullptr;
  }
  for (const ImagePackedFile &imapf : image->packedfiles) {
    const PackedFile *packed = imapf.packedfile;
    if (packed == nullptr) {
      continue;
    }
    XXH128_canonical_t canonical;
    XXH128_canonicalFromHash(&canonical, XXH3_128bits(packed->data, size_t(packed->size)));
    PyObject *item = Py_BuildValue(
        "(iiiN)",
        imapf.tile_number,
        imapf.view,
        packed->size,
        PyBytes_FromStringAndSize(reinterpret_cast<const char *>(canonical.digest), sizeof(canonical.digest)));
    if (item == nullptr || PyList_Append(result, item) == -1) {
      Py_XDECREF(item);
      Py_DECREF(result);
      return nullptr;
    }
    Py_DECREF(item);
  }
  return result;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Instances of hidden objects
 * \{ */

/** #BKE_object_visibility without its first test, the base's viewport visibility: what an object shows of itself
 * and of its instances once shown. */
static int visibility_when_shown(const Object &ob, const eEvaluationMode mode)
{
  int visibility = OB_VISIBLE_SELF;
  if (ob.particlesystem.first) {
    visibility |= OB_VISIBLE_INSTANCES | OB_VISIBLE_PARTICLES;
  }
  else if (ob.transflag & OB_DUPLI) {
    visibility |= OB_VISIBLE_INSTANCES;
  }
  if (bke::object_has_geometry_set_instances(ob)) {
    visibility |= OB_VISIBLE_INSTANCES;
  }
  if (visibility & (OB_VISIBLE_PARTICLES | OB_VISIBLE_INSTANCES)) {
    const int flag = mode == DAG_EVAL_RENDER ? OB_DUPLI_FLAG_RENDER : OB_DUPLI_FLAG_VIEWPORT;
    if (!(ob.duplicator_visibility_flag & flag)) {
      visibility &= ~OB_VISIBLE_SELF;
    }
  }
  return visibility;
}

/** `matrix` as the rows of a `mathutils.Matrix` (a #float4x4 is stored by columns). */
static PyObject *matrix_rows(const float4x4 &matrix)
{
  return Py_BuildValue("((ffff)(ffff)(ffff)(ffff))",
                       matrix[0][0], matrix[1][0], matrix[2][0], matrix[3][0],
                       matrix[0][1], matrix[1][1], matrix[2][1], matrix[3][1],
                       matrix[0][2], matrix[1][2], matrix[2][2], matrix[3][2],
                       matrix[0][3], matrix[1][3], matrix[2][3], matrix[3][3]);
}

/** Hand one piece of geometry to the callback; false with a Python error set when it raised. */
static bool hand(PyObject *callback, ID *geometry, Object *original, const float4x4 &matrix, const bool is_instance)
{
  PyObject *args = Py_BuildValue("(NNNO)",
                                 pyrna_id_CreatePyObject(geometry),
                                 pyrna_id_CreatePyObject(&original->id),
                                 matrix_rows(matrix),
                                 is_instance ? Py_True : Py_False);
  if (args == nullptr) {
    return false;
  }
  PyObject *result = PyObject_CallObject(callback, args);
  Py_DECREF(args);
  if (result == nullptr) {
    return false;
  }
  Py_DECREF(result);
  return true;
}

/** Hand what \a original shows in \a graph, as #DEG_iterator_objects_next would if it were shown: each instance it
 * makes (#object_duplilist, the iterator's own source, filtered as the iterator filters it) and its own geometry as
 * a render shows it (an instancer's own geometry with Show Instancer for renders on, never a child its parent
 * instances on vertices or faces). False with a Python error set when the callback raised. */
static bool hand_object(Depsgraph *graph, Object *original, PyObject *callback)
{
  Object *ob = DEG_get_evaluated(graph, original);
  if (ob == nullptr || ob == original) {
    return true; /* Not in this depsgraph: it shows nothing here. */
  }
  const eEvaluationMode mode = DEG_get_mode(graph);
  const int visibility = visibility_when_shown(*ob, mode);
  if ((visibility & OB_VISIBLE_INSTANCES) &&
      ((ob->transflag & OB_DUPLI) || ob->runtime->geometry_set_eval != nullptr))
  {
    DupliList duplis;
    object_duplilist(graph, ob, nullptr, duplis);
    for (const DupliObject &dob : duplis) {
      if (!DEG_iterator_dupli_is_visible(&dob, mode) || !live_dupli_shows_self(dob, mode)) {
        continue;
      }
      ID *geometry = (dob.ob_data != nullptr && dob.ob_data != dob.ob->data) ? dob.ob_data : &dob.ob->id;
      if (!hand(callback, geometry, DEG_get_original(dob.ob), float4x4(dob.mat), true)) {
        return false;
      }
    }
  }
  if ((visibility_when_shown(*ob, DAG_EVAL_RENDER) & OB_VISIBLE_SELF) &&
      DEG_iterator_object_is_visible(DAG_EVAL_RENDER, ob))
  {
    return hand(callback, &ob->id, original, ob->object_to_world(), false);
  }
  return true;
}

static PyObject *digest_instances(PyObject * /*self*/, PyObject *args)
{
  if (!digest_require_main_thread()) {
    return nullptr;
  }
  PyObject *py_depsgraph, *py_objects, *callback;
  if (!PyArg_ParseTuple(args, "OOO:instances", &py_depsgraph, &py_objects, &callback)) {
    return nullptr;
  }
  if (!PyCallable_Check(callback)) {
    PyErr_SetString(PyExc_TypeError, "instances: callback must be callable");
    return nullptr;
  }
  Depsgraph *depsgraph = static_cast<Depsgraph *>(PyC_RNA_AsPointer(py_depsgraph, "Depsgraph"));
  if (depsgraph == nullptr) {
    return nullptr;
  }
  ViewLayer *view_layer = DEG_get_input_view_layer(depsgraph);
  if (!DEG_is_fully_evaluated(depsgraph) || !BKE_view_layer_is_synced(*view_layer)) {
    /* Never geometry from a depsgraph not evaluated since its last change: the caller reads it as unknown. */
    PyErr_SetString(PyExc_RuntimeError, "instances: the depsgraph is not evaluated since its last change");
    return nullptr;
  }
  PyObject *objects = PySequence_Fast(py_objects, "instances: objects must be a sequence of objects");
  if (objects == nullptr) {
    return nullptr;
  }
  Vector<Object *> evaluated_here, unevaluated;
  const Py_ssize_t count = PySequence_Fast_GET_SIZE(objects);
  for (Py_ssize_t index = 0; index < count; index++) {
    Object *original = static_cast<Object *>(
        PyC_RNA_AsPointer(PySequence_Fast_GET_ITEM(objects, index), "Object"));
    if (original == nullptr) {
      Py_DECREF(objects);
      return nullptr;
    }
    /* Hidden with the eye, the viewport's depsgraph still evaluates it (its base is pulled in, #
     * DepsgraphBuilder::need_pull_base_into_graph); disabled in viewports, it is at most a dependency there,
     * evaluated only as far as another object needs it. */
    if (live_shown_here(view_layer, original, DAG_EVAL_VIEWPORT) && DEG_get_evaluated(depsgraph, original) != original)
    {
      evaluated_here.append(original);
    }
    else {
      unevaluated.append(original);
    }
  }
  Py_DECREF(objects);
  for (Object *original : evaluated_here) {
    if (!hand_object(depsgraph, original, callback)) {
      return nullptr;
    }
  }
  if (unevaluated.is_empty()) {
    Py_RETURN_NONE;
  }
  Depsgraph *local = DEG_graph_new(
      DEG_get_bmain(depsgraph), DEG_get_input_scene(depsgraph), view_layer, DEG_get_mode(depsgraph));
  struct FreeLocal { /* Also when a callback raises. */
    Depsgraph *graph;
    ~FreeLocal()
    {
      DEG_graph_free(graph);
    }
  } free_local{local};
  DEG_disable_visibility_optimization(local);
  Vector<ID *> ids;
  for (Object *original : unevaluated) {
    ids.append(&original->id);
  }
  DEG_graph_build_from_ids(local, ids);
  /* Drivers run during evaluation and take the GIL themselves (as `Depsgraph.update` lets them). */
  Py_BEGIN_ALLOW_THREADS;
  DEG_evaluate_on_refresh(local);
  Py_END_ALLOW_THREADS;
  for (Object *original : unevaluated) {
    /* Read as it would be once shown. Its own Disable in Viewports is all that differs, and a viewport-mode
     * depsgraph makes no instances for an object that has it (#get_dupli_generator respects the restriction
     * flags), so a scatter disabled in viewports read as its bare mesh. The evaluated copy is this temporary
     * depsgraph's alone and freed with it; what it instances keeps its own flags, as when the object is shown. */
    Object *ob = DEG_get_evaluated(local, original);
    if (ob != nullptr && ob != original) {
      ob->visibility_flag &= ~OB_HIDE_VIEWPORT;
    }
    if (!hand_object(local, original, callback)) {
      return nullptr;
    }
  }
  Py_RETURN_NONE;
}

/** \} */

static PyMethodDef methods[] = {
    {"pixels",
     reinterpret_cast<PyCFunction>(digest_pixels),
     METH_VARARGS,
     "pixels(image, image_user, scene_frame, tiles): [(tile, width, height, channels, is_float, digest)] for each "
     "buffer a bake reads of an image Cycles takes from Blender's memory, as Cycles' loader reads it (the image user, "
     "None for a default one, at the frame a render computes from scene_frame; each of the UDIM tiles of a tiled "
     "image), loading what is not held and dropping it again once hashed; digest is 16 bytes "
     "(XXH3-128) of the pixels as stored, None when there is no buffer."},
    {"holds",
     reinterpret_cast<PyCFunction>(digest_holds),
     METH_O,
     "holds(image): whether the image holds a cached buffer, an open movie decoder or a multilayer file's render "
     "result, any of which a load reads instead of the image's source."},
    {"packed",
     reinterpret_cast<PyCFunction>(digest_packed),
     METH_O,
     "packed(image): [(tile, view, size, digest)] for each packed file of the image; digest is 16 bytes (XXH3-128) "
     "of its bytes."},
    {"instances",
     reinterpret_cast<PyCFunction>(digest_instances),
     METH_VARARGS,
     "instances(depsgraph, objects, callback): call callback(geometry, original, matrix, is_instance) for the "
     "geometry each of the objects shows and each instance it makes, as the viewport evaluates a shown object, "
     "hidden in the viewport or not (one disabled in viewports is evaluated in a temporary depsgraph). The "
     "geometry is valid during the call only."},
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_bpy._angelo_digest",
    nullptr,
    0,
    methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

PyObject *BPY_angelo_digest_module()
{
  return PyModule_Create(&module);
}

}  // namespace blender
