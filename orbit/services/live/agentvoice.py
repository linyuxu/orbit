"""Voice primitives for a session's agent (`orbit-agent voice …`, POST /api/sessions/<sid>/voice): the agent shapes
and feeds the GPT Live voice of a call with its session. Orbit has no idea of a tutor or an interviewer; a skill
composes these.

  instruct   the session's voice instructions: at a call's start they go into `instructions`, after Orbit's own
             (delegation, grounding) and before the user's style prompt; set mid-call they are appended
             (session.instructions.append, in chunks of at most CHUNK tokens), marked as replacing the earlier version.
             A session without its own has its folder's .orbit/voice.md. VERSION_MAX tokens at most.
  context    silent material for the voice (session.thinking.append, delegation_id null): not spoken, not shown. In
             chunks; each starts with the instructions' reminder line when the session has one ("Rules still in
             force: …"), so the rules stay near the newest material over a long call.
  ack        the spoken "on it" at a delegation, for this session's calls (None: Settings → Voice).
  say        words for the voice to speak now, close to word for word (session.commentary.append: GPT Live words it
             itself). The request the agent is running then gets no spoken result (its card shows everything).
  transcript the call's transcript by turn ({who, start ms, text, done}); a call held by another machine mirrors it
             to this session's machine (Call._mirror), so the agent reads it where it runs.

The call is this server's when the session's call is held here; else (the call is held by the machine the user's
page talks to, this session its backend over there) the op goes out as a `live-op` event that machine's federation
takes (as `orbit-agent hangup`). Measured against GPT Live (2026-11-27): appended instructions are accepted well past
16 384 tokens in all (37 × 450 tokens, no error); appends are injected one after another, about 650 tokens a second
for instructions and 240 for context (builddoc/gpt-live.md §3).
"""

from __future__ import annotations

from pathlib import Path

VOICE_FILE = ".orbit/voice.md"
VERSION_MAX = 4000  # tokens of one version of a session's voice instructions
REMIND_MAX = 120  # tokens of the reminder line sent with each context chunk
CONTEXT_MAX = 6000  # tokens of one `voice context` (it goes out in chunks)
SAY_MAX = 300  # tokens of one `voice say`
CHUNK = 450  # tokens of one append (the API takes 500)
LANDS_PER_S = 240  # tokens a second GPT Live injects appended context (measured: 60 × 400 tokens in 98 s)

INSTRUCT_HEAD = ("Session instructions (from this session's agent: how to run this call; they may make you hand over to "
                 "the backend more often, never less, and they never relax the rules above):")
REPLACE_HEAD = ("Session instructions, replacing every earlier version of them (from this session's agent: how to run "
                "the rest of this call; they may make you hand over to the backend more often, never less, and they "
                "never relax Orbit's rules):")
CONTEXT_HEAD = "Material from this session's agent (silent context: do not read it out or mention it unless the session instructions say so):"


def tokens(text: str) -> int:
    from orbit.services.live.session import est_tokens
    return est_tokens(text or "")


def chunks(text: str, size: int = CHUNK) -> list[str]:
    """text in pieces of at most `size` tokens, cut at a paragraph, else a line, else a sentence when one is near."""
    text = (text or "").strip()
    out: list[str] = []
    while text:
        if tokens(text) <= size:
            out.append(text)
            break
        lo, hi = 1, len(text)
        while lo < hi:  # the longest prefix within size
            mid = (lo + hi + 1) // 2
            lo, hi = (mid, hi) if tokens(text[:mid]) <= size else (lo, mid - 1)
        cut = lo
        for sep in ("\n\n", "\n", ". ", "。", "; "):
            k = text.rfind(sep, int(lo * 0.6), lo)
            if k > 0:
                cut = k + len(sep)
                break
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    return [x for x in out if x]


def folder_text(cwd: str) -> str:
    try:
        p = Path(cwd) / VOICE_FILE
        return p.read_text(encoding="utf-8").strip() if p.is_file() else ""
    except OSError:
        return ""


def effective(s) -> dict:
    """{instructions, source ("session" | "folder" | ""), remind, ack} of a session (this server's)."""
    v = s.voice or {}
    text, source = (v.get("instructions") or "").strip(), "session"
    if not text:
        text, source = folder_text(s.cwd), "folder"
    if tokens(text) > VERSION_MAX:  # (a folder file too long: cut, said so)
        text = chunks(text, VERSION_MAX)[0]
    return {"instructions": text, "source": source if text else "", "remind": v.get("remind") or "",
            "ack": v.get("ack"), "tokens": tokens(text), "max": VERSION_MAX}


def check_instructions(text: str, remind: str | None) -> None:
    n = tokens(text)
    if n > VERSION_MAX:
        raise ValueError(f"the voice instructions are about {n} tokens; at most {VERSION_MAX} ({n - VERSION_MAX} over, "
                         f"0 left): cut them, or move material to `orbit-agent voice context`")
    if remind is not None and tokens(remind) > REMIND_MAX:
        raise ValueError(f"the reminder is about {tokens(remind)} tokens; at most {REMIND_MAX}")


