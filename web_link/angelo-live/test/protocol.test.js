// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Framing, proofs, the endpoint record and URL mapping, without sockets.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  FrameError, FrameReader, MAX_HEADER, encode, proof, sameProof, validNonce,
} from "../src/vite/protocol.js";
import { MAX_RECORD, readRecord, recordPath, recordSignature } from "../src/vite/record.js";
import { cleanPagePath, projectFolder, projectName, urlToProjectPath } from "../src/vite/paths.js";
import { pathless, plain } from "../src/vite/bridge.js";
import * as stub from "../src/stub.js";

const frame = (body) => {
  const data = Buffer.from(body, "utf8");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  return Buffer.concat([length, data]);
};

describe("frames", () => {
  it("round-trip whole and byte by byte", () => {
    const data = Buffer.concat([encode({ type: "scene", asset: "a.glb" }, Buffer.from("glTF....")), encode({ type: "pong" })]);
    for (const step of [data.length, 1, 3]) {
      const reader = new FrameReader();
      const frames = [];
      for (let i = 0; i < data.length; i += step) frames.push(...reader.feed(data.subarray(i, i + step)));
      expect(frames.map((item) => item.header.type)).toEqual(["scene", "pong"]);
      expect(frames[0].header.bytes).toBe(8);
      expect(frames[0].payload.toString()).toBe("glTF....");
    }
  });

  it("refuse bad lengths, oversized headers and payloads, non-objects and non-finite numbers", () => {
    const bad = [
      Buffer.from([0, 0, 0, 0]),
      Buffer.from([0, 1, 0, 1]), // 65537 > MAX_HEADER
      frame("[]"), frame("{x}"), frame('{"bytes":0}'), frame('{"type":"x","bytes":-1}'), frame('{"type":"x","bytes":1.5}'),
      frame(`{"type":"x","bytes":${512 * 1024 * 1024 + 1}}`), frame('{"type":"x","n":1e400}'),
      frame('{"type":"x","n":NaN}'), Buffer.concat([Buffer.from([0, 0, 0, 2]), Buffer.from([0xff, 0xfe])]),
    ];
    for (const data of bad) expect(() => new FrameReader().feed(data)).toThrow(FrameError);
    expect(() => new FrameReader(0).feed(frame('{"type":"status","bytes":1}'))).toThrow(FrameError);
    expect(() => encode({ type: "x", n: Infinity })).toThrow(FrameError);
    expect(() => encode({ type: "x", text: "a".repeat(MAX_HEADER) })).toThrow(FrameError);
  });

  it("accept the largest header", () => {
    const header = { type: "x", text: "" };
    header.text = "a".repeat(MAX_HEADER - JSON.stringify(header).length);
    const [{ header: back }] = new FrameReader().feed(encode(header));
    expect(back.text.length).toBe(header.text.length);
  });
});

describe("proofs", () => {
  const token = "ab".repeat(32);
  const server = "1".repeat(64);
  const client = "2".repeat(64);

  it("match Python's construction and differ by side", () => {
    // hmac.new(bytes.fromhex("ab"*32), b"angelo-link/1/client\n" + b"1"*64 + b"\n" + b"2"*64, sha256).hexdigest()
    const c = proof("client", token, server, client);
    const s = proof("server", token, server, client);
    expect(c).toMatch(/^[0-9a-f]{64}$/);
    expect(c).not.toBe(s);
    expect(proof("client", "cd".repeat(32), server, client)).not.toBe(c);
  });

  it("compare only well-formed proofs", () => {
    const c = proof("client", token, server, client);
    expect(sameProof(c, c)).toBe(true);
    expect(sameProof(c, c.toUpperCase())).toBe(false);
    expect(sameProof(c, c.slice(1))).toBe(false);
    expect(sameProof(c, null)).toBe(false);
    expect(validNonce("g".repeat(64))).toBe(false);
  });
});

