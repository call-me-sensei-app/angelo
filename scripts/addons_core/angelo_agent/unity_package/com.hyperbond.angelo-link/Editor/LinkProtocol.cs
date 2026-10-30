// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Angelo's engine live-link wire format, proofs and endpoint record, as Angelo's engine_link_protocol.py defines
// them (doc/angelo/engine_link.rst, "Transport"; doc/angelo/unity_link.rst for this package):
//
// * Frames: a 4-byte big-endian length, a UTF-8 JSON header of that length, then header.bytes bytes of payload.
// * Authentication, mutual: HMAC-SHA256(token, context + "\n" + server nonce + "\n" + client nonce) with the
//   contexts "<protocol>/client" and "<protocol>/server". The token never crosses the socket.
// * The endpoint record: a small JSON file Angelo writes owner-only while the link is on.
//
// No UnityEngine here: the protocol harness (tests/…/unity_link_e2e/Harness.cs) compiles this file outside Unity.

using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace Hyperbond.AngeloLink
{
    public static class LinkProtocol
    {
        public const string Protocol = "angelo-link/1";
        public const string RecordFile = "angelo-link.json";
        public const string RecordVariable = "ANGELO_LINK_RECORD";
        public const string Engine = "unity";   // The auth frame's client.engine.
        public const int MaxHeader = 64 * 1024;
        public const int MaxPayload = 512 * 1024 * 1024;
        public const int MaxRecord = 4096;
        public const int NonceHex = 64;

        /// <summary>Where Angelo writes its endpoint record: the override variable, else Angelo's per-user data
        /// folder (the same rule as Angelo's storage.data_directory and the Godot plugin).</summary>
        public static string RecordPath()
        {
            var overridden = Environment.GetEnvironmentVariable(RecordVariable);
            if (!string.IsNullOrEmpty(overridden))
                return overridden;
            var home = Environment.GetEnvironmentVariable("HOME") ?? "";
            if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
                return Path.Combine(home, "Library", "Application Support", "Angelo", RecordFile);
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
            {
                var local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
                if (string.IsNullOrEmpty(local))
                    local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                return Path.Combine(local, "Angelo", RecordFile);
            }
            var state = Environment.GetEnvironmentVariable("XDG_STATE_HOME");
            if (string.IsNullOrEmpty(state))
                state = Path.Combine(home, ".local", "state");
            return Path.Combine(state, "Angelo", RecordFile);
        }

        /// <summary>The record at <paramref name="path"/> when it is a small file this protocol understands,
        /// else null (Angelo isn't listening, or it's another Angelo's newer protocol).</summary>
        public static Dictionary<string, object> ReadRecord(string path)
        {
            try
            {
                var info = new FileInfo(path);
                if (!info.Exists || info.Length > MaxRecord || (info.Attributes & FileAttributes.ReparsePoint) != 0)
                    return null;
                var record = MiniJson.ParseObject(File.ReadAllText(path, Encoding.UTF8));
                if (!(record.TryGetValue("protocol", out var protocol) && protocol as string == Protocol))
                    return null;
                if (!(record.TryGetValue("port", out var port) && port is long number && number > 0 && number < 65536))
                    return null;
                if (!(record.TryGetValue("token", out var token) && token is string hex && IsHex(hex, 64)))
                    return null;
                return record;
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException
                                          || error is FormatException)
            {
                return null;
            }
        }

        public static string Text(IDictionary<string, object> header, string key, string fallback = "")
        {
            return header != null && header.TryGetValue(key, out var value) && value is string text ? text : fallback;
        }

        public static long Number(IDictionary<string, object> header, string key, long fallback = 0)
        {
            return header != null && header.TryGetValue(key, out var value) && value is long number ? number : fallback;
        }

        /// <summary>One frame's bytes. Clients never send a payload (Angelo's server closes on one).</summary>
        public static byte[] Encode(IDictionary<string, object> header)
        {
            var body = Encoding.UTF8.GetBytes(MiniJson.Write(header));
            if (body.Length == 0 || body.Length > MaxHeader)
                throw new FormatException("A header is 1-" + MaxHeader + " bytes");
            var frame = new byte[4 + body.Length];
            frame[0] = (byte)(body.Length >> 24);
            frame[1] = (byte)(body.Length >> 16);
            frame[2] = (byte)(body.Length >> 8);
            frame[3] = (byte)body.Length;
            Buffer.BlockCopy(body, 0, frame, 4, body.Length);
            return frame;
        }

        public static string NewNonce()
        {
            var bytes = new byte[NonceHex / 2];
            using (var random = RandomNumberGenerator.Create())
                random.GetBytes(bytes);
            return ToHex(bytes);
        }

        public static bool ValidNonce(object value)
        {
            return value is string text && IsHex(text, NonceHex);
        }

        public static string ClientProof(byte[] token, string serverNonce, string clientNonce)
        {
            return Proof(Protocol + "/client", token, serverNonce, clientNonce);
        }

        public static string ServerProof(byte[] token, string serverNonce, string clientNonce)
        {
            return Proof(Protocol + "/server", token, serverNonce, clientNonce);
        }

        static string Proof(string context, byte[] token, string serverNonce, string clientNonce)
        {
            if (token == null || token.Length != 32)
                throw new ArgumentException("The link token is 32 bytes");
            using (var hmac = new HMACSHA256(token))
                return ToHex(hmac.ComputeHash(Encoding.ASCII.GetBytes(context + "\n" + serverNonce + "\n" + clientNonce)));
        }

        /// <summary>Constant-time comparison of two proofs (lower-case hex).</summary>
        public static bool ProofMatches(string expected, object supplied)
        {
            if (!(supplied is string text) || expected == null || text.Length != expected.Length)
                return false;
            var difference = 0;
            for (var i = 0; i < text.Length; i++)
                difference |= text[i] ^ expected[i];
            return difference == 0;
        }

        public static bool IsHex(string text, int length)
        {
            if (text == null || text.Length != length)
                return false;
            foreach (var c in text)
                if (!(c >= '0' && c <= '9' || c >= 'a' && c <= 'f'))
                    return false;
            return true;
        }

        public static byte[] FromHex(string hex)
        {
            var bytes = new byte[hex.Length / 2];
            for (var i = 0; i < bytes.Length; i++)
                bytes[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16);
            return bytes;
        }

        public static string ToHex(byte[] bytes)
        {
            var builder = new StringBuilder(bytes.Length * 2);
            foreach (var b in bytes)
                builder.Append(b.ToString("x2"));
            return builder.ToString();
        }
    }

    /// <summary>Incremental frame parser: <see cref="Feed"/> received bytes, then <see cref="TryTake"/> whole
    /// frames. Throws FormatException on a frame outside the protocol's limits.</summary>
    public sealed class FrameReader
    {
        byte[] _buffer = new byte[64 * 1024];
        int _count;
        Dictionary<string, object> _header;

        public void Feed(byte[] data, int count)
        {
            if (_count + count > _buffer.Length)
            {
                var grown = new byte[Math.Max(_buffer.Length * 2, _count + count)];
                Buffer.BlockCopy(_buffer, 0, grown, 0, _count);
                _buffer = grown;
            }
            Buffer.BlockCopy(data, 0, _buffer, _count, count);
            _count += count;
        }

        public bool TryTake(out Dictionary<string, object> header, out byte[] payload)
        {
            header = null;
            payload = null;
            if (_header == null)
            {
                if (_count < 4)
                    return false;
                var size = (_buffer[0] << 24) | (_buffer[1] << 16) | (_buffer[2] << 8) | _buffer[3];
                if (size <= 0 || size > LinkProtocol.MaxHeader)
                    throw new FormatException("A header length outside 1-" + LinkProtocol.MaxHeader + " bytes");
                if (_count < 4 + size)
                    return false;
                var parsed = MiniJson.ParseObject(Encoding.UTF8.GetString(_buffer, 4, size));
                if (!(parsed.TryGetValue("type", out var kind) && kind is string))
                    throw new FormatException("A header is a JSON object with a type");
                var bytes = LinkProtocol.Number(parsed, "bytes");
                if (bytes < 0 || bytes > LinkProtocol.MaxPayload)
                    throw new FormatException("A payload length outside the protocol's limit");
                _header = parsed;
                Consume(4 + size);
            }
            var length = (int)LinkProtocol.Number(_header, "bytes");
            if (_count < length)
                return false;
            payload = new byte[length];
            Buffer.BlockCopy(_buffer, 0, payload, 0, length);
            Consume(length);
            header = _header;
            _header = null;
            return true;
        }

        void Consume(int count)
        {
            Buffer.BlockCopy(_buffer, count, _buffer, 0, _count - count);
            _count -= count;
        }
    }
}
