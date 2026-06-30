#!/usr/bin/env python3
"""Optional local Nextcloud Talk voice-message transcription helper.

The bridge can receive Talk voice messages as file-share webhook payloads. This
module resolves those payloads to a local Nextcloud data-file path when the
bridge host has local data/database access, converts the audio with ffmpeg, and
transcribes it with whisper.cpp.

All integration points are opt-in via environment variables. The helper returns
an empty string when local access, ffmpeg, or whisper.cpp is unavailable so the
main bridge can continue handling the file event without failing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

NEXTCLOUD_DATA_ROOT = Path(os.environ.get("NEXTCLOUD_DATA_ROOT", "/var/www/html/data"))
NEXTCLOUD_CONTAINER = os.environ.get("TALK_NEXTCLOUD_CONTAINER", "nextcloud")
WHISPER_BIN = os.environ.get("TALK_WHISPER_BIN", "whisper-cli")
WHISPER_MODEL = os.environ.get("TALK_WHISPER_MODEL", "")
FFMPEG_BIN = os.environ.get("TALK_FFMPEG_BIN", "ffmpeg")
MAX_AUDIO_BYTES = int(os.environ.get("TALK_TRANSCRIBE_MAX_BYTES", str(50 * 1024 * 1024)))
TRANSCRIBE_TIMEOUT = int(os.environ.get("TALK_TRANSCRIBE_TIMEOUT", "180"))
DEFAULT_NEXTCLOUD_USER = os.environ.get("TALK_NEXTCLOUD_USER", "")


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run an optional local helper command without letting missing tools crash the bridge."""
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


def _which_or_exists(command: str) -> bool:
    return bool(command and (Path(command).exists() or shutil.which(command)))


def _copy_to_readable(src: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="talk-voice-"))
    suffix = src.suffix or ".audio"
    dst = tmpdir / ("input" + suffix)
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        cp = _run(["sudo", "-n", "cp", "--", str(src), str(dst)], timeout=30)
        if cp.returncode != 0:
            raise RuntimeError("could not copy voice file from Nextcloud data path")
        _run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(dst)], timeout=10)
        os.chmod(dst, 0o600)
    return dst


def _safe_nc_path(user_id: str, filecache_path: str) -> Path | None:
    if not user_id or not filecache_path:
        return None
    base = (NEXTCLOUD_DATA_ROOT / user_id).resolve()
    candidate = (base / filecache_path.lstrip("/")).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except PermissionError:
        proc = _run(["sudo", "-n", "test", "-f", str(path)], timeout=10)
        return proc.returncode == 0


def resolve_share_path(share_id: str) -> Path | None:
    """Resolve a Nextcloud oc_share.id to a local file path using container PHP/PDO."""
    if not share_id or not str(share_id).isdigit() or not NEXTCLOUD_CONTAINER:
        return None
    php = r'''
include "/var/www/html/config/config.php";
$id = intval($argv[1]);
$host = $CONFIG["dbhost"];
$db = $CONFIG["dbname"];
$user = $CONFIG["dbuser"];
$pass = $CONFIG["dbpassword"];
$prefix = $CONFIG["dbtableprefix"] ?? "oc_";
$pdo = new PDO("mysql:host=$host;dbname=$db;charset=utf8mb4", $user, $pass, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
$sql = "SELECT s.uid_owner AS uid_owner, s.file_target AS file_target, f.path AS path FROM {$prefix}share s LEFT JOIN {$prefix}filecache f ON s.file_source=f.fileid WHERE s.id=? LIMIT 1";
$st = $pdo->prepare($sql);
$st->execute([$id]);
$row = $st->fetch(PDO::FETCH_ASSOC);
echo json_encode($row ?: new stdClass());
'''
    cmd = ["docker", "exec", NEXTCLOUD_CONTAINER, "php", "-r", php, str(share_id)]
    proc = _run(cmd, timeout=30)
    if proc.returncode != 0:
        proc = _run(["sudo", "-n"] + cmd, timeout=30)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        row = json.loads(proc.stdout)
    except Exception:
        return None
    if not isinstance(row, dict):
        return None
    user_id = row.get("uid_owner") or ""
    fc_path = row.get("path") or ""
    path = _safe_nc_path(user_id, fc_path)
    if path and _path_exists(path):
        return path
    target = (row.get("file_target") or "").lstrip("/")
    if target:
        path = _safe_nc_path(user_id, "files/" + target)
        if path and _path_exists(path):
            return path
    return None


