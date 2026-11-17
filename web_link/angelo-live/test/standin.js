// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Driving test/standin_angelo.py (Angelo's real link server, without Blender) from Node tests.

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";

const HERE = import.meta.dirname;
export const ANGELO_ROOT = process.env.ANGELO_ROOT || path.resolve(HERE, "../../..");

export function hasAngelo() {
  return fs.existsSync(path.join(ANGELO_ROOT, "scripts/addons_core/angelo_agent/engine_link_server.py"));
}

export class StandIn {
  constructor(record) {
    this.record = record ?? path.join(fs.mkdtempSync(path.join(os.tmpdir(), "angelo-standin-")), "angelo-link.json");
    this.events = [];
    this.waiters = [];
  }

  async start() {
    this.process = spawn(process.env.PYTHON || "python3", [path.join(HERE, "standin_angelo.py"), "--angelo", ANGELO_ROOT,
      "--record", this.record], { stdio: ["pipe", "pipe", "inherit"] });
    readline.createInterface({ input: this.process.stdout }).on("line", (line) => {
      let event;
      try {
        event = JSON.parse(line);
      } catch {
        return;
      }
      this.events.push(event);
      for (const waiter of [...this.waiters]) {
        if (waiter.test(event)) {
          this.waiters.splice(this.waiters.indexOf(waiter), 1);
          waiter.resolve(event);
        }
      }
    });
    this.ready = await this.next((event) => event.event === "ready", 15000);
    return this;
  }

  send(command) {
    this.process.stdin.write(`${JSON.stringify(command)}\n`);
  }

  /** The next event (after now, or already seen when `seen` is true) that passes `test`. */
  next(test, timeout = 10000, seen = false) {
    if (seen) {
      const found = this.events.find(test);
      if (found) return Promise.resolve(found);
    }
    return new Promise((resolve, reject) => {
      const waiter = { test, resolve };
      this.waiters.push(waiter);
      setTimeout(() => {
        const index = this.waiters.indexOf(waiter);
        if (index >= 0) {
          this.waiters.splice(index, 1);
          reject(new Error(`stand-in: no matching event in ${timeout} ms`));
        }
      }, timeout);
    });
  }

  frame(test, timeout, seen = true) {
    return this.next((event) => event.event === "frame" && test(event.header, event), timeout, seen);
  }

  async stop() {
    if (!this.process || this.process.exitCode !== null) return;
    const exited = new Promise((resolve) => this.process.once("exit", resolve));
    this.send({ cmd: "stop" });
    const timer = setTimeout(() => this.process.kill("SIGKILL"), 5000);
    await exited;
    clearTimeout(timer);
  }
}
