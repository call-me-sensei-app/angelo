# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Full-body IK for posing, as plain data (doc/angelo/rigging.rst, "Full-body pose"). No Blender dependency.

``solve(joints, parents, lengths_from, targets, pinned, hinges, iterations, tolerance)`` moves a humanoid's joints so
the effectors reach their targets while pinned joints stay where they are, the whole body taking part: a hand
reaching up pulls the chest and lowers or raises the hips as far as the planted legs allow. It is the multi-effector
form of FABRIK (Aristidou and Lasenby, "FABRIK: a fast, iterative solver for the inverse kinematics problem",
Graphical Models 73(5), 2011, section 5), on the role tree:

* **Backward**: from every effector and pinned joint toward the root, each joint put back at its bone's length from
  its child; a joint with several children (the chest, the hips) takes the mean of what each child asks.
* **Forward**: from the root outward, each child put at its bone's length from its parent, in the direction it now
  has. The root itself is free (the hips float), so a body with no pins moves as a whole toward its targets.
* **Branching joints move rigidly** (the "sub-bases" of the paper): the children of a joint with several (the
  hips' spine and legs, the chest's neck and arms) all hang from one bone, so they are placed by ONE rotation of
  their offsets, the one that best turns them toward where the children on a path ask to be (Horn's quaternion
  fit). Children leading to a pin are fitted first; when their offsets lie on one line, as two planted legs' do,
  the turn about that line is then chosen in closed form for the other children, so the spine decides the pelvis's
  pitch (weighting the legs far above the spine instead let their least disagreement lock that pitch). Otherwise the
  branch's shape deforms, and no rotation of the bone can reproduce the solve.
* **Pins come first.** Where a joint takes what its children ask (backward), it is then moved, as little as it
  takes, to where every pinned joint below it stays within reach (the chain's length; through a branching joint,
  from its child's place under the joint's last rotation). Without this, a target the arms can reach with room to
  spare still pulled the hips from planted feet: each pull was averaged with the legs'. Targets are best effort.
* **Pinned chains are placed exactly** after each forward pass: the single links from the joint below the
  nearest branching joint down to the pin (thigh, shin and foot) are placed from that joint so the pin sits on its
  goal, two links by the triangle they make (the knee keeps its side), more by FABRIK with the top held. Near a
  straightened leg FABRIK alone creeps, leaving planted feet millimetres off.
* **Hinges** (elbows, knees): a hinge joint's bend stays on the side it bent at rest (the bend plane's normal keeps
  its sign), so a knee never bends backwards; a straight rest limb bends toward the given side.
* **Limits and balance** (``body_ik_limits``): with ``limits``, after each forward pass every link is turned back
  within its swing cone (a branch's turn within its children's tightest), pinned chains excepted; with ``balance``,
  each iteration ends with the body (all but the pinned chains below their top joint) moved, square to ``up``, by
  the centre of mass's deficit beyond the support, the hips held within the pins' reach and the pinned chains
  settled again, a few rounds until it balances. Pins first, then balance, then limits, then targets.

