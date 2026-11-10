# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verified checkpoint publication and terminal evidence for the retained capture owner.

The original module retains shared state and patchable dependencies.
Methods resolve those named dependencies at call time; this base owns no resources.

Content binding (``doc/angelo/checkpoint_policy.rst``, "Content binding"): native hashes what it writes for every
Sculpt pair, every safety capture and every ordinary BASE on a volume whose timestamps two captures can share.
``publish`` feeds the chunks of its one read to native hashers and ``capture_bind`` compares inside native, before
any name is published; native refuses POST without it. The binding never reaches Python, and no record or seal
format changes: a bound ordinary BASE gets the existing version 3 content seal.
"""


def _native():
    import _bpy   # An older native without capture_hasher or capture_bind raises here: refused, never stamp-only.
    return _bpy._angelo_checkpoint


class CheckpointPublicationMixin:
    __slots__ = ()

    def _native_purpose(self):
        """Original purpose only; it selects native witness cost, not authority."""
        from . import checkpoint_capture as capture_api
        with self._operation() as resource, capture_api._transaction(self._binding.root):
            if not resource.native_active:
                raise ValueError("The capture purpose requires its live native interval")
            self._fresh(resource)
            return dict(self._binding.sealed)["purpose"]

    def _native_binding(self):
        """Native asks once, before writing BASE, whether this capture's BASE content will be bound (compared).

        A safety capture always; an ordinary one where its volume stamps times two captures can share, predicted
        from the fresh, empty BASE (``checkpoint_seal.coarse_timestamps``): the published file is stamped by the same
        volume. ``finish`` refuses a BASE whose final stamp contradicts the prediction. A Sculpt pair is bound by
        native whatever this answers. It runs on every capture's request path, so it reads one stat of the
        retained descriptor and nothing else: the purpose is the reservation's own (``_binding``), and answering yes
        is always safe (it only costs a hash)."""
        from . import checkpoint_capture as capture_api
        from .checkpoint_seal import coarse_timestamps
        with self._operation() as resource:
            if not resource.native_active or resource.bind_required is not None or "BASE" not in resource.outputs:
                raise ValueError("The content binding is decided once, for the live native BASE destination")
            empty = capture_api.descriptor_witness(resource.outputs["BASE"]._resource.descriptor)
            if empty.st_size:
                raise ValueError("The content binding is decided before the BASE write")
            safety = dict(self._binding.sealed)["purpose"] == "safety"
            resource.bind_required = bool(safety or coarse_timestamps(empty.st_mtime_ns, empty.st_dev))
            return resource.bind_required

    @staticmethod
    def _refuse_unbound_coarse(base):
        """An ordinary BASE predicted fine (not bound) but stamped coarse, about 1e-6 on a fine volume: its identity
        seal could be shared by another capture. Refused before any name is published; the next request captures."""
        from .checkpoint_seal import coarse_timestamps
        if coarse_timestamps(base.st_mtime_ns, base.st_dev):
            raise ValueError("The BASE was stamped on a coarse clock but not content-bound")

    def _bind_content(self, capture, resource, roles, facts):
        """Hash each role once (BLAKE2b and the native hasher from the same chunks) and bind them in native.

        Returns ``{role: digest}``. Raises on any difference: no name is published and POST is refused."""
        from . import checkpoint_capture as capture_api
        native = _native()
        hashers = {role: native.capture_hasher(capture, self, role) for role in roles}
        digests = {role: capture_api._hash_descriptor(resource.outputs[role]._resource.descriptor,
                                                      facts["scene_bytes" if role == "BASE" else "runtime_bytes"],
                                                      bind=hashers[role]) for role in ("BASE", "RUNTIME") if role in roles}
        native.capture_bind(capture, self, hashers["BASE"], hashers.get("RUNTIME"))
        resource.content_bound = True   # Only here: publish and finish refuse a required binding without it.
        return digests

    @staticmethod
    def _requires_binding(resource, owner_count):
        """A pair, a safety capture or a coarse-clock BASE: native compared what it wrote with what was read."""
        return bool(owner_count or resource.bind_required)

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
                if resource.bind_required is None:
                    raise ValueError("The native capture did not decide its content binding")
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
                    if not facts["owner_count"] and not resource.bind_required:
                        self._refuse_unbound_coarse(verified["BASE"])
                    if facts["owner_count"]:
                        # The native writer's own descriptors (writing=True): where closing them restamps (FAT,
                        # exFAT, measured), their stats vouch for nothing, refused before any name is published;
                        # such a volume has no hard links anyway. One wait for both, before either is hashed.
                        from .checkpoint_store_text import settle_writers
                        settle_writers(capture_api.change_clock, verified.values())
                        digests = self._bind_content(capture, resource, roles, facts)
                        receipt["scene_hash"] = digests["BASE"]
                        receipt["sculpt"] = {"filename": "sculpt.runtime", "format_version": facts["format_version"],
                            "bytes": facts["runtime_bytes"], "owner_count": facts["owner_count"],
                            "hash": digests["RUNTIME"]}
                    elif resource.bind_required:
                        # A safety capture or a coarse-clock BASE: bound here, before POST (_hash_descriptor's own
                        # settle). The digest is not in the receipt or record: finish seals it as content.
                        resource.content_digest = self._bind_content(capture, resource, roles, facts)["BASE"]
                    if self._requires_binding(resource, facts["owner_count"]) and not resource.content_bound:
                        # Native refuses POST then, but a READY pair would already be listed and restorable: refuse
                        # here, before any name or record is published, whatever skipped the binding.
                        raise ValueError("The capture's content was not bound to native's write before publication")
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
                    if facts["owner_count"]:
                        # The boundary is publish's return: one wait for both sealed components (change_clock), so
                        # from then on a write by any writer stamps another change time than the seal's. It waits for
                        # the tick boundary after the links' stamps, so placed last the READY write's time counts toward
                        # it; a write inside a component's own tick before it is the documented limit
                        # (checkpoint_policy.rst) wherever it stands. READY may be durable before the tick passes:
                        # only finish(), after publish returns, records VALIDATED, and a raise here (Unsettled) sets
                        # failed, which refuses POST and finish. FAT and exFAT, where closing restamps, were refused
                        # by the first settle, before any name was published. An ordinary BASE (an identity seal,
                        # nothing hashed) keeps its request path free of any wait.
                        from .checkpoint_store_text import settle_writers
                        settle_writers(capture_api.change_clock, resource.sealed_outputs.values())
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
                        bound = resource.content_bound or not self._requires_binding(resource, facts["owner_count"])
                        # A pair whose content was never bound is never VALID (restorable), whatever POST said: it
                        # is recorded INVALID and refused below.
                        candidate = dict(current, capture_validation=(
                            "INVALID" if not bound else
                            "VALIDATED" if facts["post_state"] == "complete" else "INTERRUPTED_VALID"))
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
                        # A bound BASE (safety, or coarse timestamps two captures can share) was compared with
                        # native's write in publish: its digest gets the version 3 content seal. Otherwise no
                        # byte is read back on a request: the identity seal binds the published BASE's size,
                        # modification time and inode; deduplication later binds its content.
                        from .checkpoint_seal import coarse_timestamps, seal, seal_identity
                        base = resource.sealed_outputs["BASE"]
                        if resource.content_digest is not None and resource.content_bound:
                            clean["component_seal"] = seal(self._binding.root, clean, resource.content_digest,
                                                           request=True)
                        elif resource.bind_required or coarse_timestamps(base.st_mtime_ns, base.st_dev):
                            raise ValueError("The ordinary capture was not content-bound before POST")
                        else:
                            clean["component_seal"] = seal_identity(self._binding.root, clean, base.st_size,
                                                                    base.st_mtime_ns, base.st_ino)
                        clean = capture_api.validate_transition(current, clean, kind="no_runtime")
                    capture_api._write_json(resource.folder, "checkpoint.json", clean)
                    capture_api.assert_directory_identity(self._binding.path, resource.folder)
                    if clean.get("capture_validation") == "INVALID":
                        raise ValueError("The capture's content was not bound to native's write; recorded INVALID")
                    self._published_unchanged(resource)
                resource.phase = "finished"
                return clean
            except BaseException:
                resource.failed = True
                raise
