from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _plugin_src() -> Path:
    return _repo_root() / "hermes_plugins" / "nextcloud_talk"


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
    source = _plugin_src()
    if not source.is_dir():
        raise SystemExit(f"Bundled nextcloud_talk Hermes plugin not found: {source}")
    target = hermes_repo / "plugins" / "platforms" / "nextcloud_talk"
    if dry_run:
        print(f"Would copy {source} -> {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
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
