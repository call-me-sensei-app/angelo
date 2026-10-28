# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native adaptive files and private Main ownership, in a disposable process."""

import gc
from array import array
import json
import os
from pathlib import Path
import sys
import threading
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from _bpy import _angelo_stage

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, program_storage as storage, scene_queries as queries
from angelo_agent.program_jobs import PREVIEWS
from angelo_agent.request_recovery import RecoveryBarrier
from program_qa_helpers import args_for, move, no_stages, spent_budget, FullStore
from program_teardown_qa import FailingBatchData

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "binary": bpy.app.binary_path, "success": False}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("Program did not settle")


def write_failure_roundtrips(mesh, plain_size, compressed_size, expected):
    """Actual short writes and finalization errors, without filling a filesystem."""
    if os.name != 'posix':
        report.setdefault('skipped', {})['file_size_limit'] = 'RLIMIT_FSIZE requires POSIX'
        return
    import resource
    import signal
    old_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
    old_signal = signal.getsignal(signal.SIGXFSZ)
    # The child process's stdout/stderr also go to a regular file. Keep its log
    # below both limits; no diagnostic output may be lost to this fault injection.
    failure_limit = compressed_size // 2
    assert Path(os.environ['ANGELO_QA_LOG']).stat().st_size + 8192 < failure_limit
    success_path, failure_path = OUT / 'quota-retry.blend', OUT / 'quota-preserved.blend'
    original = b'Existing destination must survive a failed temporary write.'
    failure_path.write_bytes(original)
    try:
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, ((plain_size + compressed_size) // 2, old_limit[1]))
        bpy.data.libraries.write(str(success_path), {mesh}, compress=True,
                                 _compression_min_size=16 * 1024 * 1024)
        resource.setrlimit(resource.RLIMIT_FSIZE, (failure_limit, old_limit[1]))
        try:
            bpy.data.libraries.write(str(failure_path), {mesh}, compress=True,
                                     _compression_min_size=16 * 1024 * 1024)
        except OSError:
            failed = True
        else:
            failed = False
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, old_limit)
        signal.signal(signal.SIGXFSZ, old_signal)
    check('real_short_write_retries_compressed', not success_path.read_bytes().startswith(b'BLENDER'))
    with bpy.data.libraries.load(str(success_path)) as (source, destination):
        destination.meshes = source.meshes
    loaded = destination.meshes[0]
    check('real_compressed_retry_preserves_geometry', [tuple(vertex.co) for vertex in loaded.vertices] == expected)
    bpy.data.meshes.remove(loaded)
    check('real_compressed_write_failure_is_reported', failed)
    check('failed_write_preserves_existing_destination', failure_path.read_bytes() == original)
    check('failed_write_removes_partial_temporary_file', not Path(str(failure_path) + '@').exists())


