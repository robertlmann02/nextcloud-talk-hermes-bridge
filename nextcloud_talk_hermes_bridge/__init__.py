"""Nextcloud Talk Hermes Bridge package."""

__version__ = "1.0.8"

__all__ = ["main"]


def main() -> None:
    """Run the bridge server.

    Imported lazily so utility modules can be imported without requiring bridge
    runtime environment variables such as TALK_BOT_SECRET and NEXTCLOUD_URL.
    """
    from .bridge import main as bridge_main

    bridge_main()
