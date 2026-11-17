// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Headless Chrome for the end-to-end tests: new headless mode, a throw-away profile, WebGL on SwiftShader (CPU), no
// background throttling; driven over the DevTools protocol with Node's own WebSocket. Every console error and
// uncaught exception on the page is collected, and fails the run.

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const CANDIDATES = ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"];

export function findChrome() {
  return process.env.ANGELO_QA_BROWSER || CANDIDATES.find((file) => fs.existsSync(file)) || null;
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export class Chrome {
  async open(url, { width = 1280, height = 800 } = {}) {
    this.profile = fs.mkdtempSync(path.join(os.tmpdir(), "angelo-e2e-chrome-"));
    this.process = spawn(findChrome(), ["--headless=new", `--user-data-dir=${this.profile}`, "--no-first-run",
      "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--disable-component-update",
      "--disable-background-networking", "--disable-default-apps", "--use-mock-keychain", "--password-store=basic",
      "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
      "--disable-backgrounding-occluded-windows", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
      "--mute-audio", "--hide-scrollbars", `--window-size=${width},${height}`, "--remote-debugging-port=0",
      "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
    this.process.stderr.on("data", () => {});
    // A harness that dies must not leave a browser drawing in the background (it would skew later timings).
    const browser = this.process;
    process.once("exit", () => browser.exitCode === null && browser.kill("SIGKILL"));
    const portFile = path.join(this.profile, "DevToolsActivePort");
    for (let i = 0; i < 200 && !fs.existsSync(portFile); i++) await wait(50);
    const port = Number(fs.readFileSync(portFile, "utf8").split("\n")[0]);
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const page = targets.find((target) => target.type === "page");
    this.socket = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve);
      this.socket.addEventListener("error", reject);
    });
    this.ids = 0;
    this.pending = new Map();
    this.errors = [];
    this.console = [];
    this.socket.addEventListener("message", (event) => this.onMessage(JSON.parse(String(event.data))));
    await this.call("Runtime.enable");
    await this.call("Page.enable");
    await this.call("Page.navigate", { url });
    return this;
  }

  onMessage(message) {
    if (message.id && this.pending.has(message.id)) {
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
      return;
    }
    if (message.method === "Runtime.consoleAPICalled") {
      const text = message.params.args.map((arg) => arg.value ?? arg.description ?? "").join(" ");
      this.console.push({ type: message.params.type, text });
      if (message.params.type === "error") this.errors.push(text);
    } else if (message.method === "Runtime.exceptionThrown") {
      const details = message.params.exceptionDetails;
      this.errors.push(details.exception?.description ?? details.text);
    }
  }

  call(method, params = {}) {
    const id = ++this.ids;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }

  /** The value of an expression in the page (awaited when it is a promise). */
  async evaluate(expression) {
    const result = await this.call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
    }
    return result.result.value;
  }

  async until(expression, timeout = 20000, interval = 50) {
    const end = Date.now() + timeout;
    for (;;) {
      const value = await this.evaluate(expression);
      if (value) return value;
      if (Date.now() > end) throw new Error(`timed out waiting for: ${expression}`);
      await wait(interval);
    }
  }

  async click(x, y, modifiers = 0) {
    for (const type of ["mousePressed", "mouseReleased"]) {
      await this.call("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1, modifiers });
    }
  }

  async close() {
    try {
      this.socket?.close();
    } catch {
      // Already closed.
    }
    this.process?.kill("SIGTERM");
    await wait(300);
    if (this.process && this.process.exitCode === null) this.process.kill("SIGKILL");
    fs.rmSync(this.profile, { recursive: true, force: true });
  }
}
