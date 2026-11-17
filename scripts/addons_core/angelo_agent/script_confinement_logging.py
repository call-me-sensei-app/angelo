# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Logging as an agent script found it (doc/angelo/script_confinement.rst, "Angelo's own I/O").

What Angelo logs reaches the handlers on its own loggers and on the root logger. A handler a script adds there (a
``SocketHandler``, a formatter that copies a record somewhere) would receive Angelo's records after the script ended,
so a sweep (registered with the confinement's ``set_sweep``, run as Angelo before and after every script) puts back
the root logger and Angelo's own loggers exactly: their handlers, level, filters, ``propagate`` and ``disabled``,
each of those handlers' level, filters, formatter and stream, the manager's logger class, and the list of handlers
logging flushes at exit (``logging._handlerList``).
"""


def logging_sweep(package):
    """(begin, end) for Angelo's loggers (``package`` and below) and the root logger."""
    import logging
    _list, _id, _getattr, _setattr, _isinstance = list, id, getattr, setattr, isinstance
    prefix = package + "."
    manager = logging.Logger.manager

    def loggers():
        found = [logging.root]
        for name, item in _list(manager.loggerDict.items()):
            if (name == package or name.startswith(prefix)) and _isinstance(item, logging.Logger):
                found.append(item)
        return found

    def handler_state(handler):
        own = _getattr(handler, "__dict__", {})
        return (handler, own.get("level"), _list(own.get("filters", ())), own.get("formatter"), "stream" in own,
                own.get("stream"))

    def begin():
        return {"loggers": [(logger, _list(logger.handlers), logger.level, _list(logger.filters), logger.propagate,
                             logger.disabled, [handler_state(handler) for handler in logger.handlers])
                            for logger in loggers()],
                "handler_list": _list(logging._handlerList), "logger_class": manager.loggerClass}

    def same(a, b):
        return len(a) == len(b) and all(x is y for x, y in zip(a, b))

    def end(snapshot):
        changed = False
        known = {_id(entry[0]) for entry in snapshot["loggers"]}
        for logger, handlers, level, filters, propagate, disabled, states in snapshot["loggers"]:
            if not same(logger.handlers, handlers):
                logger.handlers[:] = handlers
                changed = True
            if not same(logger.filters, filters):
                logger.filters[:] = filters
                changed = True
            for name, value in (("level", level), ("propagate", propagate), ("disabled", disabled)):
                if _getattr(logger, name) is not value and _getattr(logger, name) != value:
                    _setattr(logger, name, value)
                    changed = True
            for handler, hlevel, hfilters, formatter, has_stream, stream in states:
                own = _getattr(handler, "__dict__", {})
                if own.get("level") != hlevel or own.get("formatter") is not formatter or \
                        not same(own.get("filters", ()), hfilters) or (has_stream and own.get("stream") is not stream):
                    own["level"], own["formatter"] = hlevel, formatter
                    own["filters"] = _list(hfilters)
                    if has_stream:
                        own["stream"] = stream
                    changed = True
        for logger in loggers():   # Angelo's loggers made while the script ran carry no handler it added.
            if _id(logger) not in known and logger.handlers:
                logger.handlers[:] = []
                changed = True
        if not same(logging._handlerList, snapshot["handler_list"]):
            logging._handlerList[:] = snapshot["handler_list"]
            changed = True
        if manager.loggerClass is not snapshot["logger_class"]:
            manager.loggerClass = snapshot["logger_class"]
            changed = True
        return ["logging (Angelo's loggers and the root logger)"] if changed else []

    return begin, end
