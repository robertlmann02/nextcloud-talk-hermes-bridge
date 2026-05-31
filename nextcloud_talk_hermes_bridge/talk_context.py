#!/usr/bin/env python3
"""Small deterministic context layer for Nextcloud Talk bridges.

Keeps per-room recent turns and a lightweight working context so one-shot
Hermes bridge invocations can understand follow-ups like "make it shorter" or
"continue that" without relying on the model to rediscover prior context.
"""
import json
import os
import re
import time
from pathlib import Path

DEFAULT_MAX_HISTORY = 80
DEFAULT_CONTEXT_TURNS = 24
MAX_MESSAGE_CHARS = 2400
MAX_PACKET_CHARS = 9000


def _safe_token(token: str) -> str:
    token = token or "default"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", token)[:80] or "default"


def _base_dir(app_name: str | None = None) -> Path:
    root = os.environ.get("TALK_CONTEXT_DIR")
    if root:
        return Path(root)
    app = app_name or os.environ.get("TALK_CONTEXT_APP") or Path.cwd().name or "talk-bot"
    return Path.home() / ".local" / "share" / "nextcloud-talk-context" / app


def _paths(token: str, app_name: str | None = None):
    base = _base_dir(app_name)
    safe = _safe_token(token)
    return base / f"{safe}.jsonl", base / f"{safe}.working.json"


def _truncate(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit // 2].rstrip() + "\n...[truncated]...\n" + text[-limit // 2 :].lstrip()


def append_turn(token: str, role: str, actor: str, message: str, message_id: int = 0, app_name: str | None = None) -> None:
    history_path, working_path = _paths(token, app_name)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": int(time.time()),
        "role": role,
        "actor": actor or role,
        "message": _truncate(message),
        "message_id": int(message_id or 0),
    }
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _trim_history(history_path)
    _update_working(working_path, record)


def _read_history(path: Path, limit: int = DEFAULT_CONTEXT_TURNS):
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _trim_history(path: Path, max_records: int = DEFAULT_MAX_HISTORY) -> None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > max_records:
            path.write_text("\n".join(lines[-max_records:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _update_working(path: Path, record: dict) -> None:
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        state = {}
    msg = record.get("message", "")
    role = record.get("role", "")
    actor = record.get("actor", role)
    state["updated_at"] = record.get("ts")
    if role == "user":
        state["last_user_actor"] = actor
        state["last_user_message"] = msg
        if len(msg) > 20 and not _looks_like_followup(msg):
            state["current_topic_hint"] = _truncate(msg, 600)
    elif role == "assistant":
        state["last_assistant_reply"] = _truncate(msg, 1200)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _looks_like_followup(text: str) -> bool:
    t = (text or "").strip().lower()
    if len(t.split()) <= 8:
        return True
    markers = [
        "that", "it", "this", "those", "these", "previous", "earlier", "last night",
        "make it", "take out", "shorter", "longer", "continue", "option", "did you forget",
        "same", "do that", "the response", "the file", "the message",
    ]
    return any(m in t for m in markers)


def build_context_packet(token: str, app_name: str, assistant_name: str, max_turns: int = DEFAULT_CONTEXT_TURNS) -> str:
    history_path, working_path = _paths(token, app_name)
    history = _read_history(history_path, max_turns)
    try:
        working = json.loads(working_path.read_text(encoding="utf-8")) if working_path.exists() else {}
    except Exception:
        working = {}

    lines = [
        "NEXTCLOUD TALK CONTEXT PACKET",
        f"Assistant/persona for this bridge: {assistant_name}.",
        f"Room token/session key: {_safe_token(token)}.",
        "Use this packet as authoritative short-term room context for follow-up messages.",
        "If the current message is vague (that/it/continue/make it shorter/the response/option B), resolve it from Recent room turns and Working room state before answering.",
        "If this packet is still insufficient and tools are available, use session_search/memory before asking the user to repeat themself.",
        "Do not mix identities across assistants, profiles, or users.",
        "",
        "Working room state:",
    ]
    if working:
        for k in ["current_topic_hint", "last_user_actor", "last_user_message", "last_assistant_reply"]:
            if working.get(k):
                lines.append(f"- {k}: {working[k]}")
    else:
        lines.append("- No prior working state recorded yet.")
    lines.append("")
    lines.append(f"Recent room turns, oldest to newest, max {max_turns}:")
    if history:
        for r in history:
            tm = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(r.get("ts", 0) or 0)))
            role = r.get("role", "?")
            actor = r.get("actor", role)
            mid = r.get("message_id", 0)
            msg = r.get("message", "")
            lines.append(f"[{tm}] {role}/{actor}#{mid}: {msg}")
    else:
        lines.append("- No prior turns recorded yet.")
    packet = "\n".join(lines).strip()
    if len(packet) > MAX_PACKET_CHARS:
        packet = packet[-MAX_PACKET_CHARS:]
    return packet
