# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Selected files and native images: private snapshots and provider hand-over."""

import base64
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from . import child_process

MAX_IMAGES = 4
MAX_TOOL_IMAGES = 6
MAX_BYTES = 5 * 1024 * 1024
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
TEXT_TYPES = frozenset({".txt", ".md", ".rst", ".py", ".json", ".csv", ".yaml", ".yml", ".obj", ".mtl", ".svg", ".gltf"})
ASSET_TYPES = frozenset({".blend", ".glb", ".fbx", ".stl"})
FILE_TYPES = frozenset(MIME) | TEXT_TYPES | ASSET_TYPES | {".pdf"}
FILE_FILTER = ";".join("*" + suffix for suffix in sorted(FILE_TYPES))
PDF_SECONDS = 30
# What angelo_read_reference returns while a PDF it reads is extracted off Blender's main thread.
PDF_PENDING_NOTE = ("This PDF's text is being extracted off Blender's main thread (up to 30 s); "
                    "call angelo_read_reference again for it. Nothing else is needed.")


class TextFailure(str):
    """A file's cached extraction failure (``ReferenceStore.content``): the error it raises again."""


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("Select a PNG, JPEG or WebP image")


class ReferenceStore:
    def __init__(self, root, session_id, image_limit=MAX_BYTES):
        self.directory = Path(root) / "references" / str(uuid.UUID(session_id))
        self.image_limit = image_limit
        self._texts = {}      # (stem, size, mtime_ns) -> extracted text, or its TextFailure.
        self._extracting = {}  # The same keys -> threading.Event, while another thread extracts that file.
        self._lock = threading.Lock()
        self._observations = {}

    def import_images(self, paths, existing=()):
        if not paths or len(existing) + len(paths) > MAX_IMAGES:
            raise ValueError("Attach up to four files per message")
        # Validate every input before writing any, so a failed picker submission
        # doesn't leave a half-attached selection.
        loaded = []
        for source in paths:
            source = Path(source)
            if not source.is_file() or source.is_symlink():
                raise ValueError("Choose a regular image file, not a link")
            suffix = source.suffix.lower()
            if suffix not in FILE_TYPES:
                raise ValueError("Supported attachments: images, text/code, PDF and Blender/3D assets")
            limit = self.image_limit if suffix in MIME else 20 * 1024 * 1024
            with source.open("rb") as handle:
                data = handle.read(limit + 1)
            if not data or len(data) > limit:
                raise ValueError(f"Images are limited to {self.image_limit // (1024 * 1024)} MiB; other reference files to 20 MiB")
            if suffix in MIME:
                suffix = image_type(data)
            elif suffix in TEXT_TYPES:
                try:
                    data.decode("utf-8-sig")
                except UnicodeError as error:
                    raise ValueError("Text references must be UTF-8") from error
            loaded.append((source.name[:160], data, suffix))
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        snapshots = []
        try:
            for name, data, suffix in loaded:
                path = self.directory / (str(uuid.uuid4()) + suffix)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                snapshots.append({"path": str(path), "name": name, "mime": MIME.get(suffix, "application/octet-stream"),
                                  "kind": "image" if suffix in MIME else "text" if suffix in TEXT_TYPES else "pdf" if suffix == ".pdf" else "asset",
                                  "id": path.stem, "bytes": len(data)})
        except Exception:
            for reference in snapshots:
                Path(reference["path"]).unlink(missing_ok=True)
            raise
        return snapshots

    def new_observation(self, name):
        """Reserve a private generated view; never accept a model-supplied path."""
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / (str(uuid.uuid4()) + '.png')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        return {"path": str(path), "id": path.stem, "name": name, "mime": "image/png", "kind": "image", "bytes": 0}

    def discard_observation(self, reference):
        self._observations.pop(reference.get('id'), None)
        path = self._own_file(reference)
        if not path.is_symlink():
            uuid.UUID(path.stem)
            path.unlink(missing_ok=True)

    def remember_observation(self, reference, camera):
        self.checked_path(reference)
        self._observations[reference['id']] = dict(camera)
        # Each camera may own a 768-square native depth buffer. Bound that
        # memory (~34 MiB for depth and silhouette at maximum resolution), not just the number of PNGs.
        while len(self._observations) > 12:
            self._observations.pop(next(iter(self._observations)))

    def observation_camera(self, identity):
        # Runtime-only camera authority. Restart/old observations require a
        # fresh native view; user attachments and arbitrary JSON are not cameras.
        camera = self._observations.pop(identity, None)
        if camera is None:
            raise ValueError('Take a fresh native sculpt view in this chat before targeting its pixels')
        self._observations[identity] = camera  # Keep an actively reused comparison baseline alive.
        return dict(camera)

    def tool_images(self, result):
        """Only admitted native observation UUIDs, never arbitrary output paths."""
        records = result.get('views', [])
        # Three views, or angelo_view's six reference pairs (view_spec.MAX_PAIRS), each one picture.
        if not isinstance(records, list) or len(records) > MAX_TOOL_IMAGES:
            raise ValueError('A native observation returns at most %d images' % MAX_TOOL_IMAGES)
        for record in records:
            self.checked_path(record)
        return records

    def attached_image(self, reference_id):
        """The file of an image in this conversation's folder by its id (an attachment's reference_id).

        Only a canonical UUID and this folder's own regular PNG, JPEG or WebP file of at most the image
        limit; ValueError otherwise, never a path of the caller's making.
        """
        try:
            stem = str(uuid.UUID(reference_id))
        except (ValueError, AttributeError, TypeError):
            raise ValueError("Not an image reference_id") from None
        for suffix in sorted(MIME):
            path = self.directory / (stem + suffix)
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > self.image_limit:
                raise ValueError("That image is larger than the %d MiB an attachment may be"
                                 % (self.image_limit // (1024 * 1024)))
            return path
        raise ValueError("No image with that reference_id is attached to this conversation")

    def _own_file(self, reference):
        """The record's file inside THIS conversation's folder, located by its file name.

        A record's stored folder is not trusted: it is the absolute folder at
        attach time, which changes when the state folder moves (as the
        pre-rename state folder did, see state_migration). Only the name is
        used, so no record can point outside this folder.
        """
        name = Path(str(reference.get("path", ""))).name
        if not name or name in {".", ".."}:
            raise ValueError("This image reference is unavailable; attach it again")
        return self.directory / name

    def checked_path(self, reference):
        path = self._own_file(reference)
        if path.is_symlink() or not path.is_file():
            raise ValueError("This image reference is unavailable; attach it again")
        uuid.UUID(path.stem)
        if path.suffix not in FILE_TYPES or path.stat().st_size > (self.image_limit if path.suffix in MIME else 20 * 1024 * 1024):
            raise ValueError("Invalid image reference")
        return path

    def codex_inputs(self, references):
        return [{"type": "localImage", "path": str(self.checked_path(r))} for r in references if r.get("mime") in MIME.values()]

    def claude_inputs(self, references):
        return [{"type": "image", "source": {"type": "base64", "media_type": MIME[path.suffix],
                 "data": base64.b64encode(path.read_bytes()).decode("ascii")}}
                for r in references if r.get("mime") in MIME.values() for path in [self.checked_path(r)]]

    def content(self, reference, *, block=True):
        """The reference's text ('' for images and assets), extracted once per file, failures included.

        Cached by file identity (snapshot name, size, modification time), so a
        PDF whose extraction failed or timed out is not tried again every turn;
        an attachment is a fresh snapshot. A missing ``pdftotext`` or an OS
        error is not the file's failure and is not cached. ``block=False``
        (Blender's main thread) never runs ``pdftotext``: a PDF not extracted
        yet starts its extraction on a background thread and raises
        ``ReferencePending``.
        """
        path = self.checked_path(reference)
        stat = path.stat()
        key, pdf = (path.stem, stat.st_size, stat.st_mtime_ns), path.suffix == ".pdf"
        if pdf and key not in self._texts and not shutil.which("pdftotext"):
            raise ValueError("PDF text extraction requires installed pdftotext; attach text or page images instead")
        with self._lock:
            running = None if key in self._texts else self._extracting.get(key)
            start = key not in self._texts and running is None
            if start:
                running = self._extracting[key] = threading.Event()
        if start and pdf and not block:
            threading.Thread(target=self._extract, args=(key, path, running), name="Angelo PDF text",
                             daemon=True).start()
        elif start:
            error = self._extract(key, path, running)
            if error is not None:
                raise error
        if running is not None and not running.wait(PDF_SECONDS * 2 if block else 0):
            raise ReferencePending(PDF_PENDING_NOTE)
        value = self._texts.get(key)
        if value is None:   # Another thread's read ended on an OS error, which is not cached.
            raise ReferencePending(PDF_PENDING_NOTE) if not block else ValueError(
                "This reference couldn't be read; try again or attach it again")
        if isinstance(value, TextFailure):
            raise ValueError(str(value))
        return value

    def extracting(self):
        """Whether any file's text is being extracted on another thread."""
        with self._lock:
            return bool(self._extracting)

    def _extract(self, key, path, event):
        """Extract on this thread, caching the text or the file's own failure; an OS error is returned, uncached."""
        value = error = None
        try:
            value = _file_text(path)
        except ValueError as failure:
            value = TextFailure(str(failure))
        except subprocess.TimeoutExpired:
            value = TextFailure("Extracting this PDF's text timed out after %d s; attach readable text or page "
                                "images instead" % PDF_SECONDS)
        except OSError as failure:
            error = failure
        finally:
            with self._lock:
                if value is not None:
                    self._texts[key] = value
                self._extracting.pop(key, None)
            event.set()
        return error

    def prime(self, messages):
        """Extract the PDFs these messages attached (provider worker, before a turn connects).

        The tool read then finds them without extracting on Blender's main
        thread. Missing or failing files never block an unrelated message.
        """
        for message in messages:
            for reference in message.get("references", ()):
                if reference.get("kind") == "pdf":
                    try:
                        self.content(reference)
                    except (OSError, ValueError):
                        pass

    def prompt_context(self, references):
        parts = []
        for reference in references:
            path = self.checked_path(reference)
            if path.suffix in MIME:
                continue
            value = self.content(reference)
            parts.append({"reference": path.stem, "name": reference["name"], "kind": reference.get("kind"),
                          "path": str(path), "text_excerpt": value[:12000], "text_length": len(value),
                          "truncated": len(value) > 12000})
        return parts

    def read(self, reference, start=0, length=32000, *, block=True):
        try:
            value = self.content(reference, block=block)
        except ReferencePending as pending:
            return {"reference": reference.get("id"), "name": reference["name"], "kind": reference.get("kind"),
                    "ready": False, "note": str(pending)}
        if not value:
            return {"reference": reference.get("id"), "name": reference["name"], "kind": reference.get("kind"),
                    "path": str(self.checked_path(reference)), "bytes": reference["bytes"],
                    "note": "Image pixels are sent with the user turn. Binary 3D assets are attached, not auto-imported; Blender Python import still requires exact-code approval."}
        return {"reference": reference.get("id"), "text": value[start:start + length], "text_length": len(value),
                "start": start, "truncated": start + length < len(value)}

    def remove(self, reference):
        path = self.checked_path(reference)
        path.unlink()
        with self._lock:
            for key in [key for key in self._texts if key[0] == path.stem]:
                del self._texts[key]


class ReferencePending(ValueError):
    """A PDF's text is being extracted on another thread; read it again shortly."""


def _file_text(path):
    """A reference file's text: UTF-8 text, a PDF's first 64 pages through pdftotext, or '' for others."""
    if path.suffix in TEXT_TYPES:
        return path.read_text(encoding="utf-8-sig")[:1000000]
    if path.suffix != ".pdf":
        return ""
    executable = shutil.which("pdftotext")
    if not executable:
        raise OSError("PDF text extraction requires installed pdftotext")   # Not the file's failure: not cached.
    # Never on Blender's UI thread (ReferenceStore.content). No PDF scripts,
    # browsing, OCR or automatic scene import are performed.
    output = child_process.run([executable, "-f", "1", "-l", "64", "-enc", "UTF-8", str(path), "-"],
                               capture_output=True, timeout=PDF_SECONDS, check=False)
    if output.returncode:
        raise ValueError("Couldn't extract this PDF's text; attach readable text or page images")
    value = output.stdout.decode("utf-8", errors="replace")[:1000000]
    if not value.strip():
        raise ValueError("This PDF contains no extractable text; attach page images for visual reference")
    return value


def codex_result_images(context, result):
    """Pack pictures without claiming delivery; successful Codex writes stay unconfirmed."""
    records = context.references.tool_images(result)
    items = [{"type": "inputImage", "imageUrl": "data:" + record.get("mime", "image/png") + ";base64," + base64.b64encode(
        context.references.checked_path(record).read_bytes()).decode("ascii")} for record in records]
    return items


# What the providers call. ``context`` is the request's RuntimeContext (or a test's stand-in without the hooks).

def _record(context, records, staged):
    record = getattr(context, "record_images", None)
    if callable(record):
        record(records, staged)


def claude_inputs(context, references):
    """Image blocks of a Claude round's message, recorded for the provider turn that accepts it."""
    _record(context, references, True)
    return context.references.claude_inputs(references)


def codex_inputs(context, attachments):
    """The local images of a Codex turn's first message, recorded for the provider turn that accepts it."""
    _record(context, attachments, True)
    return context.references.codex_inputs(attachments)


def record_codex_result(context, result, content, *, scope, params, delivery_owner=None):
    """Record a successful tool-result write without claiming model image delivery."""
    # Code mode can return these URLs without calling image().
    # Neither tool completion nor later model output proves
    # forwarding, so a successful send remains unconfirmed.
    with scope.lock:
        if delivery_owner is not None and (scope.delivery_owner is not delivery_owner
                                          or not delivery_owner.current(getattr(context, "host", None))):
            return
        count = sum(item.get("type") == "inputImage" for item in content)
        record = getattr(context, "record_unconfirmed_images", None)
        if count and callable(record) and scope.matches(context, params, allow_completed=True):
            record(context.references.tool_images(result))
