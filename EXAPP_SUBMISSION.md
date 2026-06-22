# Hermes Talk Bridge Nextcloud App Store submission notes

Hermes Talk Bridge is packaged as a Nextcloud External App (ExApp) using AppAPI. The App Store tarball contains the Nextcloud metadata (`appinfo/info.xml`) and release documentation; the runtime is delivered as an AppAPI-managed Docker image.

## App Store metadata

- App ID: `hermes_talk_bridge`
- Name: Hermes Talk Bridge
- Categories: Integration, Social & communication, AI
- Source repository: https://github.com/robertlmann02/nextcloud-talk-hermes-bridge
- Docker image: `ghcr.io/robertlmann02/nextcloud-talk-hermes-bridge:0.2.0`
- Required AppAPI scopes: `TALK`, `TALK_BOT`, `FILES`
- Public proxy routes declared for `/health`, `/heartbeat`, `/init`, `/enabled`, and `/hook`; `/hook` remains protected by signed Talk bot webhook verification.

## Data flow

1. Nextcloud Talk sends signed bot webhook events to `/hook`.
2. The bridge verifies the Talk signature using `TALK_BOT_SECRET` or AppAPI-provided `APP_SECRET`.
3. The bridge runs `hermes chat -q` with the configured profile/toolsets.
4. The bridge posts the final response back through the Talk bot API using signed bot headers.

No LLM provider credentials are bundled. Administrators configure Hermes provider credentials in the container environment/persistent storage they control. Optional document context and voice transcription features are off unless configured by environment variables.

## AppAPI endpoints

- `GET /heartbeat`: returns JSON health/status for AppAPI.
- `POST /init`: acknowledges AppAPI initialization.
- `PUT /enabled?enabled=1|0`: acknowledges enable/disable lifecycle events.
- `POST /hook`: receives Nextcloud Talk bot webhooks.
- `GET /health`: simple health endpoint for local probes.

## Release process

1. Publish the Docker image to GHCR for the version in `appinfo/info.xml`.
2. Generate a signing CSR with `scripts/generate_signing_csr.sh`. Keep the `.key` private.
3. Submit the `.csr` to https://github.com/nextcloud/app-certificate-requests with this public repository link.
4. After receiving the certificate, sign the app with `scripts/sign_app.sh`.
5. Build the App Store tarball with `scripts/build_appstore_package.py`.
6. Upload the `.tar.gz` release artifact URL to https://apps.nextcloud.com/developer/apps/new.

For pre-review/testing before the certificate is issued, use:

```bash
python scripts/build_appstore_package.py --allow-unsigned
```
