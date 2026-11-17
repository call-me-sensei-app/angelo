// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The bridge's HTTP routes under /@angelo-live/<token>/ (PROTOCOL.md §2): the newest live .glb of an asset, and the
// page's capture PNG. Every request passes every check here, whatever Vite already did (VITE-FACTS.md §2: Vite's own
// host check is skipped with https or allowedHosts: true, accepts any IP literal, and from Vite 7 runs before plugin
// middlewares while its cors adds headers). A refusal is a bare 403 or 404: no body that could tell a prober why.

import { timingSafeEqual } from "node:crypto";
import { MAX_CAPTURE } from "./protocol.js";

export const ROOT = "/@angelo-live/";
const LOOPBACK_PEERS = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"]);
const LOOPBACK_NAMES = new Set(["localhost", "127.0.0.1", "[::1]"]);
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const SAFE_HEADERS = {
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Cross-Origin-Resource-Policy": "same-origin",
};

export function isLoopbackPeer(address) {
  return LOOPBACK_PEERS.has(address);
}

/** "name" and "port" of a Host header, or null when it isn't one. */
export function parseHost(value) {
  if (typeof value !== "string" || value.length > 300) return null;
  const match = /^(\[[0-9a-fA-F:.]+\]|[^:[\]\s/]+)(?::(\d{1,5}))?$/.exec(value.trim());
  if (!match) return null;
  return { name: match[1].toLowerCase(), port: match[2] === undefined ? null : Number(match[2]) };
}

/**
 * True when `host` names this dev server: a loopback name, or one of Vite's `allowedHosts` strings (a leading dot
 * allows subdomains, as in Vite), with the port the request arrived on. `allowedHosts: true` adds nothing here.
 */
export function hostAllowed(host, localPort, allowedHosts, secure) {
  const parsed = parseHost(host);
  if (!parsed) return false;
  const port = parsed.port ?? (secure ? 443 : 80);
  if (port !== localPort) return false;
  if (LOOPBACK_NAMES.has(parsed.name)) return true;
  if (!Array.isArray(allowedHosts)) return false;
  return allowedHosts.some((allowed) => typeof allowed === "string" && allowed && (allowed[0] === "."
    ? parsed.name === allowed.slice(1).toLowerCase() || parsed.name.endsWith(allowed.toLowerCase())
    : parsed.name === allowed.toLowerCase()));
}

/** True when the browser says the request comes from the dev server's own page. */
export function sameOrigin(headers, host, secure) {
  if (headers["sec-fetch-site"] !== undefined) return headers["sec-fetch-site"] === "same-origin";
  return typeof headers.origin === "string" && headers.origin === `${secure ? "https" : "http"}://${host}`;
}

export function sameSecret(expected, supplied) {
  const a = Buffer.from(expected, "utf8");
  const b = Buffer.from(typeof supplied === "string" ? supplied : "", "utf8");
  // Compare equal lengths only; a different length still costs one comparison, so timing says nothing.
  return timingSafeEqual(a, b.length === a.length ? b : a) && b.length === a.length;
}

/** Width and height of a PNG's IHDR, or null when `bytes` doesn't start like a PNG. */
export function pngSize(bytes) {
  if (bytes.length < 24 || !bytes.subarray(0, 8).equals(PNG) || bytes.toString("latin1", 12, 16) !== "IHDR") return null;
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

function finish(res, status, body, type) {
  for (const name of res.getHeaderNames()) {
    if (name.startsWith("access-control-")) res.removeHeader(name); // Vite 7+'s cors ran first; never answer CORS.
  }
  res.writeHead(status, { ...SAFE_HEADERS, ...(body ? { "Content-Type": type, "Content-Length": body.length } : {}) });
  res.end(body ?? undefined);
}

/**
 * The connect middleware. Requests outside ROOT go to next() untouched.
 * @param {object} options
 * @param {string} options.token  This dev-server run's path token.
 * @param {() => (string[]|true)} options.allowedHosts  Vite's resolved server.allowedHosts.
 * @param {(assetId: string, version: number) => (Buffer|null)} options.scene  The newest .glb, or null.
 * @param {(id: string, png: Buffer, size: {width, height}) => boolean} options.capture  True when it was taken.
 * @param {(id: string) => boolean} options.expectsCapture  True while a capture with that id is outstanding.
 */
export function createMiddleware({ token, allowedHosts, scene, capture, expectsCapture }) {
  return function angeloLiveMiddleware(req, res, next) {
    const url = req.url ?? "";
    if (!url.startsWith(ROOT)) return next();
    const secure = Boolean(req.socket?.encrypted);
    const host = req.headers.host;
    if (!isLoopbackPeer(req.socket?.remoteAddress) || !hostAllowed(host, req.socket?.localPort, allowedHosts(), secure)
        || !sameOrigin(req.headers, host, secure)) {
      req.resume();
      return finish(res, 403);
    }
    const parts = url.split("?")[0].slice(ROOT.length).split("/");
    if (parts.length < 2 || !sameSecret(token, parts[0])) {
      req.resume();
      return finish(res, 404);
    }
    const route = parts.slice(1);
    if (req.method === "GET" && route.length === 3 && route[0] === "scene" && /^\d{1,15}\.glb$/.test(route[2])) {
      const body = scene(route[1], Number(route[2].slice(0, -4)));
      return body ? finish(res, 200, body, "model/gltf-binary") : finish(res, 404);
    }
    if (req.method === "POST" && route.length === 2 && route[0] === "capture" && expectsCapture(route[1])) {
      return receiveCapture(req, res, route[1], capture);
    }
    req.resume();
    return finish(res, 404);
  };
}

function receiveCapture(req, res, id, capture) {
  const announced = Number(req.headers["content-length"]);
  if (Number.isFinite(announced) && announced > MAX_CAPTURE) {
    req.resume();
    return finish(res, 413);
  }
  const chunks = [];
  let size = 0;
  let over = false;
  req.on("data", (chunk) => {
    if (over) return;
    size += chunk.length;
    if (size > MAX_CAPTURE) {
      over = true; // Stop keeping bytes; answer now and let the rest drain.
      chunks.length = 0;
      finish(res, 413);
      return;
    }
    chunks.push(chunk);
  });
  req.on("end", () => {
    if (over) return;
    const png = Buffer.concat(chunks, size);
    const dimensions = pngSize(png);
    if (!dimensions) return finish(res, 400);
    return finish(res, capture(id, png, dimensions) ? 204 : 404);
  });
  req.on("error", () => {});
}
