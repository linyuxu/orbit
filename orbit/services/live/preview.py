"""A voice's preview (Settings → Voice: hovering a voice plays it): a few seconds of that voice saying hello.

OpenAI publishes no samples, so the voice makes one: a short GPT Live session over the WebSocket transport (no SDP:
`wss://…/v1/live/sessions`, `session.start` first), told in its startup input to greet in one sentence; the input
is silence (the docs: keep input audio running), the output is PCM 16-bit 24 kHz (`session.output_audio.delta`,
base64; there is no "audio done" event, so a quiet moment after it spoke ends it), then `session.close`. Kept as a
WAV in <state>/live/voices/, one per voice and language: each is made once (a few seconds of a session, a fraction
of a cent).
"""

from __future__ import annotations

import array
import base64
import json
import math
import queue
import threading
import time
import wave
from pathlib import Path

from orbit.services import keys
from orbit.services.live import config
from orbit.services.live import ws as wsock

RATE = 24000
CHUNK_S = 0.1  # silence sent this often (the input must keep running)
SPEAK_WAIT_S = 12.0  # nothing said in this long: no preview
QUIET_END_S = 1.2  # it spoke, then this much quiet audio (or this long with no audio at all): done
WIN = 2400  # loudness is judged per 0.1 s
LOUD = 300  # RMS of a 16-bit window that is speech (the stream's silence is near 0)
MAX_S = 20.0

# One sentence per language, each naming the voice. A voice with a language says it in that language; the others
# speak the language chosen in Settings (else English).
LINES = {
    "en": "Hi, I'm {name}. Tell me what you need, and I'll hand it to your agent.",
    "zh": "你好，我是{name}。告诉我你想做什么，我来交给你的 agent。",
    "ja": "こんにちは、{name}です。やりたいことを話してください。エージェントに伝えます。",
    "ko": "안녕하세요, {name}입니다. 필요한 걸 말씀해 주시면 에이전트에게 전할게요.",
    "es": "Hola, soy {name}. Dime qué necesitas y se lo paso a tu agente.",
    "fr": "Bonjour, je suis {name}. Dites-moi ce dont vous avez besoin, je le transmets à votre agent.",
    "de": "Hallo, ich bin {name}. Sag mir, was du brauchst, und ich gebe es an deinen Agenten weiter.",
    "pt": "Olá, eu sou {name}. Diga o que você precisa e eu passo para o seu agente.",
    "hi": "नमस्ते, मैं {name} हूँ। बताइए आपको क्या चाहिए, मैं आपके एजेंट को बता दूँगी।",
}
VOICE_LANG = {"Japanese": "ja", "Korean": "ko", "Spanish": "es", "French": "fr", "German": "de", "Portuguese": "pt",
              "Hindi": "hi", "English": "en"}
_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def language_for(voice: str, settings: dict | None) -> str:
    about = dict(config.VOICES).get(voice, "")
    own = VOICE_LANG.get(about.split(",")[0].strip()) if about and about != "default" else None
    if own:
        return own
    lang = config.current(settings)["live_language"]
    return lang if lang in LINES else "en"


def path_for(state: Path, voice: str, lang: str) -> Path:
    return Path(state) / "live" / "voices" / f"{voice}-{lang}.wav"


def preview(state: Path, settings: dict | None, voice: str) -> Path:
    """The WAV of `voice` saying hello (made now when missing). ValueError: no such voice; keys-less: NoKey-like
    ValueError; RuntimeError when OpenAI gave no audio."""
    if voice not in config.VOICE_NAMES:
        raise ValueError(f"no such voice: {voice}")
    lang = language_for(voice, settings)
    out = path_for(state, voice, lang)
    with _locks_lock:
        lock = _locks.setdefault(out.name, threading.Lock())
    with lock:  # (one making per voice: a second hover waits for it)
        if out.is_file() and out.stat().st_size > 1000:
            return out
        ks = keys.entries(state, "openai")
        if not ks:
            raise PermissionError("No OpenAI API key: set one in Settings → Voice")
        for i, k in enumerate(ks):  # (the keys in order: one that fails, the next)
            try:
                pcm = record(k["key"], voice, LINES[lang].format(name=voice.capitalize()),
                             config.current(settings)["live_model"])
                break
            except (OSError, RuntimeError):
                if i + 1 == len(ks):
                    raise
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        tmp.replace(out)
        return out


