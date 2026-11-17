// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Angelo's endpoint record (PROTOCOL.md §1, "Finding Angelo"): where it is and how it is read.
//
// The record holds the link's secret token, so it is trusted only when it is exactly what Angelo writes: a small
// regular file owned by this user with no group or other permission bits, opened without following a link. Anything
// else is "no record", never an error that could echo its content. The token never leaves this module's caller.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { PROTOCOL, validToken } from "./protocol.js";

export const MAX_RECORD = 4096;
const FILE = "angelo-link.json";

/** The record's path: ANGELO_LINK_RECORD, else Angelo's per-user data folder for this platform. */
export function recordPath(env = process.env, platform = process.platform, home = os.homedir()) {
  if (env.ANGELO_LINK_RECORD) return env.ANGELO_LINK_RECORD;
  if (platform === "darwin") return path.join(home, "Library", "Application Support", "Angelo", FILE);
  if (platform === "win32") {
    const local = env.LOCALAPPDATA && path.win32.isAbsolute(env.LOCALAPPDATA)
      ? env.LOCALAPPDATA : path.win32.join(home, "AppData", "Local");
    return path.win32.join(local, "Angelo", FILE);
  }
  const state = env.XDG_STATE_HOME && path.isAbsolute(env.XDG_STATE_HOME)
    ? env.XDG_STATE_HOME : path.join(home, ".local", "state");
  return path.join(state, "Angelo", FILE);
}

function finite(_key, value) {
  if (typeof value === "number" && !Number.isFinite(value)) throw new Error("non-finite");
  return value;
}

/**
 * The record at `file` as {port, pid, token, app}, or null when there is none Angelo could have written.
 * `platform` is a parameter so the permission rules can be tested on any machine.
 */
export function readRecord(file, platform = process.platform) {
  const noFollow = fs.constants.O_NOFOLLOW;
  let descriptor;
  try {
    if (noFollow === undefined) {
      // Windows has no O_NOFOLLOW: refuse a link up front (creating one there needs privileges anyway).
      if (fs.lstatSync(file).isSymbolicLink()) return null;
      descriptor = fs.openSync(file, fs.constants.O_RDONLY);
    } else {
      descriptor = fs.openSync(file, fs.constants.O_RDONLY | noFollow);
    }
  } catch {
    return null;
  }
  let data;
  try {
    const info = fs.fstatSync(descriptor);
    if (!info.isFile() || info.size > MAX_RECORD) return null;
    if (platform !== "win32") {
      if (typeof process.geteuid === "function" && info.uid !== process.geteuid()) return null;
      if ((info.mode & 0o077) !== 0) return null;
    }
    const buffer = Buffer.alloc(MAX_RECORD + 1);
    const count = fs.readSync(descriptor, buffer, 0, buffer.length, 0);
    if (count > MAX_RECORD) return null;
    data = buffer.subarray(0, count);
  } catch {
    return null;
  } finally {
    fs.closeSync(descriptor);
  }
  let record;
  try {
    record = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(data), finite);
  } catch {
    return null;
  }
  if (record === null || typeof record !== "object" || Array.isArray(record)) return null;
  const { protocol, port, pid, token } = record;
  if (protocol !== PROTOCOL || !Number.isInteger(port) || port < 1 || port > 65535) return null;
  if (!Number.isInteger(pid) || pid < 1 || !validToken(token)) return null;
  return { port, pid, token, app: typeof record.app === "string" ? record.app.slice(0, 400) : "" };
}

/** A string that changes when the record names another Angelo. It holds no secret (the token is hashed away). */
export function recordSignature(record) {
  if (!record) return "";
  let hash = 2166136261; // FNV-1a over the token: only to notice a change, never to prove anything.
  for (let i = 0; i < record.token.length; i++) hash = Math.imul(hash ^ record.token.charCodeAt(i), 16777619) >>> 0;
  return `${record.pid}:${record.port}:${hash.toString(16)}`;
}
