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

try:  # Available when the plugin is installed inside Hermes Agent.
    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import BasePlatformAdapter, SendResult
except Exception:  # pragma: no cover - lets this repo test without Hermes installed.
    class Platform(str):
        def __new__(cls, value: str):
            return str.__new__(cls, value)

        @property
        def value(self) -> str:
            return str(self)

    class PlatformConfig:  # type: ignore[no-redef]
        extra: dict[str, Any] = {}

    class SendResult:  # type: ignore[no-redef]
        def __init__(self, success: bool, message_id: str | None = None, error: str | None = None):
            self.success = success
            self.message_id = message_id
            self.error = error

    class BasePlatformAdapter:  # type: ignore[no-redef]
        def __init__(self, config: Any, platform: Any):
            self.config = config
            self.platform = platform
            self._running = False

        def _mark_connected(self) -> None:
            self._running = True

        def _mark_disconnected(self) -> None:
            self._running = False

        def set_message_handler(self, handler: Any) -> None:
            self._message_handler = handler

        def set_fatal_error_handler(self, handler: Any) -> None:
            self._fatal_error_handler = handler

        def set_session_store(self, store: Any) -> None:
            self._session_store = store

        def set_busy_session_handler(self, handler: Any) -> None:
            self._busy_session_handler = handler

        def set_topic_recovery_fn(self, fn: Any) -> None:
            self._topic_recovery_fn = fn

        def set_authorization_check(self, fn: Any) -> None:
            self._authorization_check = fn

        def set_platform_event_handler(self, handler: Any) -> None:
            self._platform_event_handler = handler


MAX_MESSAGE_LENGTH = 30000


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def check_requirements() -> bool:
    """No optional Python dependencies are required."""
    return True


def validate_config(_config: Any = None) -> bool:
    return bool(_env("NEXTCLOUD_TALK_DELIVER_URL") and _env("NEXTCLOUD_TALK_DELIVER_SECRET"))


def is_connected() -> bool:
    return bool(_env("NEXTCLOUD_TALK_DELIVER_URL") and _env("NEXTCLOUD_TALK_DELIVER_SECRET"))


def _platform_value() -> Any:
    try:
        return Platform("nextcloud_talk")
    except Exception:
        class _PluginPlatform:
            value = "nextcloud_talk"

        return _PluginPlatform()


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
        payload["thread_id"] = thread_id
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


class NextcloudTalkCronAdapter(BasePlatformAdapter):
    """Delivery-capable, no-inbound Hermes platform adapter.

    Cron delivery uses ``standalone_sender_fn`` when no live gateway adapter is
    available. When the gateway does instantiate enabled platforms, Hermes still
    expects the normal ``BasePlatformAdapter`` startup contract; this adapter
    satisfies that contract without opening an inbound Talk connection. Reactive
    Talk messages remain handled by the bridge's separate ``/hook`` endpoint.
    """

    supports_async_delivery = False
    interactive_resume = False
    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config=config, platform=_platform_value())

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        result = await _standalone_send(
            self.config,
            chat_id,
            content,
            thread_id=(metadata or {}).get("thread_id") or (metadata or {}).get("threadId") or reply_to,
            media_files=(metadata or {}).get("media_files"),
            force_document=bool((metadata or {}).get("force_document", False)),
        )
        if result.get("success"):
            return SendResult(success=True, message_id=str(result.get("message_id") or ""))
        return SendResult(success=False, error=str(result.get("error") or "Nextcloud Talk delivery failed"))

    async def send_typing(self, chat_id: str, metadata: dict[str, Any] | None = None) -> None:
        return None

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"id": chat_id, "name": chat_id or "Nextcloud Talk", "type": "channel"}


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
