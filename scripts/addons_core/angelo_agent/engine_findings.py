# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The preflight's findings as a model reads them: grouped by rule, each shared text once. No Blender dependency.

A scene with many objects can break one rule many times, and each finding repeats its rule's explanation, fix and
engine source. ``summarize`` sends each rule once, with its count, how many have a typed fix, the text its findings
share (its engine source among them), a few examples (subject, message, what was found) and how many more there
are. ``page`` is the details fetch (``angelo_engine_check`` with ``code``): one rule's findings in full, up
to ``PAGE`` (more than a fix call takes, ``engine_check_spec.MAX_FIXES``), with the count of the rest. Stored reports (``engine_preflight.link_report``) keep every finding; this only shapes tool results.
The rule check (``engine_preflight``), the fix's remaining findings (``engine_check_host``), the web export's
preflight (``web_host``) and the read-back observation (``web_readback``) all use it.
"""

EXAMPLES = 3   # Findings shown per rule; the rest are counted and fetched with ``code``.
PAGE = 100     # Findings a details fetch returns in full.


def summarize(findings, examples=EXAMPLES):
    """{"rules": [...], "total": n} for ``findings`` (errors first, as the rules order them). Each rule: code,
    severity, count (when more than one), the looked_for, fix, versions and source its findings share, ``fixable`` (its typed fix) when
    any has one, and up to ``examples`` examples (subject, message, found);
    ``more`` counts the rest. Only when some but not all have the fix: ``fixable_count``, and ``fixable`` on each
    example. Nothing is sent empty, so a report of one finding costs about what the finding did."""
    groups = {}
    for finding in findings:
        facts = finding.get("facts") or {}
        key = (finding["code"], finding["severity"])
        group = groups.get(key)
        if group is None:
            group = groups[key] = {"code": finding["code"], "severity": finding["severity"], "count": 0,
                                   "looked_for": facts.get("looked_for")}
            for field in ("fix", "versions", "source"):
                if finding.get(field):
                    group[field] = finding[field]
            group["examples"], group["_fixable"] = [], []
        group["count"] += 1
        if finding.get("fixable"):
            group["fixable"] = finding["fixable"]
        if len(group["examples"]) < examples:
            example = {"subject": finding["subject"], "message": finding["message"], "found": facts.get("found")}
            if facts.get("looked_for") != group["looked_for"]:
                example["looked_for"] = facts.get("looked_for")
            if finding.get("fix") and finding["fix"] != group.get("fix"):
                example["fix"] = finding["fix"]
            group["examples"].append(example)
        group["_fixable"].append(bool(finding.get("fixable")))
    rules = []
    for group in groups.values():
        marks = group.pop("_fixable")
        if any(marks) and not all(marks):   # Mixed: say which examples have it.
            group["fixable_count"] = sum(marks)
            for example, mark in zip(group["examples"], marks):
                example["fixable"] = mark
        more = group["count"] - len(group["examples"])
        if more:
            group["more"] = more
        if group["count"] == 1:
            del group["count"]
        rules.append(group)
    return {"rules": rules, "total": len(findings)}


def page(findings, code, size=PAGE):
    """One rule's findings in full, the first ``size``: {"code", "total", "findings", "more"?}, with the rule's source
    and versions once instead of on every finding."""
    chosen = [finding for finding in findings if finding["code"] == code]
    shown = [dict(finding) for finding in chosen[:size]]
    answer = {"code": code, "total": len(chosen)}
    for field in ("source", "versions"):
        values = {finding.get(field) for finding in shown}
        if len(values) == 1 and None not in values:
            answer[field] = values.pop()
            for finding in shown:
                finding.pop(field, None)
    answer["findings"] = shown
    if len(chosen) > size:
        answer["more"] = len(chosen) - size
    return answer
