// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The dev server's one connection to Angelo: finds the endpoint record, connects over loopback, proves the token
// without sending it (HMAC both ways) and exchanges frames. One LinkClient per dev server.
//
// States: "waiting" (no usable record: Angelo isn't running or its link is off), "connecting", "live", "lost"
// (the connection dropped or Angelo went silent; the record is polled again). While not live, the record is read
// about every 2 s; a record that Angelo refused (a stale token) is not retried until it changes.

import { EventEmitter } from "node:events";
import net from "node:net";
import {
  FrameError, FrameReader, MAX_CAPTURE, MAX_PAYLOAD, PROTOCOL, encode, newNonce, proof, sameProof, validNonce,
} from "./protocol.js";
import { readRecord, recordPath, recordSignature } from "./record.js";

export const POLL_MS = 2000;
export const PING_MS = 10000;
export const SILENT_MS = 60000; // Angelo answers every ping; this long without any frame means it is gone.
export const AUTH_MS = 10000;

export class LinkClient extends EventEmitter {
  /**
   * @param {object} options
   * @param {() => object} options.client What the editor says about itself in `auth` (read at each connect).
   * @param {string} [options.record] The record path (default: recordPath()).
   * @param {object} [options.timing] {poll, ping, silent, auth} in ms, for tests.
   */
  constructor({ client, record, timing = {} }) {
    super();
    this.clientInfo = client;
    this.recordFile = record ?? recordPath();
    this.timing = { poll: POLL_MS, ping: PING_MS, silent: SILENT_MS, auth: AUTH_MS, ...timing };
    this.state = "off";
    this.message = "";
    this.socket = null;
    this.signature = ""; // The record this connection (or the last attempt) used.
    this.refused = ""; // A record Angelo refused: wait for a different one.
    this.session = 0; // Grows when the connected Angelo is another process (its node uids start over).
    this.sessionPid = 0;
    this.queue = []; // Frames asked for while authenticating.
    this.timers = new Set();
  }

  start() {
    if (this.state !== "off") return;
    this.setState("waiting", "Angelo isn't running, or its live link is off.");
    this.poll();
    this.pollTimer = setInterval(() => this.poll(), this.timing.poll);
    this.pollTimer.unref?.();
  }

  stop() {
    clearInterval(this.pollTimer);
    this.pollTimer = null;
    this.drop();
    this.setState("off", "");
  }

  get live() {
    return this.state === "live";
  }

  /** Send a frame now when live, or once authenticated when connecting; false when there is no connection. */
  send(header, payload) {
    if (this.state === "live") return this.write(header, payload);
    if (this.state === "connecting" && this.socket) {
      this.queue.push([header, payload]);
      return true;
    }
    return false;
  }

  /** Reconnect now (the editor's capabilities changed); a no-op while not connected. */
  reconnect() {
    if (!this.socket) return;
    this.drop();
    this.setState("waiting", "Reconnecting to Angelo.");
    this.refused = "";
    this.signature = "";
    this.poll();
  }

  poll() {
    if (this.socket) return; // Connected or connecting: the socket's own events drive the state.
    const record = readRecord(this.recordFile);
    const signature = recordSignature(record);
    if (!record) {
      this.refused = "";
      if (this.state !== "waiting") this.setState("waiting", "Angelo isn't running, or its live link is off.");
      return;
    }
    if (signature === this.refused) return;
    this.connect(record, signature);
  }

  connect(record, signature) {
    this.signature = signature;
    const reader = new FrameReader(MAX_PAYLOAD);
    const socket = net.connect({ host: "127.0.0.1", port: record.port });
    socket.setNoDelay(true);
    this.socket = socket;
    this.setState("connecting", "Connecting to Angelo.");
    let serverNonce = "";
    let clientNonce = "";
    let heard = Date.now();
    let authenticated = false;
    const authTimer = this.later(() => this.fail(socket, "Angelo didn't answer.", false), this.timing.auth);

    const onFrame = ({ header, payload }) => {
      heard = Date.now();
      if (!authenticated) {
        if (header.type === "hello" && !serverNonce && header.protocol === PROTOCOL && validNonce(header.nonce)) {
          serverNonce = header.nonce;
          clientNonce = newNonce();
          const info = this.clientInfo();
          this.write({ type: "auth", nonce: clientNonce, proof: proof("client", record.token, serverNonce, clientNonce),
            client: info }, null, socket);
          return;
        }
        if (header.type === "welcome" && serverNonce
            && sameProof(proof("server", record.token, serverNonce, clientNonce), header.proof)) {
          authenticated = true;
          this.clearLater(authTimer);
          if (record.pid !== this.sessionPid) {
            this.sessionPid = record.pid;
            this.session += 1;
          }
          this.setState("live", "Linked to Angelo.");
          for (const [queuedHeader, queuedPayload] of this.queue.splice(0)) this.write(queuedHeader, queuedPayload);
          ping.refresh?.();
          return;
        }
        // An error frame, a wrong proof or a stranger on the port: never trust it, wait for a new record.
        this.refused = signature;
        this.fail(socket, header.type === "error"
          ? "Angelo didn't accept this connection. Turn its live link off and on."
          : "Something other than Angelo answered on its port.", false);
        return;
      }
      if (header.type === "pong") return;
      this.emit("frame", header, payload);
    };

    const ping = setInterval(() => {
      if (!authenticated) return;
      if (Date.now() - heard > this.timing.silent) {
        this.fail(socket, "Angelo stopped answering.", true);
        return;
      }
      this.write({ type: "ping" }, null, socket);
    }, this.timing.ping);
    ping.unref?.();

    socket.on("data", (chunk) => {
      if (this.socket !== socket) return;
      let frames;
      try {
        frames = reader.feed(chunk);
      } catch (error) {
        this.fail(socket, error instanceof FrameError ? "Angelo sent a frame this plugin can't read." : "Lost Angelo.",
          authenticated);
        return;
      }
      for (const frame of frames) {
        if (this.socket !== socket) return;
        try {
          onFrame(frame);
        } catch (error) {
          this.emit("problem", error);
        }
      }
    });
    socket.on("error", () => {}); // "close" follows and says what happened.
    socket.on("close", () => {
      clearInterval(ping);
      this.clearLater(authTimer);
      if (this.socket !== socket) return;
      this.socket = null;
      this.queue.length = 0;
      if (authenticated) this.setState("lost", "Lost the connection to Angelo.");
      else if (this.state === "connecting") this.setState("waiting", "Couldn't reach Angelo. Is it still open?");
    });
  }

  fail(socket, message, wasLive) {
    if (this.socket !== socket) return;
    this.socket = null;
    this.queue.length = 0;
    socket.destroy();
    this.setState(wasLive ? "lost" : "waiting", message);
  }

  drop() {
    const socket = this.socket;
    this.socket = null;
    this.queue.length = 0;
    socket?.destroy();
  }

  write(header, payload, socket = this.socket) {
    if (!socket || socket.destroyed) return false;
    if (payload && payload.length > MAX_CAPTURE) return false; // The only client payload is a bounded capture.
    let frame;
    try {
      frame = encode(header, payload);
    } catch {
      return false;
    }
    socket.write(frame);
    return true;
  }

  setState(state, message) {
    if (state === this.state && message === this.message) return;
    this.state = state;
    this.message = message;
    this.emit("state", state, message);
  }

  later(fn, ms) {
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      fn();
    }, ms);
    timer.unref?.();
    this.timers.add(timer);
    return timer;
  }

  clearLater(timer) {
    clearTimeout(timer);
    this.timers.delete(timer);
  }
}