def record(key: str, voice: str, line: str, model: str) -> bytes:
    """One short session: the voice says `line`; its audio as raw PCM 16-bit mono 24 kHz."""
    base = keys.OPENAI_BASE.rstrip("/")
    url = ("wss://" + base[8:] if base.startswith("https://") else "ws://" + base[7:]) + "/v1/live/sessions"
    ws = wsock.connect(url, headers={"Authorization": f"Bearer {key}"}, timeout=15)
    inbox: queue.Queue = queue.Queue()
    done = threading.Event()

    def reader():
        try:
            while not done.is_set():
                m = ws.recv()
                if isinstance(m, str):
                    inbox.put(json.loads(m))
        except (OSError, ValueError) as e:
            inbox.put({"type": "_gone", "error": str(e)})

    try:
        ws.send(json.dumps({"type": "session.start", "session": {
            "model": model,
            "instructions": "You are a voice being previewed. As soon as the session starts, say the line the "
                            "developer message gives, exactly, warmly and naturally, once. Then stay silent.",
            "input": [{"type": "message", "role": "developer",
                       "content": [{"type": "input_text", "text": f"Greet the user now by saying exactly: {line}"}]}],
            "audio": {"format": {"type": "audio/pcm", "rate": RATE}, "output": {"voice": voice}},
            "store": False,
        }}))
        threading.Thread(target=reader, daemon=True, name="voice preview").start()
        silence = base64.b64encode(b"\0" * int(RATE * CHUNK_S) * 2).decode()
        # The output streams all the time, silence too: speech is told by its loudness.
        pcm, started, last_audio, t0, next_chunk = bytearray(), False, 0.0, time.time(), 0.0
        judged, first_loud, last_loud = 0, -1, -1  # in samples
        while True:
            now = time.time()
            if started and now >= next_chunk:
                ws.send(json.dumps({"type": "session.input_audio.append", "audio": silence}))
                next_chunk = now + CHUNK_S
            spoke = first_loud >= 0
            if spoke and (judged - last_loud >= QUIET_END_S * RATE or now - last_audio > QUIET_END_S):
                break
            if now - t0 > (MAX_S if spoke else SPEAK_WAIT_S):
                break
            try:
                ev = inbox.get(timeout=0.05)
            except queue.Empty:
                continue
            t = ev.get("type")
            if t == "session.started":
                started = True
            elif t == "session.output_audio.delta":
                pcm += base64.b64decode(ev.get("delta") or "")
                last_audio = time.time()
                while (len(pcm) // 2) - judged >= WIN:
                    w = array.array("h", bytes(pcm[judged * 2:(judged + WIN) * 2]))
                    if math.sqrt(sum(x * x for x in w) / WIN) > LOUD:
                        first_loud = judged if first_loud < 0 else first_loud
                        last_loud = judged + WIN
                    judged += WIN
            elif t in ("error", "_gone", "session.closed"):
                err = ev.get("error")
                msg = err.get("message") if isinstance(err, dict) else str(err or t)
                raise RuntimeError(f"OpenAI: {msg}")
        if first_loud < 0:
            raise RuntimeError("the voice said nothing")
        try:
            ws.send(json.dumps({"type": "session.close"}))
            end = time.time() + 3
            while time.time() < end:
                try:
                    if inbox.get(timeout=0.1).get("type") in ("session.closed", "_gone"):
                        break
                except queue.Empty:
                    pass
        except OSError:
            pass
        a, b = max(0, first_loud - int(0.15 * RATE)), min(len(pcm) // 2, last_loud + int(0.3 * RATE))
        return bytes(pcm[a * 2:b * 2])  # (the speech, without the silence around it)
    finally:
        done.set()
        try:
            ws.close()
        except OSError:
            pass
