/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * A picture on the system clipboard, saved as a PNG file (see `bpy_angelo_clipboard.hh`).
 *
 * Upstream Blender (through 5.4 alpha) reads a clipboard picture only into an Image data-block,
 * through the Image Editor's paste operator; its Python API has no clipboard picture at all. The
 * chat attaches files, never data-blocks, so this module saves the same `WM_clipboard_image_get`
 * buffer straight to a file: nothing is added to the document and no undo step is pushed.
 *
 * Both calls run on Blender's main thread (GHOST's clipboard is the window system's, owned by
 * that thread) and release the GIL while the operating system answers and the PNG is encoded,
 * so Python threads (a provider's worker) keep running. The picture is freed on every path.
 */

#include <Python.h>

#include <algorithm>
#include <cstdio>
#include <exception>

#include "BLI_fileops.h"
#include "BLI_path_utils.hh"
#include "BLI_threads.h"
#include "BLI_utildefines.h"
#include "BLI_vector.hh"

#include "IMB_imbuf.hh"
#include "IMB_imbuf_types.hh"

#include "WM_api.hh"

#include "bpy_angelo_clipboard.hh"

namespace blender {

/** Whether every pixel of \a ibuf's byte buffer (RGBA) is fully opaque. */
static bool byte_pixels_opaque(const ImBuf &ibuf)
{
  const uint8_t *pixels = ibuf.byte_data();
  const size_t count = size_t(ibuf.x) * size_t(ibuf.y);
  for (size_t i = 0; i < count; i++) {
    if (pixels[i * 4 + 3] != 255) {
      return false;
    }
  }
  return true;
}

/**
 * Make \a ibuf ready to encode as a PNG at \a compression: byte pixels (from float when it has
 * only those), no alpha channel when every pixel is opaque. False when it has no pixels.
 */
static bool prepare_png(ImBuf *ibuf, const int compression)
{
  if (ibuf == nullptr || ibuf->x <= 0 || ibuf->y <= 0) {
    return false;
  }
  if (ibuf->byte_data() == nullptr) {
    IMB_byte_from_float(ibuf); /* Nothing when there is no float buffer either. */
  }
  if (ibuf->byte_data() == nullptr) {
    return false;
  }
  ibuf->ftype = IMB_FTYPE_PNG;
  ibuf->foptions = ImbFormatOptions{};
  ibuf->foptions.compress = char(std::clamp(compression, 0, 100));
  /* A screenshot is opaque: three channels make a smaller file (the attachment limit is on its
   * bytes) and say what the picture is. Any translucent pixel keeps the alpha channel. */
  ibuf->color_mode = byte_pixels_opaque(*ibuf) ? ImColorMode::RGB : ImColorMode::RGBA;
  return true;
}

/** \a ibuf encoded as a PNG in memory (see #prepare_png); empty when it cannot be. */
static Vector<uint8_t> encode_png(ImBuf *ibuf, const int compression)
{
  if (!prepare_png(ibuf, compression)) {
    return {};
  }
  return IMB_save_image_to_buffer(ibuf, ImBufFlags::ByteData);
}

/**
 * Write \a bytes to \a filepath. \a r_started becomes true once the file is open for writing
 * (created, or an existing file emptied): from then on a failure leaves a partly written file. A
 * file that cannot be opened is never touched.
 */
static bool write_file(const char *filepath, const Span<uint8_t> bytes, bool &r_started)
{
  FILE *file = BLI_fopen(filepath, "wb");
  if (file == nullptr) {
    return false;
  }
  r_started = true;
  const bool written = fwrite(bytes.data(), 1, size_t(bytes.size()), file) == size_t(bytes.size());
  const bool closed = fclose(file) == 0;
  return written && closed;
}

bool angelo_clipboard_side_refused(const int width, const int height, const int max_side)
{
  return max_side > 0 && (width > max_side || height > max_side);
}

ClipboardSave angelo_clipboard_settle(const ClipboardSave result,
                                      const bool started_writing,
                                      const char *filepath)
{
  if (result != ClipboardSave::Failed || !started_writing || !BLI_exists(filepath)) {
    return result;
  }
  /* This call opened the file for writing: what is there now is its partial output. */
  return BLI_delete(filepath, false, false) == 0 ? ClipboardSave::Failed :
                                                   ClipboardSave::FailedFileLeft;
}

bool angelo_clipboard_write_png(ImBuf *ibuf, const char *filepath, const int compression)
{
  const Vector<uint8_t> png = encode_png(ibuf, compression);
  if (png.is_empty()) {
    return false;
  }
  bool started = false;
  if (write_file(filepath, png, started)) {
    return true;
  }
  angelo_clipboard_settle(ClipboardSave::Failed, started, filepath);
  return false;
}

static ClipboardSave save_unguarded(ImBuf *ibuf,
                                    const char *filepath,
                                    const int compression,
                                    const int max_side,
                                    const int64_t max_bytes,
                                    bool &r_started)
{
  if (ibuf == nullptr) {
    return ClipboardSave::Failed;
  }
  /* The size is known before the (slow) encode: a picture past the limit is never encoded. */
  if (angelo_clipboard_side_refused(ibuf->x, ibuf->y, max_side)) {
    return ClipboardSave::TooWide;
  }
  /* Encoded in memory and measured there: a refused picture never reaches the file. */
  Vector<uint8_t> png = encode_png(ibuf, compression);
  if (png.is_empty()) {
    return ClipboardSave::Failed;
  }
  if (max_bytes > 0 && int64_t(png.size()) > max_bytes && compression < 100) {
    /* Lossless, from the same pixels: the clipboard is read once. The first encode is released
     * before the second is made, so the two are never held together. */
    png.clear_and_shrink();
    png = encode_png(ibuf, 100);
    if (png.is_empty()) {
      return ClipboardSave::Failed;
    }
  }
  if (max_bytes > 0 && int64_t(png.size()) > max_bytes) {
    return ClipboardSave::TooHeavy;
  }
  return write_file(filepath, png, r_started) ? ClipboardSave::Saved : ClipboardSave::Failed;
}

ClipboardSave angelo_clipboard_save(ImBuf *ibuf,
                                    const char *filepath,
                                    const int compression,
                                    const int max_side,
                                    const int64_t max_bytes)
{
  ClipboardSave result = ClipboardSave::Failed;
  bool started = false;
  try {
    result = save_unguarded(ibuf, filepath, compression, max_side, max_bytes, started);
  }
  catch (...) {
    result = ClipboardSave::Failed;
  }
  return angelo_clipboard_settle(result, started, filepath);
}

static bool require_main_thread(const char *function)
{
  if (BLI_thread_is_main()) {
    return true;
  }
  PyErr_Format(PyExc_RuntimeError, "%s requires Blender's main thread", function);
  return false;
}

static PyObject *clipboard_image_available(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_main_thread("image_available")) {
    return nullptr;
  }
  bool available = false;
  Py_BEGIN_ALLOW_THREADS;
  try {
    available = WM_clipboard_image_available();
  }
  catch (...) {
    available = false;
  }
  Py_END_ALLOW_THREADS;
  return PyBool_FromLong(available);
}

static PyObject *clipboard_image_save(PyObject * /*self*/, PyObject *args, PyObject *kwds)
{
  static const char *keywords[] = {"filepath", "compression", "max_side", "max_bytes", nullptr};
  const char *filepath = nullptr;
  int compression = ImbFormatOptions{}.compress;
  int max_side = 0;
  long long max_bytes = 0;
  if (!PyArg_ParseTupleAndKeywords(args,
                                   kwds,
                                   "s|$iiL:image_save",
                                   const_cast<char **>(keywords),
                                   &filepath,
                                   &compression,
                                   &max_side,
                                   &max_bytes))
  {
    return nullptr;
  }
  if (!require_main_thread("image_save")) {
    return nullptr;
  }
  if (compression < 0 || compression > 100) {
    PyErr_SetString(PyExc_ValueError, "image_save: compression is 0 to 100");
    return nullptr;
  }
  if (max_side < 0 || max_bytes < 0) {
    PyErr_SetString(PyExc_ValueError,
                    "image_save: max_side and max_bytes are 0 (no limit) or more");
    return nullptr;
  }
  if (filepath[0] == '\0' || BLI_path_is_rel(filepath) || !BLI_path_is_abs_from_cwd(filepath)) {
    PyErr_SetString(PyExc_ValueError, "image_save: filepath must be absolute");
    return nullptr;
  }

  bool found = false;
  int width = 0, height = 0;
  ClipboardSave result = ClipboardSave::Failed;
  /* Nothing in this block may leave it by an exception: the GIL is taken back on every path. */
  Py_BEGIN_ALLOW_THREADS;
  try {
    /* Where the platform can tell the size from the picture's header (macOS, Windows, Wayland), a
     * side past the limit is refused before the picture is decoded and held in memory. Elsewhere,
     * and for a picture that changed since, the decoded size is checked again below. */
    if (max_side > 0 && WM_angelo_clipboard_image_size(&width, &height) &&
        angelo_clipboard_side_refused(width, height, max_side))
    {
      found = true;
      result = ClipboardSave::TooWide;
    }
    else if (ImBuf *ibuf = WM_clipboard_image_get()) {
      found = true;
      width = ibuf->x;
      height = ibuf->y;
      result = angelo_clipboard_save(ibuf, filepath, compression, max_side, int64_t(max_bytes));
      IMB_freeImBuf(ibuf);
    }
  }
  catch (...) {
    /* Reading the clipboard ran out of memory: as if there were no picture. */
    found = false;
  }
  Py_END_ALLOW_THREADS;

  if (!found) {
    Py_RETURN_NONE;
  }
  if (result == ClipboardSave::Failed) {
    PyErr_SetString(PyExc_RuntimeError, "The clipboard picture could not be saved");
    return nullptr;
  }
  if (result == ClipboardSave::FailedFileLeft) {
    PyErr_Format(PyExc_RuntimeError,
                 "The clipboard picture could not be saved, and the partly written file could not "
                 "be removed: %s",
                 filepath);
    return nullptr;
  }
  const char *refused = result == ClipboardSave::TooWide  ? "side" :
                        result == ClipboardSave::TooHeavy ? "bytes" :
                                                            "";
  return Py_BuildValue("{s:i,s:i,s:O,s:s}",
                       "width",
                       width,
                       "height",
                       height,
                       "saved",
                       result == ClipboardSave::Saved ? Py_True : Py_False,
                       "refused",
                       refused);
}

static PyMethodDef methods[] = {
    {"image_available",
     clipboard_image_available,
     METH_NOARGS,
     "image_available(): whether the system clipboard holds a picture Blender can read (always "
     "False on X11 and in a background session). Asks the operating system: call it when the "
     "person pastes, never per frame."},
    {"image_save",
     reinterpret_cast<PyCFunction>(reinterpret_cast<void *>(clipboard_image_save)),
     METH_VARARGS | METH_KEYWORDS,
     "image_save(filepath, *, compression=15, max_side=0, max_bytes=0): save the clipboard's "
     "picture to the absolute ``filepath`` as an 8-bit PNG (no alpha channel when every pixel is "
     "opaque) with PNG ``compression`` 0 to 100, once more at the strongest compression from the "
     "same pixels when the file would be over ``max_bytes``. Returns None when the clipboard "
     "holds no picture, else {'width', 'height', 'saved', 'refused'}: ``saved`` is False, with "
     "nothing written, when ``refused`` is 'side' (a side longer than ``max_side``: never "
     "encoded, and never decoded where the system reads its size from a header) or 'bytes' "
     "(over ``max_bytes`` at the strongest compression); 0 is no limit. RuntimeError when the "
     "file cannot be written (a partly written file is removed; the message says when it could "
     "not be)."},
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_bpy._angelo_clipboard",
    nullptr,
    0,
    methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

PyObject *BPY_angelo_clipboard_module()
{
  return PyModule_Create(&module);
}

}  // namespace blender
