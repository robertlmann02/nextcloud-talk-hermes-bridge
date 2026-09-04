#!/usr/bin/env python3
"""Nextcloud Talk webhook bridge for Hermes Agent.

Receives signed Nextcloud Talk bot webhooks, invokes `hermes chat -q`, and posts
Hermes' final response back into the Talk room using signed bot messages. It
also exposes an authenticated delivery endpoint for proactive/scheduled posts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .nextcloud_ai_context import build_nextcloud_ai_context
from .talk_context import append_turn, build_context_packet, reset_context, sync_local_memory_message
from .talk_voice_transcribe import transcribe_from_talk_params
from .talk_media_resolve import describe_talk_image_for_vision

APP_NAME = os.environ.get("TALK_BRIDGE_APP_NAME", "nextcloud-talk-hermes-bridge")
APP_ID = os.environ.get("APP_ID", "hermes_talk_bridge")
APP_VERSION = os.environ.get("APP_VERSION", "1.0.10")
SECRET = os.environ.get("TALK_BOT_SECRET") or os.environ.get("APP_SECRET") or ""
DELIVER_SECRET = os.environ.get("TALK_DELIVER_SECRET") or os.environ.get("HERMES_TALK_DELIVER_SECRET") or ""
NEXTCLOUD_URL = os.environ.get("NEXTCLOUD_URL", "http://nextcloud.local").rstrip("/")
HERMES = os.environ.get("HERMES_BIN", "hermes")
HERMES_PROFILE = os.environ.get("HERMES_PROFILE", "default")
HERMES_HOME_DIR = os.environ.get("HERMES_HOME_DIR", str(Path.home()))
SOURCE_NAME = os.environ.get("HERMES_SOURCE", "nextcloud-talk-hermes-bridge")
LOG = Path(os.environ.get("TALK_BRIDGE_LOG", str(Path.home() / ".local/state/nextcloud-talk-hermes-bridge/bridge.log")))
PORT = int(os.environ.get("TALK_BRIDGE_PORT") or os.environ.get("APP_PORT", "8788"))
BIND = os.environ.get("TALK_BRIDGE_BIND") or os.environ.get("APP_HOST", "0.0.0.0")
ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "Hermes Talk Assistant")
ASSISTANT_ROLE = os.environ.get(
    "ASSISTANT_ROLE",
    "You are a helpful private assistant inside Nextcloud Talk. Be accurate, concise, privacy-aware, and practical.",
)
TOOLSETS = os.environ.get(
    "HERMES_TOOLSETS",
    "terminal,file,code_execution,skills,memory,session_search,cronjob,web,vision,delegation",
)
SKILLS = os.environ.get("HERMES_SKILLS", "messaging-bridge-ops,hermes-agent")
SKILL_STATUS_ENABLED = os.environ.get("TALK_BRIDGE_SKILL_STATUS", "1").lower() in {"1", "true", "yes", "on"}
RECEIVED_REACTION = os.environ.get("TALK_RECEIVED_REACTION", "").strip()
MAX_TURNS = os.environ.get("HERMES_MAX_TURNS", "90")
SOFT_TIMEOUT = int(os.environ.get("TALK_BRIDGE_SOFT_TIMEOUT", "180"))
HARD_TIMEOUT = int(os.environ.get("TALK_BRIDGE_HARD_TIMEOUT", "900"))
HEARTBEAT_INTERVAL = int(os.environ.get("TALK_BRIDGE_BACKGROUND_HEARTBEAT", "120"))
ENABLE_YOLO = os.environ.get("HERMES_YOLO", "1").lower() in {"1", "true", "yes", "on"}
ACCEPT_HOOKS = os.environ.get("HERMES_ACCEPT_HOOKS", "1").lower() in {"1", "true", "yes", "on"}
APPROVAL_PROMPTS = os.environ.get("TALK_APPROVAL_PROMPTS", "0").lower() in {"1", "true", "yes", "on"}
APPROVAL_TIMEOUT = int(os.environ.get("TALK_APPROVAL_TIMEOUT", "300"))
RESUME_SESSION = (
    os.environ.get("TALK_HERMES_RESUME_SESSION")
    or os.environ.get("HERMES_RESUME_SESSION")
    or os.environ.get("HERMES_SESSION_ID")
    or ""
).strip()
_PENDING_APPROVALS: dict[str, dict] = {}
_PENDING_APPROVALS_LOCK = threading.Lock()


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def strip_msg(s: str) -> str:
    return re.sub(r"\{@[^}]+\}", "", s or "").strip()


def verify(headers, raw: bytes) -> bool:
    if not SECRET:
        log("missing TALK_BOT_SECRET/APP_SECRET; rejecting webhook")
        return False
    rnd = headers.get("X-Nextcloud-Talk-Random", "")
    sig = headers.get("X-Nextcloud-Talk-Signature", "")
    if not rnd or not sig:
        return False
    exp = hmac.new(SECRET.encode(), rnd.encode() + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(exp, sig.lower())


def verify_deliver(headers) -> bool:
    """Verify the bridge-local bearer token for proactive delivery calls."""
    deliver_secret = os.environ.get("TALK_DELIVER_SECRET") or os.environ.get("HERMES_TALK_DELIVER_SECRET") or DELIVER_SECRET
    if not deliver_secret:
        log("missing TALK_DELIVER_SECRET/HERMES_TALK_DELIVER_SECRET; rejecting delivery")
        return False
    auth = headers.get("Authorization", "")
    prefix = "Bearer "
    if not auth.startswith(prefix):
        return False
    return hmac.compare_digest(auth[len(prefix):].strip(), deliver_secret)


def extract(payload: dict) -> dict | None:
    # Normal Talk text arrives as ActivityStreams Create. Voice/file shares can
    # arrive as a non-Create activity carrying JSON content like
    # {"message":"file_shared", ...}; do not drop those before inspecting the
    # object content.
    payload_type = payload.get("type")
    actor = payload.get("actor") or {}
    aid = actor.get("id", "")
    if "/bot-" in aid or aid.startswith("bots/"):
        return None
    obj = payload.get("object") or {}
    target = payload.get("target") or {}
    # Accept JSON-content Create events as well as normal Note text. Nextcloud
    # Talk file shares and voice notes can otherwise be silently ignored.
    raw = obj.get("content") or ""
    try:
        content = json.loads(raw) if raw else {}
    except Exception:
        content = {"message": raw}
    params = {}
    if isinstance(content, dict):
        params = content.get("parameters") or content.get("messageParameters") or {}
    if isinstance(params, list):
        params = {str(i): v for i, v in enumerate(params)}
    file_info = None
    if isinstance(params, dict):
        file_info = params.get("file")
        if not file_info:
            for v in params.values():
                if isinstance(v, dict) and v.get("type") == "file":
                    file_info = v
                    break
    obj_type = obj.get("type")
    candidate_msg = content.get("message") if isinstance(content, dict) else ""
    is_talk_file_payload = isinstance(content, dict) and (
        candidate_msg in ("file_shared", "{file}") or bool(file_info)
    )
    if obj_type not in (None, "Note") and not is_talk_file_payload:
        return None
    msg = content.get("message", raw) if isinstance(content, dict) else raw
    if payload_type != "Create" and not is_talk_file_payload:
        log(f"ignored non-Create event type={payload_type!r} object_type={(obj.get('type') if isinstance(obj, dict) else '')!r} content_prefix={str(raw)[:120]!r}")
        return None
    meta = {}
    if isinstance(params, dict):
        meta = params.get("metaData") or params.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    file_name = ""
    file_path = ""
    file_mime = ""
    if isinstance(file_info, dict):
        file_name = str(file_info.get("name") or "")
        file_path = str(file_info.get("path") or "")
        file_mime = str(file_info.get("mimeType") or file_info.get("mimetype") or "")
    message_type = meta.get("messageType") or meta.get("mimeType") or file_mime or "file"
    mime_type = meta.get("mimeType", "") or file_mime
    if file_info:
        name = file_name or file_info.get("name", "uploaded file")
        fpath = file_path or file_info.get("path", "")
        link = file_info.get("link", "")
        msg = f"A {message_type} was uploaded/shared in Nextcloud Talk: {name}. Path: {fpath}. Link: {link}. User message: {msg}".strip()
    elif isinstance(content, dict) and (content.get("message") == "file_shared" or meta):
        msg = f"A {message_type} was shared in Nextcloud Talk. MIME type: {mime_type or 'unknown'}. If this is a voice note, acknowledge receipt and ask for typed text or an accessible audio file until transcription is configured."
    audio_exts = (".mp3", ".m4a", ".ogg", ".oga", ".opus", ".wav", ".webm", ".aac")
    looks_audio_file = file_name.lower().endswith(audio_exts) or file_path.lower().endswith(audio_exts)
    is_audio_payload = (
        str(message_type).lower() == "voice-message"
        or str(mime_type).lower().startswith("audio/")
        or looks_audio_file
    )
    if is_audio_payload:
        transcript = transcribe_from_talk_params(params)
        if transcript:
            msg = f"A voice message was shared in Nextcloud Talk. Transcription: {transcript}"
        elif "until transcription is configured" in msg:
            msg = msg.replace(
                "until transcription is configured",
                "because local transcription could not read this audio file",
            )
    image_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif")
    looks_image_file = file_name.lower().endswith(image_exts) or file_path.lower().endswith(image_exts)
    is_image_payload = str(mime_type).lower().startswith("image/") or looks_image_file
    if is_image_payload:
        vision_context = describe_talk_image_for_vision(params, display_name=file_name or file_path or "uploaded image")
        if vision_context:
            msg = (msg + "\n" + vision_context).strip()
        else:
            msg = (msg + "\nThis appears to be an image, but the bridge could not resolve a local readable copy yet.").strip()
    msg = strip_msg(msg)
    if not msg:
        return None
    return {
        "token": target.get("id", ""),
        "message": msg,
        "message_id": int(obj.get("id", 0) or 0),
        "actor_name": actor.get("name", "User"),
    }




def _bool_env(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes", "on"}



def _approval_session_key(token: str) -> str:
    return (token or "").strip()


def _redact_approval_text(text: str) -> str:
    text = text or ""
    text = re.sub(r"(?i)(token|secret|password|app[_-]?password|api[_-]?key|bearer)(\s*[=:]\s*)\S+", r"\1\2<redacted>", text)
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]{12,}", "Bearer <redacted>", text)
    text = re.sub(r"(?i)(ghp|gho|github_pat)_[A-Za-z0-9_]+", "<github-token-redacted>", text)
    text = re.sub(r"(?i)sk-[A-Za-z0-9_-]{20,}", "<api-key-redacted>", text)
    return text[:2500]


def _parse_approval_decision(message: str) -> str | None:
    text = (message or "").strip().lower()
    if not text:
        return None
    if text.startswith("/approve") or text in {"approve", "approved", "yes", "y", "ok"}:
        if "always" in text:
            return "always"
        if "session" in text:
            return "session"
        return "once"
    if text.startswith("/deny") or text in {"deny", "denied", "no", "n", "stop", "cancel"}:
        return "deny"
    return None


def resolve_pending_approval(token: str, message: str) -> str | None:
    decision = _parse_approval_decision(message)
    if decision is None:
        return None
    key = _approval_session_key(token)
    with _PENDING_APPROVALS_LOCK:
        pending = _PENDING_APPROVALS.get(key)
    if not pending:
        return None
    pending["decision"] = decision
    pending["event"].set()
    return decision


def _format_talk_approval_prompt(prompt_text: str, allow_permanent: bool = True) -> str:
    clean_prompt = _redact_approval_text(prompt_text)
    options = "`/approve once`, `/approve session`, `/deny`"
    if allow_permanent:
        options = "`/approve once`, `/approve session`, `/approve always`, `/deny`"
    return (
        "⚠️ **Approval required**\n\n"
        "Hermes wants to run a protected action. Review the redacted command/details below.\n\n"
        "```text\n" + clean_prompt + "\n```\n\n"
        "Reply in this Talk room with " + options + "."
    )


def _request_talk_approval(token: str, reply_to: int, prompt_text: str, stdin_pipe, allow_permanent: bool = True) -> None:
    key = _approval_session_key(token)
    event = threading.Event()
    pending = {"event": event, "decision": None, "created_at": time.time()}
    with _PENDING_APPROVALS_LOCK:
        _PENDING_APPROVALS[key] = pending
    try:
        post(token, _format_talk_approval_prompt(prompt_text, allow_permanent=allow_permanent), reply_to)
    except Exception as e:
        log(f"approval prompt post failed: {e!r}")

    def waiter():
        try:
            if not event.wait(APPROVAL_TIMEOUT):
                decision = "deny"
                try:
                    post(token, "⚠️ Approval timed out; denying the protected action.", reply_to)
                except Exception:
                    pass
            else:
                decision = pending.get("decision") or "deny"
            choice = {"once": "o\n", "session": "s\n", "always": "a\n", "deny": "d\n"}.get(decision, "d\n")
            try:
                stdin_pipe.write(choice)
                stdin_pipe.flush()
            except Exception as e:
                log(f"approval stdin write failed: {e!r}")
        finally:
            with _PENDING_APPROVALS_LOCK:
                if _PENDING_APPROVALS.get(key) is pending:
                    _PENDING_APPROVALS.pop(key, None)

    threading.Thread(target=waiter, daemon=True).start()


def _collect_process_with_talk_approvals(proc, token: str, reply_to: int, start: float) -> tuple[str, str, bool, bool]:
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    state = {"last_prompt_idx": -1, "background_notice_sent": False, "approval_requested": False}
    lock = threading.Lock()

    def maybe_detect_approval() -> None:
        if not (APPROVAL_PROMPTS and token and proc.stdin):
            return
        with lock:
            buf = "".join(stdout_chunks)
        idx = buf.rfind("DANGEROUS COMMAND:")
        if idx < 0 or idx <= state["last_prompt_idx"]:
            return
        tail = buf[idx:]
        if "Choice [" not in tail:
            return
        state["last_prompt_idx"] = idx
        state["approval_requested"] = True
        _request_talk_approval(token, reply_to, tail, proc.stdin, allow_permanent="[a]" in tail)

    def read_stream(stream, chunks, is_stdout: bool):
        try:
            while True:
                ch = stream.read(1)
                if not ch:
                    break
                with lock:
                    chunks.append(ch)
                if is_stdout:
                    maybe_detect_approval()
        except Exception as e:
            log(f"process stream read failed stdout={is_stdout}: {e!r}")

    t_out = threading.Thread(target=read_stream, args=(proc.stdout, stdout_chunks, True), daemon=True)
    t_err = threading.Thread(target=read_stream, args=(proc.stderr, stderr_chunks, False), daemon=True)
    t_out.start(); t_err.start()
    deadline = start + HARD_TIMEOUT
    soft_deadline = start + SOFT_TIMEOUT
    next_heartbeat = time.time() + HEARTBEAT_INTERVAL
    while proc.poll() is None:
        now = time.time()
        if now >= deadline:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                time.sleep(2)
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
            except Exception as e:
                log(f"hard-timeout cleanup exception: {e!r}")
            return "".join(stdout_chunks), "".join(stderr_chunks), state["background_notice_sent"], True
        if not state["background_notice_sent"] and now >= soft_deadline and not state["approval_requested"]:
            state["background_notice_sent"] = True
            log(f"hermes exceeded {SOFT_TIMEOUT}s; moving to Talk background wait")
            if token:
                post(token, "This is taking longer than normal, so I am keeping the Talk run alive in background mode and will post the result here when it finishes.", reply_to)
        if state["background_notice_sent"] and token and now >= next_heartbeat:
            elapsed = int(now - start)
            post(token, f"Still working in Nextcloud Talk background mode — {elapsed} seconds elapsed.", reply_to)
            next_heartbeat = now + HEARTBEAT_INTERVAL
        time.sleep(0.2)
    t_out.join(timeout=2); t_err.join(timeout=2)
    return "".join(stdout_chunks), "".join(stderr_chunks), state["background_notice_sent"], False

def _extract_slash_command(message: str) -> tuple[str, str] | None:
    """Return (command, args) when a Talk message is a bridge slash command."""
    msg = strip_msg(message or "")
    m = re.match(r"^\s*(?:(?:[A-Za-z][\w .-]{0,48})\s+)?/(help|status|memory|tools|reset|version|queue)\b\s*(.*)$", msg, re.I | re.S)
    if not m:
        return None
    return m.group(1).lower(), (m.group(2) or "").strip()


def handle_slash_command(ev: dict, namespace: str) -> str | None:
    parsed = _extract_slash_command(ev.get("message", ""))
    if not parsed:
        return None
    command, args = parsed
    if command == "help":
        return (
            f"{ASSISTANT_NAME} bridge commands:\n"
            "• /help — show this help\n"
            "• /status — show bridge/profile/memory status\n"
            "• /memory — show Mann_Memory/local context status\n"
            "• /tools — show enabled Hermes toolsets\n"
            "• /reset — clear this room's short-term working context\n"
            "• /version — show bridge version\n"
            "• /queue — explain long-running/background task behavior"
        )
    if command == "status":
        db_path = os.environ.get("TALK_MEMORY_DB_PATH") or os.environ.get("DEUCE_LOCAL_MEMORY_DB") or ""
        db_status = "configured" if db_path else "not configured"
        if db_path:
            db_status += ", exists" if Path(db_path).expanduser().exists() else ", missing"
        return (
            f"{ASSISTANT_NAME} status:\n"
            f"• Bridge: online\n"
            f"• App/version: {APP_ID} {APP_VERSION}\n"
            f"• Hermes profile: {HERMES_PROFILE}\n"
            f"• Memory namespace: {namespace}\n"
            f"• Mann_Memory/local DB: {db_status}\n"
            f"• Toolsets: {TOOLSETS}"
        )
    if command == "memory":
        db_path = os.environ.get("TALK_MEMORY_DB_PATH") or os.environ.get("DEUCE_LOCAL_MEMORY_DB") or ""
        enabled = _bool_env("TALK_LOCAL_MEMORY_CONTEXT", os.environ.get("TALK_LOCAL_HONCHO_CONTEXT", "1"))
        exists = Path(db_path).expanduser().exists() if db_path else False
        return (
            "Mann_Memory/local context status:\n"
            f"• Enabled: {enabled}\n"
            f"• Namespace: {namespace}\n"
            f"• DB path configured: {bool(db_path)}\n"
            f"• DB exists: {exists}\n"
            "• /reset clears only short-term room context; durable Mann_Memory is retained."
        )
    if command == "tools":
        return f"Enabled Hermes toolsets for {ASSISTANT_NAME}:\n{TOOLSETS}"
    if command == "reset":
        removed = reset_context(ev.get("token", ""), APP_NAME)
        return f"Reset this room's short-term Talk context for {ASSISTANT_NAME}. Removed {removed} context file(s). Durable Mann_Memory was not deleted."
    if command == "version":
        return f"{ASSISTANT_NAME} is running {APP_ID} version {APP_VERSION}."
    if command == "queue":
        return (
            "Long-running Talk tasks are handled by the bridge background/heartbeat flow. "
            f"Current soft timeout: {SOFT_TIMEOUT}s; hard timeout: {HARD_TIMEOUT}s; heartbeat: {HEARTBEAT_INTERVAL}s. "
            "Send the task normally; I will acknowledge it and keep working until it finishes or reaches the hard timeout."
        )
    return None


def _include_talk_history_in_context() -> bool:
    """Return whether bridge-managed recent Talk turns should be inlined.

    When Hermes is resuming a persistent room session, Hermes already replays
    its own session history. Inlining the same recent Talk turns into every `-q`
    payload duplicates context and can amplify compaction. Operators can force
    the behavior with TALK_CONTEXT_INCLUDE_HISTORY=1/0; otherwise resume mode
    automatically uses a slim packet and fresh one-shot mode keeps history.
    """
    raw = os.environ.get("TALK_CONTEXT_INCLUDE_HISTORY")
    if raw is not None:
        return raw.lower() in {"1", "true", "yes", "on"}
    return not bool(RESUME_SESSION)


def clean(out: str) -> str:
    out = (out or "").strip()
    if not out:
        return ""
    noise = (
        "Query:",
        "Initializing agent",
        "Resume this session with:",
        "Session:",
        "Duration:",
        "Messages:",
        "Tool Calls:",
        "Provider:",
        "Model:",
    )
    lines = []
    skip_query = False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Query:"):
            skip_query = True
            continue
        if skip_query:
            if s.startswith("Initializing agent") or s.startswith("─") or s.startswith("╭"):
                skip_query = False
            else:
                continue
        if not s or s.startswith(noise):
            continue
        if "session_id:" in s:
            continue
        if any(ch in s for ch in "╭╰│─╮╯⠈"):
            continue
        if "tools ·" in s or "skills ·" in s or "commits behind" in s:
            continue
        lines.append(s)
    text = "\n".join(lines).strip()
    if len(text) > 6000:
        text = text[-6000:]
    return text[:6000].strip()


def _persona_system_prompt_enabled() -> bool:
    """Return whether stable bridge persona should be sent as ephemeral system content.

    Hermes persists `chat -q` as a user message in resumed sessions. Keep the
    stable bridge identity/rules out of that persisted payload by default and
    inject them through HERMES_EPHEMERAL_SYSTEM_PROMPT instead. Set
    TALK_PERSONA_SYSTEM_PROMPT=0 for legacy Hermes builds that do not support the
    ephemeral prompt environment variable.
    """
    return os.environ.get("TALK_PERSONA_SYSTEM_PROMPT", "1").lower() in {"1", "true", "yes", "on"}


def _skill_status_rule() -> str:
    if not SKILL_STATUS_ENABLED:
        return ""
    return """
