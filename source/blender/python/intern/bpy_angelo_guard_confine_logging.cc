/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Logging as each outermost region found it (doc/angelo/script_confinement.rst, "Angelo's own I/O"), for every kind
 * of region: a run and a step of a generator a script left. What Angelo logs reaches the handlers on its own loggers
 * and on the root logger, so a handler a script adds there (a `SocketHandler`, a formatter that copies a record
 * somewhere) would receive Angelo's records after the script ended.
 *
 * - The root logger, Angelo's loggers (`angelo_agent` and below) and those of the libraries its network I/O goes
 *   through (`LIBRARIES`: `urllib3`, `websocket`, `asyncio`, ... and below): each one's own attributes exactly (handlers,
 *   filters, level, parent, propagate, disabled; a list's contents too), and each of the handlers it had: their
 *   level, filters, formatter and stream.
 * - A logger of Angelo's made while the region ran keeps no handler or filter, and its parent is one the logging
 *   manager knows.
 * - The manager's own attributes (logger class, record factory, `disable`) exactly; a replaced entry of the root's or
 *   Angelo's in its logger table goes back; and `logging._handlerList` (what logging flushes at exit).
 * - When anything went back, every logger's level cache is cleared.
 * No Python runs here (plain instance-dict reads and writes): this is a region's end, where nothing is journaled.
 */

#include <Python.h>

#include <cstring>
#include <vector>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"

