#!/usr/bin/env python3
"""Build an App Store tarball for the Hermes Talk Bridge ExApp metadata."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "hermes_talk_bridge"

def version() -> str:
    tree = ET.parse(ROOT / "appinfo" / "info.xml")
    return tree.findtext("version") or "0.0.0"

def copy_if_exists(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst)
    elif src.exists():
        shutil.copy2(src, dst)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--allow-unsigned", action="store_true", help="Build package even when appinfo/signature.json is absent")
    args = parser.parse_args()
    if not args.allow_unsigned and not (ROOT / "appinfo" / "signature.json").exists():
        raise SystemExit("appinfo/signature.json missing. Sign with occ integrity:sign-app or pass --allow-unsigned for pre-review artifacts.")
    outdir = ROOT / args.output_dir
    outdir.mkdir(parents=True, exist_ok=True)
    ver = version()
    tar_path = outdir / f"{APP_ID}-{ver}.tar.gz"
    with tempfile.TemporaryDirectory() as td:
        stage = Path(td) / APP_ID
        stage.mkdir()
        for name in ["appinfo", "README.md", "CHANGELOG.md", "LICENSE", "EXAPP_SUBMISSION.md"]:
            copy_if_exists(ROOT / name, stage / name)
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(stage, arcname=APP_ID)
    sha = subprocess.check_output(["sha256sum", str(tar_path)], text=True).split()[0]
    (tar_path.with_suffix(tar_path.suffix + ".sha256")).write_text(f"{sha}  {tar_path.name}\n")
    print(tar_path)
    print(sha)

if __name__ == "__main__":
    main()
