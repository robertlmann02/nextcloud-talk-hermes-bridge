"""Nextcloud Talk Hermes Bridge package."""

__version__ = "0.1.1"

__all__ = ["main"]


def main() -> None:
    """Run the bridge server.

    Imported lazily so utility modules can be imported without requiring bridge
    runtime environment variables such as TALK_BOT_SECRET and NEXTCLOUD_URL.
    """
    from .bridge import main as bridge_main

    bridge_main()
