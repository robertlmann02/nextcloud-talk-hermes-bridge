# Changelog

## 1.0.0

- Add Talk-visible Hermes skill-management guidance: when the `skills` toolset is enabled, Hermes is prompted to report created/updated/deleted skill names in the final Nextcloud Talk reply.
- Add Talk image-share vision handling: the bridge resolves uploaded/shared images to a local readable cache and injects a `vision_analyze` instruction so Hermes can inspect pictures directly from Nextcloud Talk without asking users for an extra upload step.
- Promote the bridge to version 1.0.0 after production rollout across the managed Talk bot fleet.

## 0.2.0

- Add Nextcloud AppAPI/External App metadata for App Store submission.
- Add Docker packaging for the bridge runtime with Hermes Agent installed in the container.
- Add AppAPI lifecycle endpoints: `/heartbeat`, `/init`, and `/enabled`.
- Add release scripts for CSR generation, app signing, and App Store tarball creation.
- Keep public App Store submission docs concise by omitting certificate-request/key-handling instructions.

- Add CI and GHCR Docker publish workflows.
- Preserve the Talk context packet header/persona instructions when large local memory context must be truncated.


## 0.1.4 - 2026-06-17

- Add an optional `NEXTCLOUD_AI_CONTEXT` hook that injects bounded Nextcloud document/file search context into Hermes prompts when explicitly enabled.

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
