from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SRC = REPO_ROOT / "hermes_plugins" / "nextcloud_talk"


def _default_hermes_repo() -> Path:
    here = Path.cwd().resolve()
    candidates = [
        here,
        here / "hermes-agent",
        Path.home() / ".hermes" / "hermes-agent",
    ]
    for candidate in candidates:
        if (candidate / "cron" / "scheduler.py").exists() and (candidate / "plugins" / "platforms").is_dir():
            return candidate
    return Path.home() / ".hermes" / "hermes-agent"


def install(hermes_repo: Path, *, dry_run: bool = False) -> Path:
    hermes_repo = hermes_repo.expanduser().resolve()
    if not (hermes_repo / "cron" / "scheduler.py").exists():
        raise SystemExit(f"Hermes Agent repo not found or invalid: {hermes_repo}")
    target = hermes_repo / "plugins" / "platforms" / "nextcloud_talk"
    if dry_run:
        print(f"Would copy {PLUGIN_SRC} -> {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(PLUGIN_SRC, target)
    print(f"Installed native Hermes cron delivery platform: {target}")
    print("Restart Hermes gateway so cron discovers deliver=nextcloud_talk.")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install nextcloud_talk as a native Hermes cron delivery platform."
    )
    parser.add_argument(
        "--hermes-repo",
        default=str(_default_hermes_repo()),
        help="Path to the Hermes Agent source/install tree (default: auto-detect).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be installed.")
    args = parser.parse_args(argv)
    install(Path(args.hermes_repo), dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
