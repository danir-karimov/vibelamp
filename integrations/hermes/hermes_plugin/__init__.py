"""Hermes plugin: publish agent lifecycle events to the VibeLamp spool.

Hermes loads a plugin as ``~/.hermes/plugins/<name>/`` with a ``plugin.yaml``
manifest next to this file. :func:`register` subscribes to the hooks Hermes
fires around a turn and appends one JSON object per event to the spool file
that ``lampd.py`` follows.

Nothing here touches the USB device: the daemon owns the port, this side only
writes four-hundred-byte lines. Writes are best-effort — a full disk or a
read-only home must never take the agent down with it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

__all__ = ["SPOOL", "emit", "register"]

#: Keep in sync with ``lampd.spool_path()`` — both honour ``VIBELAMP_SPOOL``.
SPOOL = Path(os.environ.get("VIBELAMP_SPOOL")
             or (Path.home() / ".vibelamp" / "events.jsonl"))
#: Drop the spool instead of growing it forever if the daemon is not running.
MAX_BYTES = 4 * 1024 * 1024


def emit(kind: str, **fields) -> None:
    """Append one event; never raise into the agent loop."""
    try:
        SPOOL.parent.mkdir(parents=True, exist_ok=True)
        if SPOOL.exists() and SPOOL.stat().st_size > MAX_BYTES:
            SPOOL.write_text("", encoding="utf-8")
        record = {"t": round(time.time(), 3), "event": kind}
        record.update({key: value for key, value in fields.items()
                       if value is not None})
        with open(SPOOL, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:  # pragma: no cover - defensive by design
        pass


def _short(value, limit: int = 120):
    return str(value or "")[:limit]


def register(ctx) -> None:
    """Wire Hermes hooks to spool events. Called once by the plugin loader."""

    def on_turn_start(**kwargs):
        emit("turn_start", session_id=kwargs.get("session_id"),
             platform=kwargs.get("platform"))

    def on_api_start(**kwargs):
        emit("thinking", session_id=kwargs.get("session_id"),
             api_call=kwargs.get("api_call_count"), model=kwargs.get("model"))

    def on_api_done(**kwargs):
        emit("api_done", session_id=kwargs.get("session_id"),
             api_call=kwargs.get("api_call_count"),
             duration=kwargs.get("api_duration"),
             tool_calls=kwargs.get("assistant_tool_call_count"),
             finish=kwargs.get("finish_reason"))

    def on_api_error(**kwargs):
        emit("error", session_id=kwargs.get("session_id"),
             reason=_short(kwargs.get("reason")), status=kwargs.get("status_code"))

    def on_tool_start(**kwargs):
        emit("tool_start", session_id=kwargs.get("session_id"),
             tool=kwargs.get("tool_name"))

    def on_tool_end(**kwargs):
        emit("tool_end", session_id=kwargs.get("session_id"),
             tool=kwargs.get("tool_name"), status=kwargs.get("status"),
             duration=kwargs.get("duration_ms"),
             error=_short(kwargs.get("error_message")))

    def on_turn_end(**kwargs):
        emit("turn_end", session_id=kwargs.get("session_id"),
             reply=len(str(kwargs.get("assistant_response") or "")))

    ctx.register_hook("pre_llm_call", on_turn_start)
    ctx.register_hook("pre_api_request", on_api_start)
    ctx.register_hook("post_api_request", on_api_done)
    ctx.register_hook("api_request_error", on_api_error)
    ctx.register_hook("pre_tool_call", on_tool_start)
    ctx.register_hook("post_tool_call", on_tool_end)
    ctx.register_hook("post_llm_call", on_turn_end)
