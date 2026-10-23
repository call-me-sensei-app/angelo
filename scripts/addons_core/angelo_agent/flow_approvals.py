# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Approval text for Generation Flow tools: every paid generation is listed on the card.

Card text is translated where it is drawn (``ui_text``): whole sentences with
named placeholders, so a translator can move the counts, accounts and costs
but never drop one. ``flow_prices.describe`` stays English for tool results;
``price_text`` is its display twin.
"""

from . import flow_prices
from .flow_setup import step_label
from .flow_starters import flow_label
from .ui_text import iface_, plural_


def price_text(total, unknown):
    """``flow_prices.describe`` for display: the same facts, in the interface language."""
    if unknown and not total:
        return iface_("price unknown")
    amount = f"{total:.2f}"
    if unknown:
        return iface_("at least ≈ ${amount} (estimate, prices as of {date})", amount=amount, date=flow_prices.AS_OF)
    return iface_("≈ ${amount} (estimate, prices as of {date})", amount=amount, date=flow_prices.AS_OF)


def run_lines(review, choices=None):
    """(rows, selection) for a run card: one row per step that would generate."""
    selection = review.selection(choices)
    now = {line["node"] for line in selection["now"]}
    later = {line["node"] for line in selection["later"]}
    rows = []
    for line in review.plan["lines"]:
        if line["action"] == "REUSE":
            rows.append({"node": line["node"], "text": iface_("{label} · reused, free", label=step_label(line["label"])), "state": "REUSE"})
            continue
        if line["action"] != "GENERATE":
            rows.append({"node": line["node"], "state": line["action"],
                         "text": iface_("{label} · can't run: {reason}", label=step_label(line["label"]), reason=line["reason"])})
            continue
        each = line["price_each"]
        cost = f"≈ ${each * line['count']:.2f}" if each is not None else iface_("price unknown")
        state = "NOW" if line["node"] in now else "LATER" if line["node"] in later else "LEFT_OUT"
        fields = dict(label=step_label(line["label"]), count=line["count"], account=line["account"], cost=cost)
        text = (iface_("{label} ×{count} · {account} · {cost}", **fields) if state == "NOW" else
                iface_("{label} ×{count} · {account} · {cost} · after review, next card", **fields) if state == "LATER" else
                iface_("{label} ×{count} · {account} · {cost} · left out", **fields))
        rows.append({"node": line["node"], "state": state, "stop": selection["stops"].get(line["node"], False), "text": text})
    return rows, selection


def message(name, args, usage_label=""):
    if name == "angelo_flow_build":
        nodes, removed = args.get("nodes", []), args.get("remove", [])
        body = [iface_("Edit Generation Flow “{flow}”", flow=flow_label(args["flow"])) if args.get("expected_revision")
                else iface_("Create Generation Flow “{flow}”", flow=flow_label(args["flow"]))]
        if nodes:
            body.append(iface_("Set {nodes}", nodes=", ".join(n.get("id", "?") for n in nodes[:8]) + ("…" if len(nodes) > 8 else "")))
        if removed:
            body.append(iface_("Remove {nodes}", nodes=", ".join(removed[:8])))
        if args.get("links") or args.get("unlink"):
            body.append(plural_(len(args.get("links", [])) + len(args.get("unlink", [])), "Rewire {count} input", "Rewire {count} inputs"))
        return {"title": iface_("Edit Generation Flow"), "body": "\n".join(body),
                "warning": iface_("Free and undoable. Nothing is generated; runs need their own approval.")}
    if name == "angelo_flow_choose":
        return {"title": iface_("Choose an output in “{flow}”", flow=flow_label(args["flow"])),
                "body": iface_("Pass asset {asset} from '{node}' to the steps after it.", asset=args["asset_id"], node=step_label(args["node"])),
                "warning": iface_("Free and undoable. Steps after it become out of date until they run again.")}
    review = args.get("_review")
    if review is None:
        return {"title": iface_("Run Generation Flow “{flow}”", flow=flow_label(args["flow"])), "body": iface_("Preparing the itemized plan…"), "warning": ""}
    rows, selection = run_lines(review, args.get("_choices"))
    total = price_text(selection["total"], selection["unknown_prices"])
    body = "\n".join(("☑ " if row["state"] == "NOW" else "☐ " if row["state"] in {"LATER", "LEFT_OUT"} else "· ") + row["text"]
                     for row in rows)
    body += "\n\n" + plural_(selection["count"], "Runs {count} paid generation now · {total}", "Runs {count} paid generations now · {total}", total=total)
    if selection["later"]:
        body += "\n" + plural_(len(selection["later"]), "After review: {count} more step, ≈ ${total}, on a new card",
                               "After review: {count} more steps, ≈ ${total}, on a new card", total=f"{selection['later_total']:.2f}")
    return {"title": plural_(selection["count"], "Run {count} paid generation in “{flow}”", "Run {count} paid generations in “{flow}”",
                             flow=flow_label(args["flow"])), "body": body,
            "warning": iface_("Billed to {account}, NOT your coding subscription. Only the listed generations can run. "
                              "Unchanged steps are reused for free. No automatic retries; Stop only ends local monitoring.",
                              account=usage_label or iface_("these API accounts"))}