Skill-management visibility rule:
- The skills toolset is enabled when `skills` is present in HERMES_TOOLSETS; this lets Hermes create, patch, edit, or delete skills through the normal skill tools.
- If you create, patch, edit, delete, or otherwise change a Hermes skill, explicitly tell the Talk room in the final response.
- Name every skill changed and classify the action, for example: `Skills changed: created <skill-name>; patched <skill-name>`.
- If you decide a requested workflow does not need a new skill, say that no skill was created and why.
""".strip()


def build_persona_system_prompt() -> str:
    """Build stable bridge persona/rules for Hermes ephemeral system prompt."""
    skill_status_rule = _skill_status_rule()
    return f"""You are {ASSISTANT_NAME}, running inside a Nextcloud Talk bridge.

Role/persona:
{ASSISTANT_ROLE}

Bridge operating rules:
- Use the configured Hermes profile: {HERMES_PROFILE}.
- Use Hermes tools to perform safe requested work when possible, then verify and report the result.
- Do not claim to access a file, email, system, or account unless tool output or provided context gives you that access/content.
- If a request is high-risk or destructive, ask for confirmation before doing it.
- For vague follow-ups, use the current user message as the controlling request. Treat room state as lower-priority context that may be stale.
- When the user asks what was on a page/repo/file/GitHub or what existed before, use tools to inspect session history and the original source or git/file history before editing or answering.
- If context packet details conflict with retrieved source/history, trust the current user request plus source/history.
- Output only the final user-facing reply text; no banners, metadata, or session information.
{skill_status_rule}
""".strip()


def build_prompt(message: str, actor: str, context_packet: str) -> str:
    """Build the per-turn user payload passed to `hermes chat -q`.

    By default this contains only transient per-turn context and the current Talk
    message, so resumed Hermes sessions do not persist another copy of the
    stable bridge persona on every message.
    """
    per_turn = f"""{context_packet}

