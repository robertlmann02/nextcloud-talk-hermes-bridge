# Changelog

## 0.1.1 - 2026-06-07

- Accept Nextcloud Talk `file_shared` JSON events even when the object type is not `Note`, covering uploaded files and voice-message shares that some Talk deployments emit as non-Note Create events.
- Preserve a guard against unrelated non-Note Create events so the bridge does not treat arbitrary object payloads as chat messages.
- Keep actor-name extraction safely quoted/defaulted for voice/file-share handling to avoid crashes when actor names are absent.
- Fix editable-install package discovery so `systemd/` is excluded from the Python package set.
- Add regression tests for normal text, bot-loop suppression, non-Note voice/file-share extraction, missing actor names, and unrelated non-Note rejection.
