#!/usr/bin/env python3
"""Small deterministic context layer for Nextcloud Talk bridges.

Keeps per-room recent turns and a lightweight working context so one-shot
Hermes bridge invocations can understand follow-ups like "make it shorter" or
"continue that" without relying on the model to rediscover prior context.
"""
import json
import os
import re
import sqlite3
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


def reset_context(token: str, app_name: str | None = None) -> int:
    """Delete short-term room history/working-state files for a Talk room."""
    removed = 0
    for path in _paths(token, app_name):
        try:
            if path.exists():
                path.unlink()
                removed += 1
        except Exception:
            pass
    return removed


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


def build_context_packet(
    token: str,
    app_name: str,
    assistant_name: str,
    max_turns: int = DEFAULT_CONTEXT_TURNS,
    current_message: str = "",
    namespace: str | None = None,
    include_history: bool = True,
) -> str:
    history_path, working_path = _paths(token, app_name)
    history = _read_history(history_path, max_turns) if include_history else []
    try:
        working = json.loads(working_path.read_text(encoding="utf-8")) if working_path.exists() else {}
    except Exception:
        working = {}

    lines = [
        "NEXTCLOUD TALK CONTEXT PACKET",
        f"Assistant/persona for this bridge: {assistant_name}.",
        f"Room token/session key: {_safe_token(token)}.",
        "CONTEXT PRIORITY / STALE-CONTEXT GUARD:",
        "1. Newest user message controls the task.",
        "2. Room state is background only; ignore stale/unrelated items.",
        "3. For vague follow-ups (that/it/continue/what we had before), resolve from the most relevant evidence, not just the latest room topic.",
        "4. For page/repo/file/GitHub/before wording, use tools when available: session_search for prior wording, then the original source or git history as source of truth.",
        "5. If room state conflicts with source/history, trust the newest request plus source/history.",
        "Do not mix identities across assistants, profiles, or users.",
        "",
        f"Current user message (highest priority): {current_message.strip() or '(not provided)'}",
        "",
        "Working room state (lower priority; may be stale):",
    ]
    if working:
        for k in ["current_topic_hint", "last_user_actor", "last_user_message", "last_assistant_reply"]:
            if working.get(k):
                lines.append(f"- {k}: {working[k]}")
    else:
        lines.append("- No prior working state recorded yet.")
    lines.append("")
    if include_history:
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
    else:
        lines.append("Recent room turns: omitted because Hermes resume/session history already replays this room.")
    # Preserve the bridge instructions, identity boundaries, working state, and
    # recent room turns at the front of the packet. Large uploaded Mann_Memory
    # databases can produce a big local-memory/Honcho packet; truncating from
    # the tail of the combined packet would drop the critical header and make
    # the model behave as if the bridge persona/context disappeared.
    base_packet = "\n".join(lines).strip()
    memory_packet = build_local_memory_context(current_message or working.get("last_user_message", ""), token, namespace=namespace)
    if not memory_packet:
        return base_packet[:MAX_PACKET_CHARS]

    separator = "\n\nLOCAL MEMORY CONTEXT:\n"
    budget = MAX_PACKET_CHARS - len(base_packet) - len(separator)
    if budget <= 0:
        return base_packet[:MAX_PACKET_CHARS]
    if len(memory_packet) > budget:
        memory_packet = memory_packet[: max(0, budget - 80)].rstrip() + "\n...[local memory context truncated]"
    return (base_packet + separator + memory_packet).strip()


def _memory_enabled() -> bool:
    return os.environ.get("TALK_LOCAL_MEMORY_CONTEXT", "1").lower() in {"1", "true", "yes", "on"}


def _memory_retrieval_scope() -> str:
    """Return the long-term retrieval scope for Talk local memory.

    The default is room-scoped so vague follow-ups in one conversation do not
    pull raw indexed messages from another room in the same namespace. Set
    TALK_MEMORY_RETRIEVAL_SCOPE=workspace only for deliberate diagnostics or
    explicit cross-room lookup.
    """
    scope = os.environ.get("TALK_MEMORY_RETRIEVAL_SCOPE", "room").strip().lower()
    return "workspace" if scope in {"workspace", "global", "namespace", "all"} else "room"


def _memory_namespace(namespace: str | None = None) -> str:
    raw = namespace or os.environ.get("TALK_MEMORY_NAMESPACE") or os.environ.get("HERMES_PROFILE") or "default"
    ns = re.sub(r"[^a-z0-9_.-]+", "_", raw.strip().lower().replace("-", "_"))[:80]
    return ns or "default"


def _memory_db_path() -> Path:
    raw = os.environ.get("TALK_MEMORY_DB_PATH")
    if raw:
        return Path(raw).expanduser()
    hermes_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
    return hermes_home / "local-memory" / "memory.sqlite3"


