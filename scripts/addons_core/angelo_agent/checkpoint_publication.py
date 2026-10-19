# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verified checkpoint publication and terminal evidence for the retained capture owner.

The original module retains shared state and patchable dependencies.
Methods resolve those named dependencies at call time; this base owns no resources.
"""


class CheckpointPublicationMixin:
    __slots__ = ()

    def _native_publication(self):
        """Native POST admission observes actual held publication, not a receipt."""
        from . import checkpoint_capture as capture_api
        with self._operation() as resource, capture_api._transaction(self._binding.root):
            if not resource.native_active or resource.phase not in {"published", "base_published"}:
                raise ValueError("The native capture has no acknowledged publication")
            self._fresh(resource)
            for role in resource.published:
                self._matching_output(resource, role, resource.outputs[role]._resource)
                if not capture_api._stable_file(resource.sealed_outputs[role], capture_api.descriptor_witness(resource.outputs[role]._resource.descriptor)):
                    raise ValueError("The published capture component changed before POST")

    def _native_terminal(self):
        """Fresh completion evidence for the native capsule, including safety purpose."""
        from . import checkpoint_capture as capture_api
        resource = self._resource
        if resource is None or not resource.native_active or resource.phase != "finished" or resource.failed:
            return None
        with self._operation() as resource, capture_api._transaction(self._binding.root):
            return self._terminal_record(resource)

    def _terminal_record(self, resource):
        from . import checkpoint_capture as capture_api
        from .checkpoint_capture_state import decode_record
        capture_api.assert_directory_identity(self._binding.path, resource.folder)
        record = decode_record(capture_api._read_json(resource.folder, "checkpoint.json"), self.capture_id)
        if (record["state"] != "READY"
                or any(record.get(key) != value for key, value in self._binding.sealed)
                or capture_api._component_binding(record) != resource.components
                or (record.get("version") == 4 and record["capture_validation"] != "VALIDATED")):
            return None
        for role in resource.published:
            current = self._matching_output(resource, role, resource.outputs[role]._resource)
            if not capture_api._stable_file(resource.sealed_outputs[role], current):
                return None
        return record

    def _native_safety(self):
        """Revalidate retained durable safety at native Restore consumption.

        Native ownership is already closed. Success is meaningful only together
        with C's opaque capsule and current Main/Sculpt content comparison.
        """
        from . import checkpoint_capture as capture_api
        with self._operation() as resource, capture_api._transaction(self._binding.root):
            if (resource.native_active or not resource.native_attempted or resource.phase != "finished"
                    or dict(self._binding.sealed)["purpose"] != "safety"):
                raise ValueError("The retained capture is not completed safety evidence")
            record = self._terminal_record(resource)
            if record is None or record.get("protected") is not True:
                raise ValueError("The retained protected safety checkpoint changed")
            return record

    def _published_unchanged(self, resource):
        from . import checkpoint_capture as capture_api
        for role in resource.published:
            current = self._matching_output(resource, role, resource.outputs[role]._resource)
            if not capture_api._stable_file(resource.sealed_outputs[role], current):
                raise ValueError("The acknowledged capture component changed after publication")

    def _publish_output(self, resource, role):
        from . import checkpoint_capture as capture_api
        output = resource.outputs[role]._resource
        self._matching_output(resource, role, output)
        temporary, final = capture_api.output_names(role)
        capture_api.publish_new_file(resource.folder, temporary, final)
        # For this single, quiescent link operation exactly two names must refer
        # to the retained file. Never remove either name on an ambiguous result.
        current = capture_api.descriptor_witness(output.descriptor)
        if (current.st_nlink != 2 or not capture_api.same_file(output.opened, current)
                or not capture_api.same_file(current, capture_api.entry_info(resource.folder, temporary))
                or not capture_api.same_file(current, capture_api.entry_info(resource.folder, final))):
            raise ValueError("Capture publication did not preserve its exact file")
        capture_api._unlink_file(resource.folder, temporary)
        resource.published.add(role)
        current = self._matching_output(resource, role, output)
        verified = resource.verified_outputs[role]
        # Our link/unlink changes ctime, but cannot change verified bytes. Seal
        # that single transition here, before any other component or metadata I/O.
        if (not capture_api.same_file(verified, current) or verified.st_size != current.st_size
                or verified.st_mtime_ns != current.st_mtime_ns):
            raise ValueError("The verified capture component changed during publication")
        resource.sealed_outputs[role] = current

    def publish(self, capture):
        """Publish verified bytes once; the live native owner still owes POST."""
        from . import checkpoint_capture as capture_api
        with self._operation() as resource:
            if not resource.native_active or resource.publish_attempted:
                raise ValueError("Capture publication is unavailable or was already attempted")
            resource.publish_attempted = True
            try:
                facts = capture_api._native_facts(capture, self)
                if facts["post_state"] != "pending":
                    raise ValueError("Capture publication must precede the native POST outcome")
                roles = {"BASE", "RUNTIME"} if facts["owner_count"] else {"BASE"}
                if set(resource.outputs) != roles or resource.declared != roles:
                    raise ValueError("The verified capture does not discharge all output obligations")
                receipt = {"capture_id": self.capture_id, "scene_bytes": facts["scene_bytes"]}
                with capture_api._transaction(self._binding.root):
                    current = self._fresh(resource)
                    for role in roles:
                        output = resource.outputs[role]._resource
                        self._matching_output(resource, role, output)
                        if not output.acknowledged or not output.borrow_attempted:
                            raise ValueError("The native writer did not own the capture output")
                        expected_size = facts["scene_bytes" if role == "BASE" else "runtime_bytes"]
                        if capture_api.descriptor_witness(output.descriptor).st_size != expected_size:
                            raise ValueError("Native verified size differs from the actual output")
                        capture_api.os.fsync(output.descriptor)
                    verified = {role: capture_api.descriptor_witness(resource.outputs[role]._resource.descriptor) for role in roles}
                    resource.verified_outputs = verified
                    if facts["owner_count"]:
                        receipt["scene_hash"] = capture_api._hash_descriptor(resource.outputs["BASE"]._resource.descriptor,
                                                                  facts["scene_bytes"])
                        receipt["sculpt"] = {"filename": "sculpt.runtime", "format_version": facts["format_version"],
                            "bytes": facts["runtime_bytes"], "owner_count": facts["owner_count"],
                            "hash": capture_api._hash_descriptor(resource.outputs["RUNTIME"]._resource.descriptor,
                                                     facts["runtime_bytes"])}
                    # Revalidate the actual native witness after reading all
                    # bytes, before any name or metadata publication.
                    if capture_api._native_facts(capture, self) != facts:
                        raise ValueError("Native capture verification changed during publication")
                    def unchanged(role):
                        output = resource.outputs[role]._resource
                        self._matching_output(resource, role, output)
                        if (not capture_api._stable_file(verified[role], capture_api.descriptor_witness(output.descriptor))
                                or not capture_api._stable_file(verified[role], capture_api.file_witness(resource.folder, capture_api.output_names(role)[0]))):
                            raise ValueError("The hash-verified capture component changed before publication")
                    for role in roles:
                        unchanged(role)
                    resource.components = capture_api._component_binding(receipt)
                    for role in ("BASE", "RUNTIME"):
                        if role in roles:
                            unchanged(role)
                            self._publish_output(resource, role)
                            self._published_unchanged(resource)
                    capture_api.sync_directory(resource.folder)
                    self._published_unchanged(resource)
                    if facts["owner_count"]:
                        from .checkpoint_seal import seal
                        candidate = dict(current, state="READY", scene_bytes=receipt["scene_bytes"],
                            scene_hash=receipt["scene_hash"], sculpt=receipt["sculpt"], storage="full",
                            capture_ms=round((capture_api.time.monotonic() - resource.started) * 1000, 3))
                        candidate["component_seal"] = seal(self._binding.root, candidate, receipt["scene_hash"], request=True)
                        clean = capture_api.validate_transition(current, candidate, kind="publish_bundle")
                        capture_api._write_json(resource.folder, "checkpoint.json", clean)
                        resource.phase = "published"
                    else:
                        resource.phase = "base_published"
                    self._fresh(resource)
                    self._published_unchanged(resource)
                return receipt
            except BaseException:
                resource.failed = True
                raise

    def finish(self, capture):
        """Record the real POST outcome while native and file owners still live."""
        from . import checkpoint_capture as capture_api
        with self._operation() as resource:
            if (not resource.native_active or resource.phase not in {"published", "base_published"}
                    or resource.finish_attempted):
                raise ValueError("Capture completion is unavailable or was already attempted")
            resource.finish_attempted = True
            try:
                facts = capture_api._native_facts(capture, self)
                if facts["post_state"] not in {"complete", "failed"}:
                    raise ValueError("The native capture has no POST outcome")
                with capture_api._transaction(self._binding.root):
                    current = self._fresh(resource)
                    for role in resource.published:
                        output = resource.outputs[role]._resource
                        self._matching_output(resource, role, output)
                        if not capture_api._stable_file(resource.sealed_outputs[role], capture_api.descriptor_witness(output.descriptor)):
                            raise ValueError("The published capture component changed during POST")
                    if resource.phase == "published":
                        if (facts["owner_count"] != current["sculpt"]["owner_count"]
                                or facts["scene_bytes"] != current["scene_bytes"]
                                or facts["runtime_bytes"] != current["sculpt"]["bytes"]):
                            raise ValueError("The native POST outcome names different capture components")
                        candidate = dict(current, capture_validation=("VALIDATED" if facts["post_state"] == "complete"
                                                                     else "INTERRUPTED_VALID"))
                        clean = capture_api.validate_transition(current, candidate, kind="finish")
                    else:
                        if (facts["owner_count"] or facts["post_state"] != "complete"
                                or set(resource.outputs) != {"BASE"} or resource.published != {"BASE"}):
                            raise ValueError("The ordinary capture did not finish without runtime obligations")
                        clean = {key: value for key, value in current.items()
                                 if key not in {"capture_validation", "capture_outputs"}}
                        clean.update(version=3, state="READY", scene_bytes=facts["scene_bytes"], storage="full",
                                     capture_ms=round((capture_api.time.monotonic() - resource.started) * 1000, 3))
                        if capture_api._component_binding(clean) != resource.components:
                            raise ValueError("The ordinary capture completion changed its verified BASE facts")
                        # No byte is read back on a request: the identity seal binds the published
                        # BASE's size, modification time and inode; deduplication later binds its content.
                        # Coarse timestamps can be shared by two captures: bind content there instead.
                        from .checkpoint_seal import coarse_timestamps, seal, seal_identity
                        base = resource.sealed_outputs["BASE"]
                        if coarse_timestamps(base.st_mtime_ns):
                            digest = capture_api._hash_descriptor(resource.outputs["BASE"]._resource.descriptor,
                                                                  facts["scene_bytes"])
                            clean["component_seal"] = seal(self._binding.root, clean, digest, request=True)
                        else:
                            clean["component_seal"] = seal_identity(self._binding.root, clean, base.st_size,
                                                                    base.st_mtime_ns, base.st_ino)
                        clean = capture_api.validate_transition(current, clean, kind="no_runtime")
                    capture_api._write_json(resource.folder, "checkpoint.json", clean)
                    capture_api.assert_directory_identity(self._binding.path, resource.folder)
                    self._published_unchanged(resource)
                resource.phase = "finished"
                return clean
            except BaseException:
                resource.failed = True
                raise