{actor} wrote in Nextcloud Talk:
{message}
""".strip()
    if _persona_system_prompt_enabled():
        return per_turn
    return (build_persona_system_prompt() + "\n\n" + per_turn).strip() + "\n"


def ask(message: str, actor: str, context_packet: str = "", token: str = "", reply_to: int = 0) -> str:
    extra_context = build_nextcloud_ai_context(message, token=token, actor=actor)
    if extra_context:
        context_packet = (context_packet + "\n\n" + extra_context).strip()
    prompt = build_prompt(message, actor, context_packet)
    cmd = [
        HERMES,
        "--profile",
        HERMES_PROFILE,
        "chat",
        "-q",
        prompt,
        "--source",
        SOURCE_NAME,
        "--quiet",
        "--toolsets",
        TOOLSETS,
        "--max-turns",
        MAX_TURNS,
    ]
    if RESUME_SESSION:
        cmd.extend(["--resume", RESUME_SESSION])
    if SKILLS:
        cmd.extend(["--skills", SKILLS])
    if ACCEPT_HOOKS:
        cmd.append("--accept-hooks")
    if ENABLE_YOLO and not APPROVAL_PROMPTS:
        cmd.append("--yolo")

    env = {**os.environ, "HOME": HERMES_HOME_DIR, "TERM": "dumb", "NO_COLOR": "1", "PYTHONUNBUFFERED": "1"}
    if _persona_system_prompt_enabled():
        persona_prompt = build_persona_system_prompt()
        existing_ephemeral = env.get("HERMES_EPHEMERAL_SYSTEM_PROMPT", "").strip()
        env["HERMES_EPHEMERAL_SYSTEM_PROMPT"] = (
            (existing_ephemeral + "\n\n" + persona_prompt).strip() if existing_ephemeral else persona_prompt
        )
    try:
        start = time.time()
        proc = subprocess.Popen(
            cmd,
            cwd=HERMES_HOME_DIR,
            env=env,
            text=True,
            stdin=subprocess.PIPE if APPROVAL_PROMPTS else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0 if APPROVAL_PROMPTS else -1,
            start_new_session=True,
        )
        if APPROVAL_PROMPTS:
            out, err, background_notice_sent, hard_timeout = _collect_process_with_talk_approvals(proc, token, reply_to, start)
            if hard_timeout:
                return f"I kept working in background mode but hit the {HARD_TIMEOUT // 60}-minute hard limit, so I stopped the run."
        else:
            background_notice_sent = False
            try:
                out, err = proc.communicate(timeout=SOFT_TIMEOUT)
            except subprocess.TimeoutExpired:
                background_notice_sent = True
                log(f"hermes exceeded {SOFT_TIMEOUT}s; moving to Talk background wait")
                if token:
                    post(token, "This is taking longer than normal, so I am keeping the Talk run alive in background mode and will post the result here when it finishes.", reply_to)
                deadline = start + HARD_TIMEOUT
                next_heartbeat = time.time() + HEARTBEAT_INTERVAL
                while proc.poll() is None and time.time() < deadline:
                    time.sleep(2)
                    if token and time.time() >= next_heartbeat:
                        elapsed = int(time.time() - start)
                        post(token, f"Still working in Nextcloud Talk background mode — {elapsed} seconds elapsed.", reply_to)
                        next_heartbeat = time.time() + HEARTBEAT_INTERVAL
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                        time.sleep(2)
                        if proc.poll() is None:
                            os.killpg(proc.pid, signal.SIGKILL)
                        try:
                            proc.communicate(timeout=5)
                        except Exception:
                            pass
                    except Exception as e:
                        log(f"hard-timeout cleanup exception: {e!r}")
                    return f"I kept working in background mode but hit the {HARD_TIMEOUT // 60}-minute hard limit, so I stopped the run."
                out, err = proc.communicate(timeout=10)
        elapsed = time.time() - start
        log(f"hermes completed rc={proc.returncode} elapsed={elapsed:.1f}s background={background_notice_sent}")
        if proc.returncode != 0:
            log(f"hermes failed rc={proc.returncode} stdout={(out or '')[-1000:]} stderr={(err or '')[-2000:]}")
            return "I hit an internal Hermes bridge error while answering. Please check the bridge logs."
        reply = clean(out) or "I did not get a usable response. Try again in a minute."
        if background_notice_sent:
            return "Finished the Nextcloud Talk background run:\n\n" + reply
        return reply
    except Exception as e:
        log(f"ask exception: {e!r}")
        return "I hit an internal error while answering."


def post(
    token: str,
    message: str,
    reply_to: int = 0,
    *,
    thread_title: str = "",
    thread_id: int = 0,
    silent: bool = False,
    reference_id: str = "",
) -> int | None:
    if not SECRET:
        log("missing TALK_BOT_SECRET/APP_SECRET; cannot post bot message")
        return None
    url = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v1/bot/{token}/message"
    thread_title = (thread_title or "").strip()
    reference_id = (reference_id or "").strip()

    def send(include_reply: bool = True) -> int:
        rnd = secrets.token_hex(32)
        sig = hmac.new(SECRET.encode(), (rnd + message).encode(), hashlib.sha256).hexdigest()
        fields = {"message": message}
        if reference_id:
            fields["referenceId"] = reference_id
        if silent:
            fields["silent"] = "true"
        if include_reply and reply_to:
            fields["replyTo"] = str(reply_to)
        elif thread_id:
            fields["threadId"] = str(thread_id)
        elif thread_title:
            fields["threadTitle"] = thread_title
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("OCS-APIRequest", "true")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("X-Nextcloud-Talk-Bot-Random", rnd)
        req.add_header("X-Nextcloud-Talk-Bot-Signature", sig)
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read(200).decode("utf-8", "replace")
            log(f"posted status={resp.status} body={body[:120]!r}")
            return resp.status

    try:
        return send(True)
    except urllib.error.HTTPError as e:
        body = e.read(300).decode("utf-8", "replace")
        log(f"post http error status={e.code} body={body!r}")
        if e.code == 400 and reply_to:
            try:
                return send(False)
            except Exception as e2:
                log(f"post retry without replyTo exception: {e2!r}")
        return None
    except Exception as e:
        log(f"post exception: {e!r}")
        return None


def react(token: str, message_id: int, reaction: str) -> bool:
    """Add a bot reaction to an inbound Talk message.

    This is intentionally best-effort. A failed acknowledgement reaction should
    never block the Hermes run or the final Talk reply.
    """
    token = (token or "").strip()
    reaction = (reaction or "").strip()
    if not SECRET:
        log("missing TALK_BOT_SECRET/APP_SECRET; cannot react to Talk message")
        return False
    if not token or not message_id or message_id <= 0 or not reaction:
        return False
    url = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v1/bot/{token}/reaction/{message_id}"
    rnd = secrets.token_hex(32)
    sig = hmac.new(SECRET.encode(), (rnd + reaction).encode(), hashlib.sha256).hexdigest()
    data = urllib.parse.urlencode({"reaction": reaction}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("OCS-APIRequest", "true")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("X-Nextcloud-Talk-Bot-Random", rnd)
    req.add_header("X-Nextcloud-Talk-Bot-Signature", sig)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(200).decode("utf-8", "replace")
            log(f"reacted message_id={message_id} status={resp.status} body={body[:120]!r}")
            return resp.status in {200, 201}
    except urllib.error.HTTPError as e:
        body = e.read(300).decode("utf-8", "replace")
        log(f"reaction http error status={e.code} body={body!r}")
        return False
    except Exception as e:
        log(f"reaction exception: {e!r}")
        return False


def acknowledge_received(token: str, message_id: int) -> bool:
    """Optionally mark an inbound Talk message as received before Hermes runs."""
    reaction = os.environ.get("TALK_RECEIVED_REACTION", RECEIVED_REACTION).strip()
    if not reaction:
        return False
    return react(token, message_id, reaction)


def deliver(
    token: str,
    message: str,
    actor: str = "Proactive delivery",
    reply_to: int = 0,
    *,
    thread_title: str = "",
    thread_id: int = 0,
    silent: bool = False,
    reference_id: str = "",
) -> dict:
    """Post an on-demand outbound message and record it in room context/memory.

    This is the bridge-side primitive that lets Hermes cron/webhook runs treat a
    Nextcloud Talk room as a first-class delivery target. The memory write is
    intentionally part of the success path so follow-up messages like "what did
    you mean by that?" can see the proactive assistant turn.
    """
    token = (token or "").strip()
    message = (message or "").strip()
    actor = (actor or "Proactive delivery").strip()
    if not token:
        return {"ok": False, "error": "missing room_token"}
    if not message:
        return {"ok": False, "error": "missing message"}
    log(f"proactive delivery requested token={token} actor={actor!r} message={message[:250]!r}")
    status = post(
        token,
        message,
        reply_to,
        thread_title=thread_title,
        thread_id=thread_id,
        silent=silent,
        reference_id=reference_id,
    )
    if status is None:
        return {"ok": False, "error": "post failed"}
    namespace = os.environ.get("TALK_MEMORY_NAMESPACE", HERMES_PROFILE or "default")
    append_turn(token, "assistant", ASSISTANT_NAME, message, 0, app_name=APP_NAME)
    try:
        sync_local_memory_message(token, "assistant", ASSISTANT_NAME, message, namespace=namespace)
    except Exception as e:
        log(f"proactive memory sync failed after successful post: {e!r}")
    return {"ok": True, "status": "delivered", "room_token": token, "post_status": status}


def handle(ev: dict) -> None:
    log(f"message from {ev['actor_name']} token={ev['token']} id={ev['message_id']}: {ev['message'][:250]!r}")
    approval_decision = resolve_pending_approval(ev["token"], ev["message"])
    if approval_decision is not None:
        label = "approved" if approval_decision != "deny" else "denied"
        post(ev["token"], f"Approval {label}: {approval_decision}.", ev["message_id"])
        return
    try:
        acknowledge_received(ev["token"], ev["message_id"])
    except Exception as e:
        log(f"received acknowledgement reaction failed: {e!r}")
    namespace = os.environ.get("TALK_MEMORY_NAMESPACE", HERMES_PROFILE or "default")
    command_reply = handle_slash_command(ev, namespace)
    if command_reply is not None:
        append_turn(ev["token"], "user", ev["actor_name"], ev["message"], ev["message_id"], app_name=APP_NAME)
        append_turn(ev["token"], "assistant", ASSISTANT_NAME, command_reply, 0, app_name=APP_NAME)
        post(ev["token"], command_reply, ev["message_id"])
        return
    append_turn(ev["token"], "user", ev["actor_name"], ev["message"], ev["message_id"], app_name=APP_NAME)
    sync_local_memory_message(ev["token"], "user", ev["actor_name"], ev["message"], namespace=namespace, message_id=ev["message_id"])
    context_packet = build_context_packet(
        ev["token"],
        APP_NAME,
        ASSISTANT_NAME,
        current_message=ev["message"],
        namespace=namespace,
        include_history=_include_talk_history_in_context(),
    )
    reply = ask(ev["message"], ev["actor_name"], context_packet, ev["token"], ev["message_id"])
    append_turn(ev["token"], "assistant", ASSISTANT_NAME, reply, 0, app_name=APP_NAME)
    sync_local_memory_message(ev["token"], "assistant", ASSISTANT_NAME, reply, namespace=namespace)
    post(ev["token"], reply, ev["message_id"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - matches BaseHTTPRequestHandler signature
        log("http " + format % args)

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/health", "/heartbeat"}:
            self._write_json(200, {"status": "ok", "app_id": APP_ID, "version": APP_VERSION})
            return
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"not found")

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/enabled":
            enabled = urllib.parse.parse_qs(parsed.query).get("enabled", [""])[0]
            log(f"AppAPI enabled state changed: enabled={enabled!r}")
            self._write_json(200, {"error": ""})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/init":
            self._write_json(200, {})
            return
        if parsed.path == "/deliver":
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if not verify_deliver(self.headers):
                self.send_response(401)
                self.end_headers()
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception as e:
                log(f"bad deliver json {e!r}")
                self._write_json(400, {"ok": False, "error": "invalid json"})
                return
            token = str(payload.get("room_token") or payload.get("token") or "")
            message = str(payload.get("message") or "")
            actor = str(payload.get("actor") or "Proactive delivery")
            try:
                reply_to = int(payload.get("reply_to") or payload.get("replyTo") or 0)
            except (TypeError, ValueError):
                self._write_json(400, {"ok": False, "error": "invalid reply_to"})
                return
            try:
                thread_id = int(payload.get("thread_id") or payload.get("threadId") or 0)
            except (TypeError, ValueError):
                self._write_json(400, {"ok": False, "error": "invalid thread_id"})
                return
            thread_title = str(payload.get("thread_title") or payload.get("threadTitle") or "")
            reference_id = str(payload.get("reference_id") or payload.get("referenceId") or "")
            silent_value = payload.get("silent", False)
            silent = silent_value if isinstance(silent_value, bool) else str(silent_value).lower() in {"1", "true", "yes", "on"}
            result = deliver(
                token,
                message,
                actor=actor,
                reply_to=reply_to,
                thread_title=thread_title,
                thread_id=thread_id,
                silent=silent,
                reference_id=reference_id,
            )
            self._write_json(200 if result.get("ok") else 502 if result.get("error") == "post failed" else 400, result)
            return
        if parsed.path != "/hook":
            self.send_response(404)
            self.end_headers()
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not verify(self.headers, raw):
            log("invalid signature")
            self.send_response(401)
            self.end_headers()
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            log(f"bad json {e!r}")
            self.send_response(400)
            self.end_headers()
            return
        ev = extract(payload)
        if ev:
            threading.Thread(target=handle, args=(ev,), daemon=True).start()
        self.send_response(202)
        self.end_headers()
        self.wfile.write(b"accepted")


def main() -> None:
    log(f"starting {APP_NAME} on {BIND}:{PORT}")
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
