# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact host values across the worker connection: preconditions, errors and program evidence.

A precondition is compared with ``==`` (``Controller._precondition`` and the commit fences), and a scene program's is
read as a ``ProgramBinding`` by the approval review. ``encode``/``decode`` keep each value's exact type through JSON,
so a value decoded in Angelo equals another decoded from the same worker exactly when the worker's own values are
equal: tuples stay tuples, a ``ProgramBinding`` stays one, ``FrozenObligations`` keep their bytes, floats round-trip
(JSON writes ``repr``). Anything else is refused (``TypeError``), so an unknown host value never compares as equal.
Pointers and session ids inside a value are the worker's own and are compared only with that worker's.
Standard library only.
"""

import base64

from .refusal_codes import CODES, RefusalError

MAX_DEPTH = 32


def encode(value, depth=0):
    if depth > MAX_DEPTH:
        raise TypeError("The host value nests too deeply")
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if value != value or value in (float("inf"), float("-inf")):
            raise TypeError("A host value is not finite")
        return {"f": repr(value)}
    if type(value) is bytes:
        return {"b": base64.b64encode(value).decode("ascii")}
    from .program_checks import FrozenObligations
    from .program_context import ProgramBinding
    if type(value) is ProgramBinding:
        return {"pb": [encode(item, depth + 1) for item in value]}
    if type(value) is FrozenObligations:
        return {"fo": {name: encode(getattr(value, name), depth + 1) for name in _obligation_fields()}}
    if type(value) is tuple:
        return {"t": [encode(item, depth + 1) for item in value]}
    if type(value) is list:
        return [encode(item, depth + 1) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {"d": {key: encode(item, depth + 1) for key, item in value.items()}}
    raise TypeError("The host value %s cannot cross to Angelo" % type(value).__name__)


def decode(value, depth=0):
    if depth > MAX_DEPTH:
        raise TypeError("The host value nests too deeply")
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is list:
        return [decode(item, depth + 1) for item in value]
    if type(value) is not dict or len(value) != 1:
        raise TypeError("Not an encoded host value")
    (tag, item), = value.items()
    if tag == "f" and type(item) is str:
        return float(item)
    if tag == "b" and type(item) is str:
        return base64.b64decode(item.encode("ascii"), validate=True)
    if tag == "t" and type(item) is list:
        return tuple(decode(part, depth + 1) for part in item)
    if tag == "d" and type(item) is dict:
        return {key: decode(part, depth + 1) for key, part in item.items()}
    if tag == "pb" and type(item) is list:
        from .program_context import ProgramBinding
        return ProgramBinding(*(decode(part, depth + 1) for part in item))
    if tag == "fo" and type(item) is dict and set(item) == set(_obligation_fields()):
        from .program_checks import FrozenObligations
        return FrozenObligations(**{name: decode(part, depth + 1) for name, part in item.items()})
    raise TypeError("Not an encoded host value")


def _obligation_fields():
    from dataclasses import fields
    from .program_checks import FrozenObligations
    return tuple(field.name for field in fields(FrozenObligations))


# Errors -----------------------------------------------------------------------------------------------------------

# Exception types the controller tells apart (``isinstance``); anything else arrives as RuntimeError.
PLAIN_ERRORS = {"ValueError": ValueError, "RuntimeError": RuntimeError, "KeyError": KeyError,
                "TypeError": TypeError, "LookupError": LookupError}


def encode_error(error):
    """What Angelo needs to rebuild a host exception: its kind, words, refusal code, details and certainty."""
    from .refusal_codes import refusal_code, refusal_details
    record = {"type": type(error).__name__, "message": str(error)[:4000], "code": refusal_code(error),
              "details": encode(refusal_details(error)) if refusal_details(error) else None}
    certain = getattr(error, "partial_changes_possible", None)
    if type(certain) is bool:
        record["partial_changes_possible"] = certain
    from .program_jobs import WriterBusyError
    if isinstance(error, WriterBusyError):
        record["type"] = "WriterBusyError"
    return record


def decode_error(record):
    """The exception to raise in Angelo for a worker's ``encode_error`` record."""
    record = record if isinstance(record, dict) else {}
    message = str(record.get("message") or "The worker failed")
    code = record.get("code") if record.get("code") in CODES else "UNKNOWN"
    try:
        details = decode(record.get("details")) if record.get("details") is not None else None
    except (TypeError, ValueError):
        details = None
    if record.get("type") == "WriterBusyError":
        from .program_jobs import WriterBusyError
        try:
            error = WriterBusyError(message)
        except TypeError:
            error = RefusalError(message, code="WRITER_BUSY")
    elif code != "UNKNOWN" or record.get("type") in ("RefusalError", "ProgramError"):
        error = RefusalError(message, code=code, details=details)
    else:
        error = PLAIN_ERRORS.get(record.get("type"), RuntimeError)(message)
    if type(record.get("partial_changes_possible")) is bool:
        error.partial_changes_possible = record["partial_changes_possible"]
    return error


# Program evidence -------------------------------------------------------------------------------------------------

def encode_capture(captured):
    """``program_completion._CapturedProgram`` (the committed job's exact obligations and checks), or None."""
    if captured is None:
        return None
    return {"obligations": encode(captured.obligations), "results_digest": captured.results_digest,
            "status": captured.status}


def decode_capture(record, program):
    """The worker's captured evidence for ``program`` (the program Angelo approved), or None when it does not hold.

    The obligations' own digests are checked against that program here, exactly as ``capture_program`` checks them
    against the job's program in one process, so evidence for another program is never minted into a completion.
    """
    from .program_checks import FrozenObligations
    from .program_completion import _CapturedProgram
    if not isinstance(record, dict) or not isinstance(program, dict):
        return None
    try:
        obligations = decode(record.get("obligations"))
        if type(obligations) is not FrozenObligations:
            return None
        obligations.assert_integrity(program)
        digest, status = record.get("results_digest"), record.get("status")
        if type(digest) is not str or type(status) is not str:
            return None
        return _CapturedProgram(obligations, digest, status)
    except (TypeError, ValueError, KeyError, UnicodeError):
        return None
