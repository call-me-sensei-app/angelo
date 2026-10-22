# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
<#
.SYNOPSIS
  Get-AuthenticodePeDigest: a PE file's Authenticode digest (SHA-256), the hash a signature covers.

.DESCRIPTION
  Signing a PE file (.exe, .dll, .pyd) changes three things only: the optional header's CheckSum, the certificate
  table's data-directory entry, and the certificate table appended at the end (after zero padding to 8 bytes). This digest
  hashes everything else in file order: the file without those three, its end padded to a multiple of 8 bytes. That
  is stricter than the image hash of Microsoft's Authenticode specification ("Calculating the PE Image Hash"), which
  skips bytes outside the sections; it is compared only between an unsigned file and its signed copy, never with a
  signature's own digest. So a signed file whose
  digest equals the unsigned original's differs from it by its signature alone: no code, resource or data changed.
  The release's remote signing checks that for every file a signer returns (sign_windows.ps1).

  Refuses a file that is not PE, that has no certificate-table directory entry (fewer than 5 data directories), or
  whose certificate table is not where signing puts it (at the very end).
  Dot-source this file; it defines the function and nothing else.
#>

if (-not ("Angelo.PeDigest" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Security.Cryptography;

namespace Angelo {
    public static class PeDigest {
        static void Add(SHA256 hash, byte[] data, long start, long end) {
            if (end > start) hash.TransformBlock(data, (int)start, (int)(end - start), null, 0);
        }

        public static string Sha256(string path) {
            byte[] data = File.ReadAllBytes(path);
            if (data.Length < 0x40 || data[0] != (byte)'M' || data[1] != (byte)'Z') {
                throw new InvalidDataException(path + " is not a PE file (no MZ header)");
            }
            long pe = BitConverter.ToUInt32(data, 0x3C);
            if (pe + 24 + 2 > data.Length || data[pe] != (byte)'P' || data[pe + 1] != (byte)'E' || data[pe + 2] != 0 ||
                data[pe + 3] != 0) {
                throw new InvalidDataException(path + " is not a PE file (no PE signature)");
            }
            long optional = pe + 24;
            ushort magic = BitConverter.ToUInt16(data, (int)optional);
            long directories;
            if (magic == 0x10b) { directories = optional + 96; }        // PE32
            else if (magic == 0x20b) { directories = optional + 112; }  // PE32+
            else { throw new InvalidDataException(path + " has an unknown optional header (magic 0x" + magic.ToString("x") + ")"); }
            ushort optionalSize = BitConverter.ToUInt16(data, (int)(pe + 20));
            uint directoryCount = BitConverter.ToUInt32(data, (int)(directories - 4));
            if (directoryCount < 5 || optionalSize < (directories - optional) + 5 * 8) {
                throw new InvalidDataException(path + " has no certificate table entry (" + directoryCount + " data directories)");
            }
            long checksum = optional + 64;
            long certificateEntry = directories + 4 * 8;   // IMAGE_DIRECTORY_ENTRY_SECURITY
            if (certificateEntry + 8 > data.Length) { throw new InvalidDataException(path + " is truncated"); }
            long tableOffset = BitConverter.ToUInt32(data, (int)certificateEntry);
            long tableSize = BitConverter.ToUInt32(data, (int)(certificateEntry + 4));
            long end = data.Length;
            if (tableSize != 0) {
                if (tableOffset < certificateEntry + 8 || tableOffset + tableSize != data.Length) {
                    throw new InvalidDataException(path + ": the certificate table is not at the end of the file");
                }
                end = tableOffset;
            }
            using (SHA256 hash = SHA256.Create()) {
                Add(hash, data, 0, checksum);
                Add(hash, data, checksum + 4, certificateEntry);
                Add(hash, data, certificateEntry + 8, end);
                byte[] padding = new byte[(8 - end % 8) % 8];
                hash.TransformFinalBlock(padding, 0, padding.Length);
                return BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
            }
        }
    }
}
"@
}

function Get-AuthenticodePeDigest([string]$Path) {
    return [Angelo.PeDigest]::Sha256((Resolve-Path -LiteralPath $Path).ProviderPath)
}
