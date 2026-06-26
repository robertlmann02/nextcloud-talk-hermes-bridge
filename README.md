# Nextcloud Talk Hermes Bridge


A small webhook bridge that connects a Nextcloud Talk bot to [Hermes Agent](https://hermes-agent.nousresearch.com/docs).

It receives signed Nextcloud Talk bot webhook events, runs `hermes chat -q` with a configurable profile/persona/toolset, and posts the final answer back to the Talk room with signed bot messages.

## Features

- Verifies Nextcloud Talk webhook signatures.
- Posts signed bot replies back to the same Talk room.
- Ignores bot-originated messages to avoid reply loops.
- Preserves short-term per-room context for follow-ups like “make it shorter” or “continue that.”
- Optionally enriches prompts from a local SQLite memory service: workspace, peers, sessions, messages, conclusions, and representation cards.
- Passes uploaded-file metadata from Talk events into the Hermes prompt.
- Handles Talk file-share and voice-message webhook shapes that arrive as non-Note JSON Create events.
- Optionally transcribes Talk voice/audio shares when the bridge host has local Nextcloud file access, ffmpeg, and whisper.cpp available.
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
- `HERMES_TOOLSETS`: comma-separated toolsets exposed to Hermes.
- `HERMES_SKILLS`: comma-separated skills to pre-load.
- `HERMES_YOLO`: `1` enables non-interactive tool execution. Set to `0` if you want a safer/default Hermes mode.
- `TALK_BRIDGE_SOFT_TIMEOUT`: seconds before the bridge posts a “still working” notice and keeps waiting.
- `TALK_BRIDGE_HARD_TIMEOUT`: maximum runtime before stopping the Hermes process.
- `TALK_CONTEXT_DIR`: where room context JSONL files are stored.
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

Build and smoke-test locally before release:

```bash
python -m compileall nextcloud_talk_hermes_bridge
python -m pytest -q
python scripts/build_appstore_package.py --allow-unsigned
docker build -t nextcloud-talk-hermes-bridge:local .
```

After the app ID is final, generate the signing key and CSR locally:

```bash
scripts/generate_signing_csr.sh
```

Keep private and local:

- `.key` files
- app passwords
- bot tokens
- OAuth/auth files
- `.env` files

Submit to GitHub / the Nextcloud certificate request repo:

- only the generated `.csr`

By default the helper stores the private key and CSR durably at:

```text
~/.nextcloud/certificates/hermes_talk_bridge.key
~/.nextcloud/certificates/hermes_talk_bridge.csr
```

After Nextcloud returns the certificate, save it beside the retained key and sign the release locally with the matching private key and certificate:

```bash
scripts/sign_app.sh /path/to/nextcloud/occ ~/.nextcloud/certificates/hermes_talk_bridge.key ~/.nextcloud/certificates/hermes_talk_bridge.crt
python scripts/build_appstore_package.py
```

Do not put the private key on GitHub. See `EXAPP_SUBMISSION.md` for the App Store submission checklist and data-flow disclosure.
