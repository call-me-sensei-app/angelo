// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Page URLs ⇄ project paths. The page names an asset by the URL it loaded ("/models/chair.glb"); Angelo names it by
// its path in the project ("public/models/chair.glb"). The project is the folder holding package.json at or above
// Vite's root (Angelo accepts a folder with package.json as a web project); paths are POSIX and never leave it.

import fs from "node:fs";
import path from "node:path";

/** The nearest folder at or above `root` that holds package.json, else `root` itself. */
export function projectFolder(root) {
  let folder = path.resolve(root);
  for (;;) {
    if (fs.existsSync(path.join(folder, "package.json"))) return folder;
    const parent = path.dirname(folder);
    if (parent === folder) return path.resolve(root);
    folder = parent;
  }
}

/** package.json's name (bounded), else the folder's name. */
export function projectName(folder) {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(folder, "package.json"), "utf8"));
    if (typeof data.name === "string" && data.name.trim()) return data.name.trim().slice(0, 200);
  } catch {
    // No or unreadable package.json: the folder's name says enough.
  }
  return path.basename(folder).slice(0, 200);
}

/** The pathname of a page URL as the SDK sends it: no query or hash; null when it isn't a plain absolute path. */
export function cleanPagePath(url) {
  if (typeof url !== "string" || url.length > 2048 || !url.startsWith("/") || url.startsWith("//")) return null;
  const end = url.search(/[?#]/);
  return end < 0 ? url : url.slice(0, end);
}

function decodeSegments(pathname) {
  const parts = [];
  for (const raw of pathname.split("/")) {
    let part;
    try {
      part = decodeURIComponent(raw);
    } catch {
      return null;
    }
    if (part.includes("\0") || part.includes("\\") || part.includes("/") || part === "..") return null;
    if (part === "" || part === ".") continue;
    parts.push(part);
  }
  return parts;
}

/**
 * The project path of the file a page URL serves, or null when it isn't a file inside the project.
 * Vite serves `publicDir` first, then the root, then `/@fs/<absolute path>`; `base` prefixes all of them.
 * @param {string} url  The page's URL pathname.
 * @param {{root: string, publicDir: string, base: string, project: string}} context
 */
export function urlToProjectPath(url, { root, publicDir, base = "/", project }, exists = fs.existsSync) {
  const pathname = cleanPagePath(url);
  if (!pathname) return null;
  let rest = pathname;
  const prefix = base.endsWith("/") ? base : `${base}/`;
  if (prefix !== "/" && rest.startsWith(prefix)) rest = rest.slice(prefix.length - 1);
  let absolute = null;
  if (rest.startsWith("/@fs/")) {
    const parts = decodeSegments(rest.slice(4));
    if (!parts) return null;
    absolute = path.resolve(path.sep === "\\" ? parts.join("\\") : `/${parts.join("/")}`);
  } else {
    const parts = decodeSegments(rest);
    if (!parts || !parts.length) return null;
    const inPublic = publicDir ? path.join(publicDir, ...parts) : null;
    absolute = inPublic && exists(inPublic) ? inPublic : path.join(root, ...parts);
  }
  return relativeInside(project, absolute);
}

/** `absolute` relative to `project`, POSIX, or null when it is outside. */
export function relativeInside(project, absolute) {
  const relative = path.relative(project, absolute);
  if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) return null;
  return relative.split(path.sep).join("/");
}

/** The absolute file of a project path. */
export function projectFile(project, projectPath) {
  return path.join(project, ...projectPath.split("/"));
}
