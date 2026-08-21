# Nextcloud Talk Hermes Bridge


A small webhook bridge that connects a Nextcloud Talk bot to [Hermes Agent](https://hermes-agent.nousresearch.com/docs).

It receives signed Nextcloud Talk bot webhook events, runs `hermes chat -q` with a configurable profile/persona/toolset, and posts the final answer back to the Talk room with signed bot messages.

## Features

- Verifies Nextcloud Talk webhook signatures.
- Posts signed bot replies back to the same Talk room.
- Supports authenticated proactive delivery for scheduled reports, monitoring alerts, and Hermes cron/webhook jobs via `POST /deliver`.
- Ignores bot-originated messages to avoid reply loops.
- Preserves short-term per-room context for follow-ups like “make it shorter” or “continue that.”
- Adds a stale-context guard that treats the newest user message as controlling and tells Hermes to use session/source/git history for “what we had before” style requests instead of blindly trusting old room state.
- Optionally enriches prompts from a local SQLite memory service: workspace, peers, sessions, messages, conclusions, and representation cards.
- Passes uploaded-file metadata from Talk events into the Hermes prompt.
- Handles Talk file-share and voice-message webhook shapes that arrive as non-Note JSON Create events.
- Optionally transcribes Talk voice/audio shares when the bridge host has local Nextcloud file access, ffmpeg, and whisper.cpp available.
- Resolves Talk image shares into a local readable cache and instructs Hermes to call `vision_analyze`, so assistants can inspect uploaded pictures without asking the user to re-upload them elsewhere.
- Supports Hermes profiles, skills, toolsets, and source labels.
- Supports long-running jobs with background wait + heartbeat messages.
- Uses only Python standard library at runtime.

## Requirements

- Python 3.11+
- A working Hermes Agent CLI (`hermes`)
- A Nextcloud server with the Talk app and Talk bot webhook support
- A public or reverse-proxied HTTPS endpoint pointing to this bridge's `/hook` path

## Install

```bash
git clone https://github.com/YOUR-ORG/nextcloud-talk-hermes-bridge.git
cd nextcloud-talk-hermes-bridge
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env
```

Edit `.env`:

```bash
TALK_BOT_SECRET=replace-with-nextcloud-talk-bot-secret
TALK_DELIVER_SECRET=replace-with-bridge-delivery-api-key
NEXTCLOUD_URL=https://nextcloud.example.com
TALK_BRIDGE_PORT=8788
HERMES_BIN=/path/to/hermes
HERMES_PROFILE=default
HERMES_HOME_DIR=/home/your-user
ASSISTANT_NAME=Hermes Talk Assistant
ASSISTANT_ROLE=You are a helpful private assistant inside Nextcloud Talk.
```

Run locally:

```bash
set -a
. ./.env
set +a
python -m nextcloud_talk_hermes_bridge.bridge
```

Health check:

```bash
curl http://127.0.0.1:8788/health
# ok
```

## systemd user service

```bash
mkdir -p ~/.config/systemd/user
cp systemd/nextcloud-talk-hermes-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nextcloud-talk-hermes-bridge.service
systemctl --user status nextcloud-talk-hermes-bridge.service
```

The sample unit assumes the repo lives at:

```text
~/nextcloud-talk-hermes-bridge
```

Adjust `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` if you install elsewhere.

## Nextcloud Talk bot setup

Exact admin screens vary by Nextcloud/Talk version, but the bridge expects:

- Webhook URL: `https://your-public-host.example.com/hook`
- Shared secret: same value as `TALK_BOT_SECRET`
- Bot URL base / Nextcloud URL: same base as `NEXTCLOUD_URL`, for example `https://nextcloud.example.com`

The bridge handles:

- Incoming webhook signature headers:
  - `X-Nextcloud-Talk-Random`
  - `X-Nextcloud-Talk-Signature`
- Outgoing bot message signature headers:
  - `X-Nextcloud-Talk-Bot-Random`
  - `X-Nextcloud-Talk-Bot-Signature`

## Important configuration

- `HERMES_PROFILE`: Hermes profile to run, such as `default` or a dedicated assistant profile.
- `TALK_DELIVER_SECRET`: separate bearer token for proactive outbound delivery calls to `/deliver`. Keep this distinct from `TALK_BOT_SECRET`.
- `HERMES_TOOLSETS`: comma-separated toolsets exposed to Hermes.
- `HERMES_SKILLS`: comma-separated skills to pre-load.
- `TALK_BRIDGE_SKILL_STATUS`: `1`/`0` toggle. When enabled, the bridge prompt tells Hermes to explicitly report any skill creation, patch, edit, or deletion in its final Talk reply, including the skill names changed.
- `TALK_RECEIVED_REACTION`: optional emoji reaction, for example `👀`, that the bridge adds to an inbound Talk message before Hermes starts working. Leave unset to disable. Reaction failures are logged but never block the final reply or the existing `Working.` / background heartbeat messages.
- `HERMES_YOLO`: `1` enables non-interactive tool execution. Set to `0` if you want a safer/default Hermes mode.
- `TALK_BRIDGE_SOFT_TIMEOUT`: seconds before the bridge posts a “still working” notice and keeps waiting.
- `TALK_BRIDGE_HARD_TIMEOUT`: maximum runtime before stopping the Hermes process.
- `TALK_CONTEXT_DIR`: where room context JSONL files are stored. The generated context packet includes a priority rule: newest user message first, room state lower priority, and source/session/git history for “what was there before” requests.
- `TALK_LOCAL_MEMORY_CONTEXT`: `1`/`0` toggle for local SQLite memory context injection. Defaults to `1`.
- `TALK_MEMORY_NAMESPACE`: memory workspace/namespace for this bridge, for example `personal`, `assistant`, or `support`. Defaults to `HERMES_PROFILE`, otherwise `default`.
- `TALK_MEMORY_DB_PATH`: optional path to a compatible SQLite memory database. Defaults to `$HERMES_HOME/local-memory/memory.sqlite3`.

Optional Nextcloud AI/document context settings:

- `NEXTCLOUD_AI_CONTEXT`: `1`/`0` toggle for injecting bounded candidate file matches into the Hermes prompt. Defaults to `0`.
- `NEXTCLOUD_AI_CONTEXT_MODE`: `files_search` / `ocs_files` for Nextcloud OCS file search.
- `NEXTCLOUD_AI_USER` / `NEXTCLOUD_AI_APP_PASSWORD`: Nextcloud user/app password for OCS file search.
- `NEXTCLOUD_AI_CONTEXT_LIMIT`, `NEXTCLOUD_AI_CONTEXT_MAX_CHARS`, `NEXTCLOUD_AI_CONTEXT_TIMEOUT`, and `NEXTCLOUD_AI_CONTEXT_MIN_QUERY_CHARS`: bounds for result count, injected context size, network/process timeout, and query filtering.

Optional voice/audio transcription settings:

- `NEXTCLOUD_DATA_ROOT`: local Nextcloud data root, used to resolve shared audio files. Defaults to `/var/www/html/data`.
- `TALK_NEXTCLOUD_CONTAINER`: Docker container name used for resolving share IDs through Nextcloud config/PDO. Defaults to `nextcloud`.
- `TALK_NEXTCLOUD_USER`: fallback Nextcloud user ID for rendered `{file}` payloads that only include a display path/name.
- `TALK_FFMPEG_BIN`: ffmpeg binary for converting audio before transcription. Defaults to `ffmpeg`.
- `TALK_WHISPER_BIN`: whisper.cpp binary. Defaults to `whisper-cli`.
- `TALK_WHISPER_MODEL`: whisper.cpp model path. If unset or missing, transcription is skipped.
- `TALK_TRANSCRIBE_MAX_BYTES`: max audio file size to transcribe. Defaults to 50 MiB.
- `TALK_TRANSCRIBE_TIMEOUT`: transcription timeout in seconds. Defaults to 180.

Optional image/media vision settings:

- `TALK_MEDIA_CACHE_DIR`: cache directory for user-readable copies of Talk image shares. Defaults to `~/.cache/talk-media-vision`.
- `TALK_IMAGE_MAX_BYTES`: max image file size to copy for vision analysis. Defaults to 25 MiB.

## Proactive delivery and native Hermes cron delivery

`POST /deliver` lets a trusted local scheduler, Hermes cron job, or monitoring webhook post a bot message into a Talk room without waiting for an inbound Talk webhook first.

For Hermes cron, this repository ships a native `nextcloud_talk` delivery integration. Install the bridge package, then install the bundled Hermes platform into the Hermes Agent tree:

```bash
nextcloud-talk-hermes-cron-install --hermes-repo /path/to/hermes-agent
```

Set the Hermes gateway environment:

```bash
NEXTCLOUD_TALK_DELIVER_URL=http://127.0.0.1:8788/deliver
NEXTCLOUD_TALK_HOME_ROOM=abc123
NEXTCLOUD_TALK_DELIVER_SECRET=replace-with-bridge-delivery-api-key
```

After restarting Hermes gateway, cron jobs can use native delivery:

```bash
hermes cron create "0 8 * * *" "Write a short morning status report." --deliver nextcloud_talk
```

This is not a per-job script. Hermes registers `nextcloud_talk` as a platform delivery target and uses the bridge's standalone `/deliver` sender for scheduled output.

```bash
curl -X POST http://127.0.0.1:8788/deliver \
  -H "Authorization: Bearer <delivery-secret>" \
  -H "Content-Type: application/json" \
  -d '{"room_token":"abc123","message":"Daily status report: all services healthy."}'
```

Request fields:

- `room_token` or `token`: required Talk room token.
- `message`: required bot message text.
- `actor`: optional source label for bridge logs, for example `cron` or `monitoring`.
- `reply_to` / `replyTo`: optional Talk message ID to reply to; usually omitted for scheduled posts.
- `thread_title` / `threadTitle`: optional Talk thread title. When set without `reply_to`, Talk creates a thread for the bot message on servers with thread support.
- `thread_id` / `threadId`: optional Talk thread ID. When set without `reply_to`, Talk posts into that existing thread without quoting a specific message.
- `silent`: optional boolean to suppress notifications for the bot message.
- `reference_id` / `referenceId`: optional app-defined reference ID for later correlation.

Responses are JSON. A successful delivery returns:

```json
{"ok": true, "status": "delivered", "room_token": "abc123", "post_status": 200}
```

When delivery succeeds, the bridge records the outbound assistant message in the same short-term room context and local SQLite memory path used for normal reactive replies. That way, if a user responds to a scheduled report with “what did you mean by that?”, Hermes can see the proactive message as prior assistant context.

## Hermes skill creation from Talk

The bridge can let Hermes create or update skills when the deployed Hermes profile has the `skills` toolset enabled. The default `HERMES_TOOLSETS` includes `skills`, so a trusted deployment can create, patch, edit, or delete skills using the normal Hermes skill tools.

For visibility in Nextcloud Talk, `TALK_BRIDGE_SKILL_STATUS=1` is enabled by default. With that setting, the bridge instructs Hermes to state skill changes in the final Talk reply, for example:

```text
Skills changed: created canon-field-service-workflow; patched mannpro-custom-ubuntu-live-build
```

If no skill is created, Hermes should say so when the user specifically asked for a skill. Keep `HERMES_YOLO` and broad toolsets limited to trusted private assistants, because skill tools can write durable procedure files under the selected Hermes profile.

## Local SQLite memory service

The bridge includes an optional local SQLite memory service for deployments that need better follow-up handling than short-term room history alone.

When `TALK_LOCAL_MEMORY_CONTEXT=1`, the bridge will:

- create/use a local SQLite database at `TALK_MEMORY_DB_PATH` or `$HERMES_HOME/local-memory/memory.sqlite3`;
- keep memory separated by `TALK_MEMORY_NAMESPACE`;
- index Talk/Hermes messages with FTS5;
- read compatible durable memories, conclusions, and representation cards when building the prompt context;
- avoid mixing namespaces across assistants, users, or bridge instances.

This is local-only storage. It does not require a cloud memory provider.

## Security notes

- Do **not** commit `.env` or log files.
- Use a strong random `TALK_BOT_SECRET`.
- Run this bridge as a limited user.
- Be careful with `HERMES_YOLO=1` and broad toolsets. That is powerful and should only be used where the Hermes profile and host are trusted.
- Put the bridge behind HTTPS.
- Keep separate assistants/users in separate Hermes profiles and separate bridge instances.

## Development checks

```bash
python -m compileall nextcloud_talk_hermes_bridge
python -m nextcloud_talk_hermes_bridge.bridge
```

The second command needs environment variables and will start the server.

## Nextcloud App Store / AppAPI ExApp package

This repository now includes an Option A Nextcloud External App (ExApp) wrapper for App Store submission. The App Store app ID is `hermes_talk_bridge`, and the runtime is delivered as an AppAPI-managed Docker image declared in `appinfo/info.xml`.

AppAPI lifecycle endpoints implemented by the bridge:

- `GET /heartbeat` — AppAPI health probe.
- `POST /init` — initialization acknowledgement.
- `PUT /enabled?enabled=1|0` — enable/disable lifecycle acknowledgement.
- `POST /hook` — Nextcloud Talk bot webhook receiver.
- `POST /deliver` — authenticated proactive delivery endpoint for cron/report/alert messages.

Build and smoke-test locally before release:

```bash
python -m compileall nextcloud_talk_hermes_bridge
python -m pytest -q
python scripts/build_appstore_package.py --allow-unsigned
docker build -t nextcloud-talk-hermes-bridge:local .
```

For App Store packaging details, see `EXAPP_SUBMISSION.md`.


## Talk slash commands

Nextcloud Talk no longer provides the old native chat-command system for bots, so Hermes Talk Bridge emulates useful Telegram-style commands in normal Talk messages. Users can type these directly in a bot room, optionally prefixed with the assistant name, for example `/status` or `Assistant /status`.

| Command | Purpose |
|---|---|
| `/help` | Show available bridge commands. |
| `/status` | Show bridge, Hermes profile, memory namespace, and toolset status. |
| `/memory` | Show whether local memory / Talk context is enabled and configured. |
| `/tools` | Show enabled Hermes toolsets. |
| `/reset` | Clear the current room's short-term Talk context while retaining durable memory. |
| `/version` | Show bridge app/version information. |
| `/queue` | Explain long-running/background task behavior and timeout settings. |

Slash commands are handled by the bridge before invoking Hermes when possible, so lightweight status/reset commands stay fast and do not spend model tokens.