def file_roundtrips():
    mesh = bpy.data.meshes.new("Compression fixture")
    mesh.from_pydata([(i * .25, i % 11, 0) for i in range(20000)], [], [])
    expected = [tuple(vertex.co) for vertex in mesh.vertices]
    plain = OUT / "plain.blend"
    bpy.data.libraries.write(str(plain), {mesh}, compress=False)
    size = plain.stat().st_size
    for label, compress, threshold, is_plain in (
            ("default_compressed", True, 0, False),
            ("just_below", True, size - 1, False),
            ("exact_boundary", True, size, True),
            ("just_above", True, size + 1, True),
            ("explicit_plain", False, 1, True)):
        path = OUT / (label + ".blend")
        bpy.data.libraries.write(str(path), {mesh}, compress=compress, _compression_min_size=threshold)
        check(label + "_format", path.read_bytes().startswith(b"BLENDER") == is_plain)
        with bpy.data.libraries.load(str(path)) as (source, destination):
            destination.meshes = source.meshes
        loaded = destination.meshes[0]
        check(label + "_exact_geometry", [tuple(vertex.co) for vertex in loaded.vertices] == expected)
        bpy.data.meshes.remove(loaded)
    for invalid in (-1, 64 * 1024 * 1024 + 1):
        try:
            bpy.data.libraries.write(str(OUT / "invalid.blend"), {mesh}, compress=True,
                                     _compression_min_size=invalid)
        except ValueError:
            check("invalid_threshold_%d" % invalid, not (OUT / "invalid.blend").exists())
        else:
            raise AssertionError("Unbounded compression buffer admitted")
    write_failure_roundtrips(mesh, size, (OUT / 'default_compressed.blend').stat().st_size, expected)
    bpy.data.meshes.remove(mesh)
    # Exercise prefix replay plus later compressed frames through the actual
    # seekable zstd reader, with a file larger than the product's 16 MiB bound.
    mesh = bpy.data.meshes.new("Multi-frame compression fixture")
    mesh.vertices.add(750000)
    coordinates = array("f", (float((index % 127) - 63) * .125 for index in range(2250000)))
    mesh.vertices.foreach_set("co", coordinates)
    attribute = mesh.attributes.new("Retained vector", "FLOAT_VECTOR", "POINT")
    attribute.data.foreach_set("vector", coordinates)
    path = OUT / "multiple-frames.blend"
    bpy.data.libraries.write(str(path), {mesh}, compress=True, _compression_min_size=16 * 1024 * 1024)
    check("large_bundle_uses_zstd", not path.read_bytes().startswith(b"BLENDER"))
    with bpy.data.libraries.load(str(path)) as (source, destination):
        destination.meshes = source.meshes
    loaded = destination.meshes[0]
    values = array("f", [0]) * len(coordinates)
    loaded.vertices.foreach_get("co", values)
    check("multi_frame_coordinates_exact", values == coordinates)
    loaded.attributes["Retained vector"].data.foreach_get("vector", values)
    check("multi_frame_attributes_exact", values == coordinates)
    bpy.data.meshes.remove(loaded)
    bpy.data.meshes.remove(mesh)


