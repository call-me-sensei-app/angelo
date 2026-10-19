# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A sculpt tool's object and its refusals before an edit: classified, so they say nothing ran."""

from contextlib import contextmanager

from .refusal_codes import RefusalError


class SculptPreflightError(RuntimeError):
    """No mutation began; the controller must not report a partial edit.

    A classified refusal (``refusal_code``: VALIDATION, or STALE_REVISION for a surface that changed since it was
    inspected), so the model corrects it and the request's other edits go on. Unclassified, every one of these read
    as a failure that may have changed the scene (refusal_codes.error_result) and held the request's further edits
    (request_recovery): a one-shot that met one ended with its sculpt undone.
    """
    partial_changes_possible = False

    def __init__(self, message, *, code="VALIDATION"):
        super().__init__(message)
        self.refusal_code = code


def named(window, value):
    """A sculpt tool's object: its name, or an exact scene handle, resolved as angelo_set_transform resolves it
    (blender_tools.transform_object; a stale handle fails with scene_queries' reason). The model names objects by
    handle everywhere else; identifiers._object_or_handle turns a short one into the long one this resolves."""
    from .blender_tools import transform_object
    return transform_object(window, value)


@contextmanager
def before_an_edit(mutation=True):
    """Checks that run before a sculpt edit changes anything. For an edit, whatever they raise says nothing ran: a
    refusal with its own code (a stale or deleted handle) keeps it, a check's refusal (RuntimeError, ValueError) is
    VALIDATION, and any other exception (a fault, not the call's) is INTERNAL_ERROR, named by its type, which
    still holds the request's further edits until someone looks. A read-only tool's failures pass through."""
    try:
        yield
    except SculptPreflightError:
        raise
    except RefusalError as error:
        if mutation:
            error.partial_changes_possible = False
        raise
    except (RuntimeError, ValueError) as error:
        if mutation:
            raise SculptPreflightError(str(error)) from error
        raise
    except Exception as error:
        if mutation:
            raise SculptPreflightError(": ".join(filter(None, (type(error).__name__, str(error)))),
                                       code="INTERNAL_ERROR") from error
        raise
