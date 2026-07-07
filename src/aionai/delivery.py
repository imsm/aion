"""aionai.delivery — the optional "doorbell".

After a handoff is logged to a target's inbox, aionai can *summon* that tool by
opening its deeplink with a short, fixed nudge. Off by default (``AIONAI_DELIVERY``).

Security: the deeplink carries only a fixed template (segment + handoff id), never
the handoff content — so untrusted content can never ride the URL. Only schemes in
``TARGETS`` may be opened.
"""

from __future__ import annotations

import os
import webbrowser
from urllib.parse import quote, urlparse

from .store import _sanitize_segment

DELIVERY_ENABLED = os.environ.get("AIONAI_DELIVERY", "").lower() in ("1", "true", "yes")

# Doorbell rows only for verified deeplink schemes. Cursor's prompt deeplink is
# documented: prefills the chat, user must confirm, 8000-char cap.
TARGETS = {
    "cursor": {
        "scheme": "cursor",
        "path": "anysphere.cursor-deeplink/prompt",
        "param": "text",
        "max_len": 8000,
    },
}
DOORBELL_TEMPLATE = (
    "Pull your aionai inbox for segment {segment} and continue. (handoff #{entry_id})"
)


def _build_doorbell_url(target: str, segment: str, entry_id: int) -> str:
    row = TARGETS.get(target)
    if not row:
        return ""
    seg = _sanitize_segment(segment)
    text = DOORBELL_TEMPLATE.format(segment=seg, entry_id=entry_id)
    if len(text) > row["max_len"]:
        text = text[: row["max_len"]]
    return f"{row['scheme']}://{row['path']}?{row['param']}={quote(text)}"


def _open_deeplink(url: str, allowed_scheme: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != allowed_scheme:
        raise ValueError(f"scheme {parsed.scheme!r} not in allowlist")
    webbrowser.open(url)


def deliver_handoff(to: str, segment: str, entry_id: int) -> dict:
    """Fire the doorbell if enabled and the target has a verified deeplink; else the
    handoff stays inbox-only (the receiver sees it on its next pull)."""
    if not DELIVERY_ENABLED:
        return {"delivery": "inbox-only", "reason": "AIONAI_DELIVERY not enabled"}
    row = TARGETS.get(to)
    if not row:
        return {"delivery": "inbox-only", "reason": f"no verified doorbell for {to!r}"}
    try:
        _open_deeplink(_build_doorbell_url(to, segment, entry_id), row["scheme"])
    except (ValueError, OSError) as e:
        return {"delivery": "inbox-only", "reason": str(e)}
    return {"delivery": "doorbell", "target": to}
