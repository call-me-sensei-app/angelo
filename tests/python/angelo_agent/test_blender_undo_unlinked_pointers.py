# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Memfile Undo's in-place swap never remaps the ID pointers lib-linking still has to resolve.

Deterministic regression test for a World lost on Redo (doc/angelo/agent_execution.rst,
"Undo and Redo of a replaced World"). On Undo or Redo, Blender reads an ID that changed into
a temporary ID and swaps it into the address of its previous version
(``read_libblock_undo_restore_at_old_address`` in ``readfile.cc``), with a self-remap that
fixes the pointers to the swapped IDs and their embedded IDs. At that point the read ID's
other ID pointers still hold the addresses their targets had when the Undo step was written.
When such an address had been freed and re-used for the read ID's own embedded ID (its master
collection), the self-remap rewrote it, ``lib_link`` then found no ID there and cleared it:
``scene.world`` was None after Redo although the World was read back.

The real trigger is an allocator coincidence, so this test builds it on purpose: a scene
whose ``world`` pointer holds the address of its own master collection, as a scene read
from an Undo step in that state does, swapped with ``BKE_lib_id_swap_full`` and the flags
``readfile.cc`` passes (the host test ``test_native_undo_unlinked_pointers.py`` pins that
set). The unresolved pointer must survive the swap, and the pointers the swap exists for
(the embedded master collection and its owner pointer) must still be remapped.

``BKE_lib_id_swap_full`` is called through ``ctypes``, which needs the executable's exported
symbols: macOS only (Linux hides them with a version script, Windows exports
none). Run with the exact Angelo binary: ``--background --factory-startup
--disable-autoexec --python-exit-code 1``. No provider, account, network or paid call.
"""

import ctypes
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-undo-unlinked-pointers"))
OUT.mkdir(parents=True, exist_ok=True)

# The remap flags read_libblock_undo_restore_at_old_address passes (BKE_lib_remap.hh values).
READFILE_SWAP_FLAGS = {
    "ID_REMAP_NO_ORIG_POINTERS_ACCESS": 1 << 20,
    "ID_REMAP_SKIP_NEVER_NULL_USAGE": 1 << 1,
    "ID_REMAP_SKIP_UPDATE_TAGGING": 1 << 19,
    "ID_REMAP_SKIP_USER_REFCOUNT": 1 << 18,
    "ID_REMAP_SKIP_USER_CLEAR": 1 << 16,
    "ID_REMAP_SKIP_LIB_LINKED_POINTERS": 1 << 21,
}
# void blender::BKE_lib_id_swap_full(Main *, ID *, ID *, bool do_self_remap, int self_remap_flags)
SWAP_SYMBOL = "_ZN7blender20BKE_lib_id_swap_fullEPNS_4MainEPNS_2IDES3_bi"
POINTER = 8
report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def read_pointer(address):
    return ctypes.c_uint64.from_address(address).value


def write_pointer(address, value):
    ctypes.c_uint64.from_address(address).value = value


def field_offset(block, value, size):
    """The single offset in the first ``size`` bytes of ``block`` that holds pointer ``value``."""
    data = ctypes.string_at(block, size)
    needle = value.to_bytes(POINTER, "little")
    found = [offset for offset in range(0, size - POINTER + 1, POINTER) if data[offset:offset + POINTER] == needle]
    if len(found) != 1:
        raise AssertionError("expected one field holding %#x, found %r" % (value, found))
    return found[0]


def swap(function, id_a, id_b):
    flags = 0
    for value in READFILE_SWAP_FLAGS.values():
        flags |= value
    function(None, ctypes.c_void_p(id_a), ctypes.c_void_p(id_b), True, flags)


def run():
    swap_full = getattr(ctypes.CDLL(None), SWAP_SYMBOL)
    swap_full.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool, ctypes.c_int)
    swap_full.restype = None
    world = bpy.data.worlds.new("Swap World")
    read = bpy.data.scenes.new("Swap read")    # Stands for the temporary ID the Undo step was read into.
    live = bpy.data.scenes.new("Swap live")    # Stands for the previous version, kept at its address.
    read.world = world
    read_address, live_address = read.as_pointer(), live.as_pointer()
    read_collection, live_collection = read.collection.as_pointer(), live.collection.as_pointer()
    # Fields are found by their values and read raw: after a swap, Python's cached wrappers of the
    # swapped embedded IDs (ID.py_instance travels with the data) name the other address.
    offset = field_offset(read_address, world.as_pointer(), 2048)
    collection_offset = field_offset(live_address, live_collection, 6400)  # Scene.master_collection.
    owner_offset = field_offset(live_collection, live_address, 512)  # The master collection's owner_id.
    report.update(world_offset=offset, collection_offset=collection_offset, owner_offset=owner_offset)
    # The not-yet-linked value: the World's old address, re-used by the read scene's master collection.
    write_pointer(read_address + offset, read_collection)
    swapped = False
    try:
        swap(swap_full, read_address, live_address)
        swapped = True
        # The read data now lives at the previous version's address; its master collection
        # pointer keeps that address's embedded ID (counter-swapped), which holds the read data.
        unresolved = read_pointer(live_address + offset)
        report["after_swap"] = {"world_field": hex(unresolved), "read_collection": hex(read_collection),
                                "live_collection": hex(live_collection)}
        check("unlinked_pointer_survives_the_swap", unresolved == read_collection)
        check("unlinked_pointer_is_not_rewritten_to_the_counter_swapped_embedded_id", unresolved != live_collection)
        check("embedded_id_pointer_is_counter_swapped",
              read_pointer(live_address + collection_offset) == live_collection)
        # Its owner pointer came with the read data (the read scene) and must name its new owner.
        check("embedded_owner_pointer_is_remapped_to_its_new_owner",
              read_pointer(live_collection + owner_offset) == live_address)
    finally:
        if swapped:
            swap(swap_full, read_address, live_address)  # Symmetric: restores both IDs' data.
        write_pointer(read_address + offset, world.as_pointer())
    check("swap_back_restores_the_read_scene", read.world == world and read.collection.as_pointer() == read_collection
          and read.name == "Swap read" and live.name == "Swap live")
    bpy.data.scenes.remove(read)
    bpy.data.scenes.remove(live)
    bpy.data.worlds.remove(world)
    report["success"] = True


try:
    run()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    (OUT / "undo-unlinked-pointers.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_UNDO_UNLINKED_POINTERS", json.dumps(report), flush=True)
    if not report.get("success"):
        sys.exit(1)
