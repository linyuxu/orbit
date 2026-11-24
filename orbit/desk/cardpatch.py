"""Card patches: what changed in a card since the version a client last got, so a streaming turn does not send its
whole card (every step, every tool's output) on each event.

The live stream (/api/events) tells a card's change as the whole card ({"t": "card", "card"}), or, to a client that
asked for patches (?delta=1: the page, the phone, another Orbit server following this one), as
{"t": "cardp", "sid", "id", "rev", "base", "ops"}: apply ops to the card of revision `base` to get revision `rev`. A
client that does not hold `base` (it missed an event, it loaded the card later) fetches the card instead
(GET /api/sessions/<sid>/cards/<id>?exact=1). A card that starts or stops being at work (queued → at work → completed …) goes
whole, so a client that keeps only that (notifications, the phone's list) needs nothing else; between the states of
work (thinking ⇄ running a tool) it is patched like any field.

ops, in order:
  ["s", key, value]       set a field          ["d", key]               delete a field
  ["a", key, text]        append to a string   ["L", n]                 steps cut to n
  ["S", i, step]          step i, whole        ["ss", i, key, value]    set a step's field
  ["sd", i, key]          delete a step's field                         ["sa", i, key, text]  append to a step's string
"""

from __future__ import annotations

from typing import Any

SKIP = ("rev",)  # (in the patch's head)


def _field_ops(old: dict, new: dict, prefix: tuple, skip: tuple = ()) -> list[list]:
    s, d, a = ("ss", "sd", "sa") if prefix else ("s", "d", "a")
    ops: list[list] = []
    for k, v in new.items():
        if k in skip:
            continue
        if k not in old:
            ops.append([s, *prefix, k, v])
            continue
        o = old[k]
        if o == v:
            continue
        if isinstance(v, str) and isinstance(o, str) and len(v) > len(o) and v.startswith(o):
            ops.append([a, *prefix, k, v[len(o):]])
        else:
            ops.append([s, *prefix, k, v])
    ops += [[d, *prefix, k] for k in old if k not in new and k not in skip]
    return ops


def ops(old: dict, new: dict) -> list[list]:
    """The ops that turn card `old` into card `new` (both as JSON gives them)."""
    steps_both = isinstance(old.get("steps"), list) and isinstance(new.get("steps"), list)
    out = _field_ops(old, new, (), SKIP + (("steps",) if steps_both else ()))
    if not steps_both:
        return out
    os_, ns = old["steps"], new["steps"]
    if len(ns) < len(os_):
        out.append(["L", len(ns)])
    for i, st in enumerate(ns):
        if i >= len(os_):
            out.append(["S", i, st])
            continue
        o = os_[i]
        if o == st:
            continue
        if isinstance(o, dict) and isinstance(st, dict):
            out += _field_ops(o, st, (i,))
        else:
            out.append(["S", i, st])
    return out


def apply(card: dict, ops_: list[list]) -> dict:
    """A new card: `card` with ops applied (card is not changed)."""
    n = dict(card)
    if isinstance(n.get("steps"), list):
        n["steps"] = list(n["steps"])
    for op in ops_:
        k = op[0]
        if k == "s":
            n[op[1]] = op[2]
        elif k == "d":
            n.pop(op[1], None)
        elif k == "a":
            n[op[1]] = (n.get(op[1]) or "") + op[2]
        elif k == "L":
            del n["steps"][op[1]:]
        elif k == "S":
            st = n.setdefault("steps", [])
            while len(st) <= op[1]:
                st.append({})
            st[op[1]] = op[2]
        elif k in ("ss", "sd", "sa"):
            st = dict(n["steps"][op[1]])
            if k == "ss":
                st[op[2]] = op[3]
            elif k == "sd":
                st.pop(op[2], None)
            else:
                st[op[2]] = (st.get(op[2]) or "") + op[3]
            n["steps"][op[1]] = st
    return n


def patch(old: dict | None, new: dict, active: frozenset | set = frozenset()) -> dict[str, Any] | None:
    """The patch event from the card a client last got (old) to new; None when it must go whole: nothing before it,
    no revisions, or it started or stopped being at work (a state in `active`)."""
    if not old or old.get("rev") is None or new.get("rev") is None:
        return None
    a, b = old.get("state"), new.get("state")
    if a != b and not (a in active and b in active):
        return None
    return {"t": "cardp", "sid": new.get("sid"), "id": new.get("id"), "rev": new["rev"], "base": old["rev"],
            "ops": ops(old, new)}
