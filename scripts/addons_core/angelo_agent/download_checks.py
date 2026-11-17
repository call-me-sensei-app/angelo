# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_download``'s checks (doc/angelo/downloads.rst): which addresses a download may reach, what a file is from
its own bytes, and the name it is saved under. Standard library only; no bpy, no network.

* **Addresses** (``refused_address``): only public ones. Loopback, private, link-local (169.254.x.x, where clouds
  serve instance metadata), shared address space (CGNAT), multicast, reserved and unspecified addresses, IPv6
  unique-local and link-local, and an IPv4 address carried inside IPv6 (mapped, 6to4, NAT64) of any of those are
  refused: a link must never reach this computer, its network or a cloud's metadata service.
* **Types** (``sniff``): a file is what its first bytes say, never what the server's header or the link's name says.
  Each kind takes its own formats; a program or script (Windows, Linux and macOS executables, a ``#!`` script, a
  shortcut or an installer) is refused for every kind, and an HTML page (a sign-in or error page instead of the file)
  is not a file.
* **Names** (``safe_name``): the person's or agent's name, else the link's, else the server's (only a hint), cleaned
  to one plain file name: no folder, no hidden or reserved device name, no control or direction characters; the
  extension is the checked type's.
"""

import ipaddress
import re
import struct
import unicodedata
from urllib.parse import unquote, urlsplit

_SHARED = ipaddress.ip_network("100.64.0.0/10")
_UNIQUE_LOCAL = ipaddress.ip_network("fc00::/7")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _embedded(ip):
    """The IPv4 address an IPv6 address carries (mapped, 6to4, NAT64, Teredo's client), or None."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.teredo is not None:
        return ip.teredo[1]
    return None


def refused_address(text):
    """None for a public address, else the class that refuses it: ``loopback``, ``private``, ``link_local``,
    ``shared``, ``multicast``, ``unique_local``, ``reserved``, ``unspecified`` or ``not_an_address``."""
    try:
        ip = ipaddress.ip_address(text)
    except (TypeError, ValueError):
        return "not_an_address"
    if ip.version == 6:
        inner = _embedded(ip)
        if inner is not None:
            return refused_address(str(inner))
        if ip in _UNIQUE_LOCAL:
            return "unique_local"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.version == 4 and ip in _SHARED:
        return "shared"
    if ip.is_reserved:
        return "reserved"
    if ip.is_private or (ip.version == 6 and ip.is_site_local):
        return "private"
    return None if ip.is_global else "reserved"


# Types -----------------------------------------------------------------------------------------------------------

_PROGRAMS = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
             b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"#!", b"L\x00\x00\x00\x01\x14\x02\x00",
             b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"\x00asm", b"dex\n")
_HTML = re.compile(rb"^\s*(<!doctype\s+html|<html|<head|<body|<script|<meta|<title)", re.I)
_OBJ_LINE = re.compile(r"^(v|vt|vn|vp|f|l|p|o|g|s|mtllib|usemtl|cstype|deg|curv|curv2|surf|parm|trim|hole|end|bmat|"
                       r"step|call|csh|maplib|usemap|lod|shadow_obj|trace_obj)(\s|$)|^#")
EXTENSIONS = {"jpg": ".jpg", "tiff": ".tif", "usdc": ".usdc", "usda": ".usda", "usdz": ".usdz", "bin": ".bin"}


def _text(head):
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError as error:   # A cut in the middle of a character at the end of the head is fine.
        if error.start < len(head) - 4:
            return None
        return head[:error.start].decode("utf-8")


def _image(head, size):
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:2] == b"BM" and len(head) >= 26 and (size is None or struct.unpack("<I", head[2:6])[0] == size):
        return "bmp"
    if head[:12] == b"\xabKTX 20\xbb\r\n\x1a\n":
        return "ktx2"
    if head[:4] == b"DDS ":
        return "dds"
    return _hdri(head, size)


def _hdri(head, size):
    if head[:4] == b"\x76\x2f\x31\x01":
        return "exr"
    if head.startswith((b"#?RADIANCE", b"#?RGBE")):
        return "hdr"
    return None


def _zip_first_name(head):
    if head[:4] != b"PK\x03\x04" or len(head) < 30:
        return ""
    length = struct.unpack("<H", head[26:28])[0]
    return head[30:30 + length].decode("utf-8", "replace").lower()


def _model(head, size):
    if head[:4] == b"glTF" and len(head) >= 8 and struct.unpack("<I", head[4:8])[0] == 2:
        return "glb"
    if head.startswith(b"Kaydara FBX Binary  \x00"):
        return "fbx"
    if head.startswith((b"BLENDER", b"\x28\xb5\x2f\xfd")):   # A .blend, plain or Zstandard-compressed (Blender 3+).
        return "blend"
    if head.startswith(b"PXR-USDC"):
        return "usdc"
    if _zip_first_name(head).endswith((".usdc", ".usda", ".usd")):
        return "usdz"
    if head.startswith(b"Ogawa"):
        return "abc"
    if head.startswith((b"ply\n", b"ply\r\n")):
        return "ply"
    text = _text(head[:65536])
    if text is not None:
        stripped = text.lstrip("﻿ \t\r\n")
        if stripped.startswith("#usda "):
            return "usda"
        if stripped.startswith("; FBX"):
            return "fbx"
        if stripped.startswith("{") and '"asset"' in stripped:
            return "gltf"
        if stripped.startswith(("<?xml", "<COLLADA")) and "<COLLADA" in stripped[:4096]:
            return "dae"
        if stripped.startswith("solid") and ("facet" in stripped or "endsolid" in stripped):
            return "stl"
        lines = [line.strip() for line in stripped.splitlines()[:-1] or stripped.splitlines()]
        lines = [line for line in lines if line]
        if lines and any(line.startswith("v ") for line in lines) and all(_OBJ_LINE.match(line) for line in lines):
            return "obj"
    if len(head) >= 84:   # A binary STL: an 80-byte header, a triangle count, 50 bytes a triangle.
        count = struct.unpack("<I", head[80:84])[0]
        if size is None or size == 84 + 50 * count:
            return "stl"
    return None


