# Phase 1: Nextcloud AI Context Hook for Hermes Talk Bridge

## Goal

Add an optional, safe context-retrieval hook to the existing Nextcloud Talk Hermes bridge so Hermes can receive relevant Nextcloud file/document context before answering, without changing the current webhook, persona, or service behavior by default.

## Why this is step 1

The current bridge already works as:

```text
Nextcloud Talk webhook -> bridge.extract() -> build_context_packet() -> hermes chat -q -> post Talk reply
```

The lowest-risk improvement is to extend the context packet, not replace the bridge. This keeps separate assistant identities and profiles isolated while adding Nextcloud AI/document awareness only when configured.

## Phase 1 scope

Implement an optional module:

```text
nextcloud_talk_hermes_bridge/nextcloud_ai_context.py
```

The module should expose one stable function:

```python
def build_nextcloud_ai_context(message: str, token: str = "", actor: str = "") -> str:
    ...
```

It returns either:

- an empty string when disabled/unavailable/no relevant context, or
- a bounded text block titled `NEXTCLOUD AI / DOCUMENT CONTEXT` for injection into the Hermes prompt.

## Configuration

All features default off for public/sanitized safety.

Suggested env vars:

```text
NEXTCLOUD_AI_CONTEXT=0                         # enable/disable hook
NEXTCLOUD_AI_CONTEXT_MODE=files_search          # files_search first; context_chat later
NEXTCLOUD_AI_CONTEXT_MAX_CHARS=4000
NEXTCLOUD_AI_CONTEXT_TIMEOUT=20
NEXTCLOUD_AI_CONTEXT_MIN_QUERY_CHARS=12
NEXTCLOUD_AI_CONTEXT_INCLUDE_PATHS=1
```

For authenticated Nextcloud API calls, reuse existing bridge Nextcloud env where possible and add explicit app-password vars only if needed:

```text
NEXTCLOUD_URL
NEXTCLOUD_AI_USER
NEXTCLOUD_AI_APP_PASSWORD
```

Do not hardcode private URLs, room tokens, usernames, local IPs, or family/business data in the public repo.

## First backend: deterministic Nextcloud file search

Before depending on Assistant/Context Chat apps, add a deterministic retrieval backend that can work against current Nextcloud installs:

1. Detect whether the user message is likely asking about a file/document.
2. Extract a conservative search query.
3. Call Nextcloud WebDAV/OCS file search if configured.
4. Return filenames, paths, MIME types, and short metadata only.
5. Do not download file contents in Phase 1 unless a later explicit flag enables it.

This gives Hermes useful pointers without risking heavy AI installs or leaking full file contents.

## Later backends

After Phase 1 is stable:

- `context_chat`: query Nextcloud Assistant Context Chat when installed.
- `assistant_task`: submit summarization or text-processing tasks through Assistant OCS API.
- `talk_summary`: summarize recent Talk history using Nextcloud AI or Hermes local context.

## Bridge integration point

In `bridge.py`, inside `ask()` before `build_prompt()` or inside `build_prompt()`, append the optional AI context to the existing `context_packet`:

```python
extra_context = build_nextcloud_ai_context(message, token=token, actor=actor)
if extra_context:
    context_packet = (context_packet + "\n\n" + extra_context).strip()
```

This is the safest seam because:

- `extract()` remains unchanged.
- Talk HMAC verification remains unchanged.
- posting replies remains unchanged.
- Hermes command invocation remains unchanged.
- existing room history/local memory continues to work.

## Tests to add

Add tests that do not require a live Nextcloud server:

1. Disabled by default returns empty string.
2. Non-document casual messages return empty string.
3. Document-looking questions call the configured backend.
4. Results are truncated to `NEXTCLOUD_AI_CONTEXT_MAX_CHARS`.
5. Network/API failures are handled without adding document context.
6. `ask()` includes the optional context when returned.

Existing baseline before Phase 1:

```text
python3 -m unittest discover -s tests -p 'test*.py' -v
# Ran 8 tests: OK
```

## Rollout plan

1. Implement module with feature flag off.
2. Add unit tests with mocked network calls.
3. Run test suite.
4. Enable only on a staging/test bridge instance first.
5. Verify a Talk question like “find the PDF about X” adds document context.
6. Enable on one intended bridge/profile only.
7. Do not enable for other assistants until their identity and access scope are reviewed.

## Done criteria for Phase 1

- Existing 8 tests still pass.
- New tests pass.
- Public repo remains sanitized.
- Bridge works exactly as before when `NEXTCLOUD_AI_CONTEXT=0`.
- When enabled and configured, Hermes receives bounded Nextcloud file/document context.
- Runtime failures in Nextcloud AI/context retrieval never prevent Hermes from answering.