def detached_graph():
    stage = bpy.data.scenes.new("__angelo_stage_storage_test")
    stage["_angelo_program_stage"] = True
    collection = bpy.data.collections.new("Private collection")
    stage.collection.children.link(collection)
    mesh = bpy.data.meshes.new("Private mesh")
    mesh.from_pydata([(1, 2, 3), (4, 5, 6), (7, 8, 9)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("Private object", mesh)
    collection.objects.link(obj)
    material = bpy.data.materials.new("Private material")
    mesh.materials.append(material)
    stage.view_layers[0].update()
    return stage, obj, mesh, [stage, collection, obj, mesh, material]


def native_ownership():
    original = set(native._main_ids())
    stage, obj, mesh, ids = detached_graph()
    for label, bad in (("none", None), ("scalar", 5), ("non_id_rna", bpy.context.scene.render)):
        try:
            _angelo_stage.detach(stage, [bad], bpy.context.scene)
        except TypeError:
            check("invalid_native_id_" + label, True)
        else:
            raise AssertionError("Invalid ID admitted")
    identities = [(block.as_pointer(), block.session_uid, block.users) for block in ids]
    for i in range(20):
        owner = _angelo_stage.detach(stage, ids, bpy.context.scene)
        check("closed_graph_detaches_%d" % i, owner is not None and set(native._main_ids()) == original)
        _angelo_stage.attach(owner)
        stage.view_layers[0].update()
        check("closed_graph_restores_exact_identity_%d" % i,
              [(block.as_pointer(), block.session_uid, block.users) for block in ids] == identities and
              tuple(mesh.vertices[0].co) == (1, 2, 3))
        try:
            _angelo_stage.attach(owner)
        except RuntimeError:
            check("ownership_consumed_once_%d" % i, True)
        else:
            raise AssertionError("Attached one owner twice")
        _angelo_stage.discard(owner)  # Empty owner may never free the attached IDs.
        check("consumed_owner_does_not_free_main_%d" % i, obj in stage.objects[:])
    outsider = bpy.data.objects.new("Artist reference", mesh)
    check("incoming_artist_reference_falls_back", _angelo_stage.detach(stage, ids, bpy.context.scene) is None)
    bpy.data.objects.remove(outsider)
    obj.parent = bpy.context.scene.objects[0]
    check("outgoing_artist_reference_falls_back", _angelo_stage.detach(stage, ids, bpy.context.scene) is None)
    obj.parent = None
    owner = _angelo_stage.detach(stage, ids, bpy.context.scene)
    _angelo_stage.discard(owner)
    check("discard_restores_main_inventory", set(native._main_ids()) == original)
    try:
        obj.name
    except ReferenceError:
        check("discard_invalidates_rna", True)
    else:
        raise AssertionError("Discard left a valid RNA wrapper")
    stage, obj, mesh, ids = detached_graph()
    owner = _angelo_stage.detach(stage, ids, bpy.context.scene)
    del owner
    gc.collect()
    check("capsule_release_frees_private_main", set(native._main_ids()) == original)
    try:
        obj.name
    except ReferenceError:
        check("capsule_release_invalidates_rna", True)
    else:
        raise AssertionError("Capsule release leaked its stage")
    try:
        _angelo_stage.detach(stage, ids, bpy.context.scene)
    except ReferenceError:
        check("freed_rna_rejected_without_dereference", True)
    else:
        raise AssertionError("Freed RNA admitted")

    # Names may be claimed while the stage is outside Main. Restoring it must
    # preserve the artist's new object and resolve only the private name.
    stage, obj, mesh, ids = detached_graph()
    private_name = obj.name
    owner = _angelo_stage.detach(stage, ids, bpy.context.scene)
    artist = bpy.data.objects.new(private_name, None)
    _angelo_stage.attach(owner)
    check("name_collision_preserves_artist_identity", artist.name == private_name and
          bpy.data.objects.get(private_name) == artist and obj.name != private_name)
    bpy.data.batch_remove(ids=ids)
    bpy.data.objects.remove(artist)
    anchor = bpy.data.scenes.new("Disposable document anchor")
    stage, obj, mesh, ids = detached_graph()
    owner = _angelo_stage.detach(stage, ids, anchor)
    bpy.data.scenes.remove(anchor)
    try:
        _angelo_stage.attach(owner)
    except RuntimeError:
        check("deleted_document_anchor_rejects_restore", set(native._main_ids()) == original)
    else:
        raise AssertionError("Deleted document anchor admitted")
    _angelo_stage.discard(owner)


def native_jobs():
    window, source = bpy.context.window, bpy.context.scene.objects["Cube"]
    before = set(native._main_ids())
    fingerprint = native._fingerprint(source)
    with patch.object(storage.ProgramStorageMixin, "memory_staging", True):
        job = native.begin(window, "angelo_scene_program", move(window, source))
        with spent_budget():
            result = job.poll()
        assert job.stage is not None, result
        check("active_job_uses_private_main", job.stage.native_owner is not None and not job.stage.bundle and
              not result.get("final") and set(native._main_ids()) == before and no_stages())
        owner = job.stage.native_owner
        failures = []

        def wrong_thread():
            try:
                _angelo_stage.attach(owner)
            except RuntimeError as error:
                failures.append(str(error))

        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join()
        check("wrong_thread_cannot_transfer_ownership", len(failures) == 1 and set(native._main_ids()) == before)
        with native.job_context(job.id) as inspected:
            inspected.objects[queries.object_handle(source)].location = (9, 9, 9)
        check("inspection_materializes_independent_snapshot", bool(job.stage.bundle) and
              job.stage.native_owner is None and set(native._main_ids()) == before)
        result = settle(job)
        check("inspection_edits_do_not_change_job", result.get("ready_to_commit") and native._fingerprint(source) == fingerprint)
        result = job.commit()
        check("active_job_commits_expected_values", result.get("success") and tuple(source.location) == (2, 3, 4) and
              set(native._main_ids()) == before)
        source.location = (0, 0, 0)


def resume_failures():
    window, source = bpy.context.window, bpy.context.scene.objects["Cube"]
    window.view_layer.update()
    baseline, fingerprint = set(native._main_ids()), native._fingerprint(source)
    with patch.object(storage.ProgramStorageMixin, "memory_staging", True):
        for partial in (False, True):
            job = native.begin(window, "angelo_scene_program", move(window, source))
            with spent_budget():
                job.poll()
            assert job.stage.native_owner is not None
            original_remove = job.backend._remove_ids

            def fail_after_first(ids):
                original_remove([next(block for block in ids if isinstance(block, bpy.types.Object))])
                raise RuntimeError("injected private Main partial removal")

            with patch.object(job.backend, "validate_stage", side_effect=RuntimeError("injected native resume validation")):
                if partial:
                    fake = SimpleNamespace(types=bpy.types, data=FailingBatchData())
                    with patch.object(storage, "_bpy", return_value=fake), \
                            patch.object(job.backend, "_remove_ids", side_effect=fail_after_first):
                        result = job.poll()
                        check("native_partial_cleanup_retains_ownership", result.get("cleanup_pending") and
                              storage.has_pending_cleanup() and native.runtime_is_staging() and job.stage is None)
                        try:
                            storage.require_cleanup_complete()
                        except native.ProgramError:
                            check("native_partial_cleanup_blocks_admission", True)
                        else:
                            raise AssertionError("Pending cleanup did not block admission")
                else:
                    result = job.poll()
            report.setdefault("resume_failures", {})[str(partial)] = dict(
                {key: result.get(key) for key in ("error_code", "refusal_details", "partial_changes_possible",
                                                  "cleanup_pending", "cleanup_error", "mutation_started", "recovery")},
                scene_changed=result.get("scene_changed", "absent"), source_unchanged=native._fingerprint(source) == fingerprint)
            # An unclassified failure claims scene_changed: false only on a live read-back
            # (agent_execution.rst, "Correction after a changed scene"): a resume that failed before any
            # write re-reads every bound revision (program_job_state.resumed_before_writes) and says
            # rollback_verified. A failed teardown wrapping one never does: CLEANUP_FAILED
            # keeps the UNKNOWN cause and its own partial changes. The live source here is the actual proof.
            # Without a failed teardown nothing live was written and the binding read back unchanged, so the
            # engine's unknown cause is a correctable UNSUPPORTED refusal that keeps it as engine_error
            # (program_job_state.staged_refusal).
            details = result.get("refusal_details") or {}
            honest = (result.get("error_code") == "CLEANUP_FAILED" and result.get("cleanup_pending") is True
                      and details.get("cause_code") == "UNKNOWN" if partial else
                      result.get("error_code") == "UNSUPPORTED" and details.get("engine_error") == "UNKNOWN"
                      and details.get("engine_reason") == "injected native resume validation")
            claimed = (("scene_changed" not in result and result.get("partial_changes_possible") is True) if partial
                       else (result.get("scene_changed") is False and result.get("partial_changes_possible") is False
                             and result.get("rollback_verified") is True))
            check("resume_failure_preserves_original_error_%s" % partial,
                  not result["success"] and "injected native resume validation" in result["error"] and honest and
                  claimed and native._fingerprint(source) == fingerprint)
            barrier = RecoveryBarrier()  # A failed teardown's unknown cause raises the request barrier.
            barrier.refused("angelo_scene_program", move(window, source), dict(result))
            # An unclassified cause holds and is closed: no read can explain it (request_recovery, which
            # replaced the observation-id barrier; it was the "opaque" detail before). The refusal holds nothing.
            if partial:
                check("resume_failure_raises_an_opaque_barrier_%s" % partial, barrier.pending is not None
                      and barrier.pending["code"] == "UNKNOWN" and barrier.pending["holding"] is True
                      and barrier.pending["closed"] is True)
            else:
                check("resume_failure_refusal_holds_nothing", barrier.pending is None)
            storage.cleanup_pending()
            check("resume_failure_recovers_exact_owned_ids_%s" % partial,
                  set(native._main_ids()) == baseline and not storage.has_pending_cleanup())

        job = native.begin(window, "angelo_scene_program", move(window, source))
        with spent_budget():
            job.poll()
        with patch.object(job.backend, "suspend_stage", side_effect=OSError("injected inspection snapshot failure")):
            try:
                with native.job_context(job.id):
                    raise AssertionError("Failed materialization yielded inspection data")
            except OSError as error:
                check("inspection_failure_releases_native_ownership", "snapshot failure" in str(error) and
                      job.closed and job.result["cancelled"] and set(native._main_ids()) == baseline)


def cancellation_and_previews():
    window, source = bpy.context.window, bpy.context.scene.objects["Cube"]
    before = set(native._main_ids())
    with patch.object(storage.ProgramStorageMixin, "memory_staging", True):
        window.view_layer.update()
        job = native.begin(window, "angelo_scene_program", move(window, source))
        with spent_budget():
            job.poll()
        wrapper = next(iter(job.stage.resident.objects.values()))
        result = job.cancel()
        check("cancel_frees_private_main", result.get("cancelled") and set(native._main_ids()) == before)
        try:
            wrapper.name
        except ReferenceError:
            check("cancel_invalidates_private_wrappers", True)
        else:
            raise AssertionError("Cancel leaked private IDs")
        job = native.begin(window, "angelo_scene_program", move(window, source, mode="PREVIEW"))
        result = settle(job)
        record = PREVIEWS.get(result["preview_id"], native._owner(None, window))
        check("retained_preview_is_file_backed", record["stage"].native_owner is None and
              Path(record["stage"].bundle).is_file() and set(native._main_ids()) == before)
        PREVIEWS.discard(result["preview_id"], native._owner(None, window))
        # A material a move cannot write stays live in its slot (stage_borrowing) and is cleared before the
        # yield, so the stage, image and all, stays a closed native graph.
        material = source.data.materials[0]
        image = bpy.data.images.new("Borrowed image", width=2, height=2)
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image

        def moved():
            args = args_for(window, [{"kind": "transform", "target": queries.object_handle(source),
                                     "parameters": {"location": [2, 3, 4]}}], writes=[source], store=FullStore())
            job = native.begin(window, "angelo_scene_program", args, store=FullStore())
            with spent_budget():
                job.poll()
            return job

        job = moved()
        check("borrowed_material_keeps_native_stage", job.stage.native_owner is not None and not job.stage.bundle
              and material.users == 1)
        check("borrowed_material_still_commits", settle(job).get("ready_to_commit") and job.commit().get("success")
              and source.data.materials[0] == material and material.users == 1)
        source.location = (0, 0, 0)
        # An animated material is cloned, and its clone's live image forces the exact existing library snapshot path.
        material.node_tree.animation_data_create()
        job = moved()
        check("borrowed_resource_uses_bundle", job.stage.native_owner is None and bool(job.stage.bundle))
        check("borrowed_resource_still_commits", settle(job).get("ready_to_commit") and job.commit().get("success"))
        material.node_tree.animation_data_clear()
        material.node_tree.nodes.remove(node)
        bpy.data.images.remove(image)
        source.location = (0, 0, 0)


def bulk_commit():
    window, source = bpy.context.window, bpy.context.scene.objects["Cube"]
    window.view_layer.update()
    outsiders = [bpy.data.objects.new("Bulk cleanup artist %d" % index, None) for index in range(1024)]
    before = set(native._main_ids())
    fingerprint = native._fingerprint(source)
    args = args_for(window, [{"kind": "create_primitive", "target": None,
                             "parameters": {"primitive": "CUBE", "name": "Bulk committed cube", "alias": "created"}}])
    job = native.begin(window, "angelo_scene_program", args)
    result = settle(job)
    check("bulk_scene_program_stages", result.get("ready_to_commit"))
    result = job.commit()
    created = window.scene.objects["Bulk committed cube"]
    after = set(native._main_ids())
    check("bulk_cleanup_preserves_transferred_ids", result.get("success") and
          created.as_pointer() in after and created.data.as_pointer() in after and len(created.data.vertices) == 8)
    check("bulk_cleanup_preserves_every_artist_id", before <= after and
          native._fingerprint(source) == fingerprint and all(block.name.startswith("Bulk cleanup artist") for block in outsiders))
    mesh = created.data
    bpy.data.objects.remove(created, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    bpy.data.batch_remove(ids=outsiders)


def light_subtype_ownership():
    """RNA subtype changes must neither orphan an ID nor lose kernel authority."""
    window, source = bpy.context.window, bpy.context.scene.objects['Light']
    for memory in (True, False):
        before = set(native._main_ids())
        fingerprint = native._fingerprint(source)
        operation = {'kind': 'light_configure', 'target': queries.object_handle(source),
                     'parameters': {'settings': {'light_type': 'SPOT', 'spot_size': .5}}}
        job = native.begin(window, 'angelo_scene_program', args_for(window, [operation], writes=[source]))
        job.backend.memory_staging = memory
        job.backend.fuse_phases = False
        job.poll()  # Copy a PointLight wrapper.
        result = job.poll()  # Refine the same ID into a SpotLight wrapper.
        assert result.get('success'), result
        check('light_subtype_yield_owns_every_id_%s' % memory, set(native._main_ids()) == before)
        check('light_subtype_cancel_has_no_orphans_%s' % memory,
              job.cancel().get('cancelled') and set(native._main_ids()) == before and
              native._fingerprint(source) == fingerprint and not storage.has_pending_cleanup())

        operations = [operation,
                      {'kind': 'light_configure', 'target': queries.object_handle(source),
                       'parameters': {'settings': {'energy': 350}}}]
        job = native.begin(window, 'angelo_scene_program', args_for(window, operations, writes=[source]))
        job.backend.memory_staging = memory
        # Both actions execute in one quantum: file hydration cannot repair the
        # kernel's ownership set between the subtype change and the next edit.
        result = settle(job)
        check('light_subtype_repeated_edit_remains_owned_%s' % memory, result.get('ready_to_commit'))
        job.cancel()
        check('repeated_light_edits_leave_no_orphans_%s' % memory,
              set(native._main_ids()) == before and native._fingerprint(source) == fingerprint)

        operations = [
            {'kind':'light_create', 'target':None, 'parameters':{'name':'Subtype alias fixture','alias':'light',
                                                'settings':{'light_type':'POINT'}}},
            {'kind':'light_configure','target':'$light','parameters':{'settings':{'light_type':'SPOT','spot_size':.4}}},
            {'kind':'light_configure','target':'$light','parameters':{'settings':{'light_type':'AREA','shape':'DISK','size':2}}},
            {'kind':'light_configure','target':'$light','parameters':{'settings':{'energy':350}}}]
        job = native.begin(window, 'angelo_scene_program', args_for(window, operations))
        job.backend.memory_staging = memory
        result = settle(job)
        check('created_light_repeated_subtypes_stage_%s' % memory, result.get('ready_to_commit'))
        if memory:
            context = job.stage.resident.context
            check('subtype_changes_do_not_inflate_creation_ledger', len(context.created_ids) == 2)
            previous = next(block for block in context.created_ids if block.id_type == 'LIGHT')
            try:
                context.refresh_id(previous, source.data)
            except ValueError:
                check('wrapper_refresh_cannot_admit_artist_data', source.data not in context.owned_ids)
            else:
                raise AssertionError('Wrapper refresh granted ownership of artist data')
        result = job.commit()
        created = window.scene.objects['Subtype alias fixture']
        owned = {created.as_pointer(), created.data.as_pointer()}
        check('created_light_commit_preserves_exact_ids_%s' % memory,
              result.get('success') and created.data.type == 'AREA' and created.data.energy == 350 and
              set(native._main_ids()) == before | owned)
        data = created.data
        bpy.data.objects.remove(created, do_unlink=True)
        bpy.data.lights.remove(data)
        check('created_light_fixture_leaves_no_orphans_%s' % memory, set(native._main_ids()) == before)


try:
    light_subtype_ownership()
    file_roundtrips()
    native_ownership()
    native_jobs()
    resume_failures()
    cancellation_and_previews()
    bulk_commit()
    report["success"] = True
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
