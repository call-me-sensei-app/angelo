# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run an approved Generation Flow plan, one main-thread tick at a time. No Blender dependency.

A run owns exactly the generations the user approved (``flow_graph.select``'s
``now`` lines). Before each generation it rebuilds the request from the
current flow and refuses to start anything that differs from the approval,
except for references this run produced itself, which must be outputs of the
named upstream step. Provider work goes through the host's existing Studio
jobs; a failed generation is never retried.

The ``host`` adapter supplies:

* ``request(line) -> (spec, key)``: the exact generation request for a line,
  built from the CURRENT flow and library (connection_id, model, options,
  prompt, references), raising ``FlowError`` if it can't be built;
* ``next_variant(key) -> int``: the first unused variant index for a key;
* ``start(line, spec, key, variant) -> asset_id``: start one approved generation;
* ``asset(asset_id) -> dict | None``: the library record;
* ``capacity() -> int``: free generation slots;
* ``chosen(node, key) -> asset_id | None``: the output passed downstream;
* ``stop(asset_id)``: stop local monitoring.
"""

import uuid

from .flow_spec import FlowError

QUEUED, ACTIVE, DONE, FAILED, SKIPPED, STOPPED = "QUEUED", "RUNNING", "DONE", "FAILED", "SKIPPED", "STOPPED"
TERMINAL = frozenset({DONE, FAILED, SKIPPED, STOPPED})


class FlowRun:
    def __init__(self, flow_name, lines, host, *, later=(), stops=None, run_id=None):
        self.id = run_id or str(uuid.uuid4())
        self.flow_name, self.host = flow_name, host
        self.lines = {line["node"]: line for line in lines}
        self.order = [line["node"] for line in lines]
        self.later = [line["node"] for line in later]
        self.stops = dict(stops or {})
        self.steps = {node: {"state": QUEUED, "assets": [], "key": None, "error": None, "chosen": None}
                      for node in self.order}
        self.stopped = False

    @property
    def finished(self):
        return all(step["state"] in TERMINAL for step in self.steps.values())

    def _references(self, line):
        """Bind approved references; ("run", node) becomes that step's chosen output."""
        bound = []
        for kind, value in line["approval"]["references"]:
            if kind != "run":
                bound.append((kind, value))
                continue
            step = self.steps.get(value)
            if step is None or step["state"] != DONE or not step["chosen"]:
                return None
            bound.append(("asset", step["chosen"]))
        return bound

    def tick(self):
        """Advance once: settle finished jobs, skip blocked steps, start ready ones. Returns True on change."""
        changed = self._settle()
        for node in self.order:
            step, line = self.steps[node], self.lines[node]
            if step["state"] != QUEUED or self.stopped:
                continue
            upstream = [self.steps[u]["state"] for u in line["depends_on"] if u in self.steps]
            if any(state in {FAILED, SKIPPED, STOPPED} for state in upstream):
                failed = [u for u in line["depends_on"] if self.steps.get(u, {}).get("state") in {FAILED, SKIPPED, STOPPED}]
                step.update(state=SKIPPED, error="skipped because '" + "', '".join(failed) + "' produced no output")
                changed = True
                continue
            if any(state != DONE for state in upstream):
                continue
            if self.host.capacity() < line["count"]:
                continue  # Start a step's variants together, so they share one key.
            references = self._references(line)
            if references is None:
                step.update(state=FAILED, error="an upstream output disappeared before this step started")
                changed = True
                continue
            try:
                spec, key = self.host.request(line)
                self._check_approved(line, spec, references)
                step.update(state=ACTIVE, key=key)
                first = self.host.next_variant(key)
                for offset in range(line["count"]):
                    step["assets"].append(self.host.start(line, spec, key, first + offset))
            except (FlowError, ValueError, RuntimeError) as error:
                step.update(state=FAILED, error=str(error))
                if step["assets"]:
                    step["error"] += f"; {len(step['assets'])} generation(s) had already started and are kept"
            changed = True
        return changed

    @staticmethod
    def _check_approved(line, spec, references):
        """Refuse a generation that isn't the one on the approved card.

        ``spec`` is rebuilt from the current flow; ``references`` are the
        approved ones with this run's outputs bound. An edit made during the run
        (a prompt, a model, a different chosen variant) makes them differ.
        """
        approved = line["approval"]
        same = (spec["connection_id"] == approved["connection_id"] and spec["model"] == approved["model"]
                and spec["options"] == approved["options"] and spec["prompt"] == approved["prompt"]
                and [list(r) for r in references] == [list(r) for r in spec["references"]])
        if not same:
            raise FlowError("This step changed after approval; nothing was generated. Inspect the flow and run it again")

    def _settle(self):
        changed = False
        for node in self.order:
            step = self.steps[node]
            if step["state"] != ACTIVE:
                continue
            records = [self.host.asset(identity) for identity in step["assets"]]
            statuses = [r.get("status") if r else "failed" for r in records]
            if any(status not in {"ready", "failed", "stopped"} for status in statuses):
                continue
            if "ready" in statuses:
                step.update(state=DONE, chosen=self.host.chosen(node, step["key"]))
                if "failed" in statuses or "stopped" in statuses:
                    step["error"] = f"{statuses.count('ready')} of {len(statuses)} variants succeeded"
            else:
                errors = [r.get("error") for r in records if r and r.get("error")]
                step.update(state=STOPPED if "stopped" in statuses and self.stopped else FAILED,
                            error=errors[0] if errors else "the generation did not complete; no automatic retry")
            changed = True
        return changed

    def stop(self):
        """Stop local monitoring of started work and start nothing new. Provider billing may continue."""
        self.stopped = True
        for step in self.steps.values():
            if step["state"] == QUEUED:
                step.update(state=STOPPED, error="not started; the run was stopped")
            elif step["state"] == ACTIVE:
                for identity in step["assets"]:
                    self.host.stop(identity)

    def review_node(self):
        """The finished review-stop step whose choice the user should make, if any."""
        for node in self.order:
            if self.stops.get(node) and self.steps[node]["state"] == DONE and self.later:
                return node
        return None

    def result(self):
        """A compact summary for the agent and the chat card: IDs and states, no file paths."""
        steps = []
        for node in self.order:
            step = self.steps[node]
            entry = {"node": node, "state": step["state"], "outputs": list(step["assets"])}
            if step["chosen"]:
                entry["chosen"] = step["chosen"]
            if step["error"]:
                entry["error"] = step["error"]
            steps.append(entry)
        states = {step["state"] for step in self.steps.values()}
        summary = ("STOPPED" if self.stopped else "RUNNING" if not self.finished else "DONE" if states <= {DONE} else
                   "FAILED" if DONE not in states else "PARTIAL")
        result = {"run_id": self.id, "flow": self.flow_name, "status": summary, "steps": steps,
                  "generations_started": sum(len(s["assets"]) for s in self.steps.values()),
                  "automatic_retries": 0}
        review = self.review_node()
        if review:
            result["review_stop"] = {"node": review, "pick_from": list(self.steps[review]["assets"]),
                                     "remaining_steps": list(self.later),
                                     "next": "Show the user the variants. After they choose (angelo_flow_choose), "
                                             "run DOWNSTREAM from this step for a new approval."}
        elif self.later:
            result["remaining_steps"] = list(self.later)
        return result