Joints are named; positions are (x, y, z) tuples. The result is the new positions, the distance left to each target,
and whether every target was reached within ``tolerance``.
"""

import math

from . import body_ik_limits
from .body_ik_fit import _fit_branch, _fit_rotation
from .body_ik_limits import rotate as _rotate


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a, fallback=(0.0, 0.0, 1.0)):
    n = _norm(a)
    return _mul(a, 1.0 / n) if n > 1e-12 else fallback


def _toward(origin, point, length):
    """The point at ``length`` from ``origin`` in the direction of ``point``."""
    return _add(origin, _mul(_unit(_sub(point, origin)), length))


def _mean(points):
    n = float(len(points))
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n, sum(p[2] for p in points) / n)


BALANCE_ROUNDS = 8   # Moves of the body toward balance per iteration (each also re-settles the pinned chains).


def _holds(parents, children, length, pinned):
    """{joint: [(child or None, pin, reach)]}: for each joint above a pin, the pin's reach from the joint itself
    (``None``: the chain's length) or, below a branching joint, from the child the path goes through."""
    out = {}
    for pin in pinned:
        reach, below, j = 0.0, pin, parents.get(pin)
        while j is not None:
            if len(children.get(j, ())) > 1:
                out.setdefault(j, []).append((below, pin, reach))
            else:
                out.setdefault(j, []).append((None, pin, reach + length[below]))
            reach += length[below]
            below, j = j, parents.get(j)
    return out


def _keep_pins_in_reach(here, holds, turn, offset, goals, rounds=8):
    """``here`` moved, as little as it takes, so every pin below it stays within reach (alternating projections)."""
    for _ in range(rounds):
        moved = False
        for child, pin, reach in holds:
            anchor = here if child is None else _add(here, _rotate(turn or (1.0, 0.0, 0.0, 0.0), offset[child]))
            gap = _sub(goals[pin], anchor)
            d = _norm(gap)
            if d > reach + 1e-12:
                here = _add(here, _mul(gap, (d - reach) / d))
                moved = True
        if not moved:
            break
    return here


def _settle(pos, pin, goal, parents, children, length, rounds=32, normals=None):
    """Place the single-link chain ending at ``pin`` (from the joint below the nearest branching joint, held where
    it is) so the pin sits on ``goal``, as close as the chain's length allows: two links exactly, more by FABRIK with
    the top held. Returns the joints moved. Of two links, the middle joint bends the side its hinge prefers
    (``normals``: {joint: the bend plane's normal}); only a joint with none keeps the side it is on, since after the
    body moves as a whole a knee's place is stale (it bent 14 cm backwards behind lowered hips)."""
    chain = [pin]
    while parents.get(chain[-1]) is not None and len(children[parents[chain[-1]]]) == 1:
        chain.append(parents[chain[-1]])
    chain.reverse()   # top ... pin; the top stays.
    if len(chain) < 2:
        return set()
    top = pos[chain[0]]
    if len(chain) == 3:
        mid, l1, l2 = chain[1], length[chain[1]], length[chain[2]]
        span = _sub(goal, top)
        d = _norm(span)
        u = _unit(span)
        d = min(max(d, abs(l1 - l2)), l1 + l2)
        x = (l1 * l1 - l2 * l2 + d * d) / (2.0 * d) if d > 1e-12 else 0.0
        h = math.sqrt(max(l1 * l1 - x * x, 0.0))
        hint = _sub(pos[mid], top)
        if normals and _norm(normals.get(mid, (0.0, 0.0, 0.0))) > 0.0:
            hint = _cross(normals[mid], u)   # Toward the bend: n = u x side, so side = n x u.
        side = _sub(hint, _mul(u, _dot(hint, u)))
        if _norm(side) < 1e-12:
            side = _cross(u, (1.0, 0.0, 0.0)) if abs(u[0]) < 0.9 else _cross(u, (0.0, 1.0, 0.0))
        pos[mid] = _add(top, _add(_mul(u, x), _mul(_unit(side), h)))
        pos[chain[2]] = _add(top, _mul(u, d))
        return set(chain[1:])
    for _ in range(rounds):
        pos[chain[-1]] = goal
        for k in range(len(chain) - 2, 0, -1):
            pos[chain[k]] = _toward(pos[chain[k + 1]], pos[chain[k]], length[chain[k + 1]])
        for k in range(1, len(chain)):
            pos[chain[k]] = _toward(pos[chain[k - 1]], pos[chain[k]], length[chain[k]])
        if _norm(_sub(pos[chain[-1]], goal)) < 1e-9:
            break
    return set(chain[1:])


def _order(parents):
    """Joints with their depth, root first."""
    depth = {}

    def d(j):
        if j not in depth:
            p = parents.get(j)
            depth[j] = 0 if p is None else d(p) + 1
        return depth[j]
    for j in parents:
        d(j)
    return sorted(parents, key=lambda j: depth[j])


def _active(parents, ends):
    """The joints on a path from the root to an effector or pin (the rest keep their place relative to parents)."""
    out = set()
    for j in ends:
        while j is not None and j not in out:
            out.add(j)
            j = parents.get(j)
    return out


def _keep_hinge(base, joint, end, rest_normal):
    """``joint`` moved, if needed, so the bend of base-joint-end stays on the ``rest_normal`` side."""
    axis = _sub(end, base)
    if _norm(axis) < 1e-9:
        return joint
    normal = _cross(axis, _sub(joint, base))
    if _dot(normal, rest_normal) >= 0.0:
        return joint
    # Mirror the joint across the base-end line: the same lengths, the bend on the rest side.
    a = _unit(axis)
    rel = _sub(joint, base)
    along = _mul(a, _dot(rel, a))
    return _add(base, _sub(_mul(along, 2.0), rel))


def solve(joints, parents, targets, pinned=(), hinges=None, iterations=24, tolerance=1e-3, limits=None, balance=None,
          patience=None, rest=None):
    """``joints``: {name: rest position}; ``parents``: {name: parent or None} (one root); ``targets``: {name:
    position} for effectors; ``pinned``: names held at their current place; ``hinges``: {joint: (base, end,
    preferred normal)} for elbows and knees; ``limits``: {joint: the most its link may swing from rest, radians};
    ``balance``: {"segments": [(a, b, mass)], "support": [joint], "radius": r, "up": (x, y, z)}; ``patience``: with
    it, ``iterations`` is only the bound: the solve also stops once the largest error has improved by less than
    ``tolerance`` over that many iterations (converged as far as it goes); ``rest``: {name: position} of the body at
    rest, which ranges of motion count from (default ``joints``: posing starts from the current pose, but a range is
    the body's, so a joint already bent bends only the rest of its range).

    Returns {"positions": {name: position}, "errors": {effector: distance}, "reached": bool, "iterations": n}, plus
    "limited" (the joints a limit held) with ``limits`` and "balance" ({"outside": distance}) with ``balance``.

    With ``limits``, a solve that falls short is run once more letting the body move as a whole by what the targets
    still lack (``_solve(..., body_moves=True)``), and the closer of the two is returned: held at its limits, the chain
    pulls the hips only along its straight line, so a body that must lower on its knees never did; but moving the
    whole body slows a deep fold, which the chain alone reaches (test_native_body_ik_reach)."""
    args = (joints, parents, targets, pinned, hinges, iterations, tolerance, limits, balance, patience, rest)
    first = _solve(*args, body_moves=False)
    if not limits or first["reached"] or not targets:
        return first
    second = _solve(*args, body_moves=True)
    worst = lambda out: max((out["errors"][e] for e in targets), default=0.0)  # noqa: E731
    return second if worst(second) < worst(first) else first


def _solve(joints, parents, targets, pinned, hinges, iterations, tolerance, limits, balance, patience, rest,
           body_moves):
    hinges = hinges or {}
    goals = dict(targets)
    for j in pinned:
        goals.setdefault(j, joints[j])
    length = {j: _norm(_sub(joints[j], joints[p])) for j, p in parents.items() if p is not None}
    children = {}
    for j, p in parents.items():
        children.setdefault(p, []).append(j)
    order = _order(parents)
    active = _active(parents, goals)
    rest_normal = {}
    for j, (base, end, preferred) in hinges.items():
        n = _cross(_sub(joints[end], joints[base]), _sub(joints[j], joints[base]))
        rest_normal[j] = n if _norm(n) > 1e-6 * max(_norm(_sub(joints[end], joints[base])), 1e-9) ** 2 else preferred
    offset = {j: _sub(joints[j], joints[p]) for j, p in parents.items() if p is not None}
    rest = rest or joints
    rest_offset = {j: _sub(rest[j], rest[p]) for j, p in parents.items() if p is not None}
    from_rest = {}   # A branching joint's turn from rest to the start (its ranges count from rest).
    holds = _holds(parents, children, length, pinned)
    to_pins = _active(parents, pinned)   # Joints with a pinned joint at or below them.
    pos = dict(joints)
    turn = {}
    done = 0
    root = next(j for j, p in parents.items() if p is None)
    settled = set()   # The joints a pinned chain's settling places (below the chain's top joint).
    for pin in pinned:
        j = pin
        while parents.get(j) is not None and len(children[parents[j]]) == 1:
            settled.add(j)
            j = parents[j]
    on_pins = set()
    for pin in pinned:   # The pinned chains' joints (settled exactly; limits leave them to the pins).
        j = pin
        while j is not None and parents.get(j) is not None and len(children[parents[j]]) == 1:
            on_pins.add(j)
            j = parents[j]
        on_pins.add(j)
    limited = []
    frames = {}   # With limits: each link's carried frame, from the last forward pass.
    history = []   # The largest error after each iteration (``patience``).

    def settle():
        moved = set()
        for pin in pinned:   # Pinned chains placed exactly from where their top joint now is.
            moved |= _settle(pos, pin, goals[pin], parents, children, length, normals=rest_normal)
        for j in order:   # What hangs below a settled joint follows it.
            p = parents.get(j)
            if p in moved and j not in moved:
                pos[j] = (_toward(pos[p], pos[j], length[j]) if j in active
                          else _add(pos[p], _sub(joints[j], joints[p])))
                moved.add(j)

    def move_body(by):
        """Everything but the settled pinned chains moved by ``by``, the hips held within the pins' reach; the pinned
        chains settled again. False when the pins allow no move."""
        delta = _sub(_keep_pins_in_reach(_add(pos[root], by), holds.get(root, ()), turn.get(root), offset, goals),
                     pos[root])
        if _norm(delta) < 1e-12:
            return False
        for j in pos:
            if j not in settled:
                pos[j] = _add(pos[j], delta)
        settle()
        return True

    def outside_by():
        if balance is None:
            return (0.0, 0.0, 0.0)
        centre = body_ik_limits.com(pos, balance["segments"])
        return body_ik_limits.deficit(centre, [pos[s] for s in balance["support"]], balance.get("radius", 0.0),
                                      balance.get("up", (0.0, 0.0, 1.0)))
    for done in range(1, iterations + 1):
        # Backward: effectors to the root.
        asked = {}
        for j in reversed(order):
            if j not in active:
                continue
            wishes = asked.get(j, [])
            if j in goals:
                wishes = [goals[j]] + wishes
            if not wishes:
                continue
            if j in pinned:
                here = goals[j]
            else:
                here = _keep_pins_in_reach(_mean(wishes), holds.get(j, ()), turn.get(j), offset, goals)
            pos[j] = here
            p = parents.get(j)
            if p is not None:
                wish = _toward(here, pos[p], length[j])
                if limits and j in limits and j not in on_pins and len(children[p]) == 1 and p in frames:
                    # The cone both ways: the parent is asked to stand where the link may point from it (in the last
                    # forward pass's frame), so a link at its limit passes the rest of the bend up the body.
                    link, was = body_ik_limits.clamp_direction(frames[p], rest_offset[j], _sub(here, wish), limits[j])
                    if was:
                        wish = _sub(here, link)
                asked.setdefault(p, []).append(wish)
        # Forward: the root outward (the root floats; a pinned root stays).
        turn = {}   # Kept for the next backward pass: where a branch's children sit under its last rotation.
        frames, held = {root: body_ik_limits.IDENTITY}, []   # With limits: each link's carried frame.
        for j in order:
            p = parents.get(j)
            if p is None:
                continue
            if len(children[p]) > 1:   # One bone carries them all: one rotation, fitted to the children asking.
                if p not in turn:
                    kids = [k for k in children[p] if k in active]
                    turn[p] = _fit_branch([(offset[k], _sub(pos[k], pos[p]), 1.0) for k in kids if k in to_pins],
                                          [(offset[k], _sub(pos[k], pos[p]), 1.0) for k in kids if k not in to_pins])
                    cone = min((limits[k] for k in children[p] if k in limits), default=None) if limits else None
                    if cone is not None and parents.get(p) is not None and not any(k in on_pins for k in children[p]):
                        if p not in from_rest:
                            from_rest[p] = _fit_rotation([(rest_offset[k], offset[k], 1.0) for k in children[p]])
                        whole, was = body_ik_limits.clamp_turn(body_ik_limits.qmul(turn[p], from_rest[p]), frames[p],
                                                               cone)
                        turn[p] = body_ik_limits.qmul(whole, body_ik_limits.conj(from_rest[p]))
                        held += [p] if was else []
                pos[j] = _add(pos[p], _rotate(turn[p], offset[j]))
            elif j in active:
                pos[j] = _toward(pos[p], pos[j], length[j])
                if limits and j in limits and j not in on_pins:
                    now, was = body_ik_limits.clamp_direction(frames[p], rest_offset[j], _sub(pos[j], pos[p]),
                                                              limits[j])
                    if was:
                        pos[j] = _add(pos[p], now)
                        held.append(j)
            else:   # Off every path: carried along rigidly with its parent.
                pos[j] = _add(pos[p], _sub(joints[j], joints[p]))
            if limits:
                frames[j] = body_ik_limits.carry(frames[p], rest_offset[j], _sub(pos[j], pos[p]))
        for j, (base, end, _) in hinges.items():
            if j in pos and base in pos and end in pos:
                kept = _keep_hinge(pos[base], pos[j], pos[end], rest_normal[j])
                if kept != pos[j]:
                    pos[j] = kept
        if limits:
            _, after = body_ik_limits.enforce(pos, order, parents, children, rest_offset, limits, _fit_rotation,
                                              on_pins, active)
            limited = held + after
        settle()
        free_root = root not in pinned and root not in targets
        reach = [g for e, g in targets.items() if e not in pinned]
        if body_moves and free_root and reach and limited:
            # The body moves as a whole by what the targets still lack: held at its limits, the chain pulls the
            # hips only along its straight line, so a body that had to lower on its knees or shift its hips never
            # did (2.2-11.6% of the height short on reachable targets, test_native_body_ik_reach).
            left = _mean([_sub(g, pos[e]) for e, g in targets.items() if e not in pinned])
            if _norm(left) > tolerance:
                move_body(left)
        for _ in range(BALANCE_ROUNDS if balance is not None and root not in pinned else 0):
            gap = outside_by()
            if _norm(gap) <= tolerance / 2.0:
                break
            if not move_body(_mul(gap, -1.0)):
                break
        errors = {j: _norm(_sub(pos[j], g)) for j, g in goals.items()}
        worst = max(errors.values(), default=0.0)
        if worst <= tolerance and _norm(outside_by()) <= tolerance:
            break
        history.append(worst)
        if patience and len(history) > patience and history[-1 - patience] - worst < tolerance:
            break
    errors = {j: _norm(_sub(pos[j], g)) for j, g in goals.items()}
    out = {"positions": pos, "errors": errors, "reached": max(errors.values(), default=0.0) <= tolerance,
           "iterations": done}
    if limits is not None:
        out["limited"] = sorted(set(limited))
    if balance is not None:
        out["balance"] = {"outside": _norm(outside_by())}
    return out