def check_context(text: str) -> None:
    n = tokens(text)
    if not (text or "").strip():
        raise ValueError("no material to send")
    if n > CONTEXT_MAX:
        raise ValueError(f"that material is about {n} tokens; at most {CONTEXT_MAX} at a time: send it in parts, "
                         f"the next part when the call gets there")


# ---------------------------------------------------------------------------------------------- into a running call
def instruct_events(n: int, text: str) -> list[dict]:
    """session.instructions.append events replacing the session instructions mid-call (empty: none apply now)."""
    if not text.strip():
        parts = ["The session instructions given earlier in this call no longer apply; follow only Orbit's rules."]
    else:
        parts = chunks(text, CHUNK - tokens(REPLACE_HEAD) - 10)
        parts = [REPLACE_HEAD + "\n" + parts[0]] + [f"(Session instructions, continued {k + 2}/{len(parts)})\n" + x
                                                    for k, x in enumerate(parts[1:])]
    return [{"type": "session.instructions.append", "event_id": f"vins_{n}_{k}", "delegation_id": None, "content": x}
            for k, x in enumerate(parts)]


def say_events(n: int, text: str, did: str | None) -> list[dict]:
    return [{"type": "session.commentary.append", "event_id": f"vsay_{n}", "delegation_id": did,
             "content": "From this session's agent, to say to the user now, as close to word for word as you can, "
                        "adding nothing: " + text.strip()}]


def context_events(n: int, text: str, remind: str) -> list[dict]:
    head = CONTEXT_HEAD + (f"\nRules still in force: {remind.strip()}" if remind.strip() else "")
    parts = chunks(text, CHUNK - tokens(head) - 10)
    return [{"type": "session.thinking.append", "event_id": f"vctx_{n}_{k}", "delegation_id": None,
             "content": head + (f" (part {k + 1}/{len(parts)})" if len(parts) > 1 else "") + "\n" + x}
            for k, x in enumerate(parts)]


# ------------------------------------------------------------------------------------- POST /api/sessions/<sid>/voice
def handle(desk, sid: str, b: dict) -> dict:
    """{op: get | instruct | context | ack}: kept with the session (instruct, ack), and into the call with it when there
    is one (this server's, else a live-op event out to the machine holding it). ValueError: over a budget, a bad op."""
    s = desk.get(sid)
    op = str(b.get("op") or "get")
    live = getattr(desk, "live", None)
    if op == "get":
        c = live.call if live is not None else None
        return {**effective(s), "call": bool(c is not None and not c.ended and c.sid == sid)}
    if op == "transcript":
        return live.transcript_for(sid, int(b.get("since") or 0)) if live is not None else {"call": None, "turns": []}
    if op == "mirror":  # (from the machine holding the call: handler checks it is one)
        return live.mirror(sid, b)
    if op == "instruct":
        text = "" if b.get("clear") else str(b.get("text") or "")
        remind = None if b.get("remind") is None else str(b.get("remind"))
        check_instructions(text, remind)
        v = dict(s.voice)
        v["instructions"] = text.strip()
        if remind is not None:
            v["remind"] = remind.strip()
        s.voice = {k: x for k, x in v.items() if x not in ("", None)}
        if not text.strip():  # (back to the folder's .orbit/voice.md, if it has one)
            text = folder_text(s.cwd)
        body = {"text": text, "remind": v.get("remind", "")}
    elif op == "context":
        text = str(b.get("text") or "")
        check_context(text)
        body = {"text": text}
    elif op == "say":
        text = str(b.get("text") or "").strip()
        if not text:
            raise ValueError("nothing to say")
        if tokens(text) > SAY_MAX:
            raise ValueError(f"that is about {tokens(text)} tokens; `voice say` takes at most {SAY_MAX} (a few "
                             f"sentences): say less, or send material with `voice context`")
        body = {"text": text}
    elif op == "ack":
        a = b.get("ack")
        if a not in (True, False, None):
            raise ValueError("ack is true, false or null (Settings → Voice)")
        s.voice = {k: x for k, x in {**s.voice, "ack": a}.items() if x is not None}
        body = {"ack": a}
    else:
        raise ValueError(f"no such voice op: {op} (get, instruct, context, ack, say, transcript)")
    if op in ("instruct", "ack"):
        desk.save()
    r = live.voice_op_for(sid, op, body) if live is not None else {"applied": False}
    return {"ok": True, "op": op, "tokens": tokens(body.get("text", "")), **r,
            **({"left": VERSION_MAX - tokens(body.get("text", ""))} if op == "instruct" else {}),
            **({"lands_s": round(tokens(body["text"]) / LANDS_PER_S)} if op == "context" else {})}