namespace blender::angelo_guard {

namespace {

const char *const PACKAGE = "angelo_agent";
/* The libraries Angelo's own network I/O goes through: their loggers carry its traffic's records (hosts, paths), so a
 * handler a script put on one is put back as on Angelo's own. */
const char *const LIBRARIES[] = {"urllib3", "websocket", "asyncio", "requests", "httpx", "httpcore", "h11", "h2",
                                 "hpack", "charset_normalizer", "truststore", "certifi", "http.client",
                                 "concurrent.futures", "py.warnings"};

/** An object's own dict as it was, and a copy of each list it held (the list and its contents). */
struct OwnState {
  PyObject *object = nullptr;
  PyObject *dict = nullptr;              /* The object's own dict (the same one at the end, unless replaced). */
  PyObject *copy = nullptr;              /* A copy of it. */
  std::vector<PyObject *> lists, items;  /* Each list value and a copy of its contents. */
};

struct LoggingState {
  bool taken = false;
  std::vector<OwnState> loggers;   /* The root logger and Angelo's. */
  std::vector<OwnState> handlers;  /* Their handlers (only the keys below go back). */
  OwnState manager;
  PyObject *table = nullptr;       /* The manager's `loggerDict`, and the root's and Angelo's entries in it. */
  PyObject *table_entries = nullptr;
  PyObject *handler_list = nullptr; /* `logging._handlerList` and a copy of it. */
  PyObject *handler_list_copy = nullptr;
};

thread_local LoggingState state;

PyObject *own_dict(PyObject *object)
{
  if (object == nullptr || Py_TYPE(object)->tp_dictoffset == 0) {
    return nullptr;
  }
  PyObject *own = PyObject_GenericGetDict(object, nullptr);
  if (own != nullptr && !PyDict_CheckExact(own)) {
    Py_CLEAR(own);
  }
  PyErr_Clear();
  return own; /* New reference. */
}

bool take(PyObject *object, OwnState &out)
{
  out.dict = own_dict(object);
  if (out.dict == nullptr) {
    return false;
  }
  out.object = Py_NewRef(object);
  out.copy = PyDict_Copy(out.dict);
  PyObject *key, *value;
  Py_ssize_t position = 0;
  while (out.copy != nullptr && PyDict_Next(out.copy, &position, &key, &value)) {
    if (PyList_CheckExact(value)) {
      if (PyObject *items = PyList_GetSlice(value, 0, PY_SSIZE_T_MAX)) {
        out.lists.push_back(Py_NewRef(value));
        out.items.push_back(items);
      }
    }
  }
  PyErr_Clear();
  return out.copy != nullptr;
}

void release(OwnState &own)
{
  Py_CLEAR(own.object);
  Py_CLEAR(own.dict);
  Py_CLEAR(own.copy);
  for (PyObject *list : own.lists) {
    Py_DECREF(list);
  }
  for (PyObject *items : own.items) {
    Py_DECREF(items);
  }
  own.lists.clear();
  own.items.clear();
}

bool same_items(PyObject *list, PyObject *items)
{
  if (PyList_GET_SIZE(list) != PyList_GET_SIZE(items)) {
    return false;
  }
  for (Py_ssize_t i = 0; i < PyList_GET_SIZE(items); i++) {
    if (PyList_GET_ITEM(list, i) != PyList_GET_ITEM(items, i)) {
      return false;
    }
  }
  return true;
}

/** Put a list's contents back; whether they differed. */
bool put_items(PyObject *list, PyObject *items)
{
  if (same_items(list, items)) {
    return false;
  }
  PyList_SetSlice(list, 0, PyList_GET_SIZE(list), items);
  PyErr_Clear();
  return true;
}

/** Put the object's own dict back: the keys `only` names (all but `_cache` when null), exactly. */
bool put_back(OwnState &own, const char *const *only)
{
  if (own.object == nullptr) {
    return false;
  }
  bool changed = false;
  PyObject *now = own_dict(own.object);
  if (now != own.dict && now != nullptr) {
    /* The dict itself replaced: its keys go back into the one it had. */
    if (PyObject_GenericSetDict(own.object, own.dict, nullptr) == 0) {
      changed = true;
    }
    PyErr_Clear();
  }
  Py_XDECREF(now);
  auto wanted = [&](PyObject *key) {
    if (!PyUnicode_Check(key)) {
      return only == nullptr;
    }
    const char *text = PyUnicode_AsUTF8(key);
    PyErr_Clear();
    if (text == nullptr) {
      return only == nullptr;
    }
    if (only == nullptr) {
      return strcmp(text, "_cache") != 0;
    }
    for (const char *const *name = only; *name != nullptr; name++) {
      if (strcmp(text, *name) == 0) {
        return true;
      }
    }
    return false;
  };
  PyObject *keys = PyDict_Keys(own.dict);
  for (Py_ssize_t i = 0; keys != nullptr && i < PyList_GET_SIZE(keys); i++) {
    PyObject *key = PyList_GET_ITEM(keys, i);
    if (wanted(key) && PyDict_Contains(own.copy, key) == 0) {
      PyDict_DelItem(own.dict, key); /* Added: gone. */
      changed = true;
    }
  }
  Py_XDECREF(keys);
  PyObject *key, *value;
  Py_ssize_t position = 0;
  while (PyDict_Next(own.copy, &position, &key, &value)) {
    if (!wanted(key)) {
      continue;
    }
    PyObject *current = PyDict_GetItemWithError(own.dict, key);
    if (current != value) {
      PyDict_SetItem(own.dict, key, value);
      changed = true;
    }
  }
  for (size_t i = 0; i < own.lists.size(); i++) {
    /* A list of the wanted keys: its contents too (it is the value again by now). */
    PyObject *found = nullptr;
    position = 0;
    while (PyDict_Next(own.copy, &position, &key, &value)) {
      if (value == own.lists[i] && wanted(key)) {
        found = value;
      }
    }
    if (found != nullptr && put_items(found, own.items[i])) {
      changed = true;
    }
  }
  PyErr_Clear();
  return changed;
}

PyObject *logging_dict()
{
  PyObject *modules = journal_modules();
  PyObject *logging = modules ? PyDict_GetItemString(modules, "logging") : nullptr; /* Borrowed. */
  PyObject *names = logging != nullptr && PyModule_Check(logging) ? PyModule_GetDict(logging) : nullptr;
  PyErr_Clear();
  return names;
}

bool angelo_name(PyObject *name)
{
  if (!PyUnicode_Check(name)) {
    return false;
  }
  const char *text = PyUnicode_AsUTF8(name);
  PyErr_Clear();
  if (text == nullptr) {
    return false;
  }
  auto under = [text](const char *prefix) {
    const size_t length = strlen(prefix);
    return strncmp(text, prefix, length) == 0 && (text[length] == '\0' || text[length] == '.');
  };
  if (under(PACKAGE)) {
    return true;
  }
  for (const char *library : LIBRARIES) {
    if (under(library)) {
      return true;
    }
  }
  return false;
}

/** The logging manager (`Logger.manager`: a class attribute, looked up without running a descriptor). */
PyObject *manager_of(PyObject *names)
{
  PyObject *logger_class = names ? PyDict_GetItemString(names, "Logger") : nullptr;
  if (logger_class == nullptr || !PyType_Check(logger_class)) {
    PyErr_Clear();
    return nullptr;
  }
  PyObject *key = PyUnicode_FromString("manager");
  PyObject *manager = key ? _PyType_Lookup((PyTypeObject *)logger_class, key) : nullptr; /* Borrowed. */
  Py_XDECREF(key);
  PyErr_Clear();
  return manager;
}

PyObject *item_of(PyObject *dict, const char *name)
{
  PyObject *value = dict != nullptr ? PyDict_GetItemString(dict, name) : nullptr;
  PyErr_Clear();
  return value; /* Borrowed. */
}

}  // namespace

void logging_snapshot()
{
  logging_release();
  PyObject *names = logging_dict();
  if (names == nullptr || Py_IsFinalizing()) {
    return;
  }
  state.taken = true;
  PyObject *root = item_of(names, "root");
  std::vector<PyObject *> loggers;
  if (root != nullptr) {
    loggers.push_back(root);
  }
  PyObject *manager = manager_of(names);
  if (manager != nullptr && !take(manager, state.manager)) {
    release(state.manager);
  }
  if (state.manager.copy != nullptr) {
    PyObject *table = item_of(state.manager.dict, "loggerDict");
    if (table != nullptr && PyDict_CheckExact(table)) {
      state.table = Py_NewRef(table);
      state.table_entries = PyDict_New();
      PyObject *key, *value;
      Py_ssize_t position = 0;
      while (state.table_entries != nullptr && PyDict_Next(table, &position, &key, &value)) {
        if (angelo_name(key)) {
          PyDict_SetItem(state.table_entries, key, value);
          loggers.push_back(value);
        }
      }
    }
  }
  for (PyObject *logger : loggers) {
    OwnState own;
    if (!take(logger, own)) {
      release(own);
      continue;
    }
    PyObject *handlers = item_of(own.copy, "handlers");
    for (Py_ssize_t i = 0; handlers != nullptr && PyList_CheckExact(handlers) && i < PyList_GET_SIZE(handlers); i++) {
      OwnState handler;
      if (take(PyList_GET_ITEM(handlers, i), handler)) {
        state.handlers.push_back(handler);
      }
      else {
        release(handler);
      }
    }
    state.loggers.push_back(own);
  }
  PyObject *handler_list = item_of(names, "_handlerList");
  if (handler_list != nullptr && PyList_CheckExact(handler_list)) {
    state.handler_list = Py_NewRef(handler_list);
    state.handler_list_copy = PyList_GetSlice(handler_list, 0, PY_SSIZE_T_MAX);
  }
  PyErr_Clear();
}

void logging_settle(PyObject *findings)
{
  if (!state.taken || Py_IsFinalizing()) {
    return;
  }
  static const char *const HANDLER_KEYS[] = {"level", "filters", "formatter", "stream", nullptr};
  static const char *const MANAGER_KEYS[] = {
      "loggerClass", "logRecordFactory", "disable", "root", "loggerDict", "emittedNoHandlerWarning", nullptr};
  bool changed = put_back(state.manager, MANAGER_KEYS);
  PyObject *table = state.table;
  if (table != nullptr && state.table_entries != nullptr) {
    PyObject *key, *value;
    Py_ssize_t position = 0;
    while (PyDict_Next(state.table_entries, &position, &key, &value)) {
      if (PyDict_GetItemWithError(table, key) != value) {
        PyDict_SetItem(table, key, value); /* A replaced entry of Angelo's goes back. */
        changed = true;
      }
    }
    PyErr_Clear();
  }
  for (OwnState &own : state.loggers) {
    changed = put_back(own, nullptr) || changed;
  }
  for (OwnState &own : state.handlers) {
    changed = put_back(own, HANDLER_KEYS) || changed;
  }
  /* Angelo's loggers made while the region ran: no handler or filter, and a parent the manager knows. */
  PyObject *names = logging_dict();
  PyObject *root = item_of(names, "root");
  if (table != nullptr) {
    PyObject *key, *value;
    Py_ssize_t position = 0;
    while (PyDict_Next(table, &position, &key, &value)) {
      if (!angelo_name(key) ||
          (state.table_entries != nullptr && PyDict_GetItemWithError(state.table_entries, key) == value))
      {
        continue;
      }
      PyObject *own = own_dict(value);
      if (own == nullptr) {
        continue;
      }
      for (const char *list_name : {"handlers", "filters"}) {
        PyObject *list = item_of(own, list_name);
        if (list != nullptr && PyList_CheckExact(list) && PyList_GET_SIZE(list) > 0) {
          PyList_SetSlice(list, 0, PyList_GET_SIZE(list), nullptr);
          changed = true;
        }
        else if (list != nullptr && !PyList_CheckExact(list)) {
          if (PyObject *empty = PyList_New(0)) {
            PyDict_SetItemString(own, list_name, empty);
            Py_DECREF(empty);
            changed = true;
          }
        }
      }
      PyObject *parent = item_of(own, "parent");
      bool known = parent == nullptr || parent == Py_None || parent == root;
      PyObject *entry_key, *entry;
      Py_ssize_t at = 0;
      while (!known && PyDict_Next(table, &at, &entry_key, &entry)) {
        known = entry == parent;
      }
      if (!known && root != nullptr) {
        PyDict_SetItemString(own, "parent", root);
        changed = true;
      }
      Py_DECREF(own);
    }
    PyErr_Clear();
  }
  if (state.handler_list != nullptr && state.handler_list_copy != nullptr &&
      put_items(state.handler_list, state.handler_list_copy))
  {
    changed = true;
  }
  if (changed) {
    /* Every logger's level cache: it answered for what was there. */
    PyObject *key, *value;
    Py_ssize_t position = 0;
    std::vector<PyObject *> all;
    if (root != nullptr) {
      all.push_back(root);
    }
    while (table != nullptr && PyDict_Next(table, &position, &key, &value)) {
      all.push_back(value);
    }
    for (PyObject *logger : all) {
      PyObject *own = own_dict(logger);
      PyObject *cache = item_of(own, "_cache");
      if (cache != nullptr && PyDict_CheckExact(cache)) {
        PyDict_Clear(cache);
      }
      Py_XDECREF(own);
    }
    if (findings != nullptr) {
      if (PyObject *line = PyUnicode_FromString("logging (Angelo's loggers and the root logger)")) {
        if (PySequence_Contains(findings, line) == 0) {
          PyList_Append(findings, line);
        }
        Py_DECREF(line);
      }
    }
  }
  PyErr_Clear();
}

void logging_release()
{
  for (OwnState &own : state.loggers) {
    release(own);
  }
  for (OwnState &own : state.handlers) {
    release(own);
  }
  state.loggers.clear();
  state.handlers.clear();
  release(state.manager);
  Py_CLEAR(state.table);
  Py_CLEAR(state.table_entries);
  Py_CLEAR(state.handler_list);
  Py_CLEAR(state.handler_list_copy);
  state.taken = false;
}

}  // namespace blender::angelo_guard
