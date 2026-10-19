# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UI-independent, bounded Studio operations shared with the chat controller.

All methods run on the host main thread except the text-only enhancement worker.
Provider requests use the existing StudioJobs workers and exact account bindings.
"""

import errno
import hashlib
import ipaddress
import json
import os
import queue
import stat
import threading
import uuid
from pathlib import Path

from .studio_generation import GenerationError, validate_generation, public_url
from .studio_spec import validate_studio
from .studio_state import ASPECTS
from .provider_registry import GEMINI_IMAGE_CONTROLS
from .checkpoints import _transaction
from .checkpoint_policy import MAX_CREATED_FILE, _file_info, _open_file, _private_directory, _unlink_file
from .checkpoint_filesystem import (directory_info, entry_info, publish_new_file, require_anchored_files,
    sync_directory)
from .checkpoint_timeline import read_timeline


class StudioExportError(ValueError):
    """A refused preflight; no scene or workspace output was modified."""
    partial_changes_possible = False


def _copy_export(source, destination):
    """Bounded streaming prevents a growing source exceeding restore limits."""
    copied = 0
    while data := source.read(min(1024 * 1024, MAX_CREATED_FILE - copied + 1)):
        if copied + len(data) > MAX_CREATED_FILE:
            raise StudioExportError("Agent exports must be at most 150 MiB, matching the bounded Studio model-output limit")
        destination.write(data)
        copied += len(data)
    return copied


def _output_info(folder, name):
    return entry_info(folder, name)


def _same_file(first, second):
    return stat.S_ISREG(second.st_mode) and (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _publish_export(folder, temporary, name):
    """Same-directory hard-link publication is atomic and never overwrites.

    A filesystem without hard-link support refuses export and retains the
    library asset; do not fall back to an overwrite-capable rename.
    """
    try:
        if isinstance(folder, Path):
            if folder.resolve() != folder:
                raise StudioExportError("The export directory changed during copying")
            os.link(folder / temporary, folder / name, follow_symlinks=False)
        else:
            publish_new_file(folder, temporary, name)
    except OSError as error:
        if error.errno in {errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP}:
            raise StudioExportError("This filesystem cannot publish an atomic no-overwrite export; use manual Studio Export. The library asset is unchanged.") from error
        raise


def capabilities(mode, model=""):
    if mode in {"tripo_api", "meshy_api"}:
        return {"polycount": {"minimum": 48 if mode == "tripo_api" else 100,
            "maximum": (50000 if model.startswith("P2-") else 1500000) if mode == "tripo_api" else 300000},
            "texture": [False, True], "references_max": 1}
    controls = {"aspect": list(ASPECTS), "resolution": ["2K", "4K"] if mode == "seedream_api" else ["1K", "2K", "4K"], "references_max": 4}
    if mode == "gemini_images_api" and model in GEMINI_IMAGE_CONTROLS:
        # Studio's offered values, limited to what this exact model documents.
        documented = GEMINI_IMAGE_CONTROLS[model]
        controls.update(aspect=[a for a in controls["aspect"] if a in documented["aspect"]],
                        resolution=[r for r in controls["resolution"] if r in documented["resolution"]])
    if mode == "openai_images_api":
        controls.update(quality=["auto", "low", "medium", "high"], background=["auto", "opaque", "transparent"],
                        size="Optional WIDTHxHEIGHT; multiples of 16, max 3840 px, 655360–8294400 pixels, ratio <= 3:1")
    return controls


def generation_options(mode, model, supplied):
    controls = capabilities(mode, model)
    if set(supplied) - (set(controls) - {"references_max"}):
        raise ValueError("This model does not support the requested Studio controls; inspect the catalog")
    defaults = {"polycount": 10000, "texture": True} if mode in {"tripo_api", "meshy_api"} else {"aspect": "1:1", "resolution": "2K"}
    if mode == "openai_images_api":
        defaults.update(quality="auto", background="auto", size="")
    options = dict(defaults, **supplied)
    for key, value in options.items():
        constraint = controls[key]
        if isinstance(constraint, list) and value not in constraint:
            raise ValueError("This provider does not support that " + key)
    return options


def fingerprint(path):
    path = Path(path)
    stat = path.stat()
    return (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)


class StudioPending:
    """Deferred result: native UI remains responsive while providers work."""
    def __init__(self, poll, cancel, *, asset_id="", account="", cleanup=lambda: None):
        self._poll, self._cancel, self._cleanup = poll, cancel, cleanup
        self.asset_id, self.account = asset_id, account
        self.closed = False

    def poll(self):
        result = self._poll()
        if result is not None and not self.closed:
            self.closed = True
            self._cleanup()
        return result

    def cancel(self):
        if not self.closed:
            self.closed = True
            self._cancel()
            self._cleanup()


class StudioAgent:
    def __init__(self, library, jobs, catalog, binding, controller, import_model, enhancer):
        self.library, self.jobs, self.catalog, self.binding = library, jobs, catalog, binding
        self.controller, self.import_model, self.enhancer = controller, import_model, enhancer
        self.enhancement_accounts = lambda: []
        self.preview = lambda path: self.controller.references.import_images([str(path)])

    def refresh(self):
        self.jobs.pump()
        self.library.reload()

    def folder(self, identity):
        if identity and identity not in self.library.folders:
            raise ValueError("Choose an existing Studio folder ID")
        return identity

    def asset(self, identity, ready=False):
        asset = self.library.assets.get(identity)
        if not asset:
            raise ValueError("This Studio asset is unavailable; refresh the library")
        if ready and (asset.get("status") != "ready" or not asset.get("files")):
            raise ValueError("Wait for a ready Studio asset; failed/stopped tasks are never automatically retried")
        return asset

    def attachment(self, identity):
        records = self.controller.turn_attachments + [r for m in self.controller.state["messages"] for r in m.get("references", [])]
        reference = next((r for r in records if Path(r["path"]).stem == identity), None)
        if not reference or reference.get("kind") != "image":
            raise ValueError("Choose an image attached to THIS conversation")
        # The checked path, not the stored one: it survives a move of the state folder.
        return dict(reference, path=str(self.controller.references.checked_path(reference)))

    def references(self, args):
        result = []
        for requested in args.get("references", []):
            source, identity = requested["source"], requested["id"]
            if source == "ATTACHMENT":
                result.append(self.attachment(identity))
            elif source == "ASSET":
                asset = self.asset(identity, ready=True)
                if asset["kind"] != "IMAGE":
                    raise ValueError("Only a library IMAGE can be used as a generation reference")
                path = self.library.checked_file(asset, asset["files"][0])
                result.append({"path": str(path), "name": asset["prompt"][:160] or path.name})
            else:
                # Syntax only, NO DNS or URL fetch until the user approves usage.
                try:
                    parsed = public_url(identity)
                except GenerationError as error:
                    raise ValueError(str(error)) from None
                try:
                    address = ipaddress.ip_address(parsed.hostname)
                except ValueError:
                    address = None
                if (address and not address.is_global) or parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost"):
                    raise ValueError("Use a public HTTPS image URL, not a local/private address")
                result.append({"url": identity, "name": "Public HTTPS image reference"})
        return result

    def generation(self, args):
        choice = next((m for m in self.catalog() if m["connection"] == args["connection_id"] and m["id"] == args["model"]), None)
        if not choice or not choice["ready"]:
            raise ValueError("Connect/check the exact generation account and model in AI Providers; no billing fallback")
        binding = self.binding(choice["connection"])
        options = generation_options(choice["mode"], choice["id"], args.get("options", {}))
        references = self.references(args)
        self.folder(args.get("folder", ""))
        validate_generation(choice["mode"], choice["id"], args["prompt"], options, references)
        return choice, binding, options, references

    def usage_label(self, name, args):
        if name == "angelo_studio_generate":
            choice, _, _, _ = self.generation(args)
            billing = " · Angelo team" if choice.get("team") else " · API billing" + (" · unverified access" if not choice["verified"] else "")  # i18n: stored on the call and in chat rows, shown by studio_approvals; kept English
            return choice["account"] + " · " + choice["name"] + billing
        if name == "angelo_studio_enhance":
            return self.enhancer(args)["label"]
        return ""

    def precondition(self, name, arguments):
        args = validate_studio(name, arguments)
        self.refresh()
        if name == "angelo_studio_generate":
            choice, binding, options, refs = self.generation(args)
            # Key rotation/account deletion and edited references invalidate the
            # exact approval. The digest is runtime-only and never persisted.
            return (choice["connection"], choice["mode"], binding.model_ids, binding.verified,
                binding.scope_digest(), json.dumps(options, sort_keys=True),
                tuple(fingerprint(r["path"]) if r.get("path") else r["url"] for r in refs), args.get("folder", ""))
        if name == "angelo_studio_enhance":
            return self.enhancer(args)["signature"]
        if name == "angelo_studio_create_folder":
            return ()
        if name == "angelo_studio_import_image":
            self.folder(args.get("folder", ""))
            return fingerprint(self.attachment(args["reference_id"])["path"])
        asset = self.asset(args["asset_id"], ready=name in {"angelo_studio_import_3d", "angelo_studio_export"})
        signature = json.dumps(asset, sort_keys=True)
        if name == "angelo_studio_move_asset":
            self.folder(args["folder"])
        if name in {"angelo_studio_export", "angelo_studio_import_3d"}:
            index = args.get("output_index", 0)
            if index >= len(asset["files"]):
                raise ValueError("Choose an existing output index")
            path = self.library.checked_file(asset, asset["files"][index])
            signature = (signature, fingerprint(path))
            if name == "angelo_studio_import_3d" and (asset["kind"] != "3D" or path.suffix.lower() != ".glb"):
                raise ValueError("Choose a ready, self-contained 3D GLB")
            if name == "angelo_studio_export":
                require_anchored_files()
                if path.stat().st_size > MAX_CREATED_FILE:
                    raise StudioExportError("Agent exports must be at most 150 MiB, matching the bounded Studio model-output limit")
                target = self.controller.files.path(args["path"])
                if target.exists() or target.is_symlink() or target.suffix.lower() != path.suffix.lower():
                    raise ValueError("Choose a NEW workspace output path with the same file extension; no overwrites")
                signature = (signature, str(target))
        return signature

    def describe_asset(self, asset, preview=False):
        keys = {"id", "kind", "prompt", "model", "model_id", "connection", "folder", "status", "created", "progress", "error", "task_id", "preview_task_id", "options", "files", "reference_names"}
        result = {k: v for k, v in asset.items() if k in keys}
        result["automatically_imported"] = False  # Library operations never insert scene objects.
        if preview and asset.get("status") == "ready":
            filename = (asset.get("files") or [""])[0] if asset["kind"] == "IMAGE" else asset.get("thumbnail")
            if filename:
                try:
                    path = self.library.checked_file(asset, filename)
                    result["views"] = self.preview(path)
                except (ValueError, OSError, RuntimeError):
                    result["preview_note"] = "Preview unavailable or exceeds chat image bounds; saved output is retained"
        return result

    def execute(self, name, arguments):
        args = validate_studio(name, arguments)
        self.refresh()
        if name == "angelo_studio_catalog":
            entries = [dict(m, controls=capabilities(m["mode"], m["id"])) for m in self.catalog()]
            # Never expose credentials, profiles or runtime objects.
            return {"models": entries, "kinds": ["IMAGE", "3D"], "enhance": self._enhancer_status(), "enhancement_accounts": self.enhancement_accounts(),
                "billing": "Separate GenAI API accounts, NOT coding subscriptions", "concurrent_jobs_max": 4}
        if name == "angelo_studio_list":
            kind, folder, query = args.get("kind", "ALL"), args.get("folder", "ALL"), args.get("query", "")
            if folder not in {"ALL", "UNFILED"}:
                self.folder(folder)
            items = sorted((a for k in ([kind] if kind != "ALL" else ["IMAGE", "3D"])
                for a in self.library.visible(k, folder, query)), key=lambda a: a["created"], reverse=True)
            offset, limit = args.get("offset", 0), args.get("limit", 20)
            return {"assets": [{k: a.get(k) for k in ("id", "kind", "prompt", "model", "status", "progress", "folder")} for a in items[offset:offset + limit]],
                "total": len(items), "offset": offset, "more": offset + limit < len(items), "folders": list(self.library.folders.values())[:200]}
        if name == "angelo_studio_asset":
            return self.describe_asset(self.asset(args["asset_id"]), args.get("preview", True))
        if name == "angelo_studio_generate":
            choice, binding, options, refs = self.generation(args)
            if self.jobs.closed or len(self.jobs.active) >= 4:
                raise GenerationError("Wait for a generation to finish (four jobs maximum)")
            asset = self.library.add(choice["kind"] if "kind" in choice else ("3D" if choice["mode"] in {"tripo_api", "meshy_api"} else "IMAGE"),
                args["prompt"].strip(), choice["name"], choice["connection"], args.get("folder", ""),
                mode=choice["mode"], model_id=choice["id"], options=options, reference_names=[r["name"] for r in refs])
            try:
                self.jobs.start(asset, binding, options, refs)
            except Exception:
                self.library.update(dict(asset, status="failed", error="Job could not start; no automatic retry"))
                raise
            def poll():
                # This process already owns the worker's event stream. Do not
                # rescan/read the entire on-disk library on every 20 ms UI tick.
                self.jobs.pump()
                current = self.asset(asset["id"])
                if current["status"] not in {"ready", "failed", "stopped"}:
                    return None
                result = self.describe_asset(current, preview=True)
                if current["status"] != "ready":
                    result["error"] = current.get("error") or "Generation did not complete; no automatic retry"
                return result
            return StudioPending(poll, lambda: self.jobs.stop(asset["id"]), asset_id=asset["id"], account=choice["connection"])
        if name == "angelo_studio_enhance":
            binding = self.enhancer(args)
            cancel, events = threading.Event(), queue.Queue()
            def run():
                try:
                    result = {"original_prompt": args["prompt"], "enhanced_prompt": binding["run"](cancel),
                        "applied": False, "account": binding["label"], "note": "Review with the user before generating; original unchanged"}
                except GenerationError as error:
                    # Provider details in a GenerationError are already sanitized and bounded.
                    result = {"error": "Enhancement did not complete; original prompt unchanged. No automatic retry. " + str(error)[:400]}
                except Exception:
                    result = {"error": "Enhancement did not complete; original prompt unchanged. No automatic retry"}
                events.put(result)
            binding.get("started", lambda: None)()
            threading.Thread(target=run, daemon=True, name="AngeloChatEnhance").start()
            return StudioPending(lambda: events.get_nowait() if not events.empty() else None, cancel.set,
                account=binding.get("connection", ""), cleanup=binding.get("finished", lambda: None))
        if name == "angelo_studio_export":
            return self._export(args)
        self.precondition(name, args)
        if name == "angelo_studio_create_folder":
            return {"folder": self.library.folder(args["name"]), "blender_undoable": False}
        if name == "angelo_studio_import_image":
            ref = self.attachment(args["reference_id"])
            asset = self.library.import_image(ref["path"], args.get("folder", ""))
            asset["prompt"] = ref["name"]
            self.library.update(asset)
            return self.describe_asset(asset, preview=True)
        asset = self.asset(args["asset_id"])
        if name == "angelo_studio_move_asset":
            self.library.update(dict(asset, folder=args["folder"]))
            return {"asset_id": asset["id"], "folder": args["folder"], "blender_undoable": False}
        if name == "angelo_studio_stop":
            self.jobs.stop(asset["id"])
            return {"asset_id": asset["id"], "monitoring_stop_requested": asset["id"] in self.jobs.active,
                "remote_cancellation_guaranteed": False, "note": "The provider may continue processing and billing"}
        source = self.library.checked_file(asset, asset["files"][args.get("output_index", 0)])
        if name == "angelo_studio_import_3d":
            return self.import_model(source)
        raise ValueError("Unsupported Media Studio action")

    def _export(self, args):
        store, checkpoint = self.controller.checkpoints, self.controller.request_checkpoint
        if not checkpoint or not store.availability(checkpoint)["available"]:
            raise StudioExportError("File exports require a completed request snapshot; approve the export through the current chat")
        try:
            self.precondition("angelo_studio_export", args)
            asset = self.asset(args["asset_id"], ready=True)
            supplied = self.library.checked_file(asset, asset["files"][args.get("output_index", 0)])
            # Resolve only the configured library root, retaining descendant
            # components so anchored directory access rejects parent links.
            source = self.library.directory.resolve() / supplied.relative_to(self.library.directory)
            target = self.controller.files.path(args["path"])
            relative = str(target.relative_to(self.controller.files.root))
        except (OSError, ValueError) as error:
            raise StudioExportError(str(error)) from error
        with _transaction(store.root):
            before = store.load(checkpoint)
            window = read_timeline(store)["boundary"]
            prior = next((item for item in before["files"] if item["root"] == str(self.controller.files.root)
                          and item["path"] == relative and item.get("window") == window), None)
            entry = None
            try:
                with _private_directory(source.parent) as source_folder:
                    source_info = _file_info(source_folder, source.name)
                    if source_info.st_size > MAX_CREATED_FILE:
                        raise StudioExportError("Agent exports must be at most 150 MiB, matching the bounded Studio model-output limit")
                    with os.fdopen(_open_file(source_folder, source.name, os.O_RDONLY), "rb") as src:
                        opened = os.fstat(src.fileno())
                        if not _same_file(source_info, opened):
                            raise StudioExportError("The library output changed; inspect it before exporting")
                        if target.exists() or target.is_symlink():
                            raise StudioExportError("The workspace output appeared after review; no overwrite was performed")
                        # Persist the absent-target journal BEFORE any output
                        # creation. The profile transaction also owns rollback of
                        # unused entries; another chat cannot interleave changes.
                        store.journal(checkpoint, self.controller.files.root, args["path"], kind="studio_export", pending=True)
                        entry = next(item for item in store.load(checkpoint)["files"]
                                     if item["root"] == str(self.controller.files.root) and item["path"] == relative and item.get("window") == window)
                        if prior is None and entry["existed"]:
                            raise StudioExportError("The workspace output appeared during journal capture; no overwrite was performed")
                        with _private_directory(target.parent, create=True, within=self.controller.files.root) as destination:
                            temporary = "angelo-export-" + str(uuid.uuid4()) + ".tmp"
                            created, published = None, False
                            try:
                                descriptor = _open_file(destination, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                                created = os.fstat(descriptor)
                                with os.fdopen(descriptor, "wb") as dst:
                                    copied = _copy_export(src, dst)
                                    dst.flush()
                                    os.fsync(dst.fileno())
                                finished = os.fstat(src.fileno())
                                if (opened.st_size, opened.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns) or copied != opened.st_size:
                                    raise StudioExportError("The library output changed while copying; no complete export was retained")
                                current = self.controller.files.path(args["path"])
                                if current != target or current.exists() or current.is_symlink():
                                    raise StudioExportError("The workspace output appeared while copying; no overwrite was performed")
                                held = directory_info(destination)
                                if not isinstance(destination, Path) and (target.parent.stat().st_dev, target.parent.stat().st_ino) != (held.st_dev, held.st_ino):
                                    raise StudioExportError("The workspace directory changed while copying; no output was published")
                                if not _same_file(created, _output_info(destination, temporary)):
                                    raise StudioExportError("The staged export changed during copying; inspect the workspace")
                                _publish_export(destination, temporary, target.name)
                                published = True
                                _unlink_file(destination, temporary)
                                temporary = ""
                                sync_directory(destination)
                                store.commit_journal(checkpoint, entry)
                            except (OSError, ValueError) as error:
                                return self._export_failed(args, relative, store, checkpoint, entry, prior,
                                    destination, temporary, created, published, error)
                return {"success": True, "asset_id": asset["id"], "path": args["path"], "bytes": copied,
                    "journaled": True, "request_checkpoint": checkpoint, "blender_undoable": False,
                    "scene_changed": False, "file_changes": {"written": [args["path"]], "deleted": []}}
            except (OSError, ValueError) as error:
                return self._export_failed(args, relative, store, checkpoint, entry, prior, None, "", None, False, error)

    def _export_failed(self, args, relative, store, checkpoint, entry, prior, folder, temporary, created, published, error):
        remaining, changed, cleanup_error = False, False, ""
        if created is not None and not published:
            try:
                published = _same_file(created, _output_info(folder, Path(args["path"]).name))
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as failure:
                cleanup_error = "The output publication could not be inspected: " + str(failure)
        if created is not None and temporary:
            try:
                current = _output_info(folder, temporary)
                if _same_file(created, current):
                    _unlink_file(folder, temporary)
                else:
                    changed = True  # Never delete a replacement owned by someone else.
            except FileNotFoundError:
                changed = True
            except (OSError, ValueError) as failure:
                remaining, cleanup_error = True, str(failure)
        if entry is None:
            try:
                entry = next((item for item in store.load(checkpoint)["files"]
                              if item["root"] == str(self.controller.files.root) and item["path"] == relative
                              and item.get("window") == read_timeline(store)["boundary"]), None)
            except (OSError, ValueError) as failure:
                cleanup_error = "The export journal could not be inspected: " + str(failure)
        if entry is not None and not published:
            try:
                store.rollback_journal(checkpoint, entry, prior)
                entry = prior
            except (OSError, ValueError) as failure:
                cleanup_error = "The unused export journal could not be released: " + str(failure)
        elif entry is not None and published:
            try:
                entry = store.commit_journal(checkpoint, entry)
            except (OSError, ValueError) as failure:
                cleanup_error = "Published output retained; journal confirmation needs review: " + str(failure)
        partial = bool(published or remaining or changed or cleanup_error)
        result = {"success": False, "error": str(error), "asset_id": args["asset_id"], "path": args["path"],
            "journaled": entry is not None if not cleanup_error else None, "request_checkpoint": checkpoint, "blender_undoable": False,
            "scene_changed": False, "partial_changes_possible": partial,
            "file_changes": {"written": [args["path"]] if published else [], "deleted": []}}
        if remaining or changed:
            result["temporary_file"] = str(Path(args["path"]).with_name(temporary))
        if partial:
            result["recovery"] = "Inspect the workspace and request file journal before restoring or retrying; export cleanup could not verify an unchanged output path."
        if cleanup_error:
            result["cleanup_error"] = cleanup_error
        return result

    def _enhancer_status(self):
        try:
            binding = self.enhancer({"prompt": "", "kind": "IMAGE", "generation_model": ""})
            return {"ready": True, "account": binding["label"]}
        except ValueError as error:
            return {"ready": False, "notice": str(error)}