def _audio(head, size):
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[4:8] == b"ftyp" and head[8:12] in (b"M4A ", b"M4B ", b"mp42", b"isom", b"mp41"):
        return "m4a"
    # An MPEG audio frame: 11 sync bits, a layer, a bitrate and a sample rate that exist (a UTF-16 mark, FF FE,
    # has no valid bitrate after it as text).
    if head[:3] == b"ID3" or (len(head) > 2 and head[0] == 0xFF and head[1] & 0xE6 in (0xE2, 0xE4, 0xE6)
                              and head[2] >> 4 not in (0, 15) and (head[2] >> 2) & 3 != 3):
        return "mp3"
    return None


def _other(head, size):
    for reader in (_image, _model, _audio):
        found = reader(head, size)
        if found is not None and found != "stl":   # A binary STL has no mark: any data could pass for one.
            return found
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if head[:2] == b"\x1f\x8b":
        return "gz"
    return "bin"


_READERS = {"image": _image, "texture": _image, "hdri": _hdri, "model": _model, "audio": _audio, "other": _other}


def sniff(kind, head, size=None):
    """The format ``head`` (the file's first bytes; ``size`` its whole length once known) has for ``kind``, or None
    when it is not that kind, is a program or script, or is an HTML page."""
    if not head or head.startswith(_PROGRAMS) or _HTML.match(head[:1024]):
        return None
    return _READERS[kind](head, size)


def extension(fmt):
    return EXTENSIONS.get(fmt, "." + fmt)


# Names -----------------------------------------------------------------------------------------------------------

_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {"%s%d" % (p, n) for p in ("COM", "LPT")
                                                                  for n in range(10)}
_PROGRAM_EXTENSIONS = {"exe", "com", "bat", "cmd", "scr", "pif", "msi", "msp", "ps1", "psm1", "vbs", "vbe", "js",
                       "jse", "wsf", "wsh", "hta", "cpl", "lnk", "reg", "dll", "sys", "sh", "bash", "zsh", "command",
                       "app", "pkg", "py", "pyc", "pyw", "pl", "rb", "jar", "so", "dylib", "elf", "run", "bin"}
_ALLOWED = set(" -_()[]+,'&")


def _clean(hint):
    """``hint`` as a file stem: its last path part, normalized, without control, direction or separator characters,
    hidden dots or a program's extension; "" when nothing is left."""
    if not isinstance(hint, str):
        return ""
    base = re.split(r"[/\\]", unicodedata.normalize("NFC", hint[:1000]))[-1]
    base = "".join(c for c in base if unicodedata.category(c)[0] not in "CZ" or c == " ")
    base = "".join(c if c.isalnum() or c in _ALLOWED or c == "." else "_" for c in base).strip(" .")
    stem, dot, ext = base.rpartition(".")
    if dot and 0 < len(ext) <= 8 and ext.isalnum():
        base = stem
        while "." in base and base.rpartition(".")[2].lower() in _PROGRAM_EXTENSIONS:
            base = base.rpartition(".")[0]
    base = re.sub(r"\s+", " ", base).strip(" ._")[:100].rstrip(" .")
    if base.split(".")[0].upper() in _RESERVED:
        base = "_" + base
    return base


def safe_name(fmt, *hints):
    """A plain file name: the first hint that leaves a name, else "download", with ``fmt``'s extension."""
    stem = next((cleaned for cleaned in map(_clean, hints) if cleaned), "download")
    return stem + extension(fmt)


def disposition_name(header):
    """The file name a ``Content-Disposition`` header proposes, as sent (a hint for ``safe_name``, never a path)."""
    if not isinstance(header, str):
        return ""
    found = re.search(r"filename\*\s*=\s*([\w!#$%&+^`{}~-]+)'[^']*'([^;\s]+)", header, re.I)
    if found:
        try:
            return unquote(found.group(2), encoding=found.group(1), errors="replace")[:1000]
        except LookupError:
            return ""
    found = re.search(r'filename\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;\s]+))', header, re.I)
    if not found:
        return ""
    return (found.group(1) if found.group(1) is not None else found.group(2)).replace('\\"', '"')[:1000]


def link_name(url):
    """The last part of the link's path, decoded (a hint for ``safe_name``)."""
    try:
        path = urlsplit(url).path
    except ValueError:
        return ""
    return unquote(path.rsplit("/", 1)[-1])[:1000]
