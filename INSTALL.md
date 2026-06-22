# Installing Switchboard

Switchboard is authored by [Alexander Peppe](https://www.alexanderpeppe.com/). This guide describes how to install it without putting private phone numbers, credentials, contacts, message history, or server URLs into source control.

## 1. Choose The Deployment Shape

Every deployment has these pieces:

- A Switchboard server process.
- A SQLite database and media directory outside the Git checkout.
- A public HTTPS URL for the web app and provider callbacks.
- Telnyx and/or Twilio sender numbers that you own.
- Optional services for contacts, voicemail transcription, notifications, Android, and private network access.

Recommended production layout:

```text
/opt/switchboard              source checkout
/opt/switchboard/.env         ignored local configuration
/var/lib/switchboard          SQLite, received media, upload staging
https://switchboard.example   public TLS URL
```

Keep `.env`, SQLite files, received media, local Android properties, APKs, and archives out of Git. The checked-in `.gitignore` already excludes those paths and file types.

## 2. Install From Source

```bash
git clone https://github.com/your-org/switchboard.git
cd switchboard
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set at least:

```env
TEXTING_HOST=127.0.0.1
TEXTING_PORT=8766
TEXTING_DATA_DIR=/var/lib/switchboard
TEXTING_DB=/var/lib/switchboard/switchboard.sqlite
TEXTING_MEDIA_DIR=/var/lib/switchboard/media
TEXTING_PUBLIC_UPLOAD_DIR=/var/lib/switchboard/public-uploads
TEXTING_AUTH_DISABLED=0
```

Create runtime directories:

```bash
sudo mkdir -p /var/lib/switchboard/media /var/lib/switchboard/public-uploads
sudo chown -R "$USER":"$USER" /var/lib/switchboard
```

Start the app:

```bash
python3 server.py --host 127.0.0.1 --port 8766
```

Open `http://127.0.0.1:8766`. On a new install, leave `TEXTING_AUTH_USERNAME` and `TEXTING_AUTH_PASSWORD_HASH` blank and complete the first-run setup screen. For headless setup, generate values first:

```bash
python -m texting_app.auth hash-password
python -m texting_app.auth secret-key
```

## 3. Install With Docker Compose

```bash
cp .env.example .env
docker compose up -d --build
```

Compose stores runtime data in the `switchboard-data` volume and binds the app to `${SWITCHBOARD_PORT:-8766}` on the host. The image includes `ffmpeg` for 3GP-to-MP4 conversion and `poppler-utils` for fax PDF previews.

For systemd-managed Docker:

```bash
sudo cp docker/systemd/switchboard.service /etc/systemd/system/switchboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now switchboard.service
```

## 4. Put HTTPS In Front

Use Apache, Caddy, nginx, Tailscale Funnel, or another TLS proxy. The app can run on localhost behind that proxy.

Apache example:

```apache
ProxyPreserveHost On
ProxyPass / http://127.0.0.1:8766/
ProxyPassReverse / http://127.0.0.1:8766/
RequestHeader set X-Forwarded-Proto "https"
```

Do not put Basic Auth in front of the whole app unless these paths are still reachable by providers:

```text
/api/telnyx/webhook
/api/twilio/webhook
/api/telnyx/voice
/api/twilio/voice
/api/telnyx/voice/recording
/api/twilio/voice/recording
/api/telnyx/voice/transcription
/api/twilio/voice/transcription
/api/revai/webhook
/uploads/...
```

Keep `/media/...` private. It serves received attachments and voicemail recordings through Switchboard's authenticated app route.

## 5. Configure Sender Numbers

Add sender numbers you own in E.164 form:

```env
TEXTING_PERSONAL_NUMBERS=+15551230001,+15551230002
TEXTING_IDENTITY_LABELS={"+15551230001":"Personal","+15551230002":"Work"}
```

If all numbers use one provider:

```env
TEXTING_MESSAGING_PROVIDER=telnyx
```

If you mix providers:

```env
TEXTING_PROVIDER_BY_NUMBER={"+15551230001":"telnyx","+15551230002":"twilio"}
```

Restart the server after adding numbers. The Numbers panel in the UI lets you rename, recolor, activate/deactivate, and configure call behavior for each sender identity.

For copyable `.env` starting points, see `docs/env/`. It includes separate examples for core server settings, Telnyx, Twilio, Rev.ai transcription, and contact sync.

## 6. Connect Telnyx

Set credentials:

```env
TELNYX_API_KEY=
TELNYX_PUBLIC_KEY=
```

Use this webhook for Telnyx Messaging Profile inbound messages, delivery updates, and Programmable Fax Application events:

```text
https://switchboard.example/api/telnyx/webhook
```

Use this endpoint for Telnyx TeXML voice:

```text
https://switchboard.example/api/telnyx/voice
```

Switchboard will return callback URLs for recordings and transcriptions:

```text
https://switchboard.example/api/telnyx/voice/recording
https://switchboard.example/api/telnyx/voice/transcription
```

Set `TELNYX_PUBLIC_KEY` so Switchboard can verify Telnyx webhook signatures. Leave it blank only for trusted local testing.

Inbound fax PDFs are downloaded to `TEXTING_MEDIA_DIR`. Install `poppler-utils` on non-Docker hosts if you want fax page PNG previews:

```bash
sudo apt install poppler-utils
```

## 7. Connect Twilio

Set credentials:

```env
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WEBHOOK_URL=
TWILIO_STATUS_CALLBACK_URL=
```

Use this webhook for incoming Twilio SMS/MMS:

```text
https://switchboard.example/api/twilio/webhook
```

Use the same URL, or `TWILIO_STATUS_CALLBACK_URL`, for delivery status callbacks.

For inbound Twilio Group MMS, create a Twilio Event Streams webhook sink that points to the same URL and subscribe it to `com.twilio.messaging.inbound-message.received` schema v5 or newer. Switchboard uses the Event Streams `recipients` list to identify the full group. The regular incoming SMS/MMS webhook may not include those other recipients.

Use this endpoint for Twilio voice:

```text
https://switchboard.example/api/twilio/voice
```

Switchboard will return callback URLs for recordings and transcriptions:

```text
https://switchboard.example/api/twilio/voice/recording
https://switchboard.example/api/twilio/voice/transcription
```

Set `TWILIO_AUTH_TOKEN` so Switchboard can verify `X-Twilio-Signature`. If a reverse proxy rewrites the public URL, preserve the public host and scheme with `Host` and `X-Forwarded-Proto`, or set `TWILIO_WEBHOOK_URL` to the exact callback URL Twilio uses.

## 8. Configure Calls And Voicemail

Global call defaults live in Settings > Calls. Per-number rules live in the Numbers panel.

Each sender number can:

- Forward incoming calls to a phone number or SIP address.
- Set a ring timeout from 5 to 120 seconds.
- Enable or disable voicemail.
- Use a text greeting or uploaded public audio greeting.

For voicemail transcription:

```env
TEXTING_VOICEMAIL_TRANSCRIPTION_PROVIDER=provider
```

Use `provider` for Telnyx/Twilio transcripts. Use Rev.ai when you want external transcription:

```env
TEXTING_VOICEMAIL_TRANSCRIPTION_PROVIDER=revai
REVAI_ACCESS_TOKEN=
```

Configure this Rev.ai callback URL:

```text
https://switchboard.example/api/revai/webhook
```

## 9. Configure Outbound Media Uploads

Outbound MMS files and voicemail greeting uploads must be fetched by Telnyx or Twilio without a browser login. Switchboard handles this itself: it writes files to the upload staging directory and serves them at `/uploads/<random-file>`.

```env
TEXTING_PUBLIC_UPLOAD_DIR=/var/lib/switchboard/public-uploads
TEXTING_PUBLIC_UPLOAD_BASE_URL=
TEXTING_UPLOAD_MAX_FILE_MB=25
```

Leave `TEXTING_PUBLIC_UPLOAD_BASE_URL` blank for the self-packaged path. Switchboard derives upload URLs from the public request origin, for example `https://switchboard.example/uploads/<random-file>`. Set an explicit base URL only when uploads are intentionally served from a different host or path.

The upload route is public by design so providers can fetch the random-named files. Do not expose the SQLite database or `TEXTING_MEDIA_DIR`; `/media/...` remains private behind app authentication.

Some inbound phone videos arrive as 3GP files. Install `ffmpeg` on non-Docker hosts for automatic browser-friendly MP4 conversion:

```bash
sudo apt install ffmpeg
```

## 10. Configure Contacts

Switchboard stores contacts locally and links them to conversation participants by phone number.

Fastmail CardDAV:

```env
CONTACTS_PROVIDER=fastmail
FASTMAIL_USERNAME=you@example.com
FASTMAIL_APP_PASSWORD=
FASTMAIL_CARDDAV_URL=
FASTMAIL_CARDDAV_USERNAME=
```

Legacy Fastmail JMAP token mode:

```env
FASTMAIL_API_TOKEN=
FASTMAIL_ACCOUNT_ID=
```

Google People API:

```env
CONTACTS_PROVIDER=google
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
```

Generate the Google refresh token:

```bash
GOOGLE_CLIENT_ID=... scripts/google_contacts_oauth.py
GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... scripts/google_contacts_oauth.py --code YOUR_CODE
```

Enable automatic sync if desired:

```env
CONTACTS_AUTOSYNC=1
CONTACTS_SYNC_INTERVAL_MINUTES=360
```

## 11. Configure Notifications

ntfy notifications:

```env
NTFY_ENDPOINT=https://ntfy.sh/your-private-topic
NTFY_ENABLED=1
```

Android-native notifications are optional and poll-based:

```env
TEXTING_NATIVE_NOTIFICATIONS_ENABLED=1
TEXTING_NATIVE_NOTIFICATION_INTERVAL_MINUTES=15
```

Android enforces a 15 minute minimum for periodic background checks and can batch work for battery scheduling.

## 12. Build The Android Wrapper

The Android wrapper lives in `mobile/android`. Its local server URL belongs in ignored local properties:

```bash
cp mobile/android/local.properties.example mobile/android/local.properties
```

Edit:

```properties
sdk.dir=/path/to/android-sdk
SWITCHBOARD_APP_URL=https://switchboard.example
```

Build:

```bash
cd mobile/android
ANDROID_HOME=/path/to/android-sdk gradle assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The app requires an HTTPS URL for release-style use. Local/private HTTP URLs are allowed only for testing. If the configured server cannot be reached, the app shows a connection screen and lets the user retry or enter a different server URL.

Do not publish APKs that embed a private server URL unless that is intentional.

## 13. Tailscale

Tailscale is useful for private operator access:

```bash
tailscale serve set-config --all tailscale/serveconfig.json
tailscale serve advertise svc:switchboard
```

See [tailscale/README.md](tailscale/README.md).

Provider callbacks still need public HTTPS. Use a public reverse proxy, Tailscale Funnel, or provider-reachable endpoint for Telnyx, Twilio, and Rev.ai callbacks.

## 14. Backups And Updates

Back up:

- `TEXTING_DB`
- `TEXTING_MEDIA_DIR`
- `TEXTING_PUBLIC_UPLOAD_DIR` if queued uploads matter
- `.env`

The Settings screen includes a database download button that creates a consistent SQLite backup, but attachments and public uploads are separate files.

Before updating:

```bash
sqlite3 "$TEXTING_DB" 'PRAGMA integrity_check;'
cp -a /var/lib/switchboard /var/lib/switchboard.backup
git pull
python3 -m pip install -r requirements.txt
```

Then restart the service.

## 15. Release Hygiene Checklist

Before publishing source or a release artifact:

```bash
git status --short
git check-ignore -v .env data/texting.sqlite Archive.zip mobile/android/local.properties || true
rg -n "TELNYX|FASTMAIL|GOOGLE|ntfy|https://[^ ]+|\\+?[0-9][0-9() .-]{7,}" \
  --glob '!data/**' --glob '!mobile/android/build/**' \
  --glob '!mobile/android/app/build/**' --glob '!mobile/android/.gradle/**'
```

Review every hit. It is fine for examples to use `example.com`, `switchboard.example`, `+15551230001`, or `you@example.com`. Private values belong in `.env`, local provider dashboards, ignored runtime data, or ignored Android `local.properties`.

Do not include:

- `.env`
- SQLite databases
- received media
- contact exports
- archives
- APKs/AABs unless built intentionally for distribution
- `mobile/android/local.properties`

## 16. Smoke Tests

Local server:

```bash
python3 server.py --host 127.0.0.1 --port 8766
curl -fsS http://127.0.0.1:8766/api/health
```

JavaScript syntax:

```bash
node --check static/app.js
```

Docker:

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:${SWITCHBOARD_PORT:-8766}/api/health
```

Android:

```bash
cd mobile/android
gradle assembleDebug
adb devices
adb install -r app/build/outputs/apk/debug/app-debug.apk
```
