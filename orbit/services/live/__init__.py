"""GPT Live voice (builddoc/gpt-live.md): the page's microphone and speaker go to OpenAI over WebRTC; this server
creates the call, attaches the sideband and turns the voice's delegations into requests in the bound session."""

from orbit.services.live.session import Call, Live, NoKey

__all__ = ["Call", "Live", "NoKey"]
