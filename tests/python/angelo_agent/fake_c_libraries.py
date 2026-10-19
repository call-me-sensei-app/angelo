# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Helpers for fake C libraries that stand in for advapi32 and libsecret in host tests.

A fake library is a Python object whose functions are ``CFunction``s. Bindings
can call it two ways:

* directly, where each function receives the binding's own Python and ctypes
  objects (``byref`` arguments, buffers), which lets a test inspect them after
  the call, for example to check that a secret buffer was zeroed;
* through ``c_abi``, which turns each function into a real C function pointer
  with the binding's declared ``restype``/``argtypes``, so every argument goes
  through ctypes' conversion exactly as in a call into the real library.

The ``out``/``struct``/``address`` helpers let one fake serve both ways.
"""

import ctypes


class CFunction:
    """A fake C function: callable, and accepts ctypes' restype/argtypes like a real one."""

    def __init__(self, function):
        self.function, self.restype, self.argtypes = function, None, None

    def __call__(self, *args):
        return self.function(*args)


def store(reference, value):
    """Point an out-parameter (``T **``) at ``value``: a ``byref()`` argument or a converted C pointer."""
    if hasattr(reference, "_obj"):
        reference._obj.contents = value
    else:
        reference[0] = ctypes.pointer(value)


def set_value(reference, value):
    """Write a scalar out-parameter (``size_t *``)."""
    if hasattr(reference, "_obj"):
        reference._obj.value = value
    else:
        reference[0] = value


def struct(reference):
    """The structure an input pointer refers to (``byref()`` or a converted C pointer)."""
    return reference._obj if hasattr(reference, "_obj") else reference.contents


def address(pointer):
    """The address a pointer argument holds (a ctypes pointer, or an int through the C ABI)."""
    return pointer if isinstance(pointer, int) else ctypes.addressof(pointer.contents)


class CAbiLibrary:
    """``fake``'s functions as real C function pointers with the declared signatures.

    ctypes prints and swallows an exception raised inside a callback, so each
    one is also recorded in ``errors``; tests assert that it stays empty.
    """

    def __init__(self, fake, signatures, symbols=()):
        self._keep, self.errors = [], []
        for name, restype, argtypes in signatures:
            function = self._late(fake, name)
            if restype is ctypes.c_char_p:
                # A callback can't return a char* safely: return a kept buffer's address
                # and let the caller's restype read it as a string.
                function, prototype_restype = self._string_result(function), ctypes.c_void_p
            else:
                prototype_restype = restype
            pointer = ctypes.CFUNCTYPE(prototype_restype, *argtypes)(function)
            self._keep.append(pointer)
            setattr(self, name, pointer)
        for name in symbols:  # Addresses such as g_free, passed through as integers.
            setattr(self, name, getattr(fake, name))

    def _late(self, fake, name):
        def call(*args):
            try:  # Looked up per call, so a test may still replace the fake's function afterwards.
                return getattr(fake, name).function(*args)
            except BaseException as error:
                self.errors.append("%s: %r" % (name, error))
                raise
        return call

    def _string_result(self, function):
        def call(*args):
            value = function(*args)
            if value is None:
                return None
            buffer = ctypes.create_string_buffer(value)
            self._keep.append(buffer)
            return ctypes.addressof(buffer)
        return call