def _extract_share_id(params) -> str:
    if isinstance(params, dict):
        share = params.get("share") or params.get("shareId") or params.get("share_id")
        if share:
            return str(share)
    return ""


def _extract_file_path(params) -> str:
    """Return a user-visible Nextcloud file path from Talk {file} webhook params."""
    if not isinstance(params, dict):
        return ""
    file_info = params.get("file")
    if not isinstance(file_info, dict):
        for value in params.values():
            if isinstance(value, dict) and value.get("type") == "file":
                file_info = value
                break
    if not isinstance(file_info, dict):
        return ""
    return str(file_info.get("path") or file_info.get("name") or "")


def resolve_user_file_path(file_path: str, user_id: str | None = None) -> Path | None:
    user_id = user_id or DEFAULT_NEXTCLOUD_USER
    if not user_id or not file_path:
        return None
    cleaned = str(file_path).strip()
    if not cleaned:
        return None
    candidates = []
    if cleaned.startswith("files/"):
        candidates.append(cleaned)
    else:
        stripped = cleaned.lstrip("/")
        candidates.append("files/" + stripped)
        if "/" not in stripped:
            candidates.append("files/Talk/" + stripped)

    for rel in candidates:
        path = _safe_nc_path(user_id, rel)
        if path and _path_exists(path):
            return path

    if "/" not in cleaned.lstrip("/"):
        base = _safe_nc_path(user_id, "files")
        if base:
            try:
                for found in base.rglob(cleaned.lstrip("/")):
                    if found.is_file() or _path_exists(found):
                        return found
            except PermissionError:
                proc = _run(
                    ["sudo", "-n", "find", str(base), "-type", "f", "-name", cleaned.lstrip("/"), "-print", "-quit"],
                    timeout=20,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    candidate = Path(proc.stdout.strip().splitlines()[0])
                    try:
                        candidate.resolve().relative_to(base.resolve())
                    except Exception:
                        return None
                    return candidate
            except OSError:
                pass
    return None


def _transcribe_file(path: Path) -> str:
    if not _which_or_exists(WHISPER_BIN) or not WHISPER_MODEL or not Path(WHISPER_MODEL).exists():
        return ""
    if not _which_or_exists(FFMPEG_BIN):
        return ""
    try:
        size = path.stat().st_size
    except PermissionError:
        st = _run(["sudo", "-n", "stat", "-c", "%s", str(path)], timeout=10)
        if st.returncode != 0:
            return ""
        try:
            size = int(st.stdout.strip())
        except Exception:
            return ""
    except OSError:
        return ""
    if size <= 0 or size > MAX_AUDIO_BYTES:
        return ""
    readable = _copy_to_readable(path)
    wav = readable.parent / "audio.wav"
    ff = _run([FFMPEG_BIN, "-y", "-i", str(readable), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], timeout=60)
    if ff.returncode != 0 or not wav.exists():
        return ""
    out_base = readable.parent / "transcript"
    wh = _run([WHISPER_BIN, "-m", WHISPER_MODEL, "-f", str(wav), "-l", "auto", "-nt", "-otxt", "-of", str(out_base), "-np"], timeout=TRANSCRIBE_TIMEOUT)
    txt_path = Path(str(out_base) + ".txt")
    text = ""
    if txt_path.exists():
        text = txt_path.read_text(encoding="utf-8", errors="replace").strip()
    if not text and wh.stdout:
        text = wh.stdout.strip()
    return " ".join(text.split())[:4000]


def transcribe_from_talk_params(params) -> str:
    share_id = _extract_share_id(params)
    path = resolve_share_path(share_id) if share_id else None
    if not path:
        path = resolve_user_file_path(_extract_file_path(params))
    if not path:
        return ""
    return _transcribe_file(path)
