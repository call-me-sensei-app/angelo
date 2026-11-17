/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Running an agent script (doc/angelo/script_confinement.rst, "How it works"): `run_confined` digests the text,
 * runs it only when the authority admits that text, compiles it with Angelo's registered compiler (or in C), runs it
 * as a confined region, reads its result back while still confined, and calls the sweeps Angelo registered.
 */

#include <Python.h>

#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "bpy_angelo_guard_authority.hh"
#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_sha256.hh"

namespace blender::angelo_guard {

/* Angelo's own compiler for scripts (`script_prepare.Prepared.compiled`), registered once at install. */
static PyObject *registered_compiler = nullptr;
/* The start and end sweeps (Python callables Angelo registered, held only here). */
static std::vector<std::pair<PyObject *, PyObject *>> sweeps;

static PyObject *run_sweeps_begin()
{
  PyObject *snapshots = PyList_New(0);
  for (const auto &[begin, end] : sweeps) {
    PyObject *snapshot = PyObject_CallNoArgs(begin);
    if (snapshot == nullptr) {
      PyErr_Clear(); /* A snapshot that fails leaves its end nothing to do. */
      snapshot = Py_NewRef(Py_None);
    }
    PyList_Append(snapshots, snapshot);
    Py_DECREF(snapshot);
  }
  return snapshots;
}

static PyObject *run_sweeps_end(PyObject *snapshots)
{
  PyObject *findings = PyList_New(0);
  /* Only the sweeps that took a snapshot (one registered meanwhile, from another thread, has none). */
  const size_t count = std::min(sweeps.size(), size_t(PyList_GET_SIZE(snapshots)));
  for (size_t i = 0; i < count; i++) {
    PyObject *snapshot = PyList_GET_ITEM(snapshots, Py_ssize_t(i));
    if (snapshot == Py_None) {
      continue;
    }
    PyObject *found = PyObject_CallOneArg(sweeps[i].second, snapshot);
    if (found == nullptr) {
      PyErr_Clear(); /* One sweep failing never stops the others. */
      PyObject *text = PyUnicode_FromString("sweep failed");
      PyList_Append(findings, text);
      Py_DECREF(text);
      continue;
    }
    if (PyList_Check(found)) {
      for (Py_ssize_t j = 0; j < PyList_GET_SIZE(found); j++) {
        PyList_Append(findings, PyList_GET_ITEM(found, j));
      }
    }
    Py_DECREF(found);
  }
  return findings;
}

#ifdef ANGELO_GUARD_DIAG_NO_INSTALL
/* A diagnostic build (CMake ANGELO_GUARD_DIAG_NO_INSTALL) installs no confinement, so it runs no agent script at
 * all. The marker in this text is what every packaging step refuses (scripts/angelo/builder_paths.py). */
static const char DIAG_REFUSAL[] =
    "This is a diagnostic build of Angelo without the agent-script confinement "
    "(ANGELO_GUARD_DIAGNOSTIC_BUILD_NOT_FOR_RELEASE): it runs no agent script. Use a release build.";
#endif

PyObject *guard_run_confined(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
#ifdef ANGELO_GUARD_DIAG_NO_INSTALL
  (void)args;
  (void)kwargs;
  PyErr_SetString(PyExc_PermissionError, DIAG_REFUSAL); /* Fail closed: never a script without confinement. */
  return nullptr;
#endif
  static const char *keywords[] = {"source", "filename", "globals", "call_id", "compiler", "finish", nullptr};
  PyObject *source, *filename, *globals, *call_id, *compiler = Py_None, *finish = Py_None;
  if (!PyArg_ParseTupleAndKeywords(args,
                                   kwargs,
                                   "UUO!U|OO:run_confined",
                                   const_cast<char **>(keywords),
                                   &source,
                                   &filename,
                                   &PyDict_Type,
                                   &globals,
                                   &call_id,
                                   &compiler,
                                   &finish))
  {
    return nullptr;
  }
  if (!recording_works()) {
    /* Provenance is what keeps a script's leftovers confined: without it, no script runs (fail closed). */
    refuse(GUARD, "confinement unavailable on this interpreter");
    return nullptr;
  }
  /* Agent code inside agent code (a nested run) is refused: only Angelo runs a script. */
  if (refuse_if_confined("run_confined")) {
    return nullptr;
  }
  Py_ssize_t size = 0;
  const char *text = PyUnicode_AsUTF8AndSize(source, &size);
  const char *file = PyUnicode_AsUTF8(filename);
  const char *call = PyUnicode_AsUTF8(call_id);
  if (text == nullptr || file == nullptr || call == nullptr) {
    return nullptr;
  }
  /* The text runs only as the person (or their standing consent) admitted it: digested here, in C. */
  uint8_t digest[32];
  sha256(text, size_t(size), digest);
  if (!BPY_angelo_guard_authority_admits(call, digest)) {
    PyErr_SetString(PyExc_PermissionError,
                    "Angelo did not approve this script's text: a script runs only as it was approved.");
    return nullptr;
  }
  PyObject *code = nullptr;
  if (compiler != Py_None) {
    /* Angelo's own compiler (the parent-inverse rewrite), as Angelo, before the script starts: only the one
     * registered at install (a bound method of it), so the code that runs is that compiler's of the admitted text. */
    if (registered_compiler == nullptr || !PyMethod_Check(compiler) ||
        PyMethod_GET_FUNCTION(compiler) != registered_compiler)
    {
      PyErr_SetString(PyExc_PermissionError, "run_confined: only Angelo's registered compiler compiles a script");
      return nullptr;
    }
    PyObject *call_args = PyTuple_Pack(1, source);
    PyObject *call_kwargs = call_args ? Py_BuildValue("{sO}", "namespace", globals) : nullptr;
    code = call_kwargs ? PyObject_Call(compiler, call_args, call_kwargs) : nullptr;
    Py_XDECREF(call_args);
    Py_XDECREF(call_kwargs);
    if (code == nullptr) {
      return nullptr;
    }
    if (!PyCode_Check(code) ||
        PyUnicode_Compare(((PyCodeObject *)code)->co_filename, filename) != 0)
    {
      Py_DECREF(code);
      PyErr_SetString(PyExc_TypeError, "run_confined: the compiler must return the script's code object");
      return nullptr;
    }
  }
  else {
    PyCompilerFlags flags = {0, PY_MINOR_VERSION}; /* As `compile(..., dont_inherit=True)`. */
    code = Py_CompileStringExFlags(text, file, Py_file_input, &flags, -1);
    if (code == nullptr) {
      return nullptr;
    }
  }
  armed.store(true);
  journal_watch_loaded(); /* Modules loaded since: their namespaces are journaled too. */
  PyObject *snapshots = run_sweeps_begin(); /* As Angelo, before the script starts. */
  const size_t mark = journal_mark();
  if (!name_is(((PyCodeObject *)code)->co_filename, MARK)) {
    taint(code); /* What runs here is the agent's, whatever file name it was compiled under. */
  }
  (void)mark;
  /* As `exec` does: C code that imports lazily (`time.strptime`, a pybind module importing numpy) reads the
   * globals' `__builtins__` through PyImport_Import, and a namespace without one raises KeyError. */
  if (PyDict_GetItemString(globals, "__builtins__") == nullptr) {
    PyErr_Clear();
    if (PyDict_SetItemString(globals, "__builtins__", PyEval_GetBuiltins()) != 0) {
      return nullptr;
    }
  }
  const RegionMark region = region_enter();
  PyObject *result = PyEval_EvalCode(code, globals, globals);
  Py_DECREF(code);
  /* Still confined: the executor's read-back becomes plain data. The namespace stays (functions the script left
   * keep their globals); what it made, its garbage included, is confined at the region's end. */
  PyObject *finished = nullptr;
  if (result != nullptr && finish != Py_None) {
    finished = PyObject_CallOneArg(finish, globals);
    if (finished == nullptr) {
      Py_CLEAR(result);
    }
  }
  PyObject *raised = PyErr_GetRaisedException();
  /* First the namespaces (builtins included: the sweeps call standard library code), then the sweeps. */
  PyObject *journaled = PyList_New(0);
  pending = false;
  region_leave(region, journaled, globals);
  PyObject *findings = run_sweeps_end(snapshots); /* As Angelo, after the script has left the stack. */
  if (journaled != nullptr && findings != nullptr) {
    PyList_SetSlice(findings, 0, 0, journaled);
  }
  Py_XDECREF(journaled);
  Py_DECREF(snapshots);
  if (raised != nullptr) {
    Py_XDECREF(result);
    Py_XDECREF(findings);
    Py_XDECREF(finished);
    PyErr_SetRaisedException(raised);
    return nullptr;
  }
  Py_XDECREF(result);
  if (finish == Py_None || findings == nullptr) {
    Py_XDECREF(finished);
    return findings;
  }
  PyObject *pair = PyTuple_Pack(2, findings, finished);
  Py_DECREF(findings);
  Py_DECREF(finished);
  return pair;
}

/** `set_compiler(function)`: Angelo's script compiler, once (refused while confined, and once set). */
PyObject *guard_set_compiler(PyObject * /*self*/, PyObject *function)
{
  if (refuse_if_confined("set_compiler")) {
    return nullptr;
  }
  if (registered_compiler != nullptr && registered_compiler != function) {
    refuse(GUARD, "set_compiler: already set");
    return nullptr;
  }
  if (!PyFunction_Check(function)) {
    PyErr_SetString(PyExc_TypeError, "set_compiler: a function");
    return nullptr;
  }
  Py_XSETREF(registered_compiler, Py_NewRef(function));
  Py_RETURN_NONE;
}

PyObject *guard_set_sweep(PyObject * /*self*/, PyObject *args)
{
  PyObject *begin, *end;
  if (!PyArg_ParseTuple(args, "OO:set_sweep", &begin, &end)) {
    return nullptr;
  }
  if (refuse_if_confined("set_sweep")) {
    return nullptr;
  }
  if (!PyCallable_Check(begin) || !PyCallable_Check(end)) {
    PyErr_SetString(PyExc_TypeError, "set_sweep: begin and end must be callable");
    return nullptr;
  }
  sweeps.emplace_back(Py_NewRef(begin), Py_NewRef(end));
  Py_RETURN_NONE;
}

}  // namespace blender::angelo_guard
