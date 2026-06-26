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

1. Build and smoke-test locally before release:

   ```bash
   python -m compileall nextcloud_talk_hermes_bridge
   python -m pytest -q
   python scripts/build_appstore_package.py --allow-unsigned
   docker build -t nextcloud-talk-hermes-bridge:local .
   ```

2. Publish the Docker image to GHCR for the version in `appinfo/info.xml`.
3. After the app ID is final, generate the signing key and CSR locally with `scripts/generate_signing_csr.sh`.

   The helper stores the private signing material durably under:

   ```text
   ~/.nextcloud/certificates/hermes_talk_bridge.key
   ~/.nextcloud/certificates/hermes_talk_bridge.csr
   ```

4. Keep private and local:

   - `.key` files
   - app passwords
   - bot tokens
   - OAuth/auth files
   - `.env` files

5. Submit to GitHub / the Nextcloud certificate request repo only:

   - `hermes_talk_bridge/hermes_talk_bridge.csr`

6. After Nextcloud returns the certificate, save it beside the retained key as `~/.nextcloud/certificates/hermes_talk_bridge.crt`.
7. Sign the release locally with the matching private key and certificate:

   ```bash
   scripts/sign_app.sh /path/to/nextcloud/occ ~/.nextcloud/certificates/hermes_talk_bridge.key ~/.nextcloud/certificates/hermes_talk_bridge.crt
   python scripts/build_appstore_package.py
   ```

8. Upload the `.tar.gz` release artifact URL to https://apps.nextcloud.com/developer/apps/new.

Do not put the private key, app passwords, bot tokens, OAuth/auth files, or `.env` files on GitHub.

For pre-review/testing before the certificate is issued, use:

```bash
python scripts/build_appstore_package.py --allow-unsigned
```
