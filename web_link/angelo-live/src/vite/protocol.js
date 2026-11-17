// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// angelo-link/1 framing and proofs (PROTOCOL.md §1), with no I/O so they can be tested alone.
//
// A frame is a 4-byte big-endian length n (1 <= n <= 65536), n bytes of UTF-8 JSON (the header), then
// header.bytes bytes of payload. Anything malformed throws FrameError, and the caller ends the connection: a peer that
// sends one bad frame is not something to keep talking to.

import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const PROTOCOL = "angelo-link/1";
export const MAX_HEADER = 64 * 1024;
export const MAX_PAYLOAD = 512 * 1024 * 1024; // What Angelo may send (a scene .glb).
export const MAX_CAPTURE = 8 * 1024 * 1024; // The one payload this client sends (a PNG).
const HEX64 = /^[0-9a-f]{64}$/;

export class FrameError extends Error {}

// JSON.parse turns 1e400 into Infinity; the protocol has no non-finite numbers, so such a header is malformed.
function finite(_key, value) {
  if (typeof value === "number" && !Number.isFinite(value)) throw new FrameError("A non-finite number");
  return value;
}

export function parseHeader(body, maxPayload) {
  let header;
  try {
    header = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body), finite);
  } catch (error) {
    throw error instanceof FrameError ? error : new FrameError("The header is not UTF-8 JSON");
  }
  if (header === null || typeof header !== "object" || Array.isArray(header) || typeof header.type !== "string") {
    throw new FrameError("A header is a JSON object with a type");
  }
  const size = header.bytes ?? 0;
  if (!Number.isInteger(size) || size < 0 || size > maxPayload) throw new FrameError("A payload length out of bounds");
  return header;
}

/** One frame's bytes. `payload` (a Buffer) sets header.bytes. */
export function encode(header, payload) {
  const withSize = payload && payload.length ? { ...header, bytes: payload.length } : header;
  let text;
  try {
    text = JSON.stringify(withSize, finite);
  } catch {
    throw new FrameError("The header is not finite JSON");
  }
  const body = Buffer.from(text, "utf8");
  if (body.length < 1 || body.length > MAX_HEADER) throw new FrameError("The header is too large");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(body.length);
  return payload && payload.length ? Buffer.concat([length, body, payload]) : Buffer.concat([length, body]);
}

/**
 * Incremental frame parser. feed() takes socket chunks and returns the frames they complete. A payload is written
 * straight into one buffer of its announced size, so a 300 MB scene is copied once, not re-concatenated per chunk.
 */
export class FrameReader {
  constructor(maxPayload = MAX_PAYLOAD) {
    this.maxPayload = maxPayload;
    this.chunks = [];
    this.length = 0; // Bytes in this.chunks.
    this.header = null; // A header whose payload is still arriving.
    this.payload = null;
    this.filled = 0;
  }

  feed(chunk) {
    if (chunk.length) {
      this.chunks.push(chunk);
      this.length += chunk.length;
    }
    const done = [];
    for (;;) {
      if (this.header === null) {
        if (this.length < 4) return done;
        const size = this.peek(4).readUInt32BE(0);
        if (size < 1 || size > MAX_HEADER) throw new FrameError("A header length out of bounds");
        if (this.length < 4 + size) return done;
        this.take(4);
        const header = parseHeader(this.take(size), this.maxPayload);
        const bytes = header.bytes ?? 0;
        if (bytes === 0) {
          done.push({ header, payload: Buffer.alloc(0) });
          continue;
        }
        this.header = header;
        this.payload = Buffer.allocUnsafe(bytes);
        this.filled = 0;
      }
      while (this.filled < this.payload.length && this.length > 0) {
        const first = this.chunks[0];
        const count = Math.min(first.length, this.payload.length - this.filled);
        first.copy(this.payload, this.filled, 0, count);
        this.filled += count;
        this.consume(count);
      }
      if (this.filled < this.payload.length) return done;
      done.push({ header: this.header, payload: this.payload });
      this.header = null;
      this.payload = null;
    }
  }

  peek(count) {
    return this.chunks[0].length >= count ? this.chunks[0].subarray(0, count) : Buffer.concat(this.chunks).subarray(0, count);
  }

  take(count) {
    const out = Buffer.allocUnsafe(count);
    let at = 0;
    while (at < count) {
      const first = this.chunks[0];
      const used = Math.min(first.length, count - at);
      first.copy(out, at, 0, used);
      at += used;
      this.consume(used);
    }
    return out;
  }

  consume(count) {
    this.length -= count;
    if (count === this.chunks[0].length) this.chunks.shift();
    else this.chunks[0] = this.chunks[0].subarray(count);
  }
}

export function newNonce() {
  return randomBytes(32).toString("hex");
}

export function validNonce(value) {
  return typeof value === "string" && HEX64.test(value);
}

export function validToken(value) {
  return typeof value === "string" && HEX64.test(value);
}

/** hex(HMAC-SHA256(token bytes, "<protocol>/<side>\n" + server nonce + "\n" + client nonce)). */
export function proof(side, tokenHex, serverNonce, clientNonce) {
  const message = [`${PROTOCOL}/${side}`, serverNonce, clientNonce].join("\n");
  return createHmac("sha256", Buffer.from(tokenHex, "hex")).update(message, "ascii").digest("hex");
}

/** Constant-time comparison of two hex proofs; false for anything that is not a 64-hex string. */
export function sameProof(expected, supplied) {
  if (!validNonce(expected) || !validNonce(supplied)) return false;
  return timingSafeEqual(Buffer.from(expected, "ascii"), Buffer.from(supplied, "ascii"));
}
