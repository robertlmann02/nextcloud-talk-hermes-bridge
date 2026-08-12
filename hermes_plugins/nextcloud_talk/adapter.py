"""Hermes native cron delivery integration for Nextcloud Talk.

This module is intentionally small and dependency-free so it can be copied into
Hermes' bundled platform directory or loaded as a platform plugin. It registers
``deliver=nextcloud_talk`` with a standalone sender that posts finished cron
output to a trusted Nextcloud Talk Hermes Bridge ``/deliver`` endpoint.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


MAX_MESSAGE_LENGTH = 30000


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def check_requirements() -> bool:
    """No optional Python dependencies are required."""
    return True


def validate_config(_config: Any = None) -> tuple[bool, str | None]:
    missing = [
        name
        for name in ("NEXTCLOUD_TALK_DELIVER_URL", "NEXTCLOUD_TALK_DELIVER_SECRET")
        if not _env(name)
    ]
    if missing:
        return False, "Missing required env var(s): " + ", ".join(missing)
    return True, None


def is_connected() -> bool:
    return bool(_env("NEXTCLOUD_TALK_DELIVER_URL") and _env("NEXTCLOUD_TALK_DELIVER_SECRET"))


def _env_enablement() -> dict[str, Any] | None:
    if not is_connected():
        return None
    room = _env("NEXTCLOUD_TALK_HOME_ROOM")
    extra: dict[str, Any] = {"deliver_url": _env("NEXTCLOUD_TALK_DELIVER_URL")}
    if room:
        return {"home_channel": {"chat_id": room, "name": "Nextcloud Talk"}, "extra": extra}
    return {"extra": extra}


async def _standalone_send(
    _pconfig: Any,
    chat_id: str,
    message: str,
    *,
    thread_id: str | None = None,
    media_files: list[str] | None = None,
    force_document: bool = False,
) -> dict[str, Any]:
    """Send a cron result through the bridge's authenticated /deliver API."""
    endpoint = _env("NEXTCLOUD_TALK_DELIVER_URL")
    secret = _env("NEXTCLOUD_TALK_DELIVER_SECRET")
    room_token = (chat_id or _env("NEXTCLOUD_TALK_HOME_ROOM")).strip()
    if not endpoint:
        return {"error": "NEXTCLOUD_TALK_DELIVER_URL is not configured"}
    if not secret:
        return {"error": "NEXTCLOUD_TALK_DELIVER_SECRET is not configured"}
    if not room_token:
        return {"error": "NEXTCLOUD_TALK_HOME_ROOM or explicit Talk room token is required"}
    if media_files:
        return {"error": "Nextcloud Talk bridge cron delivery currently supports text messages only"}

    text = (message or "")[:MAX_MESSAGE_LENGTH]
    payload = {
        "room_token": room_token,
        "message": text,
        "actor": "hermes-cron",
    }
    if thread_id:
        payload["reply_to"] = thread_id
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + secret)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(4000).decode("utf-8", "replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        body = exc.read(400).decode("utf-8", "replace")
        return {"error": f"Nextcloud Talk bridge HTTP {exc.code}: {body[:200]}"}
    except Exception as exc:
        return {"error": f"Nextcloud Talk bridge delivery failed: {exc!r}"}

    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}
    if data.get("ok") is False:
        return {"error": f"Nextcloud Talk bridge rejected delivery: {data.get('error') or body[:200]}"}
    return {
        "success": True,
        "platform": "nextcloud_talk",
        "chat_id": room_token,
        "message_id": str(data.get("message_id") or data.get("post_id") or data.get("post_status") or status),
    }


class NextcloudTalkCronAdapter:
    """Placeholder adapter.

    Cron delivery uses ``standalone_sender_fn``. Interactive Talk messages are
    handled by the bridge itself, so this adapter deliberately does not open a
    gateway connection.
    """

    def __init__(self, config: Any):
        self.config = config

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None


def register(ctx: Any) -> None:
    ctx.register_platform(
        name="nextcloud_talk",
        label="Nextcloud Talk",
        adapter_factory=lambda cfg: NextcloudTalkCronAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=["NEXTCLOUD_TALK_DELIVER_URL", "NEXTCLOUD_TALK_DELIVER_SECRET"],
        install_hint="Install and run nextcloud-talk-hermes-bridge, then set NEXTCLOUD_TALK_* env vars.",
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="NEXTCLOUD_TALK_HOME_ROOM",
        standalone_sender_fn=_standalone_send,
        emoji="☁️",
        max_message_length=MAX_MESSAGE_LENGTH,
        pii_safe=True,
        platform_hint=(
            "You are delivering scheduled output to Nextcloud Talk through "
            "nextcloud-talk-hermes-bridge. Keep messages concise and readable."
        ),
    )
