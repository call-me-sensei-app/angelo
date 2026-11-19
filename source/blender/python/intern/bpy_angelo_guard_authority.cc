/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * The authority behind Angelo's grants, reached from Python through `_bpy._angelo_guard` and from
 * the window manager through `BPY_angelo_guard_note_input` (doc/angelo/grant_operators.rst,
 * "Authority in C"). The state lives in `bpy_angelo_guard_authority_core` (plain C++); this file
 * is the Python surface and the window manager's door, and decides nothing the core does not.
 *
 * Python methods (none holds state; each reads or changes the core):
 *
 * - `consent_regions(window, surface, regions)`: where `surface` (a canvas or card host) draws its
 *   controls that grant now, `regions` a tuple of `(x, y, width, height, idname, digest, shield)` in
 *   window pixels (`shield`: an approval's control, armed only after the shield and a move into it).
 *   Refused while an agent script runs.
 * - `take(idname, digest)`: takes the ticket an aimed press on that control minted. Single use.
 *   Always False while an agent script runs.
 * - `grant(idname, digest, function, *args, **kwargs)`: takes the ticket, then calls `function`
 *   inside a grant scope. Raises PermissionError without a ticket or while a script runs.
 * - `automatic(function, *args, **kwargs)`: Angelo's own decision (an Auto approval, a worker's
 *   lease from its lead, a chat's client recorded at its start) inside a grant scope. Refused while
 *   an agent script runs.
 * - `granting()`: whether a grant scope is open on this thread and no agent script runs.
 * - `define(namespace, narrow)`, `get(namespace, key, default=None)`, `items(namespace)`,
 *   `set(namespace, key, value)` (True when it changed; PermissionError when it widens outside a
 *   grant scope), `remove(namespace, key)`. Neither, in either direction, while an agent script
 *   runs: a script that narrowed would silently withdraw the person's consents and approvals. Values
 *   are None, bool, int, float or str, exactly (no subclass: a value is read once and is what it
 *   says).
 * - `seal(function, caller)`: `function` as an opaque callable no agent code can call, except from
 *   the code object `caller` (a grant entry's own wrapper, which judges the call first). Agent code
 *   can read a closure; what a grant entry guards is reachable through nothing it can call.
 */

#include <Python.h>

#include <cstring>
#include <algorithm>
#include <iterator>
#include <string>
#include <vector>

#include "BKE_global.hh"
#include "BLI_time.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "bpy_angelo_guard_authority.hh"
#include "bpy_angelo_guard_authority_core.hh"
#include "bpy_angelo_guard_confine.hh"

namespace blender {

using angelo_guard::authority;
using angelo_guard::RegionSpec;
using angelo_guard::Scope;
using angelo_guard::SetResult;
using angelo_guard::Value;

static constexpr const char *SCRIPT_RUNS = "Not while an agent script is running.";
static constexpr const char *NOT_CLICKED = "Only your own click in Angelo can do this.";

static double now()
{
  return BLI_time_now_seconds();
}

static bool confined()
{
  return BPY_angelo_guard_confined();
}

static bool text_of(PyObject *object, std::string &r_text, const char *what)
{
  if (!PyUnicode_CheckExact(object)) {
    PyErr_Format(PyExc_TypeError, "%s must be a str", what);
    return false;
  }
  Py_ssize_t size = 0;
  const char *data = PyUnicode_AsUTF8AndSize(object, &size);
  if (data == nullptr) {
    return false;
  }
  r_text.assign(data, size_t(size));
  return true;
}

/** Exactly None, bool, int, float or str: a subclass could answer differently each time it is read. */
static bool value_of(PyObject *object, Value &r_value)
{
  if (object == Py_None) {
    r_value = Value();
  }
  else if (PyBool_Check(object)) {
    r_value = Value(object == Py_True);
  }
  else if (PyLong_CheckExact(object)) {
    int overflow = 0;
    const long long number = PyLong_AsLongLongAndOverflow(object, &overflow);
    if (overflow != 0 || (number == -1 && PyErr_Occurred())) {
      PyErr_SetString(PyExc_ValueError, "an int in the ledger fits 64 bits");
      return false;
    }
    r_value = Value(int64_t(number));
  }
  else if (PyFloat_CheckExact(object)) {
    r_value = Value(PyFloat_AS_DOUBLE(object));
  }
  else if (PyUnicode_CheckExact(object)) {
    std::string text;
    if (!text_of(object, text, "value")) {
      return false;
    }
    r_value = Value(std::move(text));
  }
  else {
    PyErr_SetString(PyExc_TypeError, "a ledger value is exactly None, bool, int, float or str");
    return false;
  }
  return true;
}

static PyObject *python_of(const Value &value)
{
  if (const bool *flag = std::get_if<bool>(&value)) {
    return PyBool_FromLong(*flag);
  }
  if (const int64_t *number = std::get_if<int64_t>(&value)) {
    return PyLong_FromLongLong(*number);
  }
  if (const double *real = std::get_if<double>(&value)) {
    return PyFloat_FromDouble(*real);
  }
  if (const std::string *text = std::get_if<std::string>(&value)) {
    return PyUnicode_FromStringAndSize(text->data(), Py_ssize_t(text->size()));
  }
  Py_RETURN_NONE;
}

static PyObject *refuse(const char *message)
{
  PyErr_SetString(PyExc_PermissionError, message);
  return nullptr;
}

static PyObject *guard_consent_regions(PyObject * /*self*/, PyObject *args)
{
  unsigned long long window = 0;
  PyObject *surface_object = nullptr, *regions_object = nullptr;
  if (!PyArg_ParseTuple(args, "KUO!:consent_regions", &window, &surface_object, &PyTuple_Type,
                        &regions_object))
  {
    return nullptr;
  }
  if (confined()) {
    return refuse(SCRIPT_RUNS);
  }
  std::string surface;
  if (!text_of(surface_object, surface, "surface")) {
    return nullptr;
  }
  std::vector<RegionSpec> regions;
  const Py_ssize_t count = PyTuple_GET_SIZE(regions_object);
  if (count > angelo_guard::MAX_REGIONS_PER_SURFACE) {
    PyErr_SetString(PyExc_ValueError, "too many consent regions");
    return nullptr;
  }
  for (Py_ssize_t i = 0; i < count; i++) {
    PyObject *item = PyTuple_GET_ITEM(regions_object, i);
    RegionSpec spec;
    PyObject *idname = nullptr, *digest = nullptr;
    int shield = 1;
    if (!PyTuple_CheckExact(item) ||
        !PyArg_ParseTuple(item, "iiiiUUp", &spec.x, &spec.y, &spec.width, &spec.height, &idname,
                          &digest, &shield))
    {
      if (!PyErr_Occurred()) {
        PyErr_SetString(PyExc_TypeError,
                        "a region is (x, y, width, height, idname, digest, shield)");
      }
      return nullptr;
    }
    spec.shield = shield != 0;
    if (!text_of(idname, spec.idname, "idname") || !text_of(digest, spec.digest, "digest")) {
      return nullptr;
    }
    regions.push_back(std::move(spec));
  }
  return PyBool_FromLong(authority().set_regions(uintptr_t(window), surface, regions, now()));
}

static bool two_texts(PyObject *args, const char *format, std::string &a, std::string &b)
{
  PyObject *first = nullptr, *second = nullptr;
  if (!PyArg_ParseTuple(args, format, &first, &second)) {
    return false;
  }
  return text_of(first, a, "first argument") && text_of(second, b, "second argument");
}

static PyObject *guard_take(PyObject * /*self*/, PyObject *args)
{
  std::string idname, digest;
  if (!two_texts(args, "UU:take", idname, digest)) {
    return nullptr;
  }
  if (confined()) {
    Py_RETURN_FALSE;
  }
  return PyBool_FromLong(authority().take(idname, digest, now()));
}

/** `function(*rest, **kwargs)` inside a grant scope of `kind`; `rest` is `args` from `skip` on. */
static PyObject *call_in_scope(PyObject *args,
                               Py_ssize_t skip,
                               PyObject *kwargs,
                               Scope::Kind kind = Scope::AUTOMATIC)
{
  if (PyTuple_GET_SIZE(args) <= skip || !PyCallable_Check(PyTuple_GET_ITEM(args, skip))) {
    PyErr_SetString(PyExc_TypeError, "a callable is required");
    return nullptr;
  }
  PyObject *function = PyTuple_GET_ITEM(args, skip);
  PyObject *rest = PyTuple_GetSlice(args, skip + 1, PyTuple_GET_SIZE(args));
  if (rest == nullptr) {
    return nullptr;
  }
  PyObject *result;
  {
    Scope scope(kind);
    result = PyObject_Call(function, rest, kwargs);
  }
  Py_DECREF(rest);
  return result;
}

static PyObject *guard_grant(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (PyTuple_GET_SIZE(args) < 3) {
    PyErr_SetString(PyExc_TypeError, "grant(idname, digest, function, *args, **kwargs)");
    return nullptr;
  }
  std::string idname, digest;
  if (!text_of(PyTuple_GET_ITEM(args, 0), idname, "idname") ||
      !text_of(PyTuple_GET_ITEM(args, 1), digest, "digest"))
  {
    return nullptr;
  }
  if (confined()) {
    return refuse(SCRIPT_RUNS);
  }
  if (!authority().take(idname, digest, now())) {
    return refuse(NOT_CLICKED);
  }
  return call_in_scope(args, 2, kwargs, Scope::PERSON);
}

/** A test harness standing in for the person: only in an app started with --enable-event-simulate,
 * which a shipped Angelo never is (its own input is then simulated too). */
static PyObject *guard_harness(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (confined()) {
    return refuse(SCRIPT_RUNS);
  }
  if ((G.f & G_FLAG_EVENT_SIMULATE) == 0) {
    return refuse("A harness grant exists only in an app started with --enable-event-simulate.");
  }
  return call_in_scope(args, 0, kwargs, Scope::HARNESS);
}

static PyObject *guard_admitted(PyObject * /*self*/, PyObject *arg)
{
  std::string idname;
  if (!text_of(arg, idname, "idname")) {
    return nullptr;
  }
  return PyBool_FromLong(!confined() && Scope::admits_operator(authority(), idname));
}

static PyObject *guard_is_grant_operator(PyObject * /*self*/, PyObject *arg)
{
  std::string idname;
  if (!text_of(arg, idname, "idname")) {
    return nullptr;
  }
  return PyBool_FromLong(BPY_angelo_guard_is_grant_operator(idname.c_str()));
}

static PyObject *guard_peek(PyObject * /*self*/, PyObject *args)
{
  std::string idname, digest;
  if (!two_texts(args, "UU:peek", idname, digest)) {
    return nullptr;
  }
  return PyBool_FromLong(!confined() && authority().peek(idname, digest, now()));
}

static PyObject *guard_window_state(PyObject * /*self*/, PyObject *arg)
{
  const unsigned long long key = PyLong_AsUnsignedLongLong(arg);
  if (PyErr_Occurred()) {
    return nullptr;
  }
  if (confined()) {
    Py_RETURN_NONE; /* An agent script reads nothing of the person's presses. */
  }
  const angelo_guard::WindowKey window = uintptr_t(key);
  return Py_BuildValue("(KOO)",
                       static_cast<unsigned long long>(authority().epoch(window)),
                       authority().returning(window) ? Py_True : Py_False,
                       authority().press_activating(window) ? Py_True : Py_False);
}

static PyObject *guard_unarmed(PyObject * /*self*/, PyObject *args)
{
  std::string idname, digest;
  if (!two_texts(args, "UU:unarmed", idname, digest)) {
    return nullptr;
  }
  if (confined()) {
    Py_RETURN_NONE; /* An agent script reads nothing of the person's presses. */
  }
  const std::optional<angelo_guard::Authority::Unarmed> press = authority().take_unarmed(
      idname, digest, now());
  if (!press) {
    Py_RETURN_NONE;
  }
  return Py_BuildValue("(Kiiii)",
                       static_cast<unsigned long long>(press->window),
                       press->x,
                       press->y,
                       press->width,
                       press->height);
}

static PyObject *guard_carry(PyObject * /*self*/, PyObject *args)
{
  PyObject *idname = nullptr, *from = nullptr, *to = nullptr;
  int browse = 0;
  if (!PyArg_ParseTuple(args, "UUU|p:carry", &idname, &from, &to, &browse)) {
    return nullptr;
  }
  std::string i, f, t;
  if (!text_of(idname, i, "idname") || !text_of(from, f, "from") || !text_of(to, t, "to")) {
    return nullptr;
  }
  if (confined()) {
    Py_RETURN_FALSE;
  }
  return PyBool_FromLong(authority().carry(i, f, t, now(), browse != 0));
}

/** In the person's scope (a ticketed click), the dialog an operator opens keeps that consent for its
 * confirm: a ticket `to` (naming the operator instance) for `DIALOG_SECONDS` (a file browser,
 * `browse`: `BROWSE_SECONDS`), ended by any agent run. False elsewhere. */
static PyObject *guard_hold_dialog(PyObject * /*self*/, PyObject *args)
{
  PyObject *idname_object = nullptr, *to_object = nullptr;
  int browse = 0;
  if (!PyArg_ParseTuple(args, "UU|p:hold_dialog", &idname_object, &to_object, &browse)) {
    return nullptr;
  }
  std::string idname, to;
  if (!text_of(idname_object, idname, "idname") || !text_of(to_object, to, "to")) {
    return nullptr;
  }
  if (confined() || !Scope::person()) {
    Py_RETURN_FALSE;
  }
  authority().hold_dialog(idname, to, now(), browse != 0);
  Py_RETURN_TRUE;
}

/** In the person's scope, a gesture's next step keeps their consent (a slider's drag): a
 * ticket `to` for `TICKET_SECONDS`, gone on any focus loss or agent run. False elsewhere. */
static PyObject *guard_hold_gesture(PyObject * /*self*/, PyObject *args)
{
  std::string idname, to;
  if (!two_texts(args, "UU:hold_gesture", idname, to)) {
    return nullptr;
  }
  if (confined() || !Scope::person()) {
    Py_RETURN_FALSE;
  }
  authority().hold_gesture(idname, to, now());
  Py_RETURN_TRUE;
}

static PyObject *guard_allow_automatic(PyObject * /*self*/, PyObject *arg)
{
  if (confined()) {
    return refuse(SCRIPT_RUNS);
  }
  if (!PyTuple_CheckExact(arg)) {
    PyErr_SetString(PyExc_TypeError, "allow_automatic takes a tuple of operator idnames");
    return nullptr;
  }
  for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(arg); i++) {
    std::string idname;
    if (!text_of(PyTuple_GET_ITEM(arg, i), idname, "idname")) {
      return nullptr;
    }
    authority().allow_automatic(idname);
  }
  Py_RETURN_NONE;
}

static PyObject *guard_focus(PyObject * /*self*/, PyObject *args)
{
  unsigned long long window = 0;
  PyObject *surface = nullptr, *idname = nullptr, *digest = nullptr;
  int moves = 0;
  if (!PyArg_ParseTuple(args, "KUUU|p:focus", &window, &surface, &idname, &digest, &moves)) {
    return nullptr;
  }
  if (confined()) {
    return refuse(SCRIPT_RUNS);
  }
  std::string s, i, d;
  if (!text_of(surface, s, "surface") || !text_of(idname, i, "idname") ||
      !text_of(digest, d, "digest"))
  {
    return nullptr;
  }
  return PyBool_FromLong(authority().focus(uintptr_t(window), s, i, d, now(), moves != 0));
}

/** A popover or list `surface` the person opened: only in the scope of their own ticketed press or
 * key (a popover code opens has no such scope), never from agent code. */
static PyObject *guard_open_surface(PyObject * /*self*/, PyObject *args)
{
  unsigned long long window = 0;
  PyObject *surface = nullptr;
  if (!PyArg_ParseTuple(args, "KU:open_surface", &window, &surface)) {
    return nullptr;
  }
  std::string s;
  if (!text_of(surface, s, "surface")) {
    return nullptr;
  }
  if (confined() || !Scope::person()) {
    Py_RETURN_FALSE;
  }
  authority().open_surface(uintptr_t(window), s);
  Py_RETURN_TRUE;
}

/** The popover or list closed (narrowing). */
static PyObject *guard_close_surface(PyObject * /*self*/, PyObject *args)
{
  unsigned long long window = 0;
  PyObject *surface = nullptr;
  if (!PyArg_ParseTuple(args, "KU:close_surface", &window, &surface)) {
    return nullptr;
  }
  std::string s;
  if (!text_of(surface, s, "surface")) {
    return nullptr;
  }
  if (confined()) {
    return refuse(SCRIPT_RUNS);
  }
  authority().close_surface(uintptr_t(window), s);
  Py_RETURN_NONE;
}

static PyObject *guard_automatic(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (confined()) {
    return refuse(SCRIPT_RUNS);
  }
  return call_in_scope(args, 0, kwargs);
}

static bool granting()
{
  return Scope::active() && !confined();
}

static PyObject *guard_granting(PyObject * /*self*/, PyObject * /*args*/)
{
  return PyBool_FromLong(granting());
}

static PyObject *guard_agent_changed(PyObject * /*self*/, PyObject * /*args*/)
{
  /* Narrowing only: it ends held consent and vouches for nothing, so any caller may make it. */
  authority().note_agent_run();
  Py_RETURN_NONE;
}

static PyObject *guard_outside_work(PyObject * /*self*/, PyObject * /*args*/)
{
  /* Narrowing only, as agent_changed: it ends held consent and vouches for nothing. */
  authority().note_outside_work();
  Py_RETURN_NONE;
}

static PyObject *guard_define(PyObject * /*self*/, PyObject *args)
{
  PyObject *name_object = nullptr, *narrow_object = nullptr;
  if (!PyArg_ParseTuple(args, "UO:define", &name_object, &narrow_object)) {
    return nullptr;
  }
  if (confined()) {
    return refuse(SCRIPT_RUNS);
  }
  std::string name;
  Value narrow;
  if (!text_of(name_object, name, "namespace") || !value_of(narrow_object, narrow)) {
    return nullptr;
  }
  return PyBool_FromLong(authority().define(name, narrow));
}

static PyObject *guard_get(PyObject * /*self*/, PyObject *args)
{
  PyObject *name_object = nullptr, *key_object = nullptr, *fallback = Py_None;
  if (!PyArg_ParseTuple(args, "UU|O:get", &name_object, &key_object, &fallback)) {
    return nullptr;
  }
  std::string name, key;
  if (!text_of(name_object, name, "namespace") || !text_of(key_object, key, "key")) {
    return nullptr;
  }
  const std::optional<Value> value = authority().get(name, key);
  return value ? python_of(*value) : Py_NewRef(fallback);
}

static PyObject *guard_items(PyObject * /*self*/, PyObject *args)
{
  PyObject *name_object = nullptr;
  if (!PyArg_ParseTuple(args, "U:items", &name_object)) {
    return nullptr;
  }
  std::string name;
  if (!text_of(name_object, name, "namespace")) {
    return nullptr;
  }
  const auto items = authority().items(name);
  PyObject *result = PyTuple_New(Py_ssize_t(items.size()));
  if (result == nullptr) {
    return nullptr;
  }
  for (size_t i = 0; i < items.size(); i++) {
    PyObject *key = PyUnicode_FromStringAndSize(items[i].first.data(),
                                                Py_ssize_t(items[i].first.size()));
    PyObject *value = key ? python_of(items[i].second) : nullptr;
    PyObject *pair = value ? PyTuple_Pack(2, key, value) : nullptr;
    Py_XDECREF(key);
    Py_XDECREF(value);
    if (pair == nullptr) {
      Py_DECREF(result);
      return nullptr;
    }
    PyTuple_SET_ITEM(result, Py_ssize_t(i), pair);
  }
  return result;
}

static PyObject *guard_set(PyObject * /*self*/, PyObject *args)
{
  PyObject *name_object = nullptr, *key_object = nullptr, *value_object = nullptr;
  if (!PyArg_ParseTuple(args, "UUO:set", &name_object, &key_object, &value_object)) {
    return nullptr;
  }
  std::string name, key;
  Value value;
  if (!text_of(name_object, name, "namespace") || !text_of(key_object, key, "key") ||
      !value_of(value_object, value))
  {
    return nullptr;
  }
  if (confined()) {
    return refuse(SCRIPT_RUNS); /* Either direction: narrowing would withdraw the person's consents. */
  }
  switch (authority().set(name, key, value, granting())) {
    case SetResult::Set:
      Py_RETURN_TRUE;
    case SetResult::Unchanged:
      Py_RETURN_FALSE;
    case SetResult::Refused:
      return refuse(confined() ? SCRIPT_RUNS : NOT_CLICKED);
    case SetResult::UnknownNamespace:
      PyErr_Format(PyExc_KeyError, "no ledger namespace %R", name_object);
      return nullptr;
    case SetResult::Full:
      PyErr_SetString(PyExc_ValueError, "the ledger is full or the text too long");
      return nullptr;
  }
  return nullptr;
}

static PyObject *guard_remove(PyObject * /*self*/, PyObject *args)
{
  std::string name, key;
  if (!two_texts(args, "UU:remove", name, key)) {
    return nullptr;
  }
  if (confined()) {
    return refuse(SCRIPT_RUNS); /* Narrowing, yet the person's consents and approvals are theirs to withdraw. */
  }
  return PyBool_FromLong(authority().remove(name, key)); /* Narrowing: open to Angelo's own code. */
}

/* -------------------------------------------------------------------- */
/** \name Sealed callables: what a grant entry guards, unreachable to agent code
 * \{ */

struct Sealed {
  PyObject_HEAD
  PyObject *function;
  PyObject *caller; /* The one code object that may call it while an agent script runs. */
};

static PyObject *sealed_call(PyObject *self, PyObject *args, PyObject *kwargs)
{
  Sealed *sealed = (Sealed *)self;
  if (confined()) {
    PyFrameObject *frame = PyEval_GetFrame(); /* Borrowed: the frame that made this call. */
    PyCodeObject *code = frame ? PyFrame_GetCode(frame) : nullptr;
    const bool allowed = code != nullptr && (PyObject *)code == sealed->caller;
    Py_XDECREF(code);
    if (!allowed) {
      return refuse(SCRIPT_RUNS);
    }
  }
  return PyObject_Call(sealed->function, args, kwargs);
}

static void sealed_dealloc(PyObject *self)
{
  Sealed *sealed = (Sealed *)self;
  Py_XDECREF(sealed->function);
  Py_XDECREF(sealed->caller);
  Py_TYPE(self)->tp_free(self);
}

static PyObject *sealed_repr(PyObject * /*self*/)
{
  return PyUnicode_FromString("<sealed grant entry>");
}

static PyTypeObject sealed_type = {PyVarObject_HEAD_INIT(nullptr, 0)};

static PyObject *guard_seal(PyObject * /*self*/, PyObject *args)
{
  PyObject *function = nullptr, *caller = nullptr;
  if (!PyArg_ParseTuple(args, "OO!:seal", &function, &PyCode_Type, &caller)) {
    return nullptr;
  }
  if (confined()) {
    return refuse(SCRIPT_RUNS);
  }
  if (!PyCallable_Check(function)) {
    PyErr_SetString(PyExc_TypeError, "seal takes a callable");
    return nullptr;
  }
  if (!(sealed_type.tp_flags & Py_TPFLAGS_READY)) {
    sealed_type.tp_name = "_angelo_guard.Sealed";
    sealed_type.tp_basicsize = sizeof(Sealed);
    sealed_type.tp_flags = Py_TPFLAGS_DEFAULT;
    sealed_type.tp_call = sealed_call;
    sealed_type.tp_dealloc = sealed_dealloc;
    sealed_type.tp_repr = sealed_repr;
    sealed_type.tp_doc = "What a grant entry guards: no agent code calls it but the entry's own wrapper.";
    if (PyType_Ready(&sealed_type) != 0) {
      return nullptr;
    }
  }
  Sealed *sealed = PyObject_New(Sealed, &sealed_type);
  if (sealed == nullptr) {
    return nullptr;
  }
  sealed->function = Py_NewRef(function);
  sealed->caller = Py_NewRef(caller);
  return (PyObject *)sealed;
}

/** \} */

static PyMethodDef methods[] = {
    {"consent_regions", guard_consent_regions, METH_VARARGS,
     "consent_regions(window, surface, regions): where a surface draws its controls that grant."},
    {"take", guard_take, METH_VARARGS,
     "take(idname, digest): the ticket an aimed press on that control minted, once."},
    {"grant", (PyCFunction)(void (*)(void))guard_grant, METH_VARARGS | METH_KEYWORDS,
     "grant(idname, digest, function, *args, **kwargs): the function, in a grant scope, for the "
     "person's own click."},
    {"automatic", (PyCFunction)(void (*)(void))guard_automatic, METH_VARARGS | METH_KEYWORDS,
     "automatic(function, *args, **kwargs): Angelo's own decision, in a grant scope."},
    {"granting", guard_granting, METH_NOARGS, "granting(): a grant scope is open on this thread."},
    {"harness", (PyCFunction)(void (*)(void))guard_harness, METH_VARARGS | METH_KEYWORDS,
     "harness(function, *args, **kwargs): a test harness standing in for the person (simulated input only)."},
    {"admitted", guard_admitted, METH_O,
     "admitted(idname): operator idname may grant in this thread's scopes (Auto: only its own list)."},
    {"peek", guard_peek, METH_VARARGS, "peek(idname, digest): a fresh ticket exists (it stays)."},
    {"is_grant_operator", guard_is_grant_operator, METH_O,
     "is_grant_operator(idname): C lists it as a grant (an agent script can't run it or draw its button)."},
    {"hold_gesture", guard_hold_gesture, METH_VARARGS,
     "hold_gesture(idname, to): in the person's scope, a gesture's next step keeps it (seconds)."},
    {"hold_dialog", guard_hold_dialog, METH_VARARGS,
     "hold_dialog(idname, to, browse=False): in the person's scope, the dialog an operator opens "
     "keeps it (ticket to); a file browser (browse) longer; an agent run ends it."},
    {"window_state", guard_window_state, METH_O,
     "window_state(window): (focus epoch, returning, whether the last press brought it forward) as C "
     "holds them for that window's key, or None while an agent script runs. The one record every "
     "surface's arming reads, so two handlers of one press agree."},
    {"unarmed", guard_unarmed, METH_VARARGS,
     "unarmed(idname, digest): the person's press on that control, not yet armed (too soon "
     "after it was shown, or no move onto it since), as (window, x, y, width, height) in window "
     "pixels, or None; taken once. The guard answers it quietly with a hint on the control."},
    {"carry", guard_carry, METH_VARARGS,
     "carry(idname, from, to, browse=False): the press ticket becomes its dialog's (digest to), "
     "for the confirm; a file browser (browse) longer; an agent run ends it."},
    {"agent_changed", guard_agent_changed, METH_NOARGS,
     "agent_changed(): an agent tool or job may change Blender or Angelo now (not a script: its run "
     "notes itself): a dialog's held consent and a gesture's hold end, as an agent script's run ends "
     "them (narrowing)."},
    {"outside_work", guard_outside_work, METH_NOARGS,
     "outside_work(): work from outside the person's input landed (a result, a job's end): a "
     "dialog's held consent and a gesture's hold end; the person's presses and focus stay (narrowing)."},
    {"allow_automatic", guard_allow_automatic, METH_O,
     "allow_automatic(idnames): operators Angelo's own decision may run (add-only)."},
    {"open_surface", guard_open_surface, METH_VARARGS,
     "open_surface(window, surface): a popover or list the person opened, in their own scope only "
     "(False elsewhere); until it closes its focus is theirs and its arrows act."},
    {"close_surface", guard_close_surface, METH_VARARGS,
     "close_surface(window, surface): that popover or list closed (narrowing)."},
    {"focus", guard_focus, METH_VARARGS,
     "focus(window, surface, idname, digest, moves=False): the control with focus now; the "
     "person's only right after their own Tab or press on it. moves: a movement key acts on it "
     "(a slider). Empty idname: none."},
    {"define", guard_define, METH_VARARGS, "define(namespace, narrow): once."},
    {"get", guard_get, METH_VARARGS, "get(namespace, key, default=None)."},
    {"items", guard_items, METH_VARARGS, "items(namespace): ((key, value), ...), by key."},
    {"set", guard_set, METH_VARARGS,
     "set(namespace, key, value): widening needs a grant scope; never from an agent script."},
    {"remove", guard_remove, METH_VARARGS,
     "remove(namespace, key): narrowing, taken from anything but an agent script."},
    {"seal", guard_seal, METH_VARARGS,
     "seal(function, caller): function, callable by agent code only from the code object caller."},
    {nullptr, nullptr, 0, nullptr},
};

void BPY_angelo_guard_authority_add(PyObject *module)
{
  authority().define_standard();
  angelo_guard::KeyTypes keys;
  keys.tab = EVT_TABKEY;
  keys.activate[0] = EVT_RETKEY;
  keys.activate[1] = EVT_SPACEKEY;
  keys.activate[2] = EVT_PADENTER;
  const int moves[6] = {EVT_LEFTARROWKEY, EVT_RIGHTARROWKEY, EVT_UPARROWKEY, EVT_DOWNARROWKEY,
                        EVT_HOMEKEY, EVT_ENDKEY};
  std::copy(std::begin(moves), std::end(moves), std::begin(keys.move));
  authority().set_key_types(keys);
  if (PyModule_AddFunctions(module, methods) != 0) {
    PyErr_Print();
  }
}

void BPY_angelo_guard_note_input(const void *window, int x, int y, int event_type, int event_value)
{
  if (BPY_angelo_guard_thread_confined()) {
    return; /* Input an agent script's own run feeds in never counts. */
  }
  const uintptr_t key = uintptr_t(window);
  if (event_type == MOUSEMOVE) {
    authority().note_move(key, x, y, now());
  }
  else if (event_type == WINDEACTIVATE) {
    authority().note_deactivate(key, now());
  }
  else if (event_type == LEFTMOUSE && event_value == KM_PRESS) {
    authority().note_press(key, x, y, now());
  }
  else if (event_type == LEFTMOUSE && event_value == KM_RELEASE) {
    authority().note_release(key, now()); /* A drag's hold ends at the next move. */
  }
  else if (ISKEYBOARD(event_type) && event_value == KM_PRESS) {
    authority().note_key(key, event_type, now()); /* Tab, or an activation key on a focused control. */
  }
}

void BPY_angelo_guard_note_activate(const void *window)
{
  if (BPY_angelo_guard_thread_confined()) {
    return;
  }
  authority().note_activate(uintptr_t(window), now());
}

void BPY_angelo_guard_agent_ran()
{
  authority().note_agent_run();
}

void BPY_angelo_guard_forget_window(const void *window)
{
  authority().forget_window(uintptr_t(window));
}

void BPY_angelo_guard_note_warp(const void *window, const int x, const int y)
{
  authority().note_warp(uintptr_t(window), x, y, now());
}

bool BPY_angelo_guard_warped_into(
    const void *window, const int x, const int y, const int width, const int height)
{
  return authority().warped_into(uintptr_t(window), x, y, width, height);
}

bool BPY_angelo_guard_moved_here_since(const void *window,
                                       const int x,
                                       const int y,
                                       const double since)
{
  return authority().moved_here_since(uintptr_t(window), x, y, since);
}

double BPY_angelo_guard_arm_seconds()
{
  return angelo_guard::ARM_SECONDS;
}

void BPY_angelo_guard_note_presented(const void *window)
{
  authority().note_presented(uintptr_t(window), now());
}

uint64_t BPY_angelo_guard_drawing_frame(const void *window)
{
  return authority().drawing_frame(uintptr_t(window));
}

uint64_t BPY_angelo_guard_window_epoch(const void *window)
{
  return authority().epoch(uintptr_t(window));
}

double BPY_angelo_guard_shown(const void *window, const uint64_t frame, const double drawn)
{
  return authority().shown(uintptr_t(window), frame, drawn);
}

void BPY_angelo_guard_note_unarmed_button(const void *window,
                                          const char *idname,
                                          const int x,
                                          const int y,
                                          const int width,
                                          const int height)
{
  if (idname == nullptr || BPY_angelo_guard_thread_confined()) {
    return;
  }
  /* An operator's button holds its C idname; the guard asks by the Python one, as for its ticket
   * (`BPY_angelo_guard_note_button`). A property's ("prop.<name>") is already the guard's. */
  char py_idname[OP_MAX_TYPENAME];
  if (strncmp(idname, "prop.", 5) != 0) {
    WM_operator_py_idname(py_idname, idname);
    idname = py_idname;
  }
  authority().note_unarmed_button(uintptr_t(window), idname, x, y, width, height, now());
}

void BPY_angelo_guard_note_button(const char *idname, const bool property, const int event_type)
{
  if (idname == nullptr || event_type != LEFTMOUSE || BPY_angelo_guard_thread_confined()) {
    return;
  }
  if (property) {
    authority().mint(std::string("prop.") + idname, "ui", now());
    return;
  }
  /* A button holds its operator's C idname (ANGELO_OT_trusted_scripts); the guard asks by the
   * Python one (angelo.trusted_scripts), as `is_grant_operator` does. */
  char py_idname[OP_MAX_TYPENAME];
  WM_operator_py_idname(py_idname, idname);
  authority().mint(py_idname, "ui", now());
}

bool BPY_angelo_guard_draw_refuses(const char *idname)
{
  if (idname == nullptr || !BPY_angelo_guard_is_grant_operator(idname)) {
    return false;
  }
  if (BPY_angelo_guard_thread_confined()) {
    return true;
  }
  if (!Py_IsInitialized() || !PyGILState_Check()) {
    return false; /* C's own drawing, with no Python frame: not a script's. */
  }
  return BPY_angelo_guard_confined();
}

bool BPY_angelo_guard_authority_admits(const char *call_id, const uint8_t digest[32])
{
  if (call_id == nullptr || digest == nullptr) {
    return false;
  }
  return authority().admits(call_id, angelo_guard::to_hex(digest));
}

}  // namespace blender