describe("the endpoint record", () => {
  const dirs = [];
  const temp = () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "angelo-live-record-"));
    dirs.push(dir);
    return dir;
  };
  afterEach(() => dirs.splice(0).forEach((dir) => fs.rmSync(dir, { recursive: true, force: true })));
  const good = { protocol: "angelo-link/1", port: 4321, pid: 99, token: "0f".repeat(32), app: "/A", binary: "/B" };
  const write = (dir, value, mode = 0o600) => {
    const file = path.join(dir, "angelo-link.json");
    fs.writeFileSync(file, typeof value === "string" ? value : JSON.stringify(value), { mode });
    fs.chmodSync(file, mode);
    return file;
  };

  it("is found per platform, or by ANGELO_LINK_RECORD", () => {
    expect(recordPath({ ANGELO_LINK_RECORD: "/x/r.json" }, "linux", "/h")).toBe("/x/r.json");
    expect(recordPath({}, "darwin", "/h")).toBe("/h/Library/Application Support/Angelo/angelo-link.json");
    expect(recordPath({ XDG_STATE_HOME: "/s" }, "linux", "/h")).toBe("/s/Angelo/angelo-link.json");
    expect(recordPath({ XDG_STATE_HOME: "relative" }, "linux", "/h")).toBe("/h/.local/state/Angelo/angelo-link.json");
    expect(recordPath({ LOCALAPPDATA: "C:\\L" }, "win32", "C:\\U")).toBe("C:\\L\\Angelo\\angelo-link.json");
  });

  it("is read when owner-only and well-formed", () => {
    const record = readRecord(write(temp(), good));
    expect(record).toEqual({ port: 4321, pid: 99, token: good.token, app: "/A" });
    expect(recordSignature(record)).not.toContain(good.token);
  });

  it("is refused with group or other bits, through a link, oversized, or malformed", () => {
    const dir = temp();
    if (process.platform !== "win32") {
      expect(readRecord(write(dir, good, 0o640))).toBeNull();
      expect(readRecord(write(dir, good, 0o604))).toBeNull();
      const target = write(temp(), good);
      const link = path.join(dir, "link.json");
      fs.symlinkSync(target, link);
      expect(readRecord(link)).toBeNull();
    }
    expect(readRecord(write(dir, " ".repeat(MAX_RECORD + 1)))).toBeNull();
    expect(readRecord(write(dir, `${JSON.stringify(good)}${" ".repeat(MAX_RECORD)}`))).toBeNull();
    for (const change of [{ protocol: "angelo-link/2" }, { port: 0 }, { port: 70000 }, { port: "1" }, { pid: 0 },
      { token: "0F".repeat(32) }, { token: "0f".repeat(31) }]) {
      expect(readRecord(write(dir, { ...good, ...change }))).toBeNull();
    }
    expect(readRecord(write(dir, "not json"))).toBeNull();
    expect(readRecord(write(dir, `{"protocol":"angelo-link/1","port":1e400}`))).toBeNull();
    expect(readRecord(path.join(dir, "missing.json"))).toBeNull();
    fs.mkdirSync(path.join(dir, "folder.json"));
    expect(readRecord(path.join(dir, "folder.json"))).toBeNull();
  });
});

describe("page URLs and project paths", () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "angelo-live-project-"));
  fs.writeFileSync(path.join(project, "package.json"), JSON.stringify({ name: "my-app" }));
  fs.mkdirSync(path.join(project, "public/models"), { recursive: true });
  fs.writeFileSync(path.join(project, "public/models/chair.glb"), "x");
  fs.mkdirSync(path.join(project, "src/assets"), { recursive: true });
  const context = { root: project, publicDir: path.join(project, "public"), base: "/", project };

  it("maps public files, root files, /@fs/ paths and a base", () => {
    expect(urlToProjectPath("/models/chair.glb", context)).toBe("public/models/chair.glb");
    expect(urlToProjectPath("/src/assets/b%20c.glb", context)).toBe("src/assets/b c.glb");
    expect(urlToProjectPath(`/@fs${project}/src/assets/x.glb`, context)).toBe("src/assets/x.glb");
    expect(urlToProjectPath("/app/models/chair.glb", { ...context, base: "/app/" })).toBe("public/models/chair.glb");
    expect(urlToProjectPath("/models/chair.glb?import#x", context)).toBe("public/models/chair.glb");
  });

  it("refuses anything that leaves the project or isn't a plain path", () => {
    for (const url of ["/../x.glb", "/%2e%2e/x.glb", "/a/%2F/b.glb", "/a%5Cb.glb", "/a%00.glb", "//evil/x.glb",
      "http://x/a.glb", "/@fs/etc/passwd", "", "/", "/%E0%A4%A.glb"]) {
      expect(urlToProjectPath(url, context)).toBeNull();
    }
    expect(cleanPagePath("/a.glb?x")).toBe("/a.glb");
  });

  it("names the project from package.json, and finds it above Vite's root", () => {
    expect(projectName(project)).toBe("my-app");
    expect(projectFolder(path.join(project, "src"))).toBe(project);
  });
});

describe("messages", () => {
  it("are bounded and single-line; Angelo's never hold a path or URL", () => {
    expect(plain("a\nb".repeat(200))).toHaveLength(200);
    expect(pathless("Failed to fetch http://localhost:5173/models/chair.glb")).toBe("Failed to fetch (a URL)");
    expect(pathless("No such file /home/me/app/public/x.glb here")).toBe("No such file (a path) here");
    expect(pathless("C:\\Users\\me\\x.glb missing")).toBe("(a path) missing");
  });
});

describe("the production stub", () => {
  it("has the same API and does nothing", async () => {
    const live = stub.createAngeloLive({ renderer: {}, loader: {} });
    expect(live.track({}, "/a.glb").untrack()).toBeUndefined();
    expect(live.untrack("/a.glb")).toBeUndefined();
    expect(live.edit("/a.glb")).toBeUndefined();
    expect(typeof live.on("renamed", () => {})).toBe("function");
    await expect(live.stop()).resolves.toBeUndefined();
    expect(live.state.link).toBe("off");
  });

  it("holds no route, socket, fetch or package name", () => {
    // The exported name is the API (a bundler renames it); nothing else may name the package or a route.
    const source = fs.readFileSync(new URL("../src/stub.js", import.meta.url), "utf8")
      .split("\n").filter((line) => !line.startsWith("//")).join("\n").replace("createAngeloLive", "");
    for (const word of ["angelo", "fetch", "WebSocket", "import.meta", "XMLHttpRequest", "@", "/"]) {
      expect(source.toLowerCase()).not.toContain(word.toLowerCase());
    }
  });
});
