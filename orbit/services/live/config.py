"""GPT Live voice settings (Settings → Voice): each a desk setting named live_*, read when a call starts.

The voice, model, standby and wake settings go into the session's create body (fixed for the call's life); language,
answer length, the spoken acknowledgement and the user's own instructions shape the instructions and the delegation
handling. Ranges are the API's (POST /v1/live/sessions).
"""

from __future__ import annotations

from typing import Any

MODELS = ("gpt-live-1.1", "gpt-live-1")

# The built-in voices: (name, what the guide's voice table says about it; "" where it says nothing).
VOICES: tuple[tuple[str, str], ...] = (
    ("marin", "default"), ("cedar", ""), ("alloy", ""), ("ash", ""), ("ballad", ""), ("coral", ""), ("echo", ""),
    ("sage", ""), ("shimmer", ""), ("verse", ""),
    ("gleam", "English, North American, feminine"), ("meridian", "English, North American, masculine"),
    ("halo", "English, British, feminine"), ("vesper", "English, British, masculine"),
    ("pearl", "English, Australian, feminine"), ("quartz", "English, Australian, feminine"),
    ("ripple", "English, Australian, masculine"), ("willow", "English, Irish, feminine"),
    ("stone", "English, Irish, masculine"), ("delta", "English, Southern U.S., feminine"),
    ("cinder", "English, Southern U.S., masculine"), ("beacon", "English, Filipino, masculine"),
    ("harema", "Japanese, feminine"), ("shida", "Japanese, masculine"), ("noeul", "Korean, feminine"),
    ("nira", "Hindi, feminine"), ("nuri", "German, feminine"), ("juni", "German, feminine"),
    ("flitz", "German, masculine"), ("brise", "French, feminine"), ("sillage", "French, masculine"),
    ("bossa", "Portuguese, Brazilian, feminine"), ("tempo", "Portuguese, Brazilian, masculine"),
    ("calandria", "Spanish, Argentine, feminine"), ("compas", "Spanish, Argentine, masculine"),
    ("hornero", "Spanish, Argentine, masculine"), ("luciernaga", "Spanish, Mexican, feminine"),
    ("destello", "Spanish, Mexican, masculine"),
)
VOICE_NAMES = tuple(v for v, _ in VOICES)

# The language it speaks: "auto" follows the user; otherwise the instructions name the language.
LANGUAGES = {"auto": "", "zh": "Mandarin Chinese (Simplified)", "en": "English", "ja": "Japanese", "ko": "Korean",
             "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese"}
WAKE = (0.3, 0.5, 0.7)  # vad_threshold: wakes on quieter speech · normal · needs clear speech
PROMPT_MAX = 4000  # characters of the user's own instructions (the API allows 16 384 tokens in all)
RATES = {"active": 0.05, "standby": 0.02}  # USD a minute, billed by the second (OpenAI's price for gpt-live-1 and 1.1)

DEFAULTS: dict[str, Any] = {
    "live_model": "gpt-live-1.1",
    "live_voice": "marin",
    "live_language": "auto",
    "live_idle_s": 30,       # standby after this many seconds without speech either way; 0: never (always active)
    "live_wake": 0.5,        # how clear speech must be to wake it from standby (vad_threshold)
    "live_ack": False,       # say a short "on it" when a task starts (off: silent until there is something to say)
    "live_detail": "brief",  # results: brief (one to three sentences) | detailed (the key details too)
    "live_context": True,    # the session's recent requests and answers at the call's start
    "live_prompt": "",       # the user's own instructions, after Orbit's
    "live_budget": 0.0,      # what the user said is left on their OpenAI account (USD; 0: not given); an API key cannot read it
    "live_budget_at": 0.0,   # when they said so (set by the desk): calls since count down from it
}


def check(k: str, v: Any) -> None:
    """ValueError when v is not a value for setting k (the type is checked by the desk)."""
    if k == "live_model" and v not in MODELS:
        raise ValueError(f"live_model is one of {', '.join(MODELS)}")
    if k == "live_voice" and v not in VOICE_NAMES:
        raise ValueError(f"no such voice: {v}")
    if k == "live_language" and v not in LANGUAGES:
        raise ValueError(f"live_language is one of {', '.join(LANGUAGES)}")
    if k == "live_idle_s" and not (v == 0 or 5 <= v <= 60):
        raise ValueError("live_idle_s is 5–60 seconds, or 0 (never standby)")
    if k == "live_wake" and v not in WAKE:
        raise ValueError(f"live_wake is one of {', '.join(map(str, WAKE))}")
    if k == "live_detail" and v not in ("brief", "detailed"):
        raise ValueError("live_detail is brief or detailed")
    if k == "live_budget" and not 0 <= v <= 100000:
        raise ValueError("live_budget is 0–100000 (USD)")
    if k == "live_prompt" and len(v) > PROMPT_MAX:
        raise ValueError(f"live_prompt is at most {PROMPT_MAX} characters")


def current(settings: dict | None) -> dict[str, Any]:
    """The live settings in force: the user's where valid, else the default."""
    out = dict(DEFAULTS)
    for k, d in DEFAULTS.items():
        v = (settings or {}).get(k, d)
        if type(v) is type(d) or (type(d) is float and type(v) is int):
            try:
                check(k, float(v) if type(d) is float else v)
                out[k] = float(v) if type(d) is float else v
            except ValueError:
                pass
    return out
