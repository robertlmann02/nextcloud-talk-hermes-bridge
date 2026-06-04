#!/usr/bin/env python3
"""Nextcloud Talk webhook bridge for Hermes Agent.

Receives signed Nextcloud Talk bot webhooks, invokes `hermes chat -q`, and posts
Hermes' final response back into the Talk room using signed bot messages.
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

from .talk_context import append_turn, build_context_packet, sync_local_memory_message

APP_NAME = os.environ.get("TALK_BRIDGE_APP_NAME", "nextcloud-talk-hermes-bridge")
SECRET = os.environ["TALK_BOT_SECRET"]
NEXTCLOUD_URL = os.environ["NEXTCLOUD_URL"].rstrip("/")
HERMES = os.environ.get("HERMES_BIN", "hermes")
HERMES_PROFILE = os.environ.get("HERMES_PROFILE", "default")
HERMES_HOME_DIR = os.environ.get("HERMES_HOME_DIR", str(Path.home()))
SOURCE_NAME = os.environ.get("HERMES_SOURCE", "nextcloud-talk-hermes-bridge")
LOG = Path(os.environ.get("TALK_BRIDGE_LOG", str(Path.home() / ".local/state/nextcloud-talk-hermes-bridge/bridge.log")))
PORT = int(os.environ.get("TALK_BRIDGE_PORT", "8788"))
BIND = os.environ.get("TALK_BRIDGE_BIND", "0.0.0.0")
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
MAX_TURNS = os.environ.get("HERMES_MAX_TURNS", "90")
SOFT_TIMEOUT = int(os.environ.get("TALK_BRIDGE_SOFT_TIMEOUT", "180"))
HARD_TIMEOUT = int(os.environ.get("TALK_BRIDGE_HARD_TIMEOUT", "900"))
HEARTBEAT_INTERVAL = int(os.environ.get("TALK_BRIDGE_BACKGROUND_HEARTBEAT", "120"))
ENABLE_YOLO = os.environ.get("HERMES_YOLO", "1").lower() in {"1", "true", "yes", "on"}
ACCEPT_HOOKS = os.environ.get("HERMES_ACCEPT_HOOKS", "1").lower() in {"1", "true", "yes", "on"}


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def strip_msg(s: str) -> str:
    return re.sub(r"\{@[^}]+\}", "", s or "").strip()


def verify(headers, raw: bytes) -> bool:
    rnd = headers.get("X-Nextcloud-Talk-Random", "")
    sig = headers.get("X-Nextcloud-Talk-Signature", "")
    if not rnd or not sig:
        return False
    exp = hmac.new(SECRET.encode(), rnd.encode() + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(exp, sig.lower())


def extract(payload: dict) -> dict | None:
    if payload.get("type") != "Create":
        return None
    actor = payload.get("actor") or {}
    aid = actor.get("id", "")
    if "/bot-" in aid or aid.startswith("bots/"):
        return None
    obj = payload.get("object") or {}
    target = payload.get("target") or {}
    if obj.get("type") not in (None, "Note"):
        return None
    raw = obj.get("content") or ""
    try:
        content = json.loads(raw) if raw else {}
    except Exception:
        content = {"message": raw}
    msg = content.get("message", raw) if isinstance(content, dict) else raw
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
    if file_info:
        name = file_info.get("name", "uploaded file")
        fpath = file_info.get("path", "")
        link = file_info.get("link", "")
        msg = f"A file was uploaded in Nextcloud Talk: {name}. Path: {fpath}. Link: {link}. User message: {msg}".strip()
    msg = strip_msg(msg)
    if not msg:
        return None
    return {
        "token": target.get("id", ""),
        "message": msg,
        "message_id": int(obj.get("id", 0) or 0),
        "actor_name": actor.get("name", "User"),
    }


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


def build_prompt(message: str, actor: str, context_packet: str) -> str:
    return f"""You are {ASSISTANT_NAME}, running inside a Nextcloud Talk bridge.

Role/persona:
{ASSISTANT_ROLE}

Bridge operating rules:
- Use the configured Hermes profile: {HERMES_PROFILE}.
- Use Hermes tools to perform safe requested work when possible, then verify and report the result.
- Do not claim to access a file, email, system, or account unless tool output or provided context gives you that access/content.
- If a request is high-risk or destructive, ask for confirmation before doing it.
- For vague follow-ups, resolve from the context packet first, then from memory/session search if available.
- Output only the final user-facing reply text; no banners, metadata, or session information.

{context_packet}

{actor} wrote in Nextcloud Talk:
{message}
"""


def ask(message: str, actor: str, context_packet: str = "", token: str = "", reply_to: int = 0) -> str:
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
    if SKILLS:
        cmd.extend(["--skills", SKILLS])
    if ACCEPT_HOOKS:
        cmd.append("--accept-hooks")
    if ENABLE_YOLO:
        cmd.append("--yolo")

    env = {**os.environ, "HOME": HERMES_HOME_DIR, "TERM": "dumb", "NO_COLOR": "1"}
    try:
        start = time.time()
        proc = subprocess.Popen(
            cmd,
            cwd=HERMES_HOME_DIR,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
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


def post(token: str, message: str, reply_to: int = 0) -> int | None:
    url = f"{NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v1/bot/{token}/message"

    def send(include_reply: bool = True) -> int:
        rnd = secrets.token_hex(32)
        sig = hmac.new(SECRET.encode(), (rnd + message).encode(), hashlib.sha256).hexdigest()
        fields = {"message": message}
        if include_reply and reply_to:
            fields["replyTo"] = str(reply_to)
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


def handle(ev: dict) -> None:
    log(f"message from {ev['actor_name']} token={ev['token']} id={ev['message_id']}: {ev['message'][:250]!r}")
    namespace = os.environ.get("TALK_MEMORY_NAMESPACE", HERMES_PROFILE or "default")
    append_turn(ev["token"], "user", ev["actor_name"], ev["message"], ev["message_id"], app_name=APP_NAME)
    sync_local_memory_message(ev["token"], "user", ev["actor_name"], ev["message"], namespace=namespace, message_id=ev["message_id"])
    context_packet = build_context_packet(ev["token"], APP_NAME, ASSISTANT_NAME, current_message=ev["message"], namespace=namespace)
    reply = ask(ev["message"], ev["actor_name"], context_packet, ev["token"], ev["message_id"])
    append_turn(ev["token"], "assistant", ASSISTANT_NAME, reply, 0, app_name=APP_NAME)
    sync_local_memory_message(ev["token"], "assistant", ASSISTANT_NAME, reply, namespace=namespace)
    post(ev["token"], reply, ev["message_id"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - matches BaseHTTPRequestHandler signature
        log("http " + format % args)

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b"ok" if self.path == "/health" else b"not found")

    def do_POST(self):
        if self.path != "/hook":
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