def _safe_handle(text: str) -> str:
    handle = re.sub(r"[^a-z0-9_.-]+", "_", (text or "user").strip().lower())[:80]
    return handle or "user"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w]+", (query or "").lower())
    tokens = [t for t in tokens if len(t) > 1][:12]
    return " OR ".join(f"{t}*" for t in tokens) if tokens else '""'


def _db_connect() -> sqlite3.Connection | None:
    if not _memory_enabled():
        return None
    path = _memory_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_memory_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, namespace TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, memory_type TEXT NOT NULL DEFAULT 'fact', content TEXT NOT NULL, source TEXT, confidence REAL NOT NULL DEFAULT 0.70, importance REAL NOT NULL DEFAULT 0.60, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata TEXT DEFAULT '{}');
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(id UNINDEXED, namespace UNINDEXED, memory_type UNINDEXED, content, source, tokenize = 'porter unicode61');
        CREATE TABLE IF NOT EXISTS review_queue (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, proposed_type TEXT NOT NULL DEFAULT 'fact', content TEXT NOT NULL, evidence TEXT, status TEXT NOT NULL DEFAULT 'pending', confidence REAL NOT NULL DEFAULT 0.50, created_at TEXT NOT NULL, reviewed_at TEXT, metadata TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS peers (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, handle TEXT, role TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata TEXT DEFAULT '{}', UNIQUE(namespace, handle));
        CREATE TABLE IF NOT EXISTS memory_sessions (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, session_id TEXT, peer_id TEXT, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, metadata TEXT DEFAULT '{}');
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(id UNINDEXED, namespace UNINDEXED, session_id UNINDEXED, peer_id UNINDEXED, role UNINDEXED, content, tokenize = 'porter unicode61');
        CREATE TABLE IF NOT EXISTS conclusions (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, session_id TEXT, peer_id TEXT, scope TEXT NOT NULL DEFAULT 'workspace', content TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0.70, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata TEXT DEFAULT '{}');
        CREATE VIRTUAL TABLE IF NOT EXISTS conclusions_fts USING fts5(id UNINDEXED, namespace UNINDEXED, session_id UNINDEXED, peer_id UNINDEXED, scope UNINDEXED, content, tokenize = 'porter unicode61');
        CREATE TABLE IF NOT EXISTS representations (id TEXT PRIMARY KEY, namespace TEXT NOT NULL, peer_id TEXT, kind TEXT NOT NULL DEFAULT 'peer_context', content TEXT NOT NULL, source_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata TEXT DEFAULT '{}', UNIQUE(namespace, peer_id, kind));
        """
    )



def sync_local_memory_message(token: str, role: str, actor: str, message: str, namespace: str | None = None, message_id: int = 0) -> None:
    conn = _db_connect()
    if not conn:
        return
    try:
        ns = _memory_namespace(namespace)
        _init_memory_tables(conn)
        ts = _now_iso()
        session_id = f"talk_{_safe_token(token)}"
        handle = _safe_handle(actor or role)
        peer_id = f"peer_{ns}_{handle}"
        msg_id = f"msg_talk_{_safe_token(token)}_{int(message_id or time.time() * 1000)}_{role}"
        conn.execute("INSERT OR IGNORE INTO workspaces(id, namespace, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?)", (f"ws_{ns}", ns, ts, ts, "{}"))
        conn.execute(
            "INSERT OR IGNORE INTO peers(id, namespace, handle, role, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (peer_id, ns, handle, role, ts, ts, json.dumps({"actor": actor or role, "source": "talk_bridge"})),
        )
        conn.execute(
            "INSERT OR IGNORE INTO memory_sessions(id, namespace, title, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, ns, f"Nextcloud Talk room {_safe_token(token)}", ts, ts, json.dumps({"token": _safe_token(token), "source": "talk_bridge"})),
        )
        clean_message = _truncate(message, 5000)
        conn.execute(
            "INSERT OR IGNORE INTO messages(id, namespace, session_id, peer_id, role, content, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, ns, session_id, peer_id, role, clean_message, ts, json.dumps({"talk_message_id": int(message_id or 0), "source": "talk_bridge"})),
        )
        conn.execute(
            "INSERT OR IGNORE INTO messages_fts(id, namespace, session_id, peer_id, role, content) VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, ns, session_id, peer_id, role, clean_message),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _rows_to_lines(rows, label: str, field: str = "content", limit: int = 5) -> list[str]:
    lines = []
    for row in rows[:limit]:
        try:
            text = _truncate(str(row[field]), 650).replace("\n", " ")
            lines.append(f"- {text}")
        except Exception:
            continue
    return [label, *lines] if lines else []


def build_local_memory_context(query: str, token: str = "", namespace: str | None = None, limit: int = 5) -> str:
    conn = _db_connect()
    if not conn:
        return ""
    try:
        ns = _memory_namespace(namespace)
        _init_memory_tables(conn)
        ts = _now_iso()
        conn.execute(
            "INSERT OR IGNORE INTO workspaces(id, namespace, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?)",
            (f"ws_{ns}", ns, ts, ts, json.dumps({"source": "talk_context"})),
        )
        q = _fts_query(query)
        retrieval_scope = _memory_retrieval_scope()
        current_session_id = f"talk_{_safe_token(token)}" if token else ""
        room_scoped = retrieval_scope == "room" and bool(current_session_id)
        memory_rows = []
        conclusion_rows = []
        message_rows = []
        representation_rows = []
        try:
            memory_rows = conn.execute(
                """SELECT m.*, bm25(memories_fts) AS score FROM memories_fts JOIN memories m ON m.id = memories_fts.id
                   WHERE memories_fts MATCH ? AND m.namespace = ? AND m.status = 'active'
                   ORDER BY score ASC, m.importance DESC LIMIT ?""",
                (q, ns, limit),
            ).fetchall()
        except Exception:
            try:
                memory_rows = conn.execute(
                    "SELECT * FROM memories WHERE namespace = ? AND status = 'active' AND content LIKE ? ORDER BY importance DESC, updated_at DESC LIMIT ?",
                    (ns, f"%{query}%", limit),
                ).fetchall()
            except Exception:
                memory_rows = []
        try:
            if room_scoped:
                conclusion_rows = conn.execute(
                    """SELECT c.*, bm25(conclusions_fts) AS score FROM conclusions_fts JOIN conclusions c ON c.id = conclusions_fts.id
                       WHERE conclusions_fts MATCH ? AND c.namespace = ? AND c.status = 'active'
                         AND (c.session_id = ? OR c.session_id IS NULL OR c.session_id = '')
                       ORDER BY score ASC, c.confidence DESC LIMIT ?""",
                    (q, ns, current_session_id, limit),
                ).fetchall()
            else:
                conclusion_rows = conn.execute(
                    """SELECT c.*, bm25(conclusions_fts) AS score FROM conclusions_fts JOIN conclusions c ON c.id = conclusions_fts.id
                       WHERE conclusions_fts MATCH ? AND c.namespace = ? AND c.status = 'active'
                       ORDER BY score ASC, c.confidence DESC LIMIT ?""",
                    (q, ns, limit),
                ).fetchall()
        except Exception:
            conclusion_rows = []
        try:
            if room_scoped:
                message_rows = conn.execute(
                    """SELECT m.*, bm25(messages_fts) AS score FROM messages_fts JOIN messages m ON m.id = messages_fts.id
                       WHERE messages_fts MATCH ? AND m.namespace = ? AND m.session_id = ?
                       ORDER BY score ASC LIMIT ?""",
                    (q, ns, current_session_id, limit),
                ).fetchall()
            else:
                message_rows = conn.execute(
                    """SELECT m.*, bm25(messages_fts) AS score FROM messages_fts JOIN messages m ON m.id = messages_fts.id
                       WHERE messages_fts MATCH ? AND m.namespace = ?
                       ORDER BY score ASC LIMIT ?""",
                    (q, ns, limit),
                ).fetchall()
        except Exception:
            message_rows = []
        if not message_rows and token:
            try:
                message_rows = conn.execute(
                    """SELECT * FROM messages
                       WHERE namespace = ? AND session_id = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (ns, f"talk_{_safe_token(token)}", limit),
                ).fetchall()
            except Exception:
                message_rows = []
        try:
            representation_rows = conn.execute(
                "SELECT * FROM representations WHERE namespace = ? ORDER BY updated_at DESC LIMIT 2",
                (ns,),
            ).fetchall()
        except Exception:
            representation_rows = []
        parts = [
            "LOCAL SQLITE MEMORY CONTEXT",
            f"Workspace/namespace: {ns}.",
            f"Retrieval scope: {retrieval_scope}." + (f" Current Talk session_id: {current_session_id}." if current_session_id else ""),
            "Durable memories and global representation cards may describe stable assistant-wide facts; raw indexed Talk messages are room-scoped by default.",
            "Use these as targeted long-term context; do not mix namespaces or treat raw messages as approved durable facts.",
        ]
        for block in (
            _rows_to_lines(representation_rows, "Representation cards:"),
            _rows_to_lines(conclusion_rows, "Relevant conclusions:"),
            _rows_to_lines(memory_rows, "Relevant durable memories:"),
            _rows_to_lines(message_rows, "Relevant indexed Talk/Hermes messages:"),
        ):
            if block:
                parts.extend(["", *block])
        return "\n".join(parts) if len(parts) > 3 else ""
    except Exception:
        return ""
    finally:
        conn.close()
