/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * What confined threads allocate (doc/angelo/script_confinement.rst, "Work that outlives the script"): the object
 * allocator is wrapped, and while a thread runs confined every block it allocates is recorded.
 * So "the script made it" is a fact C holds, never a file name or a frame the script controls.
 */

#include <Python.h>

#include <initializer_list>
#include <unordered_map>
#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_record.hh"

namespace blender::angelo_guard {

/* -------------------------------------------------------------------- */
/** \name What confined threads allocate (the GIL serializes the object allocator)
 *
 * One append per block a confined thread allocates, nothing on free: an allocation-heavy script pays a few
 * nanoseconds per object. At a region's end the record is matched against the young objects (`settle_made`), the
 * only ones a region can have made. A block a confined thread freed and another thread reused in the same region
 * would count as the script's (rare; it only makes more confined). Past `LIMIT` blocks a region stops recording and
 * every young object counts as the script's (fail closed).
 * \{ */

static std::vector<uintptr_t> &recorded()
{
  static std::vector<uintptr_t> blocks;
  return blocks;
}
static constexpr size_t LIMIT = size_t(1) << 26;
static bool overflowed = false;
static PointerSet settled_set;
static PointerSet scripts_set; /* Of the settled, what the script's own code made. */
static bool settled = false;
static PyMemAllocatorEx inner = {};
static bool recording_ready = false;
/** Confined regions running now, on any thread (the GIL; read without it by a USD resolver thread). */
std::atomic<int> active_regions{0};

/** The smallest block of any object the confinement acts on: a cell (a GC header and a reference: 40 bytes on
 * 64-bit); functions, methods, partials, classes, weak references and instances are larger. Smaller blocks (an int,
 * a float) are never one, so they are not recorded: an allocation-heavy script pays almost nothing. */
static constexpr size_t SMALLEST = 2 * sizeof(uintptr_t) + sizeof(PyObject) + sizeof(PyObject *);

/* -------------------------------------------------------------------- */
/** \name Who made a block: the script's code, or installed code (on the script's behalf or not)
 *
 * With each block, one bit (blocks are aligned, so the pointer's lowest bit is free): whether the innermost Python
 * code running when it was allocated was the script's. Read from the thread's frame chain without making a frame
 * object (its layout is checked once at install; unreadable, every block counts as the script's). The verdict for a
 * code object is cached per thread, holding the code so its address can't be reused, until the region ends.
 * \{ */

struct FrameHead { /* The start of CPython 3.13's `_PyInterpreterFrame`. */
  PyObject *executable;
  FrameHead *previous;
};
static bool frames_readable = false;

static PyCodeObject *running_code()
{
  PyThreadState *state = PyThreadState_GetUnchecked();
  const FrameHead *frame = state ? (const FrameHead *)state->current_frame : nullptr;
  for (int hops = 0; frame != nullptr && hops < 8; hops++, frame = frame->previous) {
    if (frame->executable != nullptr && PyCode_Check(frame->executable)) {
      return (PyCodeObject *)frame->executable;
    }
  }
  return nullptr;
}

struct Verdict {
  PyObject *code;
  unsigned generation;
  bool agent;
};
static thread_local Verdict verdicts[8] = {};
static thread_local unsigned next_verdict = 0;
static thread_local bool judging = false;
static unsigned generation = 1;
static std::vector<PyObject *> held_codes; /* Released when the last region ends (never inside the allocator). */

void verdicts_forget()
{
  generation++;
}

static bool script_running()
{
  if (!frames_readable || judging) {
    return true; /* Fail closed. */
  }
  PyCodeObject *code = running_code();
  if (code == nullptr) {
    return true;
  }
  for (const Verdict &verdict : verdicts) {
    if (verdict.code == (PyObject *)code && verdict.generation == generation) {
      return verdict.agent;
    }
  }
  judging = true; /* agent_code may allocate: those blocks count as the script's. */
  const bool agent = agent_code(code);
  judging = false;
  held_codes.push_back(Py_NewRef((PyObject *)code));
  verdicts[next_verdict++ % 8] = {(PyObject *)code, generation, agent};
  return agent;
}

/** \} */

static inline void note(void *pointer)
{
  std::vector<uintptr_t> &blocks = recorded();
  if (blocks.capacity() == 0) {
    blocks.reserve(size_t(1) << 16);
  }
  if (blocks.size() < LIMIT) {
    blocks.push_back(uintptr_t(pointer) | (script_running() ? 1 : 0));
  }
  else {
    overflowed = true;
  }
}

static void *record_malloc(void * /*ctx*/, size_t size)
{
  void *pointer = inner.malloc(inner.ctx, size);
  if (size >= SMALLEST && pointer != nullptr && depth > 0) {
    note(pointer);
  }
  return pointer;
}

static void *record_calloc(void * /*ctx*/, size_t count, size_t size)
{
  void *pointer = inner.calloc(inner.ctx, count, size);
  if (count * size >= SMALLEST && pointer != nullptr && depth > 0) {
    note(pointer);
  }
  return pointer;
}

static void *record_realloc(void * /*ctx*/, void *pointer, size_t size)
{
  void *moved = inner.realloc(inner.ctx, pointer, size);
  if (size >= SMALLEST && moved != nullptr && moved != pointer && depth > 0) {
    note(moved);
  }
  return moved;
}

static void record_free(void * /*ctx*/, void *pointer)
{
  inner.free(inner.ctx, pointer);
}

/** Where `object`'s memory block starts: before its GC header and, for types that keep them there, its managed
 * dict and weak reference list (as `_PyType_PreHeaderSize`; checked by `recording_install`). */
static const void *block_of(PyObject *object)
{
  PyTypeObject *type = Py_TYPE(object);
  size_t before = 0;
  if (PyType_IS_GC(type)) {
    before += 2 * sizeof(uintptr_t);
  }
  if (type->tp_flags & (Py_TPFLAGS_MANAGED_DICT | Py_TPFLAGS_MANAGED_WEAKREF)) {
    before += 2 * sizeof(PyObject *);
  }
  return (const char *)object - before;
}

bool recorded_any()
{
  return overflowed || !recorded().empty();
}

void settle_made(PyObject *young)
{
  settled_set.clear();
  scripts_set.clear();
  PointerSet blocks;
  for (Py_ssize_t i = 0; young != nullptr && i < PyList_GET_SIZE(young); i++) {
    blocks.insert(block_of(PyList_GET_ITEM(young, i)));
  }
  if (overflowed) {
    for (Py_ssize_t i = 0; young != nullptr && i < PyList_GET_SIZE(young); i++) {
      settled_set.insert(block_of(PyList_GET_ITEM(young, i)));
      scripts_set.insert(block_of(PyList_GET_ITEM(young, i))); /* Fail closed: all the script's. */
    }
  }
  else {
    /* The record is in allocation order, and a block is allocated again only after it was freed: the live object
     * at a block is its latest allocation, so that one says who made it (an earlier object of the script's that
     * was freed there says nothing about the one living there now). */
    std::unordered_map<const void *, bool> latest;
    for (const uintptr_t entry : recorded()) {
      const void *block = (const void *)(entry & ~uintptr_t(1));
      if (blocks.contains(block)) {
        latest[block] = (entry & 1) != 0;
      }
    }
    for (const auto &[block, by_script] : latest) {
      settled_set.insert(block);
      if (by_script) {
        scripts_set.insert(block);
      }
    }
  }
  settled = true;
}

void unsettle_made()
{
  settled = false;
}

void recording_reset()
{
  std::vector<uintptr_t> &blocks = recorded();
  if (blocks.capacity() > (size_t(1) << 22)) {
    std::vector<uintptr_t>().swap(blocks); /* Give a big region's memory back (32 MB and more). */
  }
  else {
    blocks.clear();
  }
  overflowed = false;
  settled_set.clear();
  scripts_set.clear();
  settled = false;
  generation++;
  std::vector<PyObject *> codes;
  codes.swap(held_codes);
  for (PyObject *code : codes) {
    Py_DECREF(code);
  }
}

bool made_in_region(PyObject *object)
{
  return settled && settled_set.contains(block_of(object));
}

bool made_by_script(PyObject *object)
{
  return settled && scripts_set.contains(block_of(object));
}

bool recording_install()
{
  if (recording_ready) {
    return true;
  }
  PyMem_GetAllocator(PYMEM_DOMAIN_OBJ, &inner);
  PyMemAllocatorEx wrapped = {nullptr, record_malloc, record_calloc, record_realloc, record_free};
  PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &wrapped);
  /* Prove the block arithmetic on this interpreter: a cell (GC), an instance with a managed dict, a class. (Lists,
   * tuples and dicts may come from a free list: never allocated, so never recorded. Callables, classes and
   * instances, what a script leaves to run, have none.) */
  recording_reset();
  depth++;
  PyObject *cell = PyCell_New(nullptr);
  PyObject *names = PyDict_New();
  PyObject *kind = names ? PyObject_CallFunction((PyObject *)&PyType_Type, "s()O", "_Probe", names) : nullptr;
  PyObject *instance = kind ? PyObject_CallNoArgs(kind) : nullptr;
  depth--;
  PyObject *probes = cell && instance ? PyList_New(0) : nullptr;
  for (PyObject *probe : {cell, instance, kind}) {
    if (probes != nullptr) {
      PyList_Append(probes, probe);
    }
  }
  settle_made(probes);
  const bool proven = probes && made_in_region(cell) && made_in_region(instance) && made_in_region(kind);
  Py_XDECREF(probes);
  Py_XDECREF(instance);
  Py_XDECREF(kind);
  Py_XDECREF(names);
  Py_XDECREF(cell);
  PyErr_Clear();
  recording_reset();
  /* The frame chain is readable when its innermost code is the one CPython reports (called from Python code). */
  PyFrameObject *frame = PyEval_GetFrame(); /* Borrowed. */
  PyCodeObject *reported = frame ? PyFrame_GetCode(frame) : nullptr;
  frames_readable = reported != nullptr && running_code() == reported;
  Py_XDECREF(reported);
  recording_ready = proven;
  return proven;
}

bool recording_works()
{
  return recording_ready;
}

/** \} */

}  // namespace blender::angelo_guard
