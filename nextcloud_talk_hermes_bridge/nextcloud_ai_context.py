#!/usr/bin/env python3
"""Optional Nextcloud AI/document context helpers for the Talk bridge.

This module is deliberately disabled by default. When enabled, it gathers a
small, bounded context packet from Nextcloud before Hermes is invoked. The backend is deterministic file search via Nextcloud's OCS search endpoint.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_MAX_CHARS = 4000
DEFAULT_TIMEOUT = 20
DEFAULT_MIN_QUERY_CHARS = 12

DOCUMENT_HINTS = {
    "file", "files", "document", "documents", "doc", "docs", "pdf", "spreadsheet", "sheet", "sheets",
    "manual", "upload", "uploaded", "attachment", "attachments", "nextcloud", "folder", "folders", "search",
    "find", "look up", "locate", "where is", "show me", "photo", "image", "scan", "invoice", "receipt",
}
STOP_WORDS = {
    "the", "and", "for", "about", "with", "from", "that", "this", "these", "those", "please", "can", "you",
    "find", "search", "look", "locate", "show", "nextcloud", "file", "files", "document", "documents", "pdf",
    "uploaded", "upload", "folder", "folders", "where", "what", "does", "say", "tell", "need", "want", "have",
}


def _enabled() -> bool:
    return os.environ.get("NEXTCLOUD_AI_CONTEXT", "0").lower() in {"1", "true", "yes", "on"}


def _max_chars() -> int:
    try:
        return max(500, int(os.environ.get("NEXTCLOUD_AI_CONTEXT_MAX_CHARS", str(DEFAULT_MAX_CHARS))))
    except ValueError:
        return DEFAULT_MAX_CHARS


def _timeout() -> int:
    try:
        return max(1, int(os.environ.get("NEXTCLOUD_AI_CONTEXT_TIMEOUT", str(DEFAULT_TIMEOUT))))
    except ValueError:
        return DEFAULT_TIMEOUT


def _min_query_chars() -> int:
    try:
        return max(1, int(os.environ.get("NEXTCLOUD_AI_CONTEXT_MIN_QUERY_CHARS", str(DEFAULT_MIN_QUERY_CHARS))))
    except ValueError:
        return DEFAULT_MIN_QUERY_CHARS


def _include_paths() -> bool:
    return os.environ.get("NEXTCLOUD_AI_CONTEXT_INCLUDE_PATHS", "1").lower() in {"1", "true", "yes", "on"}


def _looks_document_related(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    return any(hint in text for hint in DOCUMENT_HINTS)


def _query_from_message(message: str) -> str:
    text = re.sub(r"https?://\S+", " ", message or "")
    quoted = re.findall(r"['\"]([^'\"]{2,80})['\"]", text)
    if quoted:
        return quoted[0].strip()
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,}", text)
    kept = [w for w in words if w.lower() not in STOP_WORDS]
    query = " ".join(kept[:8]).strip()
    if len(query) < _min_query_chars():
        # Fall back to the original text without common request words. This keeps
        # short specific queries like "example PDF" useful when the admin lowers
        # NEXTCLOUD_AI_CONTEXT_MIN_QUERY_CHARS.
        query = " ".join(words[:8]).strip()
    return query[:160]


def _auth_header() -> str | None:
    user = os.environ.get("NEXTCLOUD_AI_USER") or os.environ.get("NEXTCLOUD_USER")
    password = os.environ.get("NEXTCLOUD_AI_APP_PASSWORD") or os.environ.get("NEXTCLOUD_APP_PASSWORD") or os.environ.get("NEXTCLOUD_PASSWORD")
    if not user or not password:
        return None
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _ocs_file_search(query: str) -> list[dict[str, Any]]:
    base = (os.environ.get("NEXTCLOUD_URL") or "").rstrip("/")
    auth = _auth_header()
    if not base or not auth or not query:
        return []
    limit = os.environ.get("NEXTCLOUD_AI_CONTEXT_LIMIT", "6")
    params = urllib.parse.urlencode({"term": query, "limit": limit})
    url = f"{base}/ocs/v2.php/search/providers/files/search?{params}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "OCS-APIRequest": "true",
        "Authorization": auth,
        "User-Agent": "nextcloud-talk-hermes-bridge/ai-context",
    })
    with urllib.request.urlopen(req, timeout=_timeout()) as resp:
        raw = resp.read(1_000_000).decode("utf-8", "replace")
    data = json.loads(raw)
    entries = (((data.get("ocs") or {}).get("data") or {}).get("entries") or [])
    if isinstance(entries, dict):
        entries = list(entries.values())
    out: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        raw_resource = item.get("resource")
        resource: dict[str, Any] = raw_resource if isinstance(raw_resource, dict) else {}
        out.append({
            "title": item.get("title") or resource.get("name") or resource.get("basename") or "Untitled result",
            "subline": item.get("subline") or item.get("rounded") or "",
            "path": resource.get("path") or item.get("path") or resource.get("url") or "",
            "mime": resource.get("mimeType") or resource.get("mimetype") or "",
            "link": item.get("link") or resource.get("link") or resource.get("url") or "",
        })
    return out


def _format_results(query: str, results: list[dict[str, Any]]) -> str:
    if not results:
        return ""
    lines = [
        "NEXTCLOUD AI / DOCUMENT CONTEXT",
        "Source: Nextcloud deterministic file search. Treat these as candidate file matches, not full file contents.",
        f"Search query: {query}",
        "Candidate files:",
    ]
    include_paths = _include_paths()
    for idx, item in enumerate(results, 1):
        title = str(item.get("title") or "Untitled result").strip()
        mime = str(item.get("mime") or "").strip()
        path = str(item.get("path") or "").strip()
        subline = str(item.get("subline") or "").strip()
        link = str(item.get("link") or "").strip()
        detail = title
        if mime:
            detail += f" ({mime})"
        lines.append(f"{idx}. {detail}")
        if include_paths and path:
            lines.append(f"   Path: {path}")
        if subline:
            lines.append(f"   Note: {subline}")
        if link:
            lines.append(f"   Link: {link}")
    text = "\n".join(lines).strip()
    max_chars = _max_chars()
    if len(text) > max_chars:
        text = text[: max_chars - 20].rstrip() + "\n...[truncated]"
    return text


def build_nextcloud_ai_context(message: str, token: str = "", actor: str = "") -> str:
    """Return optional bounded Nextcloud context for a Talk message.

    The function must never raise to callers. Bridge availability is more
    important than retrieval. `token` and `actor` are accepted for future room or
    identity scoping; Phase 1 does not use them.
    """
    del token, actor
    if not _enabled():
        return ""
    mode = os.environ.get("NEXTCLOUD_AI_CONTEXT_MODE", "files_search").strip().lower()
    if mode not in {"files_search", "ocs_files"}:
        return ""
    if not _looks_document_related(message):
        return ""
    query = _query_from_message(message)
    if len(query) < _min_query_chars():
        return ""
    try:
        return _format_results(query, _ocs_file_search(query))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        # Avoid importing bridge.log to prevent cycles. Runtime bridge logging can
        # be added by callers later; for now failure is intentionally silent.
        _ = exc
        return ""
    except Exception:
        return ""
