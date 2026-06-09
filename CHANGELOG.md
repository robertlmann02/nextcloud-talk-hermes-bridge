# Changelog

## 0.1.3 - 2026-06-08

- Add optional local voice-message transcription for Nextcloud Talk audio/file-share webhook payloads using ffmpeg plus whisper.cpp when local Nextcloud data access is available.
- Correct the previous voice-message behavior where audio shares were only described as uploaded files and the assistant had to ask for typed text even when the audio file was locally available.
- Keep transcription best-effort: if local file resolution or transcription is unavailable, the bridge still handles the Talk file event without crashing.

## 0.1.2 - 2026-06-07

- Accept rendered Nextcloud Talk file-placeholder events where webhook content uses `{"message":"{file}","parameters":{"file":...}}` instead of `file_shared`, including non-Create `Activity` events for voice recordings.
- Use file-object MIME type/path/name metadata when explicit `metaData` is absent, so audio shares are still identifiable by `audio/*` MIME values or file names.

## 0.1.1 - 2026-06-07

- Accept Nextcloud Talk `file_shared` JSON events even when the object type is not `Note` or the activity type is a non-Create file-share event, covering uploaded files and voice-message shares that some Talk deployments emit outside normal text-message events.
- Preserve a guard against unrelated non-Note Create events so the bridge does not treat arbitrary object payloads as chat messages.
- Keep actor-name extraction safely quoted/defaulted for voice/file-share handling to avoid crashes when actor names are absent.
- Fix editable-install package discovery so `systemd/` is excluded from the Python package set.
- Add regression tests for normal text, bot-loop suppression, non-Note voice/file-share extraction, missing actor names, and unrelated non-Note rejection.
