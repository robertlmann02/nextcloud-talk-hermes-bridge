#!/usr/bin/env python3
"""Resolve Nextcloud Talk image/file shares to readable local media for Hermes vision."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .talk_voice_transcribe import (
    _extract_file_path,
    _extract_share_id,
    _path_exists,
    resolve_share_path,
    resolve_user_file_path,
)

MEDIA_CACHE_DIR = Path(os.environ.get("TALK_MEDIA_CACHE_DIR", str(Path.home() / ".cache" / "talk-media-vision")))
MAX_IMAGE_BYTES = int(os.environ.get("TALK_IMAGE_MAX_BYTES", str(25 * 1024 * 1024)))


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run optional local helper commands without letting missing tools crash extraction."""
    try:
        return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))
    except PermissionError as exc:
        return subprocess.CompletedProcess(cmd, 126, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, exc.stdout or "", exc.stderr or str(exc))
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def _safe_suffix(path: Path, display_name: str = "") -> str:
    suffix = path.suffix or Path(display_name).suffix or ".image"
    suffix = suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}:
        suffix = ".image"
    return suffix


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except PermissionError:
        st = _run(["sudo", "-n", "stat", "-c", "%s", str(path)], timeout=10)
        if st.returncode != 0:
            return -1
        try:
            return int(st.stdout.strip())
        except Exception:
            return -1
    except OSError:
        return -1


def _copy_to_cache(src: Path, display_name: str = "") -> Path | None:
    size = _file_size(src)
    if size <= 0 or size > MAX_IMAGE_BYTES:
        return None
    MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        mtime = int(src.stat().st_mtime)
    except Exception:
        mtime = os.getpid()
    stem = Path(display_name or src.name or "talk-image").stem[:80] or "talk-image"
    safe_stem = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in stem)
    dst = MEDIA_CACHE_DIR / f"{safe_stem}-{mtime}{_safe_suffix(src, display_name)}"
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        cp = _run(["sudo", "-n", "cp", "--", str(src), str(dst)], timeout=30)
        if cp.returncode != 0:
            return None
        _run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(dst)], timeout=10)
    except OSError:
        return None
    try:
        os.chmod(dst, 0o600)
    except OSError:
        pass
    return dst


def resolve_talk_media_path(params) -> Path | None:
    share_id = _extract_share_id(params)
    path = resolve_share_path(share_id) if share_id else None
    if not path:
        path = resolve_user_file_path(_extract_file_path(params))
    if path and _path_exists(path):
        return path
    return None


def describe_talk_image_for_vision(params, display_name: str = "uploaded image") -> str:
    path = resolve_talk_media_path(params)
    if not path:
        return ""
    readable = _copy_to_cache(path, display_name=display_name)
    if not readable:
        return ""
    return (
        f"Local readable Talk image file for Hermes vision: {readable}\n"
        "Instruction: before answering about this upload, call the vision_analyze tool on that local image path. "
        "Do not ask Robert to re-upload it or perform an extra picture step."
    )
